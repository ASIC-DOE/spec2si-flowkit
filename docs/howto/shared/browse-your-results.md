<!--docmeta
title: How to browse your results
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-10-15
summary: Start the artifact browser on this checkout, or the hub over every checkout; then look at a layout (drawn locally or on the cluster), a waveform, a spectral response, a Markdown doc or the design record, compare two layouts, follow the cluster's jobs and what an agent is doing, and share a deep link. What a port declares in browse/roots.json (its trees and their roles, its renderer — including one shipped to the cluster — its badge sources, its scratch and legacy OA libraries), and what to do when something looks wrong. Vendored from spec2si-flowkit; identical in every port.
-->

# How to browse your results

The **artifact browser** is a read-only web page over a repository's results:
listings badged with what the flow concluded, layouts and waveforms drawn,
the design record explained, the cluster's trees one click away. It is
vendored from spec2si-flowkit, byte-identical in every port, and needs
nothing installed beyond Python.

## Quick start

In any port checkout:

```bash
python browse/server.py
```

It opens `http://127.0.0.1:8730` on **this** repository; the header and the
tab title name the repo. Each repo's browser shows only that repo — open
another repo's browser for its files.

To see **every** repo at once, start the hub instead, from any checkout:

```bash
python browse/hub.py
```

It opens `http://127.0.0.1:8729`: one row per repo with what it is, its
branch and how far it is ahead of or behind its upstream (as of its last
fetch), whether its browser is running, and **Open** / **Start** buttons.
Start runs that repo's own `browse/server.py`. Below the table is the
Jobs / Processes / Agent strip, once for the whole machine. The hub itself
reads no repository files.

## I want to…

| …do this | …do this in the browser |
|---|---|
| look at a layout | click a `.gds`. A local stream renders here; a cluster stream renders **on the cluster** and only the picture comes back. A `.lyrdb` beside it overlays the DRC markers; on Windows, **KLayout** opens the real thing |
| compare two layouts | open one `.gds` and pick the other under **XOR**: same shapes, same shapes at different coordinates, or genuinely different |
| look at a simulation | click a `.psf` or nutascii file: tran/dc/ac/noise/stb/op, re-plotted as you zoom; Monte Carlo runs show the band across iterations. Needs the port's own transient reader; a port without one says so |
| look at a filter response | click a response JSON (`frequency_hz` + `transfer`): transmission in dB and the phase against wavelength, with the scored evaluations beside it |
| read a doc | click a `.md`: it renders as the docs site would, links to other docs keep working, and the source is one click away |
| know what a file *is* | open it: the pane says which captured view it is in the design record (`<lib>/<cell>` view, bound or not), or that it is in none |
| check a cell's design record | open its `cell.json`: the views with the bound one marked, provenance, the bill of materials as links, and a warning when a newer generation has not been adopted |
| find the cluster's copy | open a cluster root (grouped under *BNL cluster*). OA libraries, the content store, the design record and work trees are each labelled for what they are — see the table below |
| see what is running on the cluster | **Jobs** tab → Refresh (tick *watch* to keep it updating) |
| find a stale licence-holding tool | **Processes** tab → Scan. Nothing is killed without a second, confirmed click |
| see what an agent is doing | **Agent** tab: the live state, the turns, the files it touched. Sessions started in the parent directory (e.g. `C:/dev`) that worked in this repo are listed too |
| send someone the exact view | copy the address bar: `#r=<root>&p=<folder>&v=<file>` opens the same view |

## What the labels mean

Nothing here re-derives a verdict: badges are read out of the files the
flow already wrote, and live DRC/LVS/simulation still decide. **A listing
with no badges says so** (*no badge source here*) and names what it looks
for — an unbadged listing is not a clean one.

| Where | Label | Meaning |
|---|---|---|
| any listing | PASS / FAIL / DRC CLEAN / LVS … | read from `report.json`, `manifest.json`, `status.json`, any JSON with a `verdict`, `cell.json`, or a source the port declared |
| the `design` tree | *design record · N cells* | a library in the manifest tree; the root also shows the `designdb --check --fast` result |
| an OA tree | *OA · design library* | an OpenAccess library named for a design library |
| | *OA · scratch library* | where a flow draws; nothing in it is published |
| | *OA · legacy shared library* | predates one-library-per-design-library; declared by the port |
| | *backup* | a set-aside copy (`.backup*`) |
| | *OA · undeclared* | none of the above — the prompt to declare it |
| the content store | *store · N cells* | the bytes the design record's md5s point at |
| | *not in this checkout's record* | no manifest here describes it — **pull first**: the store is live, the record is whatever this checkout last pulled |
| a work tree | *work · scratch* | regenerated scratch; nothing in it is published |

