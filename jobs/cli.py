#!/usr/bin/env python3
"""jobs -- the laptop-side view of cluster jobs (Phase 1: ls/watch/run/why).

Everything reads or launches through the Phase-0 transport, so every
answer is tri-state: a transport blip prints as UNKNOWN and the watch
loop keeps going -- it NEVER reports a job "gone" because one poll
failed. Terminal verdicts come only from a KNOWN envelope.

  python3 -m jobs ls [--host asic6]
  python3 -m jobs watch <jobid> [--host asic6] [--interval 5]
  python3 -m jobs run  --flow F --target T [--expect PATH]... -- CMD...
  python3 -m jobs why  <jobid> [--host asic6]

Usage:
  python3 cli.py <jobid> <jobid> <jobid> [jobid] <cmd> [--host] [--mode] [--timeout] [--min-free] [--flow] [--interval] [--interval] [--interval] [--flow] [--target] [--interval] [--expect] [--progress] [--total] [--progress-log]
"""
import argparse
import os
import sys
import time

try:
    from . import hosts, remote     # package import (python -m jobs)
except ImportError:                  # direct-script fallback
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import hosts
    import remote

#: states that mean the job is over -- watch stops, ls dims them.
TERMINAL = {"done", "failed", "killed", "timeout", "NOTFOUND"}

#: `ls`/`top` grouping -- the three buckets a human actually triages by, in
#: display order.  `wait-lic` is a running job that has no license seat, so it
#: belongs with running; killed/timeout are failures however they got there.
#: Anything unrecognized falls into a trailing "other" group rather than being
#: dropped -- a job silently missing from the table is the exact class of bug
#: this whole system exists to prevent.
LS_GROUPS = (("running", ("running", "wait-lic")),
             ("done", ("done",)),
             ("failed", ("failed", "killed", "timeout")))


def _fmt_elapsed(sec):
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return "?"
    if sec < 60:
        return "%ds" % sec
    if sec < 3600:
        return "%dm%02ds" % (sec // 60, sec % 60)
    return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)


def _age(epoch, ref):
    try:
        a = int(ref - float(epoch))
    except (TypeError, ValueError):
        return "?"
    return _fmt_elapsed(a) if a >= 0 else "0s"


# --- ls -------------------------------------------------------------------

def _state_of(j):
    """Display state for one job record. A running job sitting on 0 free
    license seats is WAITING, not running -- surfacing that as its own state
    is what stops it being read as a hang."""
    state = j.get("state", "?")
    if state == "running" and j.get("lic_free") == 0:
        return "wait-lic"
    return state


