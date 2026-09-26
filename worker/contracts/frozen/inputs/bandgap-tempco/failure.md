# Failure report: `worker-bandgap-tempco-20260924T215226Z-9770-baseline-cluster-bandgap`

Status: **open**. Cause: **gate-fail** (derived by the tracker, not yet declared).

## Contract

- Profile `bandgap-dc` version `1` (spec2si-tsmc28), host `asic7`
- Required checks: dc-converged, predictions, saturation, tempco-reported; corners: tt_m40C, tt_27C, tt_125C
- Identities: request `e02dd7ba7bce`, manifest `276b3354b337`, source `fde4113c227b`

## Outcome

- Execution: done (rc 0); artifacts: tracker-verified; engineering: **fail**
- Checks: 9 passed, 3 failed
- Failed check: `tempco-reported/tt_m40C`
- Failed check: `tempco-reported/tt_27C`
- Failed check: `tempco-reported/tt_125C`

## Evidence

- Job `bandgap-dc-run-20260924T215232Z-df3e` (task `task-f3641972735c4b849be3bbc171121b83`); tracker records `~/.asicjobs/bandgap-dc-run-20260924T215232Z-df3e/{meta,status,result}.json`
- Workspace `/u/home/smandal/.spec2si/tsmc28/bandgap-dc/runs/task-f3641972735c4b849be3bbc171121b83`; collection [collection.json](collection.json)
- Log signatures: none

## Attempts

- 10 attempt(s) at this request; started 2026-09-24 21:52 UTC, collected 2026-09-24 21:53 UTC
- Earlier: `bandgap-dc-20260924-codex-004` job `bandgap-dc-run-20260924T164050Z-d01b`: failed / unchecked
- Earlier: `bandgap-dc-001` job `bandgap-dc-run-20260924T143758Z-c9d4`: done / pass
- Earlier: `bandgap-dc-004` job `bandgap-dc-run-20260924T144746Z-ff0e`: done / pass
- Earlier: `bandgap-dc-20260924-codex-001` job `bandgap-dc-run-20260924T162942Z-2661`: done / pass
- Earlier: `bandgap-dc-003` job `bandgap-dc-run-20260924T144353Z-8d42`: done / pass
- Earlier: `bandgap-dc-002` job `bandgap-dc-run-20260924T144239Z-bd1a`: done / pass
- Earlier: `bandgap-dc-20260924-codex-002` job `bandgap-dc-run-20260924T163322Z-973a`: failed / unchecked
- Earlier: `bandgap-dc-20260924-kickoff-01` job `bandgap-dc-run-20260924T163520Z-6711`: done / pass
- Earlier: `bandgap-dc-20260924-codex-003` job `bandgap-dc-run-20260924T163634Z-3a91`: failed / unchecked

## Cause

- Derived: **gate-fail**: the declared checks ran on verified artifacts and failed: tempco-reported/tt_m40C, tempco-reported/tt_27C, tempco-reported/tt_125C

## For exploration

- Contradicts: *not yet stated*
- Question: *not yet stated*

Record judgement with `jobs.workflow report --task-key worker-bandgap-tempco-20260924T215226Z-9770-baseline-cluster-bandgap --cause <class> --by human|agent [--contradicts ...] [--question ...] [--close ...]`. An agent may declare only gate-fail, tool-error, transport.
