<!--docmeta
title: ADR-0005 — personal development repositories with one-way ASIC-DOE mirrors
genre: decision
status: accepted
area: top
owner: soumyajit
updated: 2026-09-03
summary: Keep development under mandalsoumyajit and expose synchronized copies in ASIC-DOE. Use a public fork for flowkit and standalone internal repositories for the four private process ports. Dedicated deploy keys, destination write restrictions, atomic non-forced updates and ref verification make drift visible without discarding changes.
-->

# ADR-0005 — personal development repositories with one-way ASIC-DOE mirrors

**Status:** accepted · 2026-09-03.

## Context

The five spec2si repositories need an organizational presence while development
continues under `mandalsoumyajit`. `spec2si-flowkit` is public; the four process
ports are private. The requested audience for the process-port copies is the
enterprise containing ASIC-DOE. BNLAIO copies are deferred.

GitHub forks retain the visibility of their upstream repository network. A fork
of a private personal repository cannot independently become internal. GitHub
Pro on the personal account does not change this restriction. ASIC-DOE supports
internal repositories, whose contents are readable by all members of its
enterprise, including members outside ASIC-DOE itself.

The requirement is therefore both an ownership decision and a synchronization
decision. Forking alone would not keep the organizational copies current.

## Decision

1. Keep `mandalsoumyajit/spec2si-*` as the development sources. Local `origin`
   remotes, commits, pull requests and issue discussions remain centered there.
2. Keep `ASIC-DOE/spec2si-flowkit` as a public fork. Make `spec2si-sky130`,
   `spec2si-tsmc28`, `spec2si-tsmc65` and `spec2si-xt011` standalone internal
   repositories in ASIC-DOE. Personal source visibility remains unchanged.
3. Run the same one-way synchronization design from each personal repository.
   Copy every source branch, tag and reachable Git LFS object. Do not synchronize
   GitHub issues, pull requests, release assets, settings or credentials.
4. Use a separate write deploy key for each destination and keep its private half
   in the matching source repository's encrypted Actions secret. The workflow's
   source `GITHUB_TOKEN` needs only `contents: read`; do not reuse the developer's
   broad GitHub CLI token in automation.
5. Restrict destination branch/tag creation, updates and deletion to deploy keys.
   Disable destination Actions to prevent duplicate CI and synchronization loops.
   These repository rules do not bypass existing enterprise policies.
6. Accept only new refs and fast-forward branch updates automatically. Refuse
   destination-only refs, changed tags and non-fast-forward branch updates. Push
   refs atomically, then verify exact source/destination ref IDs. Preserve work
   and report drift instead of forcing the destination to match.

## Why this design

- **One development source avoids competing histories.** ASIC-DOE provides
  access to the code without becoming a second place to merge development.
- **Standalone internal copies meet the access requirement.** Retaining private
  forks would not provide enterprise-wide read access; making personal sources
  public would expose them beyond the intended audience.
- **Explicit ref updates bound the effect of synchronization.** A blanket
  `git push --mirror` can overwrite or delete destination refs. That is too
  destructive for unattended synchronization when the concern is drift.
- **Dedicated deploy keys make the current setup manageable.** Each key is
  scoped to one destination. A GitHub App would offer shorter-lived credentials
  and finer permission control, but requires separate registration and
  installation. Deploy keys were explicitly approved for this five-repository
  setup; they require deliberate rotation and revocation.
- **Push triggers plus periodic reconciliation cover different failures.**
  Pushes normally update the mirror promptly. Six-hour scheduled runs catch
  missed events and older branches without the workflow while limiting scheduled
  Actions usage. This is eventual synchronization, not an instantaneous guarantee.

## Consequences and evidence

Deleting or rewriting source history can intentionally stop synchronization
until an administrator reconciles it. A failed preflight publishes no refs;
network, credential or policy failures also appear in Actions. Repository admins
can change rules or install additional deploy keys, so the write restrictions
prevent routine divergence rather than eliminating every possible change.

The four original private forks were kept temporarily as archived backups during
conversion, then deleted with owner approval to avoid confusing duplicate names.
Their standalone internal replacements retain Git history and LFS data, but not
the GitHub fork relationship.

On 2026-09-03 all five live sync workflows succeeded and all 49 branch/tag refs
matched their sources. The XT011 mirror's 13 LFS objects were also independently
downloaded and hash-verified during mirror creation. Local safety tests covered
normal updates, new branches/tags, divergent commits, rewritten history, source
deletion and changed tags. These are rollout observations, not ongoing status.

See the [operating guide](../asic_doe_sync.md) for procedures and failure handling.
This GitHub mirroring mechanism is separate from flowkit's vendored-core
distribution through `sync.py`.

## GitHub references

- [Fork visibility and permissions](https://docs.github.com/en/pull-requests/reference/forks)
- [Internal repository visibility](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
- [Deploy keys and GitHub App alternatives](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
