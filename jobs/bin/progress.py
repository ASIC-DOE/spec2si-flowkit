#!/usr/bin/env python3
"""progress.py -- shared, pure per-tool progress extractors + rate-based ETA.

docs/job_status_plan.md Phase 2. Progress is computed HERE, from the log
tail, by pure functions -- the sidecar runs this to publish live progress
and the report emitters can call the same functions for the final number,
so live status and the final verdict can never disagree (the plan's rule).

Each extractor is a pure function of log text -> {done, total?, label?} or
None. `compute()` adds the rate-based ETA: rate = done/elapsed (MEASURED,
never wall-clock-guessed -- that guess was the documented ~15x error;
STATUS.md prescribes reading the true elapsed instead), eta = remaining /
rate. Silence (no recognizable signal) returns None, never a fake 0/100%.

Ships in the jobs bundle; the sidecar invokes it via the cluster's system
python3 (3.6.8), so this module is pure stdlib and 3.6-compatible (no
dataclasses, no walrus). It parses numbers and fixed tool stage-names only
-- never paths -- so its output is NDA-safe to cross to the laptop.

  python3 progress.py <tool> <logfile> [--elapsed S] [--total N]
    -> one JSON line on stdout, or no output + exit 1 if no signal.

Usage:
  python3 progress.py <tool> <logfile> [--elapsed] [--total]
"""
import argparse
import json
import re
import sys

# --- per-tool extractors: text -> {done, total?, label?} | None ----------

_VA_NORMAL = re.compile(r"VA NORMAL conv=(\d+)")
_VA_CALSUB = re.compile(r"VA CAL(?:SUB|WARM)\b")
_VA_CALERR = re.compile(r"VA CAL err\[(\d+)\]")


def spectre(text):
    """Verilog-A SAR/ADC conversions: `VA NORMAL conv=N ...`. done = the
    highest N seen (robust to a duplicated/retried line). total is the
    expected point count -- caller-supplied (e.g. 256 for a 256-pt ENOB).

    CALIBRATED (mode_pwl) decks run their CALIBRATE state machine first,
    which emits CALSUB/CALWARM and NOT ONE `VA NORMAL conv=` line. Returning
    None there is honest, but for ~21% of such a run the job reads as having
    no signal at all -- indistinguishable from a stall, and it misled two
    separate readers on 2026-07-21 (both concluded something was wrong with
    a perfectly healthy job). Report the calibration phase explicitly
    instead: counted, visibly alive, and visibly NOT the conversion count.
    `phase` is what stops the caller's conversion total being applied to it
    (see compute) -- 86 cal sub-conversions over a 256-CONVERSION total
    would render as "34%", which is worse than no number at all.
    """
    ns = _VA_NORMAL.findall(text)
    if ns:
        return {"done": max(int(n) for n in ns)}
    nsub = len(_VA_CALSUB.findall(text))
    if nsub:
        # `VA CAL err[k]` lands once per calibrated target, so it is the
        # meaningful unit of cal progress; the sub-conversion count is the
        # liveness signal underneath it.
        return {"done": nsub, "phase": "cal",
                "label": "cal %d targets" % len(_VA_CALERR.findall(text))}
    return None


#: the canonical pnr step order (dig_flows/run.py stage_table). Each step's
#: TCL prints `=== <step> done` after write_db -- the same sentinel the
#: digital runner gates on, so live progress and the gate agree by regex.
INNOVUS_STEPS = ["init", "floorplan", "place", "cts", "route",
                 "checks", "export"]
_STEP_DONE = re.compile(r"===\s*(\w+)\s+done")


def innovus(text):
    """Count `=== <step> done` sentinels against the known 7-step table.
    label = the last completed step; total defaults to the table length."""
    steps = _STEP_DONE.findall(text)
    if not steps:
        return None
    known = [s for s in steps if s in INNOVUS_STEPS]
    done = len(known) if known else len(steps)
    out = {"done": done, "total": len(INNOVUS_STEPS)}
    out["label"] = (known or steps)[-1]
    return out


