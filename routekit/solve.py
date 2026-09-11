#!/usr/bin/env python3
"""glue_solver -- the chip's signal router: a multi-tier maze search over the
track grid, allocated globally with bounded displacement.

⛔ WHY THIS REPLACES THE GREEDY PAIR ROUTER. `glue_route`'s first core chose
ONE (H, V) pair per net from `pairs_for` and drew exactly V-H-V on it, in one
fixed order, with no rip-up. 64 of 136 nets routed and the 71 failures were one
problem in three costumes -- measured on the committed snapshot:

    38  "no free lane within N um of A"   the pin's own column is taken
    21  "lanes exist but no track joins"  no single H track spans the run
    12  "the tap did not"                 trunk+tap is not a net topology

Every one of those is a consequence of the SHAPE, not of the search width. The
handoff measured the width to saturation (LANE_CAND x TRACK_SEARCH of 4x48,
10x200 and 16x400 route 64, 64 and 65): sixteen times the search buys one net,
because the lanes are not there to be found -- they are held by nets already
placed, and a route that may not bend more than twice cannot go round.

So two changes, both structural:

  1. ⭐ A ROUTE IS NOT A PAIR. The unit of feasibility was raised from a tier to
     a pair (GLUE_ROUTER_SCOPE 4) and that was right as far as it went, but a
     pair is still two tiers chosen ONCE for a whole net. Here every run picks
     its own tier from M5/M6/M7/M8 and a route may bend as often as the
     geometry wants. `pairs_for` still says which tiers can carry a BUNDLE; it
     no longer dictates each route.

  2. ⭐ ALLOCATION IS GLOBAL, NOT GREEDY. `routing.SolverLanes` solved this
     failure class one level down (an early wide-window claim squatting on a
     narrow-window net's only lanes) with bounded recursive displacement, and
     its bounds are inherited rather than re-invented: MAX_DEPTH 6, MAX_MOVES
     400. What is displaced here is a whole ROUTE rather than a lane, and the
     blockers come from the same A* that finds the path -- a SOFT search prices
     other nets' metal instead of forbidding it, so the path it returns names
     exactly who has to move. Nothing guesses and nothing re-derives.

⚠ WHAT IS KEPT, BECAUSE EVERY ONE OF THEM WAS A DEFECT ONCE:

  - the wire is `max(tier minimum, one via pad)`, not the deck minimum: a track
    a via cannot land on is not a routing track;
  - a via stack claims and checks pads on EVERY tier it passes through, one
    track either side, because those pads are off-grid on the tiers the route
    does not otherwise use;
  - a terminal's last run is at the PIN's own y, off-grid, straddling two
    tracks -- snapping it to a track centre left 34 nets as open islands;
  - a run claims PAD/2 past each end, because a cut at the end of a run puts
    metal half a pad past it;
  - every terminal's access stack is an obstacle to every other net.

`Tracks` lives here rather than in `glue_route` so there is ONE occupancy
model: the greedy control and the solver must not be able to disagree about
what is free.

Determinism: every candidate list is built in a fixed order, the priority
queue breaks ties on a monotonic counter (never on `id()`, which would make
the router's output depend on the allocator), and there is no randomness.
"""
import heapq
import json
import os
import sys

#: ⚠⚠⚠ **THIS FILE'S BODY IS `spec2si-tsmc28`'s `tile_solver.py`, WHICH IS
#: `spec2si-tsmc65`'s `glue_solver.py` GENERALIZED.** The lineage, measured:
#: the 65 nm original closed a 12-block chip at 136/136 with all four audits
#: clean; the 28 nm port kept the body and repointed ten external references
#: onto an adapter, then the tile campaign generalized exactly the places
#: where a process fact had leaked into the body -- per-tier via pads
#: (`ca.via_pad`), phase-agnostic across/along helpers, per-terminal access
#: tiers and runway spans -- every one documented in place to REDUCE EXACTLY
#: to the 65 nm behaviour when the adapter supplies the 65 nm facts. First
#: run on the second node: 48/71 nets, before pin-access fixes.
#:
#: What was an import block is now `bind()`: the ONE seam. An adapter
#: supplies the ten symbols `tile_abstract` defined (TIER_RULE, TIER_AXIS,
#: ROUTING_TIERS, WIDE_RULE, is_wide, space via rules, rects, declared_boxes,
#: _name, via_pad) plus the via table; everything node-shaped that used to be
#: computed at import (BASE, PAD, CUT, LAND_TAPER, VIA_COST, ROUTE_TIERS) is
#: computed at bind. Nothing below `bind()` is edited per node.
#:
#: ⚠️ **KEEP IT THAT WAY.** A local "improvement" here is a divergence from
#: a router that has been through a tapeout, and a consumer's diff against
#: this file should find only its own adapter.
HERE = os.path.dirname(os.path.abspath(__file__))

ca = None          #: the adapter -- set by bind()
bd = None          #: the via table (an object with .VIA) -- set by bind()
BASE = None
_TILE_VIA = None
PAD = None
CUT = None


def bind(adapter, via_table=None, route_tiers=None, base=None,
         pad_via=None, pad_along_um=None, here=None, grid_rule_min=None):
    """Bind the solver to one process. MUST be called before any class or
    helper below is used; everything node-shaped is computed here and only
    here.

    adapter      the ten-symbol seam (`tile_abstract` is the worked example)
    via_table    an object with `.VIA[name] = (layer, cut, enclosure, pitch,
                 _)`; defaults to the adapter itself
    route_tiers  the tiers a route may use, e.g. (35, 36, 37, 38) --
                 REQUIRED: the list is a per-chip decision with a measured
                 reason per entry, never a default
    base         the tier terminal access stacks top out on; defaults to
                 `min(adapter.ROUTING_TIERS)`
    pad_via      the via whose cut+enclosure define the flat PAD/CUT scale
                 (a SEARCH WEIGHT and a taper, not a clearance); defaults to
                 the base tier's own via
    pad_along_um the measured pad bar length; defaults to the 0.380 both
                 decks measured
    grid_rule_min tiers to pitch on the tier's own minimum width instead
                 of on `wire_w` -- see GRID_RULE_MIN. Omit for the
                 behaviour every consumer had before it existed.
    here         the CONSUMER directory HERE-anchored file loads resolve
                 against (the body loads e.g. adc_floorplan.json beside
                 itself at 65 nm); defaults to this file's own dir
    """
    global ca, bd, BASE, _TILE_VIA, PAD, CUT, PAD_ALONG, LAND_TAPER,         VIA_COST, ROUTE_TIERS, HERE, GRID_RULE_MIN
    if route_tiers is None:
        raise ValueError("bind() requires route_tiers -- the tier list is "
                         "a per-chip decision, never a default")
    ca = adapter
    bd = via_table if via_table is not None else adapter
    BASE = min(ca.ROUTING_TIERS) if base is None else base
    _TILE_VIA = pad_via if pad_via is not None else (
        "VIA%d" % (BASE - ca.BASE_LY + 1))
    PAD = round(bd.VIA[_TILE_VIA][1] + 2 * bd.VIA[_TILE_VIA][2], 4)
    CUT = bd.VIA[_TILE_VIA][1]
    if pad_along_um is not None:
        PAD_ALONG = pad_along_um
    LAND_TAPER = round(VIA_HALO + CUT / 2.0 + PAD_ALONG / 2.0, 4)
    VIA_COST = round(6 * PAD, 4)
    ROUTE_TIERS = tuple(route_tiers)
    GRID_RULE_MIN = frozenset(grid_rule_min or ())
    if here is not None:
        HERE = here            # the consumer dir file loads anchor to

#: ⛔ AND A PAD IS A BAR, NOT A SQUARE -- 0.140 ACROSS, THIS ALONG.
#: MEASURED ON THE DECK (`via_probe.py`, cluster 2026-08-02), not reasoned:
#:   * `VIAx.EN.2__EN.3` / `Mx.EN.2__EN.3` fire on 0.140 x 0.140 and on
#:     NOTHING ELSE tried -- 0.140 x 0.200 is already clean. The rule pair is
#:     all-sides / two-opposite-sides, and a square has no long side.
#:   * `Mx.A.1` (minimum area) was then BISECTED on the deck, because 0.020 um
#:     of pad length decides three nets: it fires up to **0.0512** and is clean
#:     from **0.0528** on M2-M7, and fires up to 0.0400 / is clean from 0.0420
#:     on M1. So 0.140 x 0.380 = 0.0532 passes everywhere -- and 0.380 is
#:     exactly the height of `dac/dn11`'s port stub, which a 0.400 bar does not
#:     fit beside the array's plates. The interval is what is known; the rule
#:     value itself is foundry-confidential and is not written down here.
#: Drawing the square cost 2,206 enclosure results and 518 area results on the
#: first chip run with signals -- from copying `qdi_tier`'s "enclosure 0.020"
#: without copying the 0.600 x 0.200 BAR it was measured on. Invariant 4.
#: ⚠ ACROSS stays 0.140: it is what sets the track pitch, and widening it
#: would re-pitch every tier. The bar grows along the tier's own axis, where
#: the wire already runs.
PAD_ALONG = 0.380


def pad_along(t):
    """The pad's extent ALONG tier `t`. -> um.

    ⚠️⚠️⚠️ **`PAD_ALONG` IS THE 65 nm BAR AND IT IS WRONG ON EVERY TIER HERE,
    IN BOTH DIRECTIONS.** `PAD` above carries exactly this warning for the
    ACROSS extent and was already fixed to ask `ca.via_pad(tier)`; the ALONG
    extent never got the same treatment and stayed a flat 0.380. Measured on
    the 28 nm card:

        M5  along 0.110   OVER-claims  3.45x
        M6  along 0.180   OVER-claims  2.11x
        M7  along 0.520   UNDER-claims 1.37x
        M8  along 1.220   UNDER-claims 3.21x

    The over-claim is a capacity loss -- and it is what refused `Dout[1]`'s
    climb off M5 at a terminal `pin_access` had proved, because a 0.380 bar
    where the via needs 0.110 reaches metal the via never touches.

    ⛔ The UNDER-claim is the serious half: on M7 and M8 the model reserves
    LESS than the metal it is standing for, which is the one shape of error
    `PAD`'s own note calls out as what invariant 5 exists to forbid.

    ⚠️ `LAND_TAPER` still uses the flat constant deliberately: it is a taper
    LENGTH and wants one scale for the whole board, exactly as `PAD` survives
    for `VIA_COST`.
    """
    return ca.via_pad(t)[1]

#: {(x, y) of an access point on BASE: half-extent ALONG the tier of the pad
#: `pin_access` RECORDED there}. Filled by glue_route from pin_access.json;
#: empty means every run end keeps the full PAD_ALONG reservation, which is
#: the safe (over-reserving) default. See Route.claims for what it buys.
TERM_PADS = {}

#: ⛔ THE DECK'S WIDE-METAL VIA RULE, the two numbers a drawn wire must
#: answer to (rule text quoted at `glue_draw.VIA_MULT`): past VIA_WIDE a
#: rung owes more than one cut (`VIAn.R.2/R.3`), and any single cut within
#: VIA_HALO of a plate wider AND longer than VIA_WIDE owes a second
#: (`VIAn.R.4`).
VIA_WIDE = 0.300
VIA_HALO = 0.800

#: ⭐ A WIDE TRUNK NARROWS BEFORE IT LANDS. A terminal's climb is drawn by
#: `glue_draw.climbs()` from the pads `pin_access` measured -- one cut per
#: rung, sized for a 0.140 wire -- and a wide run ending on top of it makes
#: every rung under it owe an array the 0.140 x 0.380 bar cannot hold.
#: Measured: 11 of the width-aware chip's 19 new DRC results, all of them
#: `VIA4.R.2/R.3/R.4` on `vrefp`/`topp` climb rungs. Widening the climb pad
#: instead would draw a pad the access measurement never checked, which
#: `glue_draw` rightly refuses. The remedy the rule itself offers: keep any
#: plate wider than VIA_WIDE more than VIA_HALO from the cut. So the last
#: LAND_TAPER of a wide run before a terminal access point is DRAWN at the
#: tier width -- the halo, plus half the cut, plus half a pad for where the
#: recorded landing bar may stand off the access point. The ohms are noise
#: (~1 um of thin metal per landing against a 10+ ohm margin) and the claim
#: is untouched: the band is reserved at the full width over the taper too,
#: which over-asks and cannot under-claim.
LAND_TAPER = None                  # computed by bind()

#: {(x, y) of an access point on BASE: the net it belongs to}. Filled by
#: glue_route beside TERM_PADS. `_land_taper` needs it because the taper is
#: OWNED: a wide run narrows near its OWN terminals' climbs (the deck's
#: VIAn.R.* results all sit on the widened net's own cuts -- D.6/E.5 measured
#: no cross-net case), and a net-blind lookup would taper `vrefp` for
#: standing near `topn`'s climb, which no rule asks for.
TERM_OF = {}

#: ⛔ A WIDE NET'S OFF-GRID LEG IS A STUB, AND THIS IS THE LENGTH THAT MAKES
#: THE MODEL TELL THE TRUTH. Claims are FILED on the tracks `covers()` names,
#: but an off-grid query inspects only its two `straddle` tracks (`band_at`
#: is 0 off-grid -- correctly, for a thin stub). So a wide net allowed to run
#: off-grid indefinitely walks through claims it never looks at: measured,
#: `vrefp`'s 70 um off-grid M5 approach at the pin's own y was committed OVER
#: `vrefn`'s landing stub (2 shorts) and 0.052 um from `topn`'s trunk, and
#: every query that approved it was thin. Under WIDE_MIN_RUN, `run_w` draws
#: and claims the leg at the tier width -- the same geometry the straddle
#: query prices -- so the cap is what makes search, claim and drawing agree.
#: The wide trunk approaches ON-GRID, where the band sees everything.
MAX_STUB = 1.99

#: metal tier -> the via layer joining it to the tier above.
VIA_OF = {31: 51, 32: 52, 33: 53, 34: 54, 35: 55, 36: 56, 37: 57, 38: 58}

#: ⛔ THE TIERS A ROUTE MAY USE, AND WHY THE LIST ENDS WHERE IT DOES.
#: M5-M7 alternate H/V/H and all three are offered to every run.
#:  - M1-M4 are INSIDE the blocks. `chip_abstract.ROUTING_TIERS` carries them
#:    as blockage only, and offering them would answer "M1/M2", which is not a
#:    chip tier at all.
#:  - M9 is excluded by MEASUREMENT, not policy: GLUE_ROUTER_SCOPE 4 measured
#:    0 of 24 M9 tracks free in the readout window, because 2.4 um straps on a
#:    12 um pair pitch sterilise every candidate at a 2.0 um rule. It is a
#:    power tier here because there is nothing on it to route on.
#:  - ⭐ M8 WAS OFFERED AND IS NOT ANY MORE, on two independent grounds, and
#:    it costs NOTHING: with M8 the solver routes 136 of 136 in 634 cuts, and
#:    without it 136 of 136 in **584** cuts over 27,308 um instead of 27,322
#:    (it pays 60 displacements instead of 3, which is search, not silicon).
#:      1. THE VIA IS NOT THE VIA THIS FILE PRICES. Every rung here is a
#:         `bd.VIA["VIA1"]` -- cut 0.100, enclosure 0.020, pad 0.140. True of
#:         VIA5 and VIA6; VIA7 crosses into the thick tiers and the signed
#:         block draws it at **0.360 with 0.120 of enclosure**, a 0.600 pad. A
#:         route that reserves 0.140 and draws 0.600 takes metal from its
#:         neighbours: measured, 78 VIA7 cuts produced 6 M7 shorts between
#:         routed nets and 5 more against `bias`, plus 2 pads inside the DAC
#:         keep-out. Every one of them is an artifact of the model, not of the
#:         routing.
#:      2. ⛔ M8 IS THE POWER MESH'S OWN TIER AND THE ROUTER CANNOT SEE THE
#:         MESH. `Tracks` is built from `chip_obstacles.json`, which is the
#:         BLOCKS; the chip's own 324 M8 mesh segments and 45 ladders are
#:         drawn by the merge and are in no map this router reads. A signal on
#:         M8 is therefore routed blind against the densest chip-level metal
#:         there is.
#:    Restoring M8 needs a per-LEVEL pad in `claims`/`pad_tracks`/`_stack_ok`
#:    and the mesh in the obstacle map -- both real work, neither of which buys
#:    a net.
#: ⚠️⚠️ **AND AT 28 nm THESE NUMBERS ARE M5, M6, M7 -- WHICH IS ALMOST
#: RIGHT BY ACCIDENT.** The 65 nm file meant M5-M7 and the layer INDEX is the
#: same here, so the constant transferred as three of the four tiers this
#: tile wants. M8 is added, and every one of the four reasons above has been
#: re-checked against this node rather than inherited:
#:
#:   * M1-M4 inside the blocks -- still true, and M5/M6 are only 9.1 % and
#:     14.7 % occupied, which is a working condition rather than a wall.
#:   * M9 excluded -- still excluded, for the opposite reason: it is EMPTY
#:     here, and that is why the tile's supply trunks took it.
#:   * the VIA is not the via this file prices -- **fixed**: `wire_w` and
#:     every reach ask `ca.via_pad(tier)` now, so a rung on M7/M8 reserves
#:     `VIA7`/`VIA8`'s pad and one on M5 reserves `VIA5`'s.
#:   * M8 is the power mesh's tier and the router cannot see the mesh --
#:     **fixed**: the tile's comb is drawn FIRST and handed in through
#:     `adc_8bit_async_ms_redundant_v3.supply_pg()`.
ROUTE_TIERS = None                 # supplied by bind()

