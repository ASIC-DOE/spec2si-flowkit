<!--docmeta
title: Agentic workflow baseline — how chat sessions use the cluster (condition A)
genre: study
status: active
area: top
owner: soumyajit
updated: 2026-09-26
summary: The §8 condition-A baseline of the agentic workflow study, measured from surviving session transcripts before the tracker was activated (tsmc65: 24 sessions, 0 tracked, 375 licensed launches, 12.2 h of in-command sleep), and the B-versus-C comparison on 19 frozen implementation tasks, 118 runs (2026-09-26): both conditions correct on every run with no false acceptance; on the six tracked tasks C took 44 % less time and half the model cost, and left no loose ends. Decision: B for small decided local tasks, C for tracked, diagnosis and stop tasks.
-->

# Agentic workflow baseline — condition A

This is the first measurement for §8 of [the agentic workflow study](agentic_workflow_report.md).
Condition A is today's tool-enabled chat: what sessions actually did on the
cluster before the tracker became the default. It is the reference for
everything that follows: the "after" measurement of chat with the tracker
(condition B in ordinary use) and the frozen-task comparison of B with a
bounded worker (C).

Read it with the study's §4.11: design work alternates between **exploration**
(chat) and **implementation** (agentic flows), and a failed implementation must
come back as a **failure report** that starts the next exploration round. The
baseline therefore measures both the implementation traffic and whether
failures are recorded in a form exploration can use.

## Method

`integrations/cluster_jobs/acceptance/baseline.py` reads Claude Code
transcripts locally and prints counts only. No command text leaves the machine
or enters this page. The committed session-log harvest (`browse/runlog.py`)
cannot answer these questions, because by design it keeps no commands.

- **Corpus.** Claude Desktop sessions of each repository. Headless trial
  sessions (`claude -p`) and everything from 2026-09-24 (activation day and the
  acceptance trials) are excluded. Tool calls are de-duplicated by id, because a
  resumed session copies its history into a new file. Subagent calls count.
- **Coverage.** Transcripts expire after 30 days. **tsmc65 is the clean
  window**: all 24 sessions that started 2026-08-30 to 2026-09-23, with every
  file present. tsmc28 and xt011 reach further back only through resumed
  sessions' copied histories, so their windows are **partial**. sky130 has no
  session before activation. No Codex session predates activation.
- **Classes.** Every shell call that touches the cluster (ssh, the tool
  wrappers, `remote_task.sh`, scp/rsync) is classified, first match wins:
  - *tracked*: a `jobs.workflow` call;
  - *EDA launch*: a licensed tool (Spectre, Innovus, Genus, Calibre, Pegasus, Quantus, Xcelium, Virtuoso) or a flow runner that calls one, run remotely; split into *detached* (nohup, setsid, screen, tmux, a trailing `&`) and foreground;
  - *compute*: another command run through the tool wrapper (Python generators and analysis), usually unlicensed;
  - *transfer*, *kill*;
  - *read*: everything else remote, such as log tails, greps and process checks: monitoring and diagnosis;
  - *opaque*: a script fed to ssh whose content the transcript does not show.

  Scripts written earlier in the same session (with the Write tool or `cat > f <<EOF`) are resolved, so `ssh host bash -s < f` is classified by what `f` does. Before resolution, two thirds of cluster calls were opaque; after it, 2 %.
- **Checked by hand.** Randomly sampled calls were read with the reason for each
  class: about **22 of 25 EDA launches** and **all 12 detached launches** were
  launches; the misses were a DRC-result analysis and a Tcl check named like
  runners. The compute class is right in kind but noisier. The classes are
  heuristics; quote them as such.

## Results

| Measure | tsmc65 (clean) | tsmc28 (partial) | xt011 (partial) |
|---|---:|---:|---:|
| Sessions | 24 | 59 | 26 |
| User prompts / interrupts | 1,051 / 38 | 852 / 15 | 358 / 15 |
| Tool calls / shell calls | 29,482 / 25,839 | 34,078 / 27,515 | 12,256 / 9,705 |
| Cluster calls | 8,041 | 7,377 | 2,583 |
| **EDA launches** (detached) | **375** (44) | **161** (61) | **111** (8) |
| Other cluster compute runs (detached) | 607 (36) | 575 (161) | 0 |
| **Tracked calls** | **0** | **0** | **1** |
| Cluster reads (monitoring, diagnosis) | 5,197 | 4,731 | 1,262 |
| Reads per launch (EDA + compute) | 5.3 | 6.4 | 11.4 |
| Transfers | 1,630 | 1,579 | 1,154 |
| Kills | 61 | 91 | 2 |
| Opaque scripts | 171 | 240 | 53 |
| Exact repeated EDA launches in a session | 6 | 10 | 25 |
| Hours of `sleep` inside commands | 12.2 | 15.3 | 1.6 |
| Wait-tool calls (Monitor, wake-ups, output polls) | 51 | 152 | 6 |
| Sessions with at least one launch | 13 | 40 | 9 |

What condition A looks like, from the clean tsmc65 window:

- **Nothing was tracked.** 375 licensed-tool launches and 607 other compute
  runs in 25 days, all outside any job record. 12 % of the EDA launches were
  detached (nohup or setsid): jobs whose only record is the conversation.
