<!--docmeta
title: Operating the ASIC-DOE repository mirrors
genre: guide
status: active
area: top
owner: soumyajit
updated: 2026-09-03
summary: Source and destination mapping, synchronization triggers, manual Actions commands, independent ref verification, drift recovery and deploy-key maintenance for the five spec2si repositories. Personal repositories remain the development sources; ASIC-DOE holds reader copies.
-->

# Operating the ASIC-DOE repository mirrors

Development occurs in `mandalsoumyajit/spec2si-*`; GitHub Actions copies Git data
one way into `ASIC-DOE/spec2si-*`. The rationale is recorded in
[ADR-0005](decisions/0005-asic-doe-repository-mirrors.md).

This is repository mirroring. It is separate from the root `sync.py` tool that
distributes selected flowkit files into process-port repositories.

## Repository map and schedule

Each source and destination has the same repository name.

| Repository | Personal source | ASIC-DOE destination | Scheduled UTC times |
|---|---|---|---|
| `spec2si-flowkit` | Public | Public fork | 00:07, 06:07, 12:07, 18:07 |
| `spec2si-sky130` | Private | Internal standalone repository | 00:10, 06:10, 12:10, 18:10 |
| `spec2si-tsmc28` | Private | Internal standalone repository | 00:13, 06:13, 12:13, 18:13 |
| `spec2si-tsmc65` | Private | Internal standalone repository | 00:16, 06:16, 12:16, 18:16 |
| `spec2si-xt011` | Private | Internal standalone repository | 00:19, 06:19, 12:19, 18:19 |

Internal means readable throughout the enterprise containing ASIC-DOE, not only
by ASIC-DOE members. No BNLAIO mirrors are configured by this procedure.

The workflow is named **Sync ASIC-DOE mirror**, with these files in each
personal repository:

- `.github/workflows/sync-asic-doe.yml`: triggers, authentication and concurrency.
- `.github/scripts/sync_asic_doe.py`: ref comparison, Git/LFS transfer and verification.
- `.github/SYNC.md`: repository-specific operating notes.