def _ls_lines(t):
    """(ok, [line,...]) for the jobs table -- shared by `ls` and `top`."""
    r = t.list()
    if not r.ok:
        return False, ["jobs: %s -- %s" % (r.status, r.reason)]
    jobs = (r.data or {}).get("jobs", [])
    if not jobs:
        return True, ["(no jobs on %s)" % t.host]
    # server clock (skew-free heartbeat age); falls back to local time.
    now = (r.data or {}).get("now") or time.time()
    jobs.sort(key=lambda j: j.get("started", 0), reverse=True)  # newest first
    # The jobid column is sized to the DATA, not to a guessed constant: a
    # fixed %-38s was silently overflowed by any longer id, which shoved
    # every later column right and made the table ragged exactly when it was
    # busiest. Truncating instead would have been worse -- the id is the
    # argument you paste into watch/why/verify, so it has to stay whole.
    # The width is bought back by dropping FLOW/TARGET, which for a runjob id
    # (<flow>-<target>-<stamp>-<rand>) is already the id's own prefix; the
    # net table is NARROWER than the ragged one it replaces.
    w = max([len(j.get("jobid", "?")) for j in jobs] + [len("JOBID")])
    # $JOBS is on the shared NFS home, so this table is CLUSTER-WIDE: asking
    # asic6 and asic10 returns the same rows. Without the host column a
    # multi-host campaign is unreadable -- you cannot tell where anything is.
    hw = max([len(j.get("host") or "-") for j in jobs] + [len("HOST")])
    head = "%-*s %-9s %-*s %8s %7s %6s" % (w, "JOBID", "STATE", hw, "HOST",
                                           "ELAPSED", "HB-AGE", "PROG")
    lines = [head]

    def render(j):
        hb = j.get("heartbeat", 0)
        age = _age(hb, now) if hb else "-"
        frac = j.get("frac")
        if isinstance(frac, (int, float)):
            prog = "%d%%" % round(frac * 100)
        elif j.get("phase"):
            # counting a different unit than the caller's total (e.g. a
            # calibrated deck's cal phase): name it rather than render "-",
            # which is indistinguishable from a stalled job with no signal.
            prog = j["phase"][:6]
        else:
            prog = "-"
        jid = j.get("jobid", "?")
        row = "%-*s %-9s %-*s %8s %7s %6s" % (
            w, jid, _state_of(j), hw, j.get("host") or "-",
            _fmt_elapsed(j.get("elapsed_s", 0)), age, prog)
        # _SAFE_ARG allows '-' inside flow/target, so the id is not ALWAYS
        # decomposable. Only when it does not already carry them does the
        # pair get appended -- so nothing is ever lost, and a job whose id
        # and meta disagree is made visible rather than hidden.
        flow, target = j.get("flow", "?"), j.get("target", "?")
        if not jid.startswith("%s-%s-" % (flow, target)):
            row += "  %s/%s" % (flow, target)
        return row

    # bucket first, THEN emit: each group keeps the newest-first order above.
    buckets = {name: [] for name, _ in LS_GROUPS}
    buckets["other"] = []
    for j in jobs:
        st = _state_of(j)
        for name, states in LS_GROUPS:
            if st in states:
                buckets[name].append(j)
                break
        else:
            buckets["other"].append(j)
    for name in [n for n, _ in LS_GROUPS] + ["other"]:
        grp = buckets[name]
        if not grp:
            continue
        tag = "-- %s (%d) " % (name, len(grp))
        lines.append(tag + "-" * max(0, len(head) - len(tag)))
        lines.extend(render(j) for j in grp)
    return True, lines


def cmd_ls(t, ns):
    ok, lines = _ls_lines(t)
    for ln in lines:
        print(ln)
    return 0 if ok else 4


def cmd_top(t, ns):
    """Live all-jobs dashboard: refresh the ls table on an interval."""
    print("aj top on %s (Ctrl-C to stop)" % t.host)
    try:
        while True:
            ok, lines = _ls_lines(t)
            # clear screen + home, then repaint
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write("aj top -- %s -- %s  (refresh %gs, Ctrl-C)\n\n"
                             % (t.host, time.strftime("%H:%M:%S"),
                                ns.interval))
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            time.sleep(ns.interval)
    except KeyboardInterrupt:
        return 0


# --- watch ----------------------------------------------------------------

def _fmt_progress(d):
    """A compact ' | 42/256 (16%) eta 3m20s [route]' from a status dict, or
    '' when there is no progress signal."""
    p = d.get("progress")
    if not isinstance(p, dict):
        return ""
    bits = []
    if "done" in p and "total" in p:
        bits.append("%s/%s" % (p["done"], p["total"]))
    elif "done" in p:
        bits.append("%s done" % p["done"])
    if isinstance(p.get("frac"), (int, float)):
        bits.append("(%d%%)" % round(p["frac"] * 100))
    if "eta_s" in p:
        bits.append("eta " + _fmt_elapsed(p["eta_s"]))
    if p.get("label"):
        bits.append("[%s]" % p["label"])
    return " | " + " ".join(bits) if bits else ""


def _license_note(d):
    """A short WAITING_LICENSE note when the job's license snapshot shows no
    free seats, else ''."""
    lic = d.get("license")
    if isinstance(lic, dict) and lic.get("free") == 0:
        feat = str(lic.get("feature", "")).split("_")[0] or "license"
        return "WAITING_LICENSE (0/%s %s seats free)" % (
            lic.get("issued", "?"), feat)
    return ""


