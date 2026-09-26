<!--docmeta
title: RESUME — the agentic-workflow plan, session handoff
genre: log
status: active
area: top
owner: soumyajit
updated: 2026-09-26
summary: Where the agentic workflow plan stands at the end of 2026-09-26 and what to do next. START HERE: the B-versus-C comparison on frozen tasks (study §8). §9.2 is done: ten reviewed worker attempts, 9 ready for review, 5 merged, the expected stop, no false acceptance; the worker gained blind replays, injected-fault bases and fetched results. Everything before it is built and accepted: the tracker in all four consumers under Claude Code and Codex, duplicate-safe submission, failure reports, the condition-A baseline. Includes the commands, paths and traps a new session needs.
-->

# RESUME — the agentic-workflow plan

Last worked **2026-09-26**. Read in this order:

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

At the end of 2026-09-26 flowkit, tsmc28, xt011 and sky130 were pushed; tsmc65's
`main` was left unpushed because it also carries other sessions' AFE/driver commits
(flowkit: the commit carrying this page; tsmc65 `89bc2706`, tsmc28 `6f6bbbe`, xt011 `96b8daa`, sky130 `3aa1231`; branches: xt011 `cml-pin-escape`, sky130 `snn-readout`, the rest `main`).

| Repo | Tracked flow(s) | Live gates | Deployed profile |
|---|---|---|---|
| tsmc65 | digital smoketest synthesis (`dig_flows/run.py smoketest_flow`, about 3 min) | **accepted**: canaries, licensed pass 5/5, ten Claude requests (two incomplete), Codex canaries | snapshot-010 on asic8, `.tracker-local/` |
| tsmc28 | ADC normal mode (about 1 h 53 min); **bandgap DC** (about 1 min, the short task) | **accepted**: ADC licensed pass 2/2; bandgap passes the canaries, ten Claude requests (one incomplete) and ten Codex requests | bandgap snapshot-010, ADC snapshot-20260926-01, both on asic7 |
| xt011 | buffer characterization, one cell per task (X1: under a minute) | **accepted**: canaries, licensed run (engineering **fail**, as the native scorer), ten requests under Claude and under Codex, none incomplete | snapshot-20260926-01 on asic7 |
| sky130 | OTA schematic regression (seconds) | **accepted**: as xt011; engineering **pass 7/7** | snapshot-20260926-01 on asic7 (asic6 profiles archived under `.tracker-local/asic6/revisions/`) |

Codex hook trust is **persisted** in all four consumers, and a canary without
the bypass flag passes in each. Profiles were all redeployed after flowkit
`1e51c95` (worker attempt 10 changed `jobs/failure.py` and `jobs/workflow.py`,
re-vendored everywhere); a change to a vendored test does not make them stale.

Evidence lives in each repo's guide: tsmc65 `docs/tracked_jobs.md`; tsmc28
`docs/howto/tracked_bandgap.md` and `docs/tracked_adc_migration.md`; xt011 and
sky130 `docs/tracked_jobs.md`.

## START HERE: the B-versus-C comparison (study §8)

