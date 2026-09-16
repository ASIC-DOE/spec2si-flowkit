# WP5 — compatibility and rollout package

Shared implementation prepared on 16 September 2026. Consumer inspection was
read-only. No hooks, process adapters, remote files or consumer sources were
installed/modified. This package closes the shared compatibility work; operational
Task 9.1 still requires the activation tests below.

## What ships

- `jobs/adapter.py`: opt-in `require_attached(recorder)` refuses a disabled,
  unavailable or incorrectly attached recorder before real work. It preserves
  the existing best-effort recorder behavior for other callers.
- `jobs/fixtures/compat_payload.py`: synthetic instrumented engine and foreground
  campaign. The campaign waits for its child; neither fixture detaches itself.
- `python3 -m jobs.smoke`: runs four real local tracker jobs with an isolated HOME,
  then checks one parent record/event per job, attachment, progress, foreground
  completion, independent workspaces and failure before work when recording is
  required but unavailable/disabled. It also checks refusal of a declared detached
  profile. No SSH, licenses or EDA tools are used.
- `integrations/cluster_jobs/package.py`: builds a fresh jobs-only candidate tree,
  using the reviewed distribution manifest, with SHA-256 file inventory and
  documentation. It refuses an existing output directory. It never installs.
- `sync.py`: now includes the shared adapter, smoke fixture/command and five
  opt-in harness files. Vendoring does not create `.claude` or `.codex` settings.

The smoke tests exercise parent-plus-attach ownership, the chosen integration
mode. Existing standalone self-registration remains supported by the tracker;
automatic adoption of arbitrary self-registered jobs is not added to the durable
workflow. Unsupported nested-detach profiles are refused before dispatch.
Mislabeling an arbitrary detaching script as foreground is not detectable in
general: review adapters and validate their completion behavior during rollout.

## Consumer findings and required work

Evidence comes from current working files, with inspected HEADs:

| Repo | HEAD inspected | Tracker/reporting finding |
|---|---|---|
| tsmc65 | `d563d9ec033d71dfa0fcfe5fe493573851cbf142` | Existing vendored tracker; digital runner loads jobrec. Needs new shared workflow/collector and a validated report adapter, not a second tracker. |
| tsmc28 | `81d8262c54a0bb06542ef7358d24c416af88ee2c` | Existing tracker; the inspected campaign has an independent detached launcher. Foreground integration is required. |
| xt011 | `7bccadd38438dd056cb86a9aecad490624efecea` | No `deployment/bnl/jobs/` in the inspected checkout and no recorder references in inspected analog/chip/deployment scripts. A detached bench wrapper and foreground payload already exist. |
| sky130 | `5c60969e418001399dac7b80ce64a43602082885` | No `deployment/bnl/jobs/` and no recorder references in inspected analog/digital/deployment scripts. Foreground shell pipelines and persisted JSON scorers already exist. |

These observations establish local gaps, not absence of every tracker deployment
on the cluster. [Inspected working-file hashes](evidence/job_tracker_wp5_consumers.json)
record the source evidence without copying private implementation code. The prior WP0 source audit and `sync.py --check` are the baseline
for inspecting upstream-copy drift; never overwrite unexplained consumer edits.

### XT011: tracker bootstrap plus workspace and reporting adaptation

Inspected paths: `analog/bench/launch_buf_bench.sh`, `run_buf_bench.sh`,
`run_cml_bench.sh`, and `deployment/bnl/push_flow.sh`.

1. Vendor the shared jobs/harness sources after drift/disclosure review. Verify
   the launcher checks out as LF; the extensionless `runjob` needs the same
   `.gitattributes` rule as flowkit. Do not rely on Python/shell filename rules.
2. Profile the **foreground** `run_buf_bench.sh` path. Keep
   `launch_buf_bench.sh` as a recognized bypass in the guard; wrapping it would
   record a launcher exit instead of bench completion.
3. Stage a dedicated bench work area and set its `BENCH_DIR`. Both inspected
   payload scripts change into that directory, so merely starting from WP1's
   unique directory does not isolate their outputs. Check all nested bench
   inputs, generated decks and output paths. Bind artifacts to the run directory.
