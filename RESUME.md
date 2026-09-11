<!--docmeta
title: RESUME — the routekit workstream, session handoff
genre: log
status: active
area: top
owner: soumyajit
updated: 2026-08-26
summary: Where routekit stands after the 2026-08-26 sessions and the exact next actions. Phases 0-2 are DONE and MERGED TO MAIN (pushed, all five repos): the corpus frozen; geom/audit/card/ruleprobe/solve/elec/gdsw vendored to all four ports at 33 files / 132 checks; both TSMC ports on the vendored search core; and the golden rule-probe arms RKP_PASS on all three carded ports — sky130 (Pegasus), tsmc28 (Calibre 11/11 incl. via probes; M7/My was thick-tier DATATYPE gating), tsmc65 (Calibre 24/24, four families + three via tiers, extract round deck-countersigned). Phase 3's solver is promoted; its widen/elec unit and phase 4's three live consumers are the open work, plus the wide-landing pair-construction flow finding running as its own session (task_6675958d) and a schema slot for rule applicability. Read the plan (docs/routekit_plan.md) for the full record; this file is only where to START.
-->

# RESUME — the routekit workstream

Last worked **2026-08-26**, across five repos in one push. **Start by
reading [`docs/routekit_plan.md`](docs/routekit_plan.md)** — it is the
plan of record with every phase's gates and measurements; this file is
the handoff: what state each repo is in, what is safe to touch, and the
exact next actions. The rendered decision page is the "Routekit"
artifact (claude.ai/code/artifact/4e69cf73-1698-4a7e-b670-191f5b3e53dd).

## The state, measured 2026-08-26 (not remembered — re-run the sweep)

```
for r in flowkit tsmc65 tsmc28 xt011 sky130; do cd /c/dev/spec2si-$r; \
  echo "$r: $(git branch --show-current) $(git log -1 --format=%h) \
  unpushed=$(git rev-list --count @{upstream}..HEAD 2>/dev/null)"; done
```

| repo | branch | HEAD | unpushed | notes |
|---|---|---|---|---|
| flowkit | **main** | 2ab737b | 0 (pushed) | `routekit` MERGED to main (fast-forward); the branch is kept in sync with main |
| tsmc65 | main | 614f7c7 | 0 (pushed) | extract round + width_for re-point landed; dirty: `analog/specs/runlog_stats.json` (auto-stats, theirs) |
| tsmc28 | main | cbd3629+ | 0 (pushed) | task_6675958d landed and is RECONCILED: its site-tier gate fix is upstreamed (audit.py + 3 poisons), its 31-verdict yardstick (`test_via_redundancy_tiers.py`) passes against the vendored copy, and the probe kit is corrected (strap caps; clean twin = the governing tier’s smallest square option at its exact ceiling). Both arms re-ran RKP_PASS. The rkpair misattribution is corrected in plan §6 |
| xt011 | `ring-rev3` | 3fdeb3a | 0 (pushed) | vendored refreshes ride the ring branch |
| sky130 | main | 7c7b1ad | 0 (pushed) | vendored refreshes |

⚠️ **Nothing here pushes a consumer.** Every consumer's unpushed commits
are deliberate: their owners push/PR on their own flow. The flowkit
`routekit` branch is pushed but unmerged for the same reason.

## What is DONE (gates in the plan, hashes in `routekit/corpus.json`)

- **Phases 0–1–2–3**: corpus frozen (tsmc65 136/136 from-scratch
  deterministic; snapshot-vs-scratch policy fixed at 83/136 measured);
  geom + audit extracted, tsmc28 re-pointed suite-green; the card
  contract + rule probes + per-layer via-enclosure override; the search
  core promoted behind `bind()` with both TSMC ports on it and the
  byte-copy deleted; `elec.py` (EM floors with band edges, ohm pricing,
  R_max, the fixpoint contract) and the round-trip-gated GDS writer.
- **sky130 cards**: all four gates including the Pegasus golden probe
  on asic9 and UNCHANGED_GEOMETRY by semantic digest; roadmap item 10
  DONE. Its per-layer card profile is validated by `card.validate`.
- **tsmc28 golden probe (Calibre, asic7)**: M2 countersigned **5/5**
  (M2.S.1/2/3/7/13, A.2+A.3, G.4:M2i×4). Drivers:
  `chip/floorplan/ruleprobe_stream.py` + `ruleprobe_run.py`
  (`--score-only` re-scores without licences).
