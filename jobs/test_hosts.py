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
    """Fake probes, and NO committed ledger: a test's speeds come only from
    the calibration jobs it passes (the committed data has its own test)."""
    orig, orig_led = hosts._probe_one, hosts.calib.LEDGER
    hosts._probe_one = fake(table)
    hosts.calib.LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "no-such-ledger.json")
    try:
        return fn()
    finally:
        hosts._probe_one, hosts.calib.LEDGER = orig, orig_led


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
    """a PRODUCTION job: flow + its own-units rate (never a speed source)"""
    return {"host": host, "flow": flow, "state": state, "rate_per_s": rate}


def _cal(host, suite, elapsed, state="done"):
    """a tracked CALIBRATION job: the suite timed on one host"""
    return {"host": host, "flow": hosts.calib.CAL_FLOW, "target": suite,
            "state": state, "elapsed_s": elapsed}


def _cals(spec, suite="s1"):
    """spec: host -> elapsed seconds (or a list of runs)"""
    out = []
    for h, e in spec.items():
        for x in (e if isinstance(e, list) else [e]):
            out.append(_cal(h, suite, x))
    return out


def test_speeds_from_calibration():
    jobs = _cals({"asic6": 60, "asic8": 180, "asic9": 180})
    sp = hosts.speeds(jobs, ledger=[])
    check(set(sp) == {"asic6", "asic8", "asic9"}, "calibrated hosts (%r)" % sp)
    check(abs(sp["asic6"][0] / sp["asic8"][0] - 3.0) < 1e-6, "speed = time ratio (%r)" % sp)
    check(abs(sp["asic8"][0] - 1.0) < 1e-9, "the median host reads 1.0 (%r)" % sp)
    check(sp["asic6"][1] == 1, "n counts suites")


def test_calibration_ignores_unfinished_bad_and_production_jobs():
    jobs = (_cals({"asic6": 60, "asic8": 180})
            + [_cal("asic9", "s1", 5, "failed"), _cal("asic7", "s1", 0),
               _job("asic9", "enob", 99.0)])
    sp = hosts.speeds(jobs, ledger=[])
    check(set(sp) == {"asic6", "asic8"},
          "failed / zero-time / production jobs are not calibration (%r)" % sp)


def test_production_rates_never_set_speed_the_2026_11_03_case():
    """Production rates count each job's own units: on spectre-campaign asic8
    (short campaigns) read 24x and asic9 (long ones) 0.08x -- two identical
    Xeons -- and asic8 at 11.9x was picked over the 9950X3D for chip P&R.
    Production jobs alone now give NO speed; calibration reads the Xeons alike."""
    camp = ([_job("asic8", "spectre-campaign", r) for r in (2e-3, 0.15, 0.2, 0.3, 0.9)]
            + [_job("asic9", "spectre-campaign", r) for r in (1.4e-4, 2e-4, 6e-4, 0.02, 0.04)]
            + [_job("asic6", "spectre-campaign", r) for r in (3e-5, 1e-3, 3e-2, 0.3, 1.6)])
    check(hosts.speeds(camp, ledger=[]) == {}, "production jobs alone: no speed")
    # the evidence the old estimator used: per-host median rates differ ~100x
    med = dict((h, sorted(j["rate_per_s"] for j in camp if j["host"] == h)[2])
               for h in ("asic8", "asic9"))
    check(med["asic8"] / med["asic9"] > 100, "the trap is real in this data (%r)" % med)
    cal = _cals({"asic6": 55.7, "asic8": 187, "asic9": 181})
    sp = hosts.speeds(camp + cal, ledger=[])
    check(abs(sp["asic8"][0] / sp["asic9"][0] - 1.0) < 0.05,
          "identical Xeons read alike (%r)" % sp)
    table = {"asic6": {"ncpu": 32, "load1": 1.6, "cpu": RYZEN},
             "asic8": {"ncpu": 20, "load1": 0.0, "cpu": XEON},
             "asic9": {"ncpu": 20, "load1": 0.0, "cpu": XEON}}
    host, _ = with_fake(table, lambda: hosts.pick(list(table), jobs=camp + cal,
                                                  flow="spectre-campaign", threads=16))
    check(host == "asic6", "the fast box wins (got %r)" % host)
    # NEGATIVE CONTROL: without calibration the pick falls to capacity (asic6
    # still has the most free threads here), and a calibration that says asic8
    # is fastest makes asic8 win -- the pick follows calibration, nothing else
    rev = _cals({"asic6": 300, "asic8": 50, "asic9": 181})
    host2, _ = with_fake(table, lambda: hosts.pick(list(table), jobs=camp + rev, threads=16))
    check(host2 == "asic8", "the pick follows the calibration (%r)" % host2)


