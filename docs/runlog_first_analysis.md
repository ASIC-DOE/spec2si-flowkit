<!--docmeta
title: The agent-loop runlog, first analysis — what 343 attempts can and cannot say
genre: study
status: active
area: top
owner: soumyajit
updated: 2026-08-26
summary: First pass over the harvested runlog (343 attempts, 62 cells, 94 sessions, across tsmc28/tsmc65/xt011) with ZERO causes classified, so nothing here says why an attempt ended. The first finding was about the instrument: the claim this log exists to test -- "~10 offline iterations for zero cluster runs" -- measured anywhere from 2.18 to 8.76 offline turns per cluster round trip depending only on whether the bare substrings "cluster" and "deployment/" counted as reaching the cluster, and 110 of 162 cluster tags came from exactly that hint. Tightening _CLUSTER_HINT into _CLUSTER_RE (hosts as tokens across asic1..asic10, evidence recorded, retier to re-derive) collapsed the band: 219 of 343 attempts re-tiered give 5.42 per round trip overall and 6.25 for analog -- the same order as the claim, somewhat lower, and the first time the number has come from anything but memory. Two corrections to how it is formed: an attempt is a TURN and a round trip spans a mean of 2.0 turns, and "offline" is a fallback rather than a detection. A separate and worse finding along the way: the transcripts are NOT durable (cleanupPeriodDays defaults to 30, unset here), so a rebuild-from-transcripts destroyed 100 records naming real cells and the committed log was the only copy -- 124 attempts still carry the loose tag because their transcripts have expired. Robust regardless: cluster turns cost 3x the actions and carry ~2x the error rate of offline ones, and 8 of 62 cells absorb half of all attempts.
-->

# The runlog, first analysis

**Population.** 343 attempts, 62 cells, 94 sessions, three repos.
**Classified: 0.** Nothing here explains *why* an attempt ended — that is
the declared half of the contract and it has never been worked. Everything
below comes from the harvested skeleton: tier, domain, actions, errors,
files, timing.

## 1. The headline claim, measured

The log exists to test a sentence quoted from RESUME.md prose:

> "~10 offline iterations for zero cluster runs."

Two corrections have to be applied before the log's numbers are even
comparable to it.

**An attempt is a TURN, and a round trip is several turns.** Launch, poll,
poll, fetch — each trips a cluster hint, so the naive offline:cluster turn
ratio divides by every poll. Collapsing maximal runs of consecutive
cluster turns on one cell into one round trip (mean **2.0** turns per trip,
83 trips behind 162 cluster turns) is what makes the number comparable.

**"offline" is a fallback, not a detection.** `_tier` returns `cluster` on
the first matching hint and `offline` otherwise, with basis *"no cluster
signal in the tail"* — 181 of 343. An offline attempt is one where nothing
looked like the cluster, which is an absence, not an observation.

With both applied, the first pass found the answer depended entirely on one
judgement call — whether the bare substrings `cluster` and `deployment/`
counted as evidence that a turn reached the cluster:

| tier rule | offline | round trips | per turn | **per round trip** |
|---|---|---|---|---|
| loose (as first recorded) | 181 | 83 | 1.12 | **2.18** |
| strong hints only | 298 | 34 | 6.62 | **8.76** |

**110 of 162 cluster tags came from that loosest hint**, and only 6 had a
`cluster`-named file — the rest fired on an action *summary*, which the
record did not store, so they could not be audited. A factor of four,
bracketing the claim, and unnarrowable by collecting more data.

### Resolved: the hint list was tightened

`_CLUSTER_HINT` became `_CLUSTER_RE` on 2026-08-26. `cluster` and
`deployment/` are gone (a file under `deployment/` is a script *about* the
cluster; editing it is not a round trip), hosts match as tokens across
`asic1`..`asic10` + `exxact`/`dgx-spark` rather than just `asic6`/`asic7`,
and `tier_evidence` now records the basename of the matching action so a
tag can be checked afterwards. `runlog.py retier` re-derives the field for
records still reachable in the transcripts.

