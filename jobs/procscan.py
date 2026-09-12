#!/usr/bin/env python3
"""procscan.py -- find EDA tool processes that are no longer doing anything for
anybody, and say which of them actually hold a licence seat.

Headless flows abandon processes. A worker whose driver died, a tool left
behind by a job that finished days ago, an interactive Virtuoso from two
months back. Nothing looks wrong, and `WAITING_LICENSE` on a fresh job is the
only symptom.

⚠️ THIS FILE USED TO ASSERT THAT SUCH PROCESSES HOLD SEATS. Measured
2026-08-07, that is false more often than it is true: of the three stale
sessions on asic8, the 85-day one and the 27-day one hold NOTHING, and only
the 70-day Virtuoso holds anything -- two CLIOSOFT SOS seats, not the Cadence
seat everybody assumed. Every seat claim in a report now comes from `lmstat`,
and "holds none" is printed as the result it is: killing that process frees
memory, not a licence.

Attribution is a CORRELATION, not a lookup. FlexLM's usage line carries user,
host, display, version, server, handle and start -- and no PID (checked: zero
usage lines on this server have a pid-shaped field). So sessions and seats are
joined on (host, user, start time), with the window taken from a measured
case: virtuoso started 12:45:53 and its seats are stamped 12:46 and 12:47.

  python3 procscan.py scan [--host asic7 ...]      # what is out there
  python3 procscan.py scan --json                  # machine-readable
  python3 procscan.py kill --host asic8 --pid 4151088 --expect virtuoso

WHAT THIS IS NOT: an "old processes" killer. Measured on this cluster, a bare
`ps -u` returns 87 processes on one host of which ~75 are a VNC/GNOME desktop
session, most of them ppid=1 and three weeks old. Age and orphanhood alone
would condemn the lot. So identity comes FIRST -- only processes under a known
tool root are ever considered -- and the evidence is reported per process
rather than collapsed into a verdict.

THE RULES, and where each one comes from:

- `driver-gone` (STRONG). `oa_worker.py` drives `virtuoso -restore worker.il`
  over its stdin/stdout PIPES. There is no other channel. So such a worker
  whose PPID is 1 has lost the only thing that could ever talk to it -- it is
  unreachable BY CONSTRUCTION, whatever its age. That is a fact about how the
  tool is driven, not a heuristic.
- `job-finished` (STRONG). Every launched job publishes `~/.asicjobs/<id>/
  status.json` with its pid, host and state. A tool process whose pid belongs
  to a job recorded done/failed/killed is a leftover of finished work.
- `job-running` (PROTECTS). The same records identify live work. A process
  under a running job is never offered, at any age.
- `idle` (WEAK). Old, and almost no CPU accumulated against its wall time.
  Deliberately INDEPENDENT of orphanhood: the 62-day Virtuoso measured on
  asic8 was launched from a VNC desktop that is still running, so it has a
  controlling terminal and a live session leader. A rule requiring "orphaned
  AND idle" called it healthy while it sat on a Cadence seat for two months.
- `orphan` (WEAK). No parent, and not claimed by a tool session either.
- `zombie` / `stopped`.

Sessions, not processes. A 62-day Virtuoso is 17 processes, and the Cadence
helpers DETACH to init while naming their session in their own arguments
(`-mpssession virtuoso4151088`). Grouping on ppid alone splits one session
into a tree plus half a dozen false orphans, and killing the root then leaves
the satellites behind.

Nothing is ever killed automatically, and nothing is pre-selected. The scan
reports; a human decides. The kill path re-verifies the command line before
signalling, because a pid observed in a scan may be a different process by the
time anyone clicks -- see `kill_procs`.

Usage:
  python3 procscan.py [--host] [--json] [--all] [--no-seats] --host --pid [--expect] [--force]
"""
import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import remote as _remote
except ImportError:                                    # pragma: no cover
    _remote = None

sys.path.insert(0, os.path.join(_HERE, "bin"))
try:
    import license as _license
