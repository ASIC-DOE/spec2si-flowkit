#!/usr/bin/env python3
"""Gates for routekit.solve's bind seam. The real gates are consumer-side
(the tracks digest, the corpus replays); what is judged here is the seam:
unbound use refuses, bind computes the node constants exactly, and the
reductions the tile port documented hold arithmetically."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir))
import pytest                                              # noqa: E402

from routekit import solve                                 # noqa: E402

#: ⚠️⚠️ bind() SETS LIVE MODULE STATE, AND A TEST THAT LEAVES A STUB BOUND
#: POISONS EVERY LATER CONSUMER IN THE SAME PROCESS. Measured: with this
#: file collected before a consumer's tracks test, the stub's pitches
#: replaced the process's and four tracks assertions failed -- and the
#: reverse order passed, which is the worst kind of green. Every test here
#: restores the pre-test bind, so suite order cannot matter.
_BOUND = ("ca", "bd", "BASE", "_TILE_VIA", "PAD", "CUT", "PAD_ALONG",
          "LAND_TAPER", "VIA_COST", "ROUTE_TIERS", "HERE")


@pytest.fixture(autouse=True)
def _restore_bind():
    saved = dict((k, getattr(solve, k)) for k in _BOUND)
    yield
    for k, v in saved.items():
        setattr(solve, k, v)


class StubCA(object):
    ROUTING_TIERS = (35, 36, 37, 38)
    BASE_LY = 31
    TIER_RULE = {35: (0.1, 0.1), 36: (0.1, 0.1), 37: (0.1, 0.1),
                 38: (0.4, 0.4)}
    TIER_AXIS = {35: "V", 36: "H", 37: "V", 38: "H"}

    def via_pad(self, t):
        return {35: (0.14, 0.11), 36: (0.14, 0.18), 37: (0.52, 0.52),
                38: (0.52, 1.22)}[t]

    #: the rest of the ten-symbol seam a `Tracks` needs -- empty answers, so
    #: the grid it builds is a bare one and every query is about nothing
    WIDE_RULE = (1.0, 1.0, 0.4)

    def declared_boxes(self, *a, **k):
        return set()

    def rects(self, snap, t):
        return ()

    def num(self, n):
        return {"M5": 35, "M6": 36, "M7": 37, "M8": 38}[n]

    def _name(self, t):
        return "M%d" % (t - 30)

    def space_between(self, t, wa, wb=0.0, run_um=None):
        return self.TIER_RULE[t][1]

    def min_space(self, t):
        return self.TIER_RULE[t][1]

    def min_width(self, t):
        return self.TIER_RULE[t][0]


class StubBD(object):
    VIA = {"VIA5": (0, 0.1, 0.02, 0.25, 0),
           "VIA7": (0, 0.36, 0.08, 0.9, 40)}


def test_bind_requires_the_tier_list():
    try:
        solve.bind(StubCA(), StubBD())
    except ValueError as e:
        assert "route_tiers" in str(e)
    else:
        raise AssertionError("bind without route_tiers did not refuse")


def test_bind_computes_the_node_constants():
    solve.bind(StubCA(), StubBD(), route_tiers=(35, 36, 37, 38),
               pad_via="VIA5")
    assert solve.BASE == 35
    assert solve.PAD == 0.14 and solve.CUT == 0.1
    assert solve.LAND_TAPER == round(0.8 + 0.05 + 0.19, 4)
    assert solve.VIA_COST == round(6 * 0.14, 4)
    assert solve.ROUTE_TIERS == (35, 36, 37, 38)


def test_per_tier_pads_answer_through_the_adapter():
    solve.bind(StubCA(), StubBD(), route_tiers=(35, 36, 37, 38),
               pad_via="VIA5")
    assert solve.wire_w(38) == 0.52          # max(rule 0.4, via_pad 0.52)
    assert solve.pad_along(36) == 0.18


def test_via_cost_reduces_to_the_flat_scalar_when_pads_are_flat():
    class FlatCA(StubCA):
        def via_pad(self, t):
            return (0.14, 0.14)
    solve.bind(FlatCA(), StubBD(), route_tiers=(35, 36, 37),
               pad_via="VIA5")
    # the 65 nm cost was VIA_COST * |dt| with VIA_COST = 6 * PAD; the
    # generalized via_cost must reduce to it exactly when every pad is flat
    for dt in (1, 2, 3):
        assert abs(solve.via_cost(35, 35 + dt)
                   - solve.VIA_COST * dt) < 1e-12


def test_every_occupancy_QUERY_is_callable_on_a_real_grid():
    """`free`, `blockers` and `bounds` answer, on a Tracks built here.

    ⚠️⚠️ **THIS EXISTS BECAUSE A BROKEN `free` PASSED EVERY GATE.** A patch
    meant for `blockers` landed in `free` instead -- the two share the line
    `w = self._ask_w(t, net, co)` and a first-occurrence replace took the
    wrong one -- leaving `w = ... if w is None else w` in a function with no
    `w` parameter. That is an `UnboundLocalError` on EVERY call, and it
    survived the 98 tests here AND the signed 136-net corpus replay, because
    neither of them ever calls `free`.

    ▶ So the gate is not "is the router right", which the corpus answers.
    It is "does each query still RUN" -- the cheapest possible check, and
    the one whose absence let an exception-on-every-call ship.
    """
    saved_tiers = solve.ROUTE_TIERS
    solve.bind(StubCA(), StubBD(), route_tiers=(35, 36, 37, 38))
    g = solve.Tracks({"tile": (10.0, 10.0), "rects": {}},
                     span=(0.0, 0.0, 10.0, 10.0), pg={})
    assert solve.ROUTE_TIERS == (35, 36, 37, 38) or saved_tiers is not None
    for t in (35, 36, 37, 38):
        k = g.index(t, 5.0)
        assert isinstance(g.free(t, k, 1.0, 2.0, "n"), bool)
        hard, nets = g.blockers(t, k, 1.0, 2.0, "n")
        assert isinstance(hard, bool)
        # the pad-width form: a caller stating the metal it is asking about
        hard2, _ = g.blockers(t, k, 1.0, 2.0, "n", w=solve.ca.via_pad(t)[0])
        assert isinstance(hard2, bool)
        w = g.bounds(t, (k,), 1.5, "n", False, 0.05)
        assert w is None or (isinstance(w, tuple) and len(w) == 2)


def test_a_pin_goals_stub_runs_ONTO_THE_PIN_not_into_the_lane():
    """`_reach` ends a pin goal's stub at `Goal.at`, the terminal itself.

    ⛔⛔ **THIS IS THE GATE FOR A CHANGE THE CORPUS CANNOT SEE.** Where a
    caller sets no `term_span`, `lo == hi == at` and both expressions give
    the same number, so a replay is byte-identical BY CONSTRUCTION -- which
    makes it a control that proves nothing, the exact shape this project has
    been caught by three times. What has to be shown is that the two
    branches DIFFER once a span IS supplied.

    The defect: a pin goal's `lo..hi` is the certified RUNWAY, a lane
    measured free of blockers. `_reach` clamped the arrival into it, so a
    drop column standing anywhere inside the lane gave `qx == lx` and a stub
    of ZERO length -- the pin joined to nothing, while `contact()` reported
    a gap of 0.0000 um because it reads the same interval. Measured on
    spec2si-tsmc28's sub-ADC tile 2026-08-27: 42 of 65 on-tier pins reached
    by no metal.

    ⚠️ AND THE CONDUCTOR'S NEAR EDGE IS NOT THE ANSWER EITHER. Stopping
    there leaves the claim short of the anchor, so `legal`'s anchor test
    never matches, the span is never unioned into the window, and the route
    is refused at the gate: 69 routed became 33 on that tile. The stub has
    to arrive ON the terminal.
    """
    solve.bind(StubCA(), StubBD(), route_tiers=(35, 36, 37, 38))
    g = solve.Tracks({"tile": (10.0, 10.0), "rects": {}},
                     span=(0.0, 0.0, 10.0, 10.0), pg={})
    base, st_t = 36, 37                  # M6 horizontal, M7 vertical
    assert g.horiz(base) and not g.horiz(st_t)

    off = g.centre(base, g.index(base, 5.0))       # the pin's own y
    lx = g.centre(st_t, g.index(st_t, 5.0))        # the riser's x
    maze = solve.Maze(g, (35, 36, 37, 38))
    maze.net, maze.soft = "n", False

    def stub_for(lo, hi, at):
        gl = solve.Goal("land", base, g.index(base, off), lo, hi,
                        off=off, pin=True, at=at)
        st = solve._St(st_t, g.index(st_t, lx), None, off,
                       off - 3.0, off + 3.0, 0.0, 0.0, None, None,
                       frozenset(), 0, frozenset())
        r = maze._reach(st, gl)
        assert r is not None, "the goal must be reachable on a bare grid"
        _run, _stack, stub, _blk = r
        return stub[3] - stub[2]

    # NO SPAN: lo == hi == at, and the old expression and the new one are
    # the same number. This is the property every existing consumer relies
    # on, so it is asserted rather than assumed.
    assert abs(stub_for(lx, lx, lx)) < 1e-9
    # ...INCLUDING when the riser stands well away from the terminal. A
    # point span means "unspecified", not "a lane of zero width", so the
    # lane guard must not fire on it -- the 65 nm corpus found exactly this
    # regression when the guard was written without the width test.
    far = lx + 2.6
    assert abs(stub_for(far, far, far) - 2.6) < 1e-9, (
        "a caller that supplies no span must be unaffected by the lane guard")

    # A SPAN, and the riser standing 2.6 um along it from the terminal.
    # The old expression clamped into the lane and drew nothing.
    at = lx + 2.6
    assert abs(stub_for(lx - 3.0, lx + 3.0, at) - 2.6) < 1e-9, (
        "the stub must run from the riser onto the terminal")

    # and a terminal on the other side of the riser, so the sign is tested
    at = lx - 1.4
    assert abs(stub_for(lx - 3.0, lx + 3.0, at) - 1.4) < 1e-9


class WideCA(StubCA):
    """StubCA with the 65 nm deck's wide-metal rule -- a shape over 0.400 um
    running parallel past 0.400 um owes 0.160, not the tier minimum. The base
    stub's (1.0, 1.0, 0.4) makes nothing on this grid wide at all."""

    WIDE_RULE = (0.400, 0.400, 0.160)