- **Most cluster traffic is watching, not doing.** 5,197 reads against 982
  launches: five reads per launch (14 per licensed launch), plus 1,630
  transfers.
- **Waiting happens inside the conversation.** Commands slept for 12.2 hours in
  total, with the session blocked, plus 51 calls to wait tools.
- **Recovery is manual.** 61 kills. Exact repeats of a launch command inside a
  session were rare (6, 1.6 %); a lost or uncertain launch was usually
  investigated by reads, not resubmitted.
- **The shell exit code says little.** Only 2 EDA launches returned an error
  to the session: a detached launch always "succeeds", and a foreground one
  often ends in a pipe to `tail`. Engineering outcomes live in the logs the
  reads inspect.

### Failures are not recorded as failures

The committed session-log harvest covers more time than the transcripts (since
July), but without commands. It records attempts, their tier (offline or
cluster), errors and "thrash" (repeated edits of one file). It also has a field
for the **terminal cause** of an attempt, from a fixed enumeration.

| Repo | Attempts | Cluster-tier share by month (Jul / Aug / Sep) | Attempts with a declared cause |
|---|---:|---|---:|
| tsmc65 | 369 | 39 % / 17 % / 16 % | 0 |
| tsmc28 | 449 | 38 % / 37 % / 15 % | 0 |
| xt011 | 140 | — / 18 % / 23 % | 0 |
| sky130 | 7 | — / — / 0 % | 0 |

**None of 965 attempts has a declared cause.** Failures and their reasons exist
only in RESUME prose and conversation. Under §4.11 this is the gap that matters
most: an implementation failure is the input to the next exploration round, and
today it has no structured form to be counted, found or handed over.

## Limits

- One engineer, four repositories, a few weeks, heavily weighted to tsmc65's ADC
  work. This describes this project's practice, not chat in general.
- The classes are heuristic, and precision was checked on samples only; recall
  was not measured.
- Transcripts carry no reliable per-session cost, and "human active time" is
  approximated by prompts and interrupts.
- The older job-status launcher (`runjob`, `jobs run`) was available through
  the whole window; none of these launches used it (it is counted as tracked).

## The "after" measurement (B in ordinary use)

The tracker became the default in all four consumers on 2026-09-24. The plan is
to re-run the same script on two weeks of ordinary sessions, 2026-09-25 to
2026-10-08, from **2026-10-09**. `baseline.py` gained what that needs
(flowkit `e881fef`):

- `--exclude-session ID`: the tracker's own development sessions are not
  ordinary use. Excluded so far (tsmc65): `498c3173-79ac-4044-97e6-4e763604ae04`
  (this workstream) and `e0fcea60-308c-4a9b-a6ee-2c29d7205688` (the licence-
  signature task). Headless runs (worker rounds, B sessions, canaries) leave no
  transcript or are excluded as `sdk-cli`.
- `--hook deployment/bnl/tracker_hook.py`: the **migrated flows** are counted by
  the repo's own deployed guard (every command naming a migrated executable is
  put to it as a synthetic PreToolUse event): tracked starts and collects,
  untracked launches the hook refused in the session, and untracked launches
  that ran (escapes).
- `cluster_call_hours` and `cluster_calls_over_10min`: time the session sat
  inside cluster calls. Literal `sleep` saw only a quarter of it: condition A
  (tsmc65, 25 days) spent **45.5 h** in cluster calls, 13 of them over ten
  minutes, against 12.2 h of `sleep`.

**Interim look, 2026-09-25 and 26 (two days, as of 16:30 on the 26th while sessions were still running; not the measurement).** Only
tsmc65 had ordinary sessions (three, all analog engineering: an AFE trim DAC,
a comparator preamplifier, the ADC driver's capacitor; the script's session
count of 4 includes an ID-less fragment); tsmc28, xt011 and sky130 had none.

| Measure (tsmc65) | Condition A, 25 days | Interim, 2 days |
|---|---:|---:|
| Ordinary sessions | 24 | 3 |
| EDA launches (detached) | 375 (44) | 34 (1) |
| Other cluster compute runs | 607 | 40 |
| Cluster reads, per launch | 5,197, 5.3 | 248, 3.4 |
| Hours in cluster calls, per day | 45.5, 1.8 | 3.3, 1.7 |
| `sleep` hours | 12.2 | 1.3 |
| Kills | 61 | 4 |
| Opaque scripts, share of cluster calls | 171, 2 % | 99, 19 % |
| **Migrated flow** (digital smoketest synthesis): tracked starts / refused / ran untracked | — | **0 / 0 / 0** |

**What it says so far.** The ordinary work of these two days never touched a
migrated flow: every launch was an analog Spectre characterization campaign
(comparator bias replays, THA characterization, driver capacitor sweeps), run by
scripts piped over ssh, none of them migrated, so all untracked, as designed.
The guard was never exercised and nothing escaped it. Unless the flows people
actually run are migrated, the 2026-10-09 measurement will measure the same
thing: B's effect on ordinary use is bounded by migration coverage, not by the
tracker. The migration candidate this points to is a generic tracked Spectre
run (a netlist or bench in a work directory, with its scorer), which covers
most of these campaigns.

