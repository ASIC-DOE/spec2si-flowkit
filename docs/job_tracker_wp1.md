# WP1 — profile-driven tracker entry point

**WP4 update:** engineering profiles now use the closed [JSON report contract](job_tracker_wp4.md);
collection can return validated pass/fail and private evidence summaries. Profiles
without a report remain unchecked. Earlier verdict descriptions below are historical.

**WP3 update:** CLI starts now require durable state/task-key/source/manifest
options; the original start example below is historical. Use the current
[WP3 invocation](job_tracker_wp3.md). Legacy reference reads remain supported.

Implemented 16 September 2026, entirely in flowkit. The shared entry point is
[`jobs/workflow.py`](../jobs/workflow.py), invoked as `python -m jobs.workflow`
from flowkit or `python -m deployment.bnl.jobs.workflow` from a vendored consumer.
The module uses the existing `Transport` directly. It does not parse CLI tables
or introduce another scheduler. Both harnesses can invoke the same JSON CLI;
making them do so by default is WP2.

## Profile contract

[`jobs/example_profile.json`](../jobs/example_profile.json) is a complete,
sanitized schema-1 example. Its host is deliberately `example.invalid`.
Supply a trusted private profile by path; no site profile registry is assumed.
Profiles are executable configuration, not an untrusted command sandbox.

| Field | Contract |
|---|---|
| `schema`, `id`, `version` | Schema 1, explicit profile identity/version; unknown fields refused |
| `repository` | Caller-declared repository identity; source/patch attestation is WP3 |
| `work_root` | Existing absolute POSIX directory on the selected host |
| `workspace` | `unique-child`: allocate a UUID child, refuse existing directory; no automatic cleanup |
| `lifecycle` | `foreground`: payload must own/wait for actual work; detached profiles refused |
| `argv` | Absolute executable followed by literal tokens or `{"parameter":"name"}` tokens |
| `parameters` | Required exact key set; string/integer types and optional allowed `choices` |
| `host_policy` | Allowed hosts, explicit default, `allow_auto`; an explicit allowed host bypasses selection |
| `expected_artifacts` | Unique relative file paths within the allocated workspace; traversal refused |
| `progress` | Null, or tracker tool/log/optional total; uses existing progress extraction |
| `engineering_report` | Null, or parser identifier and required artifact path; declaration only until WP4 |

Parameter substitution fills a complete argv element. No formatting into shell
source occurs. If a trusted profile uses `sh -c`, pass variable values as its
positional arguments, as the example does. Empty argv values are refused because
the existing base64 launcher cannot preserve them. Profiles must use absolute
input/executable paths or explicitly stage inputs in their payload: the workflow
does not copy a repository, prepare a worktree or infer a remote checkout.

`Transport.run(..., workspace=...)` is the only transport change. It performs
an exclusive `mkdir` and `cd` before invoking the existing launcher. Both payload
and artifact stamping therefore use the same fresh work directory. Existing
callers without `workspace` retain their behavior. Submission is never retried.
Automatic selection uses existing `hosts.pick`, limited to the profile's hosts.

## Invocation and output

For a configured private profile and an appropriate SSH environment:

```sh
python -m jobs.workflow start --profile /private/synthetic.json \
  --parameters '{"message":"hello","exit_code":0}' > /private/launch.json
python -m jobs.workflow status --profile /private/synthetic.json --reference /private/launch.json
python -m jobs.workflow resume --profile /private/synthetic.json --reference /private/launch.json
python -m jobs.workflow collect --profile /private/synthetic.json --reference /private/launch.json
```

Retain the launch output in a private location. `resume` performs a live status
read for the saved host/job; it never launches. Changing the profile digest or
reference workspace/repository causes refusal. `start` always means a new job;
it has no idempotency key. **Do not repeat start after a lost response.**

Output schema 1 includes `task_id`, selected `host`, `job_id`, `observation`,
`evidence`, `engineering`, `next_action` and a caller-owned `reference` containing
profile/request digests and workspace identity. It excludes commands, parameter
values and raw logs. The reference still contains private metadata and is not a
public report. Invalid contracts return a concise refusal/reason. Exit 2 means
refused; exit 4 means unknown observation/submission; exit 0 means a known
observation, **not successful execution or design acceptance**. Inspect the JSON.

Collection checks terminal state, required artifact coverage, per-artifact job
IDs and SHA-256 stamps, then calls the existing verifier and checks counts.
`tracker-verified` means this limited tracker check passed. Empty requirements,
missing artifacts and partial stamps are incomplete; changed files are unverified.
An exit-7 job can have verified artifacts. `engineering` is always `unchecked`:
WP4 must still validate design/input/corner identity and the report parser, and
provide the planned evidence summary. Hash verification is a point-in-time check,
not immutable storage or protection against a payload misdeclaring its outputs.

WP1 deliberately uses caller-retained references. It does not claim atomic
pre-dispatch persistence, crash recovery, concurrent retry safety or repository
patch identity. Those remain WP3. Unknown submission means reconcile, not retry.
Profiles declare foreground ownership; arbitrary commands cannot be statically
proven to obey it. Instrumented/detached adapter acceptance remains WP5.

## Example-job validation

Run all new tests without SSH, cluster access, licenses or consumer edits:

```sh
python3 -m unittest jobs.test_workflow -v
```

On Windows, run this command through WSL to execute the POSIX examples. The test
runner substitutes a local `/bin/sh` for SSH, with an isolated temporary HOME.
It installs the actual content-hashed tracker bundle, runs actual foreground
payloads under `runjob`, polls actual status records and rehashes real artifacts.
It is not a simulated job lifecycle. Temporary evidence is removed after tests.

| Example | Observed result |
|---|---|
| Write output and exit 0 | `done`, `tracker-verified`, engineering unchecked |
| Write output and exit 7 | `failed`, execution rc 7, artifacts verified, engineering unchecked |
| Require another output that was never written | `done`, evidence incomplete despite a valid existing file |
| Change an output after completion | Subsequent collection reports unverified |
| Declare no outputs | Evidence incomplete; no unstamped acceptance |
| Serialize/reload reference in a new Workflow instance | Same job observed; exactly one submission |
| Shell metacharacters/newlines in a parameter and quoted workspace root | Literal output preserved; no injected command executed |
| Existing workspace | Launch stops with rc 73; no terminal event created |

Additional contract tests cover unknown fields/types/policies, disallowed hosts,
explicit-host precedence, permitted auto selection, changed profiles, lost
acknowledgements, failed reads and nonzero submission responses. Unknown reads
never trigger resubmission.

Validation: 12 new tests pass under WSL; existing transport suite passes all
92 checks. Native Windows runs the six contract tests and three vendoring tests;
six POSIX tests are explicitly skipped there. The distribution test vendors into
a disposable consumer and detects missing/drifted files. Only the reusable
`workflow.py` is added to the distribution manifest; example profile and tests
remain in flowkit. No real consumer or machine configuration was changed.

WP0's hash manifest remains the historical upstream baseline. `remote.py` now
intentionally differs through the optional workspace extension. No remote tracker
shell script was changed. Live SSH/cluster validation and harness default-routing
acceptance remain future rollout gates.
