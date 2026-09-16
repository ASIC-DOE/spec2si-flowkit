<!--docmeta
title: WP0 — cluster-job tracker source and distribution audit
genre: finding
status: active
area: top
owner: soumyajit
updated: 2026-09-16
summary: Upstream flowkit already owns jobs; a stale local checkout explained the apparent source gap. Exact restoration provenance, consumer comparisons, disclosure boundaries and offline validation.
-->

# WP0 — tracker ownership resolved

**The canonical source is `spec2si-flowkit/jobs/`.** It was introduced by upstream commit `c5b8d6a3715d36954257529186bda3835556bbc3`. The existing distribution maps 19 code/test files into consumers' `deployment/bnl/jobs/`. A new import from a private process repository is unnecessary.

WP0 is complete for source ownership, targeted restoration and offline distribution validation. It does not establish default assistant use or live cluster adoption; those remain subsequent work packages in [Task 9.1](task_9_1_tracker_default_scope.md).

## Evidence and the earlier false lead

The working branch started at `8b9a34b`, with existing uncommitted research/memory work. Its cached `origin/main` was `da471d3`. Searching local refs and unreachable objects found no tracker source, while tsmc28's instructions correctly described flowkit vendoring. The earlier scope called this an ownership inconsistency. A fresh remote inspection resolved it as a stale-checkout problem.

Fetching `origin/main` exposed `df45995b7b56732ce113afe5c435c6540a71dbc2`, containing `jobs/`, its README and the mappings. Its decision is `docs/decisions/0004-browse-and-transport-vendored.md`. The local branch also has an independently authored ADR-0004 about repository mirrors; the publication merge resolved that collision by assigning the mirror decision ADR-0005.

The audit fetched refs but did not merge, reset, rebase or overwrite existing working changes. It restored only the 20 files in upstream `jobs/` and copied the 19 jobs mappings into the local `sync.py`. The README is upstream-only; the consumer's longer process-specific README is deliberately not vendored. Unrelated upstream browser, routing, housekeeping and documentation changes remain outside this work.

[Source manifest](evidence/job_tracker_sources.json) records the exact upstream revision, SHA-256 of every restored file, source/destination mapping, consumer HEADs and working-tree comparisons. The restored `jobs/` bytes match the upstream Git blobs exactly. This is a provenance snapshot, not a prohibition on subsequent intentional source changes.

## Consumer comparison

| Consumer | Local comparison of the 19 mapped files |
|---|---|
| tsmc65 | 18 byte-identical; `bin/runjob` differs only by CRLF checkout line endings |
| tsmc28 | 18 byte-identical; same CRLF-only `bin/runjob` difference |
| XT011 | All 19 paths absent |
| SKY130 | All 19 paths absent |

The two TSMC copies match each other across all 19 files. No consumer-only logic patch needs reconciliation in this snapshot. Local absence does not prove no external wrapper is used; checkout state does not measure deployed usage. No live cluster records were queried.

The extensionless launcher was not covered by the local `*.sh` LF rule. Added `jobs/bin/runjob text eol=lf` to flowkit's attributes. Consumer attributes/line endings remain unchanged; rollout must address the same issue there. Existing transport normalization does not make byte-hash drift disappear.

## Licensing and disclosure

The private process repository's root license is all-rights-reserved; it was not used as authority for a new Apache import. Source was restored from existing upstream flowkit under its repository licensing arrangement. This work does not newly relicense private-port material or publish anything remotely.

The existing tracker is site-specific shared infrastructure, not site-neutral. Inspection identified host defaults in `remote.py`/`hosts.py`, server and executable-location defaults in `procscan.py`, feature/port aliases in `bin/license.py`, and live-derived site examples in comments/tests. These already occur upstream; WP0 preserves them exactly rather than claiming sanitization.

The restored set consists of Python/shell tracker code, tests and its overview, not PDK cards, design decks, GDS or proprietary executables. This is a scoped source audit, not a general legal/security certification. Do not add new process values or raw logs in later work. WP1's explicit profiles should override ambient defaults; broader site-neutralization can be reviewed separately if distribution beyond the shared site is required.

## Validation

All eight restored `jobs/test_*.py` scripts pass under WSL Python 3.12 with bytecode writes disabled: CLI 62 checks, hosts 32, recorder 37, license 15, progress 34, remote/lifecycle 92, process scanner 31 tests. The test-harness audit found zero files whose tests could not fail. Counts retain the upstream scripts' reporting units rather than being combined.

Remote tests use an injected local POSIX-shell runner and temporary stores, not cluster access or EDA licenses. Python 3.6 syntax is checked separately with AST parsing; this is not execution on the cluster interpreter.

Three new [distribution tests](../conformance/test_jobs_distribution.py) pass: complete/unique mapping, LF/no-BOM bundle, and disposable-consumer round trip. Negative controls modify one file and remove another, requiring `DRIFTED` and `MISSING`. Real consumers were never vendored or edited.

## Handoff to WP1

- Add the entry point at **`jobs/workflow.py`**, not a second flowkit source tree under `deployment/bnl/jobs/`.
- Reuse `Transport`, records and distribution conventions; extend `sync.py` as tested shared files land.
- Keep proposed harness templates in flowkit's `integrations/cluster_jobs/`; no machine-level or consumer installation in this work.
- Preserve visibility of known gaps: best-effort registration, `UNSTAMPED` returning zero, separate flow/job verdicts and no request key. WP0 did not fix them.
- A full branch reconciliation must later preserve existing work and reconcile duplicate ADR numbering; it is not required for this targeted source recovery.

The next slice is the small shared entry point and profile contract. There is now a verified canonical source to extend.
