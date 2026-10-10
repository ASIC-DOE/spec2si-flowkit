<!--docmeta
title: jobs — the cluster transport, host chooser and process scanner
genre: overview
status: active
area: top
owner: soumyajit
updated: 2026-10-10
summary: The vendored source of every port's `deployment/bnl/jobs/`: an ssh round trip that cannot be corrupted by quoting (a script over stdin, values bound through quoted heredocs), the host chooser, the licence-holding process scanner, the job CLI, and the bundle the cluster side runs. This page is only the map; the implementation plan and the incident record live with spec2si-tsmc65, which wrote it.
-->

# jobs — the cluster transport

Vendored from here into every port's deployment area (ADR-0004). The
artifact browser reads the cluster through it (`browse/cluster.py`), and
the `aj` CLI drives detached jobs with it.

| module | what |
|---|---|
| `remote.py` | `Transport.run_sh(script)` — the script goes over stdin to `/bin/sh -s`, values are bound through quoted heredocs, nothing is interpolated into argv. Every result is `KNOWN`, `STALE` or `UNKNOWN`, never a guess |
| `hosts.py` | which cluster host performs a read or runs a job, and why |
| `procscan.py` | the licence-holding process scanner the browser's Interactions pane shows |
| `workflow.py` | the durable tracked-job CLI: `start / status / resume / collect / tasks`, plus `report` and `failures` for failure reports |
| `state.py` | the local task store: intent written before dispatch, the task id as the tracker's request key (a lost acknowledgement is resolved, never resubmitted) |
| `failure.py` | structured failure reports: a `collect` that is not a verified pass writes `failure.json`/`failure.md` (contract, outcome, evidence, attempts, cause, the question for exploration) |
| `pilot.py` | package, deploy and run a repo's adapters as immutable cluster snapshots, one per tracked workload; `segments()` runs one case's independent segments and requeues licence-queue timeouts |
| `cli.py` | `aj run / top / watch / wait / why / verify` |
| `bin/` | what ships to the cluster once and runs there: `runjob`, `progress.py`, `jobrec.py`, `license.py`, `report.sh` |
| `test_*.py` | run any of them directly; `test_harness_can_fail.py` is the negative control over the others |

## More than one tracked workload in a port

The first adapter stays at `deployment/bnl/tracked_job.py`, and its SPEC
names no path. Its package, profile and digest are the same as before.
Every further adapter lives at `deployment/bnl/tracked_jobs/<id>.py` and
says so in its SPEC (`adapter="deployment/bnl/tracked_jobs/<id>.py"`).
It puts `deployment/bnl` on `sys.path` itself before `from jobs import
pilot`. Each adapter is packaged with only its own file and its own `files`,
staged to its own snapshot and deployed to its own `--output`. So editing
one adapter never changes another's snapshot or profile. `deploy` refuses
an output that holds another adapter's profile, and it refuses a snapshot
staged for another adapter. A shared helper module must be listed in the
`files` of every adapter that imports it.

`case_checks` narrows what one case reports, for example
`{"pex_tt": {"checks": ["sndr", "enob"], "corners": ["tt"]}}`. Each value
is a subset of the SPEC's `checks`/`corners`, and a case it does not name
reports the full set. The profile then carries
`engineering_report.per_case`. The workflow resolves the requested case from
the request digest, so `case` must be the profile's only parameter. It hands
the cluster's evidence reader that one case's checks and corners, in the
shape the reader has always read, so the installed cluster bundle is
unchanged.

`pilot.segments(work, slots, ...)` runs one case's independent segments on
the job's host. Each segment runs in its own process group, owned by the job
and stopped with it. A segment whose own output, or a tool log it touched,
shows a licence denial (`report.sh`'s SIG_LICENSE, which includes spectre's
`+lqtimeout` expiry SPECTRE-209) is queued again, not failed. If it is
still denied after its retries, the case is refused with `LicenseWait`
before anything is scored. Segments spread over several hosts are not
supported. A process started over ssh on another host is outside the job's
process group, so the tracker could neither stop it nor account for it.

The README that explains the design, the incidents behind each invariant
and the phased rollout stays with the port that wrote it (spec2si-tsmc65,
beside its copy of this package), because it names that port's plan
documents and this page must not carry a link that is dead in three of the
four places it lands.