4. Add a process-local adapter that verifies actual cell/load/frequency/corner
   coverage and intended inputs, then emits WP4 `json-v1`. Do not translate a
   final banner or shell exit status into a sweep pass. Preserve existing checks
   that substitutions and sweep overrides actually took effect.
5. Initially use runjob's process status. Add `jobrec` semantic counters only if
   valuable; if a profile requires those counters, assert attachment before work.
6. Extend the selective deployment path deliberately. `push_flow.sh` copies an
   explicit list of scripts; it is not evidence that newly vendored files reach
   the cluster. Preserve its separation of source code and cluster-owned OA data.
   Never mirror or replace OA/work directories to distribute tracker code.

### SKY130: reuse scorers, isolate work and preserve signoff scope

Inspected paths: `digital/i2c_slave/run_flow.sh`, `run_signoff.sh`,
`flow/score_results.py`, `flow/score_signoff.py`,
`analog/lib/sky130_ota6/run_flow.sh`, `simulation/score_schematic.py`, and
`deployment/bnl/sync_to_asic7.ps1`.

1. Bootstrap the same jobs/harness package and LF rule. These foreground shell
   flows can have runjob as lifecycle owner without forcing Python instrumentation.
2. Digital launchers currently derive fixed work directories from the checkout.
   Add an explicit work-root contract or stage a per-run source/work tree, and
   update downstream Tcl/scorer paths consistently. Running the unchanged scripts
   from a new cwd would still co-write those fixed outputs.
3. The analog top-level flow accepts a results directory, but layout/GDS paths
   also depend on a separate work-root convention. Route every stage into one
   isolated run allocation; changing only the top-level results argument is
   insufficient. Serialize unavoidable shared OA operations explicitly.
4. Reuse the existing scorer logic behind a small normalized-report adapter.
   `score_schematic.py` reports one declared typical corner/temperature; do not
   claim coverage of corners it never ran. Digital scorers persist structured
   results and fail via exit status, but still need job/input identity and explicit
   check coverage for WP4.
5. Preserve signoff semantics. The inspected digital signoff scorer distinguishes
   local geometry cleanliness from density-related results; its existing accepted
   policy must not become an unqualified "all DRC clean" check. Name the scoped
   check and any waiver/policy explicitly in the process manifest/report adapter.
6. The deployment helper accepts individual files and performs disclosure checks;
   it refuses directories/protected paths. Use reviewed code file lists and the
   existing content-hashed runtime bundle. Do not bypass disclosure checks or
   upload private state, reports, PDK data or settings with a recursive directory copy.

### TSMC pilot work

Use tsmc65's instrumented digital path as the first parent/attach pilot. Verify
that its optional recorder import cannot silently satisfy a *required* reporting
contract; use the opt-in attachment check in that mode. Audit its actual output
locations and normalized report adapter before enabling engineering pass claims.

Use tsmc28's `analog/engine/char/adc_cal_bench.sh` as the second lifecycle case:
extract/invoke a declared foreground payload that waits for the full campaign.
Do not wrap its own detached launcher. Then activate xt011's foreground bench and
sky130's foreground scorer-backed flow after their workspace migrations.

## Build and local validation

```sh
python3 integrations/cluster_jobs/package.py --output /new/private/rollout-candidate
cd /new/private/rollout-candidate
python3 -m deployment.bnl.jobs.smoke
```

The candidate contains public shared code and synthetic fixtures only. Its README
is package documentation, not a replacement consumer README. Review
`rollout-manifest.json`, then prepare a narrow consumer change using the existing
vendoring seam. A broad `sync.py --to` affects other shared components too; inspect
that full diff rather than treating it as a jobs-only installation.

`templates/foreground_profile.json` is a sanitized starting point for the harmless
cluster canary; replace the deliberately invalid host, interpreter/payload path and
work root in a **private** copy. `templates/activation_matrix.json` records ten
ordinary-language attempts with empty results, rather than inventing a live pass.
Use its per-attempt metadata fields and repeat for each actual harness/tool path.
Keep instruction/template fixtures inside `integrations/cluster_jobs/templates`;
they are not consumer-specific executable profiles until configured and validated.

The packaged smoke runs without the source checkout on its import path. It uses
the exact shipped bundle locally, captures real records, waits for completion and
collects normalized reports. Temporary job evidence is removed after the smoke;
stdout gives counts and outcomes. The fixture's report uses expected identities
for synthetic testing only and is not a production report adapter.