def _health(prev, cur):
    """Delta-based, clock-skew-free hung-vs-slow call across two polls.
    Uses whether the HEARTBEAT and PROGRESS advanced, the child's process
    state ('D' = NFS/IO wait, not a hang), and the license snapshot
    (0 free seats = WAITING_LICENSE, not a hang -- Phase 4)."""
    if prev is None:
        return ""
    hb_moved = cur.get("heartbeat") != prev.get("heartbeat")
    if not hb_moved:
        # the sidecar itself stopped writing -> suspect (host/sidecar), NOT
        # a claim the job is gone.
        return "  ! heartbeat stalled (sidecar/host suspect)"
    dc = (cur.get("progress") or {}).get("done")
    dp = (prev.get("progress") or {}).get("done")
    pstat = cur.get("pstat", "?")
    if dc is not None and dp is not None and dc == dp:
        lic = _license_note(cur)
        if lic:
            return "  ~ " + lic          # blocked on a seat, not hung
        if pstat.startswith("D"):
            return "  ~ no progress, pstat=D (NFS/IO wait, not hung)"
        return "  ~ no progress this interval (pstat=%s)" % pstat
    return ""


def cmd_watch(t, ns):
    print("watching %s on %s (Ctrl-C to stop)" % (ns.jobid, t.host))
    last = None
    prev = None
    while True:
        r = t.status(ns.jobid)
        if r.ok:
            d = r.data or {}
            state = d.get("state", "?")
            _lic = _license_note(d)
            line = "%s  state=%s elapsed=%s log=%sB pid=%s%s%s" % (
                time.strftime("%H:%M:%S"), state,
                _fmt_elapsed(d.get("elapsed_s", 0)),
                d.get("log_bytes", "?"), d.get("pid", "?"),
                _fmt_progress(d), "  " + _lic if _lic else "")
            health = _health(prev, d)
            if line != last or health:
                print(line + health)
                last = line
            prev = d
            if state in TERMINAL:
                print("-> terminal: %s" % state)
                return 0 if state == "done" else 3
        else:
            # a transport blip is NOT the end of the job -- say so and retry.
            print("%s  %s (%s)" % (time.strftime("%H:%M:%S"),
                                   r.status, r.reason))
        time.sleep(ns.interval)


# --- run ------------------------------------------------------------------

def cmd_run(t, ns):
    if not ns.cmd:
        print("jobs run: empty command (put it after `--`)", file=sys.stderr)
        return 2
    r = t.run(ns.cmd, flow=ns.flow, target=ns.target,
              interval=ns.interval, expect=ns.expect or [],
              progress=ns.progress, total=ns.total,
              progress_log=ns.progress_log)
    if not r.ok:
        print("jobs run: %s -- %s" % (r.status, r.reason), file=sys.stderr)
        return 4
    jobid = (r.data or {}).get("jobid", "?")
    print(jobid)
    print("launched on %s: %s" % (t.host, jobid), file=sys.stderr)
    print("  watch:  python3 -m jobs watch %s --host %s"
          % (jobid, t.host), file=sys.stderr)
    return 0


# --- why: status + result + live ps state (Phase 3) -----------------------

