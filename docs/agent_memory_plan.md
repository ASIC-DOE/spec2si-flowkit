<!--docmeta
title: Agent memory — measured lessons, verified restore state, and executable gates
genre: plan
status: active
area: top
owner: soumyajit
updated: 2026-09-02
summary: Feasibility, architecture and staged evaluation plan for reducing repeated agent errors across the five spec2si repositories without creating a monolithic stale prompt. Phase 0 uses a small auditable cross-repo sample and evidence packets to minimize human labeling.
-->

# Agent memory

## Decision

Proceed, but do not make a long narrative document the memory mechanism.

> **Long-term memory is a versioned lesson registry plus executable gates.**
> **Short-term memory is a small, verified restore manifest.** A run receives
> a generated, task-specific briefing with the hashes of everything it was
> shown; it does not ingest the documentation corpus.

This is feasible because the repositories already hold the hard parts:

- `policy/flow_policy.core.json` is a vendored, hash-gated set of portable
  lessons and local `flow_policy.json` files record whether each one is
  enforced;
- `docmeta.core.json` separates evergreen studies/findings/decisions from a
  `log`, which is explicitly allowed to be stale and must not become a source
  of durable truth;
- `browse/runlog.py` harvests one metadata-only record per agent attempt and
  has a closed terminal-cause enumeration;
- tsmc65's `analog/regress/` already demonstrates a hash-pinned task, fixed
  feedback schema, mechanical scoring, and fail-closed reset-state checking.

The missing parts are promotion, selection, delivery, and measurement.

## Evidence and constraints

Measured 2026-09-01/02:

| port | enforced core rules | partial | not implemented |
|---|---:|---:|---:|
| tsmc65 | 13 | 7 | 2 |
| xt011 | 3 | 7 | 12 |
| sky130 | 2 | 1 | 19 |
| tsmc28 | 1 | 0 | 21 |

The three ports with runlogs hold **356 attempts over 2,255 scanned turns**,
and all 356 attempts are unclassified. That is useful instrumentation but not
yet evidence about which errors recur.

The five repositories contain more than 10 MB of Markdown. Several supposed
entry points have themselves become corpora: XT011's `CLAUDE.md` plus
`RESUME.md` exceed 500 KB, and tsmc28's restore archive exceeds 700 KB. Full
pre-run ingestion would spend context on superseded and unrelated facts and
make the most important constraints less salient.

Restore points also record their own failure mode. The current tsmc28 handoff
describes repeated wrong branch, remote and PR facts, including eight hours
lost when a second machine trusted a handoff stranded on another branch.
Volatile facts must therefore be measured at startup, not copied into prose.

There is no cross-agent startup contract today. Claude session-end hooks
harvest some ports, but a startup briefing is not generated, injected, or
attested across harnesses. Delivery must be a launcher responsibility rather
than an instruction asking the agent to remember to read another instruction.

## The memory model

### 1. Canonical lesson records

Phase 1 adds a public, node-agnostic `knowledge/knowledge.core.json` here and
one node-private `knowledge/knowledge.local.json` per process port. The core is
vendored and hash-gated by `sync.py`, following the policy contract.

Each lesson has:

- stable id and lifecycle (`active`, `superseded`, `investigating`);
- scope: repositories, process, domain, stage, tools and path globs;
- an activation trigger and an imperative required action;
- a trusted verification command or evidence predicate;
- a negative control when the failure class permits one;
- links to the finding, study, decision or commit that earned the lesson;
- validity conditions such as deck hash, tool version, process option or
  input-artifact hash;
- enforcement level: information, preflight, test, or signoff gate;
- disclosure class: public method or node-private fact.

Records reference evidence; they do not copy the evidence's prose or process
values into the public core.

### 2. A bounded briefing compiler

A stdlib-only compiler selects lessons from the repository, requested task,
likely paths, flow stage, tools and active restore state. Its output is capped
initially at 1,500--3,000 tokens and carries:

- source and lesson ids with hashes;
- generator version;
- repository HEAD and dirty-state summary;
- restore-manifest hash and freshness verdict;
- required preflight checks;
- the small set of applicable active lessons.

Selection starts deterministic: exact scope, path, tool and stage tags. Search
or embeddings are a later option only if measured recall demands them.

### 3. Verified delivery

One shared compiler gets thin harness adapters: a Claude session-start hook,
Codex repository instructions/launcher, and any other runner in use. The run's
provenance records the briefing manifest. Failure to produce a valid manifest
is visible as `memory unavailable`; it never silently becomes an ordinary run.

### 4. A small restore manifest

The current restore record contains only:

- goal and exact next action;
- expected commit plus dirty-state policy;
- live/queued jobs;
- artifact paths and hashes;
- blockers and recently disproved hypotheses;
- a reading order for deeper evidence.

Branch, remote, PR and job state are generated at startup. A commit mismatch
invalidates the restore record or produces a loud warning. Superseded restore
points remain logs; durable lessons are promoted separately.

### 5. Promotion, not accumulation

Every finding ends in one of four places:

