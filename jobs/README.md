<!--docmeta
title: jobs — the cluster transport, host chooser and process scanner
genre: overview
status: active
area: top
owner: soumyajit
updated: 2026-09-12
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
| `cli.py` | `aj run / top / watch / wait / why / verify` |
| `bin/` | what ships to the cluster once and runs there: `runjob`, `progress.py`, `jobrec.py`, `license.py`, `report.sh` |
| `test_*.py` | run any of them directly; `test_harness_can_fail.py` is the negative control over the others |

The README that explains the design, the incidents behind each invariant
and the phased rollout stays with the port that wrote it (spec2si-tsmc65,
beside its copy of this package), because it names that port's plan
documents and this page must not carry a link that is dead in three of the
four places it lands.
