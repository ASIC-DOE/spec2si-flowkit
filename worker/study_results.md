<!--docmeta
title: The frozen-task study and the reversal ledger, as reported in the paper
genre: reference
status: active
area: worker
owner: soumyajit
updated: 2026-10-27
summary: Per-task results of the controlled comparison of chat-driven (B) and contract-driven (C) execution on the 19 frozen contracts in worker/contracts (118 runs, every run correct, no false acceptance), with the paired task-level differences, and the ledger of the ten design reversals the paper "Agent-Compiled Analog/Mixed-Signal Design" cites, each confirmed by the designer. Sources in the ledger are commits and plan sections in the private process repositories.
-->

# The frozen-task study and the reversal ledger

This page holds the per-task results and the reversal ledger behind Sections VI-A and VI-B of the paper
*Agent-Compiled Analog/Mixed-Signal Design: From Specification to Signoff-Verified Circuits*. The contracts themselves
are in [`contracts/frozen`](contracts/frozen), [`contracts/frozen-tracked`](contracts/frozen-tracked) and
[`contracts/frozen-tracked-stophook`](contracts/frozen-tracked-stophook).

## 1. Contract versus chat on the frozen tasks

**Conditions.** B is an ordinary chat session with the job tracker and written guidance. C is the bounded worker of
this directory executing the same contract. Both receive the same goal, editable set and gates. Each task's oracle,
written before any run, states the correct outcome: an accepted change, or a stop with a design question.

**Runs.** Thirteen tasks use local tools only and were run three times per condition with Claude Code or Codex. Six
use licensed tools through the tracker and were run two to five times per condition with Claude Code (f7 ran three
times in the first tracked campaign and twice in a later, widened one). 118 runs in all. The exports record harness
versions (Claude Code 2.1.281, Codex CLI 0.155) but not model identifiers. Codex runs record tokens, not charges, so
their dollar columns are zero.

**Scoring.** Every run was scored against its task's oracle. All 118 were correct, with no false acceptance and no
edit outside the contract's scope. *Licensed* is the run's own licensed-tool launches; the 10 further runs needed to
judge condition B's tracked results are not in these rows.

| Task | Harness | B runs | C runs | B min (mean) | C min (mean) | B $ (mean) | C $ (mean) | B licensed | C licensed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| f1-guard-order | claude | 3 | 3 | 0.47 | 0.47 | 0.266 | 0.249 | 0 | 0 |
| f2-parameters-file | codex | 3 | 3 | 2.53 | 2.93 | — | — | 0 | 0 |
| f3-mangled-parameters | claude | 3 | 3 | 1.67 | 1.65 | 0.238 | 0.238 | 0 | 0 |
| f4-unpackaged-tests | codex | 3 | 3 | 2.37 | 1.96 | — | — | 0 | 0 |
| f5-codex-tokens | claude | 3 | 3 | 1.60 | 1.53 | 0.339 | 0.326 | 0 | 0 |
| f6-report-note | codex | 3 | 3 | 2.71 | 2.79 | — | — | 0 | 0 |
| f7-ota-fault | claude | 5 | 5 | 1.43 | 0.90 | 0.407 | 0.284 | 7 | 5 |
| f8-buffer-stop | claude | 3 | 3 | 4.98 | 0.75 | 3.346 | 1.327 | 5 | 0 |
| f9-license-lumerical | codex | 3 | 3 | 2.35 | 2.62 | — | — | 0 | 0 |
| f10-card-profiles | claude | 3 | 3 | 0.66 | 0.50 | 0.311 | 0.288 | 0 | 0 |
| f11-stats-report | codex | 3 | 3 | 1.51 | 1.55 | — | — | 0 | 0 |
| f12-abstract-purposes | claude | 3 | 3 | 0.61 | 0.65 | 0.264 | 0.256 | 0 | 0 |
| f13-macro-gds-shared | codex | 3 | 3 | 1.22 | 1.32 | — | — | 0 | 0 |
| f14-rail-stub | claude | 3 | 3 | 0.92 | 0.87 | 0.338 | 0.333 | 0 | 0 |
| f15-wide-claim | codex | 3 | 3 | 1.64 | 1.68 | — | — | 0 | 0 |
| f16-ota-wrong-top | claude | 3 | 3 | 1.36 | 0.97 | 0.477 | 0.313 | 3 | 3 |
| f17-ota-truncated-report | claude | 3 | 3 | 1.30 | 1.02 | 0.503 | 0.371 | 3 | 3 |
| f18-bandgap-tempco | claude | 3 | 3 | 3.21 | 1.71 | 0.787 | 0.437 | 5 | 3 |
| f19-sdc-fault | claude | 3 | 3 | 5.12 | 4.38 | 1.048 | 0.412 | 5 | 3 |