def _wide_grid():
    """A bare 3-tier grid whose tier 36 is pitched like the 65 nm chip's M6:
    0.140 wire, 0.100 space, 0.240 pitch -- the numbers the finding below was
    measured at."""
    solve.bind(WideCA(), StubBD(), route_tiers=(35, 36, 37, 38))
    g = solve.Tracks({"tile": (400.0, 400.0), "rects": {}},
                     span=(0.0, 0.0, 400.0, 400.0), pg={},
                     widths={"wide": 0.992})
    for t in (35, 36, 37, 38):
        w, s, p, c0, h, n = g.rule[t]
        g.rule[t] = (0.140, 0.100, 0.240, c0, h, n)
    g._band = {}
    return g


def test_the_daylight_verdict_does_not_depend_on_WHO_ASKS():
    """A pair either clears or it does not; the COMMIT ORDER may not decide.

    ⛔⛔ **THIS IS THE GATE THAT DID NOT EXIST, AND ITS ABSENCE PUT TWO
    WIDE-METAL VIOLATIONS ON A BOARD THE ROUTER CALLED LEGAL.** `claim` files
    `sp` -- the clearance THAT metal demands of others -- and the three
    daylight tests (`_free1`, `_blockers1`, `bounds`) each built their margin
    from the ASKER's own width and clearance, adding only the other's
    half-width. So a 0.992 um net asking about a 0.140 um neighbour demanded
    0.7260 and refused, while the SAME PAIR asked the other way demanded
    0.6660 and allowed -- and a chip is committed in one order. Measured on
    the 144-net v6 solve: `WIDE M6 topp vs vcm` and `WIDE M7 dn7 vs topp`,
    both exactly 0.0060 um short, both invisible to `audits 0/0/0` because
    `Route.legal` re-asks `bounds`, which is one of the three.

    ⚠ 3 tracks = 0.7200 um; the pair owes 0.496 + 0.070 + 0.160 = 0.7260.
    """
    for first, second in (("wide", "thin"), ("thin", "wide")):
        g = _wide_grid()
        k0 = 100
        c0 = g.centre(36, k0)
        wf = g.net_w(36, first)
        for kk in g.covers(36, c0, wf):
            g.claim(36, kk, 10.0, 40.0, first, co=c0,
                    sp=g.clear_for(36, wf), w=wf)
        # the neighbour, three tracks away, running beside it for 30 um
        assert not g.free(36, k0 + 3, 12.0, 38.0, second), (
            "%s committed first: %s at 3 tracks (0.7200 um) was allowed, "
            "and the pair owes 0.7260" % (first, second))


