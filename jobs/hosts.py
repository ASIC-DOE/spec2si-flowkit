#!/usr/bin/env python3
"""Pick a cluster host to launch on, from MEASURED free capacity.

Why this exists: `jobs run` defaults to one host, so a campaign lands
entirely on whichever box the operator typed first. On 2026-07-21 four
spectre runs were queued onto asic6 (load 33 on 16 cores, sharing it with a
Virtuoso worker and another user) while eight other hosts sat below load
0.4 -- roughly a 2.3x slowdown, self-inflicted and invisible until someone
looked. `--host auto` removes the foot-gun: probe the candidates, rank by
free threads, launch on the best.

Ranking is `free = ncpu - load1`, not load alone -- load 4 is idle on a
32-thread box and busy on a 20-thread one. A host whose probe does not come
back KNOWN is never a candidate: an unreachable host is unknown, not empty
(remote.py invariants 5 and 7).

Speed is MEASURED on identical work: calibration timings (calib.py -- a
committed ledger of cited runs plus tracked `calibration` jobs from `jobs
calibrate`), fitted as log t(suite, host) = a(suite) - s(host). Until
2026-11-03 it came from production jobs' rates, which count each job's own
units, so it measured which jobs a host was given: two identical Xeon
W-2155s read 24x and 0.08x on spectre-campaign, and asic8 (11.9x) was picked
over the 9950X3D for chip P&R. A host with no calibration of its own
borrows the measurement of hosts with the SAME CPU MODEL, which the probe
reports (`pool_by_cpu`); CANDIDATES order only breaks ties.

`--threads N` scores the job's own demand: speed x min(1, free/N), so a fast
host with part of the threads free is weighed against an idle slow one
instead of being gated in or out by a fixed MIN_FREE.
"""
import os

try:
    from . import remote                     # package import
    from . import calib
except ImportError:                          # direct-script fallback
    import remote
    import calib

#: Spectre/Calibre-capable hosts, fastest-per-core first (TIE-BREAK ONLY).
#: exxact and dgx-spark are deliberately ABSENT: they are Ubuntu GPU boxes
#: with no ASIC toolchain, so their permanent idleness is a trap, not an
#: opportunity. asic5 is absent because it has been down for weeks.
#: Override with ASIC_HOSTS="asic7 asic8" for a campaign that should stay
#: inside a subset.
CANDIDATES = ("asic6", "asic10", "asic9", "asic8", "asic7", "asic2",
              "asic1", "asic3", "asic4", "asicdesign", "pmos")

#: A host with less than this many free threads is not worth launching on:
#: below it the new job mostly fights whatever is already there.
MIN_FREE = 6.0


def candidates():
    env = os.environ.get("ASIC_HOSTS", "").split()
    return tuple(env) if env else CANDIDATES


class HostState(object):
    """free is None when the host could not be measured -- NEVER a launch
    target, and distinct from a measured zero. speed is None until enough
    finished jobs exist to measure it; 1.0 means "typical", not "unknown"."""

    def __init__(self, host, free=None, ncpu=None, load1=None, why="",
                 speed=None, nsamp=0, cpu=None, mode=None):
        self.host, self.free, self.ncpu = host, free, ncpu
        self.load1, self.why = load1, why
        self.speed, self.nsamp = speed, nsamp
        #: the CPU model the probe reported ('' / None from an older reader)
        self.cpu = cpu
        #: where `speed` came from: 'host' (its own jobs), 'cpu' (pooled over
        #: hosts with the same CPU model) or None (no measurement: 1.0)
        self.speed_src = None
        #: speed x min(1, free/threads) when pick() was given `threads`
        self.score = None
        #: the transport mode that answered, when it is not the default
        self.mode = mode

    def __repr__(self):
        return "HostState(%s, free=%s, speed=%s, why=%r)" % (
            self.host, self.free, self.speed, self.why)


#: On Windows the default transport is WSL ssh (ControlMaster); when WSL's ssh
#: cannot reach a host (measured 2026-10-01: every probe "bundle install
#: failed" or timed out at 20 s) the Windows OpenSSH client still can, given a
#: cold-connect allowance. Only a probe falls back -- it reads, it launches
#: nothing -- and the state says which transport answered.
FALLBACK_MODE = "winssh"
FALLBACK_TIMEOUT = 45.0