except ImportError:                                    # pragma: no cover
    _license = None

#: Licence servers to ask when the tool env is not set -- which is always, over
#: a non-interactive ssh. Site facts, already documented in CLUSTER.md.
#:
#: SEVEN, not one. `license.py` knew only Cadence's 7183, and asking only that
#: gives a confidently wrong answer: measured 2026-08-07, the 70-day Virtuoso
#: on asic8 holds NO Cadence seat and two Cliosoft SOS seats on 7188.
LM_SERVERS = (
    "7183@iolicense2.inst.bnl.gov",     # Cadence
    "7184@iolicense2.inst.bnl.gov",     # Siemens/Calibre
    "7188@iolicense2.inst.bnl.gov",     # Cliosoft SOS
    "7180@iolicense2.inst.bnl.gov",     # HSPICE
    "7182@iolicense2.inst.bnl.gov",
    "7186@iolicense2.inst.bnl.gov",
    "7190@iolicense2.inst.bnl.gov",
)

#: Only processes whose command line mentions one of these are considered at
#: all. This is the whole reason the scan is safe to show a kill button next
#: to: a desktop session, a shell and an editor can never appear in it.
TOOL_ROOTS = ("/u/cad/", "/cad/")

#: path fragment -> licence family, for saying WHICH seat is held. Ordered:
#: first match wins.
VENDORS = (
    ("/u/cad/cds/", "Cadence"),
    ("/u/cad/mgc/", "Siemens/Mentor"),
    ("/u/cad/cliosoft/", "Cliosoft"),
    ("/u/cad/synopsys/", "Synopsys"),
    ("/u/cad/", "other"),
)

#: Tools that are DRIVEN OVER PIPES by a parent process, so losing the parent
#: makes them permanently unreachable. Matched against the full command line.
PIPE_DRIVEN = ("-restore worker.il",)

#: An orphan under this age is not called idle even if it is doing nothing --
#: a tool can legitimately sit still while a human thinks.
IDLE_MIN_AGE_S = 3 * 3600
#: cpu-seconds per wall-second below this is "not working". 0.5% of one core.
IDLE_CPU_RATIO = 0.005


class ScanError(Exception):
    """The scan could not be performed -- distinct from 'nothing found'."""


#: One round trip per host: processes, job records and our own ancestry, so
#: the three cannot describe different moments.
_PY_SCAN = r'''
import json, os, subprocess, glob

me = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
rows = []
try:
    out = subprocess.check_output(
        ["ps", "-u", me, "-o",
         "pid=,ppid=,stat=,etimes=,times=,rss=,tty=,args="],
        stderr=subprocess.STDOUT).decode("utf-8", "replace")
except Exception:
    out = ""
for ln in out.splitlines():
    f = ln.split(None, 7)
    if len(f) < 8:
        continue
    try:
        rows.append({"pid": int(f[0]), "ppid": int(f[1]), "stat": f[2],
                     "etimes": int(f[3]), "cpusec": int(f[4]),
                     "rss_kb": int(f[5]), "tty": f[6], "args": f[7][:400]})
    except ValueError:
        continue

jobs = {}
for p in glob.glob(os.path.expanduser("~/.asicjobs/*/status.json")):
    try:
        d = json.load(open(p))
    except Exception:
        continue
    pid = d.get("pid")
    if isinstance(pid, int):
        # keyed by (host, pid): pids are only unique per machine, and these
        # records span the whole cluster
        jobs["%s/%s" % (d.get("host"), pid)] = {
            "jobid": d.get("jobid"), "state": d.get("state"),
            "heartbeat": d.get("heartbeat")}

# Our own ancestry, so the scan can never propose killing the shell it is
# running in (or anything above it).
mine, pid = [], os.getpid()
byid = dict((r["pid"], r["ppid"]) for r in rows)
while pid and pid > 1 and pid not in mine:
    mine.append(pid)
    pid = byid.get(pid, 0)

# The clock this host's ages were read against. The licence query is a
# SEPARATE round trip ~90 s later, and anchoring a session start to that
# other clock skews every lag by the gap between them.
print(json.dumps({"schema": 1, "kind": "procscan", "host": os.uname()[1],
                  "user": me, "rows": rows, "jobs": jobs, "own": mine,
                  "now": int(__import__("time").time())}))
'''