def cmd_why(t, ns):
    r = t.why(ns.jobid)
    if not r.ok:
        print("jobs why: %s -- %s" % (r.status, r.reason), file=sys.stderr)
        return 4
    d = r.data or {}
    if d.get("state") == "NOTFOUND":
        print("(no such job on %s)" % t.host)
        return 0
    st = d.get("status") or {}
    res = d.get("result") or {}
    ps = d.get("ps") or {}
    print("jobid : %s" % ns.jobid)
    print("state : %s" % (res.get("state") or st.get("state") or "?"))
    for k in ("host", "pid", "started", "heartbeat", "elapsed_s",
              "log_bytes"):
        if k in st:
            print("%-9s: %s" % (k, st[k]))
    prog = st.get("progress")
    if isinstance(prog, dict):
        print("progress :%s" % _fmt_progress({"progress": prog}))
    lic = st.get("license")
    if isinstance(lic, dict):
        print("license  : %s -- %s/%s free%s" % (
            lic.get("feature", "?"), lic.get("free", "?"),
            lic.get("issued", "?"),
            "  (WAITING_LICENSE)" if lic.get("free") == 0 else ""))
    if ps:
        if ps.get("alive"):
            print("ps       : alive stat=%s etimes=%ss%s" % (
                ps.get("stat", "?"), ps.get("etimes", "?"),
                "  (D=NFS/IO wait, not hung)"
                if str(ps.get("stat", "")).startswith("D") else ""))
        else:
            print("ps       : not alive (pid %s gone)" % ps.get("pid", "?"))
    if res:
        print("rc       : %s" % res.get("rc"))
        arts = res.get("artifacts") or []
        for a in arts:
            tag = "ok" if a.get("exists") else "MISSING"
            print("artifact : %s [%s]%s" % (
                a.get("path"), tag,
                "" if a.get("sha256") else " (unstamped)"))
    return 0


# --- verify: Class-D stale-artifact check ---------------------------------

def cmd_verify(t, ns):
    r = t.verify(ns.jobid)
    if not r.ok:
        print("jobs verify: %s -- %s" % (r.status, r.reason), file=sys.stderr)
        return 4
    d = r.data or {}
    v = d.get("verdict", "?")
    if v == "NOTFOUND":
        print("(no such job on %s)" % t.host)
        return 4
    if v == "UNSTAMPED":
        print("%s: UNSTAMPED (job recorded no hashable artifacts)" % ns.jobid)
        return 0
    print("%s: %s -- %d/%d artifact(s) match, %d stale" % (
        ns.jobid, v, d.get("ok", 0), d.get("checked", 0), d.get("stale", 0)))
    return 0 if v == "OK" else 3


# --- wait: block until job(s) finish -- the "tell me when it's done" (P3) --

def _terminal_events(t, n=200):
    r = t.events(n)
    if not r.ok:
        return None
    return {e.get("jobid"): e for e in (r.data or {}).get("events", [])
            if e.get("jobid")}


def cmd_wait(t, ns):
    targets = set(ns.jobid or [])
    # snapshot what has ALREADY finished, so we only announce NEW finishes --
    # except a named target that is already done, which we report at once.
    pre = _terminal_events(t) or {}
    for jid in list(targets):
        if jid in pre:
            e = pre[jid]
            print("%s finished: %s (rc=%s)" % (jid, e.get("state"),
                                               e.get("rc")))
            targets.discard(jid)
    if targets == set() and ns.jobid:
        return 0
    seen = set(pre)
    label = "any job" if not ns.jobid else " ".join(sorted(targets))
    print("waiting for %s on %s (Ctrl-C to stop)" % (label, t.host))
    while True:
        cur = _terminal_events(t)
        if cur is None:
            print("%s  UNKNOWN (transport) -- still waiting"
                  % time.strftime("%H:%M:%S"))
        else:
            for jid, e in cur.items():
                if jid in seen:
                    continue
                seen.add(jid)
                if ns.jobid and jid not in targets:
                    continue
                print("%s  %s finished: %s (rc=%s)" % (
                    time.strftime("%H:%M:%S"), jid, e.get("state"),
                    e.get("rc")))
                targets.discard(jid)
                if not ns.jobid:            # --any: first finish is enough
                    return 0 if e.get("state") == "done" else 3
            if ns.jobid and not targets:
                return 0
        time.sleep(ns.interval)


# --- entrypoint -----------------------------------------------------------