**Totals** (the paper's Table III). Local tasks: B 39 runs, 61 min, \$5.27; C 39 runs, 62 min, \$5.07. Tracked
tasks: B 20 runs, 55 min, \$20.52, 18 licensed runs plus 10 to judge its results; C 20 runs, 31 min, \$10.00, 17
licensed runs.

**Paired task-level differences** (C minus B, per-task means):

- Wall time: median −0.05 min, interquartile range −0.41 to +0.04 min; C faster on 11 of 19 tasks.
- Model cost, tasks with Claude runs: median −\$0.073, interquartile range −\$0.165 to −\$0.008; C cheaper on 11 of 12
  tasks.

**A fixable defect in B.** Four of B's tracked runs ended waiting on a background timer with their job uncollected.
After a foreground wait and a stop guard closed that path, a re-run of the two affected tasks
([`contracts/frozen-tracked-stophook`](contracts/frozen-tracked-stophook)) took B's mean cost from \$0.92 to \$0.54
per run, against \$0.42 for C.

Runs are repeated observations on a small task set, so these figures are descriptive. Zero observed false
acceptances do not imply a deployment failure rate. The tracked tasks are short, and human time is zero by
construction in both conditions.

## 2. The reversal ledger

A reversal is a withdrawn, overturned or retracted conclusion recorded in a plan document or commit, attributed to the
change that introduced it. These are the ten the paper cites. The designer confirmed each (2026-10-27) and corrected
one classification. Sources are commits or plan sections in the private process repositories. Measurements from
the readout study, which belongs to a separate paper, are described here but not quoted.

| # | Kind | Effort | Initial claim | Originator | Overturned by | Measurement or reason | Revised decision | Source |
|---:|---|---|---|---|---|---|---|---|
| 1 | Designer override | Hybrid ADC v7 driver | The ideal-antiphase "symmetric" driver | Agent | Designer | The designer showed the symmetric construction to be circular | Driver section corrected | spec2si-tsmc65 `d6aa6642` |
| 2 | Designer override | Readout architecture | A short-window network recommended | Agent | Designer | Objection on pile-up; on the shared rate benchmark a multi-pulse fit measured far better at high rate | Withdrawn; the fit replaces the network | spec2si-tsmc65 readout plan §2.12 |
| 3 | Designer override | Readout architecture | A gated hybrid network detects pile-up by its seed gate | Agent | Designer | The seed gate left many clean pulses wrong at high rate | Seed gate plus a residual χ² test | spec2si-tsmc65 readout plan §2.30 |
| 4 | Designer override | 65 nm PLL top level | Ringing after the extracted lock is the charge pump's amplifier loop | Agent | Designer | Suspected integration error; tighter integration and a convergence ladder removed the ringing | Ringing treated as numerical | spec2si-tsmc65 `3458837b`, `e3084816` |
| 5 | Correction by evidence | Hybrid ADC v7 controller | The real comparator does not close the loop | Agent | Evidence | The failure was the agent's own bench | Retracted; the controller converts with the real comparator | spec2si-tsmc65 `8ea009bc` |
| 6 | Correction by evidence | 28 nm bandgap | The agent's null-ratio sizing | Agent | Evidence | The first DC operating point contradicted it | Ratio corrected | spec2si-tsmc28 `6e79937` |
| 7 | Correction by evidence | Readout architecture | Filter re-optimization worth a large gain | Agent | Evidence | A measured noise above its predicted bound exposed bounds averaged over one decimation phase | Worth little | spec2si-tsmc65 readout plan §2.22, §2.25 |
| 8 | Correction by evidence | Readout architecture | n-on-p sensor for timing | Agent | Evidence | A literature survey: published Si X-ray sensors with stated polarity are p-on-n | p-on-n with an unshaped timing path | spec2si-tsmc65 readout plan §2.26 |
| 9 | Inherited design overturned | Readout architecture | A per-channel network is needed for amplitude (published design) | Human (published design) | Evidence | A simple phase-table readout measured lower noise than the trained networks | Network abandoned | spec2si-tsmc65 readout plan §2.30 |
| 10 | Inherited design overturned | Hybrid ADC v7 controller | Asynchronous (QDI) SAR controller, the designer's own choice | Human (designer) | Designer | Abandoned at the designer's suggestion: an earlier synchronous design reached 40 MS/s in silicon, while QDI collapsed post-layout | Synchronous controller | spec2si-tsmc65 `8795acee` |

Four rows are designer overrides of the agent, four are corrections of the agent by closed-mode evidence, and two
overturn inherited human designs.
