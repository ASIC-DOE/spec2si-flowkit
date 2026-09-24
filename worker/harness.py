"""Headless worker harnesses: one round of Claude Code (`claude -p`) or Codex (`codex exec`).

A round gets the prompt, runs in the worker's checkout and must end with a
structured proposal matching PROPOSAL (both harnesses enforce the schema).
The harness never launches cluster work: Claude may run only pytest and
read-only git through Bash; Codex runs in its workspace-write sandbox, which
has no network.
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time

PROPOSAL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "summary", "observations", "hypotheses", "files_changed", "stop_reason", "question"],
    "properties": {
        "action": {"type": "string", "enum": ["patch", "stop"],
                   "description": "patch: the checkout now holds a change to be checked; stop: cannot proceed"},
        "summary": {"type": "string", "description": "what was done or found, in two or three sentences"},
        "observations": {"type": "array", "items": {"type": "string"},
                         "description": "facts seen directly (test output, file contents), each one sentence"},
        "hypotheses": {"type": "array", "items": {"type": "string"},
                       "description": "inferences not yet confirmed, each one sentence"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "stop_reason": {"type": "string", "description": "if action is stop: why; else empty"},
        "question": {"type": "string",
                     "description": "if action is stop: the question the engineer must answer; else empty"},
    },
}

#: Claude's tool allowance: edit files, read, run the tests, look at git. No
#: other shell command, so no ssh, no job launch, no commit, no push.
#: Both shell tools are listed: on Windows the model often reaches for
#: PowerShell, and pilot 1's worker could not run its tests with Bash rules only.
_SHELL_OK = ["py -3 -m pytest:*", "python -m pytest:*", "python3 -m pytest:*",
             "git diff:*", "git status:*", "git log:*", "git show:*"]
CLAUDE_TOOLS = (["Read", "Edit", "Write", "Glob", "Grep"]
                + ["Bash(%s)" % c for c in _SHELL_OK] + ["PowerShell(%s)" % c for c in _SHELL_OK])


def codex_exe():
    found = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin", "*", "codex.exe"))
    return sorted(found, key=os.path.getmtime)[-1] if found else shutil.which("codex")


def run(name, prompt, cwd, budget_usd, timeout, record_dir, model=None):
    """-> dict(ok, proposal, cost_usd, turns, tokens, seconds, error, raw)."""
    start = time.time()
    raw = os.path.join(record_dir, "harness.jsonl")
    if name == "claude":
        cmd = [shutil.which("claude") or "claude", "-p", prompt, "--output-format", "json",
               "--no-session-persistence", "--max-budget-usd", "%.2f" % budget_usd,
               "--json-schema", json.dumps(PROPOSAL), "--allowedTools"] + CLAUDE_TOOLS
        if model:
            cmd += ["--model", model]
        stdin = None
    elif name == "codex":
        schema = os.path.join(record_dir, "proposal.schema.json")
        last = os.path.join(record_dir, "last_message.json")
        with open(schema, "w", encoding="utf-8") as fh:
            json.dump(PROPOSAL, fh)
        cmd = [codex_exe(), "exec", "-C", cwd, "--json", "-s", "workspace-write", "--skip-git-repo-check",
               "--output-schema", schema, "-o", last, "-"]
        if model:
            cmd[2:2] = ["-m", model]
        stdin = prompt.encode("utf-8")
    else:
        raise ValueError("unknown harness %r" % name)
    try:
        with open(raw, "wb") as out, tempfile.TemporaryFile() as err:
            proc = subprocess.run(cmd, cwd=cwd, input=stdin, stdout=out, stderr=err, timeout=timeout)
            err.seek(0)
            stderr = err.read().decode("utf-8", "replace")[-2000:]
    except subprocess.TimeoutExpired:
        return dict(ok=False, proposal=None, cost_usd=None, turns=None, tokens=None,
                    seconds=time.time() - start, error="harness timeout after %ds" % timeout, raw=raw)
    result = dict(ok=False, proposal=None, cost_usd=None, turns=None, tokens=None,
                  seconds=round(time.time() - start, 1), error=None, raw=raw)
    text = open(raw, encoding="utf-8", errors="replace").read()
    if name == "claude":
        try:
            data = json.loads(text)
        except ValueError:
            result["error"] = "no JSON result (rc %s): %s" % (proc.returncode, stderr[-300:])
            return result
        result.update(cost_usd=data.get("total_cost_usd"), turns=data.get("num_turns"),
                      proposal=data.get("structured_output"))
        if data.get("subtype") != "success" or not isinstance(result["proposal"], dict):
            result["error"] = "harness ended %s without a proposal" % data.get("subtype")
            return result
    else:
        tokens = 0
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            usage = (event.get("usage") or {}) if event.get("type") == "turn.completed" else {}
            tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        result["tokens"] = tokens
        try:
            with open(last, encoding="utf-8") as fh:
                result["proposal"] = json.load(fh)
        except (OSError, ValueError):
            result["error"] = "no structured last message (rc %s): %s" % (proc.returncode, stderr[-300:])
            return result
    missing = [k for k in PROPOSAL["required"] if k not in result["proposal"]]
    if missing or result["proposal"].get("action") not in ("patch", "stop"):
        result["error"] = "proposal does not match the schema: missing %s" % missing
        return result
    result["ok"] = True
    return result