def test_fastest_run_per_host_and_suite():
    """Contention only slows a run (asic6: 82.5 s loaded, 55.7 s clean)."""
    jobs = _cals({"asic6": [82.5, 55.7], "asic8": 187, "asic9": 181})
    sp = hosts.speeds(jobs, ledger=[])
    check(abs(sp["asic6"][0] / sp["asic8"][0] - 187 / 55.7) < 1e-6,
          "the fastest run is the host's (%r)" % sp)


def test_two_way_fit_bridges_suites():
    """Suites on different host sets share one scale through common hosts:
    A times a, b; B times b, c -> c against a through b, exactly."""
    jobs = _cals({"a": 100, "b": 200}, "A") + _cals({"b": 30, "c": 10}, "B")
    sp = hosts.speeds(jobs, ledger=[])
    check(abs(sp["a"][0] / sp["c"][0] - (200 / 100.0) / (30 / 10.0)) < 1e-9,
          "a / c = (b/a in A) / (b/c in B) (%r)" % sp)
    check(sp["b"][1] == 2, "b is in both suites")


def test_a_suite_on_disjoint_hosts_is_not_on_the_scale():
    jobs = _cals({"a": 100, "b": 200, "c": 150}, "A") + _cals({"x": 10, "y": 20}, "B")
    sp = hosts.speeds(jobs, ledger=[])
    check(set(sp) == {"a", "b", "c"}, "x / y share no host: unmeasured, not guessed (%r)" % sp)


def test_single_host_suite_compares_nothing():
    check(hosts.speeds(_cals({"a": 100}), ledger=[]) == {}, "one host: no comparison")


def test_ledger_reads_runs_and_honours_excluded():
    import json
    import tempfile
    doc = {"suites": [{"name": "L", "runs": [
        {"host": "asic6", "elapsed_s": 20}, {"host": "asic2", "elapsed_s": 60},
        {"host": "exxact", "elapsed_s": 5, "excluded": "under load"}]}]}
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh)
    try:
        recs = hosts.calib.load_ledger(p)
    finally:
        os.remove(p)
    check(sorted(recs) == [("L", "asic2", 60.0), ("L", "asic6", 20.0)],
          "excluded run left out (%r)" % recs)


def test_committed_ledger():
    """The committed measurements, held: identical W-2155s read alike, the
    9950X3D far ahead, the E5-2450 (pmos) behind -- from Spectre AND JoSIM."""
    recs = hosts.calib.load_ledger()
    check({"spectre-ams_va_drift-aps-mt14", "josim-r10f1n-n-2a34306",
           "josim-r10f1n-n-486bb3b"} <= set(s for s, _h, _e in recs), "the three suites")
    check(not any(h == "exxact" for _s, h, _e in recs), "loaded exxact runs excluded")
    sp = hosts.calib.fit(recs)
    xeon = [sp[h][0] for h in ("asic1", "asic2", "asic8", "asic9")]
    check(max(xeon) / min(xeon) < 1.15, "the W-2155s within 15 %% (%r)" % xeon)
    check(sp["asic6"][0] > 2.5 * sp["asic8"][0], "asic6 > 2.5x asic8 (%r)" % sp)
    check(sp["pmos"][0] < 0.75 * sp["asic8"][0], "pmos well behind (%r)" % sp)
    for tool in ("spectre", "josim"):
        sub = hosts.calib.fit([r for r in recs if r[0].startswith(tool)])
        check(sub["asic6"][0] / sub["asic2"][0] > 2.5, "%s alone: asic6 > 2.5x asic2" % tool)


def _med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if len(xs) % 2 else \
        (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2.0


def test_pick_prefers_measured_speed_over_free_capacity():
    """The real 2026-07-21 mistake: an idle SLOW box beat a busy FAST one."""
    table = {"asic6": {"ncpu": 32, "load1": 18.0},    # 14 free, fast
             "asic10": {"ncpu": 24, "load1": 0.0}}    # 24 free, slow
    jobs = _cals({"asic6": 60, "asic10": 150})
    host, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs))
    check(host == "asic6", "calibrated-fast beats idle-slow (got %r)" % host)
    # without calibration the capacity ranking stands
    host2, _ = with_fake(table, lambda: hosts.pick(list(table)))
    check(host2 == "asic10", "no calibration -> capacity ranking (%r)" % host2)


def test_speed_never_overrides_the_capacity_gate():
    table = {"asic6": {"ncpu": 16, "load1": 15.0},    # 1 free -- too full
             "asic9": {"ncpu": 20, "load1": 0.0}}
    jobs = _cals({"asic6": 10, "asic9": 200})
    host, _ = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs))
    check(host == "asic9",
          "a full host is not picked however fast it is (%r)" % host)