def _wrap(py):
    """Python on stdin has to arrive as a SHELL script: remote.py pipes what
    it is given to `/bin/sh -s` (its invariant 2 -- never compose a remote
    command string), so a bare python program is read as sh and exits 2."""
    return "#!/bin/sh\nexec python3 - <<'EOF_PROCSCAN_PY'\n%s\nEOF_PROCSCAN_PY\n" % py


def _run(host, script, timeout=40.0):
    if _remote is None:
        raise ScanError("remote.py is not importable")
    res = _remote.Transport(host=host, timeout=timeout).run_sh(script)
    if not res.ok:
        # UNKNOWN is not "no processes" -- saying so would report a busy host
        # as clean, which is the exact failure this whole tool exists to stop.
        raise ScanError("%s: %s" % (res.status, res.reason or "no reason"))
    return res.data or {}


def vendor_of(args):
    for frag, name in VENDORS:
        if frag in args:
            return name
    return None


def is_tool(args):
    return any(frag in args for frag in TOOL_ROOTS)


def tool_name(args):
    """The binary's own name, for display: the full path is 150 characters."""
    first = (args or "").split()[0] if args else ""
    return os.path.basename(first) or "?"


#: Cadence helpers DETACH -- they reparent to init -- but they name the
#: session they belong to in their own arguments:
#:   cdsMsgServer  -mpssession virtuoso4151088
#:   libManager    -mpssession virtuoso4151088
#:   perfUtilExtCtrl 4151088 /dev/shm/...
#: Without reading that, a 20-process Virtuoso session reports as one big tree
#: plus half a dozen "orphans", and killing the root leaves the satellites --
#: each still holding whatever it holds.
_MPSSESSION = re.compile(r"-mpssession\s+\w*?(\d{2,})\b")
_PERFUTIL = re.compile(r"perfUtilExtCtrl\s+(\d{2,})\b")


def session_parent(row, by_pid):
    """The pid this process really belongs under, or None if it is a root."""
    ppid = row.get("ppid")
    parent = by_pid.get(ppid)
    if parent and is_tool(parent.get("args", "")):
        return ppid
    args = row.get("args", "")
    for rx in (_MPSSESSION, _PERFUTIL):
        m = rx.search(args)
        if m:
            claimed = int(m.group(1))
            if claimed != row["pid"] and claimed in by_pid:
                return claimed
    return None


