<!--docmeta
title: The RoutingCard schema — one shape for four processes
genre: reference
status: active
area: top
owner: soumyajit
updated: 2026-08-26
summary: The card contract routekit/card.py implements, written down field by field: the tracked-structure / untracked-values split that keeps foundry numbers out of git, the bare-or-annotated value shapes card_num accepts, family and tier resolution by membership lists only (the P_METAL_FAMILY failure made structurally impossible), missing-is-a-refusal vs measured-absent-is-an-answer, the per-layer via-enclosure override that is the designed fix for the M9/VIA8 class, where the EM and RC sections sit in the same contract, the probe kit that populates a card, and the scope of the current map. The worked example is MINI_CARD in routekit/test_card.py; the richest real instance is tsmc28's gridcard + rules_card pair; the sky130 build-out is specified in routekit_cards_sky130.md.
-->

# The RoutingCard schema — one shape for four processes

`routekit/card.py` is the implementation; this page is the contract.
The worked example is `MINI_CARD` in `routekit/test_card.py` (it is a
test fixture precisely so the example cannot rot). The richest real
instance is tsmc28's tracked `tsmc28_gridcard.json` + untracked
`rules_card.json` pair, whose shape this schema formalizes rather than
invents.

## 1. Files: the tracked / untracked split

A card may be **one file** (sky130 — the process is public) or **two**
(the TSMC ports): a tracked *structure* card carrying identifiers,
membership lists, laws and provenance, and an untracked *values* card
carrying foundry-confidential numbers, rebuilt on the cluster
(`push.sh` → `extract_dr.py` is the tsmc28 pattern).
`card.load_split(structure, values)` deep-merges them, values winning
key-by-key; **a missing values file is not a load error** — every
accessor that needs a number refuses per-fact instead, which is the
behaviour tsmc28's `routing_rules()` already chose. All loads are UTF-8
(`corpus.json`'s `windows_note` records what cp1252 cost).

## 2. Values: bare or annotated

A number is either bare (`"min_space_um": 0.05`) or annotated
(`{"value": 0.05, "rule": "Mx.S.1", "verified": "..."}`).
`card.card_num` accepts both; the annotated form is used where the rule
number and the LEF disagree and someone had to say which is which.
Provenance keys are free-form but the discipline is fixed: a measured
value carries what measured it (`rule`, `verified`, `source`), and an
**engine choice** (a track pitch, a preferred direction) is labelled as
one — never presented as a process fact.

## 3. `metal` — families resolved by membership lists ONLY

```json
"metal": {
  "<family>": {
    "layers": ["M2", "M3", "M4", "M5", "M6"],
    "min_width_um":        0.05 | {"value": ..},
    "min_space_um":        ..,
    "line_end_space_um":   ..,        // + optional line_end_def_um
    "wide_metal_space_tiers": [
      {"width_gt_um": .., "parallel_run_gt_um": .., "space_um": ..}],
    "min_area_um2":        ..,        // or in the top-level section
    "landing_pad_um":      ..,
    "max_width_um":        ..,        // when the deck constrains it
    "rs_ohm_per_sq":       ..         // the RC section rides here too
  }
}
```

**The `layers` list is the ONE authority for which family a metal
belongs to.** `CardRules` resolves by membership and refuses a layer no
list names; `card.validate` flags a family without a list and a layer
claimed twice. There is deliberately no separate layer→family map to
get wrong: tsmc28's `P_METAL_FAMILY` literal shipped shifted a tier for
M5/M6/M7 — `min_width("M7")` answered 0.400 against 0.100 — and was
measured and worked around in five places. A single-layer family may
use its own name as the layer name and omit the list.

## 4. `via` — tiers resolved by `cut_layers` lists

```json
"via": {
  "<tier>": {
    "cut_layers": ["VIA1", "VIA2"],
    "cut_um": ..,
    "enclosure": {
      "along_um": .., "across_um": ..,
      "crowded": { .. } | null,          // the EN.11-class conditional
      "overrides": {                     // per-LAYER enclosure, where a
        "M9": {"along_um": 0.3, ..}      // layer's requirement differs
      }                                  // from its tier's
    },
    "min_space_pair_um": ..,
    "rect_cut_um": [long, short],        // omit if the kit has none
    "redundancy_tiers": [ {..} ],        // [] = measured absent, see §4b
    "plate_proximity_rules": [ {..} ]    // [] = measured absent
  }
}
```

## 4b. `redundancy_tiers` — the field whose SHAPE is load-bearing

This was `{..}` here until 2026-08-26, and it is the one field where the
shape of the value decides whether metal is legal: `audit.via_wide_landing`
reads the option list directly, so an option this schema does not describe
is a construction the gate rejects.

```jsonc
"redundancy_tiers": [
  {
    "rule": "VIAx.R.2",
    "width_and_length_gt_um": 0.18,   // null = UNGATED: applies to every
                                      // connection on the tier (VIAu.R.2)
    "options": [                      // ANY ONE of these satisfies the tier
      {"count": 1, "shape": "rectangular"},
      {"count": 2, "shape": "square",      "max_space_um": 0.10},
      {"count": 3, "shape": "mixed", "square": 2, "rect": 1,
                                           "max_space_um": 0.65}
    ],
    "deck_body": "G1_A2R/G2_A2R merged at ..",   // optional provenance
    "measured": "2026-08-26 viapair_probe: .."   // optional: what DRC did
  }
]
```

Five rules the readers depend on, each already got wrong once:

- **`shape` defaults to `"square"` when absent.** Only `VIAx` has two cut
  forms, so the thick tiers state their options without one. Read the other
  way, every tier above `VIA5` silently became single-cut and Calibre
  answered with 16 `VIA6.R.2` and 20 `VIA7.R.2`.
- **An option with no `max_space_um` is ONE cut and no merge** — the lone
  slot. Options carrying one are satisfied when that many cuts *merge*, and
  merging is a CHAIN at that spacing, not a radius: four cuts in a row at
  the ceiling are one merged region and satisfy a count of four.
- **A tier may be stated flat** — `{rule, width_and_length_gt_um,
  max_space_um}` with no `options` at all. That is one construction, two
  square cuts, and it is what `via_pair_space` exists for. A reader that
  consults only `options` turns such a tier into "nothing satisfies this"
  and fires on every cut on it.
- **The SITE's tier is the highest any ONE of its two conductors reaches.**
  The deck's predicate is a disjunction — `(Mx wide AND Mx+1i) OR (Mx+1
  wide AND Mxi)` — so a 0.10 µm stub landing on a 1.0 µm bus is governed by
  the bus. Checking only the declared landing passes a two-square pair on a
  site whose tier has no two-square option at any spacing.
- **`width_and_length_gt_um` is `min(w, h) > thr`, not width alone.** A
  0.10 × 3.0 strap owes nothing and a 0.30 × 3.0 strap is the lower tier;
  length does not promote a site. This is what the deck's `WITH WIDTH`
  measures, confirmed by probe rather than read.

`mixed` carries its own `square` and `rect` counts because the deck states
those combinations explicitly (`(SQR >=2 <=3) AND (REC ==1)`); dropping
them loses the cheapest construction a narrow landing can hold.

`enclosure.overrides` is the **designed fix for the M9/VIA8 class**:
tsmc28 measured M9-over-VIA8 needing 0.300 µm where the card said 0.080
(2 blocking `M9.EN.1` on the tile's first build) and hardcoded the
answer in a def file because "teaching the engine a per-layer enclosure"
had no home. This is the home; moving that constant into the card is
consumer-side work recorded in the plan.

## 5. Missing is a refusal; absent is an answer

Two different "no", and the schema keeps them apart structurally:

- **Missing** — the key is not in the card. Every accessor raises
  `CardError` naming the card path to fill. Nothing defaults.
- **Measured absent** — the process genuinely lacks the rule. The card
  records the *empty* explicitly (`"redundancy_tiers": []`,
  `"plate_proximity_rules": []`) with provenance saying what was
  consulted. The engine's gate then answers clean because the question
  was answered, and `card.validate` flags a via tier that records
  neither.

sky130 is expected to be measured-absent for the whole redundancy and
plate families; that is a card entry, not an omission.

## 6. EM and RC ride the same contract

The tsmc28 values card already carries `em`, `rc` and per-family
`rs_ohm_per_sq` beside the DRC values — same file, same provenance
discipline, same split. The schema recognizes them as sections
(`routekit/elec`, phase 3, is their consumer); their internal shapes are
formalized when the elec unit vendors, against the same two real
instances (tsmc28's card, tsmc65's `em_card`/`rc_card`).

## 7. The probe kit — what populates a card

| probe | measures | card fields |
|---|---|---|
| `spec2si-xt011/tech/probes/tech_route_probe.il` | techfile layer rules + viaDefs | metal widths/spacings, via cut/pitch, grid |
| `spec2si-tsmc28/tech/private/extract_dr.py` | the DRC deck's numeric values (cluster) | the whole values card |
| `spec2si-tsmc28/tech/probes/via_probe.*` | drawn via geometry as the kit builds it | `via.*.cut_um`, `enclosure` |
| `spec2si-tsmc28` `devshapes.il` / `param3.il` | PCell drawn shapes, finger pitch laws | device card (`measured.*`) |
| `spec2si-sky130/tech/probes/sky130_mos2_pcell.il` | PCell terminal pins | sky130 device card |
| `spec2si-tsmc28/tech/probes/extract_qrc_rc.py` | QRC resistance/cap tables | `rc`, `rs_ohm_per_sq` |
| `spec2si-tsmc28/tech/probes/extract_via_em.py`, `extract_metal_res.py` | EM limits, sheet R | `em`, `rs_ohm_per_sq` |

And the probe that validates a populated card is `routekit/ruleprobe.py`:
card-driven violation/clean geometry pairs per rule kind, self-checked
against the audit engine offline (`selfcheck(rules) == []`), then
streamed to GDS by the consumer for the cluster-DRC arm — where the
deck's answers are diffed against each probe's `expect`. A probe the
card cannot support is returned `skipped` with its reason, never
silently dropped.

## 8. The current map — scope (lands with phase 5)

The card family's known weak input is current: EM floors are priced
from `PIN_CURRENT_MA`-class *declared* dicts (tuned to game via counts;
148 of xt011's 216 taps on assumptions that can be 25× off). The
current map replaces declaration with computation:

- **inputs**: operating-point currents at block ports (Spectre oppoint
  via the vendored `irdrop/currents.py` reader; per corner), the net
  tree (the same union-find the audits build), device terminal currents
  where the netlist carries them;
- **computation**: propagate per-branch currents down each net's tree
  (KCL at junctions; worst corner held), emitting a per-net, per-branch
  current card with the same provenance discipline;
- **consumers**: `routekit/elec`'s EM floors (width/cuts per branch,
  not per net), the supply-synthesis sizing, the IR solve's injections
  (`ir_grid.py` is the existing move in this direction and the code to
  extend);
- **not in scope**: transient/RMS EM (the cards carry Idc limits;
  anything beyond follows the cards, not the other way round).

Until it lands, declared currents carry provenance and an explicit
margin like every other card number — and the declared dicts are on the
retirement list with the hand route tables.
