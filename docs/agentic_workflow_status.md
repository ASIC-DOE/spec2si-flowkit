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
| §9.1 tsmc65 | Code **vendored** 2026-09-24 (`0c534a5e`); activation in progress | the report names its `dig_flows/run.py` parent/attach path as the first pilot |
| §9.1 operational gates | **Not run anywhere** | harness canaries, the ten-request behavioral acceptance (every `activation_matrix.json` is empty) and a licensed representative job |
| §6.3 lost-response reconciliation | **Open** | `Transport.run` has no caller request key, so an ambiguous submission stops as `submission-unknown` |
| §8 baseline and ablation (A–D) | **Not started** | no measurement of current chat; no B-versus-C comparison |
| §9.2 bounded autonomous worker | **Not started** | no controller or ledger in any repo |
| §9.3 DRC pilot with refusal handling | Deterministic part **exists** (`drcloop/`, tsmc65 `chip_patch.py --loop`); agent part not started | [DRC loop](drc_loop.md) |
| §9.4 numeric and digital regression lanes | **Not started** | no Optuna or SymbiYosys use |
| §6.5 memory | Session-log harvest **exists** (`browse/runlog.py`, 955 records across four repos); the memory plan it builds on is **missing** | `docs/agent_memory_plan.md` exists in no repository |
| §5 vendor agent trials | **Not started** | — |

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