Validation: all 50 combined workflow/state/evidence/hook/package/distribution tests
passed under WSL. Native Windows passed the four applicable package/distribution
tests, skipping the POSIX smoke. Python 3.6 grammar checks and `git diff --check`
passed. The package test runs from its disposable output tree, not the source repo.

## Subsequent activation gates

Keep private profile paths, state/receipt directories, cluster aliases, workspace
roots and process manifests out of the public package. Use one persistent local
state store/key per logical request. Private settings candidates come from the
additive renderer; preserve SSH/heredoc guards and runlog harvesters.

1. **Review:** inspect consumer drift, output ownership, required artifacts,
   report coverage, actual input staging and deployment file list. Capture the
   reviewed source/bundle identities. Add only thin process adapters/settings.
2. **Harmless cluster canary:** stage reviewed fixture code at a known absolute
   path using the existing deployment policy; configure a private WP1 profile
   and host. Run through the durable CLI with a synthetic manifest, then resume
   and collect. Require one record/event, matching IDs and verified artifacts.
   Use the same content-hashed Transport installer; no hand-built remote shell.
3. **Harness canaries:** test SessionStart/compact/resume, direct and nested
   code-mode denial, read-only commands, post-tool capture, long-job completion
   and visible hook failures for each actual app/CLI and shell. A failing path is
   advisory, not enforced. See the WP2 activation checklist.
4. **Licensed representative job:** independently compare normalized reports with
   native tool/scorer outputs, including a deliberately failing check and a missing
   artifact. Confirm workspace isolation and that the payload waits for its work.
5. **Behavioral acceptance:** record at least ten ordinary requests across fresh
   and resumed sessions without mentioning tracking. Include long jobs, failing
   jobs, interrupted reads and a known bypass. Require tracked or explicit refused
   outcomes, zero tracker reminders, and correct evidence claims. Record unsupported
   paths in the denominator. Synthetic hook replay does not satisfy this gate.

Record each attempt's consumer revision, harness/version, shell/tool path, profile
digest, expected outcome, actual outcome, task/job ID and whether a user correction
was required. Do not put private transcripts, tool logs or process inputs in a
public acceptance report. Current live gates are **not run**, not passed.

## Rollback

Disable/remove only the newly added hook handlers for the affected profile/project;
retain prior settings and unrelated guards. Keep task reservations, receipts,
collection summaries and tracker records. Running jobs continue under their
existing tracker lifecycle. Resume through the same saved state/profile; never
clear a reservation to retry. If a bundle regression requires an older version,
validate compatibility before explicitly re-shipping that known bundle, since
automatic content hashing is version selection, not a migration guarantee.

WP5's shared artifacts and local fixtures are ready for review. The four consumer
migrations and operational gates above are separate work, with xt011 and sky130
requiring more than adding a tracker reminder.

## First consumer pilot (2026-09-16)

The user selected tsmc28 first because tsmc65 is active. A narrow 30-file runtime
and hook migration preserves the consumer's unrelated settings and code. The
process adapter initially covers normal-mode calibrated-ADC no-op evidence only.
Windows SSH canaries passed attached/campaign cases and correctly failed missing
or disabled recorders; the shared legacy helper bytes were unchanged. No licensed
simulation or live harness acceptance is claimed.

Shared additions discovered during migration:

- Profiles can opt into `isolated_bundle: true`, selecting
  `~/.asicjobs/bundles/<content hash>` instead of overwriting the common bin.
  `ASICJOBS_BINDIR` tells attached payloads where their matching helpers live.
  Default transport behavior remains backward compatible.
- Optional `transport_mode` pins `winssh`, `wsl` or `ssh`; the selected host is
  unchanged. This avoids relying on an ambient setting when WSL SSH is unavailable.
- Hook routes may supply `argument_prefixes`, a list of exact leading argument
  lists, to guard a compute subcommand while preserving prepare/status commands.
  Other argument orderings/opaque shells are not inferred as covered.

See the consumer's `docs/tracked_adc_migration.md` and
`docs/howto/tracked_adc.md` for evidence, commands and pending acceptance gates.