def classify(data, host=None):
    """Attach evidence to every TOOL process. -> [proc, ...]

    Deliberately local, and pure: the rules are the interesting part, so they
    are testable without a cluster, and identical wherever they run.
    """
    host = host or data.get("host") or ""
    short = host.split(".")[0]
    rows = data.get("rows") or []
    jobs = data.get("jobs") or {}
    own = set(data.get("own") or [])
    by_pid = dict((r["pid"], r) for r in rows)

    tools_by_pid = dict((r["pid"], r) for r in rows
                        if is_tool(r.get("args", "")))
    out = []
    for r in rows:
        if not is_tool(r.get("args", "")):
            continue
        parent_pid = session_parent(r, tools_by_pid)
        child_of_session = parent_pid is not None
        ev, protect = [], []
        args = r["args"]
        age, cpu = r.get("etimes", 0), r.get("cpusec", 0)

        job = (jobs.get("%s/%s" % (short, r["pid"]))
               or jobs.get("%s/%s" % (host, r["pid"])))
        if job:
            state = (job.get("state") or "").lower()
            if state == "running":
                protect.append("job %s is running" % job.get("jobid"))
            else:
                ev.append(("job-finished",
                           "job %s ended (%s)" % (job.get("jobid"), state)))

        if r["pid"] in own or r.get("ppid") in own:
            protect.append("this scan's own session")

        if any(frag in args for frag in PIPE_DRIVEN) and r.get("ppid") == 1:
            ev.append(("driver-gone",
                       "pipe-driven worker with no parent -- nothing can "
                       "talk to it"))

        stat = r.get("stat", "")
        if stat.startswith("Z"):
            ev.append(("zombie", "zombie"))
        elif stat.startswith("T"):
            ev.append(("stopped", "stopped"))

        # IDLE and ORPHAN are separate signals, and conjoining them was a real
        # miss: the 62-day Virtuoso on asic8 was started from a VNC desktop
        # that is still up, so it HAS a controlling tty and is not orphaned --
        # and a rule requiring both reported it as fine while it sat on a
        # Cadence seat for two months. Idleness is the fact that matters; who
        # its parent is only says how it got that way.
        ratio = cpu / float(age or 1)
        if age > IDLE_MIN_AGE_S and ratio < IDLE_CPU_RATIO:
            ev.append(("idle", "%.0f h old, %.2f%% of one core over its life"
                       % (age / 3600.0, 100.0 * ratio)))
        if r.get("ppid") == 1 and not child_of_session:
            ev.append(("orphan", "no parent process"))

        # A parent that is itself a tool means this is part of a session, not
        # a thing to reason about alone -- the whole tree lives or dies with
        # its root.

        strong = [k for k, _ in ev if k in ("driver-gone", "job-finished",
                                            "zombie", "stopped")]
        out.append({
            "host": short, "pid": r["pid"], "ppid": r.get("ppid"),
            "stat": stat, "age_s": age, "cpu_s": cpu,
            "rss_mb": r.get("rss_kb", 0) // 1024, "tty": r.get("tty"),
            "name": tool_name(args), "args": args,
            "vendor": vendor_of(args), "job": job,
            "evidence": [{"kind": k, "why": w} for k, w in ev],
            "protected": protect,
            "session_parent": parent_pid,
            # SAFE means: something strong says it is finished, and nothing
            # says it is live. Weak evidence alone never qualifies.
            "safe_to_kill": bool(strong) and not protect,
            "suspect": bool(ev) and not protect,
        })
    out.sort(key=lambda p: (not p["safe_to_kill"], -p["age_s"]))
    return out


def sessions(procs):
    """Group processes into tool SESSIONS by walking to the top tool ancestor.

    A 62-day Virtuoso is not one process, it is twenty: cdsMsgServer,
    cdsServIpc, libManager, pvsgui, beanstalkd. Offering them as twenty rows
    invites killing a child, which achieves nothing, and hides that the
    licence is held by the root.
    """
    by_pid = dict((p["pid"], p) for p in procs)
    groups = {}
    for p in procs:
        root, seen = p, set()
        while root.get("session_parent") in by_pid and root["pid"] not in seen:
            seen.add(root["pid"])
            root = by_pid[root["session_parent"]]
        groups.setdefault(root["pid"], []).append(p)
    out = []
    for root_pid, members in groups.items():
        root = by_pid[root_pid]
        out.append({
            "host": root["host"], "root_pid": root_pid, "root": root,
            "members": sorted(members, key=lambda m: m["pid"]),
            "n": len(members),
            "rss_mb": sum(m["rss_mb"] for m in members),
            "age_s": root["age_s"], "vendor": root["vendor"],
            "safe_to_kill": root["safe_to_kill"],
            "protected": any(m["protected"] for m in members),
        })
    out.sort(key=lambda g: (not g["safe_to_kill"], -g["age_s"]))
    return out