def test_unmeasured_host_is_neutral_not_slow():
    """1.0 means 'typical', so an unknown host beats a measurably SLOW one
    and loses to a measurably FAST one -- it is never assumed bad."""
    table = {"asic10": {"ncpu": 24, "load1": 0.0},
             "asic9": {"ncpu": 20, "load1": 0.0}}
    slow = _cals({"asic10": 400, "asic2": 100, "asic3": 100})
    host, _ = with_fake(table, lambda: hosts.pick(["asic10", "asic9"], jobs=slow))
    check(host == "asic9", "unmeasured beats measurably-slow (%r)" % host)
    fast = _cals({"asic10": 25, "asic2": 100, "asic3": 100})
    host2, _ = with_fake(table, lambda: hosts.pick(["asic10", "asic9"], jobs=fast))
    check(host2 == "asic10", "measurably-fast beats unmeasured (%r)" % host2)


def test_speed_host_match_is_case_insensitive():
    """The ssh alias is `pmos`; that box's own `hostname -s` answers PMOS,
    so meta.json records PMOS. An exact match dropped every sample it ever
    produced -- silently, as a missing SPEED rather than an error."""
    table = {"pmos": {"ncpu": 32, "load1": 0.0},
             "asic9": {"ncpu": 20, "load1": 0.0}}
    jobs = _cals({"PMOS": 20, "asic9": 200})
    host, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs))
    check(host == "pmos", "PMOS calibration matched to pmos (got %r)" % host)
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


def test_uncalibrated_host_borrows_its_cpu_model():
    """asic8 has no calibration; asic7 -- the same Xeon -- has one: asic8
    takes the Xeon's speed rather than a neutral 1.0, and the 9950X3D wins."""
    table = {"asic6": {"ncpu": 32, "load1": 16.5, "cpu": RYZEN},
             "asic7": {"ncpu": 20, "load1": 3.1, "cpu": XEON},
             "asic8": {"ncpu": 20, "load1": 0.1, "cpu": XEON}}
    jobs = _cals({"asic6": 60, "asic7": 200, "asic2": 190})
    host, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs, threads=16))
    st = dict((s.host, s) for s in states)
    check(st["asic8"].speed_src == "cpu" and st["asic8"].speed < 1.0,
          "uncalibrated asic8 borrows the Xeon's speed (%r)" % st["asic8"])
    check(st["asic7"].speed_src == "cal", "asic7 keeps its own calibration")
    check(host == "asic6", "the fast box wins (got %r)" % host)
    # NEGATIVE CONTROL: without CPU models nothing pools -> asic8 unmeasured
    blind = dict((h, dict(d, cpu=None)) for h, d in table.items())
    _, st3 = with_fake(blind, lambda: hosts.pick(list(blind), jobs=jobs, threads=16))
    st3 = dict((s.host, s) for s in st3)
    check(st3["asic8"].speed is None, "no CPU model -> no pooling (%r)" % st3["asic8"])


def test_pool_never_crosses_cpu_models():
    table = {"asic9": {"ncpu": 20, "load1": 0.0, "cpu": XEON},
             "asic6": {"ncpu": 32, "load1": 0.0, "cpu": RYZEN}}
    jobs = _cals({"asic6": 60, "asic2": 180})
    _, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs))
    st = dict((s.host, s) for s in states)
    check(st["asic9"].speed is None,
          "an uncalibrated Xeon does not borrow the Ryzen's speed (%r)" % st["asic9"])


def test_threads_weigh_room_against_speed():
    """A fast host with too few free threads for the job is not a free pass."""
    table = {"asic6": {"ncpu": 32, "load1": 25.0, "cpu": RYZEN},     # 7 free
             "asic10": {"ncpu": 24, "load1": 0.0, "cpu": "X"}}        # 24 free
    jobs = _cals({"asic6": 100, "asic10": 110, "asic2": 110})
    h_speed, _ = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs))
    h_thr, states = with_fake(table, lambda: hosts.pick(list(table), jobs=jobs, threads=16))
    check(h_speed == "asic6", "speed-only ranking takes the faster box")
    check(h_thr == "asic10", "16 threads into 7 free loses to an idle peer (%r)" % h_thr)
    st = dict((s.host, s) for s in states)
    check(abs(st["asic6"].score - 1.1 * 7 / 16.0) < 0.02, "score = speed x free/threads")
    lines = hosts.format_table(states, threads=16)
    check("WALLx" in lines[0], "wall-time ratio column with --threads")
    check(any(ln.startswith("asic10") and " 1.00 " in ln for ln in lines),
          "the best host reads 1.00 (%r)" % lines)


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
