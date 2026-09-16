#!/usr/bin/env python3
"""license.py -- shared FlexLM seat query (Phase 4).

docs/job_status_plan.md Phase 4: a job blocked waiting on a license seat is
alive but makes no progress, so the hung-vs-slow heuristic would flag it as
suspect. This module lets the flow (and, when it can, the sidecar) tell the
difference -- "0 of 54 Spectre seats free" is WAITING_LICENSE, not a hang.

The lmstat parse is the proven one from analog/engine/spectre_flow.py's
sim_workers (the plan's "reuse the live lmstat logic"): resolve the server
from the tool's license env, run `lmstat -c <server> -f <feature>`, read
`Total of N licenses issued; Total of M licenses in use`.

Runs INSIDE the tool env (needs lmstat on PATH and the LM_LICENSE_FILE the
toolchain sets -- non-interactive shells have neither, so a sidecar call is
best-effort and simply returns None when it cannot ask). Pure stdlib, 3.6+.
NDA: emits seat COUNTS and the (BNL-internal) license server address only --
never usernames, hosts, or anything foundry-confidential.

  python3 license.py <feature|alias> [--server 7183@host]
    -> {"schema":1,"feature":..,"issued":N,"in_use":M,"free":F} or exit 1.

Usage:
  python3 license.py <feature> [--server] [--lmstat]
"""
import argparse
import json
import os
import re
import subprocess
import sys

#: tool alias -> (FlexLM feature, preferred server prefix). CLUSTER.md:
#: Cadence 7183@, Calibre 7184@, HSPICE 7180@.
FEATURES = {
    "spectre": ("Virtuoso_Multi_mode_Simulation", "7183@"),
    "virtuoso": ("Virtuoso_Multi_mode_Simulation", "7183@"),
    # confirmed 2026-08-31 via live lmstat -a on 7183@iolicense2:
    "xcelium": ("Xcelium_Single_Core", "7183@"),
    "xcelium_dms": ("Xcelium_SC_DMS_Option", "7183@"),
    # calibre feature strings vary by kit; pass the exact feature name
    # through (default server prefix below) until confirmed.
}

_LM_ENV = ("CDS_LIC_FILE", "LM_LICENSE_FILE", "ALL_LICENSE_FILES")
_TOTALS = re.compile(r"Total of (\d+) licenses? issued;\s+"
                     r"Total of (\d+) licenses? in use")


def server_from_env(prefer="7183@"):
    """The license server `host@` token from the tool env, preferring the
    given family (Cadence 7183@ by default), else the first `@` token."""
    toks = ":".join(os.environ.get(v, "") for v in _LM_ENV).split(":")
    return next((t for t in toks if t.startswith(prefer)),
                next((t for t in toks if "@" in t), None))


def parse_totals(text):
    """Pure parse of an lmstat `-f` block -> {issued, in_use, free}, or None
    if the totals line is absent. Split out so it is testable without a
    live license server."""
    m = _TOTALS.search(text or "")
    if not m:
        return None
    issued, in_use = int(m.group(1)), int(m.group(2))
    return {"issued": issued, "in_use": in_use, "free": issued - in_use}


def resolve(feature):
    """(feature, preferred-server-prefix) for a FEATURES alias, else the raw
    feature with the default Cadence 7183@ prefix."""
    if feature in FEATURES:
        return FEATURES[feature]
    return feature, "7183@"


def seats(feature, server=None, lmstat="lmstat", timeout=30):
    """{feature, server, issued, in_use, free} for `feature` (a FEATURES
    alias or a raw FlexLM feature name), or None if lmstat is unavailable /
    the output cannot be parsed. Never raises."""
    feature, prefer = resolve(feature)
    srv = server or server_from_env(prefer)
    if not srv:
        return None
    try:
        out = subprocess.run([lmstat, "-c", srv, "-f", feature],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=timeout).stdout
    except Exception:
        return None
    t = parse_totals(out)
    if t is None:
        return None
    res = {"feature": feature, "server": srv}
    res.update(t)
    return res


def free(feature, **kw):
    """Free seat count for `feature`, or None if it cannot be determined."""
    s = seats(feature, **kw)
    return None if s is None else s["free"]


