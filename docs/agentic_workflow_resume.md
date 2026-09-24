<!--docmeta
title: RESUME — the agentic-workflow plan, session handoff
genre: log
status: active
area: top
owner: soumyajit
updated: 2026-09-24
summary: Where the agentic workflow plan stands at the end of 2026-09-24 and the exact next actions, in order. The tracked-job tracker is active and accepted in all four consumers under Claude Code and Codex, with Codex hook trust persisted, and submission is duplicate-safe (§6.3), and condition A of §8 is measured; next is the §9.2 worker built around failure reports (§4.11). Includes the commands, paths, tooling and traps a new session needs.
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

All five repos were pushed and in sync at the end of the third 2026-09-24
session (branches: xt011 `cml-pin-escape`, sky130 `snn-readout`, the rest `main`).

| Repo | Tracked flow(s) | Live gates | Deployed profile |
|---|---|---|---|
| tsmc65 | digital smoketest synthesis (`dig_flows/run.py smoketest_flow`, about 3 min) | **accepted**: canaries, licensed pass 5/5, ten Claude requests (two incomplete), Codex canaries | snapshot-008 on asic8, `.tracker-local/` |
| tsmc28 | ADC normal mode (about 1 h 53 min); **bandgap DC** (about 1 min, the short task) | **accepted**: ADC licensed pass 2/2; bandgap passes the canaries, ten Claude requests (one incomplete) and ten Codex requests | bandgap snapshot-007, ADC snapshot-20260924-06, both on asic7 |
| xt011 | buffer characterization, one cell per task (X1: under a minute) | **accepted**: canaries, licensed run (engineering **fail**, as the native scorer), ten requests under Claude and under Codex, none incomplete | snapshot-20260924-04 on asic7 |
| sky130 | OTA schematic regression (seconds) | **accepted**: as xt011; engineering **pass 7/7** | snapshot-20260924-05 on asic7 (asic6 profiles archived under `.tracker-local/asic6/revisions/`) |

Codex hook trust is **persisted** in all four consumers, and a canary without
the bypass flag passes in each. Profiles were all redeployed after flowkit
`149ea4f`; from now on a change to a vendored test does not make them stale.

Evidence lives in each repo's guide: tsmc65 `docs/tracked_jobs.md`; tsmc28
`docs/howto/tracked_bandgap.md` and `docs/tracked_adc_migration.md`; xt011 and
sky130 `docs/tracked_jobs.md`.

## Next actions, in order

**Done (second 2026-09-24 session):** the xt011 and sky130 live gates, and
persisted Codex hook trust. The §9.1 operational gates are now run in all four
consumers. Evidence is in each repo's guide and in the status survey.

**Done (third session):** duplicate-safe submission (§6.3), flowkit `37ce65e`;
the §8 condition-A baseline ([agentic_baseline.md](agentic_baseline.md)); and the
study's new §4.11 (exploration → implementation cycles, failure reports); and
structured failure reports for tracked jobs (`jobs/failure.py`: `collect` writes
them, `report` records judgement, `failures` lists the open ones).
Earlier in the session:
The task id is the tracker's request key; see the
[WP3 request-key section](job_tracker_wp3.md#tracker-side-request-key-2026-09-24).
All five profiles were redeployed afterwards.

### 1. The first bounded autonomous worker (study §9.2)

Diagnosis and maintenance first, using the same adapters and a local ledger.
Its retries can now rely on request keys (§6.3 is built). Build it to the
study's §4.11: it implements a decided contract, and when it cannot meet the
contract it stops with a **structured failure report** (contract and failed
checks, evidence, attempts and budget, cause class, the question for the next
exploration round). Build it so that the B-versus-C comparison in
[the baseline](agentic_baseline.md) can be run on it. The report format exists:
`jobs/failure.py` (built 2026-09-24); the worker should emit it, not a new one.

### 2. Re-measure B in ordinary use (from 2026-10-09)

Re-run `integrations/cluster_jobs/acceptance/baseline.py` on the two weeks after
activation and compare with condition A (the baseline's "after" section).

### 3. Report upkeep (status survey §3)

- Write a decisions-first summary.
- Move the dated tool and vendor tables to an appendix.
- Write `docs/agent_memory_plan.md`, or remove the study's references to it.

### Smaller items found along the way

- **Tempco missing from the bandgap JSON.** tsmc28's `bandgap_dc.json` is
  written before the tempco is computed. Fix it in the bench; it is a packaged
  file, so redeploy afterwards.
- **Guard argument order.** The guard matches only the flow argument first:
  `run.py --phase syn smoketest_flow` is not routed. The bench-side refusal
  covers tsmc28's flows; tsmc65's `run.py` has no such refusal.
- **"Run it" can reuse a result.** A request to run the check sometimes
  returns a matching earlier result rather than a new run (tsmc28 T1). Decide
  whether the guidance should prefer a fresh run.
- **More tsmc65 flows.** Spectre/AMS campaigns are the obvious next migration.
- **Another host: decided (owner, 2026-09-24).** Deploying to any ASIC host is
  allowed. Every guide has an "Another host" section (one directory per host,
  `.tracker-local/<host>/`, redeploys archive into its `revisions/`), and every
  AGENTS.md says to run on the named host without refusing or asking. The
  tsmc28 ADC deploy now archives the profile it replaces, as `jobs.pilot` does.
- **PowerShell over-quotes `--parameters`** (`'{\"case\":...}'`). The named
  refusal makes sessions fix it in one step; a `--parameters-file` or per-key
  flags would remove the trap.

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
- **Codex** is the desktop app's `%LOCALAPPDATA%/OpenAI/Codex/bin/<hash>/codex.exe`,
  which is not on PATH. The hooks feature is on.

## Traps already paid for

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
