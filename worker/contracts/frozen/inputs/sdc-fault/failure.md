# Failure report: `worker-sdc-injected-fault-20260926T103715Z-dee5-baseline-cluster-syn`

Status: **open**. Cause: **gate-fail** (derived by the tracker, not yet declared).

## Contract

- Profile `digital-smoketest-syn` version `1` (spec2si-tsmc65), host `asic8`
- Required checks: elaborate, preflight, synthesize, export, netlist; corners: tt
- Identities: request `bf685272b29e`, manifest `579925e1326d`, source `d9d25f562682`

## Outcome

- Execution: done (rc 0); artifacts: tracker-verified; engineering: **fail**
- Checks: 2 passed, 3 failed
- Failed check: `synthesize/tt`
- Failed check: `export/tt`
- Failed check: `netlist/tt`

## Evidence

- Job `digital-smoketest-syn-run-20260926T103737Z-d91d` (task `task-064f8b3ffe9844889d47398c2e88fc57`); tracker records `~/.asicjobs/digital-smoketest-syn-run-20260926T103737Z-d91d/{meta,status,result}.json`
- Workspace `/u/home/smandal/Documents/ms_pilot/analog/work/tracked_jobs/digital-smoketest-syn/runs/task-064f8b3ffe9844889d47398c2e88fc57`; collection [collection.json](collection.json)
- Log signatures: license 7

## Attempts

- 6 attempt(s) at this request; started 2026-09-26 10:37 UTC, collected 2026-09-26 10:39 UTC
- Earlier: `smoketest-syn-20260924-d` job `digital-smoketest-syn-run-20260924T140822Z-b8bd`: done / pass
- Earlier: `smoketest-syn-001` job `digital-smoketest-syn-run-20260924T133631Z-3761`: done / pass
- Earlier: `smoketest-syn-20260924-e` job `digital-smoketest-syn-run-20260924T140956Z-707c`: done / pass
- Earlier: `smoketest-syn-20260924-b` job `digital-smoketest-syn-run-20260924T135846Z-db9c`: done / pass
- Earlier: `smoketest-syn-20260924-c` job `digital-smoketest-syn-run-20260924T140403Z-900c`: done / pass

## Cause

- Derived: **gate-fail**: the declared checks ran on verified artifacts and failed: synthesize/tt, export/tt, netlist/tt

## For exploration

- Contradicts: *not yet stated*
- Question: *not yet stated*

Record judgement with `jobs.workflow report --task-key worker-sdc-injected-fault-20260926T103715Z-dee5-baseline-cluster-syn --cause <class> --by human|agent [--contradicts ...] [--question ...] [--close ...]`. An agent may declare only gate-fail, tool-error, transport.