# --------------------------------------------------------------- who holds it
#
# `seats()` answers "how many are left". This half answers "which of MINE are
# out", which is what turns a list of stale tool processes into a decision.
#
# ⚠️ FLEXLM DOES NOT REPORT A PID. Measured on this server, not assumed: the
# usage line is
#
#     <user> <host> <display> (<version>) (<server>/<port> <handle>), start ...
#
# and the number after the port is FlexLM's own handle, not a process id -- a
# scan of every usage line found zero pid-shaped fields. So per-process
# attribution is a CORRELATION on (host, user, start time), never a lookup, and
# everything below is careful to say which it is.
#
# PRIVACY. These lines carry other people's usernames. `usage()` filters to ONE
# user (the caller's own, by default) before returning anything, so nothing
# outside your own sessions ever reaches a caller, a report or a log. That
# keeps the module docstring's promise: counts and the server address travel,
# people do not.

#: All license servers reachable from the tool env, not just Cadence's. The
#: cluster runs SEVEN (7180/7182/7183/7184/7186/7188/7190 @iolicense2), and
#: asking only 7183 gives a confidently wrong answer: a 70-day Virtuoso here
#: holds NO Cadence seat and two Cliosoft SOS seats on 7188.
def servers_from_env():
    """Every distinct `port@host` token in the tool env, in first-seen order."""
    toks = ":".join(os.environ.get(v, "") for v in _LM_ENV).split(":")
    out = []
    for t in toks:
        t = t.strip()
        if "@" in t and t not in out:
            out.append(t)
    return out


#: One in-use line. The trailing `(linger: N / M)` is optional and real -- the
#: Cliosoft daemon emits it -- so it is consumed here rather than left to
#: corrupt the start field.
_USAGE = re.compile(
    r"^\s+(?P<user>\S+)\s+(?P<host>\S+)\s+(?P<display>\S+)\s+"
    r"\((?P<version>[^)]*)\)\s+"
    r"\((?P<server>[^/\s]+)/(?P<port>\d+)\s+(?P<handle>\d+)\)"
    r",\s*start\s+(?P<start>\w{3}\s+\d+/\d+\s+\d+:\d+)"
    r"(?:\s*\(linger:\s*(?P<linger>[^)]*)\))?\s*$")

#: `Users of <feature>:  (Total of N licenses issued; Total of M in use)`
_USERS_OF = re.compile(r"^Users of (?P<feature>\S+?):")


def parse_usage(text, user=None):
    """[{feature, user, host, display, version, server, handle, start, linger}]

    Pure parse of `lmstat -a`, so it is testable without a license server.
    `user` filters to one account and is the ONLY way any username leaves this
    function -- pass None only when the caller has already established it may
    see everyone (nothing in this repo does).
    """
    out, feature = [], None
    for ln in (text or "").splitlines():
        m = _USERS_OF.match(ln)
        if m:
            feature = m.group("feature")
            continue
        m = _USAGE.match(ln)
        if not m or feature is None:
            continue
        if user is not None and m.group("user") != user:
            continue
        d = m.groupdict()
        d["feature"] = feature
        d["port"] = int(d["port"])
        d["handle"] = int(d["handle"])
        out.append(d)
    return out


def usage(server=None, user=None, lmstat="lmstat", timeout=30):
    """In-use seats held by ONE user across one or all servers. Never raises.

    Returns [] both when nothing is held and when nothing could be asked --
    which are different facts, so callers that care must check `reachable()`
    or use `usage_report`.
    """
    return usage_report(server, user, lmstat, timeout)["seats"]


def usage_report(server=None, user=None, lmstat="lmstat", timeout=30):
    """{seats: [...], asked: [server...], failed: [server...]}

    The split matters: "no seats held" and "could not ask" both look like an
    empty list, and only one of them is evidence that a process is safe to
    kill. procscan reports them differently for exactly that reason.
    """
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "" \
        if user is None else user
    servers = [server] if server else servers_from_env()
    seats_out, asked, failed = [], [], []
    for srv in servers:
        try:
            out = subprocess.run([lmstat, "-c", srv, "-a"],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,
                                 universal_newlines=True,
                                 timeout=timeout).stdout
        except Exception:
            failed.append(srv)
            continue
        if "Users of" not in (out or ""):
            # lmstat exits 0 with an error banner when a server is down
            failed.append(srv)
            continue
        asked.append(srv)
        seats_out.extend(parse_usage(out, user=user or None))
    return {"seats": seats_out, "asked": asked, "failed": failed}


def _main(argv=None):
    p = argparse.ArgumentParser(description="FlexLM seat query")
    p.add_argument("feature", help="alias (spectre) or raw FlexLM feature")
    p.add_argument("--server", default=None, help="host@ (default: from env)")
    p.add_argument("--lmstat", default="lmstat")
    ns = p.parse_args(argv)
    s = seats(ns.feature, server=ns.server, lmstat=ns.lmstat)
    if s is None:
        return 1
    s = dict(s)
    s["schema"] = 1
    sys.stdout.write(json.dumps(s) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
