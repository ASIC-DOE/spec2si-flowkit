<!--docmeta
title: How to vendor the shared core, and read the drift gate
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-10-15
summary: Real copies of the node-agnostic core live in every port and are hash-checked. How to check for drift, how to update one port safely (copy the files you changed; `--to` only on a new port), what DRIFTED and MISSING mean, what to regenerate afterwards, and how to add a file to the shared set.
-->

# How to vendor the shared core

The node-agnostic files — the flow policy, the genre vocabulary, the
documentation system and its gates, the artifact browser and its hub, the
cluster transport, the IR solver, the routing core, the DRC loop, the
housekeeper and these shared how-tos — live in **spec2si-flowkit** and are
copied into each port. `sync.py`'s `FILES` list is the authoritative set.

Vendoring was chosen over a submodule or a package install because it costs
the consumers nothing: a clone stays a clone, the cluster rsync is unchanged,
and the cluster keeps running raw `python3` at the 3.6.8 / 3.8.10 floor its
own tests enforce, with no pip. The price is that real copies exist — so the
copies are hash-checked, the same shape as every other derived artifact here.

**Paths.** `sync.py` runs from the flowkit checkout. Use native Windows
Python with forward-slash paths (`C:/dev/spec2si-<node>`) or WSL with
`/mnt/c/dev/spec2si-<node>`. Never type an unquoted `C:\dev\...` into bash —
it loses its backslashes.

## 1. Ask whether anything drifted

Every registered port:

```bash
cd C:/dev/spec2si-flowkit
python3 sync.py --check-all
```

One port:

```bash
cd C:/dev/spec2si-flowkit
python3 sync.py --check C:/dev/spec2si-<node>
```

Each file reports one of three states:

| state | means | what to do |
|---|---|---|
| `ok` | byte-identical to the flowkit | nothing |
| `DRIFTED` | the copy was changed in place | recover the change into the flowkit, then re-vendor — never keep it local |
| `MISSING` | the port never received this file | vendor it, **or** decide the port does not take that group yet |

⛔ **Never hand-edit a vendored copy.** Change it in the flowkit, re-vendor,
and let each port decide whether its status for the changed rule still holds.
A local edit is exact the day it is made and diverges silently after, which is
the whole reason the hash gate exists.

## 2. Update an existing port: copy the files you changed

The safe way to land a flowkit change in a port is to copy exactly the files
you changed — one `cp` per file per port — and then run the port's own
tests and the drift gate:

```bash
cp browse/server.py C:/dev/spec2si-<node>/browse/server.py
```

```bash
cd C:/dev/spec2si-flowkit
python3 sync.py --check-all
```

Check `git status` in the port first: a file that is already modified there
is someone's work in flight, and is resolved before it is overwritten.

**Afterwards, regenerate the port's docs.** A re-vendored module changes the
port's generated API reference and index: run `python docs/gen.py build` in
the port and commit the regenerated lines that came from your change with
it (a port with a pre-commit docs gate refuses the commit otherwise).

## 3. A new port: `--to`

```bash
cd C:/dev/spec2si-flowkit
python3 sync.py --to C:/dev/spec2si-<node>
```

`--to` refuses a destination that is not an existing git checkout.

## ⛔⛔ The trap: `--to` overwrites, and it does not ask

`--to` copies the **whole** file list unconditionally. On a port that has
drifted it therefore **discards** the local version rather than reporting a
conflict — including work someone else has in flight.

This matters more than it sounds, because the drift gate's own failure
message tells you to run exactly that command:

```
N file(s) drifted or missing -- re-vendor with `sync.py --to <repo>`
```

So:

- on a **new** node, `--to` is safe — nothing is there to lose;
- on an **existing** one, run `--check <repo>` first and resolve what it
  lists. If you only changed one file, copy that one file rather than
  running the whole list.

⚠ `consumers.json` records **local workstation paths**, so none of this runs
in CI. The drift gate is a developer command, on purpose.

## Adding a file to the shared set

Add the `(source, destination)` pair to `FILES` in `sync.py`, **with a comment
saying why it is node-agnostic.** The bar is not "both repos have one" — it is
that the file cannot tell which node it is on. The IR solver qualifies: ohms
and amps in, volts out. A rule deck does not.

Then vendor it and confirm `--check-all` reports `ok` for the new entry
without changing the count of anything else.