#: Ask the licence servers which seats THIS user holds. Filtered at the source
#: -- only the caller's own usage lines and the feature headers cross the wire
#: -- so other people's usernames never reach a report, a log or a JSON dump.
#:
#: `lmstat` is located rather than assumed on PATH: the scan runs over a
#: non-interactive ssh, which has none of the tool env.
_PY_SEATS = r'''
import glob, json, os, subprocess, time

me = os.environ.get("USER") or os.environ.get("LOGNAME") or ""

candidates = ["lmstat"] + sorted(
    glob.glob("/u/cad/cds/*/tools/bin/lmstat"), reverse=True)

servers = []
for var in ("CDS_LIC_FILE", "LM_LICENSE_FILE", "MGLS_LICENSE_FILE",
            "ALL_LICENSE_FILES", "SNPSLMD_LICENSE_FILE"):
    for tok in os.environ.get(var, "").split(":"):
        tok = tok.strip()
        if "@" in tok and tok not in servers:
            servers.append(tok)
servers += [s for s in SERVERS_FALLBACK if s not in servers]

def ask(binary, srv):
    """Real query or None. Validating a binary by DOING the query is the only
    check that means anything: `-help` exits 255 on every FlexLM build here,
    and an old lmstat talks to the socket happily and reports nothing."""
    try:
        out = subprocess.check_output([binary, "-c", srv, "-a"],
                                      stderr=subprocess.STDOUT,
                                      timeout=25).decode("utf-8", "replace")
    except Exception:
        return None
    return out if "Users of" in out else None

# DISCOVERY RUNS ONCE, and it sweeps CANDIDATES on the outside and servers on
# the inside. The transpose is the whole fix. Sweeping servers on the outside
# meant a single DOWN server re-probed all 144 binaries under it -- measured at
# 70.6 s of a 92.9 s query, for a server that was never going to answer.
#
# The invariant that makes one sweep enough: if any binary answered a server,
# that binary is good; and once a GOOD binary gets nothing from a server, the
# SERVER is down and no other binary will help. Re-probing there is not a
# retry, it is a category error.
found = {}
lmstat = ""
budget = time.time() + 30.0
for c in candidates:
    for srv in servers:
        out = ask(c, srv)
        if out is not None:
            lmstat, found[srv] = c, out
            break
        if time.time() > budget:
            break
    if lmstat or time.time() > budget:
        break

# THE REST SEQUENTIALLY AND PACED, both measured rather than assumed.
#
# PARALLEL IS WORSE, not better. The daemon serialises and then charges about
# 5 s per concurrent connection. On asic8, the same two servers:
#
#     alone    7184  0.5 s      7180  0.0 s
#     paired   7184 10.5 s      7180  5.0 s
#
# A seven-way pool measured 15.5 s with the slowest single query also 15.5 s --
# nothing overlapped and the pool only bought the penalty. If this looks like
# an easy win later, it is not.
#
# THE 5 s HITS ARE A RATE LIMIT, not server latency, which is what the pacing
# is for. Total for all seven, by gap between queries:
#
#     gap 0.0 s -> 16.3 s   (three servers penalised)
#     gap 0.5 s ->  9.8 s   (one penalised)
#     gap 0.8 s ->  6.9 s   (none)
#     gap 1.0 s ->  8.3 s   (none)   <- chosen
#     gap 1.5 s -> 11.8 s   (none)
#
# The queries themselves total 1.3 s once unpenalised. 0.8 s measured clean and
# is quicker, but a missed gap costs 5 s while an extra 0.2 s costs 0.2 s, so
# the margin is worth having.
PACE_S = 1.0

def query(srv):
    if srv in found:
        return srv, found[srv]
    return srv, (ask(lmstat, srv) if lmstat else None)

results = []
for i, s in enumerate(servers):
    if i and s not in found:
        time.sleep(PACE_S)
    results.append(query(s))

kept, asked, failed = [], [], []
for srv, out in results:
    if out is None:
        failed.append(srv)
        continue
    asked.append(srv)
    for ln in out.splitlines():
        if ln.startswith("Users of ") or ln.strip().split(" ")[0:1] == [me]:
            kept.append(ln)

print(json.dumps({"schema": 1, "kind": "lmseats", "user": me,
                  "lmstat": lmstat, "asked": asked, "failed": failed,
                  "lines": kept, "now": int(time.time())}))
'''