**Done the same day** (tsmc65 `e5014ce7`, `b80f79d6`): **Spectre campaigns run
tracked** (`deployment/bnl/tracked_campaign.py`, profile `.tracker-local/campaign/`):
one start runs a run list's benches, at most 4 at a time per host, with the
scorer's deck step when it generates the benches. A live canary (a copy of
`tha_char`'s buffer AC campaign, two benches) passed 2/2, tracker-verified, with
every numeric result identical to the campaign's own. The guard cannot recognise a
campaign started from an ad-hoc script, so the 2026-10-09 measurement should count
tracked campaign starts against the analog EDA launches that stayed untracked:
the coverage, not the refusals, is the number to watch.

The opaque share rose because these campaigns build scripts by copying and
editing earlier ones (`cp` then `sed`), or with `printf`, so their content is
never in the transcript; they cannot be classified without running them.
Launches are therefore undercounted in the interim, detached ones most (a
`nohup` inside a generated script is invisible).

**On 2026-10-09**, per repository (add any further development sessions to the
exclusions):

```bash
X="--exclude-session 498c3173-79ac-4044-97e6-4e763604ae04 --exclude-session e0fcea60-308c-4a9b-a6ee-2c29d7205688"
for r in tsmc65 tsmc28 xt011 sky130; do
  python integrations/cluster_jobs/acceptance/baseline.py --repo $r --since 2026-09-25 --until 2026-10-09 $X \
    --hook C:/dev/spec2si-$r/deployment/bnl/tracker_hook.py --json after-$r.json
done
```

Compare with condition A per repository and per day; report the migrated flows
separately (tracked share expected to approach 1, escapes 0), and say how much of
the ordinary work the migrated flows cover.

## The after measurement: result (2026-09-25 to 2026-10-08)

Run on 2026-10-09/10 on SUPERTJHOK-ROG with the commands above, and on
2026-10-10 on LIO-180105 with the same script, the same hook (tsmc65
`e5014ce7`) and the same exclusions (draft, awaiting the owner's review). The
session listings found **no further development sessions** on either machine.
Besides the two already excluded, every session active in the window was
engineering: on SUPERTJHOK-ROG the AFE trim DAC, comparator preamplifier, ADC
driver capacitor, POR design, DSP model fixes and the AFE fast path; on
LIO-180105 the v2 DSP design, v3 ASIC planning, the v3 readout channel RTL, and
one session whose first prompt only updates the repositories. Counts are in
`C:/dev/.spec2si-job-state/after-20261009/after-<repo>.json` (SUPERTJHOK-ROG)
and the paper repository (Agent_Compiled_AMS_Paper), LIO-180105 data folder, `after-tsmc65.json`.

**tsmc28, xt011 and sky130 had no ordinary sessions in the window** on
SUPERTJHOK-ROG (no transcript at all after the exclusions), and the archived
transcript metrics show none on LIO-180105. They are not measured; their zeros
are absence, not behaviour. Everything below is tsmc65.

**Condition A covers one machine.** It was measured on SUPERTJHOK-ROG only.
LIO-180105 also had five tsmc65 sessions in A's window (about 110 prompts by the
archived transcript metrics) that A does not count. Per-day comparisons with A
therefore use SUPERTJHOK-ROG alone. The after window's own counts (tracked
starts, escapes, coverage) and the per-launch ratios use both machines.

The after window is 14 days, with tool activity on all 14; condition A is 25
days. Per-day figures are the total divided by those. "Launches" are untracked
EDA launches, other compute runs and tracked starts.

| Measure (tsmc65) | Condition A, 25 days | After, ROG | After, LIO | After, both, 14 days | A per day | ROG after per day |
|---|---:|---:|---:|---:|---:|---:|
| Sessions (with a launch) | 24 (13) | 9 (5) | 5 (2) | 14 (7) | 0.96 | 0.64 |
| User prompts | 1,051 | 403 | 183 | 586 | 42 | 29 |
| Tool calls / cluster calls | 29,482 / 8,041 | 15,539 / 3,876 | 6,541 / 1,154 | 22,080 / 5,030 | 1,179 / 322 | 1,110 / 277 |
| **EDA launches, untracked** (detached) | **375** (44, 12 %) | 143 (4) | 2 (0) | **145** (4, 3 %) | 15.0 | 10.2 |
| Other cluster compute runs (detached) | 607 (36, 6 %) | 64 (6) | 90 (83) | 154 (89, 58 %) | 24.3 | 4.6 |
| Untracked launches that were detached | 80 / 982, 8 % | 10 / 207 | 83 / 92 | **93 / 299, 31 %** | | |
| Cluster reads | 5,197 | 1,158 | 413 | 1,571 | 208 | 83 |
| Reads per untracked launch | 5.3 | 5.6 | 4.5 | 5.3 | | |
| Reads per launch, tracked starts included | 5.3 | 3.5 | 2.6 | **3.2** | | |
| Transfers | 1,630 | 838 | 178 | 1,016 | 65 | 60 |
| Hours in cluster calls (calls over 10 min) | 45.5 (13) | 64.2 (19) | 24.9 (20) | 89.1 (39) | 1.8 | 4.6 |
| … per launch | 2.8 min | 11.7 min | 9.5 min | **11.0 min** | | |
| … of which in tracked `collect` | — | 36.1 (1) | not split | ≥ 36.1 | — | 2.6 |
| `sleep` hours inside commands | 12.2 | 9.4 | 2.0 | 11.4 | 0.49 | 0.67 |
| Wait-tool calls | 51 | 110 | 85 | 195 | 2.0 | 7.9 |
| Kills (per 100 launches) | 61 (6.2) | 22 (6.7) | 13 (8.3) | 35 (7.2) | 2.4 | 1.6 |
| Exact repeated EDA launches | 6 | 0 | 0 | 0 | | |
| Opaque scripts, share of cluster calls | 171, 2 % | 1,212, 31 % | 326, 28 % | 1,538, 31 % | | |
| **Migrated flows** | | | | | | |
| Tracked starts | 0 | 122 | 65 | **187** | 0 | 8.7 |
| Tracked collects | 0 | 345 | 94 | 439 | | |
| Refused by the hook | — | 0 | 0 | 0 | | |
| Untracked launches of a migrated flow that ran | — | 0 | 0 | **0** | | |
| Tracked share (starts / starts + escapes) | — | 1.0 | 1.0 | **1.0** (187 / 187) | | |
| Tracked starts, share of all launches | 0 | 37 % | 41 % | **38 %** (187 / 486) | | |

