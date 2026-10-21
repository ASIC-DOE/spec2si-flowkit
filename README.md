<!--docmeta
title: spec2si-flowkit — the node-agnostic core of the Spec-to-Silicon flow
genre: overview
status: active
area: top
owner: soumyajit
updated: 2026-10-15
summary: The part of the Spec-to-Silicon flow that must not diverge across process nodes — policy, genre vocabulary, documentation model and backends, the artifact browser and its multi-repo hub, the cluster transport, the IR solver, the routing core and the DRC repair loop — vendored into five process ports with a SHA-256 drift gate and a conformance test. Where to start, what is shared, what deliberately is not, and how to add a node.
-->

<p align="center">
  <img src="docs/assets/spec2si-flowkit-logo.svg" alt="spec2si-flowkit" width="480">
</p>

# spec2si-flowkit

**Spec-to-Silicon** is a headless, code-driven path from a machine-readable
spec to signoff-clean silicon: sized, placed, routed, extracted, re-simulated
and gated without a human in the loop. Each process node lives in its own
repository (a *port*); this repository holds the part that must **not**
diverge between them — three copies of a lesson is where lessons start
diverging.

| Port | Node | Where it stands |
|---|---|---|
| [`spec2si-tsmc65`](https://github.com/mandalsoumyajit/spec2si-tsmc65) | TSMC 65 nm LP | **Reference port.** The most complete flow; a signed-off chip and a signed-off ADC built through it |
| [`spec2si-tsmc28`](https://github.com/mandalsoumyajit/spec2si-tsmc28) | TSMC 28 nm HPC+/ULL | Engine ported and proven; cells and a whole PLL signed off |
| [`spec2si-xt011`](https://github.com/mandalsoumyajit/spec2si-xt011) | X-FAB XT011 PDSOI | Bring-up; blocks signed off (DRC density-only, LVS MATCH) |
| [`spec2si-sky130`](https://github.com/mandalsoumyajit/spec2si-sky130) | SkyWater SKY130 | Bring-up; see its README for what is established |
| [`spec2si-aim`](https://github.com/mandalsoumyajit/spec2si-aim) | AIM Photonics (silicon photonics) | Bring-up; a cluster-qualified photonic spec-to-score loop |

A repository is scoped to a **process**, not to a design, because that is
where the fork-forcing divergence actually falls — Calibre vs PVS/Pegasus,
PyCell vs SKILL PCells, one substrate node vs per-tub isolation. Designs are
directories *inside* a port ([ADR-0001](docs/decisions/0001-process-scoped-repos.md)).
Each port's own README says where it stands; this table is a pointer, not the
record.

## Start here

| I want to | Start at |
|---|---|
| look at results — layouts, waveforms, spectra, the design record, the cluster's trees | [browse-your-results](docs/howto/shared/browse-your-results.md) — `python browse/server.py` in any port, or `python browse/hub.py` for every port on one page |
| run a design session the way this flow is driven | [run-an-agent-session](docs/howto/shared/run-an-agent-session.md) |
| understand the method, and the rules every port signs | [the-method](docs/howto/shared/the-method.md) |
| vendor the core into a port, or read the drift gate | [vendoring](docs/howto/shared/vendoring.md) |
| stand up a new process node | [add-a-process-node](docs/howto/shared/add-a-process-node.md) |
| see what the artifact browser can do and what is still open | [the visual-interaction plan](docs/visual_interaction_plan.md), section 11 |
| pick up where the last session stopped | [RESUME.md](RESUME.md) — a `log`, so re-run the gates before citing it |

## What is shared

**100 files**, vendored byte-identically into all five ports and hash-gated —
500 checks on every `sync.py --check-all`. Each port carries them; each port
runs them from inside itself.

| What | Where (in a port) | What it is, and why it is node-agnostic |
|---|---|---|
| **The artifact browser** | `browse/` | A read-only viewer over a repo's own results: listings badged from what the flow wrote, GDS renders (local, or drawn on the cluster), waveforms, spectral responses, Markdown, the design record, the cluster's trees, and the OA/store/design/work trees told apart. Plus **the hub** (`browse/hub.py`, port 8729): every port on one page, one cluster job read for the whole machine. Stdlib-only and PDK-blind; what differs per port is its `browse/roots.json`, a declaration that is never vendored ([ADR-0004](docs/decisions/0004-browse-and-transport-vendored.md), [how to use it](docs/howto/shared/browse-your-results.md)) |
| **The agent-loop runlog** | `browse/runlog.py`, `browse/agentview.py` | One agent turn that touched a known cell is one attempt; the agent panel shows what a session is doing, including sessions started in the parent directory |
| **The cluster transport** | `deployment/bnl/jobs/` | An ssh round trip that cannot be corrupted by quoting, a host chooser, the licence-holding process scanner, the durable job workflow. A SITE fact, not a node one: every port shares one cluster ([README](jobs/README.md)) |
| **The tracked-jobs integration** | `integrations/cluster_jobs/` | The agent-side hook and project glue for the tracked job workflow ([README](integrations/cluster_jobs/README.md)) |
| **The flow policy** | `policy/flow_policy.core.json` (v1.3.0, **22 rules**) + the conformance test | Each rule is a portable *principle*; the enforcement point is not shared — see below |
| **The docmeta genre vocabulary** | `policy/docmeta.core.json` (9 genres, 5 aliases) | A genre is a **staleness contract**, and a contract shared by several repos is exactly what must not diverge |
| **The documentation system** | `docs/docmodel.py`, `docs/mdrender.py`, the Markdown, HTML and LaTeX backends, `docs/apiref.py`, `docs/test_claims.py` | Frontmatter, the genre vocabulary, a static-AST API extractor, the link, freshness and runnable-claim checks, and the renderers. A docstring is a docstring on 65 nm and on 28 nm |
| **The IR solver** | `irdrop/` | Ohms and amps in, volts out. A Spectre oppoint is a *simulator* format, not a PDK one |
| **The routing core** | `routekit/` (geometry, audit, card, rule probes, solver, electrical, GDS writer) | Rectangles in, findings and routes out; every process fact arrives through a `rules` object the consumer binds, and a missing fact is a refusal, never a default ([ADR-0002](docs/decisions/0002-routekit-vendored-core.md), [the plan](docs/routekit_plan.md), [card schema](docs/routekit_card_schema.md)) |
| **The housekeeper** | `housekeeping/` | Mtimes and filenames in, `KEEP`/`SWEEP` out, for the shared NFS home every port's trees live on ([reference](docs/housekeeping.md)) |
| **The DRC repair loop** | `drcloop/` | The signoff deck's own markers ARE the positions, so a repair answers them rather than re-deriving the violating shapes. The results-database reader was written twice, independently, for Calibre and for PVS/Pegasus, and came out the same algorithm ([ADR-0003](docs/decisions/0003-drc-in-the-loop.md), [guide](docs/drc_loop.md)) |
| **The shared how-tos** | `docs/howto/shared/` | The procedures that are true on every node; each port's own how-tos sit beside them |

Everything here is **stdlib-only and never imports the code it documents or
analyses**. That is load-bearing: it is why the docs gate and the conformance
test run in CI with no PDK, no vendor licence and no dependency install.

### Tools that live only here

| Tool | What it does |
|---|---|
| [`licenses/`](licenses/README.md) | FlexLM seat status across the cluster's licence servers, by tool family (`python3 -m licenses.cli list`) |
| [`worker/`](worker/README.md) | The bounded autonomous worker of the agentic-workflow study: implements a decided contract, gates it, and hands back a review bundle or a failure report |
| `sync.py` | The vendoring tool and drift gate — see below |

## What is deliberately not shared

**The engine.** That decision was taken after *measuring* the fork rather than
assuming it, and it has been re-measured since. On 2026-10-15 the 65 nm engine
was 144 Python files and the 28 nm one 251; **33 filenames overlap and 4 are
byte-identical** (2026-08-20: 98 and 133 files, 25 overlapping, 2 identical) —
the gap has widened in the direction the decision predicted. The divergence is real work,
not drift — Calibre vs Pegasus, PyCell vs SKILL, one substrate node vs
per-tub isolation — and forcing it into one module would produce a file of
branches nobody can reason about on any node.

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
conformance. The gap becomes a number printed on every run instead of an
absence nobody can see. Measured 2026-10-15:

```
policy conformance: spec2si-tsmc65     adoption: 13/22 enforced, 7 partial, 2 not-implemented
policy conformance: spec2si-xt011      adoption:  3/22 enforced, 7 partial, 12 not-implemented
policy conformance: spec2si-aim        adoption:  0/22 enforced, 12 partial, 10 not-implemented
```

Documentation adoption is reported beside it for the same reason, and
**never** contributes an error: a gate that fails a chip commit over a missing
`summary:` line is a gate people route around.

## Distribution: vendored, with a drift gate

Chosen over a submodule, a subtree or a package install because it costs the
ports **nothing**: a clone stays a clone, the cluster push is unchanged, and
the cluster keeps running raw system `python3` — no pip, at a 3.6.8 / 3.8.10
floor the ports' own tests enforce. The price is that real copies exist, so
the copies are hash-checked.

Check every port for drift:

```bash
python3 sync.py --check-all
```

Each port then runs its own gates **from inside that port**:

```bash
python3 policy/test_policy_conformance.py
```

```bash
python3 docs/gen.py check
```

```bash
python3 docs/test_claims.py .
```

**Never hand-edit a vendored copy.** Change it here, then re-vendor.

⛔ **`sync.py --to <repo>` overwrites without asking.** It copies the whole
vendor list unconditionally, so on a port that has drifted it discards the
local file rather than reporting a conflict — including work in flight. Safe on
a new node; on an existing one run `python3 sync.py --check <repo>` first and
resolve what it lists, or copy just the files you changed. The
[vendoring how-to](docs/howto/shared/vendoring.md) has the full procedure.

## Adding a process node

The short version; [add-a-process-node](docs/howto/shared/add-a-process-node.md)
is the full one.

1. Add the repo to `consumers.json` — recording where it **is**, never where it
   is going (an intended path makes the drift gate report `MISSING` on a healthy
   repo).
2. `python3 sync.py --to <repo>` (safe: the repo is new).
3. Write `analog/specs/flow_policy.json` in the new repo, listing every core rule
   with a status — `not-implemented` for everything not reached yet.
4. Add a `docs/gen.py` holding that repo's areas and doc-exclude list, and a
   `browse/roots.json` naming its trees.
5. Run the conformance test and the docs gate, and commit.

## Plans and studies

| Doc | What it is |
|---|---|
| [`docs/visual_interaction_plan.md`](docs/visual_interaction_plan.md) | The artifact browser's roadmap and history; section 11 is the current state and the open list |
| [`docs/routekit_plan.md`](docs/routekit_plan.md) | The shared routing core's phases |
| [`docs/em_ir_alignment.md`](docs/em_ir_alignment.md) | An operating-point-driven IR-drop calculator, and the EM/resistance misalignment across the ports it exposed |
| [`docs/runlog_first_analysis.md`](docs/runlog_first_analysis.md) | The first pass over the agent-loop runlog: **6.25 offline iterations per cluster round trip** for analog work, and the finding that session transcripts are not durable (30-day retention), so the committed log is the only record past a month |
| [`docs/agentic_workflow_report.md`](docs/agentic_workflow_report.md) | The agentic-workflow study the worker and the tracked jobs came out of |
| [`docs/agent_memory_plan.md`](docs/agent_memory_plan.md) | A measured path to long- and short-term agent memory: structured lessons plus executable gates, a small verified restore manifest, and a bounded task-specific briefing; phase 0 starts from the existing runlogs with a small cross-repo sequential sample, not a labeling census |

## Licence

**Apache-2.0** — see [LICENSE](LICENSE).

This repository is the public face of the effort by design. It is small,
stdlib-only, and touches no PDK and no vendor tool: what is here is the
METHOD and the tooling around it, usable on any process.

The process ports that consume it are **internal and all-rights-reserved**,
because a port necessarily encodes foundry NDA material. That split is
deliberate, and it is why this repository can be shared without a
confidentiality scrub of a thousand files.
