<!--docmeta
title: RESUME — the agentic-workflow plan, session handoff
genre: log
status: active
area: top
owner: soumyajit
updated: 2026-09-24
summary: Where the agentic workflow plan stands at the end of 2026-09-24 and what to do next. START HERE: the remaining §9.2 work -- seven more reviewed worker attempts (3 of 10 are done and merged), then the B-versus-C comparison on frozen tasks. Everything before it is built and accepted: the tracker in all four consumers under Claude Code and Codex, duplicate-safe submission, failure reports, the condition-A baseline, and the bounded worker with tracked cluster gates and diagnosis contracts. Includes the commands, paths and traps a new session needs.
-->

# RESUME — the agentic-workflow plan

Last worked **2026-09-24**. Read in this order:

1. this file (where to start);
2. [the status survey](agentic_workflow_status.md) (what is built against each
   section of the report, with evidence);
3. [the study](agentic_workflow_report.md), when a decision needs its
   reasoning. §9 is the work plan.

## The state (re-measure it; do not trust this table)

```bash
for r in flowkit tsmc65 tsmc28 xt011 sky130; do cd /c/dev/spec2si-$r; git fetch -q; \
  echo "$r: $(git branch --show-current) $(git log -1 --format=%h) \
  ahead=$(git rev-list --count @{u}..HEAD) behind=$(git rev-list --count HEAD..@{u})"; done
python sync.py --check-all      # from flowkit; routekit / apiref / housekeeping drift is known and not ours
```

All five repos were pushed and in sync at the end of 2026-09-24
(flowkit `7719157`, tsmc65 `40bdc54c`, tsmc28 `db01d1f`, xt011 `305fb66`, sky130 `2c447b5`; branches: xt011 `cml-pin-escape`, sky130 `snn-readout`, the rest `main`).

| Repo | Tracked flow(s) | Live gates | Deployed profile |
|---|---|---|---|
| tsmc65 | digital smoketest synthesis (`dig_flows/run.py smoketest_flow`, about 3 min) | **accepted**: canaries, licensed pass 5/5, ten Claude requests (two incomplete), Codex canaries | snapshot-009 on asic8, `.tracker-local/` |
| tsmc28 | ADC normal mode (about 1 h 53 min); **bandgap DC** (about 1 min, the short task) | **accepted**: ADC licensed pass 2/2; bandgap passes the canaries, ten Claude requests (one incomplete) and ten Codex requests | bandgap snapshot-009, ADC snapshot-20260924-07, both on asic7 |
| xt011 | buffer characterization, one cell per task (X1: under a minute) | **accepted**: canaries, licensed run (engineering **fail**, as the native scorer), ten requests under Claude and under Codex, none incomplete | snapshot-20260924-05 on asic7 |
| sky130 | OTA schematic regression (seconds) | **accepted**: as xt011; engineering **pass 7/7** | snapshot-20260924-06 on asic7 (asic6 profiles archived under `.tracker-local/asic6/revisions/`) |

Codex hook trust is **persisted** in all four consumers, and a canary without
the bypass flag passes in each. Profiles were all redeployed after flowkit
`149ea4f`; from now on a change to a vendored test does not make them stale.

Evidence lives in each repo's guide: tsmc65 `docs/tracked_jobs.md`; tsmc28
`docs/howto/tracked_bandgap.md` and `docs/tracked_adc_migration.md`; xt011 and
sky130 `docs/tracked_jobs.md`.

## START HERE: the remaining §9.2 work

The bounded worker exists and works: **`worker/`** in flowkit. Read
[worker/README.md](../worker/README.md) first (design, contracts, tracked gates,
diagnosis, pilots). The study's §9.2 asks for **ten reviewed attempts** before
the paired comparison; **three are done, all merged**:

| # | Contract (`worker/contracts/`) | Kind | Harness | Gate | Result |
|---|---|---|---|---|---|
| 1 | `guard-argument-order` (flowkit) | maintenance | Claude | local | merged `8b8305b`; 1 round, $0.53 |
| 2 | `parameters-file` (flowkit) | maintenance | Codex | local | first run stopped (controller prompt fault, fixed `ac671a4`); rerun merged `2c9d45c` |
| 3 | `bandgap-tempco` (tsmc28) | **diagnosis** | Claude | **tracked cluster** | merged tsmc28 `2ef9455`; 1 round, $0.63, 2 licensed runs |

### 1. Seven more attempts (4 to 10)

**Coverage to reach by attempt 10:** at least two more Codex runs, at least two
more diagnoses, a tracked gate outside tsmc28 (xt011 or sky130), at least one
task that **should end in a stop**, and at least one **historical replay** (a
past fix re-done blind). Candidates, roughly in order:

| # | Candidate | Kind / harness | Gate | Expected |
|---|---|---|---|---|
| 4 | xt011 BUFTLLVTX1 linearity fail (its failure report is open, with the question already recorded). Editable: the scorer and bench only; protected: the 2 % criterion and the adapter | diagnosis / Codex | tracked (xt011 buffer, under a minute) | **stop**: the fix is a model or criterion decision, which is exploration. A worker that loosens the criterion is a scope violation |
| 5 | Replay a flowkit fix blind: base = the fix's parent plus the fix's own test (protected). Good ones: `7d71074` (a mangled `--parameters` is a named refusal), `e6d2f6d` (deploy validates the profile before uploading), `149ea4f` (tests are not packaged) | maintenance / Claude | local | pass; compare the patch with the real fix |
| 6 | The same, a second replay | maintenance / Codex | local | pass |
| 7 | sky130 OTA: a frozen injected fault on a scratch branch (for example a sign error in `score_schematic.py`'s transfer slope), diagnosed from the tracked run's failure report | diagnosis / Claude | tracked (sky130, seconds) | pass |
| 8 | tsmc65 smoketest with an injected fault (wrong top or a broken synthesis script), from its failure report | diagnosis / Codex | tracked (tsmc65, about 3 min) | pass |
| 9–10 | Real backlog items as they appear (the resume page's smaller items, open failure reports: `jobs.workflow failures`) | either | either | review |

**Per attempt:**

1. **Decide and write the oracle first** (the exploration half, done in chat):
   the acceptance test, or the adapter check a tracked gate will apply. Commit it
   to the target repo. It must fail on the base.
2. **Write the contract** in `worker/contracts/<id>.json` (copy a pilot's).
   The regression gates must pass on the base: exclude the acceptance test and
   any test that fails on Windows for other reasons
   (`jobs/test_isolated_bundle.py` is POSIX-only). Budget `licensed_jobs` at
   least one run per tracked gate, plus one for the baseline.
3. **Run it** from flowkit, in the background (it can take minutes):
   `py -3 -m worker.controller run --contract worker/contracts/<id>.json --state-dir C:/dev/.spec2si-job-state/worker`
4. **Review.** Read `review.md` and `change.patch` (or `failure.md`) in the run
   directory. Check the result independently where you can: for a tracked gate,
   read the passing job's `native.json` on the cluster.
5. **Merge or not.** In the target repo, `git merge --ff-only worker/<run id>`.
   `git branch --list` shows a worktree branch with a `+`; strip it. Then
   `git worktree remove --force <run dir>/worktree`, `git branch -D worker/<run id>`.
6. **Follow through.** A merged change to a packaged file makes that repo's
   deployed profile stale: redeploy it (no licence). Re-vendor if it is flowkit
   code. Update the guide in the same change.
7. **Record** the row in worker/README.md's pilot table and the count in
   [the status survey](agentic_workflow_status.md) (§9.2 row).

**Replays need history isolation.** A replay's worker must not see the fix.
The worktree shares the repository, and the Claude allowance includes
`git log` and `git show`, so the answer is one command away. Before attempt 5,
add a contract option that creates the workspace as a fresh clone of the base
only (for example `git clone --no-local --single-branch` of a scratch branch at
the base, with no other refs), and drop the git-read rules from the allowance
for those contracts. Codex's sandbox does not stop a local `git log --all`
either.

### 2. Then the B-versus-C comparison (study §8)

The design is in [the baseline](agentic_baseline.md) ("The B-versus-C
comparison on frozen tasks"): implementation rounds only, a frozen task set
(the pilots' contracts are the seed), three repeats per task and condition,
fixed harness versions and budgets.

- **C** is the worker on the contract.
- **B** is a headless chat session (`claude -p`, or `codex exec`) given the
  same goal, context and gate commands as an ordinary request, with the
  tracker, guides and hooks but no controller. Its outcome is judged by
  running the same gates afterwards.

Build a small runner that plays B from a contract, reusing
`integrations/cluster_jobs/acceptance/run_trials.py`. Score both with the same
metrics: accepted over attempts, human interventions, elapsed time, licensed
runs, cost, false acceptances, and failure-report completeness for every
non-pass.

## After §9.2

### 3. Re-measure B in ordinary use (from 2026-10-09)

Re-run `integrations/cluster_jobs/acceptance/baseline.py` on the two weeks after
activation and compare with condition A (the baseline's "after" section).

### 4. Report upkeep (status survey §3)

- Write a decisions-first summary.
- Move the dated tool and vendor tables to an appendix.
- Write `docs/agent_memory_plan.md`, or remove the study's references to it.

### Smaller items found along the way

- ~~Tempco missing from the bandgap JSON~~: diagnosed and fixed by worker pilot 3
  (tsmc28 `2ef9455`), checked by a new `tempco-reported` check.
- ~~Guard argument order~~: fixed by worker pilot 1 (`8b8305b`); tsmc65 now
  denies `run.py --phase syn smoketest_flow`.
- **"Run it" can reuse a result.** A request to run the check sometimes
  returns a matching earlier result rather than a new run (tsmc28 T1). Decide
  whether the guidance should prefer a fresh run.
- **More tsmc65 flows.** Spectre/AMS campaigns are the obvious next migration.
- **Another host: decided (owner, 2026-09-24).** Deploying to any ASIC host is
  allowed. Every guide has an "Another host" section (one directory per host,
  `.tracker-local/<host>/`, redeploys archive into its `revisions/`), and every
  AGENTS.md says to run on the named host without refusing or asking. The
  tsmc28 ADC deploy now archives the profile it replaces, as `jobs.pilot` does.
- ~~PowerShell over-quotes `--parameters`~~: `--parameters-file` added by
  worker pilot 2 (`2c9d45c`); AGENTS.md in each consumer says to use it.

## Commands and paths

- **Durable state** (outside every checkout):
  `C:/dev/.spec2si-job-state/<repo>/{tasks,receipts}`.
  **Profiles:** `<repo>/.tracker-local/` (ignored). tsmc28 keeps bandgap under
  `.tracker-local/bandgap/`; the ADC's earlier profiles are under
  `.tracker-local/revisions/<digest>/adc-normal.json`. A profile for another
  host lives in `<profile dir>/<host>/` (each guide's "Another host").
- **To read an old task**, use its archived profile. `.tracker-local/revisions/<profile sha>/profile.json`
  is chosen by the task's `profile_sha256`. With the current profile, a
  status call answers "profile changed; reconcile explicitly".
- **Commands:** package, deploy, start, status and collect are in each repo's
  guide. From Git Bash, prefix them with `MSYS_NO_PATHCONV=1`, or `/u/...`
  arguments become Windows paths.
- **Acceptance tooling:**
  - `python integrations/cluster_jobs/acceptance/run_trials.py --harness claude|codex --repo <checkout> --prompts <prompts.json> --out <private dir>`
  - `python .../analyze.py --harness ... --out ... --launch-pattern '<regex of the untracked launch>'`
  - Prompt sets are in `acceptance/prompts/`. No request may mention tracking.
  - `--hook-trust persisted` runs Codex without the bypass flag (trust is saved).
- **Worker:** `py -3 -m worker.controller run --contract worker/contracts/<id>.json --state-dir C:/dev/.spec2si-job-state/worker`;
  `py -3 -m worker.controller show --run <run dir>`. Runs live under
  `C:/dev/.spec2si-job-state/worker/<run id>/` (`ledger.jsonl`, gate logs,
  `review.md` / `change.patch` or `failure.md`). Tests: `py -3 -m pytest worker`.
- **Failure reports:** `jobs.workflow failures --profile <p> --state-dir <store>` lists
  the open ones; `report --task-key K --cause ... --by human|agent --question ...` records judgement.
- **Codex** is the desktop app's `%LOCALAPPDATA%/OpenAI/Codex/bin/<hash>/codex.exe`,
  which is not on PATH. The hooks feature is on.

## Traps already paid for

### The worker

- **Every Claude round has a fixed context cost**: about $0.30 in a small
  repo, more in tsmc28 and xt011 (their CLAUDE.md files are large). Budget
  `per_round_usd` above it, or the round ends `error_max_budget_usd`.
- **Claude on Windows reaches for PowerShell.** The allowance lists both
  `Bash(...)` and `PowerShell(...)` rules; with Bash rules only, pilot 1's
  worker could not run its tests.
- **Instructions must fit the harness.** Codex reads and edits through the
  shell inside its sandbox; a pytest-only instruction meant for Claude left
  pilot 2's first Codex worker unable to read a file.
- **The baseline decides attribution.** A regression gate that fails on the
  base (a Windows-only failure, the acceptance file itself) makes the run stop
  as "baseline broken" before any round.
- **The controller runs from flowkit's main checkout.** An edit to
  `worker/` applies to the next run at once; the worker's own changes live on
  its branch.
- **Consumer hooks run in the worker too.** A Claude worker in tsmc28, xt011
  or sky130 gets the tracker hooks (good) and the SessionEnd runlog harvest,
  which appends to the main checkout's `analog/specs/runlog.jsonl`.
- **Tracked gates leave things behind:** a snapshot per evaluation on the
  cluster (`~/.spec2si/<repo>/<flow>/worker-*`) and packages and profiles in the
  run directory. Remove them when a batch of attempts is done.

### The tracker

- **Vendoring invalidates snapshots.** Re-vendoring job code makes every
  deployed snapshot stale, because the job code is packaged. `start` refuses
  by name; redeploy each repo's profiles. Vendored tests are no longer
  packaged (`149ea4f`), so a test-only re-vendor is free.
- **Codex hook trust is keyed to `hooks.json`.** Editing a handler there needs
  `/hooks` again in that repo; editing `tracker_hook.py` does not.
- **Only literal workflow calls are captured.** A call wrapped in `timeout` or
  piped is not recorded by PostToolUse.
- **The guide is behavior.** A stale finding in a guide made three sessions
  repackage or refuse for a reason that had been fixed. Update the guides in
  the same change as the code.
- **License exhaustion happens** (`SPECTRE-209`, 2026-09-24 16:33–16:41 UTC).
  It is an execution failure, collected as "unchecked", never a design verdict.
- **Git Bash on Windows:**
  - Heredocs collapse `\\` and turn `\n` into real newlines inside string
    literals. Write Python with `os.sep`, or through the Write tool.
  - Python writes CRLF to stdout, so strip `\r` before `while read`.
- **Corner and check names** must start with a letter or digit (`tt_m40C`, not `-40`).