The SUPERTJHOK-ROG count of 9 sessions includes one that only received a task
notification in the window; 8 did work, 5 launched on the cluster. On
LIO-180105 the listing shows four session ids and the script counts five; the
difference is not resolved. The script's `days_active` counts session start
days, not active days. The tracked class (571 calls) is smaller than starts +
collects (626) because the class is first-match and some tracked calls sit
inside compound commands classified otherwise.

**Did the migrated flows get tracked?** Yes, completely, on both machines, as
far as the guard can see: 187 tracked starts (122 + 65), 0 untracked launches of
a migrated executable, 0 refusals (none was needed), and no start returned an
error to the session. On SUPERTJHOK-ROG all 122 starts were **Spectre
campaigns** (`.tracker-local/campaign/`, added 2026-09-26, host choice
2026-10-01), and the digital smoketest synthesis, the flow migrated at
activation, was not run once. LIO-180105's 65 starts are not split by route in
its counts; one of its sessions was RTL work, so some may be the smoketest
synthesis (`--sample tracked:N` on that machine would tell). The guard
recognises a campaign only by its payload (`tracked_campaign.py`), not an ad-hoc
campaign script, so "0 escapes" means no one ran the payload outside the
tracker, not that every campaign-like run was tracked.

**How much of the ordinary work do they cover?** About two fifths by launch
call: 187 tracked starts of 486 launches (38 %; 37 % on SUPERTJHOK-ROG, 41 % on
LIO-180105). On SUPERTJHOK-ROG, where the tracked starts are known to be
licensed Spectre campaigns, they are 46 % of licensed-tool launches (122 / 265).
A campaign start runs several benches, so per bench the share is higher; the
opaque share pushes the other way (untracked launches inside generated scripts
are not counted). By session it is uneven. Of SUPERTJHOK-ROG's 5 launching
sessions, two ran almost entirely tracked (64 starts against 3 untracked
launches; 43 against 6), one mixed (15 starts, 112 untracked), and two did not
use the campaign route at all (the ADC driver capacitor session, 85 untracked
launches, ending 2026-09-30; one with a single compute run). There, the
untracked remainder is mostly foreground Spectre and Python runs through the
tool wrapper. **LIO-180105 has a different shape**: almost no untracked licensed
launches (2), but 90 other compute runs, 83 of them detached. That is
unmigrated Python work run in the background, the pattern the readout episode
showed; it is the largest untracked class in the window and no route covers it.

**Did waiting and monitoring change?** Monitoring per launch fell once tracked
starts are counted (3.2 reads per launch against 5.3; 3.5 and 2.6 by machine),
and on SUPERTJHOK-ROG reads per day fell from 208 to 83. Detached untracked
licensed launches fell from 12 % to 3 %. Detached runs overall did not: 31 % of
untracked launches were detached (93 of 299, almost all LIO-180105's compute),
against 8 % in A, so jobs whose only record is the conversation persist, moved
from licensed tools to unmigrated compute. Kills fell per day on SUPERTJHOK-ROG
(2.4 to 1.6) but not per launch (6.2 per 100 in A, 6.7 after; 7.2 with
LIO-180105). Waiting did **not** shrink: the session sat in cluster calls 11.0
min per launch against 2.8 (on SUPERTJHOK-ROG 4.6 h a day against 1.8), and
36.1 of SUPERTJHOK-ROG's 64.2 h were tracked `collect --wait` calls (bounded at
nine minutes, so only one ran over ten; LIO-180105's 20 calls over ten minutes
are therefore not collects). The tracker moved waiting from `sleep` loops and log
tails into collects, which are cheaper per call and end in a normalized report,
but the conversation still blocks on them. Literal `sleep` (0.67 h a day on
SUPERTJHOK-ROG) and wait-tool calls (7.9 a day) did not fall either.

**Caveats.**
- One repository, 14 days, 7 launching sessions on two machines, three of which
  dominate; the campaign route existed from day 2 of the window and changed
  (host choice) on day 7. This is a description of this window, not an effect
  size.
- Condition A covers SUPERTJHOK-ROG only (above). Re-running A on LIO-180105's
  surviving or archived transcripts would make both windows two-machine.
