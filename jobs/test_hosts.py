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
                                   load1=load)
        return hosts.HostState(host, free=ncpu - load, ncpu=ncpu, load1=load)
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