#: Tiers whose TRACK GRID is pitched on the tier's own minimum width rather
#: than on `wire_w`. Supplied by `bind(grid_rule_min=...)`; empty means every
#: consumer behaves exactly as it always did.
#:
#: ⚠️⚠️ **THE PITCH AND THE DRAWN WIDTH ARE TWO QUESTIONS AND `wire_w`
#: ANSWERED BOTH.** Its invariant -- *"a track a via cannot land on is not a
#: routing track"* -- is real, but it is enforceable by CLAIMING the pad
#: rather than by pitching the whole tier to it, and `pad_tracks` already
#: derives that claim from `ca.via_pad` independently of the run width: at a
#: pitch below the pad it returns three tracks where it returned one.
#:
#: ▶ MEASURED on tsmc28's sub-ADC tile, 2026-08-26. M6 is the one tier that
#: is both INFLATED and CONGESTED -- pad 0.160 against a 0.050 rule, and
#: 9.9x occupancy -- so pitching it on the rule is 2.10x the tracks:
#:
#:     tier  min_w  via_pad  wire_w  pitch now  pitch on rule  tracks
#:     M5    0.050    0.050   0.050      0.100          0.100   1.00x
#:     M6    0.050    0.160   0.160      0.210          0.100   2.10x
#:     M7    0.100    0.400   0.400      0.500          0.200   2.50x  (1.0x occ)
#:     M8    0.400    0.520   0.520      0.920          0.800   1.15x  (1.0x occ)
#:
#: M5 has no inflation at all (its pad IS its minimum) -- confirmed rather
#: than derived: `HM[7]` routes entirely on M5 and prices identically at
#: both widths. M7/M8 are inflated and EMPTY, so narrowing them buys
#: capacity nobody is short of and costs the analog nets their ohms.
GRID_RULE_MIN = frozenset()


def wire_w(t):
    """The width a route draws on tier `t`: the tier minimum, or one via pad,
    whichever is larger. See PAD.

    ⚠️⚠️ **THE PAD IS `ca.via_pad(t)`'s AND NOT `PAD`'s.** `PAD` is one
    number for every tier -- the 65 nm file could use one because all three
    of its tiers shared a via class, and this tile's do not. At 0.520
    everywhere M5's pitch is 0.650 where the card says 0.100. The invariant
    is unchanged: a track a via cannot land on is not a routing track, per
    tier and by the via that actually lands there.
    """
    return max(ca.TIER_RULE[t][0], ca.via_pad(t)[0])


#: ⚠️⚠️⚠️ **THE ONE PLACE THIS FILE IS NOT THE 65 nm FILE, AND IT IS A PHASE
#: FIX RATHER THAN AN IMPROVEMENT.** `tile_abstract`'s docstring warns the
#: class outright -- *"THE DIRECTIONS ARE PHASE-FLIPPED. At 28 nm M1 is
#: VERTICAL where the ported engine assumed horizontal, and every tier
#: alternates from there"* -- and the adapter fixed `PREF_DIR`/`TIER_AXIS`.
#: What nobody audited is that THIS file hard-codes the same phase in the
#: arrival condition: `_goals_for` builds the anchor's LAND goal with `y` as
#: the across-coordinate and `x` as the along-coordinate, unconditionally,
#: and `_reach` drops onto it the same way. At 65 nm `BASE` was M5 and M5 was
#: HORIZONTAL, so across = y was right. Here M5 is **VERTICAL**.
#:
#: ▶ MEASURED, 2026-08-25: for `net31` the arrival built its run on M5 track
#: 440 -- centre **x = 44.000** -- for a terminal at **x = 63.865**. Track
#: 639. **19.865 um away.** It does not raise: a y in 0..52.83 is a perfectly
#: valid index into a tier whose 1754 tracks span x, so the goal is silently
#: constructed somewhere else and every net reports *"no path within the
#: search box"*. Worse, five nets reported SUCCESS with their far terminal
#: 23..26 um from any drawn metal.
#:
#: ⚠️ These helpers are PHASE-AGNOSTIC and reduce EXACTLY to the 65 nm
#: behaviour when the tier is horizontal, which is what keeps this a
#: generalisation rather than the divergence the header forbids. Every
#: expression below was `(x, y)` or `y`/`x` written out; none of the search,
#: the costs or the invariants changed.
def _across(g, t, x, y):
    """The coordinate `t`'s tracks are INDEXED by: y if it runs horizontal."""
    return y if g.rule[t][4] else x


def _along(g, t, x, y):
    """The coordinate a wire on `t` MOVES in: x if it runs horizontal."""
    return x if g.rule[t][4] else y


def _xy(g, t, across, along):
    """`(x, y)` from a tier's across/along pair -- the inverse of the two."""
    return (along, across) if g.rule[t][4] else (across, along)


def _name(ly):
    return ca._name(ly)


_PG_CACHE = {}


def power_pg(snap=None, tiers=range(31, 40)):
    """The chip's own drawn POWER metal, per tier. -> {tier: [(x1,y1,x2,y2)]}.

    ⛔ ONE DERIVATION FOR THREE READERS. `pin_access` needs it (a climb pad may
    not stand in a supply ladder), this router needs it (a wire may not cross
    one), and `glue_draw.check` needs it (to say whether either got it wrong).
    Three copies of "where is the power" is precisely the shape of defect this
    project has met over and over, so all three call `adc_asm.power_shapes`,
    which is the function the MERGE itself calls -- planned offline off
    `chip_obstacles.json`, on the cluster off the block GDS, same plan and same
    gates either way.

    Cut layers are dropped: a via cut is inside its own pads, and those pads
    are here, so carrying the cuts would claim the same footprint twice.
    """
    key = id(snap)
    if key not in _PG_CACHE:
        import adc_asm
        out = {}
        for q in adc_asm.power_shapes(place=None, snap=snap)[0]:
            if q[0] in tiers and q[0] in ca.TIER_RULE:
                out.setdefault(q[0], []).append((q[2], q[3], q[4], q[5]))
        _PG_CACHE.clear()
        _PG_CACHE[key] = out
    return _PG_CACHE[key]


# ---------------------------------------------------------------------------
# occupancy
# ---------------------------------------------------------------------------

#: claim kinds. `release()` gives back ROUTE claims only -- an obstacle is not
#: this router's to drop, and a terminal's own access pad must survive its
#: net's route being ripped up (it is where the next attempt starts).
OBSTACLE, SEED, ROUTE = "obs", "seed", "route"

#: ⛔ A RESERVATION IS NOT METAL, AND ITS OWNER MUST NOT SEE IT. `route_widen
#: --reserve` claims the band a budgeted analog net needs so the other routes
#: leave; it is a placeholder for geometry that does not exist yet. Filed as
#: ordinary same-net metal it went through `_own_clear`, which is a DRC merge
#: test -- and a net's OLD route sitting 0.047 um from its OWN new band read as
#: a spacing violation. `vrefp` was dropped "reused claim no longer legal" and
#: re-routed onto a path with no corridor, undoing the very move the
#: reservation existed to protect. Its owner skips it; everyone else sees a
#: solid obstacle.
RESERVE = "reserve"