- **tsmc65 binding + golden probe (Calibre, asic7, 2026-08-26 second
  session): RKP_PASS, all four rule families** — `rules65.py` +
  `routecard65.py` + ported drivers; thick-tier DATATYPE gating and
  G.4-stops-at-M7i measured and carded; clean twin density-only. The
  10 named skips in `rkprobe_expected.json` are the tsmc65 extract
  round's worklist.
- **tsmc28 arm completed (third session): RKP_PASS 11/11** — M7/My
  datatype-resolved and countersigned, via probes streaming on the
  deck-read via numbers, the line-end family modelled
  (`line_end_pair_space`), extract round done for the routing card,
  and the wide-landing pair-construction flow finding measured and
  flagged (task_6675958d).
- **P_METAL_FAMILY** fixed on tsmc28 main via the freeze-first recipe
  (its chip session, commit b8a79bb); **VIAGEO** reader fixed on xt011
  byte-replay-gated at 8e238ce; **M9/VIA8** through the card with the
  def constant cross-checked (`test_card_overrides.py`).
- **test_glue_solver re-pinned** to the vendored core (tsmc65
  676a8037): the bundles net is `code9_raw` (frozen code-bus
  contention), and the shim-rebinding hazard is test-side only
  (production code only MUTATES the shared dicts, which is safe).

## NEXT ACTIONS, in order

1. ✅ **DONE 2026-08-26 (fourth session): the owner merges** — flowkit
   `routekit` fast-forwarded into **main and pushed** (work continues
   on main; the `routekit` branch is kept in sync); every consumer's
   commits pushed (tsmc65/tsmc28/sky130 main, xt011 `ring-rev3`).
   The wide-landing pair-construction flow finding is being worked in
   its own session (task_6675958d).
2. ✅ **DONE 2026-08-26 (second session): tsmc65 rules binding + its
   probe arm — RKP_PASS on all four rule families** (plan §6 phase-2
   has the full record). `rules65.py` binds `CardRules` over the
   assembled `tsmc65_route_card` (builder `routecard65.py`, template
   tracked, values gitignored); drivers
   `analog/engine/layout/ruleprobe_{stream,run}.py` with `drc_gds` as
   the Calibre entry. Three findings, all landed: the off-grid
   `1.8*w` stub (upstream grid-snap + on-grid guard in `ruleprobe`);
   **thick tiers are DATATYPE-gated** (M8=(38;40), M9=(39;60),
   VIA7/8 dt 40 — dt-0 evaluates NOTHING; the signed chip already
   streams the NEW scheme; `gds_layers` now carries datatypes);
   **G.4 stops at M7i** (measured-absent `min_edge_rule` in the card).
   Upstream also hardened: an annotated `null` now REFUSES in every
   `CardRules` scalar accessor (a template merged through `load_split`
   used to answer None), and a refused `via_geometry` names BOTH via
   skips.
3. ✅ **DONE 2026-08-26 (third session): the M7/My mystery + the
   tsmc28 extract round — its arm is RKP_PASS, 11/11 probes incl.
   BOTH via probes** (plan §6 phase-2 has the full record). M7 was
   the tsmc65 datatype gating exactly (`M7i=(37;20)`; gridcard now
   carries `{layer, datatype}` entries for M7/M8/M9 + all vias);
   `M2.S.12` attributed by marker to the line_end clean island → the
   line-end FAMILY is modelled upstream as the optional
   `line_end_pair_space` accessor ("space OF two line-ends" vs S.7's
   "space TO"); My line-end folds into flat spacing (no plain rule —
   the sky130 precedent), M7 min-area filled. Three upstream
   clean-twin construction defects fixed in `ruleprobe.py`, and one
   measured FLOW finding flagged (chip task_6675958d): the deck
   rejects 2-square pairs on wide landings at every gap in its own
   [floor, ceiling] window except the floor (which draws VIA2.S.5);
   only the 2x2 cluster is quiet — `routing.via` builds the rejected
   construction (`rkpair` experiment, asic7 rkprobe/rkpair_run).
4. ✅ **DONE 2026-08-26 (fourth session): the tsmc65 extract round —
   RKP_PASS 24/24, all four families + ALL THREE via tiers** (plan §6
   phase-2 has the record; phase 2 is now ✅). `deckroute65.py`
   extracts every numeric deck VARIABLE (live branch, tracked, no
   values); `routecard65.py`'s `_apply_extract` maps + cross-checks
   (both min-area bisection brackets deck-CONFIRMED). Answers: no
   plain line-end class here either (flat spacing governs, spacing
   gate ON everywhere); VIAx across-enclosure is a deck-read ZERO;
   VIA8.R.8 is UNGATED (lone VIA8 never legal). Three more probe
   construction classes fixed upstream, all caught offline (across
   margin, min-width/min-area-legal pads, ungated-tier clean
   cluster) — and the notch probe DEGENERATED at L=4w=bar on the
   thick tiers (earlier M8/M9 notch OKs were vacuous; real now).
