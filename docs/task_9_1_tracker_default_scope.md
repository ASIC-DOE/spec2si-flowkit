<!--docmeta
title: Task 9.1 — make cluster-job tracking the assistant default
genre: plan
status: active
area: top
owner: soumyajit
updated: 2026-09-16
summary: Implementation scope for default tracker use in tool-enabled chat: existing-system reuse, harness integration, task linkage, evidence checks, rollout and behavioral acceptance tests.
-->

# Task 9.1 — make cluster-job tracking the assistant default

Scope companion to [the agentic workflow report](agentic_workflow_report.md). **WP0–WP5 shared implementation is complete:** see the [source audit](job_tracker_wp0.md), [WP1 results](job_tracker_wp1.md), [WP2 package](../integrations/cluster_jobs/README.md), [WP3 durable linkage](job_tracker_wp3.md), [WP4 strict collection](job_tracker_wp4.md) and [WP5 compatibility/rollout plan](job_tracker_wp5.md). Consumer migration and live harness/engineering acceptance remain pending. No hooks were installed, consumers edited or cluster code deployed.

## 1. Outcome and boundary

**An engineer asks the assistant to run a cluster task; the assistant uses the existing tracker without being reminded.** It submits or attaches correctly, retains the job ID, checks progress through the tracker, reconnects after interruption and verifies the required outputs before reporting an engineering result.

This is an improvement to today's chat workflow, not Task 9.2's autonomous repair loop. The user continues to set intent and decide the next engineering action. Task 9.1 must be useful without a new agent framework, scheduler, SQLite service, vector database or model-dependent classifier.

**All implementation work for this task stays in `spec2si-flowkit`.** Flowkit owns the shared entry point, harness integration, profile schema, synthetic fixtures and documentation. Consumer repositories are read-only evidence and compatibility targets during this task. Do not edit their launchers, instructions or settings, or install machine-wide hooks as part of it.

Compatibility cases cover Codex and Claude harnesses and two representative patterns: an instrumented digital engine and a campaign with an existing detached launcher. Use synthetic equivalents in flowkit tests. Actual consumer and harness activation is a subsequent, separately scoped rollout; do not claim default behavior in an installed tool merely because templates pass tests.

## 2. Current facts and prerequisites

Inspected 16 September 2026:

- `deployment/bnl/jobs/remote.py` already exposes `run`, `list`, `status`, `events`, `why` and `verify`; `cli.py` supplies the corresponding commands and waiting behavior.
- `bin/runjob` provides detached execution and job records; `bin/jobrec.py` attaches or self-registers. Preserve these as the authoritative job lifecycle and artifact store.
- Tsmc65 `dig_flows/run.py` self-registers; its Spectre and mixed-signal hooks increment an existing parent's counters. A hook import alone does not ensure a campaign is tracked.
- Tsmc28 `analog/engine/char/adc_cal_bench.sh` uses its own detached launch and log/done files. It is a candidate migration, but wrapping a script that detaches again would track the launcher rather than the simulation. The tracked payload must own/wait for the real work.
- Repository `.claude/settings.json` files inspected in tsmc65 and tsmc28 contain SessionEnd runlog harvesting. User-level Claude PreToolUse hooks enforce heredoc/SSH conventions. These are useful existing integration points; their presence does not establish tracker routing. Preserve them when adding behavior.
- Tsmc28's ownership instructions were correct: fresh upstream inspection found the canonical `jobs/` source, introduced in `c5b8d6a`. The initial apparent inconsistency was caused by stale local refs and an incorrect assumed source path.

**WP0 — completed:** restored 20 files and 19 distribution entries from upstream `df45995`, without merging unrelated changes or importing private-port code. Both TSMC copies match except for the extensionless launcher's CRLF checkouts; XT011/SKY130 lack local copies. Consumer files remain unchanged. See the audit for source hashes, existing site-default disclosures and passing test evidence.

No live cluster history was queried for this scope. Deployed bundle identity, supported harness versions and actual tracking coverage must be measured in implementation.

## 3. Desired user-visible behavior

| Situation | Required assistant behavior |
|---|---|
| “Run this simulation” | Resolve a supported profile and launch through the tracker; no extra tracker reminder or routine permission question |
| Instrumented engine runs directly | Retain its confirmed record, or execute under one tracked parent; avoid duplicate lifecycle owners |
| Job takes longer than a chat turn | Report the job ID and current observation; preserve state and use supported wait/resume behavior |
| New session or resumed task | Read task-to-job references, query live state and continue from the existing record |
| A remote read fails | Say status is unknown; do not declare termination or resubmit automatically |
| Output is stale, missing or unstamped | Refuse an engineering-success claim and identify the missing evidence |
| Raw detached launch is attempted | Catch the known bypass before execution and return the supported invocation to the assistant for correction |
| Read-only remote diagnosis | Permit it through the established transport; do not turn every SSH read into a tracked compute job |
| Unsupported launch path | Give a concrete missing-profile/adapter reason; use a recorded task-scoped exception only if authorized |