- The classes are the same heuristics as condition A; precision was checked by
  hand there, not re-checked here.
- **Opaque share 31 %** (1,538 of 5,030 cluster calls, against 2 % in A): the
  campaigns' scripts are built by copying and editing earlier ones, so their
  content is not in the transcript. Untracked launches, detached ones most, are
  undercounted, and coverage is overstated by an unknown amount.
- Migrated routes in the window: tsmc65 the digital smoketest synthesis (from
  2026-09-24) and Spectre campaigns (from 2026-09-26); tsmc28 the ADC bench and
  bandgap DC point, xt011 the buffer characterization, sky130 the OTA schematic
  regression (all from 2026-09-24, none exercised). The generic tracked Spectre
  run planned for tsmc65 is the campaign route.

## The B-versus-C comparison on frozen tasks

Condition B now exists: chat with the tracker, guides and hooks. Condition C
(a bounded worker, study §9.2) does not yet. The comparison is defined here so
that §9.2 is built to be measured.

**Scope.** Only implementation rounds with a contract (§4.11). Exploration stays
chat in every condition; comparing a worker with chat on open design questions
would measure the wrong thing.

**Candidate tasks** (12–20; each has an independent oracle):

| # | Task | Oracle | Expected outcome |
|---|---|---|---|
| 1 | tsmc65 smoketest synthesis | collect, 5 checks | pass |
| 2 | tsmc28 bandgap DC, per-temperature report | collect, 9 checks | pass |
| 3 | sky130 OTA schematic regression | collect, 7 checks | pass |
| 4 | xt011 BUFTLLVTX1 characterization | collect, 10 checks | **fail, with a failure report** naming the large-load model |
| 5 | xt011 BUFTLLVTX2 and X8 (not yet run) | collect | new result |
| 6 | tsmc28 ADC normal mode (about 2 h of Spectre) | collect, 2 checks | pass; tests continuation across a long wait |
| 7 | Any of 1–3 with the connection dropped after dispatch | one job on the cluster | reattach (§6.3) |
| 8 | Any of 1–3 with license exhaustion injected | collect | "unchecked", never pass or fail |
| 9 | A packaged file changed after deployment | start refuses by name | redeploy, then one job |
| 10 | Wrong top or stale artifact injected | collect | named failure report |
| 11 | Re-vendor a flowkit change into four consumers and redeploy | `sync.py --check-all`, tests, deploy canaries | done, all consistent |
| 12 | Regenerate and gate the docs across repos | `docs/gen.py check` | pass |
| 13 | A known-class DRC repair on a fixture (study §9.3) | fresh DRC, protected deck | pass or refusal |
| 14 | PEX of a signed tsmc65 block | extraction evidence | pass |

**Protocol.** Three repeats per task and condition, fixed model and harness
versions, the same briefing and budgets, randomized order. Record: accepted
tasks over all attempts; human active minutes and interventions; elapsed time to
reviewed acceptance; licensed submissions; false acceptances; duplicate
submissions; and **failure-report completeness** for every non-pass. Score it
against §4.11's contents: contract and failed checks, evidence, attempts and
budget, cause class, and the question for exploration.

**Decision.** Adopt C for a task family only where it beats B enough to pay for
its upkeep (study §8.3). If B captures most of the benefit, stop at B.

## B versus C on the local frozen tasks (2026-09-26, first measurement)