class Tracks:
    """Track occupancy per tier, over the whole die.

    ⚠ `free_tracks` answers about the OBSTACLE MAP and knows nothing about
    what this run has drawn -- invariant 5, and the defect this project has met
    four times. So the map's answer is computed ONCE per tier into a blocked
    interval list per track, and every wire this router commits is added to the
    same structure. One question, one answer, both sources.
    """

    #: ⚠️ SEAM: the default was `tiers=ca.ROUTING_TIERS`, evaluated at
    #: import -- impossible once the adapter arrives through bind(). The
    #: sentinel resolves to the same value at CALL time; behaviour is
    #: unchanged for every caller.
    def __init__(self, snap, tiers=None, span=None, pg=None,
                 reserved=None, widths=None):
        tiers = ca.ROUTING_TIERS if tiers is None else tiers
        self.snap = snap
        # ⭐ WHAT A NET IS WIDE. `net_classes.json` says how much series
        # resistance a net may have and `route_budget` turns that into a
        # width; until now the router knew none of it and drew 0.140 um for
        # everything, so a wire that had to be 0.97 um wide was WIDENED
        # AFTERWARDS -- geometry translated on top of a solved board, with the
        # corridor moved, the stems carried, the terminals re-found and every
        # one of those steps wiped by the next re-route (ROUTE_BUDGET E.7).
        # The width belongs HERE, where the maze can route to it: a wide wire
        # sterilises the tracks its metal covers, owes the WIDE spacing rule to
        # its neighbours, and is drawn at its own width by `wire()`.
        self.widths = {n: float(w) for n, w in (widths or {}).items()
                       if w and float(w) > 0.0}
        self._band = {}
        # ⛔ THE CHIP'S OWN METAL IS NOT IN THE OBSTACLE MAP. `chip_obstacles`
        # is the BLOCKS; the power mesh, its 45 supply ladders and their links
        # are drawn by the same merge that draws these routes and appear in no
        # map this router reads. Routing against the blocks alone put 9 M5, 16
        # M6 and 3 M7 wires straight through a ladder -- signal-to-supply
        # SHORTS, every one of them invisible to `audit_shorts` (which sees
        # only the router's own geometry) and to `pin_access` (same map).
        # Measured by `glue_draw.check(power=True)`, which is the first thing
        # in this project to have both sets of rectangles in one place.
        # ⚠ `pg` is a MAP of tier -> rects, exactly like `ca.rects`' output
        # minus the block name; `power_pg()` derives it from `adc_asm`.
        pg = pg or {}
        self.tiers = [t for t in tiers if t in ca.TIER_RULE]
        # (tier, track index) -> [(lo, hi, net, kind, co, sp)]. `co` is the
        # ACROSS-axis coordinate of the metal that claim stands for -- a track
        # centre, an off-grid stub's own y, or a via pad's centre. It is there
        # because same-net is not the same as merged; see `merged_or_clear`.
        # `sp` is the clearance THAT PIECE demands, which is not the tier's for
        # everything on the die -- see below.
        self.occ = {}
        self.rule = {}
        x1, y1, x2, y2 = span or self.die(snap)
        self.span = (x1, y1, x2, y2)
        # ⛔ A KEEP-OUT IS A RULE, NOT METAL, and the wide rule must not price
        # one: `dac_core`'s declaration is a 262 um rectangle standing in for
        # 543,533 combs, and charging 0.160 against its boundary would move
        # every route on the east of the die away from a shape nothing is drawn
        # at.
        keepout = ca.declared_boxes(snap)
        # ⭐ A BLOCK RECT THAT IS A PIN'S OWN CONDUCTOR IS TAGGED WITH ITS NET.
        # The map is net-blind and mostly that is right -- but a pin whose own
        # conductor reaches the ROUTING tiers walls its own terminal in:
        # `vref/iref`'s M5/M6 riser straddles its anchor pad, `bounds`
        # collapsed the run window to a point, and the net was UNREACHABLE at
        # a terminal `pin_access` had proved. `net_probe`'s extraction (exact,
        # disjointness-gated) is the one derivation that knows which shapes
        # belong to which net; a tagged rect behaves exactly as before for
        # every OTHER net and as (extent-form) merged-or-clear for its own.
        owned = {}
        try:
            import net_probe
            for _net, _pins in ((net_probe.load() or {}).get("signals")
                                or {}).items():
                for _shapes in _pins.values():
                    for _s in _shapes:
                        if _s[4] in self.tiers:
                            owned.setdefault(_s[4], []).append(
                                (_s[0], _s[1], _s[2], _s[3], _net))
        except Exception as _exc:
            # ⚠️⚠️ **THIS WAS BARE, AND THE SILENCE COST A SESSION.**
            # `net_probe` did not exist in this repo at all; the
            # ModuleNotFoundError was swallowed here on every run, `owned`
            # stayed empty, and every pin's own conductor walled in its own
            # terminal -- `bounds` collapsing to a zero-width window on 53 of
            # 57 unrouted nets, which is the failure the comment above
            # describes and nothing reported. A fallback that changes what the
            # router can reach has to say so.
            owned = {}
            print("[tile_solver] net_probe unavailable (%s: %s) -- the "
                  "obstacle map is NET-BLIND, so a pin whose own conductor "
                  "reaches a routing tier will wall in its own terminal"
                  % (type(_exc).__name__, _exc))
        # ⭐ AN ELECTRICAL RESERVATION IS AN OBSTACLE THAT KNOWS WHOSE IT IS.
        # `route_widen --reserve` claims the band a budgeted analog net needs
        # beyond its drawn width, so the readout bundle routes around it
        # instead of back into it. Passing that band through `pg` was the first
        # attempt and it is WRONG: `pg` is net-blind, so the reservation
        # blocked the very net it was for -- `vrefp` and `vrefn` came back
        # "reused claim no longer legal", `topp`/`topn` failed outright, and
        # the router dropped from 136 to 133. A reservation enters HERE, with
        # an owner, and is then merged-or-clear for that net and solid for
        # everyone else -- which is exactly the treatment a pin's own
        # conductor already gets.
        for _t, _rs in (reserved or {}).items():
            for _r in _rs:
                owned.setdefault(_t, []).append(tuple(_r))
        self._reserved = {int(k): [tuple(r) for r in v]
                          for k, v in (reserved or {}).items()}
        _rkey = {(int(k), round(r[0], 4), round(r[1], 4), round(r[2], 4),
                  round(r[3], 4)) for k, v in self._reserved.items()
                 for r in v}
        for t in self.tiers:
            # ⚠️ the PITCH's width, which is `wire_w`'s unless this tier
            # was opted in -- see GRID_RULE_MIN. `net_w` still floors every
            # net at `rule[t][0]`, so this is the tier's minimum becoming
            # the default rather than a new kind of width.
            w = (ca.TIER_RULE[t][0] if t in GRID_RULE_MIN else wire_w(t))
            s = ca.TIER_RULE[t][1]
            pitch = w + s
            horiz = ca.TIER_AXIS[t] == "H"
            base = y1 if horiz else x1
            top = y2 if horiz else x2
            self.rule[t] = (w, s, pitch, base, horiz,
                            int((top - base) / pitch) + 1)
            for (a, b, c, d, _blk) in (list(ca.rects(snap, t))
                                       + [tuple(r[:4]) + ("PG",)
                                          for r in pg.get(t, ())]
                                       + [tuple(r[:4]) + ("RESERVE",)
                                          for r in self._reserved.get(t, ())]):
                lo, hi = (b, d) if horiz else (a, c)
                run = (a, c) if horiz else (b, d)
                # ⚠ WIDE IS A PROPERTY OF THE OBSTACLE; the parallel run is a
                # property of the QUERY, and `_reaches` finishes the job.
                # Nothing this router draws is wide (0.140 wire, 0.140 x 0.380
                # pad), so the wide side is always this one.
                wide = (ca.is_wide((a, b, c, d))
                        and (t, round(a, 4), round(b, 4), round(c, 4),
                             round(d, 4)) not in keepout)
                # ⛔ AND THE INDEX MARGIN IS THE OBSTACLE'S OWN RULE. `half` is
                # what decides which tracks a shape is even FILED under, so a
                # 2.400 um mesh stripe filed at 0.100 loses the finding before
                # any query is asked -- which is 21 of the wide-metal results
                # the first chip with signals came back with. Per obstacle, so
                # a thin neighbour is filed exactly where it always was.
                half = w / 2.0 + (ca.WIDE_RULE[2] if wide else s)
                k1 = int((lo - half - base) / pitch)
                k2 = int((hi + half - base) / pitch) + 1
                for k in range(max(k1, 0), min(k2 + 1, self.rule[t][5])):
                    c0 = base + k * pitch
                    if c0 + half > lo and c0 - half < hi:
                        # ⛔ THE OBSTACLE KEEPS ITS OWN ACROSS-EXTENT (lo, hi).
                        # Registering it against every track it touches is the
                        # cheap INDEX; throwing the extent away made the index
                        # the answer, and a track is not a wire. An off-grid
                        # stub sits up to half a pitch from the track it is
                        # indexed under, so a wire that clears the obstacle by
                        # the full rule was refused because a WIRE ON THAT
                        # TRACK would not have. Measured on the east port band:
                        # the corridor's top 0.24 um read as unusable when
                        # 0.17 of it is.
                        own_net = None
                        for (oa, ob, oc, od, on) in owned.get(t, ()):
                            if abs(a - oa) < 2e-3 and abs(b - ob) < 2e-3 \
                                    and abs(c - oc) < 2e-3 \
                                    and abs(d - od) < 2e-3:
                                own_net = on
                                break
                        kind = (RESERVE
                                if (t, round(a, 4), round(b, 4), round(c, 4),
                                    round(d, 4)) in _rkey else OBSTACLE)
                        self.occ.setdefault((t, k), []).append(
                            (run[0], run[1], own_net, kind, (lo, hi),
                             ca.WIDE_RULE[2] if wide else s))

    @staticmethod
    def die(snap):
        """The DIE, not the extent of the metal on it.

        ⛔ THIS RETURNED THE OBSTACLE MAP'S BOUNDING BOX -- 799.750 wide, being
        where the blocks happen to carry M5-M9 -- and the die is 852.782. So
        the track grid stopped short of both edges, every boundary port sat
        OUTSIDE it, and `index()` produced tracks that `free()` rejected as out
        of range. The router reported the port nets as congested; they were
        off the map. A grid derived from where metal IS cannot answer about
        where metal ISN'T, which is the whole of routing.

        ⛔⛔ AND THEN IT READ THE WRONG DIE. `HERE` is rewritten by the
        vendoring shim to the host's artifact directory, and the name was the
        UNVERSIONED `adc_floorplan.json` -- so a v6 run laid its track grid
        over v1's **852.782 x 196.810** while the chip is 658.337 x 197.810.
        Same failure as the docstring above, one cause further out: not a grid
        derived from the wrong thing, a grid derived for the wrong chip. Every
        east-edge port sits 194 um outside it.
        Measured 2026-09-06, the first time `glue_route` ran on this variant.
        """
        try:                                     # the host's variant resolver
            import fp_variant
            path = fp_variant.floorplan_path()
        except Exception:                        # noqa: BLE001 -- vendored use
            path = os.path.join(HERE, "adc_floorplan.json")
        fp = json.load(open(path, encoding="utf-8"))
        return (0.0, 0.0, fp["metrics"]["W"], fp["metrics"]["H"])

    # -- geometry -------------------------------------------------------
    #: ⚠ HISTORY, and the anchor for MAX_STUB. This was the length past which
    #: an off-grid run carried the contract width, because `vrefp` once ran
    #: 70.4 um along its terminal's own coordinate and drawing that thin cost
    #: 93 ohm (152.9 against 76.5). Making it wide instead let the maze walk
    #: it through claims its straddle-only queries never saw -- two shorts
    #: and a 0.052 um gap, every query thin (see MAX_STUB). The resolution is
    #: neither width: an off-grid leg may not BE long. MAX_STUB caps it just
    #: under this constant, so `run_w`'s off-grid test and the old length
    #: test agree on every leg the router can now produce, and the trunk
    #: turns onto a track, where the band prices it correctly.
    WIDE_MIN_RUN = 2.0

    def covers(self, t, co, w):
        """The tracks metal of width `w` centred at `co` conflicts with.

        One function for both cases: an ON-GRID wire (co is a track centre)
        gets its band, an OFF-GRID one gets the tracks its own metal reaches --
        which is what `straddle` computes for a minimum wire and this
        generalises. A track is in the way when a minimum wire on it would come
        within the clearance: |dc| < w/2 + clear + w_tier/2.
        """
        lim = w / 2.0 + self.clear_for(t, w) + self.rule[t][0] / 2.0
        k0 = self.index(t, co)
        b = int(lim / self.rule[t][2]) + 1
        return [k for k in range(k0 - b, k0 + b + 1)
                if abs(self.centre(t, k) - co) < lim - 1e-9]

    def run_w(self, t, net, lo, hi, off=None):
        """The width a run carries: the net's, unless it is a TERMINAL LEG.

        ⛔ THE PREDICATE IS OFF-GRID-NESS, NOT LENGTH. It used to be
        `hi - lo < WIDE_MIN_RUN`, which called every short run a stub -- and
        `topp`'s interior 1.8 um M6 hop between two wide trunk legs was drawn
        0.140 um in the middle of a 0.589 um net, costing thin-wire ohms AND
        turning both its rungs into single cuts the wide-metal array could
        have halved. A terminal leg is off-grid by construction (it runs at
        the pin's own coordinate) and MAX_STUB bounds its length, so
        off-grid-ness is the whole test; an on-grid run is trunk whatever its
        length. Callers that know `off` pass it; extent-only callers were
        moved to `_ask_w`, which reads the same fact from `co`."""
        if net is None or off is not None:
            return self.rule[t][0]
        return self.net_w(t, net)

    def _ask_w(self, t, net, co):
        """The width the ASKING metal of a query would draw -- `run_w`'s
        off-grid test, read from the query's own `co` (a terminal leg's `co`
        is the pin's coordinate, which is no track's centre)."""
        if net is None:
            return self.rule[t][0]
        if co is not None and \
                abs(co - self.centre(t, self.index(t, co))) > 1e-9:
            return self.rule[t][0]
        return self.net_w(t, net)

    def band_at(self, t, net, k, co):
        """`band`, for a query at track `k` whose metal sits at `co`.

        ⛔ AN OFF-GRID RUN IS A TERMINAL STUB AND IT IS DRAWN AT THE TIER
        WIDTH -- `Route.segments` says so, and `claims` reserves it on the two
        tracks `straddle` names rather than on the band. So the band must not
        be asked about it either, and the reason is not tidiness: a 0.97 um
        band around a landing run makes the terminal's own access column
        illegal, the net cannot reach its own pin, and the maze searches the
        die to exhaustion proving it. The width belongs to the trunk, which is
        where the ohms are.
        """
        if net is None:
            return 0
        if co is not None and abs(co - self.centre(t, k)) > 1e-9:
            return 0
        return self.band(t, net)

    def net_w(self, t, net):
        """The width `net` draws on tier `t`: the contract's, or the tier's."""
        return max(self.rule[t][0], self.widths.get(net, 0.0))

    def clear_for(self, t, w):
        """The clearance a wire of width `w` owes -- `ca.WIDE_RULE` where it
        applies, and that rule is why a wide net cannot simply be a wider
        rectangle on the same track: it also pushes its neighbours further
        away."""
        return (ca.WIDE_RULE[2] if w > ca.WIDE_RULE[0] + 1e-9
                else self.rule[t][1])

    def band(self, t, net):
        """How many tracks EACH SIDE of its own a net of this width takes.

        ⚠ CACHED, because this is now in the router's hottest loop: `free`,
        `blockers` and `bounds` all ask it per query and the answer depends on
        nothing that changes during a solve.

        A track is taken when a minimum-width wire on it would come within the
        clearance of this net's metal: |dc| < W/2 + clear + w/2. At the tier
        minimum that is exactly one pitch, so `band` is 0 and every existing
        caller behaves as it always did -- which is the property that makes
        this change safe to land on a signed router.
        """
        key = (t, net)
        v = self._band.get(key)
        if v is None:
            w = self.net_w(t, net)
            if w <= self.rule[t][0] + 1e-9:
                v = 0
            else:
                lim = w / 2.0 + self.clear_for(t, w) + self.rule[t][0] / 2.0
                v = int((lim - 1e-9) / self.rule[t][2])
            self._band[key] = v
        return v

    def centre(self, t, k):
        return self.rule[t][3] + k * self.rule[t][2]

    def index(self, t, v):
        return int(round((v - self.rule[t][3]) / self.rule[t][2]))

    def ntracks(self, t):
        return self.rule[t][5]

    def horiz(self, t):
        return self.rule[t][4]

    def limits(self, t):
        """The die extent ALONG tier `t`'s tracks -- the run axis, not the
        band axis. An H tier runs in x."""
        x1, y1, x2, y2 = self.span
        return (x1, x2) if self.rule[t][4] else (y1, y2)

    def wire(self, t, k, lo, hi, off=None, w=None):
        """-> the rect a wire on this track (or at off-grid coordinate `off`)
        would occupy. `w` is the net's own width; absent, the tier's."""
        w = self.rule[t][0] if w is None else max(w, self.rule[t][0])
        c = self.centre(t, k) if off is None else off
        return ((lo, c - w / 2.0, hi, c + w / 2.0) if self.rule[t][4]
                else (c - w / 2.0, lo, c + w / 2.0, hi))

    # -- occupancy queries ----------------------------------------------
    def merged_or_clear(self, t, c1, c2):
        """Two SAME-NET pieces on tier `t`, at across-coordinates c1 and c2,
        overlapping along the track: is that legal?

        ⛔ SAME-NET IS NOT THE SAME AS MERGED, and treating it as such is a
        real violation the audit found twice. DRC merges POLYGONS, not nets:
        two
        rectangles of one net that do not touch are two shapes, and Mx.S.1
        applies between them exactly as it would between strangers. So the
        legal cases are the two ENDS of the range --

            |c1 - c2| <  w          the metal overlaps: one polygon, no rule
            |c1 - c2| >= w + space  a full pitch apart: the rule is met

        -- and the band between them is a violation. Measured: `dn0` jogged
        0.230 um through M6 between two M5 tracks and left its own two M5
        pieces 0.090 apart against a 0.100 rule; `s_lvl` did the same at 0.070.
        Both passed every check the router made, because both were "same net".

        `c1`/`c2` of None mean the caller does not track coordinates (the
        greedy control) and keeps the older, blanket same-net exemption.
        """
        if c1 is None or c2 is None:
            return True
        d = abs(c1 - c2)
        w, sp = self.rule[t][0], self.rule[t][1]
        return d < w - 1e-9 or d >= w + sp - 1e-9

    def _own_clear(self, t, k, cc, co):
        """`merged_or_clear`, for a piece whose across-EXTENT is known -- a
        block rect tagged with its own net. Merged is any positive metal
        overlap with the extent (one polygon on the mask); clear is a full
        space past either edge; the band between is the violation, exactly as
        the centre form has it."""
        cq = self.centre(t, k) if co is None else co
        w, sp = self.rule[t][0], self.rule[t][1]
        return (cq + w / 2.0 > cc[0] + 1e-9
                and cq - w / 2.0 < cc[1] - 1e-9) \
            or cq + w / 2.0 <= cc[0] - sp + 1e-9 \
            or cq - w / 2.0 >= cc[1] + sp - 1e-9

    def _reaches(self, t, k, e, lo, hi, co, w=None):
        """Does obstacle entry `e` reach a wire on track `k` (or at off-grid
        `co`) running over [lo, hi]? -> bool.

        ⛔ THE ACROSS-AXIS CLEARANCE IS PER OBSTACLE, and it is the whole of the
        wide-metal rule (`Mx.S.2.1`, `chip_abstract.space`). A 2.400 um shape
        demands 0.160 where a 0.100 wire demands 0.100 -- but ONLY where the two
        run parallel for more than 0.400, which is a property of this query and
        not of the obstacle. A wire crossing the END of a mesh stripe owes it
        the thin-tier minimum; one running beside it for 12 um owes 0.160.

        ⚠ `co is None` means an ON-GRID query, and the answer is the track
        centre -- the same coordinate the constructor filed the obstacle under,
        so nothing changes for a thin neighbour.

        ⛔ AND `w` IS THE ASKING WIRE'S WIDTH, NOT THE TIER'S. This measured the
        obstacle against a 0.140 um wire whoever asked, so a 0.966 um one --
        whose edge is 0.343 um further out -- reached into metal this call said
        it cleared: `vrefp` was routed straight through an M7 POWER STRAP that
        is in the map, and `glue_draw.check(power=True)` found it. The wide
        rule is per obstacle AND per asker; half of it was here.
        """
        cc = e[4]
        sp = e[5] if len(e) > 5 else self.rule[t][1]
        if sp > self.rule[t][1] and                 min(e[1], hi) - max(e[0], lo) <= ca.WIDE_RULE[1] + 1e-9:
            sp = self.rule[t][1]           # too short a parallel run to be wide
        wq = self.rule[t][0] if w is None else w
        half = wq / 2.0 + max(sp, self.clear_for(t, wq))
        cq = self.centre(t, k) if co is None else co
        return not (cq + half <= cc[0] + 1e-9 or cq - half >= cc[1] - 1e-9)

    def _claim_clear(self, t, e, cq, w):
        """Is a foreign CLAIM `e` far enough from metal of width `w` whose
        centre is at `cq`? -> bool.

        ⛔⛔ **THE CLEARANCE A PAIR OWES IS THE LARGER OF THE TWO, AND ONLY
        THE ASKER'S HALF WAS EVER READ.** `claim` files `sp` -- *the
        clearance THIS metal demands of others* -- and the three daylight
        tests that judge a foreign claim (`_free1`, `_blockers1`, `bounds`)
        each composed their margin from the ASKER's own width and clearance
        and then added only the other's half-width. So the verdict depended
        on WHICH NET ASKED, and a chip is committed in one order:

            topp 0.992 vs vcm 0.140, three tracks apart at 0.7200 um
              asked by topp : 0.496 + 0.160 + 0.070 = 0.7260 -> refused
              asked by vcm  : 0.070 + 0.100 + 0.496 = 0.6660 -> ALLOWED
            required        : 0.496 + 0.070 + max(0.100, 0.160) = 0.7260

        Six thousandths of a micron, and `glue_draw` reported it as
        `WIDE M6 topp vs vcm` and `WIDE M7 dn7 vs topp` on a board the router
        called legal with `audits 0/0/0`. The obstacle-map path (`_reaches`)
        has always taken `max(sp, clear_for(w))`; only the claim path did
        not, and `Route.legal` -- the gate for exactly this -- re-asks
        `bounds`, which is one of the three. A gate that shares the defect it
        is gating cannot fire.

        ⚠ MEASURED, NOT ASSUMED, that this costs one net: of the four widths
        the electrical contract sets, only `topp` (0.992) needs more than
        three tracks -- topn 0.933, vrefp 0.966 and vrefn 0.971 all clear at
        0.7200, so for them the two readings agree and nothing moves.

        ⛔⛔ AND THE PARALLEL-RUN TEST IS THE CLAIM'S OWN METAL LENGTH, NOT
        ITS OVERLAP WITH THE QUERY. Measuring the overlap is what the rule
        means and it is EXACTLY WRONG HERE, because at query time the asker's
        final extent does not exist yet -- the maze asks about one step at a
        time. Measured, and it is why the first version of this fix changed
        nothing: at a 0.240 pitch every step is under the 0.400 threshold,
        so every step demoted itself to the thin rule and `vcm` walked the
        entire length of `topp`'s band one legal-looking cell at a time.

            free(M6, k1500, 2.845 um)  -> False      the whole run
            free(M6, k1500, 0.401 um)  -> False
            free(M6, k1500, 0.399 um)  -> True   <-- and the maze asks this
            free(M6, k1500, 0.240 um)  -> True

        The claim's metal length BOUNDS the parallel run any neighbour can
        have with it, so it is the one extent that is both known here and
        cannot be walked around. Short claim -- a via pad is 0.380 -- can
        never make a wide pair; long claim charges the wide rule to whoever
        comes near it, which is conservative by exactly the case the deck
        would let through (`eoc` shares 0.240 um of metal with `topp` and is
        charged as if it shared more). One track, against a rule a searcher
        can defeat by taking smaller steps.
        """
        sp = max(e[5] if len(e) > 5 else self.rule[t][1], self.clear_for(t, w))
        if sp > self.rule[t][1] + 1e-9:
            a_lo = e[7] if len(e) > 7 else e[0]
            a_hi = e[8] if len(e) > 8 else e[1]
            if a_hi - a_lo <= ca.WIDE_RULE[1] + 1e-9:
                sp = self.rule[t][1]       # too short a claim to be wide
        w_other = e[6] if len(e) > 6 else self.rule[t][0]
        return abs(cq - e[4]) >= w / 2.0 + w_other / 2.0 + sp - 1e-9

    def free(self, t, k, lo, hi, net=None, clear=None, co=None):
        """Is track `k` on tier `t` clear over [lo, hi] FOR `net`?

        ⭐ FOR THE NET, NOT FOR A MINIMUM WIRE. A net the contract makes wide
        covers more than its own track and owes the wide rule to whatever is
        beside it, so the question it has to ask is about the whole band its
        metal lands on. At the tier minimum the band is one track and this is
        the query it has always been.
        """
        w = self._ask_w(t, net, co)
        if w <= self.rule[t][0] + 1e-9:
            return self._free1(t, k, lo, hi, net, clear, co)
        c = self.clear_for(t, w) if clear is None else clear
        return all(self._free1(t, kk, lo, hi, net, c, co, w)
                   for kk in self.covers(t, self.centre(t, k) if co is None
                                         else co, w))

    def _free1(self, t, k, lo, hi, net=None, clear=None, co=None, w=None):
        """`free`, for ONE track and metal of width `w` (default: the tier's).
        Same-net metal is a merge, not a conflict -- but only where it really
        merges."""
        if k < 0 or k >= self.rule[t][5]:
            return False
        c = self.rule[t][1] if clear is None else clear
        base = self.rule[t][1]
        wq = self.rule[t][0] if w is None else w
        half = wq / 2.0 + (base if w is None else self.clear_for(t, w))
        # ⚠⚠ HOISTED, AND NOT AS MICRO-OPTIMISATION. `_claim_clear` is the
        # general form and this is the router's hottest loop -- the same
        # warning the obstacle branch below already carries. Asking the
        # general question of every thin neighbour took a 120 s chip solve
        # past 600 s of CPU without finishing it. Where NEITHER piece asks
        # more than the tier minimum the pairwise rule REDUCES to the
        # arithmetic that was always here -- max(base, base) is base, and a
        # clearance that is not the wide one has no parallel-run test -- so
        # the fast path is not an approximation of the answer, it IS the
        # answer. Proven over every wire/pad width pair at every offset.
        wide_me = w is not None and self.clear_for(t, w) > base + 1e-9
        for e in self.occ.get((t, k), ()):
            a, b, n, _kd, cc = e[:5]
            if _kd == RESERVE and n == net:
                continue                    # a net does not obstruct itself
            own = n is not None and isinstance(cc, tuple)
            if n is None or (own and n != net):
                # ⚠ the thin neighbour keeps its own two comparisons. Nearly
                # every obstacle on the die is thin and this is the router's
                # hottest loop; routing the whole chip through the general
                # form cost minutes, and the general form has to answer the
                # same thing here anyway.
                if len(e) > 5 and e[5] > base + 1e-9:
                    if not self._reaches(t, k, e, lo, hi, co, w):
                        continue
                elif co is not None and (co + half <= cc[0] + 1e-9
                                         or co - half >= cc[1] - 1e-9):
                    continue
            elif n == net and (self._own_clear(t, k, cc, co) if own
                               else self.merged_or_clear(t, cc, co)):
                continue
            elif n != net and cc is not None and not own:
                # ⭐ A FOREIGN CLAIM KEEPS ITS OWN ACROSS-COORDINATE, the same
                # lesson the obstacles already got ("the obstacle keeps its
                # own (lo, hi)"): a claim is FILED under k±1 as an index, and
                # reading the index as the answer refused geometry the deck
                # allows. Measured: `mg1` and `mg2`'s seq stubs sit 0.27
                # apart -- a full pitch, legal -- and each blanketed the
                # other's only escape; whoever routed second failed, and
                # eviction just mirrored the loss. Where both across
                # coordinates are known, a full wire-plus-space of daylight
                # is CLEAR; a claim without one stays a blanket.
                # ⚠ THE DAYLIGHT IS PAIRWISE: my half-width, the OTHER
                # claim's half-width, and the larger of the two clearances. A
                # full wire-plus-space clears two minimum wires and nothing
                # else, and assuming it for a 0.97 um neighbour is how three
                # `code*` nets came to stand inside `vrefn`'s band.
                cq = self.centre(t, k) if co is None else co
                if not wide_me and (e[5] if len(e) > 5 else base) \
                        <= base + 1e-9:
                    if abs(cq - cc) >= half + (e[6] if len(e) > 6
                                               else self.rule[t][0]) / 2.0 \
                            - 1e-9:
                        continue            # neither side wide -- see above
                elif self._claim_clear(t, e, cq, wq):
                    continue
            if a < hi + c and b > lo - c:
                return False
        return True

    def blockers(self, t, k, lo, hi, net=None, clear=None, co=None,
                 w=None):
        """Who is in the way over [lo, hi]. -> (hard, frozenset of nets).

        `hard` is True when the OBSTACLE MAP blocks -- block metal, or off the
        end of the grid. Those are not displaceable and a soft search must obey
        them exactly as a hard one does. The net set is the displaceable part,
        and the reason the soft search exists.

        ⭐ Over the whole band the net's width occupies -- see `free`. A wide
        net displaces everyone standing in its band, which is what makes the
        eviction pass unnecessary for it: the router asks for the room it needs
        while it is still choosing where to go.
        """
        # ⛔⛔ **`w` IS THE METAL BEING ASKED ABOUT, AND IT IS NOT
        # ALWAYS THE NET'S WIRE.** A via PAD is `ca.via_pad(t)` wide, a run
        # is `net_w` wide, and `_stack_ok` asks this about a PAD. The two
        # were EQUAL on every tier of both decks -- `wire_w` is `max(tier
        # minimum, one via pad)` -- so the difference could not show, and
        # the day a tier was pitched on its rule instead (GRID_RULE_MIN) the
        # model checked 0.050 um of metal where the router drew 0.160.
        #
        # ⚠️ AND AN EXPLICIT `w` KEEPS THE CALLER'S TRACK. The wide path
        # below DISCARDS `k` and re-derives from `covers(t, co, w)` -- the
        # tracks the metal LANDS on, right for a run whose claims are filed
        # on exactly those. A via pad's claims are filed on `pad_tracks`,
        # which is WIDER: at a 0.100 um pitch a 0.160 um pad lands on ONE
        # track and conflicts with THREE. Re-deriving threw the other two
        # away, so the claim was broadcast wider than the question was
        # asked. A caller that passes `w` has already chosen its tracks.
        #
        # ⚠️ None by default IS the net's wire, so every existing caller
        # asks exactly what it always asked.
        if w is not None:
            _c = self.clear_for(t, w) if clear is None else clear
            return self._blockers1(t, k, lo, hi, net, _c, co, w)
        w = self._ask_w(t, net, co)
        if w <= self.rule[t][0] + 1e-9:
            return self._blockers1(t, k, lo, hi, net, clear, co)
        c = self.clear_for(t, w) if clear is None else clear
        hard, nets = False, set()
        for kk in self.covers(t, self.centre(t, k) if co is None else co, w):
            h, ns = self._blockers1(t, kk, lo, hi, net, c, co, w)
            hard = hard or h
            nets |= ns
        return hard, frozenset(nets)

    def _blockers1(self, t, k, lo, hi, net=None, clear=None, co=None, w=None):
        """`blockers`, for ONE track and metal of width `w`."""
        if k < 0 or k >= self.rule[t][5]:
            return True, frozenset()
        c = self.rule[t][1] if clear is None else clear
        base = self.rule[t][1]
        wq = self.rule[t][0] if w is None else w
        half = wq / 2.0 + (base if w is None else self.clear_for(t, w))
        # ⚠⚠ HOISTED, AND NOT AS MICRO-OPTIMISATION. `_claim_clear` is the
        # general form and this is the router's hottest loop -- the same
        # warning the obstacle branch below already carries. Asking the
        # general question of every thin neighbour took a 120 s chip solve
        # past 600 s of CPU without finishing it. Where NEITHER piece asks
        # more than the tier minimum the pairwise rule REDUCES to the
        # arithmetic that was always here -- max(base, base) is base, and a
        # clearance that is not the wide one has no parallel-run test -- so
        # the fast path is not an approximation of the answer, it IS the
        # answer. Proven over every wire/pad width pair at every offset.
        wide_me = w is not None and self.clear_for(t, w) > base + 1e-9
        hard, nets = False, set()
        for e in self.occ.get((t, k), ()):
            a, b, n, _kd, cc = e[:5]
            if _kd == RESERVE and n == net:
                continue                    # a net does not obstruct itself
            own = n is not None and isinstance(cc, tuple)
            if n is None or (own and n != net):
                if len(e) > 5 and e[5] > base + 1e-9:
                    if not self._reaches(t, k, e, lo, hi, co, w):
                        continue
                elif co is not None and (co + half <= cc[0] + 1e-9
                                         or co - half >= cc[1] - 1e-9):
                    continue
            elif n == net and (self._own_clear(t, k, cc, co) if own
                               else self.merged_or_clear(t, cc, co)):
                continue
            elif n != net and cc is not None and not own:
                # the across-distance clearance -- see free()
                # ⚠ THE DAYLIGHT IS PAIRWISE: my half-width, the OTHER
                # claim's half-width, and the larger of the two clearances. A
                # full wire-plus-space clears two minimum wires and nothing
                # else, and assuming it for a 0.97 um neighbour is how three
                # `code*` nets came to stand inside `vrefn`'s band.
                cq = self.centre(t, k) if co is None else co
                if not wide_me and (e[5] if len(e) > 5 else base) \
                        <= base + 1e-9:
                    if abs(cq - cc) >= half + (e[6] if len(e) > 6
                                               else self.rule[t][0]) / 2.0 \
                            - 1e-9:
                        continue            # neither side wide -- see above
                elif self._claim_clear(t, e, cq, wq):
                    continue
            if not (a < hi + c and b > lo - c):
                continue
            if n is None or own:
                # ⛔ block metal is not displaceable whoever owns it, and a
                # net's OWN unmergeable metal is hard the same way -- moving
                # another net would not help.
                hard = True
            elif n == net:
                hard = True
            else:
                nets.add(n)
        return hard, frozenset(nets)

    def bounds(self, t, ks, p, net, soft, pad, anchor=False, co=None):
        """The maximal run WINDOW around `p` on track(s) `ks` of tier `t`.

        -> (lo, hi) such that any run with both endpoints inside it is legal
        WITH its pad overhang, or None when `p` itself is blocked.

        ⛔ THE WINDOW IS INSET BY THE OVERHANG, not by the wire alone. A run
        claims PAD/2 past each end (a cut at the end of a run puts metal half a
        pad past it), so a window computed from the wire alone lets the router
        place an endpoint whose own pad already sits in someone else's
        clearance. That is the class the last session closed three times; it
        costs one `+ pad` here and nothing anywhere else.

        `ks` is a LIST because an off-grid run straddles the tracks
        `straddle()` names; the window is the intersection over all of them.
        `soft` prices other nets rather than obeying them, so only the obstacle
        map bounds the window.

        ⛔ `anchor` IS THE PIN, AND WITHOUT IT THE ROUTER REFUSES NETS AT THEIR
        OWN TERMINALS. The obstacle map carries no nets -- a block's own PORT
        metal is a rectangle exactly like any other -- so at an access point
        the map reports the pin as blocking the pin. Measured: `qdi_tier`'s
        east ports sit at x 503.270 and the M5 shape under them runs
        502.750..503.270, so `eng_*`, `done_*`, `dec_clk`, `cdone` and the
        whole `dn*` bus were refused at a terminal `pin_access` had already
        PROVED a via stack stands on -- and, worse, the same test made the far
        terminal unreachable, so A* searched the region to exhaustion before
        saying so (30-60 s per net).

        `pin_access` is the arbiter for that pad, having checked it with the
        block's own metal excluded. So the pad is GIVEN: an obstacle that
        reaches into it does not veto the window, it only says which way the
        wire may leave -- left of the pin, right of it, or (straddling) not at
        all. Everything beyond the pad clears normally.
        """
        lo, hi = self.limits(t)
        base = self.rule[t][1]
        # ⭐ THE BAND, AND THIS IS THE ONE PLACE IT HAD TO BE ADDED BY HAND.
        # `bounds` walks the occupancy itself rather than going through
        # `free()`, so the width wrapper there does not reach it -- and this is
        # the query `Route.legal` and the maze's every step ask. Without it a
        # 0.97 um route REUSED its 0.140 um answer and re-emitted it wider:
        # 136 of 136 "legal", 15 shorts in the geometry. A width the router
        # carries and does not CHECK is a width it does not have.
        b = self.band_at(t, net, ks[0], co)
        if b:
            ks = (range(ks[0] - b, ks[0] + b + 1) if len(ks) == 1
                  else sorted({k + e for k in ks for e in range(-b, b + 1)}))
        w = self.net_w(t, net)
        # ⚠ AN OFF-GRID QUERY IS A STUB AND IS PRICED AT WHAT A STUB DRAWS.
        # `band_at` already answers 0 there (F.3.2: a 0.97 band around a
        # landing run walls the net off its own pin) and MAX_STUB now bounds
        # the leg so `run_w` really does draw it thin; measuring the across
        # test at the net's full width here would be the same wall one
        # question later. ⛔ OFF-GRID MEANS OFF EVERY TRACK, not off ks[0]'s:
        # a wide run's band claim carries the RUN's centre as `co`, which is
        # not the neighbouring track's -- pricing those thin would weaken
        # `Route.legal` for exactly the claims the band exists to check.
        if co is not None and \
                abs(co - self.centre(t, self.index(t, co))) > 1e-9:
            w = self.rule[t][0]
        c = self.clear_for(t, w)
        half = w / 2.0 + c
        wide_me = c > base + 1e-9        # the fast path -- see `_free1`
        for k in ks:
            if k < 0 or k >= self.rule[t][5]:
                return None
            for e in self.occ.get((t, k), ()):
                a, b, n, _kd, cc = e[:5]
                if _kd == RESERVE and n == net:
                    continue          # a net does not obstruct itself
                own = n is not None and isinstance(cc, tuple)
                if n is None or (own and n != net):
                    # the window spans the whole track, so the parallel run a
                    # wide obstacle would need is asked over ITS OWN extent
                    if len(e) > 5 and e[5] > base + 1e-9:
                        if not self._reaches(t, k, e, a, b, co, w):
                            continue
                    elif co is not None and (co + half <= cc[0] + 1e-9
                                             or co - half >= cc[1] - 1e-9):
                        continue
                elif n == net:
                    if (self._own_clear(t, k, cc, co) if own
                            else self.merged_or_clear(t, cc, co)):
                        continue
                elif cc is not None and not own:
                    # the across-distance clearance -- see free()
                    # ⚠ the window spans the whole track, so -- exactly as the
                    # obstacle branch above -- the parallel run a wide claim
                    # would need is asked over ITS OWN metal extent.
                    cq = self.centre(t, k) if co is None else co
                    if not wide_me and (e[5] if len(e) > 5 else base) \
                            <= base + 1e-9:
                        ok = abs(cq - cc) >= half + (e[6] if len(e) > 6
                                                     else self.rule[t][0]) \
                            / 2.0 - 1e-9
                    else:
                        ok = self._claim_clear(t, e, cq, w)
                    if ok:
                        continue
                    if soft:
                        continue
                elif soft:
                    continue
                if b + c + pad <= p:
                    lo = max(lo, b + c + pad)
                elif a - c - pad >= p:
                    hi = min(hi, a - c - pad)
                elif not anchor or (n is not None
                                    and not (own and n == net)):
                    # ⚠ the anchor rescues the OBSTACLE MAP only -- which
                    # includes this pin's own tagged conductor (`pin_access`
                    # vouches for the pad against the block's own metal, tag
                    # or no tag). It says nothing about another net, or about
                    # this net's own unmergeable CLAIMS, and exempting those
                    # would launder a real violation through a real
                    # exemption.
                    return None
                elif b <= p:
                    lo = max(lo, p)          # it lies to the left of the pad
                elif a >= p:
                    hi = min(hi, p)          # ... to the right
                else:
                    lo, hi = max(lo, p), min(hi, p)   # it straddles the pad
        return (lo, hi) if lo <= hi else None

    # -- claims ---------------------------------------------------------
    def claim(self, t, k, lo, hi, net, kind=ROUTE, co=None, sp=None, w=None,
              mlo=None, mhi=None):
        """File a claim. `sp` is the clearance THIS metal demands of others.

        ⚠ It used to read "a claim is this router's own metal -- 0.140 wide,
        never wide metal", and the electrical contract retired that: a 0.97 um
        route owes its neighbours `ca.WIDE_RULE`, and a claim that files the
        tier minimum lets the next net stand 0.100 um away from metal that
        needs 0.160. `Route.claims` supplies it from the net's own width.
        """
        # ⛔ AND THE CLAIM CARRIES ITS OWN WIDTH. The across-distance test is
        # PAIRWISE -- w_me/2 + w_other/2 + the larger clearance -- and a claim
        # that records neither leaves the reader to assume the tier minimum for
        # the metal it is asking about. Measured: `code13`, `code14` and
        # `code3` each stood inside `vrefn`/`vrefp`'s 0.97 um band because
        # 0.24 um of daylight satisfied THEIR arithmetic; six M7 shorts on a
        # board the router called legal.
        #
        # ⛔⛔ AND `[lo, hi]` IS NOT THE METAL -- IT IS THE RESERVATION. A
        # claim runs PAD_ALONG/2 + one space past the wire it stands for
        # (`Route.claims` says so at length), and the wide-metal rule is
        # charged only where two shapes run PARALLEL for more than
        # `ca.WIDE_RULE[1]`. Asking that question of the padded interval
        # over-states the run by up to a pad at each end: measured on the
        # 144-net board, `eoc` and `topp` overlap by 0.240 um of METAL --
        # under the 0.400 threshold, so the deck charges the thin rule and
        # `ca.space` agrees -- while their CLAIMS overlap by 0.820 and the
        # padded reading refuses a track the deck allows. So the metal's own
        # along-extent rides with the claim. Absent, the claim IS the metal,
        # which is the conservative reading and what every caller that does
        # not supply one has always meant.
        self.occ.setdefault((t, k), []).append(
            (lo, hi, net, kind, co,
             self.rule[t][1] if sp is None else sp,
             self.rule[t][0] if w is None else w,
             lo if mlo is None else mlo,
             hi if mhi is None else mhi))

    def release(self, net):
        """Give back every ROUTE claim this net holds. -> how many dropped.

        ⛔ A NET THAT FAILS MUST NOT KEEP ITS RESOURCES. A multi-terminal net
        routes part of itself, claims it, and can still fail -- and the first
        version simply abandoned it, leaving those tracks held by a net that is
        not there. Routed went 61 -> 52 and the cause looked like congestion,
        which it was: congestion the router had manufactured.

        ⚠ SEED claims survive. A terminal's access pad is not part of any
        route; it is where the next attempt starts, and dropping it on rip-up
        would let another net take the one column that pin can leave on.
        """
        n = 0
        for key, v in list(self.occ.items()):
            keep = [c for c in v if not (c[2] == net and c[3] == ROUTE)]
            n += len(v) - len(keep)
            if len(keep) != len(v):
                self.occ[key] = keep
        return n

    def near(self, t, v, lo, hi, net, limit=64):
        """Track indices near coordinate `v` that are free over [lo, hi],
        nearest first. (Used by the greedy control.)"""
        k0 = self.index(t, v)
        out = []
        for d in range(limit):
            for k in ((k0,) if d == 0 else (k0 - d, k0 + d)):
                if self.free(t, k, lo, hi, net):
                    out.append(k)
        return out


