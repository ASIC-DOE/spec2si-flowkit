<!--docmeta
title: Agentic workflow baseline — how chat sessions use the cluster (condition A)
genre: study
status: active
area: top
owner: soumyajit
updated: 2026-09-26
summary: The §8 condition-A baseline of the agentic workflow study, measured from surviving session transcripts before the tracker was activated. tsmc65, 24 sessions over 25 days, 0 tracked: 375 licensed-tool launches, 607 other cluster compute runs, 5,197 cluster reads, 12.2 hours of in-command sleep, 61 kills. None of the 965 harvested attempts in four repos has a declared failure cause. Also the re-measurement plan, the B-versus-C design on frozen implementation tasks, and its measurement (2026-09-26): B and C tie at 18/18 on six local tasks and 4/4 on two tracked ones, where C used less time, money and handling.
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

The tracker became the default in all four consumers on 2026-09-24. Re-run the
same script on ordinary sessions after two weeks:

```bash
python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-09-25 --until 2026-10-09
```

Compare, per repository: the tracked share of launches for the migrated flows
(expected to approach 1), detached untracked launches, reads per launch,
in-command sleep, kills and repeated launches. Only a handful of flows are
migrated, so most launches will stay untracked; report the migrated flows
separately. Exclude trial sessions as above.

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

## B versus C on the local frozen tasks (2026-09-26)

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

## Re-running this page

```bash
python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-08-30 --until 2026-09-24 --json out.json
python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-08-30 --until 2026-09-24 --sample eda-detached:12
```

The second form prints sampled commands with the reason for each class, to the
terminal only, for checking the classifier. Never paste them into a document.
