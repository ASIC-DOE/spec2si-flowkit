# WP3 — durable task-to-job linkage

**WP4 update:** [strict collection](job_tracker_wp4.md) now uses these durable
identities to validate normalized reports and writes collection JSON/Markdown.
The statements below about future WP4 describe the original WP3 boundary.

Implemented in flowkit on 16 September 2026. `jobs/state.py` stores private local
task pointers; `jobs/workflow.py` now requires durable state for CLI starts.
The existing tracker remains the authority for job status, events and artifacts.
No tracker shell scripts, consumers or installed harness settings were changed.

## Use

Choose a private **local** state directory outside the source checkout and retain
it across worktrees and chat sessions. Use one stable task key for each logical
request. The same store and key must be used by all cooperating callers.

```sh
python3 -m jobs.workflow start --profile /private/synthetic.json \
  --state-dir /private/job-state --task-key smoke-001 \
  --repo /checkout/design --manifest /private/input-manifest.json \
  --parameters '{"message":"hello","exit_code":0}'

# A new chat/process can discover the reference even if stdout was lost:
python3 -m jobs.workflow tasks --profile /private/synthetic.json \
  --state-dir /private/job-state
python3 -m jobs.workflow resume --profile /private/synthetic.json \
  --state-dir /private/job-state --task-key smoke-001
python3 -m jobs.workflow collect --profile /private/synthetic.json \
  --state-dir /private/job-state --task-key smoke-001
```

`ASICJOBS_STATE_DIR` may supply the directory. `--repo` must be a Git checkout
with HEAD; `--manifest` is the caller's JSON description of intended inputs.
Its canonical JSON digest is recorded, not its content. Private process-specific
manifest schemas and validation remain WP4. Required profile parameters remain
argv data; no payload changes were made in this work package.

Legacy `--reference launch.json` reads remain available. Direct Python
`Workflow.start()` retains its non-durable behavior for compatibility/test
adapters; harnesses must use the CLI or `TaskStore.start()` instead. This is an
intentional tightening of the WP1 CLI: an old start without durable options now
returns an actionable refusal before submission.

Configure WP2's optional `state_dir` with the same directory. Startup/compact/
resume context then includes cross-session task-record paths and the `tasks`
invocation. The post-tool session receipt cache is still useful but is no longer
required to recover a durably acknowledged submission. Different native/WSL paths
must resolve to the **same local store** if both callers operate on one request;
using separate stores gives separate reservations. Do not use a cloud-synced or
unvalidated network filesystem for this store.

## Submission and recovery contract

1. Validate the request and select its host. No compute is submitted yet.
2. Exclusively create a directory named by the full SHA-256 of the task key. Its
   creator alone receives submission rights. The reservation is permanent.
3. Atomically save the intent with a null job ID and `submission-unknown` before
   calling the existing transport.
4. Dispatch once. Atomically save the acknowledged job pointer if one is returned.
5. On subsequent starts with the same key, verify the request digest and observe
   the existing pointer. `resume` always queries the tracker when the ID is known.

The permanent reservation replaces a transient lock for submission: there is no
lease to expire or stale lock to steal. Concurrent callers may observe an empty
reservation, but that means unresolved submission, never permission to launch.
Each task has one writer for intent/acknowledgement. Last-observation metadata is
separate, so a concurrent read of a null ID cannot overwrite a newly saved job ID.
Concurrent observers can replace each other's cached observation; this is harmless
because it is not authority and no status history is stored.

| Failure point | Recovery |
|---|---|
| Validation fails before reservation | Correct the request; no dispatch occurred |
| Crash after reservation but before readable intent | `submission-unknown`; reconcile, never automatically retry |
| Crash after saved intent, before network call | Conservatively unknown; same rule |
| Dispatch response lost or malformed | Unknown; no second dispatch |
| Acknowledgement received but persistence fails | Saved intent remains unknown; preserve any returned evidence and reconcile |
| Acknowledgement saved but chat/stdout lost | Discover with `tasks`, then resume the same job |
| Tracker read fails | `unknown`, retaining the known job ID |
| Parameters, host request, source or manifest changed under the same key | Refused as a different request |

There is no automatic reconciliation by timestamp or command similarity: those
can identify the wrong run. The existing tracker has no caller request key.
Inspect the saved workspace/host, original tool output and tracker records using
the established read-only transport. If a match cannot be proven, leave the task
unresolved. No force/retry/clear-reservation operation is provided. Do not delete
the reservation or invent a new key to turn uncertainty into another launch.
A verified manual recovery or tracker-side request-key extension is separate work.

This prevents duplicate dispatch **for one preserved local store/key**, not across
different stores/keys, arbitrary direct launcher calls or storage loss. It does
not promise tracker-side exactly-once execution.

## Data and durability

Each reserved task has `task.json` containing the task key/ID, requested-intent
digest, selected host/job ID, profile identity/version/digest/path, required
artifacts, manifest digest, tracker bundle version, creation time and source
identity. `observation.json` stores only the latest observation time and coarse
verdicts; resume never trusts them as current status. `tasks` lists pointers,
including unreadable reservations that need attention, without contacting a host.

Source identity records local checkout root, Git HEAD, a hash of the binary diff
against HEAD (staged plus unstaged tracked changes), and a hash over names/content
of nonignored untracked files. It stores no diff, source content, parameters,
remote URLs or logs. Metadata and hashes are still private. Ignored files, external
PDK inputs and the detailed contents of dirty submodules are not covered; declare
intended inputs in the manifest. This fingerprint is a local snapshot, not a
repository freeze, staging mechanism or proof of the bytes used by a remote job.
Avoid modifying inputs during capture; WP4 must establish actual run provenance.

Writes use same-directory temporary files, file flush/fsync and atomic replacement.
POSIX also fsyncs directories after reservation/rename. The portable Windows
stdlib has no directory-fsync equivalent, so power-loss durability is limited by
the filesystem/OS; process-crash behavior is tested. POSIX creation uses owner-only
modes. On Windows set appropriate ACLs on the private directory. Existing directory
permissions are not silently modified. Keep state outside Git rather than relying
on an ignore rule inside a disposable checkout. Preserve reservations with backups;
deleting state removes the duplicate-prevention guarantee.

## Tests and remaining work

```sh
python3 -m unittest jobs.test_state jobs.test_workflow \
  integrations.cluster_jobs.test_hooks conformance.test_jobs_distribution -v
```

New cases verify intent-before-dispatch, lost acknowledgements, crashes before
intent and dispatch, failed acknowledgement persistence, concurrent threads and
independent processes, request/profile mismatch, failed tracker reads, atomic
replacement failure, source fingerprints, cross-process discovery and actual
local job recovery after discarded stdout. Existing WP1 jobs and WP2 adapter tests
remain compatible; the vendoring test now includes `jobs/state.py` (21 mappings).

Validation on 16 September 2026: all 37 combined tests passed under WSL. The native
Windows state/hook/distribution run passed 23 tests, with its two actual POSIX-job
tests explicitly skipped. Python 3.6 grammar checks and `git diff --check` passed.

WP3 supplies local durable linkage and conservative recovery. Tracker-side keyed
reconciliation remains an optional extension. WP4 engineering evidence checks,
WP5 compatibility/rollout and real harness activation remain pending.