def _seats_script():
    """`_PY_SEATS` with the site's server list substituted in."""
    return _PY_SEATS.replace("SERVERS_FALLBACK", repr(list(LM_SERVERS)))


#: One `lmstat -a` per server, paced, and there are seven. MEASURED end to end
#: from asic8 at 7-12 s -- it was 92.9 s before the discovery fix and the
#: pacing, both explained in the snippet above. The ceiling stays generous
#: because the failure this replaces was a query that WORKED while the report
#: said "not checked": 60 s was too short and nothing said so.
SEATS_TIMEOUT_S = 120.0


def seats_on(host, timeout=SEATS_TIMEOUT_S):
    """{seats: [...], asked: [...], failed: [...], now: int} for one host.

    Any host will do -- the licence servers are central -- but it has to be a
    host that can reach them, so this takes the same host list the scan does.

    Costs about ten seconds. `scan --no-seats` skips it.
    """
    data = _run(host, _wrap(_seats_script()), timeout)
    text = "\n".join(data.get("lines") or [])
    parsed = []
    if _license is not None:
        parsed = _license.parse_usage(text, user=data.get("user") or None)
    return {"seats": parsed, "asked": data.get("asked") or [],
            "failed": data.get("failed") or [], "now": data.get("now"),
            "user": data.get("user")}


def _seat_start_epoch(start, now):
    """`Fri 8/7 10:03` -> epoch seconds, using `now` to supply the year.

    FlexLM omits the year. Taking the current one is right except across New
    Year, where it puts a December checkout eleven months in the FUTURE -- so a
    start that lands ahead of `now` rolls back a year.
    """
    m = re.match(r"^\w{3}\s+(\d+)/(\d+)\s+(\d+):(\d+)$", (start or "").strip())
    if not m:
        return None
    mon, day, hh, mm = (int(g) for g in m.groups())
    ref = time.localtime(now)
    for year in (ref.tm_year, ref.tm_year - 1):
        try:
            t = time.mktime((year, mon, day, hh, mm, 0, 0, 1, -1))
        except (ValueError, OverflowError):
            continue
        if t <= now + 86400:
            return t
    return None


#: How long after a process starts its licence checkout may appear. MEASURED,
#: not chosen: the 70-day Virtuoso on asic8 started 12:45:53 and its two
#: Cliosoft seats are stamped 12:46 and 12:47 -- 7 s and 67 s later. The window
#: is generous forward because a tool can check out a feature long after
#: launch (opening SOS from an already-running Virtuoso), and only one minute
#: backward because lmstat truncates the start to the minute.
SEAT_LAG_BACK_S = 90
SEAT_LAG_FWD_S = 3600


def attribute(sessions, seats, now):
    """Correlate held seats with tool SESSIONS. -> sessions, each with `seats`.

    ⚠️ THIS IS A CORRELATION, NOT A LOOKUP, and the field names say so.
    Measured on this server: FlexLM's usage line carries user, host, display,
    version, server, handle and start -- and NO PID. The number after the port
    is FlexLM's own handle. So the join is on (host, user, start time) and it
    can be ambiguous; when it is, every candidate is reported rather than one
    being picked.

    A session with no seat is a RESULT, not a gap -- it is the one that says
    killing this process frees memory rather than a licence. `seats_checked`
    distinguishes it from "nobody asked".
    """
    by_host = {}
    for s in seats:
        short = (s.get("host") or "").split(".")[0]
        by_host.setdefault(short, []).append(s)
    for sess in sessions:
        host = (sess.get("host") or "").split(".")[0]
        start = now - (sess.get("age_s") or 0)
        got = []
        for s in by_host.get(host, []):
            t = _seat_start_epoch(s.get("start"), now)
            if t is None:
                continue
            delta = t - start
            if -SEAT_LAG_BACK_S <= delta <= SEAT_LAG_FWD_S:
                got.append({"feature": s["feature"], "server": s["server"],
                            "port": s["port"], "handle": s["handle"],
                            "start": s["start"], "lag_s": int(delta)})
        got.sort(key=lambda g: (abs(g["lag_s"]), g["feature"]))
        sess["seats"] = got
    return sessions