def build_parser():
    # global options live on a parent parser so they are accepted BEFORE or
    # AFTER the subcommand (`jobs --host h ls` and `jobs ls --host h` both).
    #
    # DEFAULTS ARE SUPPRESSED HERE, and that is a bug fix, not a style. A
    # parent-parser option also lives on every subparser, and the subparser
    # runs SECOND -- so its default overwrote whatever was parsed before the
    # subcommand, and `jobs --host asic7 ls` silently read asic6. The stated
    # purpose of the parent parser ("accepted BEFORE or AFTER the subcommand")
    # was only ever half true, and it failed the quiet way: the operator names
    # a host and the tool goes somewhere else.
    #
    # With SUPPRESS neither occurrence sets the attribute unless it was
    # actually given, so `--host` before the subcommand survives -- and
    # "was it named?" becomes a fact (`hasattr`) instead of a guess.
    g = argparse.ArgumentParser(add_help=False)
    g.add_argument("--host", default=argparse.SUPPRESS,
                   help="cluster host, or 'auto' to pick the least-loaded "
                        "(ASIC_HOST=auto works too). A READ goes to whichever "
                        "host answers unless you name one here.")
    g.add_argument("--mode", default=argparse.SUPPRESS,
                   help="wsl | winssh | ssh")
    g.add_argument("--timeout", type=float, default=argparse.SUPPRESS)

    p = argparse.ArgumentParser(prog="jobs", parents=[g],
                                description="cluster job status & control")
    sub = p.add_subparsers(dest="action", required=True)

    sub.add_parser("ls", parents=[g], help="list all jobs (one-shot)")

    hp = sub.add_parser("hosts", parents=[g],
                        help="survey candidate hosts by free capacity")
    hp.add_argument("--min-free", type=float, default=hosts.MIN_FREE)
    hp.add_argument("--flow", default=None,
                    help="weight by measured speed on this flow (e.g. enob)")

    tp = sub.add_parser("top", parents=[g],
                        help="live all-jobs dashboard (refreshing ls)")
    tp.add_argument("--interval", type=float, default=4.0)

    w = sub.add_parser("watch", parents=[g], help="follow one job until it ends")
    w.add_argument("jobid")
    w.add_argument("--interval", type=float, default=5.0)

    y = sub.add_parser("why", parents=[g], help="diagnosis bundle for one job")
    y.add_argument("jobid")

    v = sub.add_parser("verify", parents=[g],
                       help="re-check a job's stamped artifacts (STALE guard)")
    v.add_argument("jobid")

    wt = sub.add_parser("wait", parents=[g],
                        help="block until job(s) finish; no id waits for any")
    wt.add_argument("jobid", nargs="*")
    wt.add_argument("--interval", type=float, default=5.0)

    r = sub.add_parser("run", parents=[g],
                       help="launch a detached, self-reporting job")
    r.add_argument("--flow", default="job")
    r.add_argument("--target", default="run")
    r.add_argument("--interval", type=int, default=5)
    r.add_argument("--expect", action="append",
                   help="expected artifact path (repeatable)")
    r.add_argument("--progress", default=None,
                   help="live progress extractor: spectre|innovus|calibre|cocotb")
    r.add_argument("--total", type=int, default=None,
                   help="expected total (points/steps) for frac + ETA")
    r.add_argument("--progress-log", default=None,
                   help="log file to parse for progress (default stdout.log)")
    r.add_argument("cmd", nargs=argparse.REMAINDER,
                   help="-- CMD ... (the command to run on the cluster)")
    return p


def _job_history(host, mode, timeout):
    """Every host's finished jobs, in ONE call -- $JOBS is shared NFS, so
    any reachable host answers for the whole cluster. Returns [] on any
    failure: speed weighting is an enhancement, never a prerequisite."""
    try:
        r = remote.Transport(host=host, mode=mode, timeout=timeout).list()
    except Exception:
        return []
    return (r.data or {}).get("jobs", []) if r.ok else []


def cmd_hosts(t, ns):
    """Show what --host auto sees. Same probe, same history, same ranking --
    so the choice is inspectable rather than a black box."""
    jobs = _job_history(ns.host, ns.mode, ns.timeout)
    host, states = hosts.pick(min_free=ns.min_free, timeout=ns.timeout,
                              mode=ns.mode, jobs=jobs, flow=ns.flow)
    for ln in hosts.format_table(states):
        print(ln)
    if ns.flow:
        print("(SPEED = measured relative throughput on flow '%s')" % ns.flow)
    print("\npick (min-free %.1f): %s" % (ns.min_free, host or
                                          "NONE -- every host is busy"))
    return 0 if host else 3


