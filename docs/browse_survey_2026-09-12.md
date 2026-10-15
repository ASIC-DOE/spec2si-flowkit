<!--docmeta
title: The shared visualization layer, surveyed after a month — what is stale, what it cannot see, what to do next
genre: finding
status: active
area: top
owner: soumyajit
updated: 2026-09-12
summary: `browse/` (11.2 k lines, one implementation here, launcher stubs in tsmc28 and xt011) last changed on 2026-08-20 and its 187 tests still pass — the code is not broken, it is behind the repos it serves. MEASURED, not inferred, on every point. (1) spec2si-sky130 is not onboarded at all: no `browse/`, no `roots.json`, no launcher stub, no how-to, no runlog harvest — `launch.py --list` files it among the unrelated projects. Its renderer sits at a third path the browser does not look at, and its cells have the view-per-cell shape the runlog can only handle through a `runlog_cells.txt` that does not exist. (2) The browser has NO NOTION OF designdb OR OA LIBRARIES, which did not exist when it was built: no root for `design/`, no viewer for a `cell.json`, no way to ask "is this stream the bound view", no listing of an OA library or the content store, `.oa` is "binary" and `.route`/`.escape`/`.pins`/`.census`/`.spi`/`.net` are "unknown". (3) Its badges read `report.json`/`manifest.json`/`status.json`/`LATEST.txt` — tsmc65 flowrun conventions that xt011 and sky130 never write, so both ports browse unbadged. (4) xt011's runlog harvest stopped on 2026-08-23 with the hook installed. (5) The core is served cross-repo from this checkout — the plan's own "honest fix is one shared package" is still open, and sky130 is the first repo that cannot even reach it by stub. Recommendations ranked: onboard sky130 by config not code (per-repo `roots.json` grows `renderer`/`badges` keys), give the browser a design-record view (a `cell.json` viewer, a `design/` root everywhere, an md5 lookup that answers "which captured view is this file", an OA/store lister on the cluster), generalise badges, add the missing kinds, fix the xt011 harvest, then move the core to the flowkit.
-->

