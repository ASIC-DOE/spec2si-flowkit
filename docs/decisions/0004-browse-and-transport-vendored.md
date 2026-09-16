<!--docmeta
title: ADR-0004 — the artifact browser and the cluster transport are vendored; what a port declares stays home
genre: decision
status: accepted
area: top
owner: soumyajit
updated: 2026-09-12
summary: Until 2026-09-12 the artifact browser lived in one port and the other three carried a launcher stub that reached across the disk into it, and the cluster transport was reached the same way two hops out. The browser's own code recorded the coupling and named the fix ("one shared package rather than two of everything"); a survey a month later found the fourth port could not even reach it by stub. Both packages vendor under the same evidence standard as ADR-0002 and ADR-0003: the browser is stdlib-only by construction and PDK-blind, the transport is a site fact every port shares, and what differs per port is one JSON declaration the package never copies. What stays home: `roots.json` (trees, renderer, badge sources), the engine-specific readers the browser locates in the served repo, and the job-status plan documents that belong to the port that wrote them.
-->

# ADR-0004 — the artifact browser and the cluster transport are vendored

## Context

`browse/` was written in spec2si-tsmc65 (2026-07/08) as a read-only viewer
over a repo's results: listings badged from what the flow wrote, GDS renders,
waveforms, the schematic/layout cross-probe, the cluster's trees through
`deployment/bnl/jobs/remote.py`. It was made servable across repositories by
a launcher (`BROWSE_REPO`) rather than by copying — spec2si-tsmc28 and
spec2si-xt011 carried a stub that located the implementation in the sibling
checkout, and `cluster.py` located the transport the same way, in the served
repo, then in the package's, then in `spec2si-tsmc65` or `ms_pilot` next
door. The code said of itself: *"This cross-repo reach is a real coupling …
the honest fix is one shared package rather than two of everything."*

The survey of 2026-09-12 (spec2si-tsmc65, `docs/browse_survey_2026-09-12.md`)
found the cost: the fourth port, sky130, had no stub, no transport and no
`roots.json`, and its renderer sat at a path with a different argument
order that no list of places-to-guess could find. Items 1–4 of that survey
made the browser configuration-driven (an object-form `roots.json` that
DECLARES a renderer and badge sources; the design record read everywhere).
This decision is item 6.

## Decision

`browse/` and `deployment/bnl/jobs/` vendor from the flowkit into every
port, hash-gated by `sync.py` like everything else here.

**Why the browser qualifies.** It is stdlib-only by its own principle and
imports no engine at module level; the two engine-specific things it draws
— the transient reader (`analog/engine/wave.py`) and the abstract's track
map (`abstract_svg`) — it LOCATES in the served repo by path and degrades
honestly without. Nothing in it knows a PDK, a deck or a device generator.
What differs per port is `browse/roots.json`: which trees to list, which
renderer to run and how, which files are badge sources. That file is a
declaration, never vendored, and adding it is the whole of onboarding.

**Why the transport qualifies.** `remote.py` is an ssh round trip that
cannot be corrupted by quoting; `hosts.py` chooses among the cluster's
hosts; `procscan.py` finds licence-holding processes. All four ports share
the one BNL cluster, so these are facts about a site, not a node.

**What stays home.** `roots.json`; the engine readers; the port's
`runlog_cells.txt` roster and path rules; the transport's README and the
job-status plan it implements, which name spec2si-tsmc65's own documents and
would read as dead links elsewhere.

**What the cross-repo reach becomes.** `cluster._transport_dirs` no longer
looks in a sibling checkout: the transport is beside the package in every
port, and a port that cannot find it has a vendoring gap the error names
rather than a neighbour to borrow from. `browse/launch.py` survives as the
convenience it was written for — serve a sibling by name, `--list` — and is
no longer how a port gets served at all.

## Consequences

* A port is onboarded by writing `roots.json` and re-vendoring. sky130 was
  the proof, the same day.
* `sync.py --check-all` grows from 168 to 332 checks. `--to` still
  overwrites unconditionally, and the day this landed every port carried
  in-flight routekit drift, so the vendoring was a filtered copy of the
  new groups rather than `--to`; the vendoring how-to's warning stands.
* The waveform view on a port without an engine reader (xt011, sky130)
  says so instead of borrowing tsmc65's — as it did when served from tsmc65.
  A shared, node-agnostic transient reader is a candidate for a later
  ADR; today the two engine ports' copies differ by 2,749 lines and have
  not been measured for portability.
* The runlog gained two roster sources in the same change: the design
  record (`design/<lib>/<cell>/cell.json`) and declared path rules, because
  the survey's "the xt011 harvest stopped" turned out to be a harvest that
  fired at every session end and matched 15 of 618 turns.
