#!/usr/bin/env python3
"""Tests for the shared progress extractors (progress.py).

Pure functions over log text -> no cluster, no tools. Run:
  python3 test_progress.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "bin"))
import progress  # noqa: E402

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


# --- extractors -----------------------------------------------------------

def test_spectre():
    txt = ("noise\nVA NORMAL conv=1 code_q=10 t=1e-8\n"
           ".VA NORMAL conv=2 code_q=20 t=2e-8\n"
           "VA NORMAL conv=3 code_q=30 t=3e-8\n")
    r = progress.spectre(txt)
    check(r == {"done": 3}, "spectre done = max conv (%r)" % r)
    check(progress.spectre("nothing here") is None, "spectre no signal -> None")


CAL_LOG = ("VA CALWARM code_q=96 t=1.8e-08\n"
           "VA CALWARM code_q=96 t=3.3e-08\n"
           "VA CALSUB k=8 pass=0 av=0 code_q=100 t=5.2e-08\n"
           "VA CALSUB k=8 pass=1 av=3 code_q=108 t=1.8e-07\n"
           "VA CAL err[8] q=-1 meas=-1 round=0 (D1=436 D0=432) t=1.8e-07\n"
           "VA CALSUB k=9 pass=0 av=0 code_q=188 t=2.0e-07\n")


def test_spectre_cal_phase():
    """A calibrated deck emits NO 'VA NORMAL conv=' for its first ~21%.
    Reporting None there is indistinguishable from a stalled job -- it
    misled two readers on 2026-07-21 -- so the phase is named instead."""
    r = progress.spectre(CAL_LOG)
    check(r and r.get("phase") == "cal", "cal phase detected (%r)" % r)
    check(r["done"] == 5, "counts CALSUB+CALWARM sub-conversions (%r)" % r)
    check("1 targets" in r.get("label", ""), "err[] lines = targets done")
    # NORMAL lines win as soon as they exist -- cal is only the fallback
    r2 = progress.spectre(CAL_LOG + "VA NORMAL conv=7 code_q=1 t=3e-6\n")
    check(r2 == {"done": 7}, "NORMAL supersedes cal (%r)" % r2)


def test_cal_phase_never_borrows_the_conversion_total():
    """The trap this guards: 5 cal sub-conversions against a 256-CONVERSION
    total would render as a confident, meaningless '2%'."""
    out = progress.compute("spectre", CAL_LOG, elapsed_s=60, total=256)
    check(out.get("phase") == "cal", "phase carried through compute (%r)" % out)
    check("frac" not in out and "total" not in out,
          "caller's total NOT applied to a different unit (%r)" % out)
    check(out.get("rate_per_s"), "liveness rate still reported (%r)" % out)
    # a normal record still gets its frac
    ok = progress.compute("spectre", "VA NORMAL conv=64 x\n", elapsed_s=10,
                          total=256)
    check(ok.get("frac") == 0.25 and "phase" not in ok,
          "normal record unchanged (%r)" % ok)


def test_innovus():
    txt = "=== init done\nblah\n=== floorplan done\n=== place done\n"
    r = progress.innovus(txt)
    check(r["done"] == 3 and r["total"] == 7, "innovus 3/7 (%r)" % r)
    check(r["label"] == "place", "innovus label = last step")
    check(progress.innovus("no sentinels") is None, "innovus no signal -> None")


def test_calibre():
    txt = ("RULECHECK M1.W.1 ...... TOTAL Result Count = 0\n"
           "RULECHECK M1.S.1 ...... TOTAL Result Count = 2\n")
    r = progress.calibre(txt)
    check(r == {"done": 2}, "calibre done = rules checked (%r)" % r)


def test_cocotb():
    r = progress.cocotb("TESTS=8\nPASS=5\n")
    check(r == {"done": 5, "total": 8}, "cocotb explicit counters (%r)" % r)
    r = progress.cocotb("TB: PASS\nTB: PASS\nTB: FAIL\n")
    check(r == {"done": 2, "total": 3}, "cocotb TB: lines (%r)" % r)
    check(progress.cocotb("quiet") is None, "cocotb no signal -> None")


# --- rate + ETA -----------------------------------------------------------

def test_compute_rate_eta():
    txt = "\n".join("VA NORMAL conv=%d code_q=1 t=1" % i
                    for i in range(1, 65))  # 64 conversions done
    r = progress.compute("spectre", txt, elapsed_s=32.0, total=256)
    check(r["done"] == 64 and r["total"] == 256, "compute done/total")
    check(r["frac"] == 0.25, "frac = 64/256 (%r)" % r.get("frac"))
    # rate = 64/32 = 2 conv/s; remaining 192 -> eta 96 s. MEASURED, not a
    # wall-clock guess (this is the ~15x-error fix).
    check(r["rate_per_s"] == 2.0, "rate = done/elapsed (%r)" % r.get("rate_per_s"))
    check(r["eta_s"] == 96, "eta = remaining/rate (%r)" % r.get("eta_s"))


def test_compute_no_eta_without_total():
    txt = "VA NORMAL conv=5 code_q=1 t=1"
    r = progress.compute("spectre", txt, elapsed_s=10.0)
    check(r["done"] == 5, "done without total")
    check("frac" not in r and "eta_s" not in r,
          "no frac/eta when total unknown (never a fake %)")
    check(r["rate_per_s"] == 0.5, "rate still computed from elapsed")


def test_compute_unknown_tool_and_silence():
    check(progress.compute("nope", "x") is None, "unknown tool -> None")
    check(progress.compute("spectre", "") is None, "empty log -> None")


def test_frac_clamped():
    # done can momentarily exceed a stale total; frac must not exceed 1.0.
    txt = "VA NORMAL conv=300 code_q=1 t=1"
    r = progress.compute("spectre", txt, elapsed_s=1.0, total=256)
    check(r["frac"] == 1.0, "frac clamped to 1.0 (%r)" % r.get("frac"))
    check("eta_s" not in r, "no ETA when already past total")


# --- CLI ------------------------------------------------------------------

def test_cli():
    d = tempfile.mkdtemp(prefix="prog-test-")
    try:
        log = os.path.join(d, "run.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("VA NORMAL conv=1 t=1\nVA NORMAL conv=2 t=2\n")
        out = subprocess.check_output(
            [sys.executable, os.path.join(os.path.dirname(__file__),
                                          "bin", "progress.py"),
             "spectre", log, "--elapsed", "4", "--total", "8"])
        obj = json.loads(out.decode())
        check(obj["done"] == 2 and obj["total"] == 8, "CLI json (%r)" % obj)
        check(obj["eta_s"] == 12, "CLI eta (2/4=0.5/s, 6 left -> 12s)")
        # no-signal file -> exit 1, no output
        empty = os.path.join(d, "empty.log")
        open(empty, "w", encoding="utf-8").close()
        rc = subprocess.call(
            [sys.executable, os.path.join(os.path.dirname(__file__),
                                          "bin", "progress.py"),
             "spectre", empty], stdout=subprocess.DEVNULL)
        check(rc == 1, "CLI exit 1 on no signal")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_progress_is_lf_no_bom_and_stdlib():
    path = os.path.join(os.path.dirname(__file__), "bin", "progress.py")
    with open(path, "rb") as fh:
        data = fh.read()
    check(not data.startswith(b"\xef\xbb\xbf"), "progress.py has no BOM")
    check(b"\r" not in data, "progress.py is LF-only")
    check(b"import dataclasses" not in data,
          "no dataclasses (must run on cluster python 3.6.8)")


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
