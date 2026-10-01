# Cluster compute defaults

Use the configured flowkit `jobs.workflow` entry point for supported compute
requests, even when the user does not mention tracking. Session-start context
lists the private profile paths and entry point. Choose the matching profile;
preserve any explicit host. Supply required parameters as data, not shell source.

Start once with a private `--state-dir`, stable `--task-key`, local `--repo` and
input `--manifest` JSON. Reuse the same key after interruption. The CLI stores
intent before submission; never use a new key to retry an uncertain launch.
Use `tasks --profile ... --state-dir ...` to discover references in a new session.
Retain its JSON reference and job ID. Query `status` or `resume`
after interruptions; never use `start` to check an existing job. A lost launch
acknowledgement (`submission-unknown`) is reconciled by repeating `start` with the
SAME task-key: the tracker holds at most one job per task, so this attaches to it,
or dispatches under that key only if the tracker has none. `resume`/`status` look
the key up too but never dispatch. Never retry under a new key. Cached receipts are pointers, not
current job status. If receipt capture fails, preserve the original tool result.

A `collect` that is not a verified engineering pass also writes a **failure report**
(`failure.md` beside the task; `failure_report` in the output). It is the handoff back
to exploration (study §4.11): give the user its path with the failed checks. Record
what you know with `report --task-key K`: `--cause gate-fail|tool-error|transport --by agent`
for the mechanical causes only (the judgement causes are the user's), and
`--question` for what the next exploration round must answer. `failures` lists the
open reports.

Choose cluster hosts by measurement, not by load alone: hosts differ ~2x in speed
by CPU model. A profile whose `host_policy.default` is `auto` picks by measured speed
(its own finished jobs, pooled across hosts with the same CPU) times room for its
`threads`, and records the survey as `host_choice` in the task record. For compute
launched by hand, run the jobs CLI's `hosts --threads N --pick` first; it prints the
best host for an N-thread job, and the session hook quotes its last survey when a
hand launch goes to a different host. Long runs launched with `jobs run --host auto
--threads N --flow F` also record their speed, so the next pick is measured on them.

From PowerShell, write the request to a small JSON file and pass `--parameters-file <file>`
instead of quoting JSON in `--parameters`; PowerShell mangles the quotes.

Use `collect` before reporting results. Report execution state, artifact evidence
and engineering verdict separately. An exit-zero job or verified artifact hashes
do not establish a design pass. Use collect's validated engineering verdict and
its failed/missing checks. `unchecked` or `invalid` never means pass. The JSON
report contract covers only its configured checks and corners, not general signoff.

Use the existing transport for read-only diagnosis. If the guard rejects a known
launch, correct it to the supplied workflow invocation. Do not hide it in another
shell, code block or interactive session. Missing profiles or nested-detaching
launchers need a foreground adapter; do not silently bypass tracking.

This instruction is advisory until the target harness has passed its activation
canary. The guard only recognizes documented command shapes; it cannot inspect
arbitrary scripts, remote stdin, generated commands or existing interactive input.
