<!--docmeta
title: Visual interaction — design roadmap
genre: plan
status: active
area: top
owner: soumyajit
updated: 2026-10-15
summary: MOVED to spec2si-flowkit 2026-10-15, where the browser's code has lived since
  ADR-0004. START AT SECTION 11 -- the current state, what landed since
  2026-08-20 (the hub, one cluster read per machine, trees marked by role,
  markdown, deep links, photonic rendering and spectra) and what is still open.
  Sections 1-10 are the history. Status as of 2026-08-20: PHASES 0-4 AND 4a
  COMPLETE, PHASE 5 MOSTLY DONE, all three ports.
  START AT SECTION 10 -- it is the resume block: how to run it, what is where,
  the traps, and the ordered next step. For actually USING any of this, read
  [browse-your-results](howto/shared/browse-your-results.md) instead -- this file is
  the roadmap and the reasoning, not the onboarding page.
  Landed: KLayout marker databases, DRC error atlases, per-flowrun report.html, a
  generated KLayout layer map, the artifact browser, semantic badges, on-demand GDS
  render, the KLayout handoff, CLUSTER ROOTS (listing, size-capped fetch and
  cluster-side rendering through jobs/remote.py), the XOR visual regression, an
  INTERACTIONS panel holding a licence-holding process scanner and the agent
  activity view, PHASE 5's internal-model track map, remote waveforms (tran/dc/
  ac/noise/stb/op), and the schematic/layout cross-probe. Still open: VNC on
  asic7, op-point-on-layout overlay, cluster-side MC aggregation, a yield
  number, nested sweeps, and `lyp.py` for XT011 (built, 14/15 tests -- one kit
  layer has no purpose entry, see 10.20 item 4).
-->

⚠ **Retagged `decision` → `plan` and refreshed 2026-08-20.** This is a
phase-tracked roadmap that gets edited as work lands — exactly what the
`decision` genre's own contract forbids ("append-only... never edited to
reflect a new choice"). `plan` is the correct fit: "must be CLOSED or
SUPERSEDED, never merely abandoned." It isn't closed yet, so it stays
`active`. Both genres are web-only, so this doesn't change where the doc
appears — only whether its own staleness contract is honest. The onboarding
page is separate either way: [howto/shared/browse-your-results.md](howto/shared/browse-your-results.md)
(vendored from spec2si-flowkit since 2026-09-12, when the browser itself was).
⚠ **Moved here from spec2si-tsmc65 on 2026-10-15** -- the browser it plans
is vendored from this repo (ADR-0004), so the plan lives beside the code.
**Start at section 11.** Sections 1-10 are carried over unchanged; their
history before the move is in spec2si-tsmc65's git log, which keeps a
pointer at the old path.

# Visual interaction — design roadmap

Status: **PHASES 0-4 AND 4a COMPLETE (2026-07-31)** in both repos. Phases 0-3
landed, and with them the semantic badges, the on-demand GDS render, the
KLayout handoff, **cluster roots** — the browser reads asic7 as readily as the
local disk and renders a 28 nm layout *on* the cluster — the **XOR visual
regression**, and an **Interactions** panel: a scanner for the tool processes
that sit holding licences, and the **agent activity view**. Phase 5 is
planned. This is the design contract; each phase lands with its own gate.


The flows are headless by design and that is not the problem. The problem is
that everything a human needs to *see* arrives as coordinates, and everything
that gets *rendered* arrives as a file nobody opens.

## 1. What already exists (do not rebuild)

Surveyed 2026-07-29 across both repos.

| Capability | State | Where |
|---|---|---|
| GDS -> PNG | pure-python GDSII reader + matplotlib raster, TSMC65 layer/hatch style table | `analog/engine/layout/render_gds.py` (325 lines); fork in `spec2si-tsmc28/analog/engine/layout/render_gds.py` (layers from `tech.layers`) |
| Floorplan SVG | hand-written `<rect>`/`<text>` emitter + matplotlib mirror | `hybrid_adc/floorplan/chipplan.py` |
| Schematic SVG | netlist-IR -> SVG, back-annotation and cross-probe **already scaffolded** (`OPV`, `HL` colours) | `analog/engine/schematic/render_svg.py`, `render_system.py`, `op_point.py` |
| Derived-CSV plots | Id-Vds, VTC, Bode, corner bars, MC histograms | `analog/engine/plot.py`, `hybrid_adc/results/plot_*.py` |
| DRC localisation | RVE `DRC_RES.db` parse -> coordinate/row text table | `hybrid_adc/control_layout/drc_where.py` |
| DRC semantics | `RULE_TABLE`: class / response / auto / severity | `analog/engine/layout/drc_triage.py` |
| Cross-run verdicts | append-only JSONL, deck-partitioned, 206 records | `analog/specs/verify_log.jsonl`, `verify_log.py` |
| Job telemetry | `events.jsonl`, measured-rate ETA, `WAITING_LICENSE` | `deployment/bnl/jobs/` |

So: rendering is not the gap. **Composition, delivery and interactivity** are.

Three concrete holes:

- DRC/LVS results and layout geometry are rendered by different tools and
  never composited. The errors are never drawn on the layout.
- ~1857 PNGs exist on disk with no index, no report page, no `LATEST` view.
  A render nobody opens is not feedback.
- The engine's **internal model** is never drawn at all — `*.abstract.json`
  track extents, keepouts, pad windows, `placement.json`, `trace.json`. The
  vref M3i / M4.S.1 closure was debugged at exactly this level, in
  coordinates, by hand.

## 2. Environment facts (verified 2026-07-29 — do not re-probe)

- `klayout` 0.30.10 and `gdstk` 1.0.1 wheels install on **both** sides:
  `cp314-win_amd64` locally (py3.14), `cp313-manylinux` on asic7 against
  `~/miniforge3` (py3.13). **PyPI egress from asic7 works.**
- asic7 system `python3` is 3.6.8 with no numpy/matplotlib. Use miniforge.
- **KLayout 0.30.2 GUI is already installed locally**:
  `%APPDATA%\KLayout\klayout_app.exe` (not on PATH; config dir is
  `%USERPROFILE%\KLayout`). Ships `strmxor.exe`, `strmcmp.exe` — GDS XOR and
  compare, no code needed.
- asic7 has `Xvfb`, `vncserver`, `xterm`. No `ffmpeg`, no klayout binary.
  Xvfb is already used by `apr_place_probe.py`, `apr_vre_probe.py`,
  `mxl_probe.py` — and `vreRunGeneration` / fluid guard-ring PCells return nil
  under it because they need the real APR-assistant window.
- Local python has matplotlib, numpy, scipy, PIL, pandas, tkinter (Tk 8.6),
  sqlite3. No cairo, no plotly, no gdstk/klayout yet.

## 3. Principles

1. **Do not build a signoff viewer.** KLayout is the viewer. Our job is to
   emit into it. Anything we render ourselves is for reports, diffs, agents
   and quick looks — `render_gds.py`'s own docstring already says this.
2. **matplotlib stays the only hard dependency.** klayout/gdstk enter as an
   *optional accelerator*, probed at import; `render_gds.py` remains the
   dependency-free fallback so the flow still runs on a bare python.
3. **Renders are agent-readable.** Claude can look at PNGs. A render inspected
   before a 25-minute 28 nm LVS is a new feedback path, not a nicety.
4. **One renderer, two delivery modes** — the browser (§5) and the static
   report (§4.3) share templates. Never two implementations of one view.
5. **Reuse the hardened transport.** There are already three copies of the
   sync idea (`analog/engine/sync`, `dig_flows/sync`,
   `deployment/bnl/remote_task.sh`) plus ~75 ad-hoc scripts in
   `.cluster_scripts/` and `.tmpq/`. Do not add a fourth — go through
   `deployment/bnl/jobs/remote.py`.
6. **Read-only.** The browser never writes into a flow tree, never deletes,
   never launches a tool except the whitelisted KLayout handoff.

## 4. Phase 0-2 — make failures visible

### 4.1 KLayout marker database export — LANDED 2026-07-29

`analog/engine/layout/lyrdb.py` converts the Calibre RVE results database to a
KLayout marker database; `verify_layout.run_drc` emits `DRC_RES.lyrdb` beside
the GDS on every DRC run (best-effort — a viewer artifact must never fail a
signoff run). Offline gate: `test_lyrdb.py`, 8 checks, no PDK/Calibre/cluster,
synthetic fixture (a real database embeds foundry deck source, which must not
land in the repo).

Verified against three real cluster databases (ctrl2 54 markers / adc_top 19 /
vref 17): every marker's coordinates round-trip **exactly** through
`klayout.rdb`. Visual confirmation in the GUI is the remaining half of the gate.

Markers hang on a two-level tree, `<triage class>/<rule>`, from
`drc_triage.RULE_TABLE`, so gating and deferred classes separate in one click.
`DRM.R.1` is filed under its own `informational` branch to match
`verify_layout.run_drc`, which excludes it from the violation count — a
presentation choice, kept out of `RULE_TABLE`.

**Parser defect found and fixed in the same change.** `drc_triage`'s
rule-header charset `^[A-Za-z][\w.\-]*$` excluded `:`, but the deck emits
qualified names — `OD.DN.2:L`, `VIA1.R.4:M1`, `M9.DN.2:L`. Those headers were
silently skipped, leaving the parser's `rule` pointing at the PREVIOUS section,
so their violations were charged to the wrong rule (measured on sdt_sar__ctrl2:
15 `VIA1.R.4:M1` reported as `VIA1.R.2__VIA1.R.3`, which read 19 instead of 4).
Full-fidelity parsing now lives in `drc_triage.parse_records` (keeps record kind
and all vertices); `parse_results` bboxes it, so there is still one reader of
the format. Blast radius is `triage()`/auto-repair only — `verify_log` scrapes
`DRC.rep` via `_RULECHECK`, not the results database, so the occurrence log is
unaffected. Risk closed: a colon-named rule following an `M*.S.*` section would
have inherited the `metal-space` class, which is auto-repairable.

Open, not actioned: `VIA1.W.1` classifies as `unknown`/gating because the
via-rule regex is `^VIA\d+\.(R|S)\.` and omits via *width*. Promoting it is a
`RULE_TABLE` semantics change and belongs with its own measurement.

Payoff: `pull.sh` brings GDS + lyrdb to Windows, KLayout opens both, and every
violation becomes a clickable list entry that zooms to real geometry with full
hierarchy and layer control. **A genuinely interactive signoff-grade
diagnostic loop for one day of work and zero UI code.** Categories come from
`drc_triage.RULE_TABLE` so the marker tree is already triaged by class and
severity.

Extend the same emitter to LVS: shorts localised by `find_short.py` and
`seam_check.py` become markers on the offending net.

### 4.2 Error atlas — LANDED 2026-07-29

`analog/engine/layout/atlas.py`: per cluster, a zoomed crop with violation
boxes overlaid and labelled from `RULE_TABLE`, plus `index.json`. Not redundant
with §4.1 — these are embeddable in reports, diffable across runs, and readable
by an agent. Offline gate: `test_atlas.py`, 9 checks, no PDK/GDS/cluster.

`render_gds.py` gained `draw_polys()` / `add_legend()`, split out of `main()`
so the atlas draws through the *same* code path (principle 4). `main()` is
otherwise unchanged and re-renders the ctrl2 pilot identically (259 247 polys).

Two things the real data taught, both now locked by tests:

**Proximity clustering does not work here.** The first implementation
single-link clustered by distance and returned ONE cluster holding all 54 ctrl2
violations — a "crop" of the whole 170×154 µm cell. The cause is not row-to-row
chaining: the **density rules are whole-cell window markers** (`M2.DN.1` …
`M9.DN.2:L`, `OD.DN.2:L`), so each one lies within any merge distance of every
other violation and welds the set together. Fixed in two parts: `partition()`
pulls whole-design markers out first (they get one `atlas_global.png`, not N
identical full-cell crops), then `cluster()` buckets the rest on a 12 µm grid,
which cannot chain and bounds the crop extent by construction.