The deployed flowkit versions are available as
[workflow](https://github.com/mandalsoumyajit/spec2si-flowkit/blob/main/.github/workflows/sync-asic-doe.yml),
[script](https://github.com/mandalsoumyajit/spec2si-flowkit/blob/main/.github/scripts/sync_asic_doe.py),
and [notes](https://github.com/mandalsoumyajit/spec2si-flowkit/blob/main/.github/SYNC.md).
An older local checkout may not yet contain those files; this guide does not
require altering its uncommitted work to inspect the deployed configuration.

## Normal development

1. Work, commit and push to the personal repository using its existing `origin`.
2. Open and merge development pull requests in the personal repository.
3. Inspect **Actions → Sync ASIC-DOE mirror** in that source repository when
   confirming delivery to ASIC-DOE. A green run includes a summary with branch
   and tag counts, a verification timestamp and the verified `main` commit.

The workflow listens for push, create and delete events, supports manual
dispatch, and reconciles every six hours. Older branches without the workflow
file may not trigger a push run; the periodic run still includes their refs.
GitHub can delay scheduled jobs, so the schedule is not a maximum-lag guarantee.
Use manual dispatch when an organizational reader needs an immediate update.

Do not develop directly in the destination. Branch and tag rules named
`Mirror branches: updates through synchronization only` and
`Mirror tags: updates through synchronization only` restrict writes to deploy
keys. Actions remains disabled in the destinations; the jobs run in the personal
repositories. Flowkit uses this automation even though its destination is a fork.

## Run and inspect synchronization manually

Use the source repository's Actions page and select **Run workflow** on `main`,
or use GitHub CLI with access to the source. In PowerShell:

```powershell
$repoName = 'spec2si-flowkit' # Change to another name from the table above.
$sourceRepo = "mandalsoumyajit/$repoName"
gh workflow run sync-asic-doe.yml --repo $sourceRepo --ref main
gh run list --repo $sourceRepo --workflow sync-asic-doe.yml --limit 5
```

Dispatch returns before the run finishes. Select the intended run from that
list, then inspect or watch it:

```powershell
$runId = 123456789 # Replace with the actual run ID from gh run list.
gh run watch $runId --repo $sourceRepo --exit-status
gh run view $runId --repo $sourceRepo --log-failed
```

Runs for a given repository are serialized. An in-progress run is not cancelled
by a later push, and each run reconciles current source state rather than only
the commit that triggered it. GitHub can replace pending queued runs; the
remaining run still reads current state.

## Verify drift independently

For a read-only branch/tag comparison, authenticate with access to both copies
and run the following in PowerShell. It changes neither repository nor local
Git configuration:

```powershell
$repoName = 'spec2si-flowkit'
$sourceRefs = @(git ls-remote --refs --heads --tags "https://github.com/mandalsoumyajit/$repoName.git" | Sort-Object)
if ($LASTEXITCODE -ne 0) { throw 'Could not read source refs' }
$mirrorRefs = @(git ls-remote --refs --heads --tags "https://github.com/ASIC-DOE/$repoName.git" | Sort-Object)
if ($LASTEXITCODE -ne 0) { throw 'Could not read mirror refs' }
$differences = @(Compare-Object $sourceRefs $mirrorRefs)
if ($differences.Count -gt 0) {
    $differences
    throw 'Source and mirror refs differ; inspect the sync run'
}
'All branch and tag IDs match'
```

In `Compare-Object` output, `<=` identifies a source-side entry and `=>` a
destination-side entry. A moved ref can produce one of each. This comparison
checks names and exact object IDs, including annotated tag objects. It does not
check GitHub metadata or independently download LFS content. A source push
during the comparison can also produce a temporary difference; wait for its
sync run and compare again.

## What the workflow guarantees

1. Read the current source into a disposable bare clone. Load the sync script
   from the source's current `main`, including when another branch triggered it.
2. Compare all source and destination branches and tags. Fetch destination
   objects into a separate local namespace for ancestry checks.
3. Refuse destination-only refs, changed tags and branch updates that would
   discard destination history. This preflight failure pushes no refs.
4. Fetch and push all LFS objects when the repository contains LFS entries,
   before publishing updated branches or tags.
5. Push the changed refs atomically, without force or deletion. Git rejects a
   conflicting concurrent update instead of partially accepting the ref set.
6. Read destination refs back and require exact equality with the source
   snapshot. If the source advanced during the run, reconcile again, up to
   three attempts, then report failure if a stable comparison is not possible.

The `+` refspecs and pruning in the script apply only to the disposable local
clone, not to destination pushes. LFS uploads are separate from Git's atomic ref
transaction: uploaded objects can remain even if a subsequent ref push fails.
Neither issues, pull requests, release assets, settings nor secrets are mirrored.

## Failure handling

| Symptom | Procedure |
|---|---|
| Network error or interrupted run | Confirm connectivity and rerun. Already-matching refs are safe to revisit. |
| Destination-only ref | Determine whether it is an intentional source deletion or work created in ASIC-DOE. Preserve unique work before deciding which ref should exist. |
| Diverged branch or rewritten source history | Inspect both histories. Bring legitimate destination work into the personal source through normal review, or explicitly approve a targeted history reconciliation. |
| Changed tag | Decide which release object is authoritative. Prefer a new tag when possible; replacement is never automatic. |
| Source kept changing during verification | Allow activity to settle and rerun; inspect the next successful verification. |
| SSH permission or key-format error | Check the matching source secret and destination deploy key. The workflow removes Windows carriage returns from the secret and appends a newline before loading it on Linux. |
| SSH host-key verification error | Verify GitHub's current published SSH host keys before updating the pinned key. Do not disable host-key checking. |
| Enterprise rule rejects a push | Read the rule failure and resolve the source change or involve the policy administrator. The mirror's deploy-key exception does not override enterprise policy. |
| LFS transfer fails | Check source read access, destination key access and LFS availability. Rerun after repair; ref publication follows successful LFS transfer. |
| No scheduled runs | Check that the source workflow is active and Actions is enabled. Public repository schedules can be disabled after 60 days without activity. Manually dispatch after re-enabling. |

Do not fix failures with a blanket `git push --mirror`, force-push or prune.
For an intentional deletion or history replacement, record the exact repository,
ref, expected old ID and intended new state; preserve any unique history; arrange
targeted administrative reconciliation; restore normal restrictions; then rerun
the workflow and verify equality. The normal sync job has no destructive mode.

Failures appear in source Actions. Enable failed-workflow notifications in your
GitHub notification settings if desired; this setup does not create a separate
email, chat or incident notification service.

## Credentials and maintenance

Each destination has one write deploy key titled
`Sync from mandalsoumyajit/<repository>`. Its private half is stored as
`ASIC_DOE_SYNC_SSH_KEY` only in that matching personal repository's encrypted
Actions secrets. The read-only source `GITHUB_TOKEN` is generated by GitHub for
the job. The developer's CLI token, including its deletion permission, is not
stored in the workflow.

The destination rule exception applies to deploy keys as a class, not a specific
key ID. Adding another write deploy key expands who can update the mirror.
Repository administrators can also change these rules. Keep key and rule
administration deliberate.

To rotate a key:

1. Pause the source sync workflow and let any in-progress run finish.
2. Generate a new dedicated SSH key pair. Add its public key to the matching
   ASIC-DOE repository with write access; keep the old key temporarily for rollback.
3. Replace `ASIC_DOE_SYNC_SSH_KEY` in the matching source with the new private key
   using GitHub's encrypted secret controls. Never commit or print private keys.
4. Re-enable the source workflow, manually dispatch it and verify a successful
   run and matching branch/tag IDs.
5. Revoke the old destination deploy key and remove temporary private-key files
   from the machine used for rotation.

Deploy keys do not expire automatically. Revoke a destination key and remove its
matching source secret when retiring synchronization. Removing a person's
organization membership alone does not revoke deploy keys.

The workflow and script are currently copied into all five personal repositories;
they are not distributed by the root flowkit `sync.py`. When changing this
mechanism, test the change, update all five copies while preserving each source
guard, destination and schedule, and verify a successful run for every repository.
Update this guide and the per-repository `.github/SYNC.md` notes if behavior changes.

## Rollout evidence and references

The 2026-09-03 setup finished with successful runs and exact ref comparisons for
all five repositories. See the dated evidence in [ADR-0005](decisions/0005-asic-doe-repository-mirrors.md).
For current status, use each source repository's Actions page rather than that
historical verification.

- [GitHub workflow triggers and scheduled-run behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [GitHub deploy-key management](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
- [Git push ref and atomic-update semantics](https://git-scm.com/docs/git-push)