## What a port declares — `browse/roots.json`

`browse/roots.json` is the one browser file that differs per port, and it is
never vendored. A plain list of roots works; the object form adds four
optional keys:

```json
{"roots": [{"name": "results", "path": "analog/results"},
           {"name": "design",  "path": "design", "role": "design"},
           {"name": "work",    "path": "analog/work", "role": "work"},
           {"name": "oa libs", "kind": "remote", "host": "asic7",
            "fs": "bnl-home", "group": "BNL cluster",
            "path": "~/Documents/<area>/analog/oa", "role": "oa"}],
 "renderer":  {"path": "<repo-relative path to the renderer>",
               "argv": ["{gds}", "{top}", "{out}"], "window": false},
 "klayout":   "<repo-relative path to the port's klayout_open.ps1>",
 "badges":    {"*-asic7.json": "verdict", "*.lvs.report": "verdict_text"},
 "libraries": {"scratch": ["<port>_work", "*_work"],
               "legacy":  ["<pre-split shared library>"]}}
```

| Key | What it says |
|---|---|
| `roots` | a name and a repo-relative path; a cluster root adds `kind: "remote"`, the `host` to ask and the filesystem it is on (`fs`) — hosts on one `fs` share one cache. An untracked `roots.local.json` beside it overrides or extends by name |
| `role` (on a root) | what kind of tree it is: `oa`, `store`, `design` or `work`. Without it the browser infers it from the directory name. A misspelt role stops the browser at startup |
| `renderer` | how to draw a `.gds`: the path (repo-relative), the argument order (`{gds}`, `{out}`, and `{top}`, which the browser fills from the stream) and whether it takes a crop window. Two more keys for a renderer that must run on the cluster: `"ship": true` sends the renderer's own bytes with each cluster render (for layouts that live outside any cluster checkout — the picture is then drawn by the code in *this* checkout), and `"python": "~/…/bin/python"` names the cluster interpreter to run it with. Undeclared, the browser looks for `render_gds.py` at the two conventional engine paths |
| `klayout` | the KLayout launcher script, same rules |
| `badges` | extra badge sources: file name or glob → reader (`report`, `manifest`, `status`, `verdict`, `cell`, `verdict_text`). A glob matches files only |
| `libraries` | OA libraries the design record cannot name: `scratch` (also read from `oa_dest.py`'s `SCRATCH`) and `legacy`. Names or globs; a design library is never called legacy |

An unknown key, role or reader is refused at startup rather than ignored —
a declaration that silently declares nothing is the failure it exists to
prevent.

## Before your first run

Nothing to set up for local trees. Cluster roots go through the vendored
transport (`deployment/bnl/jobs/remote.py`) and need the cluster access the
port's deployment notes already set up: the ssh alias for the host and, for a
cluster render, a Python with matplotlib there (miniforge by default, or the
interpreter the port's `renderer.python` names).

## When something looks wrong

| You see | What it means / what to do |
|---|---|
| *the page is stale* | it compares its build id with the server's — reload |
| the hub says *older build* | that repo's browser runs older code than this installation — restart it; if it persists, the port needs re-vendoring |
| *renderer not found* | the port keeps its renderer at neither conventional path — declare it in `roots.json` |
| *the renderer needs ONE top cell* | the stream has zero or several unreferenced structures (tool metadata such as KLayout's context structure is already ignored) — the stream itself needs fixing |
| *the declared renderer python … is not on <host>* | the `renderer.python` path does not exist on that host |
| *not in this checkout's record* on a store directory | pull this checkout before treating it as an orphan |
| a cluster listing fails | the cluster did not answer — not the same as the directory being absent; check VPN/ssh from a shell |
| a picture looks wrong | don't eyeball it: a `getBBox()` sweep over the drawn elements finds a bad frame in seconds |
| a viewer shows nothing at all | treat it as a parse bug, not an empty file, until proven otherwise |

**Changing the browser itself:** edit it in spec2si-flowkit, never in a port
(the copies are hash-gated), and run its tests before trusting a change —
the whole UI is JavaScript inside a Python string:

```bash
python browse/test_browse.py
```

## Going deeper

This page is the onboarding path. The architecture, the history, every trap
paid for and the ordered list of what is still open are in
[the visual-interaction plan](https://github.com/mandalsoumyajit/spec2si-flowkit/blob/main/docs/visual_interaction_plan.md)
in **spec2si-flowkit** — start at its section 11. (A URL rather than a
path: this page is vendored into every port, and the plan exists only in
the flowkit.) For exact flags on any script
named above, see the **Commands** chapter of the port's generated reference.