5. ✅ **DONE 2026-08-26 (fifth session): the phase-3 widen/elec unit —
   and it was smaller than feared** (plan §6). Appendix F had already
   retired the `route_widen` translation pass: the router is
   width-aware (the band machinery rode into `solve.py` and the
   136/136 corpus replay exercises it), so the unit was producer-side.
   `elec.solve_width` = `route_budget.width_for` promoted with every
   §10 guard as upstream poisons (via-floor refusal, dead band by the
   narrowest segment, keep-what-passes, never-below-drawn, headroom);
   tsmc65 re-pointed with a byte-identical `--json` replay and the
   corpus replay green. One informational drift recorded in the plan
   (topp `R_drawn_ohm` 47.82 frozen vs 48.34 today — pre-existing,
   report-only). ⚠️ the tsmc28 vendored copy is NOT refreshed to this
   flowkit state — task_6675958d is live in that checkout; re-vendor
   when it lands, together with its own audit.py reconcile.
6. **Phase 4 — three live consumers** (one now well under way):
   ◐ **the tile: 63/71 routed, 0 disconnected, 174 s (was 48/71,
   372 s)** — the 23-fail class was three more BASE-era assumptions in
   the core (start runway, anchored-legal tier, pin-goal arrival; plan
   §6 has the record) + a net_probe tie-break; the remaining EIGHT
   have named diagnoses in the artifact (port-goal legs for
   Dout/Vrefp; the bit-7 pair; SAMP's anchored-claim co tolerance;
   VDACn's keep-out needs an exit corridor, routes without the box;
   net31 is real contention). The `--solo` diagnosis mode in the
   driver is the tool for the rest. Still open: the tsmc65 v2 glue
   re-route (consumes `elec.solve_width` via the re-pointed
   `route_budget`; waits on the v2 floorplan) and xt011's
   `gen_core_route.py` as an import — **readiness AUDITED and BUILD ITEM 1
   DONE 2026-08-26 (eighth session): xt011's
   `docs/core_routing_plan.md` §10 is the verdict, and the RoutingCard
   + bind now EXIST** — `deckroute_xt011.py` (PVS rule blocks) →
   `routecard_xt011.py` (card cross-checked against gen_route's signed
   W3 cap and via_probe's measured VIAGEO — the deck derivation
   reproduces 2.250/1.050 exactly) → `chip/floorplan/core_solver.py`
   (ten-symbol adapter, ROUTE_TIERS MET3..MET5+METCT, PROVISIONAL
   axis table) with 7 gates green. Named tails: the via_enclosure
   flush-cut violation doesn't fire the offline gate on this
   one-all-sides-floor deck (probe-arm session attributes); run the
   binding suite SEPARATELY from routekit/ (import-time bind is live
   state); the Pegasus golden probe arm for the card is unrun.** Its obstacle
   snapshot is rebuilt current against ring rev 3 (step 2 closed);
   the four remaining build items are named with recipes (RoutingCard
   + bind adapter — needs a deck-read round, the deckroute65 pattern;
   route plan; net_classes content; pin_access), and one live hazard:
   the die-orientation rework in a parallel session changes the top
   composition, not the core frame — re-verify before routing. Owed
   upstream: maze-level unit fixtures for the three new seams (gated
   today by the corpus replay and the tile).

## Hazards the next session inherits (each measured this one)

- **`solve.bind()` is live module state.** A test or driver that binds
  a stub must restore (see `test_solve.py`'s fixture); rebinding a
  consumer shim's module attribute touches a dead copy — patch
  `sys.modules[Allocator.__module__]` instead (test_glue_solver does).
- **PYTHONUTF8=1 on Windows** for every gate/replay (cp1252 has cost 41
  green gates and a gridcard read).
- **Reuse `verify_layout`'s RULECHECK regex over `DRC.rep`** — a
  restated parser against `drc.out` reported "(none)" on a run that had
  results.
- **The shell's cwd does not survive parallel/backgrounded calls** —
  three commands this session ran in the wrong repo and printed
  "no tests ran"; `cd` explicitly in every compound command.
- **Signed cells are never re-routed**; the corpus
  (`routekit/corpus.json`) gates every phase, and `glue_route.json`
  stays frozen by hash.
