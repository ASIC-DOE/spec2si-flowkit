<!--docmeta
title: Shared how-to index — the node-agnostic half
genre: overview
status: active
area: top
owner: soumyajit
updated: 2026-09-11
summary: The task-shaped pages that are true on every process node, vendored byte-identically into each port. Links out only; anything that names a tool, a deck or a PDK path belongs in the port's own how-to index beside this one.
-->

# Shared how-to index

These pages are **vendored from spec2si-flowkit** and are byte-identical in
every port. They cover the half of the method that does not change when the
node does: what a stage is, what a verdict means, how the shared core gets
into a repo, and how a new node is stood up.

⛔ **Never edit these here.** Change them in the flowkit and re-vendor;
`sync.py --check` is the hash gate that catches the alternative.

| you want to | page |
|---|---|
| understand the flow's shape and the rules every port signs up to | [the-method](the-method.md) |
| run a design session the way this flow is actually driven | [run-an-agent-session](run-an-agent-session.md) |
| vendor the shared core, or read the drift gate | [vendoring](vendoring.md) |
| stand up a new process port | [add-a-process-node](add-a-process-node.md) |

## What is deliberately NOT here

Anything that names a tool, a rule deck, a PDK path or a design. Those differ
by node and belong in the port's own how-to index — the divergence is real
work, not duplication, and ADR-0001 records why the repos are process-scoped
because of it:

- Calibre on the TSMC ports, Pegasus on SKY130
- PyCell versus SKILL device generators
- Spectre model paths, corner names, rule-deck hashes
- the designs themselves

So a port's index lists **both**: these shared pages, and its own.

## Where the authority actually lives

None of these pages restate a rule, a signature or a number. Each points at
the machine-readable thing instead, so a page here can never disagree with
the code:

| for | read |
|---|---|
| the rules themselves | `policy/flow_policy.core.json` — 22 core rules, each with an `id`, a `principle` and the version it arrived in |
| what a genre promises about staleness | `policy/docmeta.core.json` |
| this port's status for each core rule | that repo's `analog/specs/flow_policy.json` |
| every command and symbol | the generated **Commands** and **API** chapters |
