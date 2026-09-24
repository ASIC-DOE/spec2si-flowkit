<!--docmeta
title: worker — the bounded autonomous worker (study §9.2)
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-09-24
summary: A deterministic controller that gives one decided task contract to a headless Claude Code or Codex worker in an isolated git worktree, checks its scope, runs the gates itself and ends with a review bundle or a structured failure report. Nothing is merged or pushed.
-->

# worker — the bounded autonomous worker

This is the first bounded worker of the [agentic workflow study](../docs/agentic_workflow_report.md)
(§9.2). It serves the study's §4.11: exploration happens in chat and ends in a
**decision**; the worker **implements** that decision, and when it cannot, it
hands back a **failure report** that opens the next exploration round.

## What it does

```text
contract (the decision) ─► preflight ─► round ─► scope check ─► gates ─► review bundle
                              │           ▲                        │
                              │           └── gate failures ◄──────┤ (budget left)
                              ▼                                    ▼
                        baseline broken /                   failure report
                        nothing to do                       (stop, scope, no progress, budget)
```

1. **Contract.** The engineer writes a JSON contract (see
   `contracts/`): goal, repository and base commit, `editable` and `protected`
   path globs, the gates, and a budget of rounds, dollars and minutes. At least
   one gate is an **acceptance** gate, and its test files are protected: the
   worker cannot change the oracle to pass.
2. **Preflight.** A git worktree on a new branch `worker/<run id>`, inside the
   run directory in the private state store, never in the engineer's checkout.
   The gates run on the base: acceptance must fail (otherwise there is nothing
   to do) and every other gate must pass (otherwise no result could be
   attributed to the change).
3. **Rounds.** A headless worker gets the contract, the earlier rounds and the
   last gate output, edits the worktree, and ends with a schema-checked
   proposal: `patch` or `stop`, a summary, observations kept apart from
   hypotheses, and on a stop the question for the engineer.
   - Claude Code: `claude -p --json-schema`, with only Read/Edit/Write/Glob/Grep,
     pytest and read-only git allowed. No other shell command, so no ssh, job
     launch, commit or push. Per-round dollar cap.
   - Codex: `codex exec --output-schema` in its `workspace-write` sandbox (no
     network). No dollar figure; rounds and minutes bound it.
4. **Checks without model turns.** The diff must stay inside `editable`, touch
   nothing protected, stay under 600 changed lines and not repeat an earlier
   round. Then the controller runs every gate itself.
5. **Outcome.** All gates pass: the change is committed to the worker branch
   and `review.md`, `review.json` and `change.patch` are written. Otherwise the
   next round gets the failures. A worker stop, a scope violation, a repeated
   diff or an exhausted budget writes `failure.md` and `failure.json` in
   [jobs/failure.py](../jobs/failure.py)'s format, with the worker's
   observations and hypotheses appended.

Every event is appended to `ledger.jsonl` in the run directory. The engineer
reviews the branch and merges it, or not; the worker never does.

## Use

```bash
python -m worker.controller run --contract worker/contracts/guard-argument-order.json --state-dir C:/dev/.spec2si-job-state/worker
python -m worker.controller show --run C:/dev/.spec2si-job-state/worker/<run id>
```

Afterwards remove the worktree with `git worktree remove <run dir>/worktree`,
and delete the branch if the change is not wanted.

## Writing a contract

- The goal states the **decision**, not the question. If the goal still needs a
  design choice, the task is exploration and belongs in chat.
- Write the acceptance test before delegating, and protect it. The worker
  pilot's own test is `integrations/cluster_jobs/test_guard_order.py`.
- The regression gates must pass on the base. Exclude the acceptance test from
  them, or the baseline reads as broken.
- Keep `editable` narrow: the files the change should need. A worker that
  needs more should stop and say so; that is a useful result.
- Budgets: every Claude round carries about $0.30 of fixed context cost on
  this machine; allow for it.

## Pilots (reviewed attempts)

Both pilots were backlog items from the agentic-workflow resume page. In each,
the engineer's side made the decision and wrote the protected acceptance test
first; the worker implemented it.

| # | Contract | Harness | Rounds | Cost | Time | Outcome |
|---|---|---|---:|---:|---:|---|
| 1 | `guard-argument-order`: a route's argument prefix may follow leading options | Claude Code | 1 | $0.53 | 0.7 min | **ready for review**, merged (`8b8305b`); a 10-line helper in `hook.py` |
| 2a | `parameters-file`: `start --parameters-file` for PowerShell | Codex | 1 | (tokens only) | 1.0 min | **stopped** with a failure report: the prompt told Codex it could run only pytest, and its file reader crashed |
| 2b | the same, prompt fixed | Codex | 1 | about 414k tokens | 3.3 min | **ready for review**, merged (`2c9d45c`) |

What the pilots showed:

- **The controller's gates, not the worker's claims, decide.** Pilot 1's worker
  could not run tests (the tool allowance had Bash rules only, and on Windows it
  used PowerShell) and said so, listing its predictions as unverified
  hypotheses. The gates then passed. Both shells are allowed now.
- **A stop is a useful result.** Pilot 2a's worker could not read files and
  stopped with a report that named the cause; nothing was changed. The fault was
  in the controller's prompt (a Claude-only instruction), fixed in `ac671a4`.
- **Reviewing a small, gated diff is quick.** Both patches were minimal and
  touched only the editable file.

Two reviewed attempts of the study's ten. Next: diagnosis contracts (a failure
report as the input) and tracked cluster jobs as gates.

## Limits

- One worker, one repository, one task at a time. Gates are local commands;
  tracked cluster jobs as gates are not wired in yet.
- The failure report's cause is derived only mechanically (`gate-fail` when the
  acceptance gates still fail after the rounds); every other stop is
  `unclassified` until the engineer judges it.
- Contracts name absolute checkout paths (this machine's).