The first measurement, on the six **local** tasks of the frozen set
(`worker/contracts/frozen/`): each is a blind replay of a fix merged during
§9.2 (`8b8305b`, `2c9d45c`, `7d71074`, `149ea4f`, `acbb85d`, `1e51c95`),
three with Claude Code and three with Codex, judged by the fix's own
acceptance test plus a regression suite. **C** is the worker (up to 3 rounds,
schema, scope enforced, the controller's gates between rounds); **B** is one
ordinary headless session in the same blind workspace, briefed with the same
goal, paths and gate commands, ending with a `STATUS:` line, then judged by
the same gates (scope measured, protected files restored before judging).
Budget for both: $6 and 45 minutes. Three repeats per task and condition in a
seeded random order (`worker/compare.py`, seed 20260926), three at a time;
no licensed runs.

| Task | Harness | Cond. | Accepted | False acceptance | Scope excursions | Claimed done / blocked | Rounds | Minutes (mean) | Cost $ (mean) | Tokens (mean) |
|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|
| f1-guard-order | claude | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0.5 | 0.27 | - |
| f1-guard-order | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0.5 | 0.25 | - |
| f2-parameters-file | codex | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 2.5 | 0.00 | 291k |
| f2-parameters-file | codex | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 2.9 | 0.00 | 378k |
| f3-mangled-parameters | claude | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 1.7 | 0.24 | - |
| f3-mangled-parameters | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 1.7 | 0.24 | - |
| f4-unpackaged-tests | codex | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 2.4 | 0.00 | 266k |
| f4-unpackaged-tests | codex | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 2.0 | 0.00 | 252k |
| f5-codex-tokens | claude | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 1.6 | 0.34 | - |
| f5-codex-tokens | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 1.5 | 0.33 | - |
| f6-report-note | codex | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 2.7 | 0.00 | 390k |
| f6-report-note | codex | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 2.8 | 0.00 | 453k |

| Condition | Accepted | False acceptances | Scope excursions | Minutes (total) | Claude $ (total) |
|---|---:|---:|---:|---:|---:|
| B | 18/18 | 0 | 0 | 34 | 2.53 |
| C | 18/18 | 0 | 0 | 34 | 2.44 |

**Reading.** On small, decided tasks with a precise acceptance test, **B and C
are indistinguishable**: every run of both passed on its first attempt (C
never needed a second round), neither touched anything outside its paths or
claimed done falsely, and time, dollars and tokens are within noise. Patch
sizes match too (B's Codex-token patches ran 20–25 changed lines against C's
11–18; everything else within a line or two). There were no non-passes, so
failure-report completeness was not exercised. By the study's §8.3 rule, **for
this task family B captures the benefit** and C's controller buys nothing
measurable. C's machinery earns its keep, if anywhere, where the local set
cannot reach: tracked gates whose licensed runs need bounding, diagnoses that
need evidence between rounds, and tasks that should end in a stop.

**Caveats.**
- Easy tasks: each was already solved once by a worker in one round.
- Codex updated itself mid-campaign (6 runs on 0.155, 12 on 0.158; spread
  over both conditions). `WORKER_CODEX_EXE` now pins a build.
- One run was invalid and re-run: the base's regression suite failed before
  the B session started (`test_two_processes_share_one_reservation`, a
  two-process race test, flaked under the campaign's parallel load).
- The replay audit first flagged one path in every f5 run of both conditions:
  a path quoted in a file the worker read (the controller's docstring), not a
  path it visited. The audit now reads tool inputs and commands only; all
  transcripts re-audit clean.

## B versus C on the tracked frozen tasks (2026-09-26)

The owner approved a small licensed set (about 12–16 short runs). Two tasks,
both with Claude Code, so B's session could reach the cluster (Codex's sandbox
has no network):

- **f7-ota-fault**: sky130 OTA with an injected fault (XM5's gate on vdd), a
  diagnosis through the tracked run; three repeats per condition.
- **f8-buffer-stop**: xt011's buffer linearity, where the right answer is a
  **stop** with a design question; one repeat per condition.

Both conditions start from the **recorded** baseline run (its failure report and
`native.json`, closing notes removed), so no baseline run is spent per repeat.
C's gates are the controller's tracked runs (at most 2 per run). B gets a profile
of the base, its own task store and the tracker commands, may launch up to 2
runs itself, and one more controller run judges its final state (skipped when it
changed nothing). Seeded order, two at a time. **10 licensed runs in all.**

| Task | Harness | Cond. | Correct | False acceptance | Scope excursions | Claimed done / blocked | Rounds | Licensed runs (total) | Minutes (mean) | Cost $ (mean) | Tokens (mean) |
|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| f7-ota-fault | claude | B | 3/3 | 0 | 0 | 2 / 1 | 1.0 | 5 | 1.6 | 0.41 | - |
| f7-ota-fault | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 3 | 0.9 | 0.29 | - |
| f8-buffer-stop | claude | B | 1/1 | 0 | 0 | 0 / 1 | 1.0 | 2 | 5.2 | 3.11 | - |
| f8-buffer-stop | claude | C | 1/1 | 0 | 0 | 0 / 1 | 1.0 | 0 | 0.7 | 1.30 | - |

| Condition | Correct | False acceptances | Scope excursions | Licensed runs | Minutes (total) | Claude $ (total) |
|---|---:|---:|---:|---:|---:|---:|
| B | 4/4 | 0 | 0 | 7 | 10 | 4.32 |
| C | 4/4 | 0 | 0 | 3 | 4 | 2.18 |

B's 7 licensed runs are 3 of its own and 4 judging runs; a chat session in
ordinary use would not have the judging run, so **own launches are 3 for B and 3
for C**.

**Reading.**
- **Correctness ties**: both fixed the injected fault in every repeat, and both
  stopped on the buffer task with the right design question. No false
  acceptance and no scope excursion in either.
- **C was cheaper and quicker to a trusted answer**: 4 minutes and $2.18 in all,
  against 10 minutes and $4.32 for B (6 minutes without the judging runs).
- **B's self-checking is fragile headless.** In one f7 repeat B's commands
  drifted (an early `cd`, then absolute paths), no longer matched its shell
  allowance, and every packaging attempt needed approval a headless session
  cannot get. It said so honestly and ended `STATUS: blocked`; its fix was
  right, but only the judging run showed it. C never depends on the model's
  permissions to run a gate.
- **On the stop task the handoff differs.** C stopped in one round without a
  licensed run and wrote a failure report with every §4.11 part: contract and
  failed checks, evidence (the fetched numbers), attempts and budget, a cause,
  observations kept apart from hypotheses, and a three-option question. B
  reached the same conclusion in prose (the numbers, three options, the job key
  of its run), but spent $3.11, left 56 lines of uncommitted instrumentation in
  the bench and scorer, and ended with its own cluster job still running and
  uncollected (collected afterwards: fail, as expected). Its answer has no
  cause class and no attempt record.

**Decision (§8.3), provisional on a small sample** (4 runs per condition):
use **B** for small, decided local tasks (it ties C at no upkeep), and **C**
where tracked gates, licensed runs, diagnoses or expected stops are involved:
there it matched B's correctness with less time, money and loose ends, and
its stop is the structured handoff that opens the next exploration round.

## B versus C on the widened set: 19 tasks, 118 runs (2026-09-26)

The owner asked for a bigger sample. The frozen set grew to **19 tasks**, inside
the study's 12–20: **13 local** (six from §9.2 plus seven mined from the repos'
histories and vetted: the fix's test fails at its parent, passes with the fix,
runs on Windows) and **6 tracked** (the sky130 injected fault and the xt011
stop task with more repeats, plus four from §8.2's injection list: a wrong top
and a truncated report injected in sky130, the tsmc28 bandgap tempco replay,
and the tsmc65 SDC fault). Three repeats per task and condition (f7 five, f8
three), seeded order, Claude on every tracked task, Codex pinned to one build.
Tracked tasks start from recorded baselines; B's own run on its exact final
source now stands as its judgement when it collected one.

**The seven new local tasks:**

| Task | Harness | Cond. | Correct | False acceptance | Scope excursions | Claimed done / blocked | Rounds | Licensed runs (total) | Minutes (mean) | Cost $ (mean) | Tokens (mean) |
|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| f10-card-profiles | claude | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 0.7 | 0.31 | - |
| f10-card-profiles | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 0.5 | 0.29 | - |
| f11-stats-report | codex | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 1.5 | 0.00 | 261k |
| f11-stats-report | codex | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 1.5 | 0.00 | 248k |
| f12-abstract-purposes | claude | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 0.6 | 0.26 | - |
| f12-abstract-purposes | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 0.7 | 0.26 | - |
| f13-macro-gds-shared | codex | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 1.2 | 0.00 | 245k |
| f13-macro-gds-shared | codex | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 1.3 | 0.00 | 278k |
| f14-rail-stub | claude | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 0.9 | 0.34 | - |
| f14-rail-stub | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 0.9 | 0.33 | - |
| f15-wide-claim | codex | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 1.6 | 0.00 | 326k |
| f15-wide-claim | codex | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 1.7 | 0.00 | 344k |
| f9-license-lumerical | codex | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 2.4 | 0.00 | 258k |
| f9-license-lumerical | codex | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 0 | 2.6 | 0.00 | 272k |

**The tracked widening** (owner-approved, about 40 licensed runs; used 35 in the
campaign plus 2 recorded baselines and 1 refusal canary):

| Task | Harness | Cond. | Correct | False acceptance | Scope excursions | Claimed done / blocked | Rounds | Licensed runs (total) | Minutes (mean) | Cost $ (mean) | Tokens (mean) |
|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| f16-ota-wrong-top | claude | B | 3/3 | 0 | 0 | 2 / 1 | 1.0 | 3 | 1.4 | 0.48 | - |
| f16-ota-wrong-top | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 3 | 1.0 | 0.31 | - |
| f17-ota-truncated-report | claude | B | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 3 | 1.3 | 0.50 | - |
| f17-ota-truncated-report | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 3 | 1.0 | 0.37 | - |
| f18-bandgap-tempco | claude | B | 3/3 | 0 | 0 | 1 / 0 | 1.0 | 5 | 3.2 | 0.79 | - |
| f18-bandgap-tempco | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 3 | 1.7 | 0.44 | - |
| f19-sdc-fault | claude | B | 3/3 | 0 | 0 | 1 / 0 | 1.0 | 5 | 5.1 | 1.05 | - |
| f19-sdc-fault | claude | C | 3/3 | 0 | 0 | 3 / 0 | 1.0 | 3 | 4.4 | 0.41 | - |
| f7-ota-fault | claude | B | 2/2 | 0 | 0 | 2 / 0 | 1.0 | 2 | 1.2 | 0.41 | - |
| f7-ota-fault | claude | C | 2/2 | 0 | 0 | 2 / 0 | 1.0 | 2 | 0.8 | 0.27 | - |
| f8-buffer-stop | claude | B | 2/2 | 0 | 0 | 0 / 2 | 1.0 | 3 | 4.9 | 3.47 | - |
| f8-buffer-stop | claude | C | 2/2 | 0 | 0 | 0 / 2 | 1.0 | 0 | 0.8 | 1.34 | - |

**All four campaigns together:**

| | Runs | Correct | False acceptances | Scope excursions | Licensed runs | Median minutes | Total minutes | Claude $ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Local, B | 39 | 39 | 0 | 0 | 0 | 1.61 | 61 | 5.27 |
| Local, C | 39 | 39 | 0 | 0 | 0 | 1.55 | 62 | 5.07 |
| Tracked, B | 20 | 20 | 0 | 0 | 28 (18 own + 10 judging) | 1.59 | 55 | 20.52 |
| Tracked, C | 20 | 20 | 0 | 0 | 17 | 1.02 | 31 | 10.00 |

**Reading.**
- **Correctness does not separate them.** Every run of both conditions reached
  the right outcome, including every injected fault and every expected stop.
- **Local, decided tasks: a tie** on time, cost and patch size, now over 13
  tasks in three repos and both harnesses.
- **Tracked tasks: C costs less.** 44 % less wall time, half the model cost.
  Licence use is equal on what each launched (C 17, B 18). B's 10 judging runs
  are what it takes to trust B's result when B did not verify it itself.
- **B leaves loose ends; C leaves none.** In 20 tracked B runs:
  - 4 ended waiting on a background timer, their own run never collected and no
    `STATUS` line: 4 of the 6 f18 and f19 repeats. The runs had passed; B did
    not wait to find out. This is the one-shot trap the SessionStart guidance
    already warns about, and it appears as soon as a job takes a minute or more.
  - 1 could not check its own work: its commands drifted out of its shell
    allowance (f7).
  - On the stop task, all 3 B repeats left 45–89 lines of uncommitted
    instrumentation in the bench and scorer and launched a run; all 3 C repeats
    stopped in under a minute with no run and a full failure report.
  - 5 of B's own cluster jobs were left uncollected at session end (collected
    afterwards: 4 passes, 1 expected fail).

**Condition D** (a deterministic script "where applicable", §8.1). The 13 local
tasks are code changes: a deterministic check (the acceptance test) already
*detects* each, but writing the fix needs reasoning, so D does not apply. Of the
tracked faults, a deterministic check names the cause in four: Spectre's
undefined-subcircuit error (f16), the adapter's "missing or nonfinite metric"
(f17), the `tempco-reported` check (f18) and the preflight's uncertainty count
(f19). Only f18's reached the failure report, as its failed check; the others
stayed in logs. That gap is now closed for adapter refusals (flowkit `7ec12e8`,
tsmc28 `93cfbbc`: "Refused by the adapter …"). For f7 (a mis-wired gate) no
deterministic check exists, and f8 needs one that is not built (the output swing).
D would have **detected** but not **fixed** any of them, so it is not a
replacement for B or C; it is the better failure report both of them read.

**Against §8.2's suggested advancement criteria:**
- zero observed false acceptances: **met** by both;
- all critical injected faults detected: **met** (4 injected tasks, 28 runs);
- no duplicate submission after restart: **none observed** (not exercised by
  deliberate restarts in this campaign; §6.3 was tested live on 2026-09-24);
- complete provenance for every accepted result: **met** for tracked gates
  (tracker-verified); local gates are the recorded test runs;
- at least 20 % lower median human active time or avoidable licensed submissions
  than B: human time is 0 by construction (both ran headless), so it was not
  measured; on the tracked tasks C's median time is **36 % lower** and its total
  licensed runs **39 % lower** once B's results are independently judged
  (equal on own launches).

**Decision (§8.3), on 118 runs:** keep **B** (chat with the tracker, guides and
hooks) for small, decided local tasks: it ties C and needs no contract.
Use **C** (the bounded worker) for tasks with tracked gates, diagnoses or a
likely stop: the same correctness at about half the time and model cost, no
unattended loose ends, and a structured failure report when it stops. The
remaining B weakness has a cheap B-side fix worth trying first for chat use: a
headless or one-shot session must collect in the foreground before ending
(the guidance says so; the hooks could enforce it).

**Caveats.** Headless runs only, so the study's main expected advantage (human
active minutes) is unmeasured; one owner's repositories and tasks; all tracked
tasks with Claude; tracked tasks are short (seconds to 3 minutes), so long
queues and overnight jobs, where persistence should matter most, are not in
the sample.

