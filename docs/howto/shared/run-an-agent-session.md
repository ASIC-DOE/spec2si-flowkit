<!--docmeta
title: How to run an agent-executed design session
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-09-11
summary: The session loop this flow is actually driven by — where a session starts, why the offline tier comes first and what the measured ratio is, what a session must leave behind so the next one can start, and which decisions are never the agent's.
-->

# How to run an agent-executed design session

Most of this flow is executed by an **agent** — a model driving the same
scripts a person would, in a session that starts, does work, and ends. That is
not a detail of how the repo happens to be maintained; it is the thing the
gates, the logs and the restore points are all shaped around, and it is the
part newcomers most often miss.

This page is the loop. Your port's own how-to index has the commands, because
the tools differ by node.

## The shape of a session

```
read the restore point
        │
        ▼
  ┌──────────────┐   fails    ┌──────────────────────┐
  │ OFFLINE tier │ ─────────▶ │ fix, re-run THAT gate│
  │ milliseconds │ ◀───────── │ not the pipeline     │
  └──────┬───────┘            └──────────────────────┘
         │ all green
         ▼
  ┌──────────────┐
  │  SITE tier   │  the PDK, the tools, the cluster — minutes to hours
  └──────┬───────┘
         │
         ▼
 record  →  commit  →  leave a restore point
```

## 1. Start at the restore point, not at the code

Every repo keeps one document that says where the work stands and what to do
next. It is filed as a **`log`**, and that genre means exactly what it says:
*a disposable snapshot, explicitly allowed to be stale.*

⛔ **So re-run the gates before trusting any number in it.** A restore point
is where to START, never what to CITE. Your port's how-to index names the file.

Read the repo's standing instructions too, where it has them — the rules that
are true in every session and that nobody wants re-derived each time.

## 2. The offline tier first — and this is measured, not a preference

The gates split by what they need:

| tier | needs | costs | run it |
|---|---|---|---|
| offline | stdlib python, a clone | milliseconds | every edit |
| site | PDK, EDA licences, the cluster | minutes to hours | when offline is green |

The offline tier is not a smoke test. Its job is to make the expensive tier
run **the only remaining unknown**, and the agent-loop runlog exists to keep
that claim honest rather than remembered. Measured on the 65 nm port:

> **247 attempts across 85 sessions — 189 offline, 58 cluster.** Per cell the
> ratio is the number that matters: `bandgap` 58 offline to 1 cluster,
> `ota` 11 to 3, `vref` 24 to 8.

⭐ That ratio is the whole argument for the architecture. A session that goes
to the cluster with a question the offline tier could have answered is the
failure this design exists to prevent — the runlog's own docstring records the
case it was written from: *three sessions of cluster round trips* that failed
to close a marker which was then reproduced offline in milliseconds.

## 3. Verdicts are hard, and you debug at the failing tool

A stage passes **iff its process exits 0**. There is no warning tier, because
the failure mode this is built against is a step that cannot produce its
deliverable and *narrates* instead of failing.

⛔ When a gate fails, re-run **that gate in its work directory** — never the
pipeline. Re-running the pipeline to debug one step is the single most
expensive habit available here, and the core rule `iterate-at-failing-tool`
exists to name it.

## 4. Believe a gate only after you have made it fail

Before a green gate means anything, mutate what it protects and watch it go
red. A good-path assertion passes against a stale hand-edit too, which is how
a gate can sit green over a wrong artifact for weeks. The core rule is
`negative-control`; several gates here carry a `--self-test` that does this to
themselves.

## 5. What a session must leave behind

A session is not finished when the work is done. It is finished when the next
session can start, and when what happened is recorded where it can be counted:

| leave | why |
|---|---|
| **provenance** in every report | PDK variant, rule-deck hashes, tool versions — *and the agent identity*. `model-is-a-variable`: the model that did the work is recorded like any other variable, or a result cannot be attributed |
| **a verification record** | each DRC/LVS verdict as one observation, keyed by deck hash so a PDK bump partitions the data instead of poisoning it |
| **a runlog attempt** | one record per turn that touched a known cell, tier included. The skeleton is harvested automatically where a session hook is wired; the terminal **cause is DECLARED**, never inferred — inferring it would be inventing data |
| **an updated restore point** | the next session reads this first |
| **a commit** | with what was measured, not only what changed |

⚠ **The runlog's cause field is the one that rots.** On the 65 nm port all 247
attempts are currently `unclassified`, which means the loop's *shape* is
counted and its *outcomes* are not. Classifying is a declaration a human or
agent has to make; nothing can derive it.

## 6. What a good prompt asks for

The difference between a prompt that costs one run and one that costs five is
usually not detail — it is asking for the **judgement** rather than the dump,
and saying what to do when something fails.

| instead of | ask |
|---|---|
| "show me the corner results" | "which corner is worst, and by how much" — you get the answer *and* the table as evidence |
| "optimize it" | "…and tell me the residual at the chosen value" — a loop that *stopped* and a loop that *converged* look identical without it |
| "run the flow" | "if a stage fails, stop there and show me that stage's log rather than re-running the pipeline" — this is `iterate-at-failing-tool`, and it is the most expensive habit to get wrong |
| "is it clean?" | "what did this change ADD over the same object before the change" — a total is not a result |

⭐ **Name the acceptance check in the prompt.** A request with no stated
"done" invites a narrative; a request that says what would count as success
gets a verdict you can act on.

⚠ Your port's how-to index may carry a **worked smoke test** — the shortest
real thing the flow does, with the prompts that drive it. Start there if you
want to see the shape rather than read about it.

## 7. What is never the agent's call

| decision | whose |
|---|---|
| widening a task's scope beyond what was asked | the person who asked |
| accepting a waiver against a core policy rule | needs a cited measurement, released in the artifact |
| discarding another session's work in a shared tree | the owner of that work |
| a rename, a migration, or anything that moves an interface other repos cite | decided, then recorded as a decision |

⭐ And the standing habit that prevents most of the damage: **read the staged
list before committing, not after.** These trees frequently hold more than one
session's work.

## Where to go next

| you want | page |
|---|---|
| the flow's shape and the shared rule set | [the-method](the-method.md) |
| the commands for your node | your port's how-to index |
| to get the shared core into a repo | [vendoring](vendoring.md) |