**219 of 343 attempts re-tiered. The band collapses to one number:**

| population | offline | round trips | **per round trip** |
|---|---|---|---|
| tight rule, all | 179 | 33 | **5.42** |
| tight rule, analog | 150 | 24 | **6.25** |
| tight rule, digital | 29 | 9 | 3.22 |

Against "~10": the same order, somewhat lower. The most defensible reading
is that the RESUME sentence describes a good episode rather than the fleet
average, and the fleet average for analog work is about **6 offline
iterations per cluster round trip**. That is a real result and it is the
first time the number has come from anything but memory.

⚠️ **124 attempts keep the loose tag and cannot be re-derived** — their
transcripts have expired (see §5). They are identifiable by the absence of
`tier_evidence`, so the two populations can be kept apart; the table above
is the 219 that could be re-tiered.

## 2. Cluster turns are much more expensive, and that is robust

| tier | n | actions (median) | actions (mean) | attempts with ≥1 error |
|---|---|---|---|---|
| offline | 181 | 19 | 30.6 | 39% |
| cluster | 162 | 62 | 93.0 | 72% |

Three times the actions and nearly twice the error rate. This is the
premise of the two-tier gate architecture stated as a measurement rather
than as a memory, and it does not depend on the ratio question above:
whatever the correct tier rule, the turns that *do* reach the cluster are
the heavy ones. (Computed under the loose rule; the direction is what is
robust, not the exact multiple.)

## 3. The loop is concentrated

Eight of 62 cells absorb **half** of all attempts. The top two are in one
repo and could not look more different:

| cell | attempts | offline:cluster |
|---|---|---|
| `cap_dac_8bit_1` | 31 | 5.2 |
| `vco_symmetric_4` | 27 | 0.59 |

A nine-fold difference in loop shape between two blocks in the same repo,
in the same period, under the same gates. That is the most interesting
thing in the data and it is exactly what causes would explain — whether
the VCO was cluster-heavy because its gates could not run offline, or
because its attempts kept failing for reasons an offline gate never
models. The skeleton cannot say.

## 4. One measurement that is not discriminating

**81% of attempts show thrash** (77% offline, 86% cluster). A signal that
fires on four attempts in five is not separating anything; it is measuring
"the agent edited a file more than once", which is ordinary. Either the
threshold wants raising or the field wants dropping — as recorded it
cannot support a claim.

## 5. The transcripts are not durable

Found the hard way while re-deriving the tier. `runlog.py` claimed "a
harvest six months late still recovers everything"; Claude Code deletes
transcripts after `cleanupPeriodDays`, which defaults to **30** and is
unset here. The oldest surviving tsmc65 transcript is 2026-07-28 while the
committed log reaches 2026-07-10.

Acting on that false sentence, a rebuild-from-transcripts dropped **100
records naming real cells** (`preamp` alone had 24). They came back only
because the log was committed to git. Two rules follow, now in the module
header: a rebuild is never safe, use `retier`; and the harvest hook is
load-bearing rather than a convenience — when it was dead for 18 days in
tsmc28 the gap happened to fall inside the retention window, and a
fortnight more would have made those attempts unrecoverable.

## 6. What cannot be said yet

Everything causal. With 0 of 343 classified there is no answer to: what
fraction of attempts end in `silent-pass`; whether `transport` really runs
at SABLE's 1-in-11; whether `engine-defect` is falling as the gates mature.
Those are the questions the enumeration exists for and they need the
declared half.

The route to them is `runlog.py sample`: a stratified random sample is
enough for a proportion, and a census of 343 (growing ~7.9/day) is not
owed. Agents may now declare the three MECHANICAL causes for their own
attempts (`tool-error`, `transport`, `gate-fail`) so the backlog stops
growing; `closed`, `silent-pass` and `abandoned` stay human, because an
actor grading its own turn is not the one to judge whether the work was
good.
