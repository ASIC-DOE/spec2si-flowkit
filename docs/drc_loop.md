<!--docmeta
title: drcloop — putting the signoff deck inside the debugging loop
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-08-29
summary: How a consumer port binds and drives drcloop/. The method in one line — the deck's own markers ARE the positions, so a repair answers them rather than re-deriving the violating shapes from the plan, and its clearances are measured against the stream the deck read. Four modules behind one seam - resultsdb (one parser, Calibre and Pegasus alike, proven by two independent implementations converging), markers (shoelace area, edge span, the area patch, the net-blind clearance test), triage (a port-supplied rule table, geometric attribution smallest-area-first, the baseline delta), loop (a control that cannot be skipped, a stream digest that makes a stale reply a refusal, a ledger). What a port must supply - a rules object answering min_area/min_space/grid, a rule-name-to-layer map, and conductors flattened out of its own stream. Worked binding for xt011 and for a Calibre port, and the three core policy rules that land with it.
-->

# `drcloop` — putting the signoff deck inside the debugging loop

> Design owner, `spec2si-xt011`, 2026-08-28:
> *"There is no need to compute these positions. The DRC already gives you the
> positions. Move DRC into the debugging loop."* — and, in the same breath,
> *"the addition can be smart — it can check local metal distances to ensure
> that new spacing errors are not introduced."*

That is the whole method. This package is the part of it that does not depend
on which deck ran. The decision to share it, and the evidence:
[ADR-0003](decisions/0003-drc-in-the-loop.md).

---

## 1. The one idea, and its corollary

**A geometry checker in the flow is a SECOND MODEL of a question the deck has
already answered.** The plan and the deck disagree about which shapes merge,
about what a via definition draws, and about what an instantiated block
contributes — so a computed patch lands where nothing was wrong while the real
one stays, and the next run looks exactly as though the patch was never
applied.

The results database already carries every offending polygon **in the top
cell's own coordinates**. Read it and answer *that*.

**The corollary is what keeps it honest.** A patch is new metal, so it owes
every rule the metal beside it owes — and the only artefact that knows what is
really there is the **stream the deck read**. Never the route file, which is
the plan again.

---

## 2. The modules

```
resultsdb   the ASCII results database  -- one parser, two vendors
markers     a marker's geometry, and the patch that closes it
triage      whose result is it, and did this change ADD one
loop        the protocol: a control, a binding, and a ledger
```

Stdlib only, Python 3.6 floor, no PDK, no deck, no vendor tool. Vendored by
`sync.py` beside `routekit/` and `irdrop/`; never hand-edit a vendored copy.

### `resultsdb`

```python
db = resultsdb.Database.load("nvg_drc.db", expect_top="ancBrain_gap")
db.counts()          # {rule: n}
db.by_rule(lambda r: r.startswith("A1M"))
```

Three refusals, each paid for:

- **it does not reduce a record to a bounding box.** A spacing violation is an
  edge pair; a box round both conductors points at the metal when the error is
  the **gap**. `bboxes()` exists for a caller that wants the reduction and it
  is theirs to ask for;
- **it does not copy the deck text out.** The counts line sizes a block of the
  rule deck's own source, which is skipped so it can never be mistaken for a
  header — and it is the NDA-sensitive half of the file, so a results database
  can then travel with a report;
- **it does not assume the marker frame.** `expect_top=` refuses when the
  database is somebody else's run. A coordinate is a perfectly good coordinate
  in the wrong frame, which is why nothing else catches this.

### `markers`

```python
cond  = markers.Conductors({"MET2": rects_from_your_flattener, ...})
patch = markers.close_area(marker, "MET2", cond, rules)
```

Five measured facts it encodes, and the numbers are from a real run:

1. **the area is a SHOELACE.** One deck's marker for a via pad measures
   0.290 × 0.195 across its bbox and **0.056050 µm²** in fact — a
   0.290 × 0.190 bar with a 0.190 × 0.005 step. Sizing off the bbox
   understates the deficit and leaves the result standing;
