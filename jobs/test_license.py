#!/usr/bin/env python3
"""Tests for the shared FlexLM seat query (license.py).

The lmstat subprocess path is verified live on the cluster; here we unit
test the pure parse, the server-from-env resolution, and the alias map.
  python3 test_license.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "bin"))
import license as lic  # noqa: E402

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


# a real lmstat -f block shape
_SAMPLE = (
    "lmstat - Copyright (c) 1989-2018 Flexera.\n"
    "Users of Virtuoso_Multi_mode_Simulation:  (Total of 54 licenses "
    "issued;  Total of 3 licenses in use)\n"
    '\n  "Virtuoso_Multi_mode_Simulation" v25.1, vendor: cdslmd\n'
    "    smandal asic6 /dev/tty (v25.1) (iolicense2/7183 101), start ...\n")


def test_parse_totals():
    t = lic.parse_totals(_SAMPLE)
    check(t == {"issued": 54, "in_use": 3, "free": 51},
          "parse 54 issued / 3 in use -> 51 free (%r)" % t)
    # singular "license" form
    t = lic.parse_totals("(Total of 1 license issued;  Total of 1 license "
                         "in use)")
    check(t == {"issued": 1, "in_use": 1, "free": 0}, "singular form + 0 free")
    check(lic.parse_totals("no totals here") is None, "no totals -> None")
    check(lic.parse_totals("") is None, "empty -> None")


def test_full_seats():
    t = lic.parse_totals(_SAMPLE)
    check(t["free"] == 51 and t["issued"] == 54, "sample -> 51/54 free")
    # exhausted pool
    ex = lic.parse_totals("(Total of 2 licenses issued;  Total of 2 "
                          "licenses in use)")
    check(ex["free"] == 0, "exhausted pool -> 0 free (WAITING_LICENSE case)")


def test_resolve_alias():
    feat, prefer = lic.resolve("spectre")
    check(feat == "Virtuoso_Multi_mode_Simulation" and prefer == "7183@",
          "spectre alias resolves to the Cadence feature/server")
    feat, prefer = lic.resolve("Some_Raw_Feature")
    check(feat == "Some_Raw_Feature" and prefer == "7183@",
          "raw feature passes through with default prefix")


def test_server_from_env():
    saved = {k: os.environ.get(k) for k in
             ("CDS_LIC_FILE", "LM_LICENSE_FILE", "ALL_LICENSE_FILES")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        check(lic.server_from_env() is None, "no env -> no server")
        os.environ["LM_LICENSE_FILE"] = "7184@calib:7183@iolicense2:5280@other"
        check(lic.server_from_env("7183@") == "7183@iolicense2",
              "prefers the 7183@ (Cadence) token")
        check(lic.server_from_env("7184@") == "7184@calib",
              "prefers a different family when asked")
        os.environ["LM_LICENSE_FILE"] = "5280@only"
        check(lic.server_from_env("7183@") == "5280@only",
              "falls back to the first @ token")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_seats_no_server_is_none():
    saved = {k: os.environ.get(k) for k in
             ("CDS_LIC_FILE", "LM_LICENSE_FILE", "ALL_LICENSE_FILES")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        # no server resolvable, and none passed -> None, never raises
        check(lic.seats("spectre") is None, "no server -> seats None")
        check(lic.free("spectre") is None, "no server -> free None")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_stdlib_lf():
    with open(os.path.join(os.path.dirname(__file__), "bin", "license.py"),
              "rb") as fh:
        data = fh.read()
    check(b"\r" not in data, "license.py is LF-only")


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