def _windows():
    """os.name == 'nt' -- a function so the fallback can be tested anywhere"""
    return os.name == "nt"


def _probe_raw(host, timeout, mode):
    try:
        t = remote.Transport(host=host, mode=mode, timeout=timeout)
        return t.probe(), None
    except Exception as e:                    # transport construction/IO
        return None, "probe error: %s" % e


def _probe_one(host, timeout, mode):
    r, err = _probe_raw(host, timeout, mode)
    used = None
    if (r is None or not r.ok) and mode is None and _windows() \
            and not os.environ.get("ASICJOBS_RSH"):
        r2, err2 = _probe_raw(host, max(timeout, FALLBACK_TIMEOUT), FALLBACK_MODE)
        if r2 is not None and r2.ok:
            r, err, used = r2, None, FALLBACK_MODE
    if r is None:
        return HostState(host, why=err)
    if not r.ok:
        return HostState(host, why="%s: %s" % (r.status, r.reason or "?"))
    d = r.data or {}
    if not d.get("jobs_dir_ok"):
        # the shared $JOBS is how a job reports itself; a host that cannot
        # see it would run blind.
        return HostState(host, why="jobs dir not visible")
    try:
        ncpu = float(d.get("ncpu") or 0)
        load = float(d.get("load1") or 0)
    except (TypeError, ValueError):
        return HostState(host, why="unparsable ncpu/load1")
    cpu = d.get("cpu") or None
    if ncpu <= 0:
        # an older bundle predates the ncpu field; rank it last rather than
        # guessing a core count.
        return HostState(host, why="no ncpu (stale reader?)", load1=load,
                         cpu=cpu, mode=used)
    return HostState(host, free=ncpu - load, ncpu=ncpu, load1=load, cpu=cpu,
                     mode=used)


def survey(hosts=None, timeout=12.0, mode=None, workers=12):
    """Probe every candidate in parallel -> [HostState], best free first,
    unmeasurable hosts last (in candidate order)."""
    hs = list(hosts or candidates())
    out = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(workers, len(hs) or 1)) as ex:
            out = list(ex.map(lambda h: _probe_one(h, timeout, mode), hs))
    except ImportError:                       # serial fallback
        out = [_probe_one(h, timeout, mode) for h in hs]
    order = {h: i for i, h in enumerate(hs)}
    out.sort(key=lambda s: (s.free is None, -(s.free or 0), order[s.host]))
    return out


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def speeds(jobs=None, flow=None, ledger=None):
    """host -> (relative_speed, n_suites), from CALIBRATION (calib.py): the
    committed ledger plus the tracked `calibration` jobs in `jobs`. 1.0 is
    the median calibrated host, so a host with no measurement can sit at a
    neutral 1.0 rather than being scored as if it were slow.

    `flow` is accepted and ignored: a production flow's rates count its own
    units and cannot compare hosts (calib.py's header has the measurement).
    `ledger` overrides the committed ledger ([] = tracked jobs only)."""
    return calib.speeds(jobs, ledger=ledger)


#: The last survey, one per USER, for the session hooks of every spec2si repo
#: (integrations/cluster_jobs/hook.py reads the same path -- test_hooks holds
#: the two to the same value). Written by `hosts` and by a workflow `auto`.
SURVEY_ENV = "SPEC2SI_HOST_SURVEY"


def survey_cache_path():
    return os.environ.get(SURVEY_ENV) or os.path.join(
        os.path.expanduser("~"), ".spec2si", "host_survey.json")