2. **the patch spans the polygon's cross-extent AT the edge it joins**, not
   the bbox's. On a stepped polygon the bbox reaches past the metal, and an
   extension that merely touches a corner is a **notch**;
3. **a bar is extended along its LONG axis** — widening it trades an area rule
   for a spacing one;
4. **the clearance test carries no net names, and that is correct.** Being one
   net excuses a SHORT and excuses nothing else. Same-net metal a patch does
   not *merge* with still owes the space;
5. **both directions are tried.** One marker of 116 needed the direction the
   other 115 did not.

And a marker that clears in neither direction comes back as a `Refusal` with
the clearance that beat it — **never `None`, never a silent skip**, because a
skip and a fix are indistinguishable in the next run.

### `triage`

```python
table = triage.RuleTable().add("area", r"^A1M", "extend the bar", auto=True)
zones = triage.Zones([("ancBrain_top", box), ("SLDO_5", box)])
owned = triage.attribute(db.records, zones)
delta = triage.diff(baseline.counts(), run.counts())
```

⛔ **Attribute by geometry, not by the tool's cell names.** On one real die run
`report_summary -hier` put 4336 of 4742 results into three cells called
`MASCO__P1/P2/P3` and listed the block that actually holds 4492 of them at
**twenty-two**. `MASCO` is in none of the stream's 2101 structures, in no rule
file, nowhere in the results database, and **is the engine's own partitioning
binary**. Read as design cells that table is not merely uninformative, it is
inverted.

⚠️ **The zones nest, so "inside" needs an order.** A ring's box contains the
block it wraps. Smallest-area-first; a result inside nothing is the
**assembly's**, reported rather than binned.