# ---------------------------------------------------------------------------
# a route
# ---------------------------------------------------------------------------

def _land_taper(g, t, co, lo, hi, w, net, cuts=()):
    """Split one run into (lo, hi, width) spans, narrowed to the tier width
    wherever the wide metal would stand inside the via rule's halo of one of
    the NET'S OWN terminal climbs.

    ⭐ THE RULE IS THE DECK'S, NOT A PREFERENCE -- see LAND_TAPER. A climb is
    drawn by `glue_draw.climbs()` from the pads `pin_access` measured, one
    0.100 cut per rung, and `VIAn.R.4` charges that cut a second it cannot
    hold whenever a plate wider than VIA_WIDE stands within VIA_HALO of it.
    So the test is PROXIMITY, not endpoint identity: with the stub cap the
    wide trunk no longer ends AT the terminal -- it ends at a stub join, or
    passes beside its own landing one track over -- and an endpoint lookup
    that misses by 0.01 um leaves the plate in the halo. A run is narrowed
    over `along +- LAND_TAPER` of every own terminal whose across-distance
    puts the wide metal inside the halo; thin metal (< VIA_WIDE) is never a
    plate, whatever its distance.

    ⚠ OWN terminals only: D.6/E.5 measured every `VIAn.R.*` result on the
    widened net's own cuts and none across nets, so the rule is scoped to
    the connection and a net-blind taper would answer a question the deck
    does not ask.

    ⚠ A wide sliver shorter than PAD_ALONG between two thin windows (or
    against a run end) is drawn thin as well -- it would be all rule
    exposure and no ohms.

    ⛔ AND THE WINDOW IS THE CUT'S, NOT ONLY THE TERMINAL'S. The landing CUT
    stands wherever the off-grid stub put it -- up to MAX_STUB from the
    access point -- and LAND_TAPER's PAD_ALONG/2 allowance covers a bar, not
    a stub. Measured (sig14): `topp`'s landing cut sat 0.41 um past its
    terminal, the taper ended 0.63 um past the CUT against the deck's 0.8
    halo, and the deck answered `VIA5.R.4:M5 x1` at 551.95,149.47 -- the ONE
    result on a chip that was otherwise the signed baseline. So every own
    cut (`cuts`, the route's stacks) inside a terminal's window re-centres a
    window of its own. Cuts elsewhere on the net -- the mid-route climbs the
    via ARRAYS serve -- are untouched: a terminal window must reach a cut
    before the cut extends it, so the taper cannot creep down the trunk.
    """
    if w <= VIA_WIDE + 1e-9 or not TERM_OF:
        return [(lo, hi, w)]
    horiz = g.rule[t][4]
    wt = g.rule[t][0]
    win = []
    for (px, py), owner in TERM_OF.items():
        if owner != net:
            continue
        along, across = (px, py) if horiz else (py, px)
        if abs(across - co) - w / 2.0 >= VIA_HALO + CUT / 2.0 - 1e-9:
            continue                  # the wide edge never enters the halo
        if along < lo - LAND_TAPER or along > hi + LAND_TAPER:
            continue
        win.append((max(lo, along - LAND_TAPER),
                    min(hi, along + LAND_TAPER)))
    for (cx, cy) in cuts:
        along, across = (cx, cy) if horiz else (cy, cx)
        if abs(across - co) - w / 2.0 >= VIA_HALO + CUT / 2.0 - 1e-9:
            continue
        if not any(a - 1e-9 <= along <= b + 1e-9 for (a, b) in win):
            continue                  # only a terminal window recruits a cut
        win.append((max(lo, along - LAND_TAPER),
                    min(hi, along + LAND_TAPER)))
    if not win:
        return [(lo, hi, w)]
    win.sort()
    merged = [list(win[0])]
    for (a, b) in win[1:]:
        if a - merged[-1][1] < PAD_ALONG - 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, cur = [], lo
    for (a, b) in merged:
        if a - cur >= PAD_ALONG - 1e-9:
            out.append((cur, a, w))
        else:
            a = cur                   # the wide sliver joins the thin span
        out.append((a, b, wt))
        cur = b
    if hi - cur >= PAD_ALONG - 1e-9:
        out.append((cur, hi, w))
    elif hi > cur + 1e-9:
        a, b, _ww = out[-1]
        out[-1] = (a, hi, wt)
    return out