Automatic continuation after the application is closed is not assumed. Session restoration must work regardless; background notifications/wakeups depend on the tested harness lifecycle and belong in a later extension if unavailable.

## 4. Work packages and implementation locations

### WP1 — One discoverable entry point and process-local profiles

**Completed:** `jobs/workflow.py` implements all four operations and validated private profiles. A small optional `Transport.run(workspace=...)` extension allocates a fresh working directory before tracker launch. References are caller-retained until WP3; report parser declarations remain unchecked until WP4. See the [contract, invocation examples and test results](job_tracker_wp1.md). The paragraphs below preserve the intended boundary; harness integration is still proposed.

Add a thin stdlib entry point under flowkit's canonical `jobs/`, provisionally `workflow.py`, callable from both harnesses. Reuse `Transport` directly rather than parsing human CLI tables or rebuilding SSH. Proposed operations are `start`, `status`, `collect` and `resume`; names are design proposals, not existing commands. Shared harness logic/templates live under proposed `integrations/cluster_jobs/`; synthetic profiles and tests remain in flowkit. `jobs/` now exists; the entry point and harness-integration directory remain proposed additions.

The shared profile schema accepts repository/remote work root, executable argument construction, host policy, expected artifacts, progress source, engineering-report parser and workspace allocation. Commit only sanitized examples and synthetic profiles to flowkit. Runtime process-private profiles can be supplied by path; creating them in consumer repositories is outside this task. Reject unknown profiles and missing required parameters. Explicit host selection remains authoritative; use existing automatic host selection only where the profile permits it. Do not change the project-wide host policy as a side effect.

Return a small versioned JSON envelope containing task ID, selected host, job ID, observation state, evidence verdict and next permitted action. Keep raw logs and process-sensitive content behind the existing disclosure boundary. Do not require the model to copy identifiers out of prose.

### WP2 — Harness defaults and bypass guard

**Shared package implemented:** `integrations/cluster_jobs/` contains startup instructions, a scoped command recognizer, common Codex/Claude hook adapter, post-tool receipt cache and additive settings renderer. Windows/WSL protocol tests pass, including actual local job receipt recovery under WSL. This supplies default guidance and denial of recognized bypasses; it does not establish installed-harness enforcement. See the [coverage limits and activation checklist](../integrations/cluster_jobs/README.md). Receipt caching is post-response only; WP3 still owns durable pre-dispatch linkage and cross-session recovery.

Use three layers together:

1. **Startup/resume delivery:** a short harness instruction names the entry point, its location, supported profiles and pending task references. A shared document can feed `AGENTS.md`/`CLAUDE.md` and session-start integration. Do not load a large flow manual on every turn.
2. **Pre-execution guard:** deterministic recognition of supported compute-launch patterns and known bypasses, scoped to these projects/cluster targets. Reject a recognized untracked launch with an actionable replacement. Do not silently rewrite arbitrary shell programs or regex-classify every SSH command as a job.
3. **Post-execution/result handling:** capture returned task/job references automatically and require collection/evidence checks before a completed-job success response. Keep hooks short; waiting belongs in the existing wait API or bounded polling, not a hook that blocks indefinitely.

