<!--docmeta
title: EM sizing and IR drop — aligning three cards, then computing a voltage
genre: decision
status: active
area: top
owner: soumyajit
updated: 2026-08-06
summary: Plan for a fast IR-drop calculator fed by the simulated operating point, and the prerequisite step it exposed — the EM and resistance features of the three flows are not aligned, and both are keyed to a metal option that only one repo records.
-->
# EM sizing and IR drop

*2026-08-06. The objective is **quick feedback during interconnect sizing**,
not a Voltus replacement. Voltus stays the signoff authority and the
correlation target; this is the number you want before you have a routed
block to give it.*

## 1. The gap, in the repos' own words

`spec2si-tsmc28/chip/floorplan/pll_rails.json`, `_still_open`:

> IR drop is NOT modelled anywhere in this repo — `em_power.py` sizes for
> current density only. A 1.1 mA rail that meets EM can still drop
> millivolts a VCO's phase noise cares about, and VDDL is the supply whose
> noise becomes phase noise directly.

`spec2si-tsmc28/tech/probes/extract_metal_res.py` opens with *"Nothing in
this repo could compute an IR drop."* That probe fixed half of it — the
metal sheet resistance was in the tech LEF the whole time. The other half
is a solver and the currents to drive it.

**EM and IR are the same knob and different criteria, and they do not bind
in the same place.** EM binds on the highest-current segment, near the pad.
IR binds on the longest cumulative path, at the far tap. An EM-clean grid
can have a far-end drop that EM sizing is structurally incapable of seeing.
That asymmetry is the whole argument for the tool.

## 2. What the calculator needs, and what already exists

Three inputs. Only one of them is new code.

| input | where it comes from | state |
|---|---|---|
| currents | `op1.dc` PSF (`Vdd:p`, per instance), or a transient mean | readers exist in every needed form |
| resistance | the vendor RC file | **extracted on all three nodes.** AIML + ONR from their QRC `.ict`; XT011 from its tech LEF |
| topology | the rail plan already on disk | exists, already carries width and layer |