class Route:
    """One net's geometry, as the ONE description everything else derives from.

    ⛔ CLAIMS, SEGMENTS AND CUTS ARE DERIVED HERE AND NOWHERE ELSE. The greedy
    core built all three by hand at three call sites and they drifted -- the
    pads at the end of a run were claimed in one place and not in another,
    which is what the last M6 shorts were. A route is a list of RUNS and a list
    of STACKS; everything a gate or a draw pass wants is a function of the two.

    run   = (tier, track index, lo, hi, off)  off = the off-grid coordinate a
                                              terminal stub runs at, else None
    stack = (tier_a, tier_b, x, y)            a via ladder, one cut per rung
    """

    def __init__(self):
        self.runs = []
        self.stacks = []
        self.displaced = frozenset()

    def add_run(self, t, k, lo, hi, off=None):
        self.runs.append((t, k, round(min(lo, hi), 4), round(max(lo, hi), 4),
                          off))

    def add_stack(self, a, b, x, y):
        if a != b:
            self.stacks.append((min(a, b), max(a, b), round(x, 4),
                                round(y, 4)))

    # -- derivations ----------------------------------------------------
    def claims(self, g, net=None):
        """-> [(tier, k, lo, hi, co, sp)] every interval this route occupies: each
        run's extent plus PAD/2 at both ends -- on the tracks `straddle` names
        when the run is off-grid -- and every stack's pad on every tier it
        PASSES, on the tracks `pad_tracks` names. `co` is the metal's own
        across-coordinate, which is what makes a same-net claim answerable
        (`Tracks.merged_or_clear`)."""
        # ⛔ HALF A PAD PLUS ONE SPACE, not half a pad. A claim is what the
        # occupancy model hands out; the metal drawn inside it runs to the
        # half-pad, so two claims that merely fail to OVERLAP put two pads
        # edge to edge -- 0.000 um apart against a 0.100 rule. Nothing saw it
        # while the pad was 0.140 (the ends rarely met); at 0.380 it produced
        # a short between `dn0` and `up0` on M5 and nine spacing findings
        # against block metal, none of which the router could report because
        # in ITS model nothing overlapped.
        #
        # ⭐ AND AT A RUN END THAT IS A TERMINAL, THE PAD IS NOT THE ROUTER'S
        # WORST-CASE BAR -- IT IS THE ONE `pin_access` RECORDED, and claiming
        # 0.380 where 0.200 will be drawn eats 0.190 of a neighbour's legal
        # gap. Measured where it mattered: the abutted diode row puts five
        # 0.200 pads at 0.400 pitch -- legal metal, 0.200 gaps against a
        # 0.100 rule -- and `trim_cmp_0` was refused its own access column in
        # EVERY commit order because `trim_cmp_1`'s claim reached 0.290 past
        # a run end where the drawn pad stops at 0.100. `TERM_PADS` carries
        # the recorded half-extent per access point (glue_route fills it from
        # pin_access.json); an end that is not a terminal keeps the full
        # reservation, because there the router really does drop its own bar.
        out = []
        for (t, k, lo, hi, off) in self.runs:
            co = g.centre(t, k) if off is None else off
            # ⭐ THE BAND, NOT THE TRACK. A net the contract makes wide covers
            # the tracks its metal lands on, and claiming one of them is how a
            # 0.97 um wire used to be committed onto a board that believed it
            # was 0.140 -- legal in the model, six shorts in the geometry
            # (ROUTE_BUDGET D.5.4). `band` is 0 at the tier minimum, so an
            # ordinary net claims exactly what it always did.
            # ⭐ ONE RULE FOR BOTH: `run_w` says what this run carries and
            # `covers` says which tracks that metal is in the way of --
            # `straddle` was the minimum-width case of exactly this question.
            wn = g.run_w(t, net, lo, hi, off)
            ks = (straddle(g, t, off)
                  if off is not None and wn <= g.rule[t][0] + 1e-9
                  else tuple(g.covers(t, co, wn)))
            m = pad_along(t) / 2.0 + g.rule[t][1]
            m_lo, m_hi = m, m
            if t == BASE:
                h_lo = TERM_PADS.get((round(lo, 4), round(co, 4)))
                h_hi = TERM_PADS.get((round(hi, 4), round(co, 4)))
                if h_lo is not None:
                    m_lo = h_lo + g.rule[t][1]
                if h_hi is not None:
                    m_hi = h_hi + g.rule[t][1]
            sp = g.clear_for(t, wn)
            # ⚠ `(lo, hi)` RIDES ALONG AS THE METAL -- see `Tracks.claim`.
            # The margins above are the reservation; the wide rule's
            # parallel-run test is about the wire.
            for kk in ks:
                out.append((t, kk, lo - m_lo, hi + m_hi, co, sp, wn, lo, hi))
        for (a, b, x, y) in self.stacks:
            for ly in range(a, b + 1):
                horiz = g.rule[ly][4]
                v, q = (y, x) if horiz else (x, y)
                m = pad_along(ly) / 2.0 + g.rule[ly][1]
                # the pad's own ALONG half-extent -- the metal inside the
                # reservation, which is what the parallel-run test wants
                hm = pad_along(ly) / 2.0
                # the BASE pad of a landing stack is the terminal's RECORDED
                # pad too -- same reasoning as the run ends above
                if ly == BASE:
                    h = TERM_PADS.get((round(x, 4), round(y, 4)))
                    if h is not None:
                        m = h + g.rule[ly][1]
                        hm = h
                # ⚠️ AND THE CLAIM SAYS THE PAD'S WIDTH TOO. The last two
                # fields are what this metal DEMANDS of others and how wide
                # it is; filing the tier's wire width for a via pad tells
                # every later query to clear 0.050 um of metal that is
                # 0.160. Same coincidence as the query above, same fix.
                _pw = ca.via_pad(ly)[0]
                for kk in pad_tracks(g, ly, v):
                    out.append((ly, kk, q - m, q + m, v,
                                g.clear_for(ly, _pw), _pw, q - hm, q + hm))
        return out

    def segments(self, g, net=None):
        """-> [(x1, y1, x2, y2, tier)] the drawn wires, at the NET's width.

        ⭐ This is where the contract becomes geometry, and it is the whole
        point of the change: the width the maze routed to is the width that is
        drawn, so nothing downstream has to widen anything.
        """
        out = []
        for (t, k, lo, hi, off) in self.runs:
            # ⚠ A TERMINAL LEG (off-grid, MAX_STUB-capped) keeps the tier
            # width -- it is a couple of um onto a pin conductor 0.140 wide,
            # all cost and no ohms. Every ON-GRID run carries the contract's
            # width whatever its length: `run_w` decides, and `claims` uses
            # the same answer through the same function.
            w = g.run_w(t, net, lo, hi, off)
            co = g.centre(t, k) if off is None else off
            # the route's own stacks, so a landing cut that stands off its
            # terminal still re-centres the taper window (see _land_taper)
            for (a, b, ww) in _land_taper(g, t, co, lo, hi, w, net,
                                          cuts=[(x_, y_) for (_a, _b, x_, y_)
                                                in self.stacks]):
                x1, y1, x2, y2 = g.wire(t, k, a, b, off, ww)
                out.append((round(x1, 4), round(y1, 4), round(x2, 4),
                            round(y2, 4), t))
        # ⛔⛔ **AND A LANDING PAD WHEREVER A CUT STANDS ON METAL NARROWER
        # THAN THE PAD.** `wire_w`'s invariant is *"a track a via cannot land
        # on is not a routing track"*, and it held it by making EVERY run as
        # wide as a via pad. With `GRID_RULE_MIN` a run may be the tier's
        # minimum instead, and then the cut has no enclosure -- which is
        # exactly why forcing the minimum without this drew a board the
        # router called legal and routed 0 of 5 on.
        #
        # ⚠️ The CLAIM side already handled it: `pad_tracks` derives its
        # conflict set from `ca.via_pad`, not from the run width, so at a
        # pitch below the pad it claims three tracks where it claimed one.
        # What was missing is the GEOMETRY -- the model reserved the room
        # and nothing drew the metal in it.
        #
        # ⚠️ `via_pad` is (ACROSS, ALONG) and the two differ on every tier
        # here (0.160 x 0.180 on M6), so the rectangle is oriented by the
        # tier's own direction rather than assumed square.
        # ⚠️ Emitted as its own rectangle rather than spliced into the run's
        # spans: overlapping rectangles merge, and a splice would have to
        # re-derive `_land_taper`'s windows to avoid fighting them.
        for (a, b, x, y) in self.stacks:
            for ly in range(a, b + 1):
                if ly not in g.rule:
                    continue
                pw, pl = ca.via_pad(ly)
                if g.net_w(ly, net) >= pw - 1e-9:
                    continue            # the run already carries the pad
                if g.rule[ly][4]:       # horizontal: across is y, along x
                    r = (x - pl / 2.0, y - pw / 2.0,
                         x + pl / 2.0, y + pw / 2.0)
                else:                   # vertical: across is x, along y
                    r = (x - pw / 2.0, y - pl / 2.0,
                         x + pw / 2.0, y + pl / 2.0)
                out.append((round(r[0], 4), round(r[1], 4),
                            round(r[2], 4), round(r[3], 4), ly))
        return out

    def cuts(self):
        """-> [(metal tier, x, y)] one per RUNG, which is what `pairs_for` has
        been counting all along."""
        return [(t, x, y) for (a, b, x, y) in self.stacks
                for t in range(a, b)]

    def length(self):
        return sum(hi - lo for (_t, _k, lo, hi, _o) in self.runs)

    def commit(self, g, net):
        for (t, k, lo, hi, co, sp, w, mlo, mhi) in self.claims(g, net):
            g.claim(t, k, lo, hi, net, ROUTE, co, sp, w, mlo, mhi)

    def legal(self, g, net, anchors=(), spans=None):
        """Re-ask the occupancy model about every claim this route makes.

        ⛔ THE SEARCH IS NOT THE GATE. A soft search deliberately walks through
        other nets and displacement then removes them -- but "removed" is a
        claim about a data structure, and this is where that claim is checked
        against the structure itself rather than against the search's memory of
        it. -> (ok, blocking nets).

        ⚠ `anchors` are the net's own terminals, and WITHOUT THEM THIS GATE
        REJECTS EVERY ROUTE IT IS GIVEN. At an access point the obstacle map
        reports the block's own port metal as blocking (it carries no nets --
        see `Tracks.bounds`), so the landing run of any terminal on
        `qdi_tier`'s east edge is "illegal" by construction. Measured: 22
        evictions in one chip run, every one of them rolled straight back on a
        finding that was the pin itself, and `rst`, `samp_a` and `samp_t` --
        each of which routes alone in under a second -- stayed failed. A gate
        that cannot pass a correct answer is not a strict gate, it is a broken
        one. So a landing run is asked the question `bounds` asked: does it fit
        inside the ANCHORED window? Everything else is asked strictly.
        """
        # ⛔ THE QUESTION IS THE SEARCH'S OWN, RE-ASKED OF THE STRUCTURE. A
        # claim reserves PAD_ALONG/2 + one space past the metal it stands
        # for, so two legal neighbours can OVERLAP AS CLAIMS -- and the first
        # version of this gate measured claim intersection. Only the
        # displacement path asks this gate, so it taxed exactly the nets
        # that could only route BY REPAIR: `s_lvl` was refused for a claim
        # brush `audit_shorts` scores 0, and the trim churner for brushing
        # the SEED pads of the very siblings it had evicted. Each claim is
        # now tested the way the search tested it -- does the RUN the claim
        # stands for fit the window `bounds` gives at its coordinate?
        # Anchored where it holds this net's own terminal, strict elsewhere.
        # The margin mismatch WAS the gate's whole verdict on those nets.
        # ⚠️⚠️ **THE ANCHORED EXCEPTION IS PER-TERMINAL-TIER, NOT
        # PER-BASE.** This used to read `t == BASE`, which was the whole
        # truth at 65 nm (pin_access climbed every terminal to BASE) and
        # the third member of that assumption's family here (after the
        # search's start tier and its start window): an on-tier M6
        # terminal's landing run was judged STRICTLY against the map
        # that contains its own pin's surroundings -- the CDAC's
        # unpublished decode-bus lines 0.16 um off the pin line -- so
        # the route the maze found along the certified runway was
        # refused at commit ("blocked M6 k169", 2026-08-26, the tile's
        # 22-net class). An anchor is matched on ITS line in the
        # claim's own phase; the window is then the anchored one, which
        # is the question `bounds` answered when the search started
        # there.
        for (t, k, lo, hi, co, _sp, _w, _ml, _mh) in self.claims(g, net):
            m = pad_along(t) / 2.0 + g.rule[t][1]
            p_lo, p_hi = lo + m, hi - m
            ax, aspan = None, None
            if co is not None:
                horiz = g.rule[t][4]
                for (x, y) in anchors:
                    aco = y if horiz else x
                    aal = x if horiz else y
                    # ⚠️⚠️ **ON ITS LINE, AND A TERMINAL IS OFF-GRID BY
                    # CONSTRUCTION.** The comment above says the anchor is
                    # matched "on ITS line in the claim's own phase", and
                    # the test was `co` equality -- which is the same thing
                    # only when the terminal happens to sit ON a track
                    # centre. It does not: this router's own invariant is
                    # that *"a terminal's last run is at the PIN's own y,
                    # off-grid, straddling two tracks"*, and `claims` gives
                    # an ON-GRID run `co = centre(t, k)`. So the run down
                    # the terminal's own access column was never anchored,
                    # and was judged against the strict map that contains
                    # the pin's own surroundings.
                    #
                    # Measured (tsmc28 sub-ADC tile, 2026-08-26): `SAMP`'s
                    # claim `<blocked M5 k1033 28.13..44.17 co=103.30>` for
                    # a terminal at x 103.285 -- 0.015 um off a 0.100 um
                    # pitch, which IS track 1033 and nothing else.
                    #
                    # ▶ So the question is asked of the GRID, which is what
                    # decided `k` in the first place. The `co` equality is
                    # kept and OR'd, not replaced: an OFF-GRID run claims
                    # BOTH straddled tracks at one `co`, and only one of
                    # them is `index(aco)` -- dropping it would un-anchor
                    # the other half of every off-grid terminal run.
                    if (abs(aco - co) < 1e-4
                            or g.index(t, aco) == k) and                             lo - 1e-9 <= aal <= hi + 1e-9:
                        ax = aal
                        aspan = (spans or {}).get((x, y))
                        break
            p = ax if ax is not None else (p_lo + p_hi) / 2.0
            # ⚠️ ALONG the tier, and per tier: what a run claims past its
            # end is the pad's own long extent (0.110 on M5, 1.220 on M8).
            w = g.bounds(t, (k,), p, net, False, ca.via_pad(t)[1] / 2.0,
                         anchor=ax is not None, co=co)
            # ⚠️ THE RE-ASK IS THE SEARCH'S OWN QUESTION, and the search
            # was granted the terminal's certified RUNWAY (`start_span`).
            # Re-asking without it refuses at the gate the exact leg the
            # maze was told it may draw -- the anchored window resolved
            # and was still the collapsed point (w=51.37..51.37 against a
            # 0.9 um claim, 2026-08-26).
            if aspan is not None:
                w = (aspan if w is None else
                     (min(w[0], aspan[0]), max(w[1], aspan[1])))
            if (w is None or w[0] > p_lo + 1e-9 or p_hi > w[1] + 1e-9):
                # name the rectangle, not just the verdict -- a bare
                # "<obstacle>" cost three instrumentation round trips in one
                # session before anyone knew WHICH claim was refused
                return False, frozenset([
                    "<blocked %s k%d %.2f..%.2f co=%s w=%s>"
                    % (_name(t), k, lo, hi,
                       "-" if co is None else "%.2f" % co,
                       "-" if w is None else "%.2f..%.2f" % w)])
        # ⛔ AND THE STACKS ARE RE-ASKED TOO -- the same `_stack_ok` the maze
        # asked when it placed them, so the two can never drift. A route's
        # claims cover its pads AGAINST OTHER ROUTES; against the obstacle
        # map only `_stack_ok` measures the pad's true PAD_ALONG reach, and
        # a legality gate that skips it kept `mg1`'s rung 0.090 um from
        # `dac_core` REUSED through every incremental run after the reach
        # was fixed, because nothing ever re-asked the fixed question.
        for (a, b, x, y) in self.stacks:
            ok, _ns = _stack_ok(g, a, b, x, y, net, False)
            if not ok:
                return False, frozenset([
                    "<stack %s..%s at %.2f,%.2f>"
                    % (_name(a), _name(b), x, y)])
        return True, frozenset()


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------