_RULECHECK = re.compile(r"RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count")


def calibre(text):
    """Each `RULECHECK <name> ... TOTAL Result Count = N` line is one rule
    finished -> done = rules checked so far (a liveness/progress proxy; the
    rule total is deck-dependent and not known from the log alone)."""
    n = len(_RULECHECK.findall(text))
    if not n:
        return None
    return {"done": n}


_TESTS = re.compile(r"TESTS=(\d+)")
_PASS_N = re.compile(r"PASS=(\d+)")
_TB_PASS = re.compile(r"TB:\s*PASS")
_TB_ANY = re.compile(r"TB:\s*(?:PASS|FAIL)")


def cocotb(text):
    """AMS/cocotb testbench progress: prefer explicit `TESTS=n`/`PASS=n`
    counters, else count `TB: PASS` lines against `TB: PASS|FAIL` lines."""
    mt = _TESTS.search(text)
    mp = _PASS_N.search(text)
    if mt:
        out = {"done": int(mp.group(1)) if mp else 0, "total": int(mt.group(1))}
        return out
    npass = len(_TB_PASS.findall(text))
    nany = len(_TB_ANY.findall(text))
    if not nany:
        return None
    return {"done": npass, "total": nany}


TOOLS = {"spectre": spectre, "innovus": innovus,
         "calibre": calibre, "cocotb": cocotb}


# --- rate + ETA -----------------------------------------------------------

def assemble(tool, done, total=None, label=None, elapsed_s=None, phase=None):
    """Build a progress record from an ALREADY-KNOWN (done, total) plus a
    measured elapsed -- the single place rate/ETA are computed, shared by
    the log extractors (compute, below) and the in-process recorder
    (jobrec), so live status and the final number can never disagree. rate
    is MEASURED (done/elapsed), never a wall-clock guess (the ~15x error)."""
    out = {"tool": tool, "done": done}
    if phase:
        out["phase"] = phase
    if label:
        out["label"] = label
    if total:
        out["total"] = total
        out["frac"] = round(min(done, total) / float(total), 4)
    if elapsed_s and elapsed_s > 0 and done > 0:
        rate = done / float(elapsed_s)
        out["rate_per_s"] = round(rate, 6)
        if total and done < total and rate > 0:
            out["eta_s"] = int((total - done) / rate)
    return out


def compute(tool, text, elapsed_s=None, total=None):
    """Full progress record for `tool` over log `text`, or None if no
    signal. Adds frac, measured rate_per_s, and eta_s when enough is
    known."""
    ext = TOOLS.get(tool)
    if ext is None:
        return None
    r = ext(text)
    if r is None:
        return None
    phase = r.get("phase")
    # A phase counts a DIFFERENT unit of work than the caller's total, so it
    # must never borrow it: cal sub-conversions over a conversion total is a
    # meaningless percentage that would look authoritative.
    tot = None if phase else (total if total is not None else r.get("total"))
    return assemble(tool, r["done"], total=tot, label=r.get("label"),
                    elapsed_s=elapsed_s, phase=phase)


def from_file(tool, path, elapsed_s=None, total=None):
    try:
        with open(path, errors="replace", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    return compute(tool, text, elapsed_s=elapsed_s, total=total)


def _main(argv=None):
    p = argparse.ArgumentParser(description="per-tool progress extractor")
    p.add_argument("tool", choices=sorted(TOOLS))
    p.add_argument("logfile")
    p.add_argument("--elapsed", type=float, default=None,
                   help="measured elapsed seconds (enables rate + ETA)")
    p.add_argument("--total", type=int, default=None,
                   help="expected total (enables frac + ETA)")
    ns = p.parse_args(argv)
    out = from_file(ns.tool, ns.logfile, elapsed_s=ns.elapsed, total=ns.total)
    if out is None:
        return 1                      # no signal -> no output, nonzero
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
