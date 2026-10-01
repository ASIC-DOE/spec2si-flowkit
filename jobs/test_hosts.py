#!/usr/bin/env python3
"""Tests for hosts.py host selection (no cluster)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hosts  # noqa: E402

_PASS = 0
_FAIL = 0


def check(cond, msg):
    """Assert, loudly, in BOTH runners.

    This used to count a failure and print it, then RETURN. Under
    `python3 <file>.py` that produced an honest tally -- but the project runs
    pytest, where a test function returning None has PASSED. Every check in
    this file was therefore green by construction, and a deliberately reverted
    bug in cli.py was caught by neither the suite nor its own negative control
    (2026-08-01). Raising costs the script runner nothing: `main` below now
    catches per test and still prints the tally.
    """
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        return
    _FAIL += 1
    raise AssertionError(msg)


class _R:
    def __init__(self, ok, data, status="KNOWN", reason=None):
        self.ok, self.data, self.status, self.reason = ok, data, status, reason


def fake(table):
    """table: host -> dict(ncpu,load1[,jobs_dir_ok]) | None (unreachable)."""
    def probe_one(host, timeout, mode):
        d = table.get(host)
        if d is None:
            return hosts.HostState(host, why="UNKNOWN: unreachable")
        d = dict(d)
        d.setdefault("jobs_dir_ok", True)
        r = _R(True, d)
        if not r.data.get("jobs_dir_ok"):
            return hosts.HostState(host, why="jobs dir not visible")
        ncpu, load = float(d["ncpu"]), float(d["load1"])
        if ncpu <= 0:
            return hosts.HostState(host, why="no ncpu (stale reader?)",
                                   load1=load, cpu=d.get("cpu"))
        return hosts.HostState(host, free=ncpu - load, ncpu=ncpu, load1=load,
                               cpu=d.get("cpu"))
    return probe_one


def with_fake(table, fn):
    orig = hosts._probe_one
    hosts._probe_one = fake(table)
    try:
        return fn()
    finally:
        hosts._probe_one = orig


def test_pick_most_free():
    """The real 2026-07-21 situation: asic6 oversubscribed, others idle."""
    table = {"asic6": {"ncpu": 32, "load1": 33.7},
             "asic10": {"ncpu": 24, "load1": 0.0},
             "asic9": {"ncpu": 20, "load1": 0.0},
             "pmos": {"ncpu": 32, "load1": 0.0}}
    host, states = with_fake(table, lambda: hosts.pick(list(table)))
    check(host == "pmos", "most free threads wins (got %r)" % host)
    check(states[0].host == "pmos" and states[-1].host == "asic6",
          "ranked free-first, saturated last (%r)" % [s.host for s in states])
    # load alone would have tied asic10/asic9/pmos at 0.0; capacity breaks it
    check(states[1].host == "asic10", "24 threads beat 20 at equal load")


def test_oversubscribed_is_not_picked_and_negative_free():
    table = {"asic6": {"ncpu": 16, "load1": 33.7}}
    host, states = with_fake(table, lambda: hosts.pick(list(table)))
    check(host is None, "no host clears min_free -> None, not a bad pick")
    check(states[0].free < 0, "free may be negative (%r)" % states[0].free)


def test_unreachable_never_picked():
    """An unreachable host is UNKNOWN, not empty -- invariant 5/7."""
    table = {"asic1": None, "asic9": {"ncpu": 20, "load1": 1.0}}
    host, states = with_fake(table, lambda: hosts.pick(["asic1", "asic9"]))
    check(host == "asic9", "unreachable skipped (got %r)" % host)
    dead = [s for s in states if s.host == "asic1"][0]
    check(dead.free is None and "UNKNOWN" in dead.why,
          "unreachable carries free=None + reason (%r)" % dead)
    check(states[-1].host == "asic1", "unmeasurable sorts last")


def test_stale_reader_without_ncpu_is_not_guessed():
    table = {"asic7": {"ncpu": 0, "load1": 0.1},
             "asic8": {"ncpu": 20, "load1": 5.0}}
    host, states = with_fake(table, lambda: hosts.pick(["asic7", "asic8"]))
    check(host == "asic8", "host with no ncpu is not guessed at (%r)" % host)
    check([s for s in states if s.host == "asic7"][0].free is None,
          "missing ncpu -> unmeasurable, not free=0")


def test_min_free_threshold():
    table = {"asic3": {"ncpu": 24, "load1": 20.0}}     # 4 free
    host, _ = with_fake(table, lambda: hosts.pick(["asic3"], min_free=6.0))
    check(host is None, "4 free < min_free 6 -> None")
    host, _ = with_fake(table, lambda: hosts.pick(["asic3"], min_free=2.0))
    check(host == "asic3", "4 free >= min_free 2 -> picked")


def test_candidates_env_override_and_gpu_exclusion():
    check("exxact" not in hosts.CANDIDATES and
          "dgx-spark" not in hosts.CANDIDATES,
          "GPU boxes excluded: idle there is a trap, no ASIC tools")
    check("asic5" not in hosts.CANDIDATES, "known-down host excluded")
    os.environ["ASIC_HOSTS"] = "asic7 asic8"
    try:
        check(hosts.candidates() == ("asic7", "asic8"), "ASIC_HOSTS override")
    finally:
        del os.environ["ASIC_HOSTS"]
    check(hosts.candidates() == hosts.CANDIDATES, "default restored")


def _job(host, flow, rate, state="done"):
    return {"host": host, "flow": flow, "state": state, "rate_per_s": rate}


def test_speeds_from_measured_rate():
    jobs = [_job("asic6", "enob", 0.289), _job("asic6", "enob", 0.143),
            _job("asic10", "enob", 0.060),
            _job("asic9", "calibre", 9.9)]        # other flow: ignored
    sp = hosts.speeds(jobs, flow="enob")
    check(set(sp) == {"asic6", "asic10"}, "only the flow's hosts (%r)" % sp)
    check(sp["asic6"][1] == 2, "sample count kept")
    check(sp["asic6"][0] > sp["asic10"][0], "faster host scores higher")
    # normalized to the MEDIAN host, so 'no history' can sit at a neutral 1.0
    check(abs(_med([v[0] for v in sp.values()]) - 1.0) < 0.51,
          "normalized around 1.0 (%r)" % sp)


def _med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if len(xs) % 2 else \
        (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2.0


def test_speeds_ignores_unfinished_and_bad_rates():
    jobs = [_job("asic6", "enob", 0.2), _job("asic6", "enob", 99.0, "killed"),
            _job("asic7", "enob", 0), _job("asic8", "enob", None)]
    sp = hosts.speeds(jobs, flow="enob")
    check(set(sp) == {"asic6"},
          "killed/zero/None rates excluded (%r)" % sp)


def test_speeds_excludes_phase_records():
    """A cal-phase rate is in cal sub-conversions/s, NOT conversions/s --
    mixing the two units would silently corrupt the speed factor."""
    cal = _job("asic9", "enob", 50.0)
    cal["phase"] = "cal"
    jobs = [_job("asic6", "enob", 0.2), cal]
    sp = hosts.speeds(jobs, flow="enob")
    check(set(sp) == {"asic6"}, "phase record excluded (%r)" % sp)


def test_pick_prefers_measured_speed_over_free_capacity():
    """The real 2026-07-21 mistake: an idle SLOW box beat a busy FAST one."""
    table = {"asic6": {"ncpu": 32, "load1": 18.0},    # 14 free, fast
             "asic10": {"ncpu": 24, "load1": 0.0}}    # 24 free, slow
    jobs = [_job("asic6", "enob", 0.289), _job("asic10", "enob", 0.060)]
    host, states = with_fake(table, lambda: hosts.pick(
        list(table), jobs=jobs, flow="enob"))
    check(host == "asic6",
          "measured-fast beats idle-slow (got %r)" % host)
    # without history the capacity ranking stands
    host2, _ = with_fake(table, lambda: hosts.pick(list(table)))
    check(host2 == "asic10", "no history -> capacity ranking (%r)" % host2)


def test_speed_never_overrides_the_capacity_gate():
    table = {"asic6": {"ncpu": 16, "load1": 15.0},    # 1 free -- too full
             "asic9": {"ncpu": 20, "load1": 0.0}}
    jobs = [_job("asic6", "enob", 9.9), _job("asic9", "enob", 0.1)]
    host, _ = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs,
                                                  flow="enob"))
    check(host == "asic9",
          "a full host is not picked however fast it is (%r)" % host)


def test_unmeasured_host_is_neutral_not_slow():
    """1.0 means 'typical', so an unknown host beats a measurably SLOW one
    and loses to a measurably FAST one -- it is never assumed bad."""
    table = {"asic10": {"ncpu": 24, "load1": 0.0},
             "asic9": {"ncpu": 20, "load1": 0.0}}
    slow = [_job("asic10", "enob", 0.05), _job("asic2", "enob", 0.5)]
    host, _ = with_fake(table, lambda: hosts.pick(["asic10", "asic9"],
                                                  jobs=slow, flow="enob"))
    check(host == "asic9", "unmeasured beats measurably-slow (%r)" % host)
    fast = [_job("asic10", "enob", 5.0), _job("asic2", "enob", 0.5)]
    host2, _ = with_fake(table, lambda: hosts.pick(["asic10", "asic9"],
                                                   jobs=fast, flow="enob"))
    check(host2 == "asic10", "measurably-fast beats unmeasured (%r)" % host2)


def test_speed_host_match_is_case_insensitive():
    """The ssh alias is `pmos`; that box's own `hostname -s` answers PMOS,
    so meta.json records PMOS. An exact match dropped every sample it ever
    produced -- silently, as a missing SPEED rather than an error."""
    table = {"pmos": {"ncpu": 32, "load1": 0.0},
             "asic9": {"ncpu": 20, "load1": 0.0}}
    jobs = [_job("PMOS", "enob", 5.0), _job("asic9", "enob", 0.5)]
    host, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs,
                                                       flow="enob"))
    check(host == "pmos", "PMOS sample matched to pmos (got %r)" % host)
    st = [s for s in states if s.host == "pmos"][0]
    check(st.speed is not None and st.nsamp == 1,
          "speed attached despite case mismatch (%r)" % st)


def test_format_table():
    table = {"asic9": {"ncpu": 20, "load1": 0.5}, "asic1": None}
    _, states = with_fake(table, lambda: hosts.pick(["asic9", "asic1"]))
    lines = hosts.format_table(states)
    check("HOST" in lines[0] and "FREE" in lines[0], "table header")
    check(any("19.5" in ln for ln in lines), "free rendered (%r)" % lines)
    check(any("UNKNOWN" in ln for ln in lines), "reason rendered for dead host")


RYZEN = "AMD Ryzen 9 9950X3D 16-Core Processor"
XEON = "Intel(R) Xeon(R) W-2155 CPU @ 3.30GHz"


def _jobs(spec, flow="enob"):
    """spec: host -> list of rates"""
    return [_job(h, flow, r) for h, rs in spec.items() for r in rs]


def test_pool_by_cpu_the_2026_10_01_case():
    """asic8 idle with ONE job at a neutral 1.00x, asic7 -- the same Xeon --
    with five at 0.29x, asic6 (9950X3D) partly loaded and measurably fast.
    Per-host speed sent the 16-thread run to asic8; pooled by CPU model the
    Xeon reads ~0.36x and the Ryzen wins."""
    table = {"asic6": {"ncpu": 32, "load1": 16.5, "cpu": RYZEN},
             "asic7": {"ncpu": 20, "load1": 3.1, "cpu": XEON},
             "asic8": {"ncpu": 20, "load1": 0.1, "cpu": XEON}}
    # rates chosen so the per-host medians normalize to ~2.4 / 0.29 / 1.0
    jobs = _jobs({"asic6": [2.4] * 18, "asic7": [0.29] * 5, "asic8": [1.0]})
    host, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs,
                                                       flow="enob", threads=16))
    st = dict((s.host, s) for s in states)
    check(st["asic8"].speed_src == "cpu" and st["asic8"].nsamp == 6,
          "thin asic8 pooled with asic7 (%r)" % st["asic8"])
    check(st["asic8"].speed < 0.5, "pooled Xeon speed is slow (%r)" % st["asic8"].speed)
    check(st["asic6"].speed_src == "host", "asic6 keeps its own 18 samples")
    check(host == "asic6", "the fast box wins (got %r)" % host)
    # NEGATIVE CONTROL: without CPU models nothing pools -> asic8 at 1.0
    # still loses to asic6 on speed... so make asic6 busier, where the
    # pooled answer and the per-host answer must DIFFER.
    table2 = dict(table)
    table2["asic6"] = {"ncpu": 32, "load1": 26.0, "cpu": RYZEN}      # 6 free
    host2, _ = with_fake(table2, lambda: hosts.pick(list(table2), jobs=jobs,
                                                    flow="enob", threads=16))
    blind = dict((h, dict(d, cpu=None)) for h, d in table2.items())
    host3, st3 = with_fake(blind, lambda: hosts.pick(list(blind), jobs=jobs,
                                                     flow="enob", threads=16))
    st3 = dict((s.host, s) for s in st3)
    check(st3["asic8"].speed_src == "host" and st3["asic8"].nsamp == 1,
          "no CPU model -> no pooling (%r)" % st3["asic8"])
    check(host3 == "asic8" and host2 == "asic6",
          "pooling changes the answer: blind %r, pooled %r" % (host3, host2))


def test_pool_never_crosses_cpu_models():
    table = {"asic9": {"ncpu": 20, "load1": 0.0, "cpu": XEON},
             "asic6": {"ncpu": 32, "load1": 0.0, "cpu": RYZEN}}
    jobs = _jobs({"asic9": [1.0], "asic6": [2.0] * 10, "asic2": [1.0]})
    _, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs,
                                                    flow="enob"))
    st = dict((s.host, s) for s in states)
    check(st["asic9"].speed_src == "host" and st["asic9"].nsamp == 1,
          "a thin Xeon does not borrow the Ryzen's speed (%r)" % st["asic9"])


def test_threads_weigh_room_against_speed():
    """A fast host with too few free threads for the job is not a free pass."""
    table = {"asic6": {"ncpu": 32, "load1": 25.0, "cpu": RYZEN},     # 7 free
             "asic10": {"ncpu": 24, "load1": 0.0, "cpu": "X"}}        # 24 free
    jobs = _jobs({"asic6": [1.1] * 5, "asic10": [1.0] * 5, "asic2": [1.0]})
    h_speed, _ = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs,
                                                     flow="enob"))
    h_thr, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs,
                                                        flow="enob", threads=16))
    check(h_speed == "asic6", "speed-only ranking takes the faster box")
    check(h_thr == "asic10", "16 threads into 7 free loses to an idle peer (%r)" % h_thr)
    st = dict((s.host, s) for s in states)
    check(abs(st["asic6"].score - 1.1 * 7 / 16.0) < 0.02, "score = speed x free/threads")
    lines = hosts.format_table(states, threads=16)
    check("WALLx" in lines[0], "wall-time ratio column with --threads")
    check(any(ln.startswith("asic10") and " 1.00 " in ln for ln in lines),
          "the best host reads 1.00 (%r)" % lines)


def test_flow_none_normalizes_each_flow_first():
    """Raw rates of different flows are different units: asic9 ran calibre
    (rules/s, large numbers) as well as enob, asic6 only enob. Pooling raw
    rates called asic9 the fast one; per-flow it is the slow one."""
    jobs = (_jobs({"asic6": [0.3, 0.3], "asic9": [0.1, 0.1]}, "enob")
            + _jobs({"asic9": [10.0, 12.0]}, "calibre"))
    sp = hosts.speeds(jobs)                      # flow=None
    check(sp["asic6"][0] > sp["asic9"][0],
          "per-flow combination: asic6 faster (%r)" % sp)
    check("asic9" in sp and sp["asic9"][1] == 2,
          "a flow seen on one host only adds no samples (%r)" % sp)
    # the per-flow path is unchanged
    check(hosts.speeds(jobs, flow="calibre") == {"asic9": (1.0, 2)},
          "single-flow speeds unchanged")


def test_cpu_short_names():
    check(hosts._cpu_short(RYZEN) == "9950X3D", hosts._cpu_short(RYZEN))
    check(hosts._cpu_short(XEON) == "W-2155", hosts._cpu_short(XEON))
    check(hosts._cpu_short(None) == "-" and hosts._cpu_short("") == "-", "blank")


def test_windows_probe_falls_back_to_winssh():
    """2026-10-01: from Windows every WSL-ssh probe failed, the Windows
    OpenSSH client reached all of them. A failed default-mode probe is
    retried over winssh; an explicit mode or ASICJOBS_RSH is never
    second-guessed."""
    calls = []

    def raw(host, timeout, mode):
        calls.append((mode, timeout))
        if mode == hosts.FALLBACK_MODE:
            return _R(True, {"ncpu": 20, "load1": 1.0, "jobs_dir_ok": True,
                             "cpu": XEON}), None
        return _R(False, None, status="UNKNOWN", reason="timeout"), None
    orig_raw, orig_win = hosts._probe_raw, hosts._windows
    hosts._probe_raw, hosts._windows = raw, (lambda: True)
    try:
        st = hosts._probe_one("asic9", 12.0, None)
        check(st.free == 19.0 and st.mode == hosts.FALLBACK_MODE and st.cpu == XEON,
              "fallback answered (%r)" % st)
        check(calls[1][1] >= hosts.FALLBACK_TIMEOUT, "with the cold-connect allowance")
        del calls[:]
        st = hosts._probe_one("asic9", 12.0, "wsl")
        check(st.free is None and len(calls) == 1, "explicit mode: no fallback")
        hosts._windows = lambda: False
        del calls[:]
        st = hosts._probe_one("asic9", 12.0, None)
        check(st.free is None and len(calls) == 1, "not Windows: no fallback")
    finally:
        hosts._probe_raw, hosts._windows = orig_raw, orig_win


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in tests:
        print("*", fn.__name__)
        try:
            fn()
        except AssertionError as exc:      # keep going: a tally is the point
            bad += 1
            print("  FAIL:", exc)
        except Exception as exc:           # noqa: BLE001
            bad += 1
            print("  ERROR: %s: %s" % (type(exc).__name__, exc))
    print("\n%d check(s) passed, %d test(s) failed" % (_PASS, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