def test_a_wide_claim_cannot_be_WALKED_AROUND_IN_SMALL_STEPS():
    """The parallel-run test is the CLAIM'S OWN METAL LENGTH, never its
    overlap with the query.

    ⛔⛔ **THIS IS THE GATE THE FIRST VERSION OF THE FIX NEEDED AND DID NOT
    HAVE, AND WITHOUT IT THE FIX CHANGED NOTHING.** The wide rule is charged
    past `WIDE_RULE[1]` of parallel run, so measuring the overlap looks like
    the rule -- but at query time the ASKER'S FINAL EXTENT DOES NOT EXIST.
    The maze asks about one step at a time, and at a 0.240 um pitch every
    step is under the 0.400 threshold. Measured on the re-routed chip: every
    step demoted itself to the thin rule and `vcm` walked the whole length of
    `topp`'s band one legal-looking cell at a time, reproducing
    `WIDE M6 topp vs vcm` at the identical coordinate.

    The claim's metal length BOUNDS the parallel run any neighbour can have
    with it. It is the one extent that is known here and cannot be walked
    around.
    """
    g = _wide_grid()
    k0, pad = 100, solve.pad_along(36) / 2.0 + g.rule[36][1]
    c0 = g.centre(36, k0)
    wf = g.net_w(36, "wide")
    for kk in g.covers(36, c0, wf):
        g.claim(36, kk, 20.0 - pad, 30.0 + pad, "wide", co=c0,
                sp=g.clear_for(36, wf), w=wf, mlo=20.0, mhi=30.0)
    # the whole run, and every step size a searcher could take
    for span in (10.0, 1.0, 0.401, 0.399, 0.240, 0.120):
        assert not g.free(36, k0 + 3, 22.0, 22.0 + span, "thin"), (
            "a %.3f um query escaped the wide rule -- the maze takes steps "
            "this size and would walk the band" % span)