def _resolve_host(ns):
    """'auto' -> the best host by measured speed, gated on free capacity.
    Only ever used when asked for explicitly; a silent change of WHERE work
    lands would be a nasty surprise for an operator who typed a host on
    purpose."""
    if (ns.host or "").lower() != "auto":
        return 0
    # ...but 'auto' is not a host, so the history call needs a real one.
    seed = os.environ.get("ASIC_SEED_HOST") or hosts.candidates()[0]
    flow = getattr(ns, "flow", None)
    jobs = _job_history(seed, ns.mode, ns.timeout)
    host, states = hosts.pick(timeout=ns.timeout, mode=ns.mode, jobs=jobs,
                              flow=flow)
    if host is None:
        print("jobs: --host auto found no host with >= %.1f free threads:"
              % hosts.MIN_FREE, file=sys.stderr)
        for ln in hosts.format_table(states):
            print("  " + ln, file=sys.stderr)
        return 4
    best = [s for s in states if s.host == host][0]
    sp = ("" if best.speed is None
          else ", %.2fx speed on '%s' (n=%d)" % (best.speed, flow, best.nsamp))
    print("jobs: --host auto -> %s (%.0f threads, load %.2f, %.1f free%s)"
          % (host, best.ncpu, best.load1, best.free, sp), file=sys.stderr)
    ns.host = host
    return 0


#: Subcommands that only ask the shared store questions. `run` is absent on
#: purpose; `hosts` is absent because it is ABOUT the hosts and must probe
#: each one itself.
_READ_ACTIONS = frozenset(("ls", "top", "watch", "why", "verify", "wait"))


def _fill_globals(ns):
    """Apply the suppressed defaults, and report whether a host was NAMED.

    The distinction is the whole point: `ns.host == "asic6"` cannot tell
    "asic6 because I said so" from "asic6 because nobody said anything", and
    that decides whether a read is allowed to go somewhere else. With
    SUPPRESS it is simply whether the attribute exists. `ASIC_HOST` counts as
    saying so -- an operator who exported it meant it.
    """
    named = hasattr(ns, "host") or bool(os.environ.get("ASIC_HOST"))
    if not hasattr(ns, "host"):
        ns.host = remote.DEFAULT_HOST
    if not hasattr(ns, "mode"):
        ns.mode = None
    if not hasattr(ns, "timeout"):
        ns.timeout = remote.DEFAULT_TIMEOUT
    return named


def main(argv=None):
    ns = build_parser().parse_args(argv)
    named = _fill_globals(ns)
    # argparse REMAINDER keeps a leading '--'; drop it.
    if getattr(ns, "cmd", None) and ns.cmd and ns.cmd[0] == "--":
        ns.cmd = ns.cmd[1:]
    rc = _resolve_host(ns)
    if rc:
        return rc
    def make(h):
        return remote.Transport(host=h, mode=ns.mode, timeout=ns.timeout)

    # A READ of $JOBS is a question about the shared NFS, not about a machine:
    # `_job_history` has said so in a docstring for as long as it has existed
    # ("any reachable host answers for the whole cluster"), and it is measured
    # -- asic6, asic7 and asic8 return the identical 389-job set. So a read
    # fails over across hosts unless the operator NAMED one, in which case
    # they meant it and get exactly that host.
    #
    # `run` is deliberately not in READ_CALLS: where work lands is a decision,
    # and `--host auto` is how you ask for that one to be made for you.
    if ns.action in _READ_ACTIONS and not named:
        t = hosts.SharedReader(make, fs=os.environ.get("ASIC_FS", "shared"),
                               prefer=ns.host)
    else:
        t = make(ns.host)
    dispatch = {"ls": cmd_ls, "top": cmd_top, "watch": cmd_watch,
                "run": cmd_run, "why": cmd_why, "verify": cmd_verify,
                "wait": cmd_wait, "hosts": cmd_hosts}
    try:
        return dispatch[ns.action](t, ns)
    except KeyboardInterrupt:
        print("\n(interrupted)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