def save_survey(states, picked, threads=None, flow=None, path=None):
    """Best effort: a cache that cannot be written costs advice, nothing else."""
    import json
    import tempfile
    import time as _t
    path = path or survey_cache_path()
    best = max([s.score for s in states if s.score] or [0])
    rows = []
    for s in states:
        rows.append(dict(host=s.host, cpu=_cpu_short(s.cpu), free=s.free,
                         speed=s.speed, speed_src=s.speed_src,
                         wallx=(round(best / s.score, 2) if (threads and s.score) else None),
                         why=s.why or None))
    rec = dict(schema=1, time=_t.time(), threads=threads, flow=flow, picked=picked, rows=rows)
    try:
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".survey-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def job_history(hosts=None, mode=None, timeout=30.0, tries=2):
    """Every host's finished jobs, read ONCE from the shared $JOBS (one NFS,
    so any host answers for all; see `shared_read`). Tries at most `tries`
    hosts per transport -- a dead transport must not cost a launch minutes --
    and on Windows falls back to winssh like `_probe_one`. [] on any failure:
    speed is an enhancement, never a prerequisite."""
    hs = list(hosts or candidates())[:max(1, tries)]

    def read(m, tmo):
        res, _h, _s = shared_read(
            lambda h: remote.Transport(host=h, mode=m, timeout=tmo).list(),
            hosts=hs, fs="jobs-history-%s" % (m or "default"))
        return res
    res = read(mode, timeout)
    if (res is None or not res.ok) and mode is None and _windows() \
            and not os.environ.get("ASICJOBS_RSH"):
        res = read(FALLBACK_MODE, max(timeout, FALLBACK_TIMEOUT))
    if res is None or not res.ok:
        return []
    return (res.data or {}).get("jobs", []) or []


#: A host with fewer calibrated suites than this borrows the pooled speed of
#: every host with the same CPU model. One suite is a measurement of the host
#: itself (the fit already takes its fastest run), so only an uncalibrated host
#: borrows.
MIN_OWN_SAMPLES = 1


def pool_by_cpu(states, min_own=MIN_OWN_SAMPLES):
    """Fill missing speeds from identical machines, in place.

    Hosts are grouped by the CPU model their probe reported; a host with
    fewer than `min_own` calibrated suites takes the suite-weighted geometric
    mean over its group. A host with no CPU model (an older reader) is never
    pooled -- two blanks are not evidence of the same machine.
    """
    import math
    groups = {}
    for s in states:
        if s.cpu and s.speed is not None:
            g = groups.setdefault(s.cpu, [0.0, 0])
            g[0] += s.nsamp * math.log(s.speed)
            g[1] += s.nsamp
    for s in states:
        if s.speed is not None:
            s.speed_src = "cal"
        if not s.cpu or s.cpu not in groups:
            continue
        lw, n = groups[s.cpu]
        if n and s.nsamp < min_own and n > s.nsamp:
            s.speed, s.nsamp, s.speed_src = math.exp(lw / n), n, "cpu"
    return states


def pick(hosts=None, min_free=MIN_FREE, timeout=12.0, mode=None,
         jobs=None, flow=None, threads=None, ledger=None):
    """(host, states) -- the best host with at least `min_free` threads, or
    (None, states) when none qualifies. Never silently falls back to a busy
    host.

    The ranking is by CALIBRATED relative speed (`speeds`: the ledger plus
    any tracked calibration jobs in `jobs`), not free capacity: on 2026-07-21
    capacity-only ranking sent a run to an idle Xeon W-2265 over a partly-
    loaded Ryzen 9950X3D and it ran 3-5x slower -- free threads say how much
    room there is, not how fast the work will go. Capacity remains a hard
    gate and the tie-break; an uncalibrated host borrows its CPU model's speed
    (`pool_by_cpu`) or scores a neutral 1.0. `flow` is recorded by callers and
    does not affect speed; `ledger=[]` ranks on tracked calibration jobs only.

    With `threads` (the job's own thread count, e.g. spectre +mt=16) the
    ranking is by `score = speed x min(1, free / threads)`: the throughput the
    job can expect there. A fast host with half the threads it wants free
    scores half -- weighed honestly against an idle slow host rather than
    passed by the fixed MIN_FREE gate as if room did not matter.
    """
    states = survey(hosts, timeout=timeout, mode=mode)
    # case-insensitive: the ssh alias is `pmos` but that box's own
    # `hostname -s` answers PMOS, so meta.json records PMOS and an
    # exact match silently dropped every sample it ever produced.
    sp = {k.lower(): v for k, v in speeds(jobs, ledger=ledger).items()}
    for s in states:
        if s.host.lower() in sp:
            s.speed, s.nsamp = sp[s.host.lower()]
    pool_by_cpu(states)
    for s in states:
        if s.free is not None and threads:
            sp1 = s.speed if s.speed is not None else 1.0
            s.score = sp1 * max(0.0, min(1.0, s.free / float(threads)))
    ok = [s for s in states if s.free is not None and s.free >= min_free]
    if not ok:
        return None, states
    order = {s.host: i for i, s in enumerate(states)}
    if threads:
        ok.sort(key=lambda s: (-s.score, -s.free, order[s.host]))
    else:
        ok.sort(key=lambda s: (-(s.speed if s.speed is not None else 1.0),
                               -s.free, order[s.host]))
    return ok[0].host, states