def test_a_claim_TOO_SHORT_TO_RUN_PARALLEL_is_not_wide():
    """...and the demotion still fires where it is real: a claim whose own
    metal is shorter than `WIDE_RULE[1]` cannot make a wide pair with
    anything. A via pad is 0.380 um long.

    ⚠ The negative control for the gate above -- without this, "charge the
    wide rule always" would pass it, and that would cost every via pad on a
    wide net a track it does not owe.
    """
    g = _wide_grid()
    k0 = 100
    c0 = g.centre(36, k0)
    wf = g.net_w(36, "wide")
    for kk in g.covers(36, c0, wf):
        g.claim(36, kk, 20.0 - 0.29, 20.38 + 0.29, "wide", co=c0,
                sp=g.clear_for(36, wf), w=wf, mlo=20.0, mhi=20.38)
    assert g.free(36, k0 + 3, 18.0, 24.0, "thin"), (
        "a 0.380 um claim was charged the wide rule; nothing can run "
        "parallel to it for the %.3f um the rule needs" % solve.ca.WIDE_RULE[1])


class LadderCA(WideCA):
    """WideCA plus the step BELOW its rule -- the 65 nm deck's `Mx.S.2`.

    Read off `CLN65S_9M_6X1Z1U_cell.24a` 2026-09-11, taking the SECOND
    definition of each variable because that is the one SVRF keeps, and
    identical for M5, M6 and M7:

        Mx.S.2     width > 0.200   prl > 0.380   ->  0.120
        Mx.S.2.1   width > 0.400   prl > 0.400   ->  0.160
    """

    WIDE_STEPS = ((0.200, 0.380, 0.120), (0.400, 0.400, 0.160))


def _ladder_grid():
    """`_wide_grid`, bound to an adapter that offers the whole ladder."""
    solve.bind(LadderCA(), StubBD(), route_tiers=(35, 36, 37, 38))
    g = solve.Tracks({"tile": (400.0, 400.0), "rects": {}},
                     span=(0.0, 0.0, 400.0, 400.0), pg={},
                     widths={"wide": 0.992})
    for t in (35, 36, 37, 38):
        w, s, p, c0, h, n = g.rule[t]
        g.rule[t] = (0.140, 0.100, 0.240, c0, h, n)
    g._band = {}
    return g


