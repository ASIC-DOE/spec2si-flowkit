<!--docmeta
title: The method — stages, verdicts, and the rules every port signs
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-09-11
summary: What Spec-to-Silicon actually is, independent of node: a staged pipeline with hard verdicts and content-hash caching, gated in two tiers, under a shared rule set that each port declares a status against. Points at the machine-readable policy rather than restating it.
-->

# The method

The product here is the **method and its docs**, not a package: most of the
code cannot run outside a site that licenses the tools. What follows is the
part that transfers.

## The shape

A circuit goes from a machine-readable spec to an extracted, re-scored layout
through named stages:

```
char -> constraints -> place -> route -> pex [-> corners]
```

Three properties make that a flow rather than a sequence of scripts.

**Verdicts are hard.** A stage passes **iff its process exits 0**. The runner
stops at the first failure and exits non-zero. There is no partial credit and
no warning tier, because the failure this design is built against is a step
that cannot produce its deliverable and *narrates* instead of failing — after
which a green gate covers a wrong artifact.

**Staleness is computed, not remembered.** Every stage declares its input
files; the runner hashes them with the circuit name and the stage arguments,
and skips a stage whose hash matches its last passing run *and* whose outputs
still exist. Because downstream stages hash their upstream artifacts,
invalidation chains by itself — so nobody reasons about staleness by hand.

**Derived, never authored beside.** A value that was correct for the geometry
that existed when it was written, and that no script reproduces, survives
exactly until the thing is re-spun. Interfaces, geometry constants and
coverage sets are computed from the artifact they describe.

## Two tiers, and why

Gates are split by what they need:

| tier | needs | costs | runs |
|---|---|---|---|
| offline | stdlib python, a clone | milliseconds | every edit, every PR |
| site | the PDK, the EDA tools, the cluster | minutes to hours | when the offline tier is green |

The offline tier exists so the expensive one is entered with a specific
question. Its job is not to approve the design; it is to make the site run
the *only* remaining unknown.

## The shared rules

Every port signs up to one rule set. It is machine-readable and it is the
authority — this page will not restate it, because a restatement is exact the
day it is written and diverges silently after:

- **`policy/flow_policy.core.json`** — 22 core rules, each with an `id`, a
  `principle`, and the version it arrived in.
- **that port's `analog/specs/flow_policy.json`** — a status for *every* core
  rule id, `not-implemented` included. A port that has not reached a rule
  says so; it does not omit it.
- **the conformance gate** that the two agree, vendored into every port as
  `policy/test_policy_conformance.py`.

Deviating from a rule needs a **waiver with a cited measurement**, released in
the artifact. Not an argument: a number.

⭐ If you read only three of the rules, read these, because every other rule
in the file is downstream of them:

| rule | what it demands |
|---|---|
| `negative-control` | a gate written for a "lost or stale artifact" class must be **made to fail** — remove what it protects and assert the original failure returns. A good-path assertion passes against a stale hand-edit too. |
| `evaluated-set` | the evaluated set is part of the verdict. A report says what it actually scored, so a missing spec item cannot make a gate pass *more easily* and say nothing. |
| `iterate-at-failing-tool` | debug a failing gate by re-running **that gate** in its work dir, never by re-running the pipeline. |

## How this fails, when it fails

Every recurring defect in this project has had one shape: **a green gate over
a wrong artifact.** The variants are worth knowing by name, because the fix
differs:

- the gate scored a **replica** of the engine rather than the engine;
- the gate's fingerprint was a **census of counters**, which cannot see a
  change that moves no counter;
- the gate ran over a corpus that **silently excluded** the thing under test;
- the gate's input was **regenerated in place**, so a recorded failure stopped
  reproducing and nobody noticed;
- the environment the gate ran in **lacked a variable**, so the correct answer
  and the broken one produced identical output.

The common defence is the same in all five: a gate takes an **independent**
measurement of the built result, and is believed only after it has been
observed to fail.

## Where to go next

| you want | page |
|---|---|
| to get the shared core into a repo | [vendoring](vendoring.md) |
| to stand up a new node | [add-a-process-node](add-a-process-node.md) |
| to actually run something | your port's own how-to index — the commands name that port's tools |