def scan(hosts, timeout=40.0):
    """-> {host: {"procs": [...], "sessions": [...], "error": str|None}}"""
    result = {}
    for h in hosts:
        try:
            data = _run(h, _wrap(_PY_SCAN), timeout)
            procs = classify(data, h)
            result[h] = {"procs": procs, "sessions": sessions(procs),
                         "error": None, "user": data.get("user"),
                         "now": data.get("now")}
        except ScanError as exc:
            result[h] = {"procs": [], "sessions": [], "error": str(exc)}
    return result


#: Kill script. Takes pid+expected substring; verifies BOTH before signalling.
_PY_KILL = r'''
import json, os, signal, subprocess, sys, time

pid = int(os.environ["PS_PID"])
expect = os.environ.get("PS_EXPECT", "")
hard = os.environ.get("PS_HARD") == "1"

def cmdline(p):
    try:
        with open("/proc/%d/cmdline" % p, "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return None

def emit(**kw):
    kw["schema"] = 1
    print(json.dumps(kw))
    raise SystemExit(0)

cur = cmdline(pid)
if cur is None:
    emit(kind="kill", pid=pid, result="gone", note="already exited")
# THE reason this check exists: a pid seen in a scan may be a different
# process by the time anyone clicks. Refusing on mismatch is the difference
# between killing a stale tool and killing whatever inherited its pid.
if expect and expect not in cur:
    emit(kind="kill", pid=pid, result="mismatch", current=cur[:200],
         note="command line no longer matches what was scanned")
try:
    st = os.stat("/proc/%d" % pid)
except OSError:
    emit(kind="kill", pid=pid, result="gone")
if st.st_uid != os.getuid():
    emit(kind="kill", pid=pid, result="refused", note="not owned by you")

os.kill(pid, signal.SIGTERM)
for _ in range(20):                       # up to ~5 s for a clean exit
    time.sleep(0.25)
    if cmdline(pid) is None:
        emit(kind="kill", pid=pid, result="terminated", signal="TERM")
if not hard:
    emit(kind="kill", pid=pid, result="still-running", signal="TERM",
         note="did not exit on TERM; re-run with force to send KILL")
os.kill(pid, signal.SIGKILL)
time.sleep(0.5)
emit(kind="kill", pid=pid,
     result="killed" if cmdline(pid) is None else "still-running",
     signal="KILL")
'''


def kill_procs(host, pid, expect="", hard=False, timeout=30.0):
    """TERM (then optionally KILL) one process, after re-verifying it.

    `expect` is a substring of the command line as it was when scanned. The
    remote side refuses if it no longer matches, which is what stops a pid
    recycled since the scan from being signalled by mistake.
    """
    script = ("#!/bin/sh\nPS_PID=%d\nPS_EXPECT=%s\nPS_HARD=%d\n"
              "export PS_PID PS_EXPECT PS_HARD\n"
              % (int(pid), _sh_quote(expect), 1 if hard else 0)
              + "exec python3 - <<'EOF_KILL'\n" + _PY_KILL + "\nEOF_KILL\n")
    return _run(host, script, timeout)


def _sh_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


# ---------------------------------------------------------------------- CLI