1. mechanically preventable recurrence -> automated test or preflight gate;
2. recurring judgment-dependent hazard -> conditionally selected lesson;
3. one-off evidence -> finding or study, retrieved only in matching scope;
4. transient work state -> restore manifest.

A deterministic lesson that remains prompt text instead of becoming a gate is
unfinished work.

## Phase 0 — establish the baseline with little labeling

### Why not classify all 356

The existing runlog correctly rejects that approach: the backlog grows, the
full census would take hours, and the questions are proportions. A reproducible
sample estimates those proportions. Unreviewed attempts remain honestly
`unclassified`.

Start with **24 attempts**, stratified by repository, domain and tier. At the
worst-case 50% rate, an unstratified sample of 24 has a rough 95% margin near
+/-20 percentage points: enough to identify large failure classes and decide
where a second sample is useful, not enough for a final benchmark. Add 16 only
if the first sample leaves the leading causes or promotion candidates unclear.

### Minimize each review

`runlog/phase0.py` prepares an ignored local review packet containing:

- the selected attempt's existing metadata;
- adjacent attempts on the same cell and session;
- nearby commits ranked by file-basename overlap and time;
- candidate cause hints derived from explicit words in commit subjects;
- the exact existing `runlog.py classify` command.

Commit subjects are **review evidence, never labels**. The tool writes no
classification and does not read prompts, tool output, commands, rule values,
or transcript message bodies. Only the human makes the irreducible terminal
judgment. Review packets live under `runlog/work/`, which is gitignored because
the flowkit is public and the consumer commit subjects and design names are
not its material to publish.

Run from this repository under the environment's Python 3:

```bash
python3 runlog/phase0.py sample --n 24 --seed 20260902
```

Review `runlog/work/phase0_review.md`, classify the selected ids through each
consumer's existing append-only command, then run:

```bash
python3 runlog/phase0.py report --sample runlog/work/phase0_review.json
```

The report estimates cause proportions using each stratum's population/sample
weight, prints exact sample counts, and refuses to present the sample as a
census. If the top result is ambiguous, draw the planned second increment with
a different recorded seed rather than relabeling the first draw.

### Phase-0 outputs

- a reproducible sample manifest and compact review sheet;
- weighted and unweighted cause estimates with sample denominators;
- the top recurring documented-error classes;
- a promotion table: gate, conditional lesson, evidence only, or restore;
- estimated avoidable cluster attempts where the sample supports it.

### Complement: periodically mined text themes

The runlog measures attempts, but much of the existing knowledge predates it
or lives in Markdown findings, studies, decisions and logs. A second Phase-0
input therefore mines the five repositories' Markdown corpus with a
deterministic, stdlib-only text pipeline:

- strip code and Markdown mechanics, parse `docmeta`, normalize words and form
  one-to-three-word candidate phrases;
- rank by cross-repository support, document support, distinctiveness,
  durable-genre weight and actionability cues;
- report themes present in multiple repositories separately from local
  process/design themes;
- retain only paths/titles as evidence, never infer that a frequent theme is a
  correct lesson;
- assign stable theme ids and record corpus hashes and rank deltas.

`knowledge/themes.py refresh` is idempotent and updates the ignored local
snapshot only when the corpus or ranking parameters change. `check` exits
nonzero when Markdown has changed since the last refresh, so it can run in a
weekly local scheduler or a post-merge check. The combined private-corpus
output stays under `knowledge/work/`; only a reviewed, sanitized lesson may be
promoted into the public core.

```bash
python3 knowledge/themes.py refresh --top 30
python3 knowledge/themes.py check
```

The first deployment should refresh weekly and after a documentation campaign.
Measure runtime and rank churn before deciding whether every merge is useful;
an update cadence that creates mostly noise will train reviewers to ignore it.

## Later phases

### Phase 1 — memory MVP

Define and validate the lesson schema, implement deterministic selection and
briefing generation, add restore validation and one harness adapter, then seed
only 10--20 high-confidence lessons. Estimated software work: 5--8 engineering
days.

### Phase 2 — controlled pilot

Use tsmc65's frozen StrongARM regression to validate the infrastructure, then
add historical pre-fix tasks covering stale artifacts, silent passes, derived
geometry and tool/environment failures. Compare baseline, briefing, and
briefing-plus-gates at the same model, harness, commit, inputs, task and
feedback schema.

### Phase 3 — measured rollout

Pilot on tsmc65 and validate portability on tsmc28. Promote high-value lessons
to gates before growing the briefing. Cluster and signoff turnaround will
likely dominate the 2--4 week evaluation period.

## Go/no-go measures

Set these before the controlled pilot:

- at least 30% fewer previously documented repeat errors;
- at least 20% fewer unnecessary cluster/licensed-tool attempts;
- no increase in silent-pass escapes;
- zero unwarned stale restore manifests;
- briefing within its fixed token budget;
- every run records the lesson ids and hashes it received;
- low enough irrelevant-lesson rate that the briefing remains actionable.

The primary outcome is trusted closure with fewer repeated errors and fewer
expensive iterations, not whether the agent quotes the memory.
