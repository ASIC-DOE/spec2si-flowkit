<!--docmeta
title: RESUME — the agentic-workflow plan, session handoff
genre: log
status: active
area: top
owner: soumyajit
updated: 2026-09-24
summary: Where the agentic workflow plan stands at the end of 2026-09-24 and the exact next actions, in order. The tracked-job tracker is active and accepted in tsmc65 and tsmc28 (Claude Code and Codex); xt011 and sky130 are next. Includes the commands, paths, tooling and traps a new session needs.
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

All five repos were pushed and in sync at the end of the session: flowkit
`69e0366`+, tsmc65 `f9138b6e`, tsmc28 `a463c6d`, xt011 `5cb002f` (branch
`cml-pin-escape`), sky130 `f6af65b` (branch `snn-readout`).

| Repo | Tracked flow(s) | Live gates | Deployed profile |
|---|---|---|---|
| tsmc65 | digital smoketest synthesis (`dig_flows/run.py smoketest_flow`, about 3 min) | **accepted**: canaries, licensed pass 5/5, ten Claude requests (two incomplete), Codex canaries | snapshot-005 on asic8, `.tracker-local/` |
| tsmc28 | ADC normal mode (about 1 h 53 min); **bandgap DC** (about 1 min, the short task) | **accepted**: ADC licensed pass 2/2; bandgap passes the canaries, ten Claude requests (one incomplete) and ten Codex requests | bandgap snapshot-004, ADC snapshot-20260924-02, both on asic7 |
| xt011 | buffer characterization (`run_buf_bench.sh` via `tracked_job.py`) | **not run** | stale: the job code changed since 2026-09-22 |
| sky130 | OTA schematic regression | **not run** | stale: as xt011 |

Evidence lives in each repo's guide: tsmc65 `docs/tracked_jobs.md`; tsmc28
`docs/howto/tracked_bandgap.md` and `docs/tracked_adc_migration.md`; xt011 and
sky130 `docs/tracked_jobs.md`.

## Next actions, in order

### 1. xt011 and sky130: run the live gates (the plan's §9.1, operational)

Do each repo the way tsmc65 and tsmc28 were done.

1. **Bring the hook shim up to date.** `deployment/bnl/tracker_hook.py` calls
   `project.main` with its defaults, which cover only asic7 and no wrappers.
   Pass `hosts=` (asic1..10, asicdesign, pmos, plus FQDNs) and `wrappers=`
   (the repo's activation wrapper, e.g. `asic_tools_xt011.csh` or
   `asic_tools_sky130.csh`, plus `remote_task.sh`), as tsmc65 does.
2. **Package and deploy a new snapshot.** The job code changed (`workflow.py`,
   `pilot.py`), so the old snapshot's `start` will be refused, correctly and by
   file name. `deploy` is the cluster canary; it uses no license.
3. **Run the harness canaries.** In a fresh `claude -p` session and a
   `codex exec` session: a marker plus the legacy launch must be denied before
   the marker exists; read-only ssh must run; a workflow status call must be
   captured; a resumed session must get its reference back.
4. **Run one licensed job, and compare it independently** with the native
   output: xt011's `native.json` and bench logs; sky130's scorer JSON.
5. **Run the ten ordinary requests under both harnesses** with
   `integrations/cluster_jobs/acceptance/` (below). Pick a **short** case: time
   one run first. xt011's X1 buffer runs 40 simulations; sky130's OTA
   schematic is a single TT point.
6. **Verify and record.** Tasks must map one to one to cluster jobs
   (`jobs.workflow tasks` against `ls ~/.asicjobs`). Collect anything left
   uncollected. Record the results in the repo's guide and in the status survey.

### 2. Codex hook trust (the owner's action)

The Codex runs used `--dangerously-bypass-hook-trust` per invocation. For
everyday use, the owner trusts the project hooks once per repo with `/hooks`
in a Codex session. Then re-run one Codex canary **without** the bypass flag
to confirm that persisted trust works.

### 3. Duplicate-safe submission (study §6.3; still open)

`Transport.run` has no caller request key, so a lost acknowledgement ends as
`submission-unknown` and stops. Design a request key that is persisted
atomically before launch and resolved on the tracker side. Test both crash
points on either side of dispatch, and two concurrent starts. Flowkit only;
then vendor.

### 4. The §8 baseline and ablation

Status-survey improvement 2: use the session-log harvest
(`browse/runlog.py`, committed `analog/specs/runlog.jsonl` in each repo) to
count raw `ssh`/`nohup`/wrapper compute launches before and after activation.
That is condition A, and the untracked-launch rate the tracker cannot see.
Then define the B-versus-C comparison on frozen tasks.

### 5. The first bounded autonomous worker (study §9.2)

Diagnosis and maintenance first, using the same adapters and a local ledger.
It needs item 3 before it may retry anything.

### 6. Report upkeep (status survey §3)

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

## Commands and paths

- **Durable state** (outside every checkout):
  `C:/dev/.spec2si-job-state/<repo>/{tasks,receipts}`.
  **Profiles:** `<repo>/.tracker-local/` (ignored). tsmc28 keeps bandgap under
  `.tracker-local/bandgap/`; the ADC's earlier profile is under
  `.tracker-local/adc-revisions/`.
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
- **Codex** is the desktop app's `%LOCALAPPDATA%/OpenAI/Codex/bin/<hash>/codex.exe`,
  which is not on PATH. The hooks feature is on.

## Traps already paid for

- **Vendoring invalidates snapshots.** Re-vendoring job code makes every
  deployed snapshot stale, because the job code is packaged. `start` refuses
  by name; redeploy each repo's profiles.
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
