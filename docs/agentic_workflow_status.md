<!--docmeta
title: Agentic workflow report — what is built and what remains
genre: log
status: active
area: top
owner: soumyajit
updated: 2026-09-24
summary: A dated survey of the agentic workflow report against all five repositories. It covers what is built for each section, what remains, where each checkout stands, and proposed improvements to the report.
-->

# Agentic workflow report — what is built and what remains

This is a survey of [the agentic workflow report](agentic_workflow_report.md)
as of **24 September 2026**. The report is a study written on 16 September.
This page records what has since been built against it; update this page
rather than the study. Each entry names the evidence it rests on.

## 1. Status by report section

| Report section | State | Evidence |
|---|---|---|
| §9.1 shared package (WP0–WP5) | **Built** | `jobs/workflow.py`, `state.py`, `adapter.py`, `pilot.py`, `smoke.py`, `bin/evidence.py`; `integrations/cluster_jobs/`; [WP5](job_tracker_wp5.md) |
| §9.1 consumer pilots | **Built** in 3 of 4: tsmc28 (ADC normal mode), xt011 (buffer characterization), sky130 (OTA schematic) | each consumer's `docs/tracked_jobs.md` / `docs/tracked_adc_migration.md`; xt011's first job correctly reported an engineering failure (5 of 15 fits linear) |
| §9.1 tsmc65 | **Activated** 2026-09-24 for the digital smoketest synthesis (`aa08c61f`) | licensed job pass 5/5, and it agrees with the native report; tsmc65 `docs/tracked_jobs.md` |
| §9.1 operational gates | **Run in all four consumers** under Claude Code **and Codex**. tsmc65: licensed job passes, ten Claude requests pass (two incomplete). tsmc28: ADC licensed run passes 2/2; the short bandgap flow passes ten Claude requests (one incomplete) and ten Codex requests (none incomplete; a real license exhaustion was classified correctly). **xt011** (X1 buffer, under a minute) and **sky130** (OTA schematic, seconds): cluster canary, licensed job with an independent re-measurement from the raw waveforms, harness canaries, and ten requests under each harness, **none incomplete**; tasks map one to one to jobs (10/10 and 13/13) | tsmc65 `docs/tracked_jobs.md`; tsmc28 `docs/howto/tracked_bandgap.md`, `docs/tracked_adc_migration.md`; xt011 and sky130 `docs/tracked_jobs.md` |
| Codex hook trust | **Persisted** in all four consumers (owner, `/hooks`, 2026-09-24); a canary **without** the bypass flag passes in each | `~/.codex/config.toml` `[hooks.state]`: one entry per `hooks.json` handler, keyed by the handler definition's hash, so editing `hooks.json` needs `/hooks` again |
| §6.3 lost-response reconciliation | **Built** 2026-09-24 (`37ce65e`): the task id is the tracker's request key, claimed atomically by runjob before launch; `resume` attaches, a repeated `start` with the same key attaches or dispatches under that key | [WP3, request-key section](job_tracker_wp3.md); `jobs/test_request_key.py`; live on asic7: a discarded launch reply, resume, repeated start and a raw duplicate dispatch left exactly one job |
| §8 baseline and ablation (A–D) | Condition A **measured** 2026-09-24; B in ordinary use to be re-measured from 2026-10-09; the B-versus-C frozen-task design is written, C awaits §9.2 | [baseline](agentic_baseline.md): tsmc65, 24 sessions in 25 days, 0 tracked, 375 licensed launches, 5,197 cluster reads, 12.2 h of in-command sleep; 0 of 965 harvested attempts has a declared failure cause |
| §4.11 exploration → implementation cycles | **Added to the study** 2026-09-24 (owner): chat for exploration, agentic flows for implementation, failure reports as the handoff back | [study §4.11](agentic_workflow_report.md) |
| Failure reports for tracked jobs | **Built** 2026-09-24: a `collect` that is not a verified pass writes `failure.json`/`failure.md` (contract, outcome, evidence with log-signature counts, attempts, derived and declared cause, exploration question); `report` records judgement (agents: mechanical causes only), `failures` lists open reports | `jobs/failure.py`, `jobs/test_failure.py`; live: xt011's buffer fail derived `gate-fail`, tsmc28's SPECTRE-209 run derived `tool-error` |
| §9.2 bounded autonomous worker | **Built** 2026-09-24 (`worker/`): contract → isolated worktree branch → headless Claude or Codex rounds with schema-checked proposals → scope check → the controller's own gates → review bundle or failure report; ledger per run. **2 of 10** reviewed attempts, both merged | [worker/README.md](../worker/README.md); pilot 1 (Claude, guard argument order): 1 round, $0.53; pilot 2 (Codex, `--parameters-file`): one stop with a report (a controller prompt fault), then 1 round |
| §9.3 DRC pilot with refusal handling | Deterministic part **exists** (`drcloop/`, tsmc65 `chip_patch.py --loop`); agent part not started | [DRC loop](drc_loop.md) |
| §9.4 numeric and digital regression lanes | **Not started** | no Optuna or SymbiYosys use |
| §6.5 memory | Session-log harvest **exists** (`browse/runlog.py`, 955 records across four repos); the memory plan it builds on is **missing** | `docs/agent_memory_plan.md` exists in no repository |
| §5 vendor agent trials | **Not started** | — |