⚠ **Moved here from spec2si-tsmc65 on 2026-10-15** with the visual-interaction
plan; unchanged otherwise. Its items 1-4 and 6 landed (the plan's sections 10-11).

# The shared visualization layer, surveyed 2026-09-12

> ✅ **Items 1–4 of §8 were implemented the same day** (`browse/` in this
> repo, plus the four repos' `roots.json`). What landed: sky130 is onboarded
> by configuration — an object-form `roots.json` that DECLARES its renderer
> (`analog/lib/sky130_ota6/render/render_gds.py <gds> <top> <out>`, no crop)
> and its badge sources, a launcher stub, `analog/specs/runlog_cells.txt`,
> the SessionEnd hook, a how-to, and the runlog pair vendored from the
> flowkit; a `design` root in all four repos, a `cell.json` viewer, an md5
> lookup that says which captured view any local file is (or that it is in
> no view, or that it is a view's origin whose bytes drifted), the manifest
> check as a badge on `design/`, and library/scratch badges on the cluster's
> `analog/oa/`; badge sources generalised (`result.json`/`pex.json`/
> `schematic.json` by `verdict`, `cell.json` by its bindings, per-repo
> globs via `roots.json`) with a visible *no badge source here* state; and
> the missing kinds. Items 5 (the xt011 harvest) and 6 (the core into the
> flowkit) are still open. Details in
> [howto/shared/browse-your-results](howto/shared/browse-your-results.md).
>
> ✅ **Items 5 and 6 followed the same day.** Item 5: the harvest had never
> stopped -- it fired at every session end and matched 15 of 618 turns,
> because the die work lives in flow-level files that name no cell; the
> runlog now takes the design record as a roster and declared path rules
> (`analog/specs/runlog_cells.txt`), and xt011's log went from 29 to 137
> records. The transcripts between 2026-08-23 and 09-03 are not on this
> machine at all, so that gap is permanent here. Item 6: `browse/` and
> `deployment/bnl/jobs/` are vendored from spec2si-flowkit into every port
> (flowkit ADR-0004); no repo reaches into another any more, and the
> how-to is a shared page.

> **In one line.** The browser works and is a month behind: it does not know
> the newest port (sky130) exists, and it does not know the newest thing all
> four ports now have (the design record, `designdb`, and the OA library
> named for it).

Everything below was measured on this machine on 2026-09-12: the four
checkouts under `C:\dev`, `launch.py --list`, the test suites, `git log`,
and the roots, kinds and badge tables read out of the code.

## 1. What is there, and how old it is

| module | lines | what | last substantive change |
|---|---:|---|---|
| `browse/server.py` | 3329 | the three-pane browser: `/api/{roots,list,file,render,xor,klayout,jobs,procs,mc,agent,health}` | 2026-08-20 |
| `browse/model.py` | 644 | listing, `KINDS`, badges (`BADGE_SOURCES`, `LATEST.txt`) | 2026-08-20 |
| `browse/roots.py` | 351 | the security boundary: confinement by resolved path | 2026-08-20 |
| `browse/tools.py` | 380 | the whole exec surface: GDS render, KLayout handoff, XOR | 2026-08-20 |
| `browse/cluster.py` | 583 | list / fetch / render on the cluster through `deployment/bnl/jobs/remote.py` | 2026-08-20 |
| `browse/estimate.py` | 335 | what a click will cost, measured per installation | 2026-08-20 |
| `browse/launch.py` | 328 | serve any sibling checkout by name; `--list` | 2026-08-20 |
| `browse/runlog.py`, `agentview.py` | 1528 | the agent-loop occurrence log and the activity view — vendored, identical in flowkit (`runlog/`), tsmc28 and xt011 | 2026-08-26 |
| tests | 3549 | `test_browse` 133/133, `test_runlog` 25/25, `test_agentview` 29/29 | pass today |

The 2026-09-11 commit touching `browse/` only resolved repository names.
So: **nothing is broken, and nothing has learned anything since 2026-08-20.**
Two things happened in the repos since then that the browser cannot see.

## 2. ⛔ sky130 is not onboarded

```
$ python browse/launch.py --list
servable checkouts (implementation: C:\dev\spec2si-tsmc65\browse)
   spec2si-tsmc65     Spec-to-Silicon on TSMC 65 nm LP
   spec2si-tsmc28     Spec-to-Silicon on TSMC 28 nm HPC+/ULL
   spec2si-xt011      Spec-to-Silicon on X-FAB XT011 PDSOI
   -- no browse/roots.json (servable, default roots only):
      Agent_Compiled_AMS_Paper, FDFD_PE, …, spec2si-flowkit, spec2si-sky130
```

The launcher's weak "looks like a repo" test is deliberate — an un-onboarded
checkout is browsable, just plainer — and it is exactly how sky130 has been
sitting since 2026-08-24 beside a photonics project. Measured:

* `spec2si-sky130/browse/` holds `__pycache__` and `.pytest_cache` and
  nothing tracked: no `roots.json`, no launcher stub of the kind tsmc28 and
  xt011 carry, no `browse-your-results.md` how-to.
* Its renderer is `analog/lib/sky130_ota6/render/render_gds.py`, and it is
  the right one — it keeps layer/datatype pairs, which SKY130 needs because
  poly and `licon1` share layer 66. `browse/tools.py` looks for a renderer
  at exactly two paths (`analog/engine/layout/render_gds.py`,
  `analog/layout/render_gds.py`), so a sky130 layout would answer
  *renderer not found* and the KLayout handoff likewise.
* Its cells have the VIEW-PER-CELL shape (`analog/lib/sky130_ota6/{layout,
  schematic,simulation,render}`), which `runlog.py` already knows about —
  its comment records the first harvest attributing 26 attempts to cells
  named "layout" and "schematic" — and handles only through a declared
  `analog/specs/runlog_cells.txt`. sky130 has neither that file nor a
  `runlog.jsonl` nor a `.claude/settings.json` with the SessionEnd hook.
  Its sessions since 2026-08-24 are not in the occurrence log at all.
* Its results are not where the badges look (§4).

## 3. ⛔ No notion of the design record, or of OA libraries

On 2026-09-12 all four ports adopted the library / cell / view model:
`design/<library>/<cell>/cell.json` in git, the bytes in a content store on
the cluster, and the OA library a cell publishes into named for its design
library (this repo: ADR 0003; xt011 and sky130: their ADR 0001; tsmc28:
`design_library_adoption`). The browser predates all of it. Measured:

| question a user has | what the browser does today |
|---|---|
| what does `adc_v6/dac_core` hold, which view is bound, what md5 | `cell.json` is kind `json`: a pretty-printed dict, no view table, no bound marker, no BOM links |
| is THIS `.gds` under `analog/work/` the one the library binds | nothing — no md5 lookup against the manifests; the flow's commonest question since the migration |
| what is in OA library `adc_v6` / `ancasic_p2` / `sky130_ota6` on the cluster | nothing — no root for `analog/oa/`, `.oa` is "binary", no census (`LC_INST`) reader |
| what is in the content store | nothing — no root for `design_store/`, none of the four `roots.json` names it |
| is the manifest tree consistent | nothing — `designdb --check --fast` is one call and not surfaced |

`grep -n "designdb\|design_store\|OpenAccess\|cds.lib" browse/*.py` returns
one line: the `.oa` suffix in `KINDS`.

## 4. ⚠ Badges are tsmc65 flowrun conventions

`model.BADGE_SOURCES = ("report.json", "manifest.json", "status.json")`,
followed through `LATEST.txt`. Those files are written by this repo's
`run.py`. xt011 writes `.out` record files, `.route`/`.escape` plans,
`.pins` dumps and `cell.json`; sky130 writes `result.json`, `pex.json`,
`schematic.json` and an evidence JSON. Neither port has ever produced a
badge, and nothing says so — an unbadged row reads as "nothing to report".

## 5. ⚠ Kinds the flows now produce that the browser calls "unknown"

`.route`, `.escape`, `.pins`, `.census`, `.spi`, `.net`, `.va`, `.pvl`,
`.rul`, `.sdc`, `.strm` (the last three read as text anyway by luck of the
`.log` suffix on their logs). All are plain text and would view as text with
one table entry each; today they fall through to `unknown`.

## 6. ⚠ The xt011 harvest stopped on 2026-08-23

| repo | records | last record |
|---|---:|---|
| tsmc65 | 304 | 2026-09-12 |
| tsmc28 | 390 | 2026-09-11 |
| xt011 | 29 | **2026-08-23** |
| sky130 | — | never |

xt011's `.claude/settings.json` carries the same SessionEnd hook as the other
two, so the hook is installed and something after it stops producing
records. Twenty days of the busiest sessions in the family (the die's LVS,
the channel routing, the designdb migration) are missing from the log that
exists to measure exactly that kind of work. Not diagnosed here; it is a
one-session finding and it belongs to whoever picks the runlog up.

## 7. The coupling the plan already named

`browse/cluster.py` locates the hardened transport in
`deployment/bnl/jobs/remote.py` of THIS repo or of a `ms_pilot` sibling;
the stubs in tsmc28 and xt011 locate the implementation in THIS repo's
`browse/`. The code says of itself: *"This cross-repo reach is a real
coupling … the honest fix is one shared package rather than two of
everything."* sky130 is the first repo where the coupling costs a whole
port: it has neither a stub nor the transport, and the flowkit — which now
carries `runlog/`, `routekit/`, `irdrop/`, `drcloop/` — does not carry the
browser.

## 8. What to do, in the order it pays

1. **Onboard sky130 by configuration, not code.** A `browse/roots.json`
   naming `analog/lib/sky130_ota6`, `analog/results`, `digital/i2c_slave`,
   `design`, `tech` and the remote `~/Documents/sky130`; the launcher stub
   tsmc28 and xt011 carry; `analog/specs/runlog_cells.txt`; the SessionEnd
   hook; a two-line how-to. And let `roots.json` grow two optional keys —
   `renderer` and `klayout` — so `tools._RENDERER_AT` stops being a list of
   places to guess and becomes a per-repo declaration, the same move
   `oa_dest` made for OA libraries.
2. **Give the browser a design-record view.** A `design/` root in every
   onboarded repo. A `cell.json` viewer: library/cell, the views table
   (name, type, files, md5, origin, provenance), the bound view marked, the
   BOM as links to sibling cells, `bom_external` as text. An md5 lookup over
   the manifests, so any file in any root can answer *"this is
   `ancasic_p2/ancBrain_Buffer` view `stream`, bound"* or *"in no view"* —
   which is the question every signoff in xt011 turned into a shell loop.
   On the cluster, a lister for `analog/oa/<lib>/<cell>/<view>` that says
   which library is scratch and which is published, and reads the census
   (`LC_VIEW`/`LC_INST`) the publish already writes. `designdb --check
   --fast` as a badge on the `design/` root.
3. **Generalise the badges.** Per-repo badge sources in `roots.json`
   (`result.json`, `pex.json`, `*.lvs.report` verdict lines, `cell.json`
   bindings), and a visible "no badge source here" state so silence stops
   reading as clean.
4. **Add the missing kinds** (§5). One line each.
5. **Fix the xt011 harvest** (§6) and declare sky130's cells.
6. **Move the core into the flowkit** as a vendored package, with the
   transport. Then a port is onboarded by adding `roots.json` and re-vendoring,
   and no repo reaches into another to draw a picture.

Items 1–4 are a day; item 6 is the structural one the plan has been
carrying since July. None of them is blocked on anything but choosing.
