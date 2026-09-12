<!--docmeta
title: How to browse your results
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-09-12
summary: Start the artifact browser on this checkout, view a layout or a waveform, cross-probe a schematic, read the design record (which captured view is this file, what does a cell.json bind), list the cluster's trees, and check what an agent did or which processes hold licences. What a port declares in roots.json (its trees, its renderer, its badge sources), and what the listing says when it has nothing to badge. The browser is vendored from spec2si-flowkit and identical in every port.
-->

# How to browse your results

**One command, in any checkout:**

```bash
python browse/server.py
```

Opens `http://127.0.0.1:8730` on **this** checkout. Every tree the port's
`browse/roots.json` names is a root; listings are badged from the files the
flows write; a layout renders, a waveform plots, a `cell.json` opens as the
design record. The browser is read-only, stdlib-only, and vendored from
spec2si-flowkit byte-identically into every port — a fix lands once.

`python browse/launch.py <name>` serves a *sibling* checkout by prefix from
wherever you are (`--list` says what can be served and what is up); several
run side by side on consecutive ports. That is a convenience, not how a port
is served: each checkout serves itself.

## What it shows

| Pane | What you get |
|---|---|
| Results | every root the port declared, badged; click through to its files |
| Interactions | the live job panel, agent activity, licence-holding processes (through the vendored `deployment/bnl/jobs/`) |
| a layout (`.gds`) | rendered PNG through the port's renderer, cropped by window where the renderer takes one; DRC markers overlaid from the `.lyrdb`; the KLayout handoff on Windows |
| a schematic (`.svg`) | the engine's own drawn model; click a net to dim everything else, in the schematic and the paired layout |
| a waveform (`.psf`/nutascii) | tran/dc/ac/noise/stb/op, decimated and re-plotted as you zoom — on a port whose engine has a transient reader; a port without one says so |
| Monte Carlo | the min/max band across iterations, not sigma |
| two GDS files | a geometric XOR — same shapes, different coordinates, or genuinely different |
| the `design` root | the manifest tree, badged with `designdb --check --fast` (the gate CI runs); each library directory says how many cells it holds |
| a `cell.json` | the views table with the bound view marked, the provenance, the BOM as links to sibling cells, `bom_external` as text — and a warning when a newer generation of a bound view has not been adopted |
| any local file | which captured view it is, by md5 against the manifests (*captured as `<lib>/<cell>` view `stream`, bound*), or *in no captured view*; a file that is a view's tracked origin with differing bytes is called out as drift |
| the cluster's `analog/oa/` | each library directory badged *design library · N cells* or *scratch OA library*, from the served repo's record and its `oa_dest.py` |

Nothing here re-derives a verdict. It reads what the flow already wrote and
draws it; live DRC/LVS/simulation still decide.

**Silence is not clean.** A listing whose rows carry no badge says *no badge
source here* and names what it looks for. Until 2026-09-12 the badge sources
were one port's flowrun files only, so two ports browsed unbadged for a month
and every one of their listings looked like a directory of runs that all
passed.

## What a port declares

`browse/roots.json` is the ONE file that differs per port, and it is never
vendored. It may be a plain list of roots, or an object with a `roots` list
and three optional keys — a declaration per repo instead of a list of places
to guess:

```json
{"roots": [{"name": "results", "path": "analog/results"},
           {"name": "design",  "path": "design"},
           {"name": "cluster", "kind": "remote", "host": "asic7",
            "fs": "bnl-home", "group": "BNL cluster",
            "path": "~/Documents/<area>"}],
 "renderer": {"path": "<repo-relative path to the port's render_gds.py>",
              "argv": ["{gds}", "{top}", "{out}"], "window": false},
 "klayout":  "<repo-relative path to the port's klayout_open.ps1>",
 "badges":   {"*-asic7.json": "verdict", "*.lvs.report": "verdict_text"}}
```

* **`roots`** — a name and a repo-relative path; a remote root names a host
  and the filesystem it is on (`fs`), so two hosts that see the same home
  share one cache. `roots.local.json` beside it, untracked, overrides or
  extends by name, for a machine-specific path.
* **`renderer`** — a repo-relative path, or an object naming the argument
  order. `{top}` is filled from the stream itself (the one structure nothing
  instantiates; two is a refusal). `window: false` says it takes no crop.
  Without a declaration the browser tries the two conventional locations —
  a `render_gds.py` in the engine's layout directory, then in a port's flat
  layout directory — and names what it looked for when neither exists. The
  same declaration reaches the cluster: a remote render looks for the
  declared path above the layout and calls it the declared way.
* **`klayout`** — the launcher script, same rules.
* **`badges`** — extra badge sources, file name or glob → reader
  (`report`, `manifest`, `status`, `verdict`, `cell`, `verdict_text`). A
  glob applies to file entries; a directory is badged from exact names.
  An unknown reader or key is refused at startup rather than ignored.

Built in, in precedence order: `report.json`, `manifest.json`,
`status.json` (flowrun stamps), `result.json`, `pex.json`, `schematic.json`
(any scored file with a `verdict`), and `cell.json` (the design record's
bindings).

## Before your first run

Nothing to set up locally — `browse/` reads local files directly. A
**remote** listing, render or waveform fetch goes through
`browse/cluster.py` → `deployment/bnl/jobs/remote.py` (vendored beside it)
and needs whatever cluster access the port's deployment notes already set
up: the ssh alias for the host and, for a remote render, a python with
matplotlib on the cluster.

## When something looks wrong

1. **The page says it's stale.** It compares its own build id against the
   server's — reload.
2. **A picture looks wrong but you can't tell why.** Don't eyeball it: a
   `getBBox()` sweep over the drawn elements finds a bad frame in seconds,
   and two views have shipped broken because "I can't screenshot this" was
   mistaken for "I can't check this."
3. **A viewer shows nothing at all.** Treat that as a parse bug, not an
   empty file, until proven otherwise — a hardcoded axis name has produced a
   confidently empty plot before.
4. **"renderer not found".** The port has not declared one and keeps it at
   neither conventional path — declare it in `roots.json`.
5. **You edited `server.py`.** Edit it in the flowkit, not here: the copy is
   hash-gated. The whole UI is JavaScript inside a Python string, so run
   `python browse/test_browse.py` before trusting it — one escaped backslash
   away from a `SyntaxWarning` that only fires at runtime.

## Going deeper

This page is the onboarding path. The architecture, the phase history and
every trap paid for are in the visual interaction plan kept with the port
that wrote the browser (spec2si-tsmc65); the survey that made it
configuration-driven is beside it. For exact flags on any script named
above, see the **Commands** chapter of the port's generated reference.
