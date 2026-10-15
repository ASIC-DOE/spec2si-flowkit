<!--docmeta
title: How to browse your results
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-10-15
summary: Start the artifact browser on this checkout (or the hub over every checkout), view a layout or a waveform, cross-probe a schematic, read the design record (which captured view is this file, what does a cell.json bind), list the cluster's trees, and check what an agent did or which processes hold licences. What a port declares in roots.json (its trees and what kind each is, its renderer, its badge sources, its scratch and legacy OA libraries), and what the listing says when it has nothing to badge. The browser is vendored from spec2si-flowkit and identical in every port.
-->

# How to browse your results

**One command, in any checkout:**

```bash
python browse/server.py
```

Opens `http://127.0.0.1:8730` on **this** checkout; the header and the
tab title name the repo, so several open side by side stay told apart. Every tree the port's
`browse/roots.json` names is a root; listings are badged from the files the
flows write; a layout renders, a waveform plots, a `cell.json` opens as the
design record. The browser is read-only, stdlib-only, and vendored from
spec2si-flowkit byte-identically into every port — a fix lands once.

`python browse/launch.py <name>` serves a *sibling* checkout by prefix from
wherever you are (`--list` says what can be served and what is up); several
run side by side on consecutive ports. That is a convenience, not how a port
is served: each checkout serves itself.

## The hub: every repo on one page

```bash
python browse/hub.py
```

Opens `http://127.0.0.1:8729`, from any checkout. One row per onboarded repo:
what it is (its README line), its branch and how far it is ahead of or behind
its upstream *as of its last fetch*, whether its browser is running and on
which build, an **Open** link, and a **Start** button that runs that repo's
own `browse/server.py`. Below it is the same Jobs / Processes / Agent strip
the repo pages carry, once for the machine; its Agent tab lists sessions
from every repo and from the parent directory, and an artifact link there
opens the right repo's browser at that file.

The hub serves no files and cannot read any repo -- every browsing route
404s there. Each repo's browser still confines itself to its own repo; the
hub only links between them. A checkout that is behind is worth noticing
first: the design-record badges compare the cluster's live store against
whatever record the checkout last pulled.

## What it shows

| Pane | What you get |
|---|---|
| Results | every root the port declared, badged; click through to its files |
| Interactions | the live job panel, agent activity, licence-holding processes (through the vendored `deployment/bnl/jobs/`). The job list is ONE read per machine: every browser and the hub share a cache file, a browser asking during another's read waits for it, and a read stays fresh for twice what it cost (one list measured 52 s for 1227 records). The agent tab also lists sessions started in the parent directory (`C:/dev`) whose transcript tail names this repo |
| a Markdown file (`.md`) | rendered by the docs system's own `docs/mdrender.py`, in a sandboxed frame served with a no-script CSP; links to other `.md` files keep rendering, the source is one click away. A port without `docs/mdrender.py` shows the text |
| the address bar | a deep link -- `#r=<root>&p=<dir>&v=<file>` -- so a reload lands where you were and a link opens a browser at a file; the hub and the agent panel use it |
| a layout (`.gds`) | rendered PNG through the port's renderer, cropped by window where the renderer takes one; DRC markers overlaid from the `.lyrdb`; the KLayout handoff on Windows |
| a schematic (`.svg`) | the engine's own drawn model; click a net to dim everything else, in the schematic and the paired layout |
| a waveform (`.psf`/nutascii) | tran/dc/ac/noise/stb/op, decimated and re-plotted as you zoom — on a port whose engine has a transient reader; a port without one says so |
| Monte Carlo | the min/max band across iterations, not sigma |
| two GDS files | a geometric XOR — same shapes, different coordinates, or genuinely different |
| the `design` root | the manifest tree, badged with `designdb --check --fast` (the gate CI runs); each library directory says *design record · N cells* |
| a `cell.json` | the views table with the bound view marked, the provenance, the BOM as links to sibling cells, `bom_external` as text — and a warning when a newer generation of a bound view has not been adopted |
| any local file | which captured view it is, by md5 against the manifests (*captured as `<lib>/<cell>` view `stream`, bound*), or *in no captured view*; a file that is a view's tracked origin with differing bytes is called out as drift |
| the cluster's `analog/oa/` | each library directory says which kind it is: *OA · design library*, *OA · scratch library*, *OA · legacy shared library*, *backup*, or *OA · undeclared* — from the served repo's record, its `oa_dest.py` and its `libraries` declaration |
| the cluster's `design_store/` | *store · N cells* for a design library, *not in this checkout's record* for bytes no manifest describes (pull first: the store is live, the record is whatever this checkout last pulled) |
| a work tree | the listing says *work · scratch*, and so does a `work` directory (or a declared work root) seen from the listing above it |

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
and four optional keys — a declaration per repo instead of a list of places
to guess:

```json
{"roots": [{"name": "results", "path": "analog/results"},
           {"name": "design",  "path": "design", "role": "design"},
           {"name": "work",    "path": "analog/work", "role": "work"},
           {"name": "oa libs", "kind": "remote", "host": "asic7",
            "fs": "bnl-home", "group": "BNL cluster",
            "path": "~/Documents/<area>/analog/oa", "role": "oa"}],
 "renderer": {"path": "<repo-relative path to the port's render_gds.py>",
              "argv": ["{gds}", "{top}", "{out}"], "window": false},
 "klayout":  "<repo-relative path to the port's klayout_open.ps1>",
 "badges":   {"*-asic7.json": "verdict", "*.lvs.report": "verdict_text"},
 "libraries": {"scratch": ["<port>_work", "*_work"],
               "legacy":  ["<pre-split shared library>"]}}
```

* **`roots`** — a name and a repo-relative path; a remote root names a host
  and the filesystem it is on (`fs`), so two hosts that see the same home
  share one cache. `roots.local.json` beside it, untracked, overrides or
  extends by name, for a machine-specific path.
* **`role`** on a root — what KIND of tree it is: `oa` (OpenAccess
  libraries), `store` (the content store the manifests' md5s join to),
  `design` (the manifest tree) or `work` (regenerated scratch). The same
  directory name — `adc_v6` — is a library in each of the first three, and
  the badge says which. A tree with no role is inferred from its directory
  name (`oa`, `design_store`, `design`, `work`), so `analog/oa` reached by
  walking down a whole cluster area is still marked. A misspelt role is
  refused at startup.
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
* **`libraries`** — the OA libraries the design record cannot name:
  `scratch` (what a flow draws into; `oa_dest.py`'s `SCRATCH` counts too)
  and `legacy` (shared libraries that predate one OA library per design
  library and are still on the cluster). Names or globs. The record wins:
  a design library is never called legacy. Anything in an `oa` tree that is
  none of these, and not a `.backup*` copy, reads *OA · undeclared* — which
  is the prompt to declare it.

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

This page is the onboarding path. The architecture, the phase history, every
trap paid for and the ordered list of what is still open are in the
visual-interaction plan, `docs/visual_interaction_plan.md` in
**spec2si-flowkit** (moved there from spec2si-tsmc65 on 2026-10-15, beside the
code; start at its section 11), with the survey that made the browser
configuration-driven beside it. For exact flags on any script named above,
see the **Commands** chapter of the port's generated reference.
