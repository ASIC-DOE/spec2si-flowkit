# Failure report: `worker-buffer-linearity-20260926T101624Z-db56-baseline-cluster-buffer`

Status: **open**. Cause: **gate-fail**.

## Contract

- Profile `buffer-current` version `1` (spec2si-xt011), host `asic7`
- Required checks: sweep-coverage, current-linearity; corners: tm, wp, ws, wo, wz
- Identities: request `7fc746c10d94`, manifest `95d68c386e4d`, source `305fb66b302a`

## Outcome

- Execution: done (rc 0); artifacts: tracker-verified; engineering: **fail**
- Checks: 5 passed, 5 failed
- Failed check: `current-linearity/tm`
- Failed check: `current-linearity/wp`
- Failed check: `current-linearity/ws`
- Failed check: `current-linearity/wo`
- Failed check: `current-linearity/wz`

## Evidence

- Job `buffer-current-run-20260926T101630Z-974f` (task `task-ad73fe2cb5d74ed8b62461001bc42e49`); tracker records `~/.asicjobs/buffer-current-run-20260926T101630Z-974f/{meta,status,result}.json`
- Workspace `/u/home/smandal/.spec2si/xt011/buffer-current/runs/task-ad73fe2cb5d74ed8b62461001bc42e49`; collection [collection.json](collection.json)
- Log signatures: none

## Attempts

- 11 attempt(s) at this request; started 2026-09-26 10:16 UTC, collected 2026-09-26 10:17 UTC
- Earlier: `gate-buffer-x1-20260924` job `buffer-current-run-20260924T170649Z-8b9b`: done / fail
- Earlier: `buffer-x1-20260924-run9` job `buffer-current-run-20260924T173821Z-3f21`: done / fail
- Earlier: `buffer-x1-20260924-run8` job `buffer-current-run-20260924T173603Z-4125`: done / fail
- Earlier: `buffer-x1-20260924-run4` job `buffer-current-run-20260924T171803Z-e153`: done / fail
- Earlier: `buffer-x1-20260924-run3` job `buffer-current-run-20260924T171608Z-5743`: done / fail
- Earlier: `buffer-x1-20260924-run2` job `buffer-current-run-20260924T171413Z-3cef`: done / fail
- Earlier: `buffer-x1-20260924-run7` job `buffer-current-run-20260924T173335Z-e6b9`: done / fail
- Earlier: `buffer-x1-20260924-run5` job `buffer-current-run-20260924T172051Z-5674`: done / fail
- Earlier: `buffer-x1-20260924-run6` job `buffer-current-run-20260924T173042Z-14e7`: done / fail
- Earlier: `buffer-x1-20260924-run10` job `buffer-current-run-20260924T174158Z-fabe`: done / fail

## Cause

- Derived: **gate-fail**: the declared checks ran on verified artifacts and failed: current-linearity/tm, current-linearity/wp, current-linearity/ws, current-linearity/wo, current-linearity/wz

## For exploration

- Contradicts: *not yet stated*
- Question: *not yet stated*

Record judgement with `jobs.workflow report --task-key worker-buffer-linearity-20260926T101624Z-db56-baseline-cluster-buffer --cause <class> --by human|agent [--contradicts ...] [--question ...] [--close ...]`. An agent may declare only gate-fail, tool-error, transport.
