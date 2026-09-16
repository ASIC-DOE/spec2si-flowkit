<!--docmeta
title: License checker — FlexLM seat status across a cluster's tool families
genre: reference
status: active
area: top
owner: soumyajit
updated: 2026-08-31
summary: A CLI + Tkinter GUI that queries every configured FlexLM license server on a cluster over ssh, groups seats by tool family (Cadence, Calibre, HSPICE, MATLAB, ...), and shows who holds a feature's seats and for how long only on request (`who <feature>` / a GUI click) — never in the default summary. Ships with BNL inst.bnl.gov's 7 servers as the default cluster, confirmed live 2026-08-31, but every field (ssh host, license-server host, lmstat path, port-to-family map) is overridable via CLI flags, a JSON config file, or the GUI's Cluster dialog, so it is not BNL-specific. The remote side needs no tcsh/Cadence-env activation: lmstat is called with an explicit path and an explicit `-c host:port`, so the query runs over a bare `ssh <host> /bin/sh -s`, sidestepping CLUSTER.md's tcsh history-expansion/quoting hazard class entirely.
-->

# License checker

Query a cluster's FlexLM license servers, grouped by tool family, with
per-feature holder/duration detail available on request.

```bash
python3 -m licenses.cli list                    # seat counts by tool family
python3 -m licenses.cli who Xcelium_SC_DMS_Option
python3 -m licenses.cli who xcelium_dms          # alias -- see lm.ALIASES
python3 -m licenses.gui                          # same data, clickable tree
```

`list` never shows usernames — only issued/in-use/free counts per feature,
grouped under its tool family. `who` (CLI) and clicking a feature row (GUI)
are the only paths that show holders, each with a computed "held" duration.

## Why this needs no tcsh

The BNL cluster's login shell is tcsh, and its EDA toolchain is activated by
sourcing csh setup scripts (see `CLUSTER.md` in each process port) — but
only to put `lmstat` on `PATH` and set `LM_LICENSE_FILE`. Passing an
explicit binary path and an explicit `-c <port>@<host>` needs neither, so
the remote side here is a **plain POSIX `/bin/sh` script** piped over
`ssh <host> /bin/sh -s` (never an interpolated command string). That is the
one thing borrowed deliberately from `spec2si-tsmc65/deployment/bnl/jobs/
remote.py`'s `Transport`: piping to `/bin/sh -s` means the login shell never
sees the script, so tcsh's history expansion (`!` in an inline command) and
`Ambiguous output redirect` never come up.

## Pointing it at a different cluster

Every cluster-specific value lives in `lm.DEFAULT_SETTINGS` and layers as
CLI flags > config file > built-in default:

```bash
python3 -m licenses.cli config --save \
    --host mycluster \
    --lic-server lic.example.com \
    --lmstat /opt/cadence/bin/lmstat \
    --server 27000:Cadence --server 1717:Mentor
```

`--save` writes the effective settings to `~/.licenses_checker.json` (or
`--config <path>`), so later runs need no flags. Passing any `--server`
replaces the whole port/family-label list — it does not merge with BNL's.
The GUI has the same thing under **Cluster...**, with a Save + Apply button.

## What ships as the BNL default

Confirmed live 2026-08-31 by querying every port on `iolicense2.inst.bnl.gov`
directly (`CLUSTER.md` already documented 7180/7183/7184/7186; 7182/7188/7190
were confirmed in this pass):

| Port | Family | Vendor daemon |
|---|---|---|
| 7180 | Synopsys (HSPICE) | `snpslmd` |
| 7182 | MathWorks (MATLAB/Simulink) | `MLM` |
| 7183 | Cadence | `cdslmd` |
| 7184 | Siemens (Calibre) | `mgcld` |
| 7186 | xgdsplot | — (observed unreachable 2026-08-31; still listed, marked `UNREACHABLE` rather than dropped) |
| 7188 | Cliosoft (SOS) | `cliolmd` |
| 7190 | DVT (Verilog/VHDL IDE) | `dvtlmd` |

`lmstat`'s absolute path is resolved once per query: try `DEFAULT_SETTINGS
["lmstat"]`, else glob `lmstat_glob` and validate the first hit against the
license-server host, else fall back to `lmstat` on `PATH`. Queries are
serial with a 1s pace between servers — FlexLM rate-limits concurrent
queries from one client (measured on this cluster: a naive concurrent sweep
is *slower*, not faster), so this stays serial by design.

## Files

- `lm.py` — settings load/save, remote script construction, the ssh
  transport, `lmstat -a` parsing, and duration formatting. No GUI/CLI
  dependencies; safe to import standalone.
- `cli.py` — argparse CLI (`list`, `who`, `config`).
- `gui.py` — Tkinter tree view + Cluster settings dialog.
- `__main__.py` — `python3 -m licenses` shorthand for the CLI.

Pure stdlib, Python 3.6+, on both the local and remote ends.