#: ⛔ WHAT A VIA RUNG COSTS, DERIVED. A rung's pad sterilises three tracks over
#: one pad-length on EACH of the two tiers it joins (`Route.claims`), so it
#: costs 6 x PAD of track. That is the geometry's own answer, and it is the
#: per-route form of the ranking `pairs_for` already applies to bundles: vias
#: rank ahead of slack because a via level is a hard DRC and LVS surface.
#:
#: ⚠️⚠️ **AND "6 x PAD" IS TWO DIFFERENT PADS ONCE THE TIERS DIFFER.** A
#: `VIA5` rung sterilises 0.110 um of M5 and 0.180 of M6; a `VIA8` rung
#: sterilises 1.220 of each. Held as one scalar it is 0.660 with `BASE` on M5
#: and 3.120 with `BASE` on M7 -- the same ladder priced five times apart by
#: which tier happens to be lowest, which is not a property of the ladder.
#: `via_cost` is the sentence above, computed.
VIA_COST = None                    # computed by bind()


def via_cost(t1, t2):
    """What a ladder from tier `t1` to `t2` costs, in um of track."""
    lo, hi = sorted((int(t1), int(t2)))
    return sum(3.0 * (ca.via_pad(t)[1] + ca.via_pad(t + 1)[1])
               for t in range(lo, hi))

#: what a soft search pays to cross a net that would then have to move. Large
#: enough that any legal detour wins, finite so that "who is in the way" stays
#: answerable when no legal detour exists. In um-equivalent: one displacement
#: costs more than crossing the die twice.
SHARE_COST = 2000.0

#: turn candidates. A run may leave its track at ANY crossing of a
#: perpendicular tier, which over a 100 um window is 417 of them -- so an
#: expansion samples: where we came in, the window's two ends, the coordinate
#: aligned with the goal, and a geometric fan about the goal. Geometric rather
#: than uniform because congestion at a pin is LOCAL (the measured failure was
#: "no free lane within 2.4 um") while a detour that must leave the region
#: wants to leave it far: 1..128 tracks spans 0.24 to 30 um in fourteen steps.
TURN_STRIDES = (1, 2, 4, 8, 16, 32, 64, 128)

#: bound on A* pops per connection. A search that has not found a path in this
#: many states is reporting that the region is full, and DISPLACEMENT is the
#: answer to that, not a longer search -- measured on the greedy core, whose
#: 16x wider search bought exactly one net.
#:
#: ⭐ RE-MEASURED AT 2026-08-02, AND THE ANSWER CHANGED WITH THE GEOMETRY. Once
#: the pad became a 0.380 bar (see PAD_ALONG) every claim grew 0.240 um and the
#: die got tighter: at 2500 the solver routes **128**, at 12000 it routes
#: **131**. ⚠ And the old conclusion still holds for the OTHER knob -- raising
#: MAX_DEPTH to 10 and MAX_MOVES to 1200 on top of the wider search changes
#: nothing at all (131 either way), so the budget is load-bearing here and
#: displacement is not. A constant measured under one geometry is a measurement
#: OF THAT GEOMETRY.
MAX_EXPAND = 12000

#: how far outside the terminals' bounding box a route may wander, tried in
#: order. Escalating rather than fixed for the reason the stub allowance was
#: escalated: a route allowed to spread claims tracks over its whole length and
#: starves the nets behind it, so the tight box is tried first and only a net
#: that needs the room gets it.
MARGINS = (6.0, 30.0, 120.0)


class _St:
    """A search state: a track, the window we may run inside it, and where on
    that window we arrived."""
    __slots__ = ("t", "k", "off", "pos", "lo", "hi", "g", "f", "prev",
                 "via", "blk", "seq", "used")

    def __init__(self, t, k, off, pos, lo, hi, g, f, prev, via, blk, seq,
                 used=frozenset()):
        self.t, self.k, self.off, self.pos = t, k, off, pos
        self.lo, self.hi, self.g, self.f = lo, hi, g, f
        self.prev, self.via, self.blk, self.seq = prev, via, blk, seq
        #: ⛔ (tier, across-coordinate) OF EVERY PIECE THIS PATH HAS LAID.
        #: A route's own earlier runs are NOT in the occupancy model while it
        #: is being searched -- nothing is committed until the path is whole --
        #: so a net could and did lay two of its own pieces 0.090 um apart on
        #: M5 and pass every check the router made (`s_lvl`, `dn0`). The
        #: occupancy model catches this BETWEEN nets and cannot catch it
        #: within one. Carried per state, and it is the whole of the fix.
        self.used = used

    def __lt__(self, other):
        return (self.f, self.seq) < (other.f, other.seq)


class Goal:
    """Where a connection may end. Two kinds, and the distinction was paid for.

      LAND  an off-grid piece: a terminal's access pad, or another terminal's
            stub. Reached by dropping to BASE at that piece's OWN y and running
            to its x. Snapping that last stub to a track centre left 34 nets as
            4-shape islands that every spacing audit passed (`audit_opens`
            found them), which is why an off-grid goal is its own kind.
      RUN   an on-grid piece of this net that is already drawn. Reached by
            arriving on the same track and running into it: collinear same-tier
            same-net metal merges into one shape. This is what makes a 3- or
            4-terminal net a SEQUENCE of ordinary connections rather than a
            trunk with taps -- 12 of the greedy core's 71 failures were taps.
    """

    def __init__(self, kind, t, k, lo, hi, off=None, pin=False, at=None):
        self.kind, self.t, self.k = kind, t, k
        self.lo, self.hi, self.off = lo, hi, off
        #: a TERMINAL, not a piece of drawn route. Its pad is `pin_access`'s
        #: to vouch for, not the obstacle map's -- see Tracks.bounds(anchor).
        self.pin = pin
        #: ⛔⛔ **THE TERMINAL'S OWN COORDINATE ALONG THE TIER, WHICH IS WHERE
        #: THE ARRIVAL STUB HAS TO END.** For a pin goal `lo..hi` is the
        #: certified RUNWAY -- a lane a consumer measured FREE OF BLOCKERS,
        #: which is a licence to draw and not a claim that anything is drawn.
        #: `_reach` used it as both and clamped the arrival into it, so a
        #: drop column standing anywhere inside the lane gave `qx == lx` and
        #: a stub of ZERO length: the pin joined to nothing, while a
        #: `contact` check reading the same interval scored it 0.0000 um.
        #: Measured on spec2si-tsmc28's sub-ADC tile 2026-08-27: 42 of 65
        #: on-tier pins reached by no metal at all.
        #: ⚠️ Running to the conductor's near EDGE instead is not enough and
        #: was measured: the claim then stops short of the anchor, `legal`'s
        #: anchor test (`lo <= aal <= hi`) never matches, the certified span
        #: is never unioned into the window, and the route is refused at the
        #: gate -- 69 routed became 33 on that tile.
        #: ⚠️ `None` reduces to the old expression, and where no span was
        #: supplied `lo == hi == at`, so a caller that sets no `term_span`
        #: is byte-identical by construction rather than by observation.
        self.at = at


def _goal_point(g, gl):
    mid = (gl.lo + gl.hi) / 2.0
    if gl.kind == "tier":
        return (mid, mid)
    if gl.kind == "land":
        return _xy(g, gl.t, gl.off, mid)          # PHASE: across, along
    c = g.centre(gl.t, gl.k)
    return (mid, c) if g.rule[gl.t][4] else (c, mid)


def _goal_dist(g, gl, x, y):
    """Manhattan distance from (x, y) to a goal piece."""
    if gl.kind == "tier":
        # ⚠ ANY point on that tier will do, so the heuristic is 0 and the
        # search degenerates to Dijkstra. That is correct rather than lazy: a
        # CLIMB has no destination in x/y, only in z, and the admissible
        # heuristic for "get to M5" is zero. The box is what bounds it.
        return 0.0
    if gl.kind == "land":
        _ac = _across(g, gl.t, x, y)
        _al = _along(g, gl.t, x, y)
        return abs(_ac - gl.off) + max(0.0, gl.lo - _al, _al - gl.hi)
    c = g.centre(gl.t, gl.k)
    if g.rule[gl.t][4]:
        return abs(y - c) + max(0.0, gl.lo - x, x - gl.hi)
    return abs(x - c) + max(0.0, gl.lo - y, y - gl.hi)


def pad_tracks(g, ly, v):
    """The tracks a via PAD at across-coordinate `v` is in conflict with on
    tier `ly`. DERIVED, not a fixed three: a neighbour is in conflict when a
    wire on it would come within one space of the pad, |centre - v| <
    (PAD + w)/2 + space. For an ON-GRID pad -- one sitting on the track its own
    route runs on -- that is exactly one track, its own; for the off-grid pads
    a ladder drops through the tiers it merely PASSES, it is two. Claiming
    three unconditionally sterilised a track a full pitch away, which is
    legal."""
    k = g.index(ly, v)
    # ⚠️ THE PAD IS THIS TIER'S, not the whole route's -- see `wire_w`. A
    # ladder passing THROUGH M5 leaves a `VIA5` pad there and a `VIA7` pad on
    # M7, and charging M5 for the VIA7 sterilises tracks a pitch away.
    lim = (ca.via_pad(ly)[0] + g.rule[ly][0]) / 2.0 + g.rule[ly][1]
    return tuple(k + d for d in (-1, 0, 1)
                 if abs(g.centre(ly, k + d) - v) < lim - 1e-9)


def _stack_ok(g, a, b, x, y, net, soft):
    """Is a via ladder from tier `a` to tier `b` at (x, y) placeable?
    -> (ok, blocking nets).

    ⛔ THE MIDDLE OF A LADDER IS ON TIERS THE ROUTE DOES NOT OTHERWISE USE, and
    the first core claimed or checked none of them. A 3-rung M5->M8 stack puts
    a pad on M6 and M7 as it passes; those are neither the route's H tier nor
    its V tier, so the track model never heard of them. Measured: `code6_raw`
    climbed to M8 and its M6 pad landed inside `code5_raw`'s lane, which had
    been claimed correctly and checked correctly -- against the two tiers the
    router thought it was using.

    ⛔ AND THE PAD IS OFF-GRID ON THE TIERS IT MERELY PASSES. A lane's x is a
    track centre on ITS OWN tier; the pads the stack drops through sit at that
    same x, which is a track centre nowhere else. Anything within half a pad
    plus half a wire plus a space is in conflict -- one track either side.
    """
    if a == b:
        return True, frozenset()
    nets = set()
    for ly in range(min(a, b), max(a, b) + 1):
        horiz = g.rule[ly][4]
        v, p = (y, x) if horiz else (x, y)
        # ⛔ THE PAD IS A BAR AND THE QUERY MUST REACH AS FAR AS THE BAR.
        # `_cut_rects` draws every rung a PAD_ALONG bar along the tier's own
        # axis, and asking `blockers` about a PAD-sized square instead leaves
        # 0.12 um of drawn metal outside the question: D.5.5's `mg5` stood
        # 0.090 from `dac_core` against a 0.100 rule on a board the router
        # called legal, and `mg1` repeated it at 607.2,85.9 the first solve
        # after displacement pushed it there. (The ACROSS reach stays a PAD:
        # that is what `pad_tracks` prices, and it is what is drawn.)
        _pa = pad_along(ly)
        lo, hi = p - _pa / 2.0, p + _pa / 2.0
        # ⚠️ THE PAD'S WIDTH, not the net's -- `pad_tracks` already picks the
        # tracks from `ca.via_pad`, and asking about them at the WIRE's
        # width prices the clearance for metal that is not what is drawn.
        _pw = ca.via_pad(ly)[0]
        for k in pad_tracks(g, ly, v):
            hard, ns = g.blockers(ly, k, lo, hi, net, co=v, w=_pw)
            if hard:
                return False, frozenset()
            nets |= ns
    if nets and not soft:
        return False, frozenset(nets)
    return True, frozenset(nets)


def straddle(g, t, off):
    """The tracks an OFF-GRID wire at coordinate `off` is in conflict with.

    ⛔ IT IS TWO, NOT THREE, AND THE DIFFERENCE IS EVERY TERMINAL ON THE DIE.
    Derived: a neighbouring track is in conflict when a wire on it would come
    within one space of this one, i.e. |off - centre| < w/2 + w/2 + space --
    which on the thin tiers is 0.240, exactly one pitch. An off-grid wire sits
    at most half a pitch from its own centre, so it always conflicts with its
    own track and with the ONE it leans toward, and never with the other.

    Claiming three was the conservative guess, and conservative was wrong in
    the way that matters: the check is also what says whether a terminal can be
    REACHED, so the third track refused nets at their own pins -- `iref`,
    `vref_p`, `vrefp`, the whole `dn*` bus -- and, worse, made the goal
    unreachable so A* searched the region to exhaustion before saying so. A
    guess that only ever costs you is still a measurement you did not make.
    """
    k = g.index(t, off)
    d = off - g.centre(t, k)
    lim = g.rule[t][0] + g.rule[t][1]
    if d > 0 and abs(off - g.centre(t, k + 1)) < lim - 1e-9:
        return (k, k + 1)
    if d < 0 and abs(off - g.centre(t, k - 1)) < lim - 1e-9:
        return (k - 1, k)
    return (k,)


def _same_grid(g, t1, t2):
    """Do two tiers share a track grid exactly? Only then may a route change
    tier WITHOUT turning: the via lands at one point, and on the new tier the
    wire runs at that tier's own track centre. If the centres differ the pad
    does not reach the wire and the 'route' is two shapes that clear each other
    -- an open, not a connection. M5/M7 share (0.240 from 0.0); M6/M8 do not
    (0.240 against 0.800), so M6 -> M8 always turns.
    """
    return (abs(g.rule[t1][2] - g.rule[t2][2]) < 1e-9
            and abs(g.rule[t1][3] - g.rule[t2][3]) < 1e-9)