**§9.2 is done** (2026-09-26): ten reviewed worker attempts, eleven runs. Read
[worker/README.md](../worker/README.md) first: design, contracts, tracked
gates, fetched results, blind replays, and the table of attempts. In short:
9 ready for review, each equivalent to its oracle or to the real fix; 5 merged;
two stops (a controller fault in 2a, and attempt 4's expected design question);
no false acceptance, no scope violation; 8 licensed runs, $2.19 of Claude and
about 2.4M Codex tokens. Coverage: 5 Codex runs, 4 diagnoses, tracked gates in
tsmc28, xt011, sky130 and tsmc65, one expected stop, two blind replays, two
injected faults, two real backlog items.

### 1. Freeze the task set

Every attempt can now be expressed as a **replay contract** (`"replay"`), so
each task re-runs from the same base with the same oracle:

| Task | Source | Oracle | Gate | Licence per run |
|---|---|---|---|---|
| guard order | replay of `8b8305b` | `integrations/cluster_jobs/test_guard_order.py` | local | 0 |
| parameters-file | replay of `2c9d45c` | `jobs/test_parameters_file.py` | local | 0 |
| mangled parameters | `replay-mangled-parameters.json` (`7d71074`) | its test in `jobs/test_state.py` | local | 0 |
| unpackaged tests | `replay-unpackaged-tests.json` (`149ea4f`) | its test in `jobs/test_pilot.py` | local | 0 |
| Codex tokens | replay of `acbb85d` | `worker/test_cost_report.py` | local | 0 |
| report note | replay of `1e51c95` | `jobs/test_failure_note.py` | local | 0 |
| bandgap tempco | replay of tsmc28 `2ef9455` | the `tempco-reported` check | tracked, asic7, ~1 min | 2 |
| OTA fault | `ota-injected-fault.json` (sky130 `worker-fault/ota-tail-gate`) | the adapter's checks | tracked, asic7, seconds | 2 |
| SDC fault | `sdc-injected-fault.json` (tsmc65 `worker-fault/sdc-clock-name`) | the adapter's checks | tracked, asic8, ~3 min | 2 |
| buffer linearity | `buffer-linearity.json` (xt011; the answer is a **stop**) | the contract's `oracle` | tracked, asic7, <1 min | 1–3 |

The scratch `worker-fault/*` branches are local only; keep them. Local tasks
cost nothing but model time. **Size the licensed part before running it:** three
repeats × two conditions × four tracked tasks is about 48 licensed runs, well
past the ten at which the owner wants to be asked. Propose a smaller tracked
set (for example one tracked task, three repeats) and ask.

### 2. Build the B runner

- **C** is the worker on the contract, as now.
- **B** is an ordinary headless session (`claude -p`, or `codex exec`) in the
  same blind workspace, given the goal, the context files and the gate
  commands as a plain request, with the repository's tracker, guides and hooks
  and **no controller**: no schema, no scope enforcement, no rounds. When it
  ends, the controller runs the same gates and records scope (measured, not
  enforced), cost and time. For tracked tasks B launches through the tracker
  itself, as a chat session would.

A `--condition B` mode in `worker.controller` that reuses preflight (the blind
workspace), the gates and the review/failure writers is probably the least
code; `integrations/cluster_jobs/acceptance/run_trials.py` has the session
plumbing. Pin the harness versions for the whole comparison.

### 3. Score

The same metrics for both conditions, from the baseline's design
([agentic_baseline.md](agentic_baseline.md), "The B-versus-C comparison on
frozen tasks"): accepted over attempts, human interventions, elapsed time,
licensed runs, cost, false acceptances, scope excursions, and
failure-report completeness for every non-pass.

### Running any worker task (the per-attempt procedure)

1. **Decide and write the oracle first** (the exploration half, done in chat):
   the acceptance test, or the adapter check a tracked gate will apply, and the
   expected outcome in the contract's `oracle` field (the worker never sees it).
   Commit the test to the target repo. It must fail on the base.
2. **Write the contract** in `worker/contracts/<id>.json` (copy one). The
   regression gates must pass on the base: exclude the acceptance test and any
   test that fails on Windows for other reasons (`jobs/test_isolated_bundle.py`
   is POSIX-only). Budget `licensed_jobs` at one run per tracked gate plus the
   baseline. Declare `fetch` for results that hold measured values.
3. **Run it** from flowkit, in the background (minutes):
   `py -3 -m worker.controller run --contract worker/contracts/<id>.json --state-dir C:/dev/.spec2si-job-state/worker`
4. **Review** `review.md` and `change.patch` (or `failure.md`); for a replay,
   compare with `fix.patch` and read the audit line; read the worker's
   observations for leaks the audit cannot see.
5. **Merge or not.** Worktree runs: `git merge --ff-only worker/<run id>` (rebase
   it with `--autostash` first if main moved), then
   `git worktree remove --force <run dir>/worktree` and `git branch -D`.
   Replay runs live in their own clone: nothing to merge; delete `worktree/`.
6. **Follow through.** A change to packaged code: re-vendor (flowkit `jobs/`),
   commit in each consumer, redeploy every profile (no licence), update the
   guides' "current profile" line.
7. **Record** it in worker/README.md and the status survey.

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
- ~~`report --note` alone was refused~~: worker attempt 10 (`1e51c95`).
- ~~Codex runs read "$0.00"~~: worker attempt 9 (`acbb85d`).
- **The licence log signature counts routine checkout lines.** A passing tsmc65
  Genus run reads `license: 9`, so an early Genus failure would be derived as
  a licence `tool-error`. Tighten `SIG_LICENSE` in `jobs/bin/report.sh`
  (success lines out, denials in), test, re-vendor, redeploy.
- **xt011 buffer linearity is an open design question** for the owner: X1 is
  slew-limited at 250–520 fF at 160 MHz, so the linear-in-f model (or the 2 %
  criterion, or the operating envelope) needs deciding. The open report
  `gate-buffer-x1-20260924` carries the worker's numbers and what they
  contradict (the scorer's docstring).
- **Codex's Windows sandbox** sometimes fails shell calls
  (`helper_unknown_error: apply deny-read ACLs`); only the model reports it.

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

- **Uncommitted contracts are readable.** A worktree worker can read the main
  checkout, including a contract's `oracle` text if it names the answer. Keep
  oracle text to the expected shape, or use a replay (the prompt then names no
  checkout, and reads outside are audited).
- **Merging worker changes while other runs are live is safe**: a running
  controller has already imported its code; the next run picks up the merge.
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