**Drawing every layer defeats the purpose.** The first readable-looking crop
was ~20 layers of hatch over a 10 µm window with the violation marker invisible
inside it — the same "mud" the hatch was introduced to prevent at full-die
zoom, reappearing because at this scale *every* layer is dense. `rule_layers()`
reads the layer out of the rule name (`M2.S.1`→M2, `VIA1.R.4:M1`→VIA1+M1,
`OD.DN.2:L`→OD) using the same rule↔layer correspondence phase 0 measured
(45/45 markers on their rule's layer); context layers draw dimmed and hatchless
underneath. An unmapped rule (`DRM.R.1`) yields nothing and falls back to
drawing everything — a noisy picture beats a confidently empty one.

Measured on the ctrl2 pilot: 54 violations → 44 localizable in 34 clusters
+ 10 whole-design. The crop cap reports what it dropped, by rule, and never
truncates silently.

Delivery is automatic: `run.emit_atlas()` finds `DRC_RES.db` beside the routed
GDS and writes `atlas/index.json` plus the crops into every flowrun stamp. Both
`report.json` and `report.md` link the resulting artifacts; failures are
best-effort warnings and never change the signoff verdict.

The coordinate gate uncovered a second Calibre edge encoding,
`x1 y1 x2 y2` on one line. `drc_triage.parse_records()` now accepts that and
the existing two-line form. `drc_where.py` delegates to this shared parser, so
its row localisation and the atlas use identical boxes instead of drifting.

### 4.3 Per-flowrun HTML report — LANDED 2026-07-29

`analog/engine/reporthtml.py`, emitted by `run.emit_report` from the **same
`report` dict** that produces `report.json` and `report.md`, so the three
cannot disagree about a verdict. Stdlib only, no template engine, no JS
(`<details>` and `<img>` are native). Sections: verdict banner, stage table,
gates, pre/post-layout + promotion gate, error atlas (§4.2) with the crop-cap
warning, renders, datasheet link, provenance, and a listing of the stamp dir so
nothing in it is unreachable from the page.

Self-contained but **not** self-inlining: CSS inline, sibling images by
relative name. Base64-inlining a 127 kB render (let alone 25 atlas crops) would
destroy the diffability that motivated the page, and the stamp dir already
travels as a unit.

Gate met: byte-identical on re-run (verified, same sha256 twice), no clock read,
no set iteration, every directory listing sorted.

**Ordering trap, found and fixed.** The page lists the stamp dir, so
`report.json` must be *final* before the page is built. The first version added
`report["html"]` and rewrote `report.json` afterwards — which changed that
file's size, so re-running `reporthtml.py` on the settled directory produced
different bytes than the flow itself had. The key is now declared up front and
`report.json` written once.

### 4.4 KLayout layer properties — LANDED 2026-07-29

`analog/engine/layout/lyp.py` generates a `.lyp` from `render_gds.LAYER_STYLE`;
`verify_layout.run_drc` emits `<cell>.lyp` beside the GDS and the `.lyrdb` on
every DRC run, and `klayout_open.ps1` opens a layout with both (`-l` and `-m`),
generating the layer map if absent. `-l` rather than same-basename auto-load:
that is a per-installation setting and off on a fresh machine.

Without this KLayout assigns its own colour cycle with no names, so M1–M9 look
alike and every marker session starts by working out which metal a violation is
on. Generated from the one styled table so the GUI, the full-die render and the
atlas crops cannot disagree.

**This exposed a real defect in the layer table, not just a missing viewer
convenience.** The first `.lyp` emitted one `<layer>/0` entry per styled layer
and looked complete. It was not: on this PDK the *routing* streams on the **net
purpose**, `M<n> net = (130+n)/83` (confirmed entry-by-entry against the PDK
layermap; the convention was already recorded in `chip_lvs.py`). The ctrl2
pilot carries **9629 PATH elements on 132/83, 133/83, 134/83, 135/83** —
M2–M5 net — and `LAYER_STYLE` had none of those layers, so:

- `render_gds.py` was collapsing all of that routing into the faint anonymous
  `other` style — the same class of bug as the PATH note that had already
  silently dropped the M8/M9 power mesh;
- the **error atlas was worse than noisy, it was misleading**: `rule_layers`
  mapped `M2.S.1` to M2 *drawing* (32) only, so the crop dimmed the very M2
  routing the spacing rule is about and read cleaner than the layout was.

Fixed by deriving net entries from their drawing layer (colour and hatch cannot
drift; hand-transcribing them had already put `M2net` on `#c02060` against
M2's `#c03060`) and by focusing both purposes for a metal rule. Coverage is now
verified against the real GDS: every one of the 25 `(layer, datatype)` pairs
present has a named entry, and KLayout's own `LayoutView.load_layer_props`
loads all 47 of them.

Offline gate: `test_lyp.py`, 10 checks, KLayout load check skipped when the
module is absent.

## 5. Phase 3 — the artifact browser — LANDED 2026-07-29

A three-pane local+cluster browser, served by stdlib `http.server` on
`127.0.0.1`, opened in the default browser.

### 5.1 Why a browser and not tkinter

tkinter is present (Tk 8.6) and would give a native window with no browser
tab. Rejected:

- **The artifact mix decides it.** Outputs are PNG + **SVG** + HTML + MD +
  JSON/JSONL + text + GDS. A browser renders PNG/SVG/HTML natively with free
  zoom, pan, scroll, text selection and find. tkinter renders PNG only (via
  PIL) and **cannot show SVG without a new dependency** — there is no cairo
  locally — and the schematic renderer emits SVG. That is a hard blocker on
  the view we most want.
- **Template reuse.** The browser's viewers and the §4.3 static report are the
  same HTML. Principle 4 pays for itself immediately.
- **Zero new dependencies**, consistent with `docs/gen.py` being stdlib-only.
- It also works over an SSH port-forward if we ever run it *on* asic7 against
  cluster-local artifacts.

### 5.2 Layout

```
+--------------+---------------------------+----------------------------+
| roots/tree   | listing                   | viewer                     |
| (lazy)       | name/size/mtime + badges  | type-dispatched            |
+--------------+---------------------------+----------------------------+
```

**Pane 1 — roots.** Configured, lazily expanded, and **grouped by scope**:
a `Local` section and one section per remote filesystem. Local:
`analog/results/flowruns`, `analog/work`, the cell library, the repo. Remote:
`ms_pilot`, `onr_t28`, `~/.asicjobs`. ONR is *just another root* — one
browser, N roots.

Three revisions the first build earned (2026-07-31), all from the panel being
genuinely confusing to read:

- **Local and remote must be separate sections.** They are different kinds of
  place — one is on this disk and instant, the other an ssh round trip away —
  and one flat list of identical-looking rows hid that entirely.
- **Never group or name a remote root by its HOST.** The host is only the
  machine that runs the read: asic6, asic7 and asic8 return byte-identical
  results on the shared home, so `asic7:ms_pilot` / `asic6:jobs` split one set
  of files across headings and implied a difference that does not exist.
  Group by *filesystem*; show the hosts once per group ("via asic7, asic6 ·
  shared") so a failed read can still name who was asked.
- **The root list must not be fixed by config.** Each section takes a new path
  inline; the name defaults to the last path component, a remote add inherits
  the group's host and filesystem, and the entry persists to the untracked
  `roots.local.json`. That file is the single write this browser performs —
  its own config, never a browsed tree — and it is guarded like the exec
  (§5.4), because adding a root widens what the server will serve.


**Pane 2 — listing.** Where it beats a file manager: it **reads the
artifacts**. Semantic badges next to entries — flowrun verdict from
`report.json`, DRC clean / violation count, LVS CORRECT/INCORRECT, stage
pass/fail from `manifest.json`, job state from `status.json`. Filter and sort;
default sort newest-first because the question is almost always "what did the
last run do".

**Pane 3 — viewer**, dispatched by type:

| Type | View |
|---|---|
| `.png` | inline, zoom/pan |
| `.svg` | inline, native |
| `.json` | collapsible tree, **plus** special-cased views: `report.json` verdict card, `manifest.json` stage table, `*.abstract.json` **track map** (§6.1), `placement.json`, `*.layout.json` fact card |
| `.jsonl` | filterable table (`verify_log.jsonl`, `events.jsonl`) |
| `.rep` `.rpt` `.log` | text with rule/error highlighting, jump-to-next-error |
| `.gds` | on-demand render, cached by content hash; **Open in KLayout** button (with sibling `.lyrdb` if present); XOR-vs-previous via `strmxor.exe` |
| `.csv` | table + quick plot |
| PSF-ASCII / nutascii | waveform plot (§6.3) |
| `.md` | monospace initially; a minimal renderer later, not a dependency |
| `.spice` `.cdl` `.v` `.def` `.lef` | text |
| large / binary | head+tail within a byte budget, never a whole-file auto-pull |

### 5.3 Cluster transport

Through `deployment/bnl/jobs/remote.py` only: ship-once script over stdin
(remote shell is tcsh — never quote inline), stdout-only `{"schema":1}` parse,
wall-clock timeout, tri-state `KNOWN`/`UNKNOWN`/`STALE`. On Windows the chain
is `wsl.exe -e bash -lc "ssh ..."` per `deployment/bnl/CLUSTER.md` §1.

- Listing = one remote `ls`-to-JSON script. Cache with a TTL;
  stale-while-revalidate plus an explicit refresh, because an ssh round trip
  is ~1 s and the UI must never block on it.
- File fetch = size-capped pull into a local cache keyed by
  `(host, path, mtime, size)`.
- **Render remote GDS remotely** (miniforge klayout per §2) and ship only the
  PNG. Never pull a multi-GB OA tree — the same pruning rule `pull.sh`
  already enforces.

### 5.4 Config and safety

`browse/roots.json` — `[{name, kind: local|remote, host, path}]`, with an
untracked `roots.local.json` override in the spirit of `sync/sync.local`.

Bind `127.0.0.1` on a random port; read-only over every browsed tree; every
path resolved and confined to a configured root (reject `..` escapes); no
auto-fetch above the byte budget. Two POST routes exist and only two — the
KLayout handoff and adding a root — both behind a same-origin guard, because
each one widens what the server will do. `roots.local.json` is the only file
written, and it is the browser's own config.

### 5.6 What landed, and what is deliberately still phase 4

`browse/` — `server.py` (stdlib `http.server`, loopback, random port),
`model.py` (listing + type dispatch, no HTTP so it is testable without a
socket), `roots.py` (config + confinement), `roots.json`, `test_browse.py`
(17 checks). Registered in `docs/gen.py` `AREAS`.

Verified on a real flowrun: the `strongarm_ss` stamp dir lists 10 artifacts and
each resolves to the right viewer — `render_full.png` renders (1980 px natural
width), `report.json`/`manifest.json` pretty-print, `report.html` frames, and
the logs render as text. Navigation, breadcrumbs and newest-first ordering work
from the UI, not just the API.

**Confinement is the part that got the tests.** The browser serves whatever path
it is handed, so `roots.resolve` is the one function whose failure turns a
read-only convenience into a file-exfiltration endpoint. Each escape shape fails
for a different reason and so gets its own check: `..` needs realpath, an
absolute path needs an explicit reject (`os.path.join` would DISCARD the root),
a sibling directory needs `commonpath` rather than a string prefix, and a
symlink is caught only by resolving before comparing.

Two defects the tests found, both mine:

- The absolute-path guard ran AFTER `lstrip("/")`, so `/etc/passwd` was silently
  reinterpreted as `<root>/etc/passwd`. Confined, so not an escape — but
  silently redefining the request is the contract that becomes one on the next
  edit. It now rejects.
- Relative paths in `roots.json` resolved against the **cwd**, so a tracked
  config pointed at different trees depending on where the browser was started.
  They anchor on the repo.

Still phase 4, and not smuggled in: cluster roots, semantic badges (verdict, DRC
count, LVS state read out of the artifacts), GDS rendering and the KLayout
handoff. Each needs the hardened ssh transport through `jobs/remote.py` rather
than a fourth copy of the sync idea (principle 5).

### 5.5 Placement

New top-level `browse/`, with `("browse", "Artifact browser + viewer (local +
cluster)", ["browse"])` added to `AREAS` in `docs/gen.py` so it enters the API
reference and repo map. Not under `deployment/` — it browses local trees too.

## 6. Phase 4-5 — the rest

### 6.1 Draw the engine's model, not just its output

Render `*.abstract.json` as a **track map**: occupied M3 tracks, keepouts,
rails, pad windows, pin extents. Same for `placement.json` / `trace.json` as
the router sees them.

Justification: the abstract is the *only* geometry contract between block and
top, and the last three vref fixes were all lane adjacency and track windows.
A picture of the abstraction the assembler reasons over is worth more than a
picture of the GDS it emitted.

### 6.2 Visual regression

"Geometry-identical regression before any engine change lands" is already
policy (`analog/CLAUDE.md`). Make it a picture: `strmxor.exe` (installed) or
gdstk boolean XOR between two GDS files yields exactly the shapes an engine
change moved. Identical -> empty, which is the assertion; non-identical ->
a reviewable diff render instead of a wall of coordinates.

### 6.3 Plot the transient, not just the scalar

`spectre_flow.py` regexes PSF-ASCII down to scalars; ONR's `char/nutascii.py`
does the same for nutascii. Add a plotter over the *same* parsed data:
extracted values become annotations on the curve rather than replacements for
it. The vref ESR result — 44.7 mV against a 56.25 mV budget at the 300 ps
decision — is a curve with two labels, and would have been obvious on sight.

### 6.4 Schematic <-> layout cross-probe

Finish what `render_svg.py` scaffolded: overlay `op_point.py` results in
reports, and link net highlighting between schematic SVG and layout render in
one HTML page. Both sides already key on net names, so the join is free.

### 6.5 Live job panel

`events.jsonl` + `progress.py` measured-rate ETA already exist; only the view
is missing. An auto-refreshing pane in the browser (pane 3 over a jobs root)
turns `aj top` polling into ambient awareness. Weighted by the real latencies:
4 min analog flow, 17 s DRC+LVS, 2 min route round trip, ~25 min 28 nm LVS,
4-25 min Virtuoso cold start.

### 6.6 VNC on asic7

Documented as the escape hatch in `analog/apr_device_flow_reference.md` and
`analog/oa_schematic_import.md`, never implemented. This buys **capability,
not just eyeballs**: `vreRunGeneration` / `vreCreateRowRegion` /
`vreRunPlacer` and the fluid guard-ring PCells return nil under Xvfb because
they need the real APR-assistant window. Also enables Calibre RVE screenshot
capture. Heaviest item, lowest urgency — §4.1 covers the diagnostic case far
more cheaply.

## 7. Agent activity view

A real-time view of what the coding agent is doing, as a mode of pane 3 (and a
persistent strip above panes 2-3 showing *current* activity).

The terminal already shows the transcript. What it cannot show is the **join**:
the agent's actions, the cluster jobs those actions launched, and the artifacts
they produced are three separate streams that only a human currently correlates,
badly, by scrolling. The browser is already tailing two of them.

### 7.1 Event source — tail the transcript, hook only for liveness

Two sources are available (measured 2026-07-29):

- **Session transcripts**, `~/.claude/projects/<cwd-slug>/<session>.jsonl` —
  already written, no configuration, no added latency, and *retroactive*: 30
  sessions / 137 MB exist for `C--dev` today. Records carry `type`
  (`user`/`assistant`/`attachment`), `timestamp`, `sessionId`, `uuid`,
  `parentUuid`, `cwd`, `gitBranch`, and **`isSidechain`** — which is what marks
  subagent records, so the fan-out tree comes free.
- **Hooks** in `settings.json` (`PreToolUse`, `PostToolUse`, `Stop`,
  `SubagentStop`, `Notification`). None are configured today.

**Decision: transcript tail is the primary source. Do not hook
`PreToolUse`/`PostToolUse`.** A hook spawns a subprocess on *every tool call*;
on Windows that is tens of ms each, paid forever, to obtain data already being
written to disk. Hook only `Stop` and `Notification` — they fire rarely and give
a reliable "idle / needs you" edge that is awkward to infer from a tail. If the
transcript format shifts under us, the parser degrades to "unknown event" rather
than breaking the browser; it is an observability view, not a gate.

Read transcripts **in place**. Never copy them into the repo, never commit them.

### 7.2 NDA constraint (non-negotiable)

Transcripts contain full prompts, file contents and tool output — which for this
work means **PDK geometry, rule-deck text and sim results**. The view therefore
renders *metadata only* by default: event kind, tool name, target path, elapsed,
exit status, and a truncated one-line summary. Message bodies are never
displayed and never leave `~/.claude/`. This is the same rule that already makes
the back-annotated op-point SVG a gitignored artifact.

### 7.3 What it shows

1. **Now** — the single line that matters: what the agent is doing this instant,
   or what it is *blocked on*. Distinguish waiting-on-cluster (with the
   measured-rate ETA from `jobs/bin/progress.py`), waiting-on-licence
   (`WAITING_LICENSE` already exists there), running-locally, and waiting-on-you.
   Given 25-minute 28 nm LVS and 4-25 minute Virtuoso cold starts, "is it stuck
   or is Calibre just slow" is a recurring, currently unanswerable question.
2. **Artifact link-out** — when an agent action writes a render, report or GDS,
   the timeline entry is clickable and opens it in the viewer. This is what makes
   the browser and the agent view one product rather than two: *the agent just
   produced this* -> look at it, now.
3. **Thrash detection** — the highest-value item, and pure derived analytics over
   the event stream with no new data. Surface repetition: same rule failing
   across N iterations, same file edited N times, same cluster job resubmitted.
   The vref decap closure took **9 coordinate-diagnosed iterations**; a view that
   says "iteration 6, same rule, no progress" catches a wrong turn while
   intervention is still cheap.
4. **Fleet tree** — parallel subagents from `isSidechain`/`parentUuid`, with
   per-agent status. Today they are invisible until they report back.
5. **What the agent looked at** — a Read on a `.png` means the agent inspected a
   render. Principle 3 says renders are agent-readable; this is how you audit
   whether it actually looked before acting.
6. **Cross-session history** — 30 sessions are already on disk. "What happened
   overnight on this block" becomes browsable and joins to the flowrun stamps
   from the same window. This does not replace the hand-written `RESUME.md`
   files; it makes writing them factual instead of reconstructed.

### 7.4 Constraints

- **Read-only, and deliberately no control surface.** Driving the agent from the
  browser is a non-goal (§9). The terminal is the control path; a second one
  invites two operators disagreeing about state.
- Tail-only with a bounded byte budget and an index cache keyed by
  `(file, size, mtime)`; 137 MB must never be parsed eagerly.
- Degrades silently: if no transcript is found, the view is empty and the rest of
  the browser is unaffected.

## 8. spec2si-tsmc28 (ONR) — viewer ported 2026-07-29

ONR inherited `render_gds.py` and nothing else — no verify_log, no
provenance, no docs gate, no stage runner — against a **25-minute** LVS. Every
item here belongs in the shared engine, and the pre-LVS visual check is worth
strictly more at 28 nm than at 65 nm. ONR enters the browser as a root entry;
no separate tool.

**Landed at 28 nm:** the layer map (`layout/lyp.py`), the results-DB reader
(`layout/drc_db.py`) and the marker export (`layout/lyrdb.py`), all emitted by
`verify_layout.run_drc` beside the results. Offline gate: `test_lyrdb.py`,
10 checks.

Verified against **real 28 nm data**, not by analogy: two databases pulled from
the cluster (`Prescaler_89_1` 48 records, `DFF_TSPC_1` 36) — **84/84 markers
coordinate-exact** through `klayout.rdb`, and the 65 nm parser read the 28 nm
format with no change (the results database is a Calibre format, not a process
one).

Four node differences that mattered:

- **The net-purpose convention was CHECKED, not assumed.** `tsmcN28.layermap`
  gives `M<n> net = (130+n)/83` and `M<n> pin = (130+n)/0` for M1–M9, the same
  as 65 nm — but `tech/layers.py` documented "datatype is always 0" and carried
  only the drawing purpose, so the identical latent bug was present: routing
  would render as anonymous `other`. Now in `tech.layers.NET`/`PIN`, with the
  render style derived from the drawing entry.
- **No rule semantics were invented.** The 65 nm marker tree hangs on
  `drc_triage.RULE_TABLE` classes, which are measured judgement about a
  specific rule set. The 28 nm set is different (`PO.R.19`, `SR_DOD.DN.1`,
  `DOD.R.1`, plus `*:WARNING*` deck options), so the tree is
  `<syntactic group>/<rule>` from `drc_db.group_of` — the layer-ish prefix, or
  `warning`. Navigable without claiming to know what is gating. A real 28 nm
  `RULE_TABLE` is its own measured piece of work.
- **`render_gds.py` was not runnable standalone.** `tech/` is a sibling of
  `analog/` here, so its bare `import layers` only resolved when some other
  module had already called `analogpath.bootstrap()`; alone it raised
  `ModuleNotFoundError`. Fixed — this is the dependency-free fallback renderer
  the plan leans on (principle 2).
- **`IO_CONNECT_CORE_NET_VOLTAGE_IS_CORE:WARNING1`** — the trailing digit is
  the deck's own name, confirmed against the raw database, not a parse artifact.
  A header charset without `:` would drop it and charge its violations to the
  preceding rule, which is the 65 nm colon bug in a new costume.

**Also landed, after the paragraph above was written** (`f4f6ef3`, `7cce0e2`,
`d77cd85`, `25a3fdf`): the error atlas, the 28 nm flowrun stamp dir with
`report.json`/`report.html`, and the artifact browser. ONR's `report.json` came
out with the same shape as the 65 nm one — `verdict`, `metrics.<stage>.drc`,
`metrics.<stage>.lvs.correct` — which is why the phase-4 badge reader ported
with no change at all.

**The fork is CLOSED (2026-08-01).** The browser did not land the way §5.2
specified: ONR got its own copy rather than entering as another root, and by
the end of July that was eight byte-identical modules kept in step by hand,
plus cross-repo reaches for the transport and for `abstract_svg`. Every
feature landed twice; the cost grew with each one.

The implementation now lives once, in `spec2si-tsmc65/browse/`, and is
**repo-agnostic**: `BROWSE_REPO` names the repository being served, and the
roots, `tools.RENDERER`, the KLayout launcher, the engine `abstract_svg` is
imported from and the transport search all hang off that one answer. ONR keeps
`roots.json` — genuinely its own — and a launcher with no implementation in
it, which locates the package exactly as `cluster.py` locates `remote.py`.
Verified by running both at once on different ports, each serving its own
trees, with ONR's instance resolving ONR's renderer and listing the 28 nm
flowruns off asic7.

`REPO` resolves ONCE, at import, so the launcher must set the variable BEFORE
importing — asserted, because a silent fallback would have the second repo
quietly browsing the first one's artifacts, which is worse than not working.

## 8a. spec2si-xt011 — onboarded 2026-08-07

XT011 postdates this plan and was never in it: the NVG clock-gen block and the
pad ring went through DRC, LVS, PEX and publication with **none** of phases
0-5 available. Its whole visual surface was one ported `render_gds.py` and six
PNGs pasted into `docs/`.

What it has now is the browser, which was the cheap half:
`browse/server.py` (a launcher stub, no implementation, same rule as ONR's) and
`browse/roots.json` naming its own five local trees plus `anc_xt011` on asic7.
Verified serving: `/api/health` reports `C:\dev\spec2si-xt011`, and its roots list
and read correctly.

Two things the onboarding had to fix rather than assume:

- **`tools.RENDERER` was anchored on one path.** Both flow repos keep the
  renderer at `analog/engine/layout/render_gds.py`; XT011 has no `engine`
  layer at all and keeps its port at `analog/layout/`. Every XT011 layout would
  have reported "renderer not found", which reads as a broken file rather than
  a missing installation. Now a short enumerated list of repo-relative
  locations — enumerated, not globbed, because what `tools.py` may execute has
  to stay answerable by reading that one page.
- **The started-with hint was a constant naming `browse/server.py`.** In a
  launcher-stub repo that sends the reader somewhere that cannot start what
  they were just looking at. `server.START_HINT`, set by the launcher.

**Phase 0 landed 2026-08-07, and the open question is answered: the results
database IS Calibre-shaped.** Signoff here is X-FAB's PVS deck (PVS 23.1, not
Pegasus — a node-licence tier problem recorded in that repo's
`docs/pegasus_drc_first_run.md`), and `results_db -drc ... -ascii` already
emits the format `lyrdb.py` reads. Measured with the 65 nm parser's algorithm
unmodified against five real databases on asic7:

| run | records / rules | |
|---|---|---|
| `drc_final` | 11 / 11 | the signed density-only result |
| `drc_v3`, `drc_v4`, `drc_pwr6` | 12 / 12, 11 / 11, 12 / 12 | |
| `drc_v5` | 2183 / 21 | a broken intermediate — 1984 of them EDGES |
| `drc_v6` | 191 / 16 | routed: `W1M2` width, `S1M*` spacing, `A1M2` area |

Verified end to end, not just parsed: three exported databases loaded back
through `klayout.rdb` — **2385 markers, every one geometry-exact and paired
with the right rule**. `drc_final` groups to `{R}` alone, so the density-only
signoff claim falls out of the data rather than being asserted.

**One trap worth carrying to the next port:** KLayout canonicalises polygon
winding, so comparing ordered vertex lists reports 0/2385 and is measuring the
viewer rather than the export. Compare vertex multisets.

What did NOT port is the semantics half, and that was the real content of the
question. The 65 nm tree hangs on `drc_triage.RULE_TABLE` — measured judgement
about a specific rule set — and X-FAB's names (`W1M2`, `S1M3`, `A1M2`, `Q1NW`,
`floating.TUB`) are a third scheme again. `drc_db.group_of` there is syntactic
only, the same restraint ONR adopted for 28 nm, with a gate asserting it so
severities cannot appear from a name prefix by accident.

**Phases 1 and 2 landed the same day.** `analog/layout/{atlas,report}.py`, both
delivered by `run_drc.sh` beside the marker database, all three best-effort.

Two things the real data changed from the port, and the second is the one worth
carrying:

- **The crop window is no longer square.** This block is 29 × 6 µm, so a square
  window spent ~70% of every figure on empty space and the geometry became a
  thin band. Windows now preserve the cluster aspect, clamped to 2.5:1 so an
  edge violation (zero extent in one axis) cannot become a sliver.
- **`rule_layers` resolves through the renderer's own table, so a layer the
  flow has never drawn resolves to NOTHING rather than to a guess.** `R1M4`,
  `R1M5` and `R1MT` fire on a block with no M4, M5 or top metal in it —
  **density rules fire on absence** — and inventing layer numbers for them
  would draw a focus box around geometry that does not exist. Generalises: a
  rule→layer map must be able to answer "no such layer here".

Phase 2 states counts and the tools' own verdicts and **does not grade the
violations**, for the same reason phase 0's tree is syntactic. The verdict
vocabulary is deliberately not PASS/FAIL — the signed block has 11 DRC results
and *is* signed off, so PASS would be a lie and FAIL a worse one. It reads
`DRC VIOLATIONS (11 in group R)`. The one judgement it does make is the one
that repo's CLAUDE.md requires: zero geometry or any `Not Completed` is
INCOMPLETE, never a count of zero violations, because that is the shape a
licence failure takes.

Verified by reproducing a document: reading `drc_final` plus the LVS
comparison report regenerates `STATUS.md`'s hand-written table exactly from raw
tool output — 11 results all group R, 700 rulechecks, 496 geometry, LVS MATCH
at 146:146 devices, 36:36 nets, 5:5 pins.

**Three defects found building it, all from real output**: a `KEY : VALUE`
regex requiring two spaces after the colon matched the LVS banner and silently
dropped the *entire* DRC summary header; `lvs.rep` is the extraction header and
the MATCH verdict is only in `lvs.rep.cls`, so reading the obvious file finds
no result at all; and a stale `atlas/` directory pairs old crops with new
numbers, which the report now detects and says.

**Cluster note for anyone editing that script:** `python3` there is 3.6.8 with
**no matplotlib**. The atlas runs under `~/miniforge3/bin/python3` (3.13, mpl
3.11) — the same interpreter §10.3's remote renders use — and is skipped
*loudly*, because an atlas that quietly does not exist looks like a run with no
violations worth drawing. `lyrdb` and `report` are stdlib and run under either.

**What XT011 still does NOT have:** no `lyp.py` layer map, no flowrun stamp,
and no `deployment/bnl/jobs/` transport of its own (cluster reads fall back to
the package's `remote.py`, which works). The layer map is the remaining piece
and it needs the kit layertable read out first — see §10.24 on reading BOTH
purposes' datatypes.

The render path is still unproven against a stream *in that checkout*, for the
same reason ONR's was in §10.2: **there is no local layout there at all** — the
geometry lives in OA on asic7. It is now proven against a real XT011 stream
pulled to a scratch directory, which is the honest version of that claim.

## 9. Order and gates

| Phase | Content | Gate |
|---|---|---|
| 0 | `lyrdb.py` exporter | **DONE (headless)**: 3 real databases round-trip exactly through `klayout.rdb`, `test_lyrdb.py` 8/8. Remaining: open one in the KLayout GUI and confirm markers zoom to the right geometry |
| 1 | error atlas | **DONE**: shared parser gives exact `drc_where.py`/atlas coordinates (14/14 real local records); real ctrl2 GDS + synthetic markers emit crops and `index.json`; offline atlas 9/9 and delivery 3/3 |
| 2 | `report.html` per flowrun | **DONE**: byte-identical on re-run (same sha256 twice); emitted from the same dict as report.json/md; renders every section of a real strongarm_ss stamp |
| 2a | KLayout `.lyp` layer map (§4.4) | **DONE**: all 25 (layer, datatype) pairs in the real ctrl2 GDS named; KLayout `load_layer_props` accepts all 47 entries; `test_lyp.py` 10/10 |
| 3 | browser: 3 panes, local roots, png/svg/text/json viewers | **DONE**: opens a real `strongarm_ss` stamp dir and serves all 10 artifacts with the right viewer kind, no shell; `test_browse.py` 17/17, 8 of them path-confinement (now 34/34 with §4 badges, both repos) |
| 4 | semantic badges | **DONE 2026-07-31**: the eight 65 nm circuits and the 28 nm `Prescaler_89_1` all badge from their own artifacts over HTTP; badge-text filter takes the 11 `strongarm_ss` stamps to the 1 that failed |
| 4 | GDS render + KLayout handoff | **DONE 2026-07-31**: the real 1.65 MB ctrl2 block renders (70.3 s cold, 0.001 s cached) and opens in KLayout from the pane with its layer map and 54 markers; listings stay at 12-17 ms during a cold render |
| 4 | cluster roots | **DONE 2026-07-31**: `ms_pilot` flowruns list off asic7 in 0.41 s with badges (0.004 s cached), a 2.8 MB log caps at 512 kB, and the 28 nm divider renders ON asic7 in 7.2 s shipping back only the PNG |
| 4 | XOR diff | **DONE 2026-07-31**: identical pair -> IDENTICAL with no diff written (1.4 s); all 32/0 stripped -> 22881 shapes, drawn by the ordinary renderer (7.3 s) |
| 4a | agent activity view (§7): transcript tail, now-strip, artifact link-out, fleet tree | **DONE 2026-07-31**: reads this project's 25 sessions; the now-strip named the live call fetching it and thrash read "Edit touched server.py 31 times"; zero message bodies (allowlist + closed key-set, checked against the live endpoint); no hooks configured, so no agent slowdown |
| 5.1 | internal-model track map (§6.1) | **DONE 2026-08-01**: all 28 real abstracts render, 0 fail; separates lane PACKING from lane OVERLAP and finds exactly one — vref, `vdd` rail × `vrefn` track, 129.33 µm; net isolation dims 236 of 249 shapes |
| 5.3 | waveform plotting (§6.3): **partial views while running**, group/split panels, zoom/pan | **DONE 2026-08-01**: a real transient replayed in 20 appends reads 37% → … → complete, refreshing itself; grouping by unit separates a µA supply from 1.2 V spikes that a shared axis flattens; zoom RE-DECIMATES in the window (5 notches = 2.1×, a drag holds the span to 9.659 µs exactly) |
| 5 | thrash detection; waveform, cross-probe, live jobs, VNC | per-item |

Phases 0-2 are independent of 3-5 and should land first: they raise the value
of every run whether or not the browser exists.

## 10. RESUME HERE — state as of 2026-08-20

Phases 0-4, 4a and two of phase 5 are done, in **three** repos, on `main`.
What exists is a local+cluster artifact browser that badges what it lists,
renders and diffs layouts, draws the engine's own model, plots simulations
while they are still running, shows what the coding agent is doing, and finds
the tool processes sitting on licences.

### Run it

```
python browse/launch.py             # this checkout, http://127.0.0.1:8730
python browse/launch.py xt011       # any checkout, by name, prefix or path
python browse/launch.py --list      # what can be served, and what is up now
python browse/launch.py tsmc28 --status
```

`C:\dev\browse.ps1` is a shim over the same thing (`.\browse.ps1 xt011`),
untracked because `C:\dev` is not a repository.

Works from any of the three checkouts (all now named `spec2si-<node>` —
tsmc65, tsmc28, xt011). tsmc28's and xt011's `browse/` are **launchers
only**: the implementation lives once in `spec2si-tsmc65/browse/`, and
`BROWSE_REPO` tells it which repository it is serving. If the header says the
page is stale, reload — it compares its own build id against the server's.

⚠ The prefix match is against the whole directory name OR a `-`-separated
component of it (`launch._matches`) — added 2026-08-20 when the `spec2si-`
rename silently broke the short tokens this section has always advertised
(`xt011`/`onr`/`aiml` all stopped matching anything, because `resolve()`
only ever checked a whole-name prefix and no real directory starts with
those substrings anymore). Found while cleaning up this file's own stale
repo names, not by anyone hitting it — worth remembering next time a repo
gets renamed.

**They run side by side, deliberately.** `state_path()` is keyed by the served
repo and `serve()` only refuses a second server for the *same* repo, so
tsmc65, tsmc28 and xt011 coexist on 8730/8731/8732 and each `--status` finds
its own. Switching repos is a browser tab, not a restart. Verified by
running all three at once and reading each `/api/health` back.

**Two defects that only appear at three repos**, and both were found by
running them at once rather than by reading anything:

- **`status()` checked the state file for "is a browser answering", not "is it
  MINE".** A killed server never clears its state file, so a stale record
  naming the stable port outlives the process — and the next repo's browser
  takes that port. The stale record then resolved to the *other* repo's
  server, `--status` reported it as this repo's, and `serve()` declined to
  start: you were handed another checkout's artifacts under your own repo's
  name. The port-scan branch three lines below had always done this check;
  keying the state file by repo was only half the fix. Now `_is_ours()`, used
  by both branches.
- **`_bind`'s port walk was a no-op on Windows.** `http.server` sets
  `allow_reuse_address = 1`. On POSIX that lets a restart re-bind through
  TIME_WAIT while a bind to a *live* port still fails — which is what the walk
  is built on. On Windows SO_REUSEADDR means the second bind SUCCEEDS and
  takes the port: all three servers "bound" 8730, all three printed 8730, and
  requests went to whichever socket the kernel picked. Now a `_Server`
  subclass setting the flag false on `nt` only, since on POSIX it is doing
  real work. The gate binds a live port twice and requires the second to
  raise, because the whole defect was a platform difference in what one flag
  means.

Both had been latent since the browser was written. With one server they look
fine forever, which is why they survived to the day there were three.

Why the repo is not switchable *inside* the page: `roots.REPO` resolves once,
at import, and `tools`, `cluster` and `server` all take their answer from it —
that is what makes the confinement boundary a fixed set of trees for the life
of the process rather than something a request can move. Making it per-request
would put the served repo inside the request, which is the one place
`roots.py` is careful to keep it out of.

### What is where

| Thing | Code | Gate |
|---|---|---|
| marker DB, error atlas, `report.html`, layer map | `analog/engine/layout/{lyrdb,atlas,lyp}.py`, `engine/reporthtml.py` | `test_lyrdb` `test_atlas` `test_lyp` |
| the browser: roots, listing, badges, viewers | `browse/{server,model,roots}.py` | `test_browse.py` 133 |
| which repo it serves, and starting it | `browse/launch.py`, per-repo `browse/server.py` stubs | in `test_browse.py` |
| the agent-loop experimental record | `browse/runlog.py` | `test_runlog.py` 24 |
| the GDS renderer and its crop | `analog/engine/layout/render_gds.py` | `test_render_gds.py` 5 |
| the schematic, and its cross-probe keys | `analog/engine/schematic/render_svg.py` | `test_render_svg.py` 2 |
| its exec surface: render, KLayout, XOR | `browse/tools.py` | in `test_browse.py` |
| cluster reads: list, fetch, remote render, remote wave | `browse/cluster.py` → `deployment/bnl/jobs/remote.py` | in `test_browse.py` |
| what a click will cost, measured | `browse/estimate.py` | in `test_browse.py` |
| agent activity (metadata only) | `browse/agentview.py` | `test_agentview.py` 29 |
| licence-holding stray processes | `deployment/bnl/jobs/procscan.py` | `test_procscan.py` 31 |
| the engine's model, drawn | `analog/engine/layout/abstract_svg.py` | `test_abstract_svg.py` 13 |
| sweeps (tran/dc/ac/noise/stb/pss/pac/pnoise), operating points, nutascii | `analog/engine/wave.py` | `test_wave.py` 66 |
| Monte Carlo across iterations | `analog/engine/mc.py` | `test_mc.py` 12 |

Re-measured 2026-08-20, all three repos, every file run directly rather than
trusted from the last pass: **tsmc65 335/335** (the table above, plus
`test_atlas` 10 and `test_lyp` 10, plus the `lyrdb` selftest); **tsmc28
131/131** (`test_abstract_svg` 13, `test_atlas` 10, `test_lyp` 12,
`test_lyrdb` 10, `test_render_gds` 8, `test_mc` 12, `test_wave` 66 — this
repo forks the process-specific trio, so its own counts differ from
tsmc65's); **xt011 44/45** — `test_atlas` 19 and `test_lyrdb` 11 are clean,
but `test_lyp` is **14/15**: kit layer 2 (DTI) is drawn by `render_gds.LAYERS`
and has no entry in `layers.PURPOSE` or `layers.UNMAPPED` at all. Not fixed
here — the correct entry needs the X-FAB layertable, which is confidential
foundry data this pass has no access to, and `test_nothing_is_invented_
beyond_the_kit_table`'s whole point is that a guessed entry is worse than a
missing one. This is exactly the risk §10.22 item 4 named in advance.
Detail and the reasoning behind each decision are in
§10.1-§10.19 below; §10.20 is what is *not* done and §10.22 is the ordered next
step.

`python3 browse/estimate.py` prints what this installation believes about how
long things take, and where each number came from.

### Traps for whoever picks this up

- **The whole UI is JavaScript inside a Python string.** Every regex is one
  backslash from an invalid escape; a test compiles `server.py` with
  `SyntaxWarning` promoted to an error, so run `test_browse.py` after any UI
  edit.
- **Measure the picture, do not eyeball it.** Two views shipped looking wrong
  because "I could not screenshot it" was treated as "I could not check it".
  A `getBBox()` sweep over the drawn elements finds a bad frame in seconds.
- **Anything read while it is being written must say so.** The waveform reader
  discards its last time group when the file has no `END`; the cluster
  transport reports UNKNOWN rather than "empty". Both rules exist because the
  alternative is a confident wrong answer.
- **Anything read only PARTLY must say that too, and it is a different
  statement.** A capped read of a finished 7.2 GB transient looks exactly like
  an in-flight one; `state()` grew a fourth answer (`capped`) rather than
  guess between them (§10.8).
- **`import wave` picks up the stdlib WAV module** on a checkout that has no
  `analog/engine/wave.py`, and hands back something with no `read_psf`. Load
  engine modules by path and check for the function.
- **`roots.local.json` is the only file the browser writes.** Keep it that way.
- **Run more than one repo's server before believing anything about ports or
  discovery.** Two separate defects sat latent in that code from the beginning
  because a single instance exercises neither (see "They run side by side").


### 10.1 What is done

- Phase 0: `lyrdb.py` marker export, emitted best-effort by `run_drc` in both
  repos. Parser regressions cover both Calibre edge encodings and the colon
  rule names. 84/84 real 28 nm markers and 90 real 65 nm markers round-trip
  coordinate-exact.
- Phase 1: `atlas.py` — bounded local crops, whole-design overview, rule→layer
  focus, truncation recorded in `index.json`. Delivered automatically into
  every flowrun stamp by `run.emit_atlas()`, linked from `report.json`/`.md`,
  never affecting a verdict. Ported to 28 nm.
- Phase 2: `reporthtml.py`, byte-identical on re-run, emitted from the same
  dict as `report.json`/`report.md`. Ported to 28 nm along with the stamp dir.
- Phase 2a: `lyp.py` layer map, emitted beside every DRC run; `klayout_open.ps1`
  opens layout + markers + layer map together.
- Phase 3: `browse/` — three panes, local roots, type-dispatched viewers, GDS
  summary instead of a hex dump, path-shaped file URLs so an embedded
  `report.html` resolves its own images. Confinement is the tested part.
- Phase 4 (badges), 2026-07-31: pane 2 reads `report.json`, `manifest.json`
  and `status.json` and badges the row with verdict, DRC state, LVS state,
  stage count and job/licence state; `LATEST.txt` is followed one level so a
  circuit directory shows its newest run's verdict; the filter matches badge
  text. Landed in both repos.

### 10.2 The exec surface — LANDED 2026-07-31

`browse/tools.py` holds the browser's **entire** ability to run a program: the
on-demand GDS render and the KLayout handoff, one file, so "what can this
server execute" is answered by reading one page. Neither takes a command, a
template or a shell string from the request — one already-confined path,
through argv, no shell anywhere.

The render is a subprocess because `render_gds.py` imports matplotlib and the
browser is stdlib-only (principle 2); it is cached by **content** hash, so the
same stream pulled to two local paths renders once and a geometry-identical
re-run reuses the render. The cache is outside every root — writing beside the
GDS would drop a file into a signed stamp directory.

Three things only the real data decided:

- **The server had to become `ThreadingHTTPServer`.** A cold render of the
  ctrl2 pilot is 70 s, and on the single-threaded server that request blocks
  every other one — the whole browser freezes, including the listing you would
  use to look at something else while waiting. Measured on the live server:
  listings return in 12–17 ms with a 70 s render in flight.
- **matplotlib picks its format from the extension.** A temp name ending
  `.part` failed inside `savefig` on every real render, while the stub renderer
  in the tests — which writes whatever bytes it is told to — passed happily.
  The lesson is asserted on the temp *name* so it survives a refactor.
- **POST needed a same-origin guard.** A page on another origin can send a
  simple POST to `127.0.0.1` with no preflight; it cannot read the reply, but
  the exec would already have happened.

Verified through the live UI on the 1.65 MB ctrl2 block: render 70.3 s cold,
0.001 s cached, 2340×1530 in the pane; KLayout launched from the button with
the layer map and 54 real markers. `test_browse.py` 45/45 in both repos.

ONR got the same plus the launcher it never had. Its render path is exercised
through the real subprocess but **not against a 28 nm stream** — that repo has
no local layouts at all. Which is the finding below.

### 10.3 Cluster roots — LANDED 2026-07-31

`browse/cluster.py`, entirely through `deployment/bnl/jobs/remote.py`. Three
reads — list, fetch, render — and the transport's invariants come free, of
which the load-bearing one is the tri-state: if `UNKNOWN` degraded to an empty
listing, the browser would show a confidently empty flowruns directory
whenever the VPN was down. That has its own test.

Measured against the real cluster: `ms_pilot` flowruns, 12 circuits badged, in
**0.41 s** (0.004 s cached); a 46 kB `lvs.rep` in 0.16 s; a 2.8 MB `lvs.out`
capped at 512 kB and saying so; a 28 nm GDS shown by metadata alone, never
pulled. The first answer it gave was worth having: `comparator` is **FAIL at
assemble_top, LVS INCORRECT**.

**Badges come back with the listing.** Badging a remote directory the local
way is one round trip per entry. The lister inlines the small, known-named
badge files (capped at 64 entries × 16 kB) and the *same* `model` functions
compute the badges locally — so a cluster PASS and a local PASS cannot come to
mean different things. That shared definition is `model.badges_from_text`.

**Confinement is two-sided**, and both halves are tested against the real
cluster: a cheap local rejection so a malformed request never reaches asic7,
and the authoritative realpath+commonpath check *on* the cluster, because only
the cluster can resolve a cluster symlink. Remote paths are bound into the
shipped script through **quoted heredocs** rather than a charset whitelist —
strictly stronger, and a cluster path with a space in it becomes a fact to
handle rather than a request to reject.

**The shared filesystem is declared, not inferred.** matplotlib went into
`~/miniforge3` on asic7 (user-level, no root) and the 28 nm divider renders in
7.2 s. Since the homes are shared, the same render should come off any host —
checked, not assumed: asic6, asic7 and asic8 produce a **byte-identical** PNG
(sha `1dd0e73d3d48`). So a render is an artifact of *(filesystem, path)* and
the host is provenance; both caches now key on a root's `fs`, declared in
`roots.json` and defaulting to the host. Sharedness is a fact about a site,
and a wrong guess would serve one machine's layout as another's — the default
has to be the safe one.

Two costs recorded rather than hidden: the transport is **located, not
copied** (ONR has no python transport, and principle 5 does not stop being
true at a repo boundary), which is a real cross-repo coupling; and the plain
`browse/` fork now spans five files.

### 10.4 Visual regression (XOR) — LANDED 2026-07-31

`analog/CLAUDE.md` has required a geometry-identical regression before any
engine change lands, and it was checked by reading coordinates. It is now a
verdict, and when it fails, a drawing of exactly what moved.

**Empty is the pass**, stated as a verdict rather than shown as an absence.
When it fails the diff is itself a GDS, so the *ordinary* renderer draws it —
no second renderer, same visual language as every other layout view
(principle 4). Measured on the ctrl2 pilot: identical → IDENTICAL, no diff
file, 1.4 s; all 32/0 (M2) stripped → 22881 shapes rendered in 7.3 s, and the
picture is exactly the missing M2 routing as standard-cell rows.

**`-l` is mandatory, and the reason is the picture, not the verdict.** Without
it a layer present in only one layout is skipped: the stripped case still
exits nonzero but writes a **130-byte empty output**, so the pane would show a
blank diff beside "differences exist". Measured both ways before the flag was
chosen, and asserted on the argv so it cannot be dropped later.

The verdict comes from the **exit code**, never the summary text — a summary
that parses to zero rows must not become a silent PASS, which is the one
failure mode a regression check may not have. Compare targets are discovered
(the same file name in sibling stamps, newest first) and confined through the
same `roots.resolve` as the primary path: a second path in a request gets no
more trust than the first. Local-only, like the KLayout handoff and for the
same reason.

### 10.5 Agent activity + Interactions panel — LANDED 2026-07-31

`browse/agentview.py` and a new **Interactions** strip below the three panes:
things you *do*, kept out of the panes you read. Two tabs — Processes and
Agent.

**Processes** (`deployment/bnl/jobs/procscan.py`) finds tool processes still
holding licences. Measured live: **17 stale sessions across 11 hosts**,
including Virtuoso sessions of 62.8 d (asic8), 39.0 d (asicdesign) and 31.7 d
(asic2, 8.5 GB). Identity comes first — a bare `ps -u` returns 87 processes on
asic7 of which ~75 are a VNC/GNOME desktop, so age-and-orphanhood would
condemn the lot. The strong rule is derived: `oa_worker.py` drives
`virtuoso -restore worker.il` over stdin/stdout **pipes**, so such a worker
with `ppid 1` is unreachable by construction — which separates asic7's
163-hour worker from asic6's live one with the same command line. Job records
protect anything under a running job. Two live-data corrections: *idle and
orphan must be judged separately* (the 62-day Virtuoso has a tty from a VNC
session that is still up), and *Cadence helpers detach but name their session*
(`-mpssession virtuoso4151088`), so ppid-only grouping split one 17-process
session into a tree plus six false orphans.

**Agent** (§7) tails the transcript — 204 MB across 25 sessions, bounded and
cached by `(size, mtime)`. §7.2 is the whole design: an **allowlist** of five
input fields, everything else dropped unread. A denylist is correct only until
someone adds a field. Verified on the live endpoint: 30 keys emitted, none
forbidden. Thrash detection is the highest-value item and costs nothing — pure
analytics over data already collected.

### 10.6 The internal-model track map — LANDED 2026-08-01

`analog/engine/layout/abstract_svg.py`, beside `assemble_top.py` which writes
those fields, so one place knows what they mean. **SVG, not matplotlib**: a
track map is rectangles and labels, so the raster path costs a subprocess and
buys nothing, while SVG keeps net names as findable text, needs no dependency
(the browser stays stdlib-only), and gives §6.4's cross-probe something to key
on — every shape carries its net. The emitted SVG holds no script, so it stays
embeddable in `report.html`; the browser injects it inline and adds the
interactivity itself.

**The measurement that made it worth building.** Lane sharing — two nets at
one track `y` — splits into two cases that are nothing alike. On the real
vref: three lanes are shared, and only **one** has nets whose x spans actually
intersect: the `vdd` **rail** (0.7–238.4) against the `vrefn` track
(109.1–238.8), **129.33 µm of overlap**. The other two are ordinary packing —
disjoint segments at a common y, which is how a lane is meant to be used.
Reporting them together is noise, so `facts()` separates `shared_lanes` from
`overlapping_lanes` and names which net is a rail, because that changes the
reading. Across all 28 abstracts in the repo, exactly one block reports an
overlap: vref, the block this section was written about.

The browser gets the special-cased `*.abstract.json` viewer §5.2 promised: a
facts line, the overlap as a warning, a chip per net, and the map. Clicking a
net isolates it — 236 of 249 shapes dimmed on vref.

### 10.7 Waveforms, and the partial view — LANDED 2026-08-01

`analog/engine/wave.py`. Built for the INCOMPLETE file first, because that is
the requirement that shapes everything else: a long transient runs for an
hour, and seeing the first 10% and deciding to kill it is worth more than any
polish on the finished picture.

- The header's `stop` gives a partial file an **honest denominator** — "11.2 µs
  of 20 µs, 56.1%" is a decision. With no `stop`, the fraction is `None`
  rather than invented: guessing one from the data reports 100% for every
  partial file.
- No `END` means **running**, never final, and the state is stated first.
- **The last time group is discarded** while unterminated. Spectre writes one
  line per trace, so an in-flight file very likely ends short a signal, and a
  curve that dips because a value had not been written yet is worse than one
  that stops a step early.
- Decimation keeps **min and max** per bucket: stride sampling drops exactly
  the spikes and glitches that are the reason to look.
- The **x axis spans the intended sweep**, so a growing run does not rescale
  under you, and the unfinished remainder is shaded.

In the browser: a state badge, per-trace chips, and a *watch* checkbox that
re-polls every 4 s and stops itself on completion. The picture was **measured**
this time — across a refresh the ink grew rightward while the Y extent stayed
identical, confirming the axis holds still.


**Reading it (added the same day).** Traces group by **unit** into stacked
panels sharing the x axis: on the real run `Vdd3:p` swings −286 µA to 8.6 µA
while the spikes swing 0 to 1.2 V, and one shared axis makes the current a
flat line. `split` gives a panel per trace, `overlay` is the old behaviour,
and a single-unit file still gets one panel — the default costs nothing when
there is nothing to separate.

**Zoom had to re-fetch, not magnify.** The picture is a decimated envelope, so
scaling the SVG reveals nothing new; the window travels to the server, which
re-buckets the samples inside it. The plot geometry travels *with* the picture
as data attributes, because the client maps cursor to time and hardcoded
margins would desynchronise silently. A real leak surfaced here: drag handlers
on `window` survived every re-render, so a stale closure fought the live one
and panning appeared to reset the zoom — both this and the track map now use
pointer capture.

### 10.8 Remote waveforms — LANDED 2026-08-01

`browse/cluster.py` grew a fourth read. The transient is parsed **and
plotted** on the cluster; only the picture comes back — the same rule §5.3
states for layouts, and for a stronger reason, because the numbers here are
larger than anything in this plan so far. The cluster holds **107 PSF
transients over 40 MB, the largest 7.2 GB**. Pulling one to look at it is not
a slow operation, it is an impossible one.

**The reader is SHIPPED, where the renderer is LOCATED, and the difference is
the point.** `render_gds.py` imports matplotlib, so it has to run where its
dependencies are and the remote script walks up the tree to find it.
`wave.py` is stdlib-only and 22 kB, so `cluster.wave` sends the source
base64-encoded in one line (`_script` refuses a newline in a bound value —
that guard is right for paths and there was no reason to weaken it). Two
things that buys:

- **The cluster checkout does not have the reader.** Measured, not assumed:
  `wave.py` was written on 2026-08-01 and `~/Documents/ms_pilot/analog/engine/`
  had no copy. A feature that works only when the remote checkout is current
  breaks silently and blames the file.
- **The picture is drawn by byte-identical code**, which is §10.3's badge
  argument applied to plots. The partial-file rule — discard the last time
  group while the file is unterminated — must not be able to differ between
  a local read and a cluster read, on the one view whose job is judging a run
  that has not finished. A test asserts the shipped bytes are the file the
  local reader was imported from.

**The stat comes first and can stop the read**, in the same round trip. That
is what makes an enormous file safe to click: measured against the real 7.2 GB
transient, **0.16 s to be told it is too big, 4.00 s to actually read it.**
Statting from here and then deciding would be two round trips for a question
whose answer is usually "go ahead".

Measured end to end, through the live UI:

| file | wall | remote parse | plot | picture |
|---|---|---|---|---|
| local `lif_neuron` 1.1 MB | 0.07 s | — | — | 92 kB |
| asic7 `sar_kernel` PEX 38.1 MB | 2.7 s | 1.90 s | 0.25 s | 215 kB |
| asic7 `ams_bench` **7.2 GB** | 4.3 s | 3.89 s | 0.38 s | 201 kB |

The SVG is zlib-compressed before base64 — 6.5× on the real 73-trace file
(1.29 MB → 198 kB on the wire), which matters because the watch checkbox asks
again every few seconds.

**Two things the real data changed.**

*`capped` had to become a state of its own.* "No `END` means the run is still
going" is true only when the whole file was read. Past the 64 MB head budget a
**finished** run reads back with no `END`, and the view would have reported a
run that ended two hours ago as in flight — the one confident wrong answer
this feature may not give. What a capped read knows about the end of the file
is nothing, so that is what it now says: `complete | capped | running |
starting`, with the fraction phrased as *read to 0.8% of 13.2 µs* rather than
*0.8% complete*, because those are different facts. Watch is not offered on a
capped read either: the head of a file never changes, so polling one re-reads
64 MB to draw the same picture.

*73 traces is not a plot of anything.* The sar_kernel run has 73 and the
`ams_bench` one 98; drawn together they were a 1.29 MB SVG in which the 16
`sl*` and 16 `dn*` lines lay exactly on top of each other. `wave.choose` caps
at 12 **and says so**, with a chip per trace and a "draw all 98" escape —
a silent cap would be the pane lying about the file, and a cap that cannot be
lifted is a lie about the data. Naming traces overrides it completely.

One trap closed on the way. `import wave` is a **silent pass**: `wave` is also
a stdlib module (the WAV one), so on a checkout without
`analog/engine/wave.py` the import succeeds and returns something with no
`read_psf`. The failure then surfaces as a viewer error on one file rather
than "this installation has no reader". The locator now loads by path and
checks for the functions, falls back to the sibling checkout, and prints a
line when it skips a reader that is too old.

### 10.9 What a click costs — LANDED 2026-08-01

`browse/estimate.py`. Several things here are one click from a pane that stops
responding for a minute, and an unexplained pause is indistinguishable from a
hang — which is how a user learns to distrust a tool. So each expensive read
says beforehand roughly what it will take.

**The rates are measured, and they are this machine's.** A constant compiled
into the source would be a claim about someone else's disk: the cluster parses
PSF at 0.055 s/MB and this laptop at 0.037. Every operation that finishes
records `(bytes, seconds)` into a store beside the render cache (never inside
a flow tree — principle 6), and the next estimate is the **median rate** over
what actually happened. Seeds are real measurements too, cited in the source,
and the first live sample of the same kind starts displacing them.

Verified through the UI on the ctrl2 pilot: the button reads **"Render ~70 s"**
before the click, counts *"rendering… 43 s of about 70 s"* during it, and
finished in **72 s** — 2.4% off, from a seed of one. Nothing here can report
real progress (matplotlib does not), and a counter that admits it is guessing
beats a fake progress bar.

Three rules the data forced:

- **Estimate what will be READ, not what the file weighs.** Every big read
  here is head-capped, so a 7.2 GB transient costs what 64 MB costs. Quoting
  7.2 GB back would be a terrifying number that is also wrong.
- **A cache hit is not a measurement.** A 0.001 s dictionary lookup recorded as
  a render would teach the estimator that rendering is instant, after which
  the pane promises an instant render of a stream it has never drawn. Only
  `state == "rendered"` records, and a gated round trip that read nothing
  records nothing.
- **An unmeasured operation says so.** `seconds` is `None`, not a guess: a
  made-up number on the first click is indistinguishable from a measured one.

Under `CONFIRM_SECONDS` (5 s, and settable — `BROWSE_CONFIRM_SECONDS`) the
estimate is *told*; over it the pane *asks*, once per path, and reads nothing
until you say yes. The honest outcome of measuring first is that the ask
rarely fires: the head budget bounds every transient at about four seconds, so
what the gate mostly protects against is a slower day. The one click that is
genuinely expensive is the GDS render at **45.4 s/MB measured**, and that
number now lives on the button.

The watch poll also learned. Four seconds is right for a local file that
parses in 40 ms; against a 38 MB transient on asic7 it would keep the cluster
busy essentially continuously to move a picture a few pixels. The interval is
now four times the last measured read, and the pane says which.

### 10.10 Every analysis, the operating point, and a real picker — LANDED 2026-08-01

The reader plotted transients and nothing else. Opening `dc1.dc` did not fail:
it drew an **empty frame and called it "no samples yet"**, because the
independent variable was hardcoded to `time` and a DC sweep's is called `dc`.
No value line matched, every group was dropped, and the picture was confidently
blank. A wrong axis name does not fail loudly — which is why the name is now
read rather than known.

**The SWEEP section is authoritative**, and it answers three questions at
once: what the x variable is called, its units, and its `grid`. Measured
across all four real analyses in this repo — tran `time`/s/1, dc `dc`/V/1, ac
`freq`/Hz/**3**, noise `freq`/Hz/**3** — where grid 3 means logarithmic. The
axis is now labelled with the file's own name for it (`freq (Hz) - log`), so
"what am I looking at" is answered without reading the path.

Two format facts the transient path had never met:

- **AC values are complex** — `"out" (re im)`. A complex trace is not one
  curve but a choice of five (dB, magnitude, phase, real, imaginary), and that
  choice is a **view, not five traces**: splitting magnitude and phase into
  separate entries would make picking `out` on a Bode plot mean picking it
  twice, and the panel grouping would then have to re-pair them by unit. The
  unit follows the part, so gain in dB and phase in degrees land in different
  panels — the same rule §10.7 established for amps against volts, arrived at
  from the other side. 20log10(0) is −∞ and becomes a **gap**, not a floor: on
  the real `ota_pss` sweep that correctly makes `vdd` undrawable in dB and
  drawable in `real`.
- **Noise files interleave multi-line STRUCT blocks** of per-device
  contributions with the scalar traces. They are skipped explicitly rather
  than by a regex happening not to match, because a bare float on its own line
  is only safe to ignore when you know which block it is inside.

Log axes are not just a scale. Ticks are computed **before** the gridlines and
the lines are drawn at them — five equal bands labelled by linear
interpolation would put "3.2 kHz" on a line standing somewhere else, a picture
that lies quietly. And **zoom happens in decades**: verified in the live UI on
the `ota_pss` AC sweep, the frequency under the cursor held at **316.226 kHz**
across a wheel zoom from 5 decades to 3.92, where a linear cursor mapping
would have said **50 MHz** — a factor of 158. The renderer states which axis
it drew (`data-xlog`) rather than leaving the client to infer it.

Measured, all four, through the live server:

| analysis | sweep | axis | traces | picture |
|---|---|---|---|---|
| tran `lif_neuron` | `time` (s) | linear | 5 | 2 panels, A and V |
| dc `cmos_inverter` VTC | `dc` (V) | linear | 1 | 1.2 V → 40.5 µV |
| ac `ota_pss` | `freq` (Hz) | **log** | 10, 9 drawable | dB, 1 k–100 M |
| noise `ota_pss` | `freq` (Hz) | **log/log** | 10, 1 drawable | V/√Hz |
| stb `inv_vref_smoke` | `freq` (Hz) | **log** | 6, all complex | −34.8…−1.13 dB |
| **op** `smart_sch` | *none* | — | 2 values | a table, not a plot |

**Not every PSF file is a sweep.** An operating point has no independent
variable at all — it is a list of typed values:

```
VALUE
"Vdd:p" "I" -6.792475613049781e-05 PROP(
"units" "A"
)
"out" "V" 2.657313782574502e-01
```

That is a **table**, and drawing it would mean putting a current and a voltage
on one axis and calling the result a plot. Sent down the sweep path it
produced the same confidently empty frame as the DC file — a third instance of
the same failure.

**The extension decides nothing here.** This flow writes its operating point
as `psf/op1.dc`, so the name says "DC sweep" and the content says otherwise —
and there are **12 022** of them on the cluster against 241 real DC sweeps.
The discriminator is structural: a file that reaches `VALUE` having seen **no
SWEEP section and no TRACE section** is a point. Both are absent on every real
operating point measured and present on every real sweep (tran, dc, ac, noise,
stb). The pane gets rows — sortable by name, value or file order, with a
filter, because a chip-level op point is every node voltage and every branch
current — and no SVG is built at all. Verified on the real `op1.dc`:
`Vdd:p −67.9 µA (A)`, `out 266 mV (V)`, units picked out of the `PROP(` block.

**`.stb` came free and is now mapped**, on the same evidence rather than on
faith: a real stability run is structurally an AC sweep — `freq`, grid 3,
complex `LOOPGAIN` — and there are **8 700** of them. Measured on
`inv_vref_smoke`: loop gain −34.8 to −1.13 dB, phase 17.7° to 180°, and the
`phase` chip is one click, which is the question a stability run is opened to
ask.

**The picker is checkboxes now.** A "selected" chip and the only chip left
look identical, and on a 98-trace file the state of the selection was
unreadable at a glance; a box is ticked or it is not, and the browser draws
that better than any class of ours. Three rules the real files forced:

- **Only drawable traces get a box.** A remote noise run on asic7 declares
  **5886 traces of which 3 carry a curve** — the rest are per-device STRUCT
  blocks. A checkbox that cannot change the picture is a control that lies,
  and the 5883 are reported as a count with the reason instead.
- **Unticking everything draws nothing.** `signals` is None-or-list all the
  way to the cluster: `None` means "you choose, and cap it", `[]` means the
  user unticked every box. Collapsing them would make the last untick
  silently redraw all 98 — the control fighting the user at the moment they
  are most explicit. A query string cannot express an empty list, hence
  `nosig=1`.
- **Do not enumerate what cannot be drawn.** That same 5886-trace file was
  shipping **387 kB of trace names and units to deliver a 9 kB picture**. The
  summary now lists the drawable names and counts the rest: **398 kB → 10.9
  kB**, and the payload is essentially the picture.

### 10.11 The GDS crop, made fast — LANDED 2026-08-01

`render_gds.main()` read its crop *after* drawing, applying it with
`set_xlim`, so a 20 µm window off the 1.65 MB ctrl2 pilot cost exactly what
the whole die cost. It now clips before drawing.

**The profile said where the time is, and it is not where the plan assumed.**
On that pilot — 259 247 polys, 172 × 154 µm: `parse` 0.10 s, `flatten` 0.65 s,
`draw_polys` 6.30 s, **`savefig` 64.72 s**. Ninety percent of it is matplotlib
rasterising a quarter of a million unfilled hatched patches. So clipping does
not make `draw_polys` quicker — it hands `savefig` **4 375 patches instead of
259 247**. End to end, through the real CLI:

| | old | new | |
|---|---|---|---|
| whole die | 72.5 s | 73.0 s | unchanged, as it must be |
| 50 µm window | 71.4 s | 9.6 s | **7.4×** |
| 20 µm window | 71.5 s | 3.3 s | **21.8×** |

**All three PNGs are byte-identical to the old renderer's** — the
geometry-identical regression `analog/CLAUDE.md` requires, run as a sha256 of
the picture rather than by reading coordinates.

That took one correction the speed number would have hidden. A polygon
entirely outside the window still has a 0.5 pt **stroke**, and one just past
the edge paints a fraction of a pixel inside it; clipping on the bare window
differed from the slow path by **37 pixels of 3 580 200** — invisible, and
still not the same picture. `CLIP_PAD` draws 2% beyond the window; 1% was
measured to be exactly enough for a byte-identical PNG. `test_render_gds.py`
asserts the pad on the argv `draw_polys` actually receives, and each of its
guards was checked by removing what it protects and watching it fail.

**And the crop had never once been visible.** The render completed, the file
was written, the pane said *"rendered in 3 s"* — and the `<img>` got a **403**,
because a crop is cached as `<hash>_<window>.png` and `_CACHE_NAME` admits only
hex. That guard's own comment already records this happening for the remote
render's `r` prefix: *"a guard and the thing it guards have to be changed
together"*. It happened again and hid longer, because **the button and the
picture are different subsystems and only the picture is evidence**. The test
now asks `tools.render_gds` to mint a real cropped name and checks the route
will serve it, rather than asserting a shape this file invented.

One consequence: the counter no longer quotes the die's estimate over a crop.
72 s promised for a 4-second wait is a worse answer than no estimate, and a
crop's cost tracks its window and the local polygon density — neither of which
the file size predicts.

### 10.12 The live job panel — LANDED 2026-08-01

§6.5's last piece: `events.jsonl` and `progress.py`'s measured-rate ETA
already existed, only the view was missing. It is a third tab in the
Interactions strip, beside Processes and Agent, reading `remote.list()`.

**The ETA is the job's own.** `progress.py` measures a rate from the work
actually done and publishes `frac`, `phase`, `eta_s`, `rate_per_s`; the
browser re-derives none of it. A second cost model would drift from the
first, and the flow is the only thing that knows what a phase means.

**A `list` is expensive and that shapes the whole panel.** Measured against
asic6: **5.9–9.9 s** for `list` (it stats every job record — there are 389)
against **0.11 s** for `events`. An "ambient awareness" pane that re-polls a
ten-second call every few seconds is not ambient; it is a permanent job on the
head node. So the route is TTL-cached (0.02 s on a hit), it records its cost
in `estimate.py`, and the client sets its own refresh interval from what the
last poll actually took — the same rule the waveform watch follows, and no
second cost model. It is also not fetched at all until asked: the tab opens
saying so rather than spending ten seconds on a pane nobody looked at.

Three things the real records decided:

- **A heartbeat that stopped is not a running job.** `state` is whatever the
  record last managed to write, so a job whose host died stays `running`
  forever. The heartbeat age is the only thing that can contradict it, and
  the row says `running` + `stale 2.1h` rather than picking one.
- **No fraction, no bar.** `progress.py` leaves `frac` null when the flow
  cannot say how far along it is, and a bar invented there would be the most
  confident lie on the page.
- **Running first, then most recent.** The panel answers "what is happening";
  sorting by start time alone put last week's finished job above a live one.

Verified against the live cluster: 389 records, 277 done / 109 failed / 3
killed, cold read 5.9 s and cached 0.02 s, table capped at 40 rows with the
remainder stated. **The running-state rendering was exercised against
synthetic records, not a live job** — nothing was running on asic6 at the
time, and launching one to look at it was not worth a cluster side effect.
The branch is covered (progress bar at 42%, `eta 4m`, phase, the stale badge,
and no bar when `frac` is null); a genuinely live job has still not been seen
through this panel, and that is the one claim here that is not measured.

### 10.13 Schematic ↔ layout cross-probe — LANDED 2026-08-01

§6.4, and the plan's own claim about it turned out to be exactly right: *"both
sides already key on net names, so the join is free."* One selection drives
both pictures, and **neither SVG knows the other exists** — the schematic tags
its leads and labels with `data-net`, the layout track map already tagged its
rectangles the same way (§10.6), and highlighting is asking both the same
question. No coordinate mapping, no index built ahead of time.

**The join was measured before it was built.** Across the real artifacts each
schematic matches its own layout on *all* of its nets and nothing else comes
close: `ota` 8/8 → `ota_lvs`, `lvds` 7/7 → `lvds_lvs`, `lif` 7/7 → `lif_lvs`,
`fc` 15/15 → `fc_lvs`. Through the browser (which also counts `vss` and the
internal `nz`) the winner scores **9/10 against 5/10** for the runner-up. That
margin is the evidence the pairing is sound rather than a filename
coincidence, so the score rides on every option and a candidate sharing **no**
nets is not offered at all.

`render_svg.py` gained `data-net` on every net-bearing element and `data-dev`
on each device group. It is **purely additive**: generating each of the four
schematics with the pre-change and post-change renderer and stripping the new
attributes back out reproduces the old output byte for byte, all four. The
existing server-side `highlight=` re-rendered the whole SVG to colour one net;
with the net on the element the client does it on a click, which is the
decision §10.6 already made for the track map.

Measured through the live UI on `ota_op.svg` + `ota_lvs.abstract.json`:
clicking `vinp` lights **3 of 92** schematic elements and **5 of 52** layout
shapes and dims the rest in both; `ntail` lights 9 and 3; clicking again
clears both.

Two things the real roots decided, neither of them visible from the design:

- **The search must span roots.** Schematics are published to
  `analog/results/schematic` and layout abstracts live under
  `analog/work/layout_cc` — different roots by design, so a same-root search
  found nothing and the feature looked broken on precisely the artifacts it
  was built for.
- **And then it must dedupe.** `repo` is the checkout and `analog/work` is
  inside it, so every layout came back once per root and the picker offered
  each as a duplicate pair. Deduped on the real path, keeping the first root —
  `roots.json` lists the specific trees before the catch-all.

One defect the test found rather than the picture: `_esc` escapes `&<>` for
text content and leaves `"` alone, which is right there and wrong in an
attribute — a net name containing a quote would have closed `data-net` and
turned the rest into markup. No net in this flow has one; that is not a reason
to emit an attribute that only works because of it.

Not done here: the op-point JSON is read and its size reported, but the
**voltages are not overlaid on the layout** — §6.4's other half. They are
already baked into the schematic text by the back-annotation step, so the
missing piece is showing a net's voltage when it is highlighted in the layout.

### 10.14 Host identity: the store is a filesystem — LANDED 2026-08-01

The job panel shipped reading `asic6`, and that was the wrong shape of
configuration rather than the wrong value. §10.3 had already settled the
principle for renders — *"a render is an artifact of (filesystem, path) and
the host is provenance"* — and the jobs mechanism predates it.

**Measured before changing anything.** `list` run against asic6, asic7 and
asic8 returns the **identical 389-job set** (sha `b7a1a6ab8836`), and those
records name **11 different hosts** as where the work actually ran. What
differs is only latency: **asic6 5.90 s, asic8 13.93 s, asic7 22.99 s** — a
3.9× spread. So a hardcoded host was never wrong about *what you see*; it was
an unexplained bet on reachability and speed, and a single point of failure
for a read any host can serve.

`deployment/bnl/jobs/hosts.py` — the module that already answers "which host"
— gained the second question. `pick()` answers *where should this job run*
(capacity; the work lands there). `shared_read()` / `SharedReader` answer
*which host should perform this read* (reachability; the read lands nowhere).
**The read is the probe**: a separate reachability check would double the
round trips to learn what the read is about to say, so the common path costs
exactly one call. Verified against the real cluster: the remembered host is
reused; `asic-nope` and the long-dead `asic5` are skipped and asic8 serves the
same 389 records; and when nothing answers the result is **UNKNOWN, not an
empty store** — the tri-state a jobs panel must not lose.

The browser keys its cache on the **fs** (`roots.json` already declares it),
names the host that answered as provenance, and lets an explicit `host=` win.
Candidate order comes from `hosts.py`, not `roots.json`: taking roots first
put asic7 at the head and the panel habitually asked the 13.5 s box.

**Two defects fell out of looking.**

1. **`jobs --host asic7 ls` silently read asic6.** A parent-parser option also
   lives on every subparser and the subparser runs *second*, so its default
   overwrote the value. The parent parser's stated purpose — "accepted BEFORE
   or AFTER the subcommand" — was only ever half true, and it failed the quiet
   way: the operator names a host and the tool goes elsewhere. Fixed with
   suppressed defaults, which also turns "was a host named?" from a guess into
   a fact — and that is the flag deciding whether a read may wander.
2. **`test_cli.py` could not fail under pytest.** Its `check()` counted a
   failure and printed it, then returned; a pytest test that returns None has
   passed. Eleven tests were green by construction, and the negative control
   for defect 1 passed *both* ways before this was found. `check()` now
   raises, and `main()` catches per test so the script runner still prints a
   tally. With it fixed, reverting defect 1 fails 2 tests.

   **A sweep found it was not one file.** All six hand-rolled suites in
   `deployment/bnl/jobs` shared the identical non-raising `check()`:
   `test_cli`, `test_hosts`, `test_jobrec`, `test_license`, `test_progress`,
   `test_remote` — **66 of the package's 85 tests could not fail**, on the
   very mechanism this section is about. All six are fixed and each was
   proven by mutation: with every check forced to fail, each file now reports
   all of its tests red (11, 14, 9, 6, 12, 14). They all still passed
   afterwards, so the tests were *correct* — they simply could not have said
   otherwise.

   The sweep is now a **gate**, `test_harness_can_fail.py`: it walks the repo
   and refuses any `test_*` from which no AssertionError can escape, directly
   or through a helper in the same module. It is conservative by design
   (anything it cannot see through counts as guarded) and it has **no
   allowlist** — when `test_browse.py`'s escape check turned up, which asserts
   by `compile()`ing with SyntaxWarning promoted, the answer was to teach the
   scanner that `compile` raises rather than to name the test. Its own
   negative control is written in the shape of the real defect. 86 test files
   scanned across both repos; 0 unguarded.

Reads now fail over in the CLI too (`ls`, `top`, `why`, `verify`, `wait`,
`watch`) unless a host is named. `run` is deliberately excluded: where work
lands is a decision, and `--host auto` is how you ask for it to be made.

### 10.15 Per-device noise: a table — LANDED 2026-08-01

The STRUCT blocks a noise file interleaves with its traces were skipped
(§10.10). They are the answer to the question a noise plot is opened to ask,
and the answer is a **ranking**, not curves: a real cluster run breaks down
**5 886 devices**, and drawing them is not a plot of anything.

**The join is positional and spans three sections.** `TYPE` defines a struct
per device *master* with ordered, named fields — each carrying a `noise_type`
(thermal / shot / flicker) and ending in `total`. `TRACE` maps *instance* →
master. `VALUE` writes the numbers as a bare list. Read one without the others
and the table is anonymous numbers.

**It is integrated as it is read**, which is what keeps it affordable: 5 886
devices over 146 frequencies would be ~860 000 floats to answer one question,
and the question is about the band. Each device keeps a handful of running
sums (total, and one per mechanism) weighted by the frequency step, so the
cost is O(devices) rather than O(devices × points).

**The check that makes it trustworthy.** Contributions are power densities
referred to the same output, so they add — which was verified rather than
assumed. On the real `ota_pss` run the device totals sum to **1.57871e-08 V²**
against an integrated `out` trace of **1.57871e-08 V²**: a ratio of
**1.000000**. So a share really is a share of the noise you measure, and the
check is cheap enough to keep as a test.

Measured on the two real files:

| | devices | result |
|---|---|---|
| local `ota_pss` (139 kB) | 9 | 126 µV rms over 1 k–100 MHz; `X1.M8` **26.5%**, thermal |
| asic7 `fc_lvs` (87.9 MB) | **5 882** | parsed in **3.6 s**; four PMOS at **7.2%** each, **flicker** |

That second row is the feature working: four devices dominate, the mechanism
is flicker, and flicker says *make them bigger*. Naming the mechanism is half
the answer — "M3 is 42% of the noise" alone does not tell you what to change.

Four rules, each with a negative control that fails when the rule is removed:
a short-but-closed group is **skipped, not padded** (positional matching would
read `id` where `total` belongs and invent a contribution — and it moves the
mechanism split first, while the headline number still looks fine); an
**unlabelled field is never folded into a mechanism**; the integral really
weights by frequency step; and the share denominator is the summed totals.

When the read was capped, the band is what was **read** — the table says so,
because an integral over a truncated band is not the integrated noise.

### 10.16 nutascii — and deleting the reason for it — LANDED 2026-08-01

ONR's characterisation flow asks spectre for `-format nutascii` where the
analog flow asks for `psfascii`: **the same binary, the opposite flag**, in
`char/divider.py` and `char/libgen.py` against `spectre_flow.py`. Nothing about
the repo, the PDK or the tool version chooses it — and `char/nutascii.py`'s own
docstring gives the reason it was chosen: *"the one Spectre format that parses
without a PSF library"*. That stopped being true when `wave.py` was written,
which parses PSF-ASCII with no library either.

So the work split in two: read the format, and remove the second copy of it.

**Reading it.** `wave.read_nutascii` produces the same `Wave`, so panels,
decimation, zoom, the trace picker and the partial-file rules all come free.
`read_any` decides **by content**, because spectre is told `-raw <path>` and
writes a file with no extension at all — the same rule the operating point
forced. Three traps, every one already paid for in `char/nutascii.py`, whose
docstring is the specification this follows: variable 0 sits on the
`Variables:` line itself; every point is prefixed by its **index**, which is
not a variable; and **a point wraps across lines**, so a point is not a line.
That third one is the expensive one — a line-per-point reader worked on a
3-variable bench and, on a 4-variable one, silently returned `time` = the
point index with every signal shifted one place, which looks exactly like a
divider that has latched up.

**Removing the copy.** `char/nutascii.py` is now a thin adapter over
`wave.read_nutascii`, because the browser SHIPS `wave.py` to the cluster to
read a rawfile in place and a shipped module cannot import a sibling. What
stays in `char/` is the **contract, which is not the viewer's**: the
characterisation flow raises on a short or mis-declared read, because a
silently truncated measurement is worse than none; the viewer says so and
draws what it has, because refusing to show a file you can partly read is the
wrong trade in a pane. One parse, two policies.

The old parser is kept as `_legacy_read` — the **oracle** the shared one was
checked against, not runtime code. Adapter and oracle agree on every path:
the 3-variable file, the 4-variable file whose points wrap, empty, no values,
a declared-variable mismatch and a declared-point mismatch (both `ValueError`,
both messages preserved).

Verified in the browser on a nutmeg rawfile: 3 traces, 400 points, panels
split into **voltage** and **current** off the nutmeg type strings, nothing
off-screen. Five guards, each with a negative control that fails when removed:
dropping variable 0, reading the index as a value, the stride that skips it,
calling a short read complete, and sniffing the format at all.

One trap re-appeared while doing it: the adapter's `import wave` picked up the
**stdlib WAV module** because ONR's copy of `wave.py` was a commit behind. It
now checks for `read_nutascii` and says exactly that — the same guard §10.8
added on the browser side, for the same reason, in a second place.

### 10.17 pss, pac, pnoise — and the index file — LANDED 2026-08-01

The plan said to open a real one before mapping anything. **They already
existed locally**: `analog/work/ota_pss/psf` holds `pss1.td.pss`,
`pss1.fd.pss`, `pac1.pac` with its eleven per-harmonic files, and
`pnoise1.pnoise` — the `ota_pss` bench was built for exactly this ("OTA
unity-gain buffer: pss/pac/pnoise + ac/noise cross-check"). No simulation and
no licence were needed.

**Four of the five shapes already read correctly**, because the SWEEP section
is authoritative and everything since §10.10 was built on it:

| file | analysis | sweep | axis | traces |
|---|---|---|---|---|
| `pss1.td.pss` | `td.pss` | `time` (s) | linear | 11 |
| `pss1.fd.pss` | `fd.pss` | `freq` (Hz) | **linear** | 11, complex |
| `pac1.<h>.pac` | `pac` | `freq` (Hz) | log | 10, complex |
| `pnoise1.pnoise` | `pnoise` | `freq` (Hz) | log/log | 10, +9 devices |

That `fd.pss` row is the interesting one and it is **not a bug**: the file
declares `grid 1`. An fd.pss sweeps harmonics — 0, f₀, 2f₀ — not decades, and
drawing it logarithmically would be this module inventing physics. `grid`
decides, never the analysis name.

**The fifth was the third appearance of one failure.** The bare `pac1.pac` has
a SWEEP section, **no TRACE section**, and a VALUE section holding nothing but
`"harmonic" -5 … 5`. Read as a sweep it is 561 points and zero traces — an
empty frame captioned "no samples yet", exactly as the DC sweep and the
operating point were. It is an **index**: it names what was swept, and the
curves are in the siblings. `.montecarlo` and `.sweep` have the identical
shape, which is why §10.10 correctly refused to map them on a guess.

So `is_index` is structural — SWEEP, no TRACE, nothing drawable — and the pane
gets a card naming the values and **linking the sibling files it can actually
find on disk**, because a killed run has fewer files than harmonics. Verified:
`pac1.pac` → 11 result files, one click each.

### 10.18 Monte Carlo: distributions and bands — LANDED 2026-08-01

`analog/engine/mc.py`. An MC run is **not a file** — it is N sibling
directories, one per iteration, each holding ordinary per-analysis results.
Measured on the real `pa_lvs` run: `smart_mc001` … `smart_mc020`, each with a
`psf/` containing `mc_op1.dc`, three `mc_ac*.ac` and a 41.7 MB
`mc_noise1.noise`. So the aggregator reads nothing itself — it collects what
`wave.read_any` already returns, and **the shape of the answer follows the
shape of the artifact**:

| artifact | per iteration | view |
|---|---|---|
| a point file (`mc_op1.dc`) | one number | **distribution** — histogram, mean, σ, min/max |
| a sweep file (`mc_ac1.ac`) | one curve | **band** — min/max shading with the mean through it |

Both were asked for and neither substitutes: a histogram answers *will it
pass*, a band answers *where does it go wrong*. **The individual iterations
stay drawn in both** — under the band, and listed beside the histogram —
because an aggregate is precisely what hides the outlier, and the outlier is
usually why someone opened a Monte Carlo run.

Measured on the real 20-iteration run, through the browser: `voutp` mean
**501 mV, σ 37.6 mV, 416–562 mV**; the AC band is 20 curves over five decades,
0.41 s to build. It is offered **in the listing of the run**, because there is
no single file to click.

Five rules, each with a negative control that fails when it is removed:

- **The shading is min/max, not ±σ.** A sigma band tucks the worst case out of
  sight, which is the same argument decimation already makes for keeping both
  extremes of a bucket. σ is reported per point alongside.
- **A missing iteration is EXCLUDED, never counted as zero.** A hole is not a
  measurement, and averaging over "whatever happened to be there" is how a
  failed run becomes a data point at 0. The count of skipped iterations is
  shown.
- **Artifacts are the INTERSECTION** across iterations, not the union — a file
  only some of them wrote is a broken run, not a series.
- **σ is the sample standard deviation** (n−1).
- **Iterations sort as numbers**, because `mc010` before `mc9` makes
  "iteration 7 is the outlier" point at the wrong file.

**Nothing is fitted.** No gaussian is assumed and none is drawn: 20 iterations
do not establish a distribution's shape, and a bell through the bars would be
this module asserting what the data cannot support. σ is a number in the
caption.

MC aggregation is **local-only**: it opens every iteration directory, and
doing that over ssh is one round trip per directory. Refusing is better than a
view that takes a minute and looks broken.

One side effect closed on the way: `mc.py` loads `wave.py` **by path** rather
than `sys.path.insert(_HERE); import wave`. The obvious spelling put the
engine directory on `sys.path` as a global effect of importing a library, so a
bare `import wave` anywhere in the process resolved to the engine copy — which
silently disarmed the browser's own test that the stdlib-WAV trap is real.

### 10.19 Listing order: sortable, and said out loud — LANDED 2026-08-02

Reported as one annoyance and it was two: *"the display seems to be in
last-modified order (I think, it is not clear) and this cannot be changed."*
Invisible **and** fixed.

**The default stays newest-first.** It is the right one and `listdir` already
argued for it: a stamp directory sorted alphabetically buries today's run
under three months of them. The complaint was never that it was wrong.

The pane now sorts by **name / modified / size**, clicking the active key
reverses it, and each key opens in the direction that is useful first —
newest, largest, but names A→Z, because descending names would be an odd
thing to hand anyone. Directories stay first whatever the key: they are a
different kind of thing to click, and interleaving them by size, where every
directory reads as null, turns a listing into a lucky dip. The choice persists
across navigation.

**And the order is named on screen** — "83 entries · by modified, newest
first" — which is the half of the complaint a sort control alone would not
have fixed.

Sorted in the CLIENT: the rows are already in hand for a local and a cluster
listing alike, so re-ordering is instant and costs no round trip.

**One thing that only became wrong once the order could change.** Badging is
capped — 300 local, 64 remote — and the cap applies in the order the *server*
produced. While the pane showed that same order the cap simply truncated the
bottom of the list. Re-sorted, a row past the cap has no badge because it was
never READ, which is a different statement from "this run has no verdict", and
the two are indistinguishable. So the listing now reports how many rows it
badged, and the pane says *"badges cover the 64 newest only"* — but only when
the order actually differs. Verified against the real cluster: `layout_cc` is
**83 entries, 64 badged**, the note appears under name/size order and vanishes
on newest-first.

### 10.20 What is NOT done

1. Phase 5, the rest: **VNC on asic7** (§6.6) is the only piece left. Two
   things are built but unproven against live data: a **running job** has not
   been watched through the panel (§10.12 — nothing was on asic6 to see), and
   the cross-probe does not yet **overlay op-point voltages on the layout**
   (§10.13).
3. The KLayout handoff and the XOR stay local-only by construction.
4. Per-PID licence attribution for the process scanner (`lmstat -a`).
5. `wave.py` is **forked into both repos** (ONR has its own
   `analog/engine/`), and the browser prefers the served repo's copy. It bit
   again in §10.16 — the nutascii adapter imported the stdlib WAV module
   because ONR's copy was a commit behind — and both places now check for a
   function they need rather than trusting the import. Loud, not fixed.
   Measured 2026-08-07: the drift has mostly closed on its own — `mc.py` and
   `reporthtml.py` are byte-identical, `wave.py` is 2 lines apart and
   `abstract_svg.py` 4. What is genuinely forked is the process-specific
   trio (`lyrdb` 169, `atlas` 219, `lyp` 117, `render_gds` 450 diff-lines),
   which is the layer tables and is *meant* to differ.
5a. ~~**ONR gates neither `lyp.py` nor `render_gds.py`**~~ — **DONE
   2026-08-07**, and it was not a formality. Porting the two gates found three
   real defects in the modules that had never been checked (§10.24).
5b. ~~**XT011 phases 0-2**~~ — **DONE** (§8a, §10.22 items 2-3): the results
   database turned out to be Calibre-shaped, so `lyrdb.py` ported, and the
   atlas needed two real changes rather than a copy. This item's own text
   said "phases 1-2 still open" through 2026-08-20 despite §10.22 already
   marking them done on the day they landed — corrected here rather than
   left contradicting itself. What XT011 still lacks: `report.html` and a
   flowrun stamp to badge — `lyp.py`'s own gap closed the same way (§10.22
   item 4).
7. **MC aggregation is local-only** (§10.18): it opens every iteration
   directory, and doing that over ssh is one round trip per directory. The
   cluster route refuses rather than crawling. Shipping `mc.py` + `wave.py`
   and aggregating cluster-side is the fix, and is not built.
8. **No yield number.** The distribution view has mean/sigma/min/max but no
   spec limits and so no pass count: where the limits come from (the spec
   JSON, or typed in the pane) is a design question, not a coding one.
8. A **nested (multi-dimensional) sweep** is still unhandled: the reader
   flattens and would silently produce one curve out of several. None of the
   files measured here is one — `pac1.pac` looked like a candidate and turned
   out to be an index — so this is a known gap with no example to test
   against, which is the honest reason it is not built.


### 10.21 Remaining Phase 0 visual gate

The ctrl2 GDS is at
`C:\dev\.lyrdb_dev\sdt_sar__ctrl2_316_78_7128_4.gds`. A real `DRC_RES.db`
is deliberately not copied there by default because it embeds foundry deck
text. Once that database and its generated `.lyrdb` are present, open them in
KLayout and confirm Marker Browser double-click zooms to the right geometry.
This is a Phase 0 GUI confirmation, not a Phase 1 code blocker.

### 10.22 Next actions — in order

Reordered 2026-08-07. **Items 1 and 2 are DONE** — both landed the same day
they reached the top of the list, and both returned more than they cost
(§10.24). What is left is the list as it stood before them, plus the phases
1-2 work XT011 now has a foundation for.

1. ~~Gate ONR's `lyp.py` and `render_gds.py`~~ — **DONE**, three defects
   (§10.24).
2. ~~Phase 0 for XT011~~ — **DONE**, the database was Calibre-shaped (§8a).
3. ~~Phases 1-2 for XT011~~ — **DONE 2026-08-07** (§8a). Three defects found
   in the reading of real tool output; the atlas port needed two real changes,
   not a copy.
4. ~~`lyp.py` for XT011~~ — **BUILT**, and the §10.24 warning fired exactly as
   predicted: `test_lyp.py` was 14/15 (measured 2026-08-20), because kit layer
   2 (DTI) is in `render_gds.LAYERS` (it gets drawn) but had no entry in
   `layers.PURPOSE` or `layers.UNMAPPED` at all.

   **DONE 2026-08-20.** Fixed from the kit deck's own `layer_map`/
   `layer_def` pairs (`xt011_DRC.rul` lines 98-603, read on `asic7`), not
   guessed — `test_nothing_is_invented_beyond_the_kit_table` exists for
   exactly that reason. The `assert` that catches this sits inside a `for`
   loop over `render_gds.LAYERS`, so it only ever reports the *first* failing
   layer — "14/15 on layer 2" hid the true shape of the gap. Of the 27 layers
   `render_gds.LAYERS` draws, only 11 (the ones the NVG stream exercises) had
   a `PURPOSE` entry; the other **16 — DTI, MV5, and the whole upper stack
   (VIA3-5, MET4-7, VIACM/METCM, VIATPL/METTPL, PAD, VIACT/METCT)** — were
   equally unstyled, each one hidden behind the last until it was fixed. All
   16 resolved from the deck in one pass (83 new `PURPOSE` rows, matching the
   existing met1/met2/met3 shape — drawing purpose repeated at datatype 0 and
   9, `_bnd` at 10, `_hole`/`_holexcl` at 65/66 except METTPL, which the deck
   genuinely has neither for). `test_lyp.py` is now 15/15.
5. VNC on asic7 (§6.6) — heaviest, and it buys capability
   (`vreRunGeneration` needs a real window) rather than just eyeballs.
6. Per-PID licence attribution (`lmstat -a`) for the process scanner.
7. Decide `VIA1.W.1`; extend marker emission to localised LVS shorts.
8. A measured **X-FAB rule table**, if anything ever needs XT011 violations
   classified rather than merely grouped. Deliberately last: nothing does yet,
   and inventing one is worse than not having one.

### 10.23 Open, outside this plan

- **17 stale tool sessions** are still holding licences on the cluster —
  Virtuoso at 62.8 d (asic8), 39.0 d (asicdesign), 31.7 d (asic2, 8.5 GB).
  `python deployment/bnl/jobs/procscan.py scan` lists them; nothing is killed
  without a second click.
- The **Phase 0 GUI gate** is still unconfirmed: open a `.lyrdb` in KLayout
  and check Marker Browser double-click zooms to the right geometry.
- `vref` reports one **overlapping lane** — `vdd` rail × `vrefn` track,
  129.33 µm at y=75.46. Reported as a fact, not a defect; it wants an
  engineer's eye.


### 10.24 What porting the ONR gates found — 2026-08-07

Recorded because it is the argument for the item existing at all. `lyp.py` and
`render_gds.py` were the only modules in the visualisation set with no gate,
and they hold the most node-specific content in that repo. Porting the two test
files found three real defects, none of which anything else would have caught.

**1. The layer map hid the top of the stack.** `_datatypes_for` emitted the
drawing purpose at datatype 0 unconditionally. At 28 nm that is right only for
the 1X metals: the thick tiers step by 20 (M7/20, M8/40, M9/60, VIA6/7/8 the
same — `layers.DRAWING_DATATYPE`, whose own docstring says anything writing a
GDS number must pair it with this). Six entries covered nothing.

It got a second thing wrong at the same time, and that one is worse than an
omission: **39/0 is not unstyled M9, it is `NOUSEM9` — a keep-out.** The map
was labelling a keep-out as metal.

Measured rather than argued: `spiral_draw.COIL_LAYER` is M9 and `UNDER_LAYER`
is M8, so the six broken entries were exactly the ones over the inductor. Open
a spiral in KLayout with that map and the winding is the part you cannot see.

This is the same defect shape the 65 nm gate was written for — there it was the
NET purpose, `132/0` matching 2 shapes while `132/83` held 2958 — one node over
and on the other purpose. **The generalisation worth keeping: at every new
node, read the datatypes for BOTH purposes out of the layertable, and treat "0"
as a value to check rather than a default.**

**2. The crop was still a view, not a speedup.** `main()` applied the window
with `set_xlim` after drawing everything. `draw_polys` already had the `clip=`
parameter — only the caller was missing, which is why nothing looked broken.
Measured on 60 000 synthetic polys over 300 µm with a 20 µm window: **15.36 s →
0.37 s, 60 000 patches → 317, 41.6×, and the PNG is byte-identical.** The
byte-identity is the whole point of `CLIP_PAD`: a fast render that is not the
same render is not a faster render.

**3. The cropped legend described the file, not the picture** — `other` was
keyed on what the poly list contained, so a crop with no annotation geometry in
it still got an `other` swatch for layers nowhere on screen.

Two of the three were *ports that had silently not happened*: the code looked
finished because the capability was present one call away. That is the specific
risk of a fork kept in step by hand, and it is why §10.20.5's "loud, not fixed"
was the wrong resting place for as long as it lasted.

- Not a signoff viewer (KLayout is) and not a layout editor.
- Not a replacement for `aj` — the browser reads job state, it does not
  schedule.
- **Not an agent control surface.** The activity view (§7) observes; it never
  starts, stops, answers or steers an agent. The terminal keeps that role.
- Not a transcript reader. §7 shows metadata and paths, never message bodies
  (§7.2).
- Not a web service. Localhost, single user, no auth, no writes.
- Not a new transport. If it needs an ssh feature `jobs/remote.py` lacks, that
  feature is added *there*.

## 11. RESUME HERE — state as of 2026-10-15 (moved to the flowkit)

**Why it moved.** Since 2026-09-12 the browser has been one package, vendored
from spec2si-flowkit into every port ([ADR-0004](decisions/0004-browse-and-transport-vendored.md)),
so its plan belongs where its code is. Sections 1-10 are carried over
unchanged as the history and the reasoning; spec2si-tsmc65 keeps a pointer at
the old path, and everything before 2026-10-15 is in that repo's git log.
How to USE any of this is still the onboarding page,
[browse-your-results](howto/shared/browse-your-results.md).

### 11.1 Where everything is

| Piece | File | What it is |
|---|---|---|
| a repo's browser | `browse/server.py` (+ `model`, `roots`, `tools`, `cluster`, `estimate`, `agentview`, `runlog`) | one server per repo, confined to that repo's declared trees |
| the hub | `browse/hub.py` | port 8729: every onboarded repo on one page; serves no files |
| spectra | `browse/spectrum.py` | stdlib SVG of a `photonic-filter-interchange` response |
| a port's declarations | `browse/roots.json` in each port, never vendored | trees and their `role`, renderer (incl. `ship`/`python`), `badges`, `libraries` |
| the photonic renderer | spec2si-aim `tech/render/render_pic.py` | shipped to the cluster with each render |

### 11.2 What landed since section 10 (2026-08-20 → 2026-10-15)

* **Vendored everywhere** (ADR-0004, 2026-09-12): byte-identical in all five
  ports, hash-gated by `sync.py --check`; a fix lands once.
* **Which repo am I looking at.** The header and the tab title name the
  served repo (the tab leads with its short tag, `aim · docs`), and
  `/api/roots` carries it. With five browsers open the tab strip used to read
  "flowruns", "flowruns", "docs".
* **The hub** (`python browse/hub.py`): what each repo is, its branch and
  ahead/behind as of its last fetch, whether its browser is up and on which
  build, Open/Start (Start runs the repo's own `browse/server.py`), and the
  Jobs/Processes/Agent strip once for the machine. Every browsing route 404s
  there; a repo's browser is still the only thing that reads a repo.
* **One cluster read per machine.** The job list is shared through a cache
  file with an in-flight lock (`tools.shared_cached`): a browser asking during
  another's read waits for it, and a read stays fresh for twice what it cost.
  Measured: one list took 52 s for 1227 records, so a fixed 20 s shared
  nothing; a second browser then answered in 0.0 s from the first one's read.
  The process scan is deliberately NOT shared -- it is an explicit action and
  it feeds the kill.
* **Trees marked by role.** A root may declare `role` (`oa`, `store`,
  `design`, `work`; inferred from the directory name otherwise), and a port
  may declare `libraries.scratch` / `libraries.legacy`. The same library name
  now reads *OA · design library*, *store · N cells* or *design record ·
  N cells* depending on the tree, and an OA tree tells design, scratch,
  legacy shared, backup and undeclared libraries apart. Measured across the
  four electronic ports on the day: tsmc65's eight pre-split libraries and
  tsmc28's three were unbadged before.
  ⚠ *not in this checkout's record* on a store directory can mean a stale
  checkout rather than an orphan: tsmc28's `pll_wref` read that way until a
  26-commit-behind checkout was pulled. The hub's behind count is the first
  thing to look at.
* **Markdown renders** through the docs system's own `docs/mdrender.py`, in a
  sandboxed frame served with a no-script CSP; links between docs keep
  rendering.
* **Deep links**: `#r=<root>&p=<dir>&v=<file>` -- a reload lands where you
  were, a pasted link opens there, and the hub and the agent panel use it to
  open another repo's browser at a file.
* **The agent panel sees the parent directory.** A session is filed under
  the directory it started in, so one started in `C:/dev` that edits a port
  all day was invisible to that port's panel; `agentview.sessions_touching`
  lists the parent's sessions whose transcript tail names the repo.
* **Photonics (spec2si-aim).** Three pieces, none of them carrying anything
  from the kit:
  * a SHIPPED renderer: `roots.json` `renderer` gained `ship` (send the
    declared renderer's bytes to the cluster with each render) and `python`
    (the cluster interpreter to run it with). The port's layouts live only in
    its cluster content store (DISTRIBUTION STATEMENT F), outside any cluster
    checkout, so finding a renderer "above the layout" could never work; the
    picture is now drawn by the bytes in the checkout you browse from, and the
    renderer's hash is part of the render cache key;
  * `render_pic.py`: flattens curves through the hierarchy (a ring keeps its
    hole), draws layers by their number with a colour hashed from it, draws a
    layer covering more than a quarter of the die as an outline, hides
    metadata text (`=`-carrying or long labels; the first real stream had
    29 against a handful of port names) and merges labels at one place.
    Names come only from a private table on the machine that runs it;
  * a SPECTRUM view for any JSON carrying `frequency_hz` and `transfer`:
    |H|^2 in dB against wavelength and the phase as recorded, with the
    evaluations and verdicts exactly as scored. Group delay is NOT derived --
    the phase sign is a convention the file states, and the plot quotes it.
  * ⚠ found on the way: KLayout writes `$$$CONTEXT_INFO$$$` into every stream
    it saves, an unreferenced structure that REFERENCES the cells it
    describes -- so a top-cell search saw two tops in one stream and none in
    another. Both the local and the cluster search now skip it on both
    counts.

### 11.3 Still open, in order

1. **aim's private layer-name table** (`browse_layer_names.json` on the
   cluster, outside git): the renderer reads it if present; nobody has
   written it, so legends show numbers.
2. **A shipped renderer for the electronic ports too.** They still rely on a
   cluster checkout above the layout and on miniforge; `ship` would make a
   remote render follow the local code there as well.
3. **Group delay / phase-minus-mean-delay** on the spectrum view -- only once
   the contract's sign convention is applied in one shared place rather than
   re-derived in the viewer.
4. The hub has no **Stop**; a repo browser it started is stopped from that
   repo's shell. Deliberate for now (a kill is the one irreversible action,
   and the hub has none).
5. Carried from 10.20, unchanged: VNC on asic7, the op-point overlay on the
   layout, cluster-side MC aggregation, a yield number, nested sweeps,
   per-PID licence attribution.