⚠️ **And a zone is a bounding box, which is evidence and not proof.** What
settles ownership is re-running the deck on the identical stream bytes with
the suspect block as top cell — cheap, because a block inside a die stream is
already a valid top cell (one measured pair: 51 s against the die's 255).
This module localises; the control run proves.

An unmatched rule comes back class `None` and `unclassified()` counts it. A
table that folds the unknown into `other` stops reporting the day the deck
gains a rule.

### `loop`

```python
led = loop.Ledger("ancBrain_gap")
led.baseline(base.counts(), "same cellview, no routes drawn")
led.step("routed", run.counts())
reply = loop.Reply.build(top, db_path, gds_path, patches, refusals)
reply.check_fresh(gds_path_now)      # raises StaleReply if the stream moved
```

Three rules, and they are the framework:

1. **a run without a control is a number.** `Ledger.clean` raises `NoControl`
   rather than answering. `clean` means **added nothing**, never *returned
   zero* — a real baseline is rarely zero, and a flow that waits for zero
   waits forever while one that quotes a total hides what it added;
2. **a reply is bound to the stream it replied to**, by sha256. Re-route,
   re-DRC, re-patch: skipping the middle step is how a patch comes to sit one
   grid step from the shape it was meant to merge with;
3. **shrink the artefact, never the check.** When the total cannot be read,
   build the smallest cell holding the thing under test **with its coordinates
   unchanged**, so a marker there and a marker here are the same number and
   the two runs diff. `check_isolation` verifies that the shrink kept the
   frame.

---

## 3. What a port must supply

Three things, and each is a process fact this package refuses to guess.

**A `rules` object** — the same seam `routekit` uses:

```python
class Rules(object):
    def min_area(self, layer):  ...   # um2
    def min_space(self, layer): ...   # um
    def grid(self):             ...   # um
```

A missing method raises `RulesError` naming it. An unmeasured value raises; it
does not default.

**A rule-name-to-layer map.** `A1M2 -> MET2` on one node, `M2.A.1 -> M2` on
another. Returning `None` means "not mine", which is how one responder runs
over a mixed database.

**Conductors, flattened out of the stream the deck read** — drawing purpose
only. A dummy-fill blockage marker streams to the same layer *number* as real
metal on more than one node, and a census that unions the purposes reports a
blockage as conductor.

### Worked: an X-FAB / PVS port

```python
import core_solver as cs, gds_layer
from drcloop import resultsdb, markers, triage, loop

def layer_of(rule):                       # A1M2 -> MET2, A1MT -> METCT
    m = re.match(r"^A1M(\d|T)$", rule)
    return None if not m else ("METCT" if m.group(1) == "T"
                               else "MET" + m.group(1))

db   = resultsdb.Database.load(dbpath, expect_top="ancBrain_gap")
area = db.by_rule(lambda r: layer_of(r) is not None)
by   = {lay: [(s[0]/1000., s[1]/1000., s[2]/1000., s[3]/1000.)
              for s in flat(gds, top, cs._GDS[lay]) if s[4] == 0]
        for lay in sorted(set(layer_of(r["rule"]) for r in area))}

cond = markers.Conductors(by)
patches, refusals = markers.close_all(area, layer_of, cond, cs.RULES)
reply = loop.Reply.build("ancBrain_gap", dbpath, gds, patches, refusals)
```

### Worked: a Calibre port

Same code; `layer_of` matches `^M(\d+)\.A\.` and returns `M<n>`, the
flattener is the port's own, and `rules` wraps `tech/process.py`. Nothing in
`drcloop` changes — which is the claim ADR-0003 makes and the reason the
parser is shared at all.

---

## 4. The policy rules that land with it

Core v1.3.0 adds three, each stated as a portable principle with the
enforcement point left per port:

| id | in one line |
|---|---|
| `marker-is-the-position` | answer the deck's markers; measure clearances against the stream; the plan may ATTRIBUTE and may never LOCATE; refuse by name |
| `baseline-delta` | judge by what a change ADDED over a control that is the same object; clean means added nothing; shrink the artefact, not the check; bind a reply to its input |
| `render-before-acting` | draw a geometric claim before acting on it — the recurring failure is a CORRECT measurement of the wrong object, and a picture reads no number |

`not-implemented` remains a passing state. At v1.3.0: `spec2si-xt011`
2 enforced + 1 partial, `spec2si-tsmc65` 1 partial, `spec2si-tsmc28` and
`spec2si-sky130` not-implemented.

---

## 5. Gates

```bash
python3 drcloop/test_resultsdb.py && python3 drcloop/test_markers.py && python3 drcloop/test_triage.py && python3 drcloop/test_loop.py
```

41 checks, every one with a control that must fire — the house rule that a
detector which has never reported is not evidence. The ones worth naming:

- the fixture is the **stepped** polygon, not a rectangle, because a test on a
  rectangle cannot tell the shoelace from the bbox;
- the fixture database hides the string `S1M1` **inside** `A1M2`'s deck text,
  so a parser that fails to skip the block files A1M2's markers under a rule
  that does not exist;
- `test_a_marker_boxed_in_on_both_sides_is_REFUSED_BY_NAME` is the poison twin
  of the two patch tests: it asserts the clearance appears in the message;
- `test_clean_is_ADDED_NOTHING_not_returned_zero` asserts a run of 11 against
  a baseline of 11 is clean **and** that 12 against 11 is not;
- `test_a_ledger_with_no_control_REFUSES_to_answer` — the poison twin of every
  `clean` claim in the package;
- `test_check_isolation_pairs_markers_by_position` re-origins the small cell
  and requires the pairing to fail.

---

## See also

- [ADR-0003](decisions/0003-drc-in-the-loop.md) — the decision and its evidence
- [ADR-0002](decisions/0002-routekit-vendored-core.md) — the same evidence
  standard applied to the router
- [`em_ir_alignment.md`](em_ir_alignment.md) §6 — the seam both obey
- `spec2si-tsmc28` `docs/drc_in_the_loop.md` — the narrative, with figures and
  the 1063 → 11 trajectory
