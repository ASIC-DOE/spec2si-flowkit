#!/usr/bin/env python3
"""Host speed from CALIBRATION: the same work timed on every host.

Why this replaced the production-history estimate (2026-11-03): a job's
`rate_per_s` counts ITS OWN units -- a Spectre campaign counts benches of
whatever size that campaign has -- so dividing per-host median rates within a
flow measured which jobs a host was given, not how fast it is. On the shared
store (1,494 jobs) one host's spectre-campaign rates spanned five decades, and
two identical Xeon W-2155s read 24x (asic8) and 0.08x (asic9); combined, asic8
read 11.9x and was picked over the 9950X3D for chip P&R. Work is not assigned
to hosts at random, so no statistic over that history separates host from
workload. Only identical work does.

Evidence comes from two places, each record `(suite, host, elapsed_s)`:

* `calibration_ledger.json` beside this file: measurements made outside the
  tracker, each citing its source (a job id and the tool's own log line, or a
  document line and its commit). A run the source records as loaded is left
  out -- contention is not the host's speed.
* tracked jobs of flow CAL_FLOW, `target` = the suite name, as `jobs
  calibrate` launches them. Their `elapsed_s` is the job's wall time.

A suite is ONE piece of work: same input, same tool build, same options and
thread count. Different builds of a tool are different suites.

The estimate is the two-way model log t(suite, host) = a(suite) - s(host),
fitted by least squares: every suite on its own scale a, every host one speed
s, so suites covering different host sets still land on one scale through the
hosts they share. Each (suite, host) cell is the host's FASTEST run --
contention only ever slows a run (measured: asic6 82.5 s while loaded, 55.7 s
clean, same netlist). Speeds are reported relative to the median calibrated
host (1.0 = typical, as before), with n = the number of suites behind each.
"""
import json
import math
import os

#: tracked calibration jobs: flow, with target = suite
CAL_FLOW = "calibration"

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_ledger.json")


def load_ledger(path=None):
    """[(suite, host, elapsed_s)] from the committed ledger ([] if absent)."""
    path = path or LEDGER
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for suite in doc.get("suites", []):
        name = suite.get("name")
        for r in suite.get("runs", []):
            if r.get("excluded"):
                continue                 # e.g. a run the source records as loaded
            e = r.get("elapsed_s")
            if name and r.get("host") and isinstance(e, (int, float)) and e > 0:
                out.append((name, r["host"].lower(), float(e)))
    return out


def from_jobs(jobs):
    """[(suite, host, elapsed_s)] from finished tracked calibration jobs."""
    out = []
    for j in jobs or []:
        if j.get("flow") != CAL_FLOW or j.get("state") != "done":
            continue
        e, h, s = j.get("elapsed_s"), j.get("host"), j.get("target")
        if s and h and isinstance(e, (int, float)) and e > 0:
            out.append((s, h.lower(), float(e)))
    return out


def fit(records, iters=200):
    """host -> (speed, n_suites) from (suite, host, elapsed_s) records.

    Only suites timed on >= 2 hosts carry a comparison, and only hosts joined
    to the largest group of suites through shared hosts are on one scale; a
    host outside it is left unmeasured (None), never guessed."""
    best = {}
    for s, h, e in records:
        k = (s, h)
        best[k] = min(best.get(k, e), e)
    by_suite = {}
    for (s, h), e in best.items():
        by_suite.setdefault(s, {})[h] = math.log(e)
    by_suite = {s: v for s, v in by_suite.items() if len(v) >= 2}
    if not by_suite:
        return {}
    comp = _largest_component(by_suite)
    by_suite = {s: v for s, v in by_suite.items() if s in comp}
    hosts = sorted(set(h for v in by_suite.values() for h in v))
    # alternating least squares on log t = a_s - s_h (exact for a connected design)
    sp = dict((h, 0.0) for h in hosts)
    a = {}
    for _ in range(iters):
        a = dict((s, sum(lt + sp[h] for h, lt in v.items()) / len(v)) for s, v in by_suite.items())
        new = {}
        for h in hosts:
            terms = [a[s] - v[h] for s, v in by_suite.items() if h in v]
            new[h] = sum(terms) / len(terms)
        moved = max(abs(new[h] - sp[h]) for h in hosts)
        sp = new
        if moved < 1e-12:
            break
    ref = _median([sp[h] for h in hosts])
    n = dict((h, sum(1 for v in by_suite.values() if h in v)) for h in hosts)
    return dict((h, (math.exp(sp[h] - ref), n[h])) for h in hosts)


def speeds(jobs=None, ledger=None):
    """host -> (speed, n_suites): the ledger plus tracked calibration jobs."""
    recs = (load_ledger() if ledger is None else ledger) + from_jobs(jobs)
    return fit(recs)


def suites(jobs=None, ledger=None):
    """suite -> sorted hosts it timed (for the table's provenance line)."""
    recs = (load_ledger() if ledger is None else ledger) + from_jobs(jobs)
    out = {}
    for s, h, _e in recs:
        out.setdefault(s, set()).add(h)
    return dict((s, sorted(v)) for s, v in out.items())


def _largest_component(by_suite):
    """The suites joined, through shared hosts, to the most hosts."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for s, v in by_suite.items():
        for h in v:
            parent[find(("s", s))] = find(("h", h))
    groups = {}
    for s, v in by_suite.items():
        g = groups.setdefault(find(("s", s)), [set(), set()])
        g[0].add(s)
        g[1].update(v)
    return max(groups.values(), key=lambda g: (len(g[1]), sorted(g[0])))[0]


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0