**Currents.** `spec2si-tsmc65/analog/engine/wave.py` (`read_psf`, and it already
handles this flow's misnamed `op1.dc`), `spectre_flow.parse_value`/`afile`,
and `spec2si-tsmc28/tech/probes/parse_oppoint.py` (SI-prefix aware — a naive
`float()` on a Spectre oppoint mis-scales by the prefix). Two call sites
already pull supply current off the operating point:
`spec2si-tsmc65/analog/engine/layout/pex_ota.py:58` and `pex_vref.py:41`.
On the transient side `spec2si-tsmc28/analog/engine/char/libgen.py`
`measure_supply_current` already returns **`(mean_A, peak_A)`** — the peak
is measured today and nothing consumes it.

**Topology.** `spec2si-tsmc65/analog/engine/layout/assemble_top.py` `plan_rails()`
returns `{net: {y, x1, x2, w, layer}}` and `plan["nets"]` carries endpoints
with per-net current; ONR has `chip/floorplan/pll_rails.json` (inputs, each
with a `basis`) and the derived `pll_power.json`.

**The solver.** Nodes at taps and branch points; conductance `w/(L·Rs)` per
metal segment and `n_cuts/R_cut` per via stack; inject tap currents, pin the
pad, solve `G·v = i`. These grids are tens to a few hundred nodes — a
pure-Python LU is milliseconds and adds no dependency, which matters given
that these repos run raw `python3` through a cluster activation wrapper.

The output worth having is **both checks on one line**: per-tap drop, worst
node, the binding segment, and the EM margin on that same segment.

## 3. The prerequisite: the three flows are not aligned

Measured 2026-08-06, not assumed.

| capability | spec2si-tsmc65 (65 nm) | spec2si-tsmc28 (28 nm) | spec2si-xt011 (110 nm) |
|---|---|---|---|
| metal Imax | `em_card.metal_imax_ma`, keyed **by layer name** M1–M9 | `em.metal_mA`, keyed **by tier class** M1/Mx/My/Mz/Mu/Mr | — |
| Imax formula | `k·(w − dw)`, `w` **drawn** (no `layout_scale`) | `k·(0.9·w − offset)` — the 0.9 is `layout_scale`, a process shrink | `k·w`, **no offset**, width-tiered; `em_output_wlt drawn` |
| length/width boost | **hardcoded in `routing.em_width`** | `em.length_width_boost`, a card table keyed on length *and* width, per class | — |
| temperature derate | table on the card, **never read** | `em.temp_derate`, applied in `metal_imax_mA` and `via_imax_mA` | — |
| via per-cut Imax | `via_imax_ma_per_cut`, VIA1–8 | `em.via_mA`, CO + VIA1–8 | — |
| via array rule | `via_array_factor` (a **credit** for ≥2 cuts) | none — `ceil(I / per_cut)` | — |
| stack limit | `stack_imax_ma_per_stack` | none | — |
| poly Imax | `poly_imax_ma_per_um` | none | — |
| sheet resistance | `rc_card.metal.<layer>`, rho table over (width, spacing) | `rc.metal.<layer>`, rho over (width, **thickness**); the LEF scalar it replaces was **2× wrong on M7** | `rc_card.metal.<layer>`, LEF `RPERSQ` + `THICKNESS`, all six layers |
| per-cut resistance | `rc_card.via.<tier>`, VIA1–VIA9 + contacts | `rc.via.<n>`, VIA1–VIA8 + RV + contacts (QRC) | `rc_card.cut.<n>`, VIA1–VIA4 + VIACT |
| temperature on resistance | `temp_tc1`/`temp_tc2` per layer, ref 25 °C | none recorded | — |
| RC corner | five; `rcworst` chosen and stamped | five; `rcworst` chosen and stamped | three (QRC-Max/Min/Typ) |
| EM reference temp | 110 °C | 110 °C | **125 °C** |
| EM lifetime | not a dimension | not a dimension | **1000 / 10000 / 100000 h** |
| API surface | `routing.em_width / em_cuts / em_stack_ok` + `sheet_res / line_res / via_res` | `process.metal_imax_mA / width_for_current / via_imax_mA / vias_for_current / em_temp_derate / sheet_res / line_res` | — |

Of the four originally listed as defects, **two survived and two were
retracted** once the vendor files and the DRM were read rather than
reasoned about. Both retractions are kept, struck through: a finding that
did not hold is evidence about how it was reached.

**(a) AIML applies no temperature derate at all.** `em_card.json` carries a
`temp_rating` table; `grep` finds no reader for it, and `em_width`,
`em_cuts` and `em_stack_ok` take no temperature argument. ONR applies its
derate and defaults to 125 °C. So the same rail sized on the two nodes is
sized against different physics, and on AIML it is the reference-temperature
number regardless of junction temperature. The derate spans a large factor
across its tabulated range, so this is not a rounding-order difference.
Fixing it will move existing widths — it is the change with real blast
radius, and it needs a re-spin audit rather than a quiet edit.

**(b) ~~The width argument means two different things.~~ RETRACTED
2026-08-06 — this was never a defect.** The 28 nm QRC tech file declares
`layout_scale 0.9` in one line of its process block: **that process is
fabricated at 0.9× the drawn dimensions** (confirmed by the flow owner).
The 65 nm ICT has no `layout_scale` at all. So `k·(0.9w − offset)` on one
node and `k·(w − dw)` on the other are both correct, and the `0.9` that
`extract_via_em.py` painstakingly recovered from two via tiers is a
process-wide optical shrink, not an EM convention. XT011 states its own
convention explicitly (`em_output_wlt drawn`).

Where the shrink bites is also not where this document guessed:

- **EM capacity — yes.** Capacity goes with cross-sectional area `0.9w · t`,
  and thickness does not scale with a layout shrink.
- **Via resistance and via current — yes, squared.** A cut's area goes as
  `(0.9·cut)²`, i.e. 0.81.
- **Line resistance — no.** A uniform shrink divides length and width alike,
  so the square count `L/W` is unchanged. The claim below that ONR's
  `line_res` is optimistic by 11 % **was wrong and is withdrawn.**
- **The rho table lookup — yes, second order.** The table's axis is silicon,
  so a drawn width must be scaled before lookup or it reads a slightly low
  resistivity. Fractions of a percent, not 11 %.

**(c) A boost law is living in a `.py` file.** AIML hardcodes the short-line
enhancement inside `em_width`; ONR reads it from the card. Both repos'
policy says rule values do not go in tracked source. The AIML form is also
width-blind where ONR's is not.

**(d) ~~The via array factor points in an unexpected direction.~~
RETRACTED 2026-08-06 — this was not a defect either.** Read directly from
T-N65-CL-DR-001 v2.2 §11.3.3.5.4, Table 11.3.5: every via and contact tier
carries a *"Single via"* row and a *"Via array"* row at **exactly twice**
it, for arrays of two cuts or more. So AIML's `via_array_factor` is a
credit, it is 2×, it is stated per via, and `em_cuts` has implemented it
correctly all along. This document argued the other way from the general
principle that current crowds into an array's outer cuts — sound in
general, wrong for this process. **A general principle is not evidence
about a specific rule.**

The companion rule — that the short-length and array credits may not be
combined — is the DRM's own wording (*"can't collateral at the same
time"*), and `em_cuts` already gates the short credit to the single-via
branch.

ONR granting no array credit is therefore **not** a disagreement to
resolve in one direction; it is a question for the 28 nm DR, which is a
different document about a different process.

**A conservatism nobody chose, found in the same table.** It also doubles
the *metal line's* limit when the line is fed by an array
(`Mx: 2 × 1.877 × (w−0.016)`). `em_width` does not apply it — it is not
told whether the line terminates in an array — so it sizes against the
single-via limit and can return a width up to 2× wider than required.
Safe direction, unchosen margin, now recorded.

**The card checks out entry by entry.** Every `k` and `dw`, the contact
and per-cut via ratings, the nine temperature factors and the short-length
law reproduce the DRM's tables exactly. This is the first time the
transcription has been confirmed against the source — worth having, given
the 28 nm port found its own via table shifted by a tier.

## 4. Both features are keyed to the process flavor — and only one repo records it

This is the part that must not be left implicit. **EM limits and sheet
resistance are both properties of the metal *option*, not of the node.**

| repo | node | option string | recorded where | read from |
|---|---|---|---|---|
| spec2si-tsmc65 | TSMC 65 nm LP | `1P9M 6X1Z1U` | `metal_stack` + `process` in `tsmc65_gridcard.json` (added `c002851`) | the deck path and card id, which agree — **not** yet a PDK read |
| spec2si-tsmc28 | TSMC 28 nm HPC+/ULL | `9M_5X1Y1Z1U_UT-AlRDL`, flavor `HPC_PLUS`, PDK `CRN28HPC+ULL_v1.8_2p3a_20211109` | `tech/cards/tsmc28_gridcard.json` → `process` | the `nch_mac` CDF `pdkVersion` parameter |
| spec2si-xt011 | X-FAB XT011 PDSOI | option `1157` — **adopted, not decoded**; `metal_count: null` | `tech/cards/xt011_gridcard.template.json` → `process` | reference designs |

**The clearest evidence for this whole section turned up in the PDK tree
itself.** `$TSMC_PDK` on the cluster resolves through `$OPTION`, and the
65 nm release ships **three metal stacks side by side** as sibling
directories — `1p6m3x1z1u`, `1p7m4x1z1u`, `1p9m6x1z1u` — each with its own
DRC decks and its own QRC tech files. `pdk_setup.csh` picks the third, with
the first still present as a commented-out line. So the option is not a
label describing the node; it is a **selector**, and every EM and resistance
number downstream of it belongs to whichever directory was chosen.

Read the two TSMC options against each other:

- `6X1Z1U` — above M1, six thin (X), one thick (Z), one ultra-thick (U).
  So M2–M7 thin, M8 = Z, M9 = U.
- `5X1Y1Z1U` — five thin, one intermediate (Y), one Z, one U.
  So M2–M6 thin, **M7 = Y**, M8 = Z, M9 = U.

Both are nine-metal stacks. Both have an M7. On one node M7 is a thin tier
and on the other it is an intermediate one — **different rule family,
different `k`, different minimum width, different sheet resistance, same
layer name.** A layer name carries no EM or resistance meaning without the
option string beside it.

This is not hypothetical. It has already fired once, on the only node that
has an explicit map: `spec2si-tsmc28/tech/process.py` `METAL_CLASS` had M6,
M7 and M8 each mapped one tier too high, and **M8 landed on `Mr`, the
aluminium redistribution tier, whose limits are far more permissive** — a
power rail on M8 would have been sized against the wrong metal entirely.
Corrected 2026-07-31 against the tech LEF's own minimum widths, which
identify each tier unambiguously. The card's `metal_mA` still carries an
`Mr` entry that `METAL_CLASS` deliberately maps nothing to.

AIML has no such map. Its `em_card.json` is keyed by layer name, so
`6X1Z1U` is encoded in it *implicitly*, with no place for the assumption to
fail. A shuttle flavor change would leave every number in it silently
wrong. The card's own `_stack` note records that it stopped at M7 until
2026-07-31 and had to be extended to the thick tiers — which is the same
fact discovered the hard way rather than declared.

XT011 cannot write either card yet, and should not try. Its `CLAUDE.md` is
already explicit: *"`1157` is adopted from the reference designs, not
decoded. It must hold identically across `PRIMLIB_1157`, `TECH_XT011_1157`,
the PVS runset `XT011_1157`, and the Tanner tech library. Any flavour change
moves all of them together. Do not freeze a grid card before SCOPE WP 0.5
decodes it."* The EM and resistance sections inherit that discipline: schema
now, `null` values that **raise** at the point of use, populated after WP 0.5.

**The requirement that follows.** On every node:

1. the layer → tier-class map is an explicit **card** artifact, derived from
   the tech LEF's minimum widths (which identify a tier unambiguously) and
   never from a layer's name or index;
2. every EM and resistance entry is stamped with the **option string** it
   was read under, alongside the rule or file that fixed it;
3. an option change invalidates both cards together — they are one fact with
   two consumers, not two facts.

## 5. Open questions

**Settled 2026-08-06 — per-cut via resistance is not available.** The
populated ONR rules card carries `rs_ohm_per_sq` on all five metal tiers and
**no `r_ohm_per_cut` on any of its six via tiers**. `extract_metal_res.py`
has a correct parser for it (a `RESISTANCE` line is ohm/sq on a `ROUTING`
layer and ohm/cut on a `CUT` layer — one column, two units), so the parser
ran and found nothing: the 28 nm tech LEF has no `RESISTANCE` on cut layers.
`process.line_res`'s "metal only, so this is a **lower bound**" caveat is
therefore correct, and the claim in `extract_metal_res.py`'s own title line
that it extracts per-cut resistance is not currently met. **Any IR number
*that node* produces is a lower bound until a per-cut resistance is sourced
elsewhere** — the DR document, the QRC tech file, or measurement.

**⚠️ CLOSED 2026-08-06 — and it was never a general limitation.** The
28 nm **QRC tech file** carries `area_resistance` for `VIA1`…`VIA8`, `RV`
and the contacts. It had simply never been opened: the flow concluded from
the LEF's silence that the data did not exist, rather than that *that file*
did not have it. `tech/probes/extract_qrc_rc.py` reads it, and ONR's IR
numbers are no longer a bound. The same run settled the `suspect` flag on
M7 — the ICT says ~0.212 Ω/sq against the LEF's 0.428, so the LEF **was**
carrying the copied thin-tier default, wrong by exactly the 2.1× thickness
ratio the suspicion was raised on.

**And XT011 — the least mature repo — has the most complete data of the
three.** Its tech LEF carries `RPERSQ` *and* a per-cut `RESISTANCE` on every
CUT layer, and the kit ships X-FAB's own **EM-only ICT files**, per option,
per corner, and per target **lifetime** (1000 / 10000 / 100000 h), written
for Voltus. So on that node the Voltus path and the fast-estimator path
share an input and neither needs a transcription step. One caveat travels with
it: the vendor marks the EM files *"Data has alpha status"*.

**⚠️ Corrected 2026-08-06.** This paragraph previously said `METCT` — the
thick copper top metal — had no `RESISTANCE` or `THICKNESS` in the LEF. It
has both, as does every routing layer, and every cut layer carries a
per-cut `RESISTANCE`. **The XT011 LEF is complete.** The false claim came
from reading a fixed line window around the layer instead of parsing the
block; the extractor now reads whole blocks and prints `NOT IN THE LEF` per
layer, which is the check that settles it. *Do not conclude a field is
absent from a window that did not reach it.*

The original text of this section follows, kept because the reasoning that
produced the wrong conclusion is worth seeing:

**~~this is an ONR limitation, not a general one~~** This section previously called
sourcing a cut resistance "the highest-value open item" and put ONR first.
Step 3 found the 65 nm answer, and it inverts that: **the AIML PDK ships no
tech LEF at all, and its QRC `.ict` carries per-cut resistance for every via
tier** (`VIA1`…`VIA9` plus poly and diffusion contacts) as an `area_resistance
R A` pair. So the mature node can cost an M1→M9 rail through its via stacks
and produce a **value**, while the 28 nm node can still only produce a
**bound**. The solver should therefore be developed against AIML, where its
output is checkable, and ONR's use of it must carry the bound caveat until
its own cut resistance is sourced — plausibly from the 28 nm QRC tech file,
which nobody has looked at, rather than from the LEF that does not have it.

**The 65 nm QRC file is also richer than the 28 nm LEF in three further
ways**, each of which turns a number that looked like a constant into one:

- **resistivity is a 2-D table over (width, spacing)**, not a scalar. A thin
  tier at minimum width is materially more resistive than the same tier at
  1 µm. There is consequently no `sheet_res(layer)` taking a layer alone on
  that node — asking for one is asking for whatever width the caller assumed;
- **temperature coefficients**, referenced to the ICT's own `temp_reference`
  of **25 °C** — which is *not* the EM card's 110 °C. Both references appear
  in one calculation and neither may be assumed;
- **five RC corners** where the LEF had one number, with a best-to-worst
  spread of about **37 %** on a 300 µm trunk. `rcworst` is the IR corner.

**Does the effective/drawn shrink apply to resistance too? — answered for
65 nm, still open for 28 nm.** The ICT states `wire_top_enlargement` and
`wire_bottom_enlargement` per layer, so the conducting width is
`w + (top + bottom)/2` — a signed, few-nanometre, per-layer geometric bias.
**It is not the EM card's 0.9 factor and must not be conflated with it**: one
is a cross-section, the other a current-rule convention, and an RC file says
nothing about EM. The 28 nm question — whether `RPERSQ` is per square of
drawn width while the conducting width is `0.9·w`, making `line_res`
optimistic by about 11 % — is unchanged and still open.

**DC vs AC limits — the repo contradicts itself.** `tech/process.py`'s
2026-07-31 correction states the tech LEF carries `DCCURRENTDENSITY` on all
nine routing layers *and* `ACCURRENTDENSITY` beside it.
`analog/engine/char/libgen.py:1177` states the technology LEF carries **no**
`ACCURRENTDENSITY`. Both cannot be true, and it bears directly on the
transient half of this plan.

**Three currents, not one.** A transient yields `I_avg` (the EM DC rule —
what `divider_idd.py` measures), `I_rms` (Joule self-heat, which feeds back
into the temperature derate), and `I_peak` (static IR worst case).
`divider_idd.py` names the gap: *"a via array sized for the average is not
sized for `Irms`. No rule in the card asks for those, and nothing here
models it."*

## 6. Where the code should live

The flowkit's contract is deliberate — **shared rule set, not shared
engine** — and it was set on measured evidence: 94 Python files against 93,
21 overlapping filenames, exactly one byte-identical.

An IR solver is the rare piece that is genuinely node-agnostic: it touches
no PDK API, no deck, no PCell, only numbers a card hands it. So it splits
along the seam that already exists here:

- **vendored, byte-identical, hash-gated by `sync.py`**: the solver and the
  report schema;
- **local to each repo**: the resistance lookup, the current reader, and the
  topology adapter.

Plus a new core policy rule — `supply-drop-is-computed` — so XT011 carries
it as `not-implemented` and the gap is *counted* rather than invisible,
which is what that contract is for.

## 7. Voltus

Worth doing, and cheaper than expected: the recipe is already written down
in `spec2si-tsmc65/dig_flows/dig_tools_dig_flow_18.x_19.x/CERN_generic_flow_2020/built_project/signoff/voltus/`
— `voltus_init.tcl` (`read_db`, `read_spef` across three RC corners,
`check_pg_shorts`, `check_power_vias`, `set_pg_nets`,
`set_rail_analysis_domain`, `write_pg_library` → `techonly.cl`), plus
`voltus_static.tcl`, `voltus_dynamic.tcl` and `em_analysis.tcl`. It needs a
QRC tech file and a license check on the cluster.

Its role here is **correlation, not competition**. `ctrl_top` is the one
block with a real Innovus PG mesh (29 % / 27 % M8/M9 occupancy per
`spec2si-tsmc65/hybrid_adc/POWER_PLAN.md`), so it is the natural place to run
both and measure how far the fast estimate lands from signoff.

## 8. Sequencing

Alignment first — the calculator is not worth building on two cards that
disagree about what a width means.

| # | step | repo | status | note |
|---|---|---|---|---|
| 1 | stamp the option string into every EM/resistance entry; make the layer→tier map a card artifact | ONR | **done** `0bb594d` | was a literal in `process.py` and the fourth copy of one fact; now `metal_stack.rule_family` in the tracked grid card, read by `process.py` and `extract_metal_res.py`. Derived map verified identical to the literal; `em_power.py` output unchanged |
| 1b | record the metal option and its tier map | AIML | **done** `c002851` | the card had no `process` section at all. Flagged `PARTIAL`: composition is from the deck name + the DRM table, **not** cross-checked against a tech LEF — which is what caught ONR's one-tier error |
| 2 | add the EM + resistance schema as `null`s that raise | XT011 | **done** `5dc8ffd` | schema only. The three points where the prior nodes disagree are written in as questions to read, not blanks to fill from a neighbour |
| 2b | **extract the XT011 data** | XT011 | **done** `cc1b34c` | `extract_rc_em.py` reads the kit's techLEF + EM ICT. Six metals, five cuts, twelve EM rule sets, no missing values. `1157` decoded from two independent vendor artefacts, cross-checked on every run |
| 3 | locate the 65 nm tech LEF / QRC tech file; write the `extract_metal_res.py` equivalent | AIML | **done** `1799f18` | there is no LEF; the QRC `.ict` is the source and a richer one. `routing.sheet_res / line_res / via_res` added. A parser bug that dropped every layer's temperature coefficients was caught by its own asymmetry and is now guarded by `audit_parse` |
| 4 | give the AIML EM API a temperature and read `temp_rating` | AIML | **done** `87be330` | the table was dead data. `temp_c` defaults to the card's own reference, so all 4122 checked cases reproduce byte-for-byte and the offline regression fingerprint is identical. At 125 °C the same rail needs **2.7×** the width and the pin case goes 5 cuts → 12 |
| 5 | move the AIML length/width boost onto the card | AIML | **done** `87be330` | `em_card.length_boost`. A card lacking the key raises rather than falling back to the old literals. Cluster card patched to match |
| 6 | resolve drawn vs effective width against the 65 nm DRM | AIML | **done** | settled by `layout_scale`: 28 nm has 0.9, 65 nm has none, XT011 states `drawn`. §3(b) retracted |
| 7 | resolve the via array factor direction | AIML | **done** `7d90e01` | DRM §11.3.3.5.4 Table 11.3.5: the 2× array credit is correct as implemented. §3(d) retracted, and the whole card confirmed against the source |
| 8 | source a per-cut via resistance, or label every result a lower bound | **ONR only** | **half done** `0bb594d` | no longer a general gap — AIML has cut resistance from its `.ict`. For ONR the absence is now reported in a paragraph instead of an empty table. Next place to look is the **28 nm QRC tech file**, which nobody has opened; the LEF definitively does not have it |
| 9 | the solver, vendored; the new core policy rule | flowkit | **done** `9c3255b` | `irdrop/solver.py`, vendored into all three and hash-gated. Ten offline checks with answers known without the solver, plus negative controls. Core policy 1.1.0 adds `supply-drop-is-computed`; all three declare it `partial`. Adapters landed too, see 9b |
| 9b | the per-repo adapters | ONR + AIML | **done** `a9b9ffd` `b9f176c` | ONR: `tech/irdrop.py` + `pll_3_def.ir_report`, **rail and trunk both tapped, via stacks counted** — the worst drop went 1.164 mV (metal, star) → 3.860 mV (solved, with vias). AIML: `ir_grid.py`, self-checked against a hand-computed trunk, not yet called from `assemble_top`. XT011 has no engine to adapt |
| 9c | **couple the currents to a simulated operating point** | all three | **done** `d201efc` `d024add` `fe4586d` `054e441` | `irdrop/currents.py` vendored: sign, SI prefixes, and **coverage raises rather than reading zero**. Local hooks on all three — PSF on AIML, text oppoint + the rails budget on ONR, the card on XT011 |
| 10 | correlate against Voltus on `ctrl_top` | AIML | open | §7 |

**The alignment pass is complete.** All three nodes have extracted RC data
from their vendor's own files, a recorded metal stack, and the vendored
solver. Steps 1–6, 8 and 9 are done.

**One remains.** Step 10 needs a Voltus licence and a routed block.

**What the adapters found.** On the 28 nm PLL grid the via stacks are
roughly **half** of every drop at a block that climbs one — a four-cut
M5→M8 stack is about 5.6 Ω against 0.6 Ω for a 300 µm M9 trunk. So
"metal only is a lower bound" was an understatement by about a factor of
two, not a rounding caveat. And solving rather than summing mattered
independently: pricing a shared rail as a star from the entry, which is
what the previous model did, understated `vco.VDD` by 2×.

**The loop is closed.** `irdrop/currents.py` is vendored alongside the
solver — shared for the same reason, since a Spectre operating point is a
*simulator* format and `Vdd:p` means the same thing on all three nodes.
Each repo has its own hook onto it: `currents_from_psf` on AIML,
`currents_from_oppoint` plus a `currents_from_rails` bridge on ONR, and
the card on XT011. What stays local is only *how you get the numbers*.

The three things it centralises are the three that fail by returning a
believable number: the terminal **sign** (magnitude taken, but a
*mixture* of signs across supplies is a bench fault and is reported), the
SI **prefix** on a text oppoint's unit token (dropping it is twelve
orders of magnitude and still looks like a current), and **coverage** — a
net with no simulated current *raises* instead of reading as zero,
because "absent" and "draws nothing" both render as 0.000 mV and only one
is true.

