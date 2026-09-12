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

Speed per core is deliberately NOT modelled. It would need a benchmark to
stay honest, and a hardcoded speed table is exactly the kind of constant
that rots silently; CANDIDATES order is used only to break ties.
"""
import os

try:
    from . import remote                     # package import
except ImportError:                          # direct-script fallback
    import remote

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
                 speed=None, nsamp=0):
        self.host, self.free, self.ncpu = host, free, ncpu
        self.load1, self.why = load1, why
        self.speed, self.nsamp = speed, nsamp

    def __repr__(self):
        return "HostState(%s, free=%s, speed=%s, why=%r)" % (
            self.host, self.free, self.speed, self.why)


def _probe_one(host, timeout, mode):
    try:
        t = remote.Transport(host=host, mode=mode, timeout=timeout)
        r = t.probe()
    except Exception as e:                    # transport construction/IO
        return HostState(host, why="probe error: %s" % e)
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
    if ncpu <= 0:
        # an older bundle predates the ncpu field; rank it last rather than
        # guessing a core count.
        return HostState(host, why="no ncpu (stale reader?)", load1=load)
    return HostState(host, free=ncpu - load, ncpu=ncpu, load1=load)


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


def speeds(jobs, flow=None):
    """host -> (relative_speed, n_samples), measured from finished jobs.

    `rate_per_s` is the job's own MEASURED throughput (done/elapsed in the
    tool's own units), so for a given flow it compares hosts directly on
    real work -- no synthetic benchmark, no hardcoded speed table to rot.
    Normalized to the MEDIAN host, so >1 is faster than typical and <1
    slower, and a host with no history can sit at a neutral 1.0 rather than
    being scored as if it were slow.

    $JOBS is shared NFS, so one `list` from any host carries every host's
    history -- this costs no extra round trips.

    Restricted to one flow because rate is in tool units: an enob
    conversions/s means nothing next to a calibre rules/s.
    """
    by_host = {}
    for j in jobs:
        if flow and j.get("flow") != flow:
            continue
        if j.get("state") != "done":
            continue                     # killed/failed rates are truncated
        if j.get("phase"):
            # a phase record counts a DIFFERENT unit (cal sub-conversions,
            # not conversions), so its rate is not comparable with the rest
            # -- averaging the two would silently corrupt the factor.
            continue
        r, h = j.get("rate_per_s"), j.get("host")
        if not h or not isinstance(r, (int, float)) or r <= 0:
            continue
        by_host.setdefault(h, []).append(float(r))
    if not by_host:
        return {}
    meds = {h: _median(v) for h, v in by_host.items()}
    ref = _median(list(meds.values()))
    if ref <= 0:
        return {}
    return {h: (m / ref, len(by_host[h])) for h, m in meds.items()}


def pick(hosts=None, min_free=MIN_FREE, timeout=12.0, mode=None,
         jobs=None, flow=None):
    """(host, states) -- the best host with at least `min_free` threads, or
    (None, states) when none qualifies. Never silently falls back to a busy
    host.

    With `jobs` (a job list) the ranking is by MEASURED relative speed, not
    free capacity: on 2026-07-21 capacity-only ranking sent a run to an idle
    Xeon W-2265 over a partly-loaded Ryzen 9950X3D and it ran 3-5x slower --
    free threads say how much room there is, not how fast the work will go.
    Capacity remains a hard gate and the tie-break; hosts with no history
    score a neutral 1.0, so an unmeasured host still beats a measurably slow
    one and loses to a measurably fast one.
    """
    states = survey(hosts, timeout=timeout, mode=mode)
    if jobs:
        # case-insensitive: the ssh alias is `pmos` but that box's own
        # `hostname -s` answers PMOS, so meta.json records PMOS and an
        # exact match silently dropped every sample it ever produced.
        sp = {k.lower(): v for k, v in speeds(jobs, flow=flow).items()}
        for s in states:
            if s.host.lower() in sp:
                s.speed, s.nsamp = sp[s.host.lower()]
    ok = [s for s in states if s.free is not None and s.free >= min_free]
    if not ok:
        return None, states
    order = {s.host: i for i, s in enumerate(states)}
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


def format_table(states):
    w = max([len(s.host) for s in states] + [4])
    lines = ["%-*s %7s %6s %6s %7s  %s" % (w, "HOST", "THREADS", "LOAD1",
                                           "FREE", "SPEED", "NOTE")]
    for s in states:
        if s.free is None:
            lines.append("%-*s %7s %6s %6s %7s  %s" % (
                w, s.host, "-", "-", "-", "-", s.why))
            continue
        sp = "-" if s.speed is None else "%.2fx" % s.speed
        note = "" if s.speed is None else "(n=%d)" % s.nsamp
        lines.append("%-*s %7.0f %6.2f %6.1f %7s  %s" % (
            w, s.host, s.ncpu, s.load1, s.free, sp, note))
    return lines


if __name__ == "__main__":
    host, sts = pick()
    for ln in format_table(sts):
        print(ln)
    print("\npick: %s" % (host or "NONE free enough"))
