# Failure report: `worker-ota-injected-fault-20260926T103328Z-93aa-baseline-cluster-ota`

Status: **open**. Cause: **gate-fail** (derived by the tracker, not yet declared).

## Contract

- Profile `ota6-schematic` version `1` (spec2si-sky130), host `asic7`
- Required checks: vout_min, vout_max, dc_gain, ugb, phase_margin, power, transfer_polarity; corners: tt
- Identities: request `fec73badec67`, manifest `0c3678b8edb2`, source `c053c6609666`

## Outcome

- Execution: done (rc 0); artifacts: tracker-verified; engineering: **fail**
- Checks: 6 passed, 1 failed
- Failed check: `power/tt`

## Evidence

- Job `ota6-schematic-run-20260926T103334Z-262d` (task `task-99158b49990045e7a7d7387f3c458ba1`); tracker records `~/.asicjobs/ota6-schematic-run-20260926T103334Z-262d/{meta,status,result}.json`
- Workspace `/u/home/smandal/.spec2si/sky130/ota6-schematic/runs/task-99158b49990045e7a7d7387f3c458ba1`; collection [collection.json](collection.json)
- Log signatures: none

## Attempts

- 15 attempt(s) at this request; started 2026-09-26 10:33 UTC, collected 2026-09-26 10:33 UTC
- Earlier: `ota6-schematic-asic6-20260924b` job `ota6-schematic-run-20260924T173234Z-8584`: done / pass
- Earlier: `ota6-schematic-20260924d` job `ota6-schematic-run-20260924T171741Z-d51d`: done / pass
- Earlier: `ota6-schematic-20260924f` job `ota6-schematic-run-20260924T172357Z-20fd`: done / pass
- Earlier: `gate-ota6-20260924` job `ota6-schematic-run-20260924T170650Z-3995`: done / pass
- Earlier: `ota6-schematic-20260924-fresh-01` job `ota6-schematic-run-20260924T173452Z-1f83`: done / pass
- Earlier: `ota6-schematic-20260924-async-01` job `ota6-schematic-run-20260924T173350Z-a23a`: done / pass
- Earlier: `reqkey-live-20260924` job `ota6-schematic-run-20260924T180838Z-184b`: done / pass
- Earlier: `ota6-schematic-20260924c` job `ota6-schematic-run-20260924T171435Z-ea92`: done / pass
- Earlier: `ota6-schematic-20260924g` job `ota6-schematic-run-20260924T173017Z-3d1c`: done / pass
- Earlier: `ota6-schematic-20260924h` job `ota6-schematic-run-20260924T173315Z-47e1`: done / pass
- Earlier: `ota6-schematic-asic6-20260924` job `ota6-schematic-run-20260924T171352Z-d314`: done / pass
- Earlier: `ota6-schematic-20260924b` job `ota6-schematic-run-20260924T171207Z-f492`: done / pass
- Earlier: `ota6-schematic-20260924e` job `ota6-schematic-run-20260924T171829Z-d9e3`: done / pass
- Earlier: `ota6-schematic-20260924-connection-safe-01` job `ota6-schematic-run-20260924T173705Z-3066`: done / pass

## Cause

- Derived: **gate-fail**: the declared checks ran on verified artifacts and failed: power/tt

## For exploration

- Contradicts: *not yet stated*
- Question: *not yet stated*

Record judgement with `jobs.workflow report --task-key worker-ota-injected-fault-20260926T103328Z-93aa-baseline-cluster-ota --cause <class> --by human|agent [--contradicts ...] [--question ...] [--close ...]`. An agent may declare only gate-fail, tool-error, transport.
