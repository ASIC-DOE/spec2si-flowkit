# Failure report: `worker-f17-ota-truncated-report-20260926T133430Z-9b59-baseline-cluster-ota`

Status: **open**. Cause: **unclassified** (derived by the tracker, not yet declared).

## Contract

- Profile `ota6-schematic` version `1` (spec2si-sky130), host `asic7`
- Required checks: vout_min, vout_max, dc_gain, ugb, phase_margin, power, transfer_polarity; corners: tt
- Identities: request `fec73badec67`, manifest `54573956a59a`, source `87e6115c0c41`

## Outcome

- Execution: failed (rc 1); artifacts: incomplete; engineering: **unchecked**
- Checks: 0 passed, 0 failed
- Issue: required-artifacts-missing-unstamped-or-incomplete

## Evidence

- Job `ota6-schematic-run-20260926T133436Z-d3c3` (task `task-20705c19d97641a7acf8b5a18d72a8b8`); tracker records `~/.asicjobs/ota6-schematic-run-20260926T133436Z-d3c3/{meta,status,result}.json`
- Workspace `/u/home/smandal/.spec2si/sky130/ota6-schematic/runs/task-20705c19d97641a7acf8b5a18d72a8b8`; collection [collection.json](collection.json)
- Log signatures: traceback 1

## Attempts

- 24 attempt(s) at this request; started 2026-09-26 13:34 UTC, collected 2026-09-26 13:34 UTC
- Earlier: `worker-f7-ota-fault-B-20260926T120950Z-9681-session-cluster-ota` job `ota6-schematic-run-20260926T121106Z-f067`: done / pass
- Earlier: `worker-f7-ota-fault-20260926T120949Z-7008-round-1-cluster-ota` job `ota6-schematic-run-20260926T121025Z-1b28`: done / pass
- Earlier: `ota6-schematic-asic6-20260924b` job `ota6-schematic-run-20260924T173234Z-8584`: done / pass
- Earlier: `ota6-schematic-20260924d` job `ota6-schematic-run-20260924T171741Z-d51d`: done / pass
- Earlier: `ota6-schematic-20260924f` job `ota6-schematic-run-20260924T172357Z-20fd`: done / pass
- Earlier: `gate-ota6-20260924` job `ota6-schematic-run-20260924T170650Z-3995`: done / pass
- Earlier: `worker-f16-ota-wrong-top-20260926T133407Z-6f89-baseline-cluster-ota` job `ota6-schematic-run-20260926T133412Z-8f5f`: failed / unchecked
- Earlier: `ota6-schematic-20260924-fresh-01` job `ota6-schematic-run-20260924T173452Z-1f83`: done / pass
- Earlier: `worker-ota-injected-fault-20260926T103328Z-93aa-baseline-cluster-ota` job `ota6-schematic-run-20260926T103334Z-262d`: done / fail
- Earlier: `worker-f7-ota-fault-20260926T120517Z-cb47-round-1-cluster-ota` job `ota6-schematic-run-20260926T120602Z-0cec`: done / pass
- Earlier: `ota6-schematic-20260924-async-01` job `ota6-schematic-run-20260924T173350Z-a23a`: done / pass
- Earlier: `reqkey-live-20260924` job `ota6-schematic-run-20260924T180838Z-184b`: done / pass
- Earlier: `ota6-schematic-20260924c` job `ota6-schematic-run-20260924T171435Z-ea92`: done / pass
- Earlier: `worker-f7-ota-fault-B-20260926T120818Z-4f6e-session-cluster-ota` job `ota6-schematic-run-20260926T120931Z-ffa8`: done / pass
- Earlier: `ota6-schematic-20260924g` job `ota6-schematic-run-20260924T173017Z-3d1c`: done / pass
- Earlier: `ota6-schematic-20260924h` job `ota6-schematic-run-20260924T173315Z-47e1`: done / pass
- Earlier: `ota6-schematic-asic6-20260924` job `ota6-schematic-run-20260924T171352Z-d314`: done / pass
- Earlier: `ota6-schematic-20260924b` job `ota6-schematic-run-20260924T171207Z-f492`: done / pass
- Earlier: `worker-f7-ota-fault-B-20260926T120622Z-6f7d-session-cluster-ota` job `ota6-schematic-run-20260926T120738Z-151c`: done / pass
- Earlier: `ota6-schematic-20260924e` job `ota6-schematic-run-20260924T171829Z-d9e3`: done / pass
- Earlier: `worker-f7-ota-fault-20260926T120818Z-492a-round-1-cluster-ota` job `ota6-schematic-run-20260926T120847Z-7300`: done / pass
- Earlier: `worker-ota-injected-fault-20260926T103328Z-93aa-round-1-cluster-ota` job `ota6-schematic-run-20260926T103414Z-dff5`: done / pass
- Earlier: `ota6-schematic-20260924-connection-safe-01` job `ota6-schematic-run-20260924T173705Z-3066`: done / pass

## Cause

- Derived: **unclassified**: log signatures: traceback 1; judge the cause

## For exploration

- Contradicts: *not yet stated*
- Question: *not yet stated*

Record judgement with `jobs.workflow report --task-key worker-f17-ota-truncated-report-20260926T133430Z-9b59-baseline-cluster-ota --cause <class> --by human|agent [--contradicts ...] [--question ...] [--close ...]`. An agent may declare only gate-fail, tool-error, transport.