# --------------------------------------------------------------------------
# READING the shared store, which is a different question from launching.
#
# `pick()` above answers "where should this job RUN" -- a question about
# capacity, and the answer matters because the work lands there. Reading
# `$JOBS` is not that question at all: the cluster homes are ONE NFS, so every
# host sees the same records and the host that performs the read is
# PROVENANCE, not scope.
#
# Measured, not assumed -- the same standard `browse/roots.py` holds its `fs`
# declaration to. `list` run against asic6, asic7 and asic8 returns the
# IDENTICAL 389-job set (sha b7a1a6ab8836), and those records name 11
# different hosts as where the work ran. What differs is only the latency:
#
#     asic6   5.90 s      asic8  13.93 s      asic7  22.99 s
#
# A 3.9x spread. So hardcoding one host was not wrong about WHAT you see -- it
# was an unexplained bet on reachability and speed, and a single point of
# failure for a read that any host can serve.
#
#: How long a remembered choice stays good. Long enough that a panel polling
#: every minute does not re-race the hosts; short enough that a box coming
#: back from maintenance is used again the same session.
READER_TTL = 600.0
_READER = {}                      # fs -> (chosen_at, host, seconds)


def reader_reset(fs=None):
    """Forget the remembered reader (tests, and an explicit refresh)."""
    for k in [k for k in _READER if fs is None or k == fs]:
        del _READER[k]


def reader_state():
    """{fs: (host, seconds, age_s)} -- what is remembered and how stale."""
    import time as _t
    now = _t.time()
    return {k: (v[1], v[2], now - v[0]) for k, v in _READER.items()}


def shared_read(fn, hosts=None, fs="shared", ttl=READER_TTL, prefer=None):
    """Run `fn(host)` on the first host that answers. -> (result, host, secs)

    `fn` must return something with `.ok` (a remote.Result). THE READ IS THE
    PROBE: a separate reachability check would double the round trips to learn
    something the read itself is about to tell us, and on the common path
    (the remembered host answers) this costs exactly one call.

    Failover is the point. A read of a shared store has no reason to fail
    because one box is down -- and before this, every job query in the tool
    went to whatever `ASIC_HOST` said and stopped there.

    Returns `(None, None, 0.0)` when NO host answered, which the caller must
    report as UNKNOWN rather than as an empty store: "I could not reach the
    cluster" is not "nothing is running" (remote.py invariant 5), and that
    distinction is exactly what a jobs panel must not lose.
    """
    import time as _t
    order = []
    if prefer:
        order.append(prefer)
    hit = _READER.get(fs)
    if hit and _t.time() - hit[0] < ttl and hit[1] not in order:
        order.append(hit[1])
    for h in (hosts or candidates()):
        if h not in order:
            order.append(h)
    for h in order:
        t0 = _t.time()
        try:
            res = fn(h)
        except Exception:                     # transport/IO -- try the next
            continue
        secs = _t.time() - t0
        if getattr(res, "ok", False):
            # Remember the host that ANSWERED, with what it cost. Not the
            # fastest ever seen: a host that was quick last week and is
            # swamped today should be replaced by measurement, and the only
            # measurement we get is the read we just did.
            _READER[fs] = (_t.time(), h, secs)
            return res, h, secs
    return None, None, 0.0


