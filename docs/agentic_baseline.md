<!--docmeta
title: Agentic workflow baseline — how chat sessions use the cluster (condition A)
genre: study
status: active
area: top
owner: soumyajit
updated: 2026-09-24
summary: The §8 condition-A baseline of the agentic workflow study, measured from surviving session transcripts before the tracker was activated. tsmc65, 24 sessions over 25 days, 0 tracked: 375 licensed-tool launches, 607 other cluster compute runs, 5,197 cluster reads, 12.2 hours of in-command sleep, 61 kills. None of the 965 harvested attempts in four repos has a declared failure cause. Also the re-measurement plan and the B-versus-C design on frozen implementation tasks.
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

## Re-running this page

```bash
python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-08-30 --until 2026-09-24 --json out.json
python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-08-30 --until 2026-09-24 --sample eda-detached:12
```

The second form prints sampled commands with the reason for each class, to the
terminal only, for checking the classifier. Never paste them into a document.