It also keeps the **kind** with the numbers. `avg` is what every EM card
here asks for; `peak` is what a supply-sensitive block actually sees;
`rms` drives self-heating and nothing states a limit for it. Two sets of
different kinds refuse to merge.

**What is still hand-declared is now visible rather than invisible.** ONR
still reads its budget from `pll_rails.json` and AIML from
`NET_CURRENT_MA` — but both now arrive as a `Currents` carrying a `kind`
and a provenance string, so a report can say which it got. Pointing a
block's bench at `currents_from_psf`/`currents_from_oppoint` is a
one-line change at the call site rather than a new code path. And step 9's second half — the per-repo adapters that turn a rail
plan into a `Grid` — is the work that makes the solver reachable from a
flow rather than from a script. Step 3 is the next one that needs cluster access; step 4
is the one with blast radius. Step 8 gates whether step 9's output is a
value or a bound.

**What the alignment pass changed about the problem.** The tier map turned
out to be duplicated four times on ONR and absent on the other two nodes,
and the copy that disagreed is the one that had been wrong. So the ordering
above is now load-bearing rather than tidy: **the map has to be single-source
on a node before any EM or resistance number on that node is worth
stamping**, because stamping a number with an option string only helps if
one map turns that option into a tier.

## What this document does not contain

No rule values. The EM constants, derate tables, boost tables and sheet
resistances are foundry-confidential and live in the gitignored cards
(`spec2si-tsmc65` `em_card.json`, `spec2si-tsmc28` `tech/cards/rules_card.json`).
What is recorded here is **structure** — which keys exist, which are read,
which formula shape each node uses, and which option string fixes them.