### Changes to shared code made during the tsmc65 activation (2026-09-24)

- **Starts bind to the packaged files** (`2d8427a`), not the whole checkout.
  Before this, a commit or a session-log rewrite made the cluster refuse a job
  that had already been submitted.
- **The workflow CLI reports task-store refusals by their real reason.** A
  `__main__` class-identity bug had hidden them all behind "cannot read/write
  workflow state".
- **Pilot profiles run `python3 -B`**, so jobs no longer write `__pycache__`
  into the snapshot.
- **The hook sees through configured launch wrappers and `tcsh -c`**
  (`64e0946`). A project also sets its hosts and argument prefixes.
- **From the acceptance runs** (`e6d2f6d`, `7d71074`):
  - The one-shot rule ("don't end on a background poll") is now part of the
    SessionStart guidance.
  - Receipts are kept only for the session's own repository.
  - `pilot.deploy` validates the profile before uploading anything.
  - A mangled `--parameters` value is a named refusal, not "cannot read/write
    workflow state". In the xt011 trials two sessions hit it and corrected it
    on the next call without reading any code.
- **From the xt011 and sky130 gates** (`149ea4f`): the vendored `test_*.py`
  files are no longer packaged. A test-only re-vendor (`dd5a79e`) had made
  every deployed snapshot stale, and one trial session had to redeploy
  mid-acceptance. The consumers' hook shims now pass every ASIC host and their
  tool wrappers, and their preflights require `CDS_LIC_FILE` and `spectre`
  inside the wrapper.

## 2. Where each checkout stood (2026-09-24)

The report and its work-package documents were written on the cluster and
reached the laptop only when flowkit was fast-forwarded to `aab2156`. Integrated
and pushed the same day:

| Repository | Branch | Result |
|---|---|---|
| flowkit | `main` | fast-forwarded to `aab2156` |
| tsmc28 | `main` | moved from the contained `feat/adc-calibration`; `pilot.py` and `project.py` added (`2ff0079`) |
| tsmc65 | `main` | package vendored (`0c534a5e`); `remote.py` and `runjob` updated from their unmodified `c5b8d6a` copies |
| sky130 | `snn-readout` | `origin/main` merged (`9cc9d46`). The newer `test_harness_can_fail` flags three branch-only tests |
| xt011 | `cml-pin-escape` | `origin/main` merged (`7f28124`, `8f892ee`) after 50 and 68 divergent commits; see below |

The xt011 merge found two problems that git itself did not report as conflicts:

- `main` wrote scripts and records against `xt011_published` after the branch
  had migrated that library to `ancasic_p2` and removed it.
- The branch's 2026-09-12 design record captured the ring's pre-redraw route
  file. Its pins and via count match only the redrawn file.

Both were corrected, and the corrected record was re-captured on the cluster.

## 3. Proposed improvements to the report

1. **Keep the study separate from its status.** The study still says there is no
   deployed pilot and that xt011 and sky130 lack a tracker. Both stopped being
   true on 16 September. This page is the status record.
2. **Use the session-log harvest as the §8 baseline.** Counting raw `ssh` or
   `nohup` compute launches in the harvested transcripts measures condition A.
   It also counts the untracked launches that tracker records cannot see.
3. **Add environment preflight to tracked profiles.** A tool setup script that
   stops silently on an undefined variable fails before any job exists.
   (tsmc65's `asic-setup.csh` did this on 2026-09-24.) A profile should require
   the environment to prove it activated. `asic_env.csh` sets
   `ASIC_ENV_ACTIVE` for this purpose.
4. **Plan the rollout for branches and checkouts.** The package landed on `main`
   while work continued on long-lived branches. Cluster-made commits never
   reached the laptop. A startup check could warn when a checkout is behind its
   remote or has drifted from flowkit.
5. **Put decisions first.** The study is 56 KB. The tool and vendor tables (§7)
   are a dated snapshot and could move to an appendix behind a one-page summary.
6. **Fix or write the missing memory plan** that §3.1 and §6.5 cite.
