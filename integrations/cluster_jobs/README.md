# WP2 harness defaults and bypass guard

**WP4 update:** collection now validates the [normalized engineering report](../../docs/job_tracker_wp4.md).
Hook guidance distinguishes pass/fail from invalid/unchecked; process exit or hashes
alone still do not establish a pass. Older WP4 deferrals below describe the original WP2 boundary.

**WP3 update:** configure optional `state_dir` alongside `receipt_dir` to expose
durable task records across sessions. Starts now require `--state-dir`, a stable
`--task-key`, `--repo` and `--manifest`; guard replacement guidance includes these.
See [durable linkage and current invocation](../../docs/job_tracker_wp3.md).
The session receipt-cache limitations below describe that cache, not the new
durable task store. Pre-dispatch persistence and cross-session discovery are now
implemented in `jobs/state.py`; tracker-side reconciliation is still unresolved.

This opt-in flowkit package supplies startup instructions, a command guard and
post-tool reference capture. Nothing here installs hooks or edits consumers.
Use `instructions.md` as the shared text for a later consumer's AGENTS.md and
CLAUDE.md integration; the SessionStart hook supplies the concrete configuration.

## Configuration and settings

Copy `example_config.json` into a private location and replace its synthetic
values. `roots` lists exact project roots and native/WSL aliases; matching respects
directory boundaries. `hosts` lists exact SSH destinations (include FQDN aliases
explicitly). `routes` map known executable names or relative script suffixes to
private WP1 profiles. Bare names match executable basenames; script paths match
at a path boundary. Include every supported launcher's name. No site hosts,
private commands or process profiles are bundled.

Set `entrypoint` for the target environment, e.g. `python3 -m jobs.workflow` in
flowkit or `python3 -m deployment.bnl.jobs.workflow` in a consumer. The command
must run with the package importable; configure absolute paths when needed.
`receipt_dir` must be an absolute private directory on the **hook host**, preferably
outside disposable worktrees, unique to the project. Windows and WSL configs
must use their respective paths. Configure directory ACLs for private metadata;
POSIX cache directories/files use owner-only creation modes.

Generate a reviewable settings candidate, without installing it:

```sh
python3 integrations/cluster_jobs/render.py --harness codex \
  --command '/usr/bin/python3 /absolute/flowkit/integrations/cluster_jobs/hook.py --config /private/jobs-hooks.json' \
  > /private/codex-hooks.candidate.json
```

Use `--harness claude` for POSIX Claude, `claude-windows` for its PowerShell command
hook, or `codex` with a command appropriate to its host. On Windows, quote absolute
interpreter and script paths for the selected shell (PowerShell uses `&` before a
quoted executable). Use `--existing PATH` to merge a **copy** of existing JSON
settings. Generation is idempotent for identical handlers and preserves unrelated
settings, PreToolUse guards and SessionEnd harvesters. Review changed handler
commands to avoid leaving an obsolete duplicate. This is a settings renderer,
not an installer; no real settings are changed automatically.

At later activation, the candidate's target is the project's `.codex/hooks.json`
or `.claude/settings.json`. Codex also needs hooks enabled in its active settings.
Do not replace existing settings wholesale, especially when hooks are also defined
inline. Keep the shared source in flowkit for this milestone; packaging/vendoring
the integration and real consumer activation remain rollout work.

## Behavior and limits

- **SessionStart:** injects short default-routing instructions, profile paths and
  up to 20 cached receipt paths for this harness session. Runs for startup, resume,
  clear and compact. Cached pointers must be re-queried; the hook never polls jobs.
- **PreToolUse:** recognizes configured commands in direct shell, basic `env`,
  `wsl -e`, `bash/sh -c`, PowerShell, SSH and known detacher wrappers. A matched
  compute launch receives a deny response with a profile invocation. Compound
  commands are inspected individually: mentioning the workflow does not exempt
  another command. Unrelated commands and read-only diagnostics produce no
  permission decision. Existing guards remain authoritative.