## Closing B's one-shot trap (2026-09-26, after the comparison)

The comparison's main B weakness was fixed on B's side (flowkit `2153013`,
`00118e2`; re-vendored and redeployed in all four consumers):

- `jobs.workflow collect --wait SECONDS` waits in the foreground, polling inside
  Python (the harness blocks a foreground `sleep`, which is why B had used a
  background timer), bounded at 540 s per call.
- A Claude **Stop hook** refuses to end a session holding a tracked job it
  started and never collected, and says how to wait; it lets go when the last
  message hands each open job off by task key, after three refusals, or for
  receipts older than the rule. Not yet for Codex (a new handler needs `/hooks`
  trust again).

**Re-run of the two tasks where the trap showed** (f18, f19; B only, three
repeats each; today's tooling laid over the frozen bases with `replay.tooling`):

| | Runs | Correct | Ended without collecting | Licensed runs | Minutes (mean) | Claude $ (mean) |
|---|---:|---:|---:|---:|---:|---:|
| B before (f18+f19) | 6 | 6 | 4 | 10 | 4.2 | 0.92 |
| B after (f18+f19) | 6 | 6 | **0** | **6** | 3.7 | 0.54 |
| C (f18+f19) | 6 | 6 | 0 | 6 | 3.0 | 0.42 |

Every B session used `collect --wait 540` from the SessionStart guidance, ended
`STATUS: done`, and its own tracker verdict was reused as the judgement, so B
now spends one licensed run per repeat, as C does. The Stop hook did not need to
fire in these runs; two live canaries showed it does: a session told to start a
job and end with "OK" was refused ("Stop hook feedback: This session started
tracked job(s) it has not collected …") and then handed the job off by task key,
which the hook accepted. On these tasks the remaining C advantage is modest
(about a fifth less time and model cost); the stop task's structured failure
report and clean stop are unchanged.

## Re-running this page

```bash
python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-08-30 --until 2026-09-24 --json out.json
python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-08-30 --until 2026-09-24 --sample eda-detached:12
```

The second form prints sampled commands with the reason for each class, to the
terminal only, for checking the classifier. Never paste them into a document.
