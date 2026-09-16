# ASIC-DOE synchronization

Development and pull requests belong in `mandalsoumyajit/spec2si-flowkit`.
`ASIC-DOE/spec2si-flowkit` is a one-way copy for readers.

The **Sync ASIC-DOE mirror** Actions workflow copies all branches, tags and Git
LFS objects. It runs on push/create/delete events, on manual dispatch, and every
six hours at minute 7 UTC. GitHub can delay scheduled runs. Older branches
without this workflow file are covered by the scheduled reconciliation; use
Actions → Sync ASIC-DOE mirror → Run workflow for an immediate update.

Every run reads the synchronization script from the current personal `main`
branch, compares branch/tag IDs, transfers LFS objects before publishing refs,
and checks the destination again afterward. Concurrent runs are serialized.
Source changes during a run are retried up to three times.

The workflow refuses destination-only refs, diverged branches, and changed tags.
It does not delete destination refs or force-push. A source branch/tag deletion
or rewritten history therefore requires deliberate manual reconciliation. A
failed preflight pushes no refs; Git pushes are atomic across updated refs.
The Actions run summary reports the result and verified `main` commit.

Destination branch and tag rules restrict changes to deploy keys. Each
destination has its own write deploy key; its private half exists only in the
matching personal repository's encrypted `ASIC_DOE_SYNC_SSH_KEY` Actions secret.
The source `GITHUB_TOKEN` has read-only contents permission. GitHub Actions is
disabled on the destination to prevent duplicate CI and sync loops. Existing
enterprise restrictions still apply.

To investigate a failure, inspect the Actions run first. Resolve legitimate
changes in the personal repository, then run the workflow again. Do not resolve
drift by blindly force-pushing. Repository admins can manage mirror rules when
intentional deletion or history reconciliation is needed.

The workflow copies Git data, not GitHub issues, pull requests, release assets,
or settings. Enable failed-workflow notifications in your GitHub notification
settings if desired. Public repositories can have schedules disabled after
60 days without activity; check the workflow status when resuming an inactive
project. Deploy keys do not expire automatically; rotate the matching deploy
key and Actions secret together when necessary.