def _fmt_age(s):
    if s >= 86400:
        return "%.1fd" % (s / 86400.0)
    if s >= 3600:
        return "%.1fh" % (s / 3600.0)
    return "%dm" % (s // 60)


def _main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("scan")
    s.add_argument("--host", action="append", default=[])
    s.add_argument("--json", action="store_true")
    s.add_argument("--all", action="store_true",
                   help="every tool process, not just the suspect ones")
    s.add_argument("--no-seats", action="store_true",
                   help="skip the licence query (one lmstat round trip)")

    k = sub.add_parser("kill")
    k.add_argument("--host", required=True)
    k.add_argument("--pid", type=int, required=True)
    k.add_argument("--expect", default="")
    k.add_argument("--force", action="store_true")

    a = ap.parse_args(argv)
    if a.cmd == "kill":
        got = kill_procs(a.host, a.pid, a.expect, a.force)
        print(json.dumps(got))
        return 0 if got.get("result") in ("terminated", "killed",
                                          "gone") else 1

    hosts = a.host or default_hosts()
    result = scan(hosts)

    # Seats, once: the licence servers are central, so any host that answers
    # will do. Failure here degrades the report, never the scan -- and it is
    # recorded per host so "holds nothing" is never confused with "not asked".
    seatinfo = {"seats": [], "asked": [], "failed": [], "error": None}
    if not a.no_seats:
        sys.stderr.write("[procscan] asking %d licence server(s), ~10 s "
                         "(--no-seats to skip)\n" % len(LM_SERVERS))
        sys.stderr.flush()
        for h in hosts:
            if result[h]["error"]:
                continue
            try:
                seatinfo = seats_on(h)
                break
            except ScanError as exc:
                seatinfo["error"] = str(exc)
    checked = bool(seatinfo["asked"])
    for h in hosts:
        if not result[h]["error"]:
            # THIS host's scan clock, not the licence query's: the two are
            # ~90 s apart and the gap lands directly in every reported lag.
            attribute(result[h]["sessions"], seatinfo["seats"],
                      result[h].get("now") or time.time())
            result[h]["seats_checked"] = checked
    result["_licences"] = {"asked": seatinfo["asked"],
                           "failed": seatinfo["failed"],
                           "error": seatinfo.get("error"), "checked": checked}

    if a.json:
        print(json.dumps(result, indent=1))
        return 0
    total = 0
    for h in hosts:
        r = result[h]
        if r["error"]:
            print("%-9s !! %s" % (h, r["error"]))
            continue
        groups = [g for g in r["sessions"] if a.all or g["root"]["suspect"]
                  or g["safe_to_kill"]]
        if not groups:
            print("%-9s ok" % h)
            continue
        print("%-9s %d session(s)" % (h, len(groups)))
        for g in groups:
            root = g["root"]
            mark = "KILLABLE" if g["safe_to_kill"] else (
                "protected" if g["protected"] else "suspect")
            print("   [%-8s] %-18s pid %-8d %6s  %4d proc  %5d MB  %s"
                  % (mark, root["name"], root["pid"], _fmt_age(g["age_s"]),
                     g["n"], g["rss_mb"], root["vendor"] or "?"))
            for e in root["evidence"]:
                print("               - %s: %s" % (e["kind"], e["why"]))
            for p in root["protected"]:
                print("               + protected: %s" % p)
            # Seats LAST, because it is the line that decides what killing
            # this actually buys. "holds no seat" is a result, and it is
            # spelled differently from "nobody asked".
            if not checked:
                # WHY, not just "no". A silent "not checked" reads as "nothing
                # held", which is the confusion this whole feature removes.
                why = (result.get("_licences") or {}).get("error")
                print("               ? licences: not checked%s"
                      % (" (%s)" % why if why else ""))
            elif g.get("seats"):
                for x in g["seats"]:
                    print("               $ licence: %s on %s (checked out "
                          "%ds after start)"
                          % (x["feature"], x["server"], x["lag_s"]))
            else:
                print("               $ licences: none held "
                      "-- killing this frees memory, not a seat")
            total += 1
    lic_state = result.get("_licences") or {}
    if lic_state.get("failed"):
        print("\n%d licence server(s) did not answer: %s"
              % (len(lic_state["failed"]), ", ".join(lic_state["failed"])))
    if total:
        print("\nNothing was killed. To act on one:")
        print("  python3 procscan.py kill --host <h> --pid <p> "
              "--expect <substring>")
    return 0


def default_hosts():
    try:
        import hosts as _hosts
        return list(_hosts.candidates())
    except Exception:                                  # noqa: BLE001
        return ["asic6", "asic7"]


if __name__ == "__main__":
    sys.exit(_main())
