<!--docmeta
title: How to add a process node
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-09-11
summary: Five steps to stand up a new process port: register it, vendor the shared core, declare a status for every policy rule, add a docs config, and run the two gates.
-->

# How to add a process node

⛔ **VENDORED.** This page lives in spec2si-flowkit and is copied
byte-identically into every port. Edit it there.

A repository is scoped to a **process**, not to a design — designs are
directories inside it. Standing up a new one is five steps, and the whole
point of the first four is that step five gives you a number instead of an
impression.

## 1. Register where the repo *is*

Add it to `consumers.json` in
[spec2si-flowkit](https://github.com/mandalsoumyajit/spec2si-flowkit).

⛔ **Record where the repo IS, never where it is going.** A registry naming
an intended path makes the drift gate report `MISSING` on a healthy repo —
the cry-wolf failure the file's own note exists to prevent. Move first,
register second.

## 2. Vendor the shared core

`sync.py` lives in the flowkit and vendors *out* of it, so run it from
there — there is no copy of it in a port:

```bash
cd C:\dev\spec2si-flowkit
python3 sync.py --to C:\dev\spec2si-<node>
```

That copies the node-agnostic files byte-identically: the flow policy and
its conformance test, the docmeta genre vocabulary, the documentation model
and its backends, the runnable-claim gate, the IR solver, the routing core,
the DRC loop, the housekeeper, the agent-loop runlog, the artifact browser
and the cluster transport it reads through.

**The browser is onboarded by one file, not by code.** Write
`browse/roots.json` naming the trees worth listing (and, if the port's
renderer is not at the engine's conventional location or takes a different
argument order, declare it there); `python browse/server.py` then serves
the new port. `analog/specs/runlog_cells.txt` declares the cells the
runlog should count when the tree does not name them, and
`.claude/settings.json` installs the SessionEnd harvest — copy a sibling's.
[browse-your-results](browse-your-results.md) has the details.

⛔ **Never hand-edit a vendored copy.** Change it in the flowkit, re-vendor,
and let each port decide whether its status for the changed rule still
holds. `sync.py --check-all` is the hash gate that catches the alternative.

⛔⛔ **`--to` OVERWRITES, and it does not ask.** It copies the whole file
list unconditionally, so on a port that has drifted it discards the local
version rather than reporting a conflict — including work someone else has
in flight. That matters because the drift gate's own failure message tells
you to run exactly this command. On a NEW node it is safe (nothing is
there yet); on an existing one, run `sync.py --check <repo>` first and
resolve what it lists, or copy the single file you actually changed.

## 3. Declare a status for every core rule

Write `analog/specs/flow_policy.json` listing **every** core rule id with a
status: `enforced`, `partial`, `not-implemented`, or `waived` (with a
reason). Its own wording, its own enforcement path, its own evidence.

⭐ **`not-implemented` is a passing state.** A bring-up port declaring most
of the set unimplemented passes conformance. That is the design: the gap
becomes a number printed on every run instead of an absence nobody can see.

## 4. Add a docs configuration

Copy a sibling's `docs/gen.py`. It holds only what is local — the areas and
their globs, the doc-exclude list, the titles. The model, the Markdown
backend, the web backend and the PDF backend all come from the flowkit.

⛔ Do not add rendering to it. If a port's `gen.py` starts formatting
Markdown, the ports have begun to diverge again and the next shared backend
has three things to keep in step instead of one.

## 5. Run the gates and commit

From inside the new port:

```bash
python3 policy/test_policy_conformance.py
python3 docs/gen.py check
python3 docs/test_claims.py .
```

The third is the runnable-claim gate: it asks whether the paths and
flags your prose names still exist. It gates `guide` and `overview`
and leaves the genres the vocabulary allows to age advisory.

## What does *not* get shared

The engine. That was decided by measuring the fork, and re-measuring it
since: as of 2026-08-20 the 65 nm engine is 98 Python files and the 28 nm
one 133, **25 filenames overlap and exactly two are byte-identical**, and
`netlist_route.py` differs by 2,671 lines. The divergence is real work —
Calibre versus Pegasus, PyCell versus SKILL, one substrate node versus
per-tub isolation — and forcing it into one module produces a file of
branches nobody can reason about on any node.

Enforcement paths, evidence and wording stay local for the same reason: a
shared file claiming a Calibre parser runs on a Pegasus node would be a lie
with a hash on it.