class Maze:
    """One connection: from a terminal to a Goal set, over all of ROUTE_TIERS.

    `soft` prices another net's metal at SHARE_COST instead of forbidding it,
    and the route that comes back carries the set of nets it crossed. That set
    IS the displacement list: the same A* that finds the path names who has to
    move, so the allocator never guesses and never re-derives.
    """

    def __init__(self, g, tiers=None):        # SEAM: was =ROUTE_TIERS
        tiers = ROUTE_TIERS if tiers is None else tiers
        self.g = g
        self.tiers = [t for t in tiers if t in g.rule]
        #: the tier this search's TERMINALS live on -- M5 for the chip router,
        #: because `pin_access` climbs to exactly there; M1 for a pin CLIMB,
        #: which is the same search run one stack lower (`pin_maze`). `min`
        #: rather than a constant, so the two cannot disagree.
        self.base = min(self.tiers) if self.tiers else BASE
        self.expanded = 0
        self.net, self.soft = None, False

    # -- helpers --------------------------------------------------------
    def _h(self, x, y, goals):
        return min(_goal_dist(self.g, gl, x, y) for gl in goals)

    def _self_clash(self, used, pieces):
        """Would any of `pieces` -- (tier, across-coordinate) this step is
        about to lay -- sit in the spacing band of metal this same path already
        laid? See `_St.used` and `Tracks.merged_or_clear`."""
        for (t, co) in pieces:
            for (t2, c2) in used:
                if t2 == t and not self.g.merged_or_clear(t, c2, co):
                    return True
        return False

    def _pieces(self, a, b, x, y):
        """The (tier, across-coordinate) a via ladder from `a` to `b` at
        (x, y) puts metal on -- pads included, because a pad is metal."""
        return tuple((ly, y if self.g.rule[ly][4] else x)
                     for ly in range(min(a, b), max(a, b) + 1))

    def _reach(self, st, gl):
        """Can state `st` finish at goal `gl`?
        -> (run, stack, stub, blockers) or None."""
        g = self.g
        if gl.kind == "tier":
            # Arrived. A zero-length run holds the tier; the metal is the pad
            # of the stack that got here, which `_records` draws.
            if st.t != gl.t or st.off is not None:
                return None
            return (st.t, st.k, st.pos, st.pos, None), None, None, frozenset()
        if gl.kind == "run":
            if gl.t != st.t or gl.k != st.k or st.off is not None:
                return None
            if gl.hi < st.lo - 1e-9 or gl.lo > st.hi + 1e-9:
                return None
            q = min(max(st.pos, gl.lo), gl.hi)
            q = min(max(q, st.lo), st.hi)
            blk = frozenset()
            if self.soft:
                _ph = ca.via_pad(st.t)[1] / 2.0
                lo, hi = (min(st.pos, q) - _ph, max(st.pos, q) + _ph)
                _hd, blk = g.blockers(st.t, st.k, lo, hi, self.net,
                                      co=g.centre(st.t, st.k))
            return (st.t, st.k, st.pos, q, None), None, None, blk
        # a LAND goal is off-grid on BASE: only a PERPENDICULAR column that
        # spans its across-coordinate can drop onto it.
        # ⚠️ PHASE: this read `if g.rule[st.t][4]` -- "if st.t is horizontal"
        # -- which is "same orientation as BASE" only while BASE is
        # horizontal, as it was at 65 nm. Compared against BASE now.
        # ⚠️⚠️ TIER: this read `self.base`, which is "the goal's tier"
        # only while every terminal is on BASE -- true at 65 nm by
        # construction, because `pin_access` climbed them all to M5. Here
        # 66 of 147 are reached ON THEIR OWN TIER and never touch M5. The
        # arrival belongs to the GOAL's tier, and `gl.t` IS `self.base`
        # for a 65 nm goal, so this reduces exactly.
        if g.rule[st.t][4] == g.rule[gl.t][4]:
            if st.t == gl.t and st.off is not None \
                    and abs(st.off - gl.off) < 1e-9:
                # the degenerate case: both pieces sit on the same off-grid
                # line, so one run joins them with no via at all.
                q = min(max(st.pos, gl.lo), gl.hi)
                if not (st.lo - 1e-9 <= q <= st.hi + 1e-9):
                    return None
                blk = frozenset()
                if self.soft:
                    _ph = ca.via_pad(st.t)[1] / 2.0
                    lo, hi = (min(st.pos, q) - _ph, max(st.pos, q) + _ph)
                    acc = set()
                    for k2 in straddle(g, st.t, st.off):
                        acc |= g.blockers(st.t, k2, lo, hi, self.net,
                                          co=st.off)[1]
                    blk = frozenset(acc)
                return (st.t, st.k, st.pos, q, st.off), None, None, blk
            return None
        if not (st.lo - 1e-9 <= gl.off <= st.hi + 1e-9):
            return None
        base = gl.t                  # TIER: the goal's, not the maze's
        lx = g.centre(st.t, st.k)
        # ⚠️ PHASE: `lx` is st.t's across-coordinate, which is BASE's ALONG
        # one, and `gl.off` is BASE's across. Both calls below take (x, y),
        # so the pair is assembled rather than written in the 65 nm order.
        _px, _py = _xy(g, base, gl.off, lx)
        if self._self_clash(st.used, self._pieces(base, st.t, _px, _py)
                            + ((base, gl.off),)):
            return None
        ok, ns = _stack_ok(g, base, st.t, _px, _py, self.net, self.soft)
        if not ok:
            return None
        ks = g.index(base, gl.off)
        kk = straddle(g, base, gl.off)
        w = g.bounds(base, kk, gl.lo if gl.pin else lx, self.net,
                     self.soft, ca.via_pad(base)[1] / 2.0, anchor=gl.pin,
                     co=gl.off)
        # ⚠️ A PIN GOAL'S SPAN IS THE CERTIFIED RUNWAY -- the same fact
        # the start window and the legality gate honour -- and `bounds`
        # cannot see it past a neighbour's unpublished bus line 0.16 um
        # off the pin (the 28 nm decode row, 2026-08-26: the second-leg
        # searches drained toward exactly these goals).
        if gl.pin:
            w = ((gl.lo, gl.hi) if w is None else
                 (min(w[0], gl.lo), max(w[1], gl.hi)))
        if w is None or not (w[0] - 1e-9 <= lx <= w[1] + 1e-9):
            return None
        # ⛔⛔ **A PIN GOAL'S STUB RUNS ONTO THE PIN.** This read
        # `min(max(lx, gl.lo), gl.hi)` -- clamp into the certified RUNWAY --
        # on the premise stated at the start window, *"the part beyond
        # `bounds` is the pin's own certified conductor"*. A runway is not
        # conductor: it is a lane measured free of BLOCKERS, and the
        # conductor merely lies inside it. So a drop column standing anywhere
        # in the lane produced `qx == lx`, a stub of zero length, and a
        # terminal joined to nothing.
        # ▶ The lane keeps its job -- it is what makes crossing to the pin
        # legal, and `legal()` unions it into the window for exactly that.
        # ⚠️ WHERE NO SPAN WAS SUPPLIED `lo == hi == at`, so both expressions
        # give `at` and the caller is unchanged BY CONSTRUCTION. That is the
        # only reason a corpus replay is allowed to be evidence here.
        if gl.pin and gl.at is not None:
            # ⛔⛔ **AND THE RISER MUST STAND INSIDE THE CERTIFIED LANE.**
            # `bounds` is probed HERE at `gl.lo` and by `legal` at the
            # ANCHOR, and on a pin's own tier those are two different
            # questions about one track: at the anchor the pin's own metal
            # and halo make it None, so the ONLY interval the gate will
            # honour is the span. A riser outside it produces a claim the
            # gate then refuses, which is a search that cannot be committed:
            #     HM[6] <blocked M6 k116 105.86..114.19 co=24.49
            #                                            w=110.05..118.05>
            # -- eight microns of stub against a four-micron licence, and
            # seven nets of one tile lost that way (2026-08-27). Claim only
            # what was certified.
            # ⚠️⚠️ **ONLY WHERE A SPAN WAS ACTUALLY SUPPLIED.** With no
            # `term_span` the goal carries `lo == hi == at` -- a degenerate
            # point that means "unspecified", not "a lane of zero width" --
            # and guarding against it demands the riser stand exactly on the
            # pin. The 65 nm corpus caught that within one replay, which is
            # the whole reason it is run: the `qx` change above IS identical
            # by construction there, and this one is not.
            if (gl.hi - gl.lo > 1e-9
                    and not (gl.lo - 1e-9 <= lx <= gl.hi + 1e-9)):
                return None
            qx = gl.at
        else:
            qx = min(max(lx, gl.lo), gl.hi)
        if not (w[0] - 1e-9 <= qx <= w[1] + 1e-9):
            return None
        # ⛔ THE STUB CAP, goal side -- see MAX_STUB. A drop column far from
        # the pin leaves a long off-grid stub behind it, which is the same
        # straddle-blind leg the start cap forbids.
        if abs(qx - lx) > MAX_STUB and \
                g.net_w(base, self.net) > g.rule[base][0] + 1e-9:
            return None
        blk = set(ns)
        if self.soft:
            _pb = ca.via_pad(base)[1] / 2.0
            _pt = ca.via_pad(st.t)[1] / 2.0
            lo, hi = min(lx, qx) - _pb, max(lx, qx) + _pb
            for k2 in kk:
                blk |= g.blockers(base, k2, lo, hi, self.net, co=gl.off)[1]
            lo2, hi2 = (min(st.pos, gl.off) - _pt,
                        max(st.pos, gl.off) + _pt)
            blk |= g.blockers(st.t, st.k, lo2, hi2, self.net,
                              co=g.centre(st.t, st.k))[1]
        return ((st.t, st.k, st.pos, gl.off, None),
                (base, st.t) + _xy(g, base, gl.off, lx),
                (base, ks, min(lx, qx), max(lx, qx), gl.off),
                frozenset(blk))

    def _turns(self, st, t2, goal_pt):
        """Track indices on tier `t2` this state may turn onto, in a fixed
        order. Deduped; the goal-aligned candidate comes first."""
        g = self.g
        pitch = g.rule[t2][2]
        horiz = g.rule[st.t][4]
        gp = goal_pt[0] if horiz else goal_pt[1]
        want = min(max(gp, st.lo), st.hi)
        seen, out = set(), []
        cands = [want, st.pos, st.lo, st.hi]
        far = abs(st.pos - want) > pitch
        for s in TURN_STRIDES:
            cands.append(want + s * pitch)
            cands.append(want - s * pitch)
            if far:
                cands.append(st.pos + s * pitch)
                cands.append(st.pos - s * pitch)
        for q in cands:
            if q < st.lo - 1e-9 or q > st.hi + 1e-9:
                continue
            k2 = g.index(t2, q)
            if k2 in seen or k2 < 0 or k2 >= g.ntracks(t2):
                continue
            c2 = g.centre(t2, k2)
            if c2 < st.lo - 1e-9 or c2 > st.hi + 1e-9:
                continue
            seen.add(k2)
            out.append(k2)
        return out

    # -- the search -----------------------------------------------------
    def search(self, net, start, goals, soft=False, box=None,
               tier_cost=None, start_tier=None, start_span=None):
        """-> (Route, blockers, why). `start` is (x, y) on BASE, off-grid: a
        terminal's access point. `box` bounds where the route may wander.

        `start_span` is the terminal's measured RUNWAY along its own
        line -- the same certified fact `_goals_for` already applies to
        the ANCHOR -- and it widens the start window past what `bounds`
        can see. The 28 nm tile measured why that matters: a decode-bus
        pin with neighbours at 1.2 um pitch sits inside THEIR tagged
        halos, `bounds` collapses its window to a POINT, and a point
        window admits no turn (a perpendicular track centre would have
        to sit exactly on the off-grid pin) -- 22 of 23 "no path within
        the search box" failures, at ONE expansion each, were this.
        Running along the pin's own conductor inside the runway is
        precisely what `pin_access` certified; None (the 65 nm corpus,
        every stack terminal) changes nothing."""
        g = self.g
        # ⚠️⚠️ `start_tier` DEFAULTS TO `self.base`, WHICH USED TO BE THE
        # ONLY OPTION. This docstring read "start is (x, y) on BASE" and
        # it held at 65 nm by construction. A terminal reached on its own
        # tier has NO metal on BASE, so a search started there finds
        # nothing and dies -- measured: SIX expansions for `MM`, against
        # 13354 when the same terminal is the anchor instead.
        base = self.base if start_tier is None else start_tier
        self.net, self.soft = net, soft
        counter = [0]
        openq, best = [], {}
        goal_pt = _goal_point(g, goals[0])

        ax, ay = start
        horiz0 = g.rule[base][4]
        a0 = ay if horiz0 else ax
        p0 = ax if horiz0 else ay
        kk = straddle(g, base, a0)
        ks = g.index(base, a0)
        w = g.bounds(base, kk, p0, net, soft, ca.via_pad(base)[1] / 2.0,
                     anchor=True, co=a0)
        if w is None and start_span is None:
            return None, frozenset(), ("the terminal's own access column is "
                                       "blocked on %s" % _name(base))
        if start_span is not None:
            # the union is contiguous (p0 lies in both), and the part
            # beyond `bounds` is the pin's own certified conductor
            w = (start_span if w is None else
                 (min(w[0], start_span[0]), max(w[1], start_span[1])))
        # ⛔ THE STUB CAP -- see MAX_STUB. A wide net's whole window here is
        # what let the maze run 70 um along the pin's own off-grid line with
        # straddle-blind queries; clipped to a stub, the leg draws thin and
        # the trunk must turn onto a track, where `bounds` sees the band.
        if g.net_w(base, net) > g.rule[base][0] + 1e-9:
            w = (max(w[0], p0 - MAX_STUB), min(w[1], p0 + MAX_STUB))
        if box is not None:
            b0, b1 = (box[0], box[2]) if horiz0 else (box[1], box[3])
            w = (max(w[0], min(b0, p0)), min(w[1], max(b1, p0)))
        s0 = _St(base, ks, a0, p0, w[0], w[1], 0.0,
                 self._h(ax, ay, goals), None, None, frozenset(), 0,
                 frozenset([(base, round(a0, 4))]))
        heapq.heappush(openq, s0)
        self.expanded = 0
        why = "no path within the search box"
        while openq:
            st = heapq.heappop(openq)
            key = (st.t, st.k, st.off is not None, round(st.lo, 3))
            if key in best and best[key] <= st.g + 1e-9:
                continue
            best[key] = st.g
            self.expanded += 1
            if self.expanded > MAX_EXPAND:
                why = "search budget of %d states exhausted" % MAX_EXPAND
                break
            for gl in goals:
                r = self._reach(st, gl)
                if r is None:
                    continue
                run, stack, stub, blk = r
                return self._build(st, run, stack, stub), (st.blk | blk), None
            for succ in self._succ(st, goals, goal_pt, box, tier_cost,
                                   counter):
                k2 = (succ.t, succ.k, succ.off is not None, round(succ.lo, 3))
                if k2 in best and best[k2] <= succ.g + 1e-9:
                    continue
                heapq.heappush(openq, succ)
        return None, frozenset(), why

    def _succ(self, st, goals, goal_pt, box, tier_cost, counter):
        g = self.g
        out = []
        horiz = g.rule[st.t][4]
        coord = g.centre(st.t, st.k) if st.off is None else st.off
        wc = 1.0 if tier_cost is None else tier_cost(st.t)
        for t2 in self.tiers:
            if t2 == st.t:
                continue
            if g.rule[t2][4] == horiz:
                # a tier change WITHOUT a turn: only where the grids coincide.
                if st.off is not None or not _same_grid(g, st.t, t2):
                    continue
                k2 = g.index(t2, coord)
                if abs(g.centre(t2, k2) - coord) > 1e-9:
                    continue
                want = min(max(goal_pt[0] if horiz else goal_pt[1],
                               st.lo), st.hi)
                for q in (st.pos, want):
                    s = self._step(st, t2, k2, q, coord, wc, box, goals,
                                   counter)
                    if s is not None:
                        out.append(s)
                continue
            for k2 in self._turns(st, t2, goal_pt):
                s = self._step(st, t2, k2, g.centre(t2, k2), coord, wc, box,
                               goals, counter)
                if s is not None:
                    out.append(s)
        return out

    def _step(self, st, t2, k2, q, coord, wc, box, goals, counter):
        """One expansion: run along `st`'s track from st.pos to q, then a via
        ladder to tier `t2`, arriving on track k2 at position `coord`."""
        g = self.g
        if q < st.lo - 1e-9 or q > st.hi + 1e-9:
            return None
        x, y = (q, coord) if g.rule[st.t][4] else (coord, q)
        if box is not None and not (box[0] - 1e-9 <= x <= box[2] + 1e-9
                                    and box[1] - 1e-9 <= y <= box[3] + 1e-9):
            return None
        # ⛔ WHERE WE ARRIVE DEPENDS ON WHICH KIND OF HOP THIS IS, and
        # getting that wrong is an OPEN, not a detour. A TURN onto a
        # perpendicular tier arrives at this track's across-coordinate; a
        # tier change that does NOT turn (M5 -> M7) arrives at the same point
        # on the same axis, so its position is `q`. Taking `coord` for both
        # put `vinn`'s M7 run at x 147.36 when the route was standing at
        # x 576.0 -- a legal, clear, correctly-claimed wire 429 um from the
        # via that was supposed to feed it. `audit_opens` found it; no
        # spacing check could have.
        same = g.rule[t2][4] == g.rule[st.t][4]
        arrive = q if same else coord
        # ⚠ CHEAPEST REJECT FIRST. `bounds` is one interval scan and
        # `_stack_ok` is up to twelve; asking them the other way round cost
        # ~2.5 ms per expansion and was most of the router's runtime.
        w = g.bounds(t2, (k2,), arrive, self.net, self.soft,
                     ca.via_pad(t2)[1] / 2.0, co=g.centre(t2, k2))
        if w is None:
            return None
        ok, ns = _stack_ok(g, st.t, t2, x, y, self.net, self.soft)
        if not ok:
            return None
        run_ns = frozenset()
        if self.soft:
            ks = (straddle(g, st.t, st.off) if st.off is not None
                  else (st.k,))
            _ph = ca.via_pad(st.t)[1] / 2.0
            lo, hi = min(st.pos, q) - _ph, max(st.pos, q) + _ph
            acc = set()
            rco = g.centre(st.t, st.k) if st.off is None else st.off
            for kk in ks:
                acc |= g.blockers(st.t, kk, lo, hi, self.net, co=rco)[1]
            run_ns = frozenset(acc)
        if box is not None:
            b0, b1 = (box[0], box[2]) if g.rule[t2][4] else (box[1], box[3])
            w = (max(w[0], b0), min(w[1], b1))
            if w[0] > arrive or w[1] < arrive:
                return None
        lay = self._pieces(st.t, t2, x, y) + ((t2, g.centre(t2, k2)),)
        if self._self_clash(st.used, lay):
            return None
        new = (ns | run_ns) - st.blk
        blk = st.blk | ns | run_ns
        cost = abs(q - st.pos) * wc + via_cost(st.t, t2) \
            + SHARE_COST * len(new)
        gg = st.g + cost
        nx, ny = ((arrive, g.centre(t2, k2)) if g.rule[t2][4]
                  else (g.centre(t2, k2), arrive))
        counter[0] += 1
        return _St(t2, k2, None, arrive, w[0], w[1], gg,
                   gg + self._h(nx, ny, goals), st,
                   (st.t, t2, x, y), blk, counter[0],
                   st.used | frozenset((t, round(c, 4)) for (t, c) in lay))

    def _build(self, st, last_run, stack, stub):
        """Walk the parent chain into a Route."""
        r = Route()
        chain, s = [], st
        while s is not None:
            chain.append(s)
            s = s.prev
        chain.reverse()
        for i, s in enumerate(chain[:-1]):
            a, b, vx, vy = chain[i + 1].via
            q = vx if self.g.rule[s.t][4] else vy
            r.add_run(s.t, s.k, s.pos, q, s.off)
            r.add_stack(a, b, vx, vy)
        t, k, p, q, off = last_run
        r.add_run(t, k, p, q, off)
        if stack is not None:
            r.add_stack(*stack)
        if stub is not None:
            bt, bk, x1, x2, sy = stub
            r.add_run(bt, bk, x1, x2, sy)
        return r


# ---------------------------------------------------------------------------
# the allocator
# ---------------------------------------------------------------------------