#: Transport calls that only READ the shared store. Everything absent from
#: this set -- `run` above all -- keeps whatever host it was given, because a
#: read is a question about the filesystem and a launch is a decision about a
#: machine. `_resolve_host` already says why that distinction matters: "a
#: silent change of WHERE work lands would be a nasty surprise for an operator
#: who typed a host on purpose". Reads land nowhere.
READ_CALLS = frozenset((
    "list", "events", "status", "why", "verify", "probe", "read"))


class SharedReader(object):
    """A Transport for READS, which any host on the filesystem can serve.

    Wraps `make(host) -> Transport`. A read method fails over across the
    candidates and remembers whichever answered; anything not in `READ_CALLS`
    is refused rather than silently sent to an arbitrary box.

    `.host` is the host that actually served the last call -- provenance,
    which is worth reporting and never worth configuring.
    """

    def __init__(self, make, hosts=None, fs="shared", prefer=None):
        self._make, self._hosts, self._fs = make, hosts, fs
        self._prefer = prefer
        self.host = None
        self.seconds = 0.0

    def __getattr__(self, name):
        if name.startswith("_") or name not in READ_CALLS:
            raise AttributeError(
                "%r is not a shared-store read; give it a host explicitly"
                % name)

        def call(*a, **kw):
            res, host, secs = shared_read(
                lambda h: getattr(self._make(h), name)(*a, **kw),
                hosts=self._hosts, fs=self._fs, prefer=self._prefer)
            self.host, self.seconds = host, secs
            if res is None:
                tried = list(self._hosts or candidates())
                return remote.Result(
                    remote.UNKNOWN, self._fs,
                    reason="no host on %s answered (tried %s)"
                           % (self._fs, ", ".join(tried[:6])))
            return res
        return call


def _cpu_short(cpu):
    """'AMD Ryzen 9 9950X3D 16-Core Processor' -> '9950X3D';
    'Intel(R) Xeon(R) W-2155 CPU @ 3.30GHz' -> 'W-2155'."""
    if not cpu:
        return "-"
    import re
    words = [w for w in re.split(r"\s+", cpu.replace("(R)", "").replace("(TM)", ""))
             if w and w not in ("AMD", "Intel", "CPU", "Processor", "Ryzen",
                                "Xeon", "Core", "EPYC", "Gold", "Silver")
             and not w.startswith("@") and not w.endswith("GHz")
             and not re.match(r"^\d+-Core$", w)]
    best = [w for w in words if re.search(r"\d", w) and len(w) >= 4]
    return (best or words or ["?"])[-1][:12]


def format_table(states, threads=None):
    w = max([len(s.host) for s in states] + [4])
    head = "%-*s %-9s %7s %6s %6s %7s %-5s" % (w, "HOST", "CPU", "THREADS",
                                               "LOAD1", "FREE", "SPEED", "FROM")
    if threads:
        head += " %7s" % "WALLx"
    lines = [head + "  NOTE"]
    best = max([s.score for s in states if s.score] or [0])
    for s in states:
        if s.free is None:
            lines.append("%-*s %-9s %7s %6s %6s %7s %-5s%s  %s" % (
                w, s.host, _cpu_short(s.cpu), "-", "-", "-", "-", "-",
                (" %7s" % "-") if threads else "", s.why))
            continue
        sp = "-" if s.speed is None else "%.2fx" % s.speed
        note = "" if s.speed is None else "(n=%d)" % s.nsamp
        if s.mode:
            note += (" " if note else "") + "via %s" % s.mode
        row = "%-*s %-9s %7.0f %6.2f %6.1f %7s %-5s" % (
            w, s.host, _cpu_short(s.cpu), s.ncpu, s.load1, s.free, sp,
            s.speed_src or "-")
        if threads:
            # expected wall time relative to the best-scoring host
            row += " %7s" % (("%.2f" % (best / s.score)) if s.score else "inf")
        lines.append(row + "  " + note)
    return lines


if __name__ == "__main__":
    host, sts = pick()
    for ln in format_table(sts):
        print(ln)
    print("\npick: %s" % (host or "NONE free enough"))
