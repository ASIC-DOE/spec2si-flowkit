<!--docmeta
title: spec2si-flowkit — the node-agnostic core of the Spec-to-Silicon flow
genre: overview
status: active
area: top
owner: soumyajit
updated: 2026-08-20
summary: The part of the Spec-to-Silicon flow that must not diverge across process nodes — policy, genre vocabulary, documentation model and IR solver — vendored into each process port with a SHA-256 drift gate and a conformance test. What is shared, what deliberately is not, and how to add a node.
-->

<p align="center">
  <img src="docs/assets/spec2si-flowkit-logo.svg" alt="spec2si-flowkit" width="480">
</p>

# spec2si-flowkit

**Spec-to-Silicon** is a headless, code-driven path from a machine-readable
spec to signoff-clean silicon: sized, placed, routed, extracted, re-simulated
and gated without a human in the loop. It has three proven process ports and a
fourth, SKY130, in bring-up; each lives in its own repository.

This repository holds the part that must **not** diverge between them. Three
copies of a lesson is where lessons start diverging.

| Port | Node | Where it stands |
|---|---|---|
| [`spec2si-tsmc65`](https://github.com/mandalsoumyajit/spec2si-tsmc65) | TSMC 65 nm LP | **Reference port.** Full flow; a signed-off chip and a signed-off ADC built through it |
| [`spec2si-tsmc28`](https://github.com/mandalsoumyajit/spec2si-tsmc28) | TSMC 28 nm HPC+/ULL | Engine ported and proven — 15 cells DRC-clean and LVS-correct, a whole PLL signed off |
| [`spec2si-xt011`](https://github.com/mandalsoumyajit/spec2si-xt011) | X-FAB XT011 PDSOI | Bring-up; first block signed off (DRC density-only, LVS MATCH) |
| [`spec2si-sky130`](https://github.com/mandalsoumyajit/spec2si-sky130) | SkyWater SKY130 | Bring-up; Cadence PDK pinned and deployment infrastructure under construction |

A repository is scoped to a **process**, not to a design, because that is
where the fork-forcing divergence actually falls — Calibre vs PVS/Pegasus,
PyCell vs SKILL PCells, one substrate node vs per-tub isolation. Designs are
directories *inside* a port (see [ADR-0001](docs/decisions/0001-process-scoped-repos.md)).

## Start here

This repo is the node-agnostic core, so the procedures here are the ones
that are true on every process node. Each port carries its own index for
the commands that are not.

| I want to | Start at |
|---|---|
| run a design session the way this flow is driven | [run-an-agent-session](docs/howto/shared/run-an-agent-session.md) |
| understand the method, and the rules every port signs | [the-method](docs/howto/shared/the-method.md) |
| vendor the core into a port, or read the drift gate | [vendoring](docs/howto/shared/vendoring.md) |
| stand up a new process node | [add-a-process-node](docs/howto/shared/add-a-process-node.md) |
| pick up where the last session stopped | [RESUME.md](RESUME.md) — a `log`, so re-run the gates before citing it |

## What is shared

Eighty-three files, vendored byte-identically into all four ports and
hash-gated — 332 checks on every `sync.py --check-all`.

| What | Files | Why it is node-agnostic |
|---|---|---|
| **The artifact browser** | `browse/` (model, roots, tools, cluster, estimate, launch, server + their test) | A read-only viewer over a repo's own results: listings badged from what the flow wrote, GDS renders, waveforms, the design record (which captured view is this file), the cluster's trees. Stdlib-only by construction and PDK-blind; what differs per port is `roots.json`, a declaration it never copies, and what is engine-specific (the transient reader, the abstract track map) it LOCATES in the served repo and degrades without. Vendored 2026-09-12 ([ADR-0004](docs/decisions/0004-browse-and-transport-vendored.md)); until then three ports reached across the disk into a fourth to draw a picture |
| **The cluster transport** | `jobs/` (remote, hosts, procscan, the job CLI, the cluster-side bundle + their tests), landing in each port's deployment area | An ssh round trip that cannot be corrupted by quoting, a host chooser, the licence-holding process scanner. A SITE fact, not a node one: all four ports share one cluster, and the browser reads it through this |
| **The agent-loop runlog** | `browse/runlog.py`, `browse/agentview.py` + their tests | One agent turn that touched a known cell is one attempt; which cells exist comes from the port's tree, its design record and its declared roster |
| **The flow policy** | `policy/flow_policy.core.json` (v1.2.0, **19 rules**) + `conformance/test_policy_conformance.py` | Each rule is stated as a portable *principle*. The enforcement point is not shared — see below |
| **The docmeta genre vocabulary** | `policy/docmeta.core.json` (8 genres, 5 aliases) | A genre is a **staleness contract**, and a contract shared by three repos is exactly what must not diverge. All three had adopted `docmeta` independently and drifted — 26 tracked docs carried a genre the generator rejected |
| **The documentation model** | `docs/docmodel.py` | Frontmatter, the genre vocabulary, a static-AST API extractor, doc discovery, the link and freshness checks. A docstring is a docstring on 65 nm and on 28 nm |
| **The Markdown backend** | `docs/mdbackend.py` | Renders what the model parsed. Three repos rendering three slightly different API pages is the same divergence, one level up |
| **The IR solver** | `irdrop/solver.py`, `irdrop/currents.py` + their tests | Ohms and amps in, volts out. A Spectre oppoint is a *simulator* format, not a PDK one |
| **The routing core (phase 1)** | `routekit/geom.py`, `routekit/audit.py` + their tests | Rectangles in, findings out. Every process fact arrives through a `rules` object the consumer binds; a missing fact is a refusal, never a default. See [ADR-0002](docs/decisions/0002-routekit-vendored-core.md) and [the plan](docs/routekit_plan.md) |
| **The card contract (phase 2)** | `routekit/card.py`, `routekit/ruleprobe.py` + their tests | Load/validate/bind a RoutingCard — families by membership lists only, missing-vs-measured-absent kept apart, NDA split supported — and card-driven rule probes self-checked against the audit engine. [Schema](docs/routekit_card_schema.md) |
| **The housekeeper** | `housekeeping/run_features.py`, `housekeeping/stale_classify.py`, `housekeeping/attic_sweep.sh` | Mtimes and filenames in, `KEEP`/`SWEEP` out. One shared NFS home holds every node's trees, so what may be forgotten is not any one port's question. Features, decision and mover are kept apart so the decision can be *measured* rather than asserted — age alone was a one-feature classifier nobody had validated. [Reference](docs/housekeeping.md) |
| **The DRC repair loop** | drcloop/ (4 modules + 4 test modules) | The signoff deck's own markers ARE the positions, so a repair answers THEM rather than re-deriving the violating shapes from the plan. Shared on the strongest evidence here: the results-database reader was written TWICE, independently, for Calibre 2024.1 and for PVS/Pegasus, and came out the same algorithm. Rule NAMES and the flattener stay local. See [ADR-0003](docs/decisions/0003-drc-in-the-loop.md) and [the guide](docs/drc_loop.md) |

Everything here is **stdlib-only and never imports the code it documents or
analyses**. That is load-bearing: it is why the freshness gate and the
conformance test run in CI with no PDK, no vendor licence and no dependency
install.

## What is deliberately not shared

**The engine.** That decision was taken after *measuring* the fork rather than
assuming it, and re-measuring it since. As of 2026-08-20 the 65 nm engine is 98
Python files and the 28 nm one 133; **25 filenames overlap and exactly two are
byte-identical** (`reporthtml.py`, `test_abstract_svg.py`), while
`netlist_route.py` differs by 2,671 lines. The gap has widened, not closed, in
the direction the decision predicted. The divergence is real work, not drift —
Calibre vs Pegasus, PyCell vs SKILL, one substrate node vs per-tub isolation —
and forcing it into one module would produce a file of branches nobody can
reason about on any node.

**Enforcement paths, evidence, wording detail.** The enforcement point for
`verify-log` is a Calibre report parser on one node and a Pegasus one on
another. A shared file claiming otherwise would be a lie with a hash on it.

So the contract is narrow on purpose:

> Every consumer carries **every** core rule id in its own policy file, with a
> **status** — `enforced`, `partial`, `not-implemented`, or `waived` (with a
> reason). Its own wording, its own enforcement path, its own evidence, plus
> any process-specific rules it needs.

## `not-implemented` is a passing state

A bring-up port declaring most of the set `not-implemented` **passes**
conformance. That is the design: the gap becomes a number printed on every run
instead of an absence nobody can see.

```
policy conformance: spec2si-tsmc65      policy conformance: spec2si-xt011
  adoption: 12/18 enforced, 6 partial     adoption: 1/18 enforced, 6 partial,
  docs:     176/176 tagged                          11 not-implemented
                                          docs:     19/20 tagged (1 untagged)
```

Which answers "are the ports consistent?" with a figure rather than an
impression — the same argument as counting unclassified runlog attempts, and
the opposite of a check that quietly has nothing to check.

Documentation adoption is reported beside it for the same reason, and **never**
contributes an error: a gate that fails a chip commit over a missing
`summary:` line is a gate people route around.

## Distribution: vendored, with a drift gate

Chosen over a submodule, a subtree or a package install because it costs the
consumers **nothing**: a clone stays a clone, the site rsync push is
unchanged, and the cluster keeps running raw system `python3` — no pip, at a
3.6.8 / 3.8.10 floor the consumers' own tests enforce. Vendoring is not a
workaround there; it is the only mechanism that works.

The price is that real copies exist, so the copies are hash-checked — the same
shape as every other derived artifact in these repos.

```bash
python3 sync.py --to C:\dev\spec2si-xt011   # vendor / update one port
python3 sync.py --check-all                 # gate: has any copy drifted?
```

Each port then runs its own, **from inside that port** — neither file exists
here: the conformance test is vendored *to* a port's `policy/`, and each repo
writes its own `docs/gen.py` (only the model and the backends come from here).

```bash
cd C:\dev\spec2si-xt011
python3 policy/test_policy_conformance.py
python3 docs/gen.py check
python3 docs/test_claims.py .
```

**Never hand-edit a vendored copy.** Change the core here, re-vendor, and let
each port decide whether its status for the changed rule still holds.

⛔ **`--to` overwrites without asking.** It copies the whole vendor list
unconditionally, so on a port that has drifted it discards the local file
rather than reporting a conflict — including work in flight. Safe on a new
node; on an existing one run `--check <repo>` first and resolve what it
lists, or copy the single file you changed.

## Adding a process node

1. add it to `consumers.json` — recording where the repo **is**, never where
   it is going; an intended path makes the drift gate report `MISSING` on a
   healthy repo;
2. `python3 sync.py --to <repo>`;
3. write `analog/specs/flow_policy.json` in the new repo listing every core
   rule with a status — `not-implemented` for everything the port has not
   reached yet;
4. add a `docs/gen.py` holding that repo's areas and doc-exclude list; the
   model and backend come from here;
5. run the conformance test and the docs gate, and commit.

## Plans

[`docs/em_ir_alignment.md`](docs/em_ir_alignment.md) — a fast IR-drop
calculator driven by the simulated operating point, and the prerequisite it
exposed: the EM and resistance features of the three flows are not aligned,
and both are keyed to a **metal option** that only one repo records. Carries
the measured feature matrix across the three cards, four divergences that are
defects rather than gaps, and the sequencing. The solver half of it is now
shared; the local halves are not.

Next: the web and PDF backends hang off `docmodel.py` alongside `mdbackend.py`,
so a manual cannot drift from the repo it documents.

## Studies

[`docs/runlog_first_analysis.md`](docs/runlog_first_analysis.md) — first pass
over the agent-loop runlog, **343 attempts across tsmc28/tsmc65/xt011**, which
is why it sits here rather than in any one of them. Measures the claim the log
was built to test (*"~10 offline iterations for zero cluster runs"*) at **6.25
offline iterations per cluster round trip** for analog work — the first time
that number has come from anything but memory — and records what had to be
fixed before it meant anything: a cluster-hint substring carrying 110 of 162
tags, an "attempt" being a *turn* where a round trip spans a mean of 2.0, and
`offline` being a fallback rather than a detection. Also the finding that cost
data: **the session transcripts are not durable** (30-day retention), so the
committed log is the only record past a month.

The tool it analyses, `browse/runlog.py`, is vendored from here into every
port's `browse/` (since 2026-08-26, with `agentview` beside it; the whole
browser followed on 2026-09-12). Since 2026-09-12 a port's design record is
its roster too, and a port can declare PATH RULES for chip-level work whose
files name no cell — xt011's die work was 603 of 618 turns unrecorded
before that, with the harvest firing on every session end.

## Licence

**Apache-2.0** — see [LICENSE](LICENSE).

This repository is the public face of the effort by design. It is small,
stdlib-only, and touches no PDK and no vendor tool: what is here is the
METHOD — the flow policy and its conformance test, the shared genre
vocabulary, the documentation model and Markdown backend, and a pure
resistive IR solver. All of it is usable on any process.

The four process ports that consume it (`spec2si-tsmc65`,
`spec2si-tsmc28`, `spec2si-xt011`, `spec2si-sky130`) are **internal and all-rights-
reserved**, because a port necessarily encodes foundry NDA material.
That split is deliberate, and it is why this repository can be shared
without a confidentiality scrub of a thousand files.
