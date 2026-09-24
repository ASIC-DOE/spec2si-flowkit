"""Behavioral acceptance: ordinary requests in fresh/resumed headless assistant sessions.

    python integrations/cluster_jobs/acceptance/run_trials.py --harness claude \
        --repo C:/dev/spec2si-tsmc28 --prompts integrations/cluster_jobs/acceptance/prompts/tsmc28_bandgap.json \
        --out <private dir outside the checkout>
    python .../analyze.py --harness claude --out <same dir>

Each prompt set is a JSON list of [id, "fresh"|"resume", parent_id|null, request].
No request may mention tracking, the workflow or profiles -- the point is to
measure what the assistant does BY DEFAULT. Resumed sessions continue their
parent's session, so SessionStart(resume) restoration is exercised too.

Harnesses:
  claude  `claude -p` with stream-json + hook events. Bash/PowerShell/Read/Grep/Glob
          allowed, $3 cap per session.
  codex   `codex exec --json`. The desktop app's codex.exe is found under
          %LOCALAPPDATA%/OpenAI/Codex/bin/<hash>/ (it is not on PATH). Project hook
          trust is bypassed PER INVOCATION (--dangerously-bypass-hook-trust):
          these are the repo's own reviewed hooks and nothing is persisted. For
          everyday use the owner trusts them once with /hooks.
Both run with enough access for ssh. They launch REAL licensed jobs when a
request asks for one, so choose a short tracked flow (the ADC pilot is an hour
of Spectre; tsmc28's bandgap DC is a minute).
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import time
import uuid


def codex_exe():
    found = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin", "*", "codex.exe"))
    return sorted(found, key=os.path.getmtime)[-1] if found else shutil.which("codex")


def run(harness, repo, trials, out, timeout):
    os.makedirs(out, exist_ok=True)
    state_path = os.path.join(out, "sessions.json")
    sessions = json.load(open(state_path)) if os.path.exists(state_path) else {}
    for tid, mode, parent, prompt in trials:
        if harness == "claude":
            sid = str(uuid.uuid4()) if mode == "fresh" else sessions[parent]
            cmd = [shutil.which("claude"), "-p", prompt,
                   "--session-id" if mode == "fresh" else "--resume", sid,
                   "--output-format", "stream-json", "--verbose", "--include-hook-events",
                   "--allowedTools", "Bash", "PowerShell", "Read", "Grep", "Glob", "--max-budget-usd", "3"]
            stdin = None
            sessions[tid] = sid
        else:
            common = ["--json", "--dangerously-bypass-hook-trust", "-s", "danger-full-access", "--skip-git-repo-check"]
            cmd = [codex_exe(), "exec", "-C", repo] + common + (
                ["-"] if mode == "fresh" else ["resume", sessions[parent], "-"])
            stdin = prompt.encode("utf-8")
        t0 = time.time()
        with open(os.path.join(out, tid + ".jsonl"), "w", encoding="utf-8") as fh, \
                open(os.path.join(out, tid + ".err"), "w", encoding="utf-8") as fe:
            try:
                rc = subprocess.run(cmd, cwd=repo, input=stdin, stdout=fh, stderr=fe, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
        if harness == "codex":
            for line in open(os.path.join(out, tid + ".jsonl"), encoding="utf-8", errors="replace"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "thread.started":
                    sessions[tid] = d["thread_id"]
                    break
            sessions.setdefault(tid, sessions.get(parent))
        json.dump(sessions, open(state_path, "w"), indent=1)
        print("%s %s rc=%s %.0fs" % (tid, mode, rc, time.time() - t0), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harness", choices=("claude", "codex"), required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--out", required=True, help="private directory outside the checkout")
    ap.add_argument("--only", nargs="*", help="run just these trial ids")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    trials = [t for t in json.load(open(a.prompts, encoding="utf-8")) if not a.only or t[0] in a.only]
    run(a.harness, os.path.abspath(a.repo), trials, os.path.abspath(a.out), a.timeout)


if __name__ == "__main__":
    main()
