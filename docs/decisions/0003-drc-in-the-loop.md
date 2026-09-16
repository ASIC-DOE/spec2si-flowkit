<!--docmeta
title: ADR-0003 — the physical-verification repair loop is vendored; the rule names stay home
genre: decision
status: accepted
area: top
owner: soumyajit
updated: 2026-08-29
summary: The "shared rule set, not shared engine" contract gains a second clause under the same evidence standard ADR-0002 set. Two ports independently built a DRC-report-driven repair loop — spec2si-tsmc65's R3 auto-repair against Calibre 2024.1, spec2si-xt011's drc_patch.py against PVS 23.1 / Pegasus — and their results-database readers came out the same algorithm to within a comment, after xt011 wrote down "does Pegasus emit a Calibre-shaped database" as a question to ANSWER and ran the 65 nm reader against five real Pegasus files. That is measured portability, not assumed. What vendors as drcloop/: the ASCII results-database parse, marker geometry and the area patch, geometric attribution and the baseline delta, and the loop protocol (a control, a stream digest, a ledger). What never vendors: rule NAMES and their semantics, the layer map, the flattener, the deck driver, the drawer. Three core policy rules are added with it — marker-is-the-position, baseline-delta, render-before-acting — and a port declaring them not-implemented still passes.
-->

# ADR-0003 — the physical-verification repair loop is vendored; the rule names stay home

**Status:** accepted · 2026-08-29 · extends the sharing contract of
[ADR-0002](0002-routekit-vendored-core.md) to a second family; supersedes
nothing.

## Context

Signoff DRC reaches a person as a count. Turning that count into a change
requires three things no deck provides — an **attribution** (whose result is
it), a **diff** (did this change add one), and a **response** (what geometry
closes it) — and every port has to build them.

Two did, independently:

| port | deck | what it built |
|---|---|---|
| `spec2si-tsmc65` | Calibre 2024.1 | R3 report-driven triage + bounded auto-repair: parse `DRC_RES.db`, classify against a measured `RULE_TABLE`, repair the same-net spacing class, restream and re-DRC **once** |
| `spec2si-xt011` | PVS 23.1 / Pegasus | `drc_patch.py`: parse the ASCII results database, close every minimum-area marker against the flattened stream, refuse by name, re-derive on every re-stream |

## The fact

**The two readers are the same algorithm to within a comment.** That is not a
coincidence anybody arranged. The xt011 port wrote down *"whether Pegasus
emits a Calibre-shaped database"* as the first question to **answer rather
than assume**, ran the 65 nm parser unmodified against five real Pegasus
databases under `~/Documents/anc_xt011/analog/work/` on `asic7`, and read
every one of them — 11, 12, 11, 12, 2183 and 191 records. Header line
`<top cell> <units-per-um>`; per rule a bare name, a counts line, the braced
deck text it sizes, then `p`/`e` records with integer database units. That is
the Calibre ASCII layout exactly.

So the format half of the port was free, and the two implementations then
converged on the same code without either author reading the other.

This is the same evidence standard ADR-0002 used for the router: *the fork
was measured, not assumed*, and it came back saying the boundary is somewhere
other than where the copies fell.

## The decision

Vendor the node-agnostic half as `drcloop/`, on the seam
[`em_ir_alignment.md`](../em_ir_alignment.md) §6 set and ADR-0002 reaffirmed:
**it touches no PDK API, no deck, no PCell, no vendor tool — only numbers a
caller's `rules` object hands it, and a fact it was not given is a refusal.**

**What vendors**

| module | contract |
|---|---|
| `resultsdb` | the ASCII database. Keeps record KIND (a spacing violation is an edge pair, and a box round both conductors points at the metal when the error is the gap), skips the deck text (NDA), and refuses a database whose top cell is not the one the caller is patching |
| `markers` | shoelace area, bar axis, the edge span AT an edge, the patch, and the clearance test — measured against conductors the caller supplies **from the stream the deck read** |
| `triage` | a `RuleTable` the port fills, geometric attribution smallest-area-first, and the baseline `Delta` |
| `loop` | the protocol: `Ledger` refuses to answer `clean` without a control; `Reply` binds a patch set to the sha256 of the stream it answered; `check_isolation` verifies that a shrunk artefact kept its frame |

**What never vendors**

Rule NAMES and their semantics (`A1M2` here, `M2.A.1` there, `met2.area`
somewhere else) — a rule table is measured judgement about one deck. The
layer map, the GDS flattener, the deck driver, the drawer, the cluster
mechanics. A shared file claiming otherwise would be a lie with a hash on it,
which is ADR-0002's phrasing and still the test.

**And three core policy rules land with it** — `marker-is-the-position`,
`baseline-delta`, `render-before-acting` (core v1.3.0). Each is stated as a
portable principle; the enforcement point stays per port, and
`not-implemented` remains a passing state, so the adoption gap is a printed
number rather than an absence. At acceptance: xt011 2 enforced + 1 partial,
tsmc65 1 partial, tsmc28 and sky130 not-implemented.

## Consequences

**Positive.** One parser instead of four, gated by hash. The two hardest
lessons — *the marker is the position* and *the clearance is measured against
the stream* — arrive in a port before it has paid for them. The refusal
discipline (a marker that cannot be closed is named with the clearance that
beat it) is in the shared code, so it cannot be quietly dropped by a port in
a hurry.

**Negative, and named.** A second vendored package is a second thing that can
drift, and `sync.py --check-all` grows by 9 files. The 65 nm port's existing
`drc_triage.RULE_TABLE` and `lyrdb.py` are **not** rewritten onto `drcloop`
by this ADR — they keep working, and migration is a separate change with its
own gate. Until it happens the 65 nm port has two readers of one format,
which is exactly the condition this ADR is against; it is accepted as
temporary and recorded here so it is not forgotten.

**Open.** `markers` ships the **area** responder only. Width and same-net
spacing markers want different geometry (metal added *across*, a gap
bridged), both are described in the xt011 findings, and neither is
implemented. They are named rather than half-done, which is the same
discipline as the refusals.

## Evidence

`ancBrain_gap`, a two-block cell, 2026-08-28:

```
baseline      11    density only -- the SAME cellview, no route drawn
routed      1063    +1052, and all 1052 are ONE via pad
re-routed    147
re-snapped    21
patched       11    116 of 116 minimum-area markers closed, none refused,
                    and not one new spacing or width result created
```

328 of 328 nets routed, 0 gate findings. LVS reports **no `SHORTS AND OPENS`
section at all** — no short and no open on any net, where the 323-routed run
before it held four, every one an OPEN on a gap bit the router had never been
given. The run's overall verdict is `MISMATCH`, from a harness pin and from
4536 ambiguous instances *inside* the two blocks exceeding the engine's
threshold; neither is reachable by anything the router draws, and saying which
is the point. The same change measured on the die was 88 → 1098, unreadable.

The 28 nm port's local half and its first use: `spec2si-tsmc28` `docs/howto/drc_in_the_loop.md`.
The API and the seam: [`drc_loop.md`](../drc_loop.md).