- **PostToolUse:** for recognized workflow invocations, extracts JSON envelopes
  from stdout/output/text/content wrappers and stores only WP1 references. It adds
  collection guidance, never an engineering-pass claim. No logs or parameter
  values are stored. A missed/truncated response prompts explicit recovery guidance.

The recognizer is deliberately limited. It does not evaluate arbitrary shell
grammar, aliases, variable expansion, encoded PowerShell, Python subprocess code,
remote scripts supplied via stdin, shell startup files or interactive input.
Opaque recognized wrappers produce advisory context. Unknown tools and hidden
implementations may be invisible altogether. Use literal direct workflow calls
as the supported path. Background wrappers around unrelated commands are not
blocked. A trusted profile can itself run arbitrary code; hooks are not a security
sandbox or a universal accounting boundary.

Supported shell event shapes are `command` and native exec's `cmd`/`workdir`.
Nested code-mode coverage relies on the harness emitting a hook for the inner
shell call; this package does not parse JavaScript. Input is bounded to 1 MiB;
pre-hook parsing/config errors exit 2 visibly. Startup/post errors provide recovery
context because the tool may already have run. Hook timeouts and interpreter
failures are harness-controlled and must be exercised at activation.

Receipts are atomic individual JSON files under a hash of the session ID. Only
the original reference is cached; no latest-state database is introduced. Multiple
observations of the same task overwrite the same immutable pointer. A different
session does not automatically discover prior sessions; retain/provide the receipt
path for a new chat. Missing events, crashes before receipt capture, ambiguous
submissions, cross-session lookup and durable pre-dispatch intent remain WP3.
Cached output is untrusted evidence: WP1 validates the profile/reference and reads
the tracker again. Post-tool guidance cannot enforce the truth of final prose;
engineering validation remains WP4.

## Validation and activation gate

```sh
python3 -m unittest integrations.cluster_jobs.test_hooks -v
```

The tests replay hook events through both the library and command-hook subprocess.
They cover 16 blocked command shapes, read-only and out-of-scope cases, native/WSL
root aliases, native exec arguments, receipt disclosure limits, resume context,
unknown payloads, error exits and additive settings generation. A POSIX integration
test launches a real local tracker job, captures its reference and resumes that
same job without another submission. No SSH/EDA jobs or model sessions are started.

On 16 September 2026: nine tests passed under WSL; eight passed on native Windows
with the POSIX job test skipped. Existing local heredoc and inline-SSH guard engines
were also exercised read-only: both accepted a direct workflow invocation and an
unquoted SSH `ls`. This checks those examples, not every possible hook combination.

Installed versions inspected: Codex CLI `0.154.0-alpha.6.2` (hooks feature enabled)
and Claude Code `2.1.237`. Version availability is **not** proof that the desktop
or CLI actually delivers these events. The adapter tests are synthetic protocol
canaries, not live harness activation tests.

Before calling a consumer enforced, record for each target harness/shell/version:

1. A SessionStart event delivers profile guidance, including after resume/compaction.
2. A harmless configured synthetic executable is denied before it creates a marker.
3. A read-only command still runs, and existing SSH/heredoc guards still deny their
   own prohibited examples. Do not remove those guards to make this one pass.
4. A synthetic workflow job's returned reference is captured and restored.
5. A nested code-mode shell invocation receives the same denial. If it does not,
   record advisory coverage for that path; do not infer coverage from direct calls.
6. A long-running unified-exec result is captured on completion, and hook failures
   are visible. No success claim is derived from a missing post-tool event.

Then perform the plan's ten ordinary-language requests without tracker reminders.
This milestone does not claim that behavioral acceptance, consumer activation,
cross-session recovery or engineering validation has happened.

Protocol sources checked: [Codex hooks](https://learn.chatgpt.com/docs/hooks) and
[Claude hooks](https://code.claude.com/docs/en/hooks). Both document command hooks
with JSON input and pre-tool denial. Codex documents shell hooks for unified exec
and nested code-mode calls, with exceptions; Claude documents tool-specific
post-result shapes. The adapter emits no explicit allow, input rewrite or
permission escalation. Runtime canaries remain the authority for deployed coverage.