def test_clear_for_walks_the_LADDER_and_falls_back_where_there_is_none():
    """Every step of the deck's wide-metal rule, widest applicable wins -- and
    an adapter that offers no ladder answers exactly as it always did.

    ⛔ `WIDE_RULE` alone is `Mx.S.2.1`. Reading only that step priced a 0.240
    um line at the thin-tier minimum, which is how the 65 nm chip's only
    geometric DRC result reached the deck.
    """
    g = _ladder_grid()
    assert g.clear_for(36, 0.140) == 0.100, "a thin wire owes the tier minimum"
    assert g.clear_for(36, 0.240) == 0.120, (
        "0.240 um is past Mx.S.2's 0.200 threshold and owes 0.120")
    assert g.clear_for(36, 0.992) == 0.160, "and past 0.400 it owes 0.160"
    # ...and the seam: no ladder, no change.
    plain = _wide_grid()
    assert plain.clear_for(36, 0.240) == 0.100, (
        "an adapter with no WIDE_STEPS must behave as it did before the "
        "ladder existed -- this is what keeps other consumers unmoved")


def test_a_net_that_STEPS_BETWEEN_LANES_is_charged_on_the_UNION():
    """Two claims of one net that touch are ONE SHAPE to the deck, and the
    wide rule is charged on the shape.

    ⛔⛔ **THE DIE'S ONLY GEOMETRIC DRC RESULT CAME THROUGH THIS HOLE.** Same-net
    claims never conflict -- correctly, they merge -- so nothing asked what
    they merge INTO. On the 65 nm chip `code7_raw` came down its M6 climb onto
    M5 lane 7.440, ran 0.48 um west and stepped up to lane 7.540 through a
    0.100-tall M6 jumper; the two runs overlap by 0.040, so DRC saw ONE shape
    0.240 tall running 0.670 um at 0.100 from `code8_raw`, where `M5.S.2`
    wants 0.120. Every query along the way was legal on its own: each wire is
    0.140 and 0.100 is the tier minimum.

    Here the same shape at the stub's pitch: a net holding the next lane makes
    the query's metal 0.380 across, which owes 0.120 and cannot stand one
    0.240 pitch from a foreign wire -- while the identical query from a net
    holding nothing owes 0.100 and can.
    """
    g = _ladder_grid()
    k0 = 100
    c = g.centre(36, k0)
    g.claim(36, k0 - 1, 20.0, 30.0, "thin", co=g.centre(36, k0 - 1))
    assert g.free(36, k0, 22.0, 28.0, "step"), (
        "a lone 0.140 wire one pitch from a thin neighbour is legal -- if "
        "this fails the test is measuring the wrong thing")
    # ...now the same net already holds a lane 0.100 um off this one, which is
    # the step the chip took: 7.440 against 7.540, overlapping by 0.040.
    g.claim(36, k0, 20.0, 30.0, "step", co=c + 0.100)
    assert not g.free(36, k0, 22.0, 28.0, "step"), (
        "the net's own metal 0.100 um away merges with this query into a "
        "0.240 um shape; it owes the foreign wire 0.120 and has 0.100")


def test_same_net_metal_that_never_RUNS_PARALLEL_does_not_union():
    """...and the negative control: no along-overlap is no union.

    ⚠ Without this, "always charge the union" would pass the gate above and
    charge every net for metal it merely passes near on another part of the
    die -- two shapes that never meet are two polygons, and the deck says so.
    """
    g = _ladder_grid()
    k0 = 100
    c = g.centre(36, k0)
    g.claim(36, k0 - 1, 20.0, 30.0, "thin", co=g.centre(36, k0 - 1))
    g.claim(36, k0, 40.0, 50.0, "step", co=c + 0.100)
    assert g.free(36, k0, 22.0, 28.0, "step"), (
        "the net's own metal runs 40..50 and this query runs 22..28 -- they "
        "share no parallel run, so they are not one shape and nothing is owed")


def test_a_net_far_from_its_own_metal_does_not_union_either():
    """Touching, not nearby. Two lanes further apart than the wire is wide are
    two shapes however long they run beside each other."""
    g = _ladder_grid()
    k0 = 100
    c = g.centre(36, k0)
    g.claim(36, k0 - 1, 20.0, 30.0, "thin", co=g.centre(36, k0 - 1))
    g.claim(36, k0, 20.0, 30.0, "step", co=c + 0.300)
    assert g.free(36, k0, 22.0, 28.0, "step"), (
        "0.300 um between lane centres is 0.160 um of daylight between two "
        "0.140 um wires -- they do not touch and do not merge")