Official [Codex hooks documentation](https://developers.openai.com/codex/hooks) describes startup and pre/post tool hooks, including command interception, while noting coverage exceptions. The [Claude hooks guide](https://code.claude.com/docs/en/hooks-guide) provides pre-tool denial and lifecycle integration. Confirm schemas, tool-name matching and behavior against each installed version with a harmless canary; documentation availability is not proof that the active desktop/CLI path executes the hook.

Test native PowerShell, WSL/Bash, nested code-mode calls and supported resume paths. Existing user-level SSH guards must continue to work. A guard that sees only direct shell text cannot detect arbitrary subprocesses hidden in Python or an already-running interactive session. Document its coverage; for stronger enforcement, restrict compute credentials/tool access to the entry point in a controlled runner. Do not claim hooks are a universal security boundary.

If a harness cannot intercept the actual launch path, label that integration advisory and do not pass the default-enforcement acceptance gate. A direct tool/MCP adapter over the same entry point is an option if it materially improves discovery; no new tracking backend is needed.

### WP3 — Minimal durable task-to-job linkage

**Implemented:** `jobs/state.py` provides an exclusive permanent task-key reservation, atomic pre-dispatch intent and acknowledgement, local Git/manifest fingerprints, cross-session discovery and live resume. CLI starts now require durable state; legacy reference reads remain compatible. Crash and competing-caller tests prove no repeat dispatch for the same preserved store/key. There is no tracker-side request key: ambiguous submissions remain unresolved and cannot be automatically retried. See [WP3 usage and durability limits](job_tracker_wp3.md).

Persist an ignored, process-private task reference before/after submission using atomic writes. Proposed location: a configured local task-state directory, outside disposable worktrees. Store task ID, repository and patch identity, profile/version, manifest digest, selected host, existing job ID(s), required artifact set and last observation time. Requery tracker state on resume; cached state is not authority.

For the first milestone, use one writer per task and small JSON records. No new job database: the reference points to tracker records rather than copying heartbeat/result histories.

The lost-response window needs explicit treatment. The inspected `Transport.run` has no request key. Until a tracker-side key is implemented, a lost submission acknowledgement is `submission-unknown`: reconcile if possible and otherwise stop for resolution, never blindly resubmit. A subsequent small extension can persist a caller request key atomically before launch and resolve repeated requests to the same job. A lock must protect concurrent retries; a reservation with an uncertain launch remains ambiguous, not permission to spawn again. Test crash points on both sides of dispatch. This scope does not promise universal exactly-once execution.

### WP4 — Artifact completeness and engineering verdict

**Implemented:** the existing verifier is followed by a bounded remote evidence reader checking workspace/job binding and the exact stamped report bytes. The closed `json-v1` report contract requires declared design/top, durable input/source/request identity and full check/corner coverage. Collection returns separate execution/artifact/engineering verdicts and private JSON/Markdown summaries. Trusted process-specific report producers still require consumer validation in WP5; the shared validator cannot attest input consumption by an arbitrary producer. See [WP4 format, tests and limits](job_tracker_wp4.md).

Build a strict collection wrapper over `why`/`verify` and the existing process-local result parser. Keep the current CLI's behavior compatible for existing users.

Required checks:

- Transport observation is known and the intended job has reached a terminal state.
- Required outputs are enumerated by the profile and all are present, bound to this run and hash-verified.
- `UNSTAMPED` is not accepted, despite today's `jobs verify` exit code 0. Nor is `OK` on only a subset of required files sufficient.
- The flow report identifies the intended design/top/input and supplies all required checks/corners.
- Engineering verdict and job execution verdict remain separate. A successfully executed failing simulation is a valid collected result, not a successful design.

Produce JSON plus a concise Markdown evidence summary with links to the existing records. The summary includes incomplete checks and actual observations; it should make chat results easier to review, not create another source of truth.

### WP5 — Compatibility fixtures and rollout package

**Implemented:** opt-in required attachment, packaged local smoke jobs for parent/attach and foreground campaign ownership, recorder-unavailable/disabled failure checks, declared nested-detach refusal, additive harness distribution and a jobs-only hashed rollout candidate builder. Read-only consumer inspection confirmed additional bootstrap, workspace and reporting work for xt011/sky130. See the [per-consumer migration steps, canaries and rollback procedure](job_tracker_wp5.md). Synthetic fixtures do not satisfy live harness or licensed-report acceptance gates.

Test one lifecycle owner for the instrumented path: the parent tracker plus attach-mode recorder, or the engine's own confirmed record. For a detached campaign, the shared interface must invoke a declared foreground payload or refuse the unsupported nested-detach path. Model both cases with flowkit fixtures; do not modify the actual analog launcher. Declare expected outputs and allocate independent work areas to prevent co-written files. Document exactly what a later consumer migration must supply.

Prepare deployment templates using the existing content-hashed bundle mechanism after establishing flowkit ownership. Test harness adapters with recorded/synthetic events and isolated configurations. Deliver instructions for a subsequent activation test with a harmless tracked cluster payload, followed by a representative licensed run. Machine-level hook installation, remote deployment, real consumer changes and licensed validation are rollout actions, not changes to make in this flowkit-only implementation scope.

## 5. Proposed change surface

| Location | Scoped change |
|---|---|
| Flowkit `jobs/` | Existing source restored in WP0; add thin entry point and strict collector; request-key extension separately |
| Existing `remote.py`, `bin/runjob`, `bin/report.sh` | Reuse unchanged initially where possible; targeted schema/reconciliation extensions only when required |
| Flowkit `integrations/cluster_jobs/` (proposed) | Profile routing, deterministic hook logic and Codex/Claude configuration templates |
| Flowkit shared profile schema and synthetic examples | Parameters for commands, outputs, parsers and work-area policy; no private process values |
| Flowkit compatibility fixtures | Model parent/attach, self-registration and nested-detach cases without editing consumer launchers |
| Ignored local task-state directory | Durable references, not duplicated job history or public PDK data |
| Documentation | How to run, resume, diagnose exceptions and disable the integration without losing job records |

Keep every source change, test and template in flowkit. Keep site configuration, process commands and sensitive manifests outside committed public content. Extend the existing vendoring manifest only after reviewing source ownership, disclosure and portability; validate distribution into disposable test consumers under flowkit rather than writing to real checkouts. Package hook activation as a documented, separate installation step, not an automatic side effect of vendoring.

## 6. Tests that demonstrate the actual change

| Test | Required observation |
|---|---|
| Fresh chat: “run profile X” with no tracker wording | Assistant uses the entry point and records a confirmed job ID |
| Direct known EDA/`nohup` bypass | Pre-tool guard redirects before execution; user does not have to correct the assistant |
| Read-only cluster diagnostic | Allowed without registering a compute job |
| Existing SSH guard plus new guard | No precedence conflict or broken quoting behavior |
| Recorder unavailable/disabled | Selected workflow cannot silently count the launch as tracked |
| Launcher that detaches its own child | Pilot refuses/migrates the path; parent success cannot conceal active untracked work |
| Restart after acknowledgement | Restored task attaches to the same job |
| Lost submission acknowledgement | Reconcile or report submission-unknown; never duplicate the job |
| Two concurrent retries, if request keys are implemented | One logical job or explicit unresolved state, not two launches |
| Missing/unstamped/stale/subset artifacts | Strict collector refuses evidence acceptance |
| Job completes but flow fails | Report execution completion and engineering failure separately |
| Changed input/top/corner set | Reject incompatible evidence |
| Native/WSL/code-mode harness variants | Verify hook execution and actual default routing per supported path |

Reuse existing tracker tests and add contract/fault fixtures where behavior changes. This implementation's tests use mocked transport, a local shell sandbox and isolated harness configurations. Live smoke checks and licensed validation remain requirements for later operational acceptance, not evidence that the flowkit-only package can claim now.

For behavioral acceptance, conduct at least ten unprompted-to-use-tracker launch requests across the supported harness/profile combinations, including fresh and resumed sessions. Record all attempts, bypass detections, exceptions and corrections. Require every in-scope launch to be tracked or refused explicitly, complete artifact coverage for accepted results, and zero user reminders to use the tracker. Ten attempts are a release smoke test, not statistical proof of universal compliance.

Measure coverage against the harness launch-attempt log and pilot launcher records, not tracker history alone. Report unsupported paths as unsupported rather than excluding them from the denominator invisibly.

## 7. Delivery order, effort and rollback

| Slice | Engineering allowance | Reviewable result |
|---|---|---|
| Ownership (WP0 complete); harness canary still pending | 0.5–1 day originally | Source/distribution restored; actual hook path remains to demonstrate |
| Entry point, profiles and task references | 1–2 days | A normal tracked launch and reliable resume using existing tracker state |
| Default routing and hook integration | 1–2 days | Assistant corrects a known bypass without a user reminder |
| Strict collection and pilot paths | 1–2 days | Complete evidence bundle and correct failure handling |
| Synthetic behavioral validation and rollout documentation | 1–2 days | Package acceptance matrix and separate live deployment/rollback procedure |

**Shared-package estimate: approximately 5–9 engineering days**, conditional on recovering a reusable tracker source and functioning hook support. WP0 resolved source recovery without a new private-port import. A robust request-key extension may add 1–2 days; broad site-neutralization is separate from default tracker adoption. The table includes preparation for operational tests; actual consumer installation and live validation are separately scheduled rollout effort. Missing harness interception or incompatible deployed schemas require re-estimation, not silent scope expansion.

For the subsequent operational rollout, activate per project/profile. On problems, disable the harness integration for that profile while keeping tracker records, existing commands and task references intact. Do not roll back by deleting evidence or weakening engineering checks. Recorded exceptions retain reason, scope and expiry/review condition; routine supported launches should require no new permission prompts.

The **flowkit implementation** is complete when the shared package, integration templates, synthetic behavioral tests and rollout instructions pass without consumer edits. **Operational Task 9.1** is complete only after separate activation demonstrates that current chat defaults to the tracker and restores/collects the right evidence. Keep these milestones distinct. Autonomous diagnosis loops, full memory deployment, multi-agent scheduling and signoff-policy changes remain later tasks.