#: ⛔ INHERITED FROM `routing.SolverLanes`, NOT INVENTED. Its displacement
#: bounds were measured on the folded cascode (the deepest chain it ever needed
#: was 3, against a limit of 6) and the failure class is the same one: an early
#: claim with a wide choice squatting on a later request's only option.
#: Re-deriving a budget here would be a second answer to a settled question.
MAX_DEPTH = 6
MAX_MOVES = 400

#: how many times the still-failing nets are re-swept after the main pass.
#: Each sweep runs against a DIFFERENT track state -- the previous sweep's
#: displacements moved things -- so unlike the greedy core's retry, which
#: re-ran the same order against the same state and recovered exactly one net,
#: this one can converge. It stops as soon as a round places nothing.
SWEEPS = 3


class Allocator:
    """Every net, placed against one occupancy model, with rip-up.

    The greedy core's whole failure mode was that a net which could not be
    placed simply lost. Here it displaces: the soft search names the nets in
    its way, `Tracks.release` gives their tracks back, the net is placed, and
    each evicted net is re-placed recursively. Anything that cannot be made to
    work rolls the subtree back, so a failure never costs a route already good.
    """

    def __init__(self, g, terms, cls=None, tiers=None, deadline=None):
        tiers = ROUTE_TIERS if tiers is None else tiers   # SEAM
        self.g = g
        #: `run` sets it; `place` obeys it -- see place().
        self.deadline = deadline
        self.terms = terms            # net -> [(x, y)] access points
        #: `{(x, y): tier}` -- where each terminal's metal ACTUALLY is.
        #: Empty means "every terminal is on BASE", which is the 65 nm
        #: assumption and this file's exact previous behaviour.
        self.term_tier = {}
        #: ⚠️⚠️ `{(x, y): (lo, hi)}` -- how far a route may RUN ALONG a
        #: terminal's own conductor to reach it, in that tier's along-
        #: coordinate. Empty means the anchor goal is a POINT, which is what
        #: this file always built and is right for a terminal reached by a
        #: via STACK: the ladder drops at one spot. A terminal reached ON
        #: TIER is reached by running INTO it, and a point goal demands a
        #: track centre at exactly one coordinate -- measured, `bounds`
        #: returned the zero-width window (86.985, 86.985) for `MM/cdac/MM`
        #: while `pin_access` had proved 4.000 um of runway each way, and the
        #: arrival's last gate rejected 3892 times and passed ZERO. With the
        #: runway: 255 expansions and 0.3 s against 10442 and a failure.
        self.term_span = {}
        #: terminal coordinate -> the along-coordinate its arrival stub must
        #: END on. Present ONLY where `term_span` is a LANE -- a certified
        #: runway, free of blockers, which is a licence to draw and not a
        #: claim that anything is drawn there. Absent where the span IS
        #: metal: a port's own published rectangle, another terminal's drawn
        #: stub, every `run` goal, and every consumer that sets neither.
        #: ⚠️ The distinction is not one `Goal` can make for itself, and
        #: deriving it from the anchor cost a measured regression: a tile
        #: PORT sits ON the die boundary, so running to its centre drew M7
        #: at x -0.200 and `contact` refused the board outright.
        self.term_at = {}
        self.cls = cls or {}
        self.maze = Maze(g, tiers)
        self.routes = {}              # net -> Route
        self.failed = {}              # net -> why
        self.moves = 0
        self.displaced = 0
        self.expansions = 0

    # -- seeds ----------------------------------------------------------
    def seed(self):
        """⛔ EVERY TERMINAL'S ACCESS STACK IS AN OBSTACLE TO EVERY OTHER NET,
        and leaving them out is what let 28 shorts through on the first pass.
        The readout bundle is the clean example: `code0_raw`..`code15_raw` all
        leave `qdi_tier` at x 251.07 with 2.000 um of y between them, so a
        vertical lane drawn at that column runs straight down through fifteen
        other terminals' pads. The track model knew about the block metal and
        about this run's wires and not about the very points it routes FROM.

        Claimed as SEED, so a rip-up of the net's route leaves them standing.
        """
        n = 0
        for net, pts in sorted(self.terms.items()):
            for (x, y) in pts:
                for ly in (BASE, BASE + 1):
                    horiz = self.g.rule[ly][4]
                    v, p = (y, x) if horiz else (x, y)
                    # ⛔ THE SEED IS THE PAD THAT WILL BE DRAWN. `climbs()`
                    # draws the bar `pin_access` recorded -- up to
                    # PAD_ALONG/2 past the access point -- and a seed
                    # claiming PAD/2 leaves 0.12 um of drawn metal outside
                    # the model: measured, `trim_vref_3`'s route pad stood
                    # 0.090 from `trim_vref_2`'s climb against a 0.100 rule,
                    # on a board the router called legal. The recorded
                    # half-extent is the same number `Route.claims` already
                    # uses for a run-end at a terminal (TERM_PADS), and
                    # over-claiming a blanket PAD_ALONG/2 instead is the
                    # abutted-diode-row defect (a net refused its own access
                    # column by a neighbour's fiction).
                    # ⚠️ PER TIER, like every other pad reach -- see
                    # `wire_w`. The seed is claimed on BASE and BASE+1,
                    # which are two different via classes the moment the
                    # tile routes below M7.
                    h = ca.via_pad(ly)[1] / 2.0
                    if ly == BASE:
                        hr = TERM_PADS.get((round(x, 4), round(y, 4)))
                        if hr is not None:
                            h = max(h, hr)
                    for k in pad_tracks(self.g, ly, v):
                        self.g.claim(ly, k, p - h, p + h, net,
                                     SEED, v)
                    n += 1
        return n

    # -- one net --------------------------------------------------------
    def _order_terminals(self, pts):
        """The connection order: the two furthest apart first -- they span the
        net and fix its shape -- then the rest nearest-to-the-tree first, so a
        tap is short and lands on metal that already exists."""
        if len(pts) <= 2:
            return list(pts)
        best = max(((i, j) for i in range(len(pts))
                    for j in range(i + 1, len(pts))),
                   key=lambda ij: (abs(pts[ij[0]][0] - pts[ij[1]][0])
                                   + abs(pts[ij[0]][1] - pts[ij[1]][1])))
        seq = [pts[best[0]], pts[best[1]]]
        rest = [p for k, p in enumerate(pts) if k not in best]
        while rest:
            rest.sort(key=lambda p: min(abs(p[0] - q[0]) + abs(p[1] - q[1])
                                        for q in seq))
            seq.append(rest.pop(0))
        return seq

    def _goals_for(self, route, anchor):
        """The goal set a further terminal may join: every piece of this net
        that is already drawn, plus the anchor terminal itself."""
        # ⚠️ PHASE: across/along rather than y/x -- see the helpers at the
        # top. At 65 nm BASE was horizontal and this read (index by y, span
        # in x, off = y); here BASE is VERTICAL and that indexed 1754
        # x-tracks with a y.
        _k = (round(anchor[0], 4), round(anchor[1], 4))
        _t = self.term_tier.get(_k, BASE)
        _ac = _across(self.g, _t, anchor[0], anchor[1])
        _al = _along(self.g, _t, anchor[0], anchor[1])
        _lo, _hi = self.term_span.get(_k, (_al, _al))
        gl = [Goal("land", _t, self.g.index(_t, _ac),
                   _lo, _hi, off=_ac, pin=True,
                   at=self.term_at.get(_k))]
        if route is not None:
            for (t, k, lo, hi, off) in route.runs:
                if off is None:
                    gl.append(Goal("run", t, k, lo, hi))
                else:
                    gl.append(Goal("land", t, k, lo, hi, off=off))
        return gl

    def _box(self, pts, margin):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0, x1, y1 = self.g.span
        return (max(x0, min(xs) - margin), max(y0, min(ys) - margin),
                min(x1, max(xs) + margin), min(y1, max(ys) + margin))

    def _route(self, net, soft, margins=MARGINS):
        """Route one whole net against the current track state.
        -> (Route, blockers, why). Commits as it goes; the caller releases.

        ⛔ THE DEADLINE IS ASKED HERE TOO, per margin. `place` checks it on
        entry, which bounds the RECURSION -- and the expensive part is not the
        recursion, it is this: three margins x every terminal x a soft pass and
        a hard one, each of them an A* whose every step now asks about a band.
        A 0.97 um net spent 25 minutes inside one call with a 600 s deadline
        that no loop containing it ever came back to test.
        """
        if self.deadline is not None:
            import time
            if time.monotonic() > self.deadline:
                return None, frozenset(), "deadline reached"
        pts = self._order_terminals(self.terms[net])
        tc = self._tier_cost(net)
        last = "no attempt"
        for margin in margins:
            if self.deadline is not None and \
                    __import__("time").monotonic() > self.deadline:
                self.g.release(net)
                return None, frozenset(), "deadline reached"
            self.g.release(net)
            box = self._box(pts, margin)
            whole, blk, ok = Route(), set(), True
            for i in range(1, len(pts)):
                goals = self._goals_for(whole if whole.runs else None, pts[0])
                _pk = (round(pts[i][0], 4), round(pts[i][1], 4))
                _st = self.term_tier.get(_pk)
                r, b, why = self.maze.search(net, pts[i], goals, soft=soft,
                                             box=box, tier_cost=tc,
                                             start_tier=_st,
                                             start_span=self.term_span.get(
                                                 _pk))
                self.expansions += self.maze.expanded
                if r is None:
                    last = "terminal %d of %d: %s" % (i + 1, len(pts), why)
                    ok = False
                    break
                r.commit(self.g, net)
                whole.runs += r.runs
                whole.stacks += r.stacks
                blk |= b
            if ok:
                whole.displaced = frozenset(blk) - {net}
                return whole, whole.displaced, None
        self.g.release(net)
        return None, frozenset(), last

    def _tier_cost(self, net):
        """Per-tier wire cost for one net: 1.0 everywhere unless a class asks
        otherwise -- and no class does yet. It is a HOOK, not a constant.
        `net_classes.json` argues tier preference in prose ("aggressors get
        first pick of the best PAIR available over their own run") and the one
        MEASURED fact about tier preference on this die is that it is not
        monotonic: M9, the highest, cannot carry the readout bundle at all. A
        preference that is asserted rather than measured belongs behind a hook
        that says so, not inside the cost function."""
        return None

    # -- placement with displacement ------------------------------------
    def _snapshot(self):
        return dict(self.routes)

    def _restore(self, snap):
        for net in set(snap) | set(self.routes):
            cur, was = self.routes.get(net), snap.get(net)
            if cur is was:
                continue
            self.g.release(net)
            if was is not None:
                was.commit(self.g, net)
                self.routes[net] = was
            else:
                self.routes.pop(net, None)

    def place(self, net, frozen=frozenset(), depth=None, budget=None):
        """Place one net, displacing others if it must. -> (ok, why).

        ⚠ `depth` and `budget` resolve from the module constants HERE, not in
        the signature: a default argument is bound once at definition, so a
        negative control that withholds displacement by setting MAX_DEPTH to 0
        would have changed nothing and reported a pass.

        ⛔ AND THE DEADLINE IS CHECKED HERE, NOT ONLY BETWEEN NETS. `run` tests
        it per net, which is exact while every net costs seconds -- and the
        electrical contract broke that premise too. A 0.97 um net asks about
        five tracks per query and finds almost nothing legal, so its search
        explores the whole reachable set before failing and then does it again
        for every displacement candidate: measured, ONE net ran 37 minutes
        against a 600 s deadline that could not be reached because the loop
        containing it had not come back. A budget that only applies between
        the expensive things is not a budget.
        """
        depth = MAX_DEPTH if depth is None else depth
        budget = [MAX_MOVES] if budget is None else budget
        if self.deadline is not None:
            import time
            if time.monotonic() > self.deadline:
                return False, "deadline reached inside placement"
        r, _b, why = self._route(net, soft=False)
        if r is not None:
            # ⛔ THE SEARCH IS NOT THE GATE -- HERE TOO. The displacement path
            # has re-asked `Route.legal` since the day it existed (line below:
            # "route illegal after eviction") and this path trusted the maze:
            # so a route the stepping queries under-measured was committed
            # with no question asked, and the board ended with four wide nets
            # mutually illegal while every per-net verdict was "ok". One
            # acceptance criterion, both paths.
            ok0, bad0 = r.legal(self.g, net, self.terms[net],
                                spans=self.term_span)
            if ok0:
                self.routes[net] = r
                return True, None
            self.g.release(net)
            why = ("route illegal as searched: %s"
                   % ", ".join(sorted(bad0))[:60])
        if depth <= 0:
            return False, why
        # ⭐ THE SOFT SEARCH IS THE BLOCKER QUERY. It prices other nets instead
        # of obeying them, so the path it returns is the cheapest one that
        # WOULD be legal -- and the nets it crossed are exactly the ones that
        # have to move. Nothing else enumerates candidates and nothing guesses.
        r, blk, why2 = self._route(net, soft=True)
        if r is None:
            self.g.release(net)
            return False, why2 or why
        blk = frozenset(blk) - {net}
        # ⭐ ONE FROZEN EVICTION, TOP LEVEL ONLY, SOLE BLOCKER ONLY. The
        # freeze exists so the maze cannot re-open the contention the bundle
        # construction closed -- but a blanket refusal also refuses the case
        # the displacement machinery was BUILT for: `mg2` fails with exactly
        # one net in its way (`mg1`, frozen), and place() already carries the
        # proof obligation a freeze wants -- the evicted net must RE-PLACE or
        # the whole subtree rolls back. So a single frozen blocker may be
        # evicted from a top-level attempt; the recursion stays strict, so a
        # frozen net can never be evicted to make room for re-placing
        # another eviction.
        top = depth == MAX_DEPTH
        if not blk or len(blk) > budget[0] or (
                (blk & frozen) and not (top and len(blk) == 1)):
            self.g.release(net)
            return False, ("%s; displacing %d net(s) is %s"
                           % (why, len(blk),
                              ("circular (frozen: %s)"
                               % ",".join(sorted(blk & frozen)))
                              if blk & frozen else
                              "over budget" if blk else "not the problem"))
        snap = self._snapshot()
        self.g.release(net)
        for b in sorted(blk):
            self.g.release(b)
            self.routes.pop(b, None)
        budget[0] -= len(blk)
        self.moves += len(blk)
        r2, _b2, why3 = self._route(net, soft=False)
        if r2 is None:
            self._restore(snap)
            return False, "%s (displacement did not help: %s)" % (why, why3)
        ok2, bad = r2.legal(self.g, net, self.terms[net],
                            spans=self.term_span)
        if not ok2:
            # ⛔ THE SEARCH IS NOT THE GATE. Re-asked of the structure itself.
            self.g.release(net)
            self._restore(snap)
            return False, "%s (route illegal after eviction: %s)" % (
                why, ", ".join(sorted(bad))[:60])
        self.routes[net] = r2
        self.displaced += len(blk)
        for b in sorted(blk):
            if self.deadline is not None and \
                    __import__("time").monotonic() > self.deadline:
                self._restore(snap)
                return False, "%s (deadline reached mid-eviction)" % why
            ok, _w = self.place(b, frozen | {net}, depth - 1, budget)
            if not ok:
                self._restore(snap)
                return False, "%s (evicted %s could not be re-placed)" % (
                    why, b)
        return True, None

    # -- the whole chip -------------------------------------------------
    def run(self, order, log=None, frozen=frozenset(), deadline=None):
        """`frozen` nets may not be displaced -- the bundle planner's routes
        are constructed against resources no other net was promised, and a
        maze that could evict one would re-open the exact contention the
        construction closed. The soft search still SEES them (they price like
        any other net); `place` just refuses the eviction as circular.

        ⛔ AND THE RUN IS BOUNDED IN WALL CLOCK, NOT ONLY PER NET. Every single
        attempt already terminates -- `MAX_EXPAND` states, `MAX_MOVES`
        displacements, `SWEEPS` rounds -- but the SUM of 136 of them is not a
        number anyone was told, and a run that took 25 minutes could only be
        watched, not predicted. `deadline` (a `time.monotonic()` value) stops
        the search cleanly: nets not yet attempted are reported as
        `deadline reached` alongside the ones that failed on their own merits,
        so a timed-out run yields a partial answer AND names what it skipped
        rather than being killed from outside with nothing to show.

        ⚠ It reports per net as it goes (`log`), because a search whose only
        output is the final tally is indistinguishable from a hang -- which is
        exactly how this one was read, correctly, as unacceptable.
        """
        import time
        t0 = time.monotonic()
        self.deadline = deadline
        n_all = sum(1 for n in order if n in self.terms)
        i = 0
        for net in order:
            if net not in self.terms:
                continue
            i += 1
            if deadline is not None and time.monotonic() > deadline:
                self.failed[net] = ("deadline reached (%.0f s) before this "
                                    "net was attempted" % (time.monotonic()
                                                           - t0))
                continue
            ok, why = self.place(net, frozen)
            if not ok:
                self.failed[net] = why
                self.g.release(net)
            if log and (i % 10 == 0 or i == n_all):
                log("    ... %d/%d net(s) attempted, %d failed, %.0f s"
                    % (i, n_all, len(self.failed), time.monotonic() - t0))
        for rnd in range(SWEEPS):
            again = sorted(self.failed)
            if not again:
                break
            if deadline is not None and time.monotonic() > deadline:
                if log:
                    log("  deadline reached (%.0f s) -- %d net(s) left "
                        "unretried" % (time.monotonic() - t0, len(again)))
                break
            got = 0
            for net in again:
                if deadline is not None and time.monotonic() > deadline:
                    break
                ok, why = self.place(net, frozen)
                if ok:
                    del self.failed[net]
                    got += 1
                else:
                    self.failed[net] = why
                    self.g.release(net)
            if log:
                log("  sweep %d: +%d net(s), %d displacement(s) so far, %.0f s"
                    % (rnd + 1, got, self.displaced, time.monotonic() - t0))
            if not got:
                break
        return self.routes, self.failed
