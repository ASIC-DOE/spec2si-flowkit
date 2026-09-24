"""Summarize behavioral trials recorded by run_trials.py.

    python integrations/cluster_jobs/acceptance/analyze.py --harness claude|codex --out <dir> \
        [--launch-pattern REGEX]

Per trial: workflow start/status/collect calls, untracked launches that
EXECUTED (--launch-pattern, e.g. 'bandgap_dc\\.py.*--run|tracked_job\\.py\\s+run'),
hook denials, SessionStart guidance (Claude stream only), and the final answer.
This is a reading aid, not a verdict: judge each outcome against the request.
Afterwards, confirm on the cluster that tasks map one-to-one to jobs
(`jobs.workflow tasks` against `ls ~/.asicjobs`) and collect any run a session
left uncollected.
"""
import argparse
import json
import os
import re
import sys


def claude(path):
    r = dict(ctx=False, cmds=[], denied=[], final="", cost=None)
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "system" and d.get("subtype") == "hook_response":
            out = (d.get("output") or "") + (d.get("stdout") or "")
            if d.get("hook_event") == "SessionStart" and "jobs.workflow profile" in out:
                r["ctx"] = True
            if d.get("hook_event") == "PreToolUse" and '"deny"' in out:
                r["denied"].append(out[out.find("permissionDecisionReason"):][:120])
        if d.get("type") == "assistant":
            for c in d.get("message", {}).get("content", []):
                if c.get("type") == "tool_use" and c.get("name") in ("Bash", "PowerShell"):
                    r["cmds"].append((None, c.get("input", {}).get("command", "")))
        if d.get("type") == "result":
            r["final"], r["cost"] = d.get("result") or "", d.get("total_cost_usd")
    return r


def codex(path, err):
    r = dict(ctx=None, cmds=[], denied=[], final="", cost=None)
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        it = d.get("item") or {}
        if d.get("type") == "item.completed" and it.get("type") == "command_execution":
            r["cmds"].append((it.get("exit_code"), re.sub(r'^"[^"]*pwsh\.exe" -Command ', "", it.get("command") or "")))
        if d.get("type") == "item.completed" and it.get("type") == "agent_message":
            r["final"] = it.get("text") or r["final"]
    if os.path.exists(err):
        r["denied"] = re.findall(r"Command blocked by PreToolUse hook: (.{0,110})", open(err, encoding="utf-8", errors="replace").read())
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harness", choices=("claude", "codex"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--launch-pattern", default=r"$^")
    a = ap.parse_args()
    # Final answers carry ≥, µ, ° ...; a Windows console's cp1252 cannot encode them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ids =sorted({f[:-6] for f in os.listdir(a.out) if f.endswith(".jsonl")}, key=lambda s: (len(s), s))
    for tid in ids:
        p = os.path.join(a.out, tid + ".jsonl")
        r = claude(p) if a.harness == "claude" else codex(p, os.path.join(a.out, tid + ".err"))
        n = lambda pat: sum(bool(re.search(pat, c)) for _e, c in r["cmds"])
        raw = sum(bool(re.search(a.launch_pattern, c)) and "jobs.workflow" not in c for _e, c in r["cmds"])
        print("=" * 100)
        print("%s  start=%d status=%d collect=%d untracked-launch=%d denied=%d session-start-ctx=%s cost=%s"
              % (tid, n(r"jobs\.workflow\s+start"), n(r"jobs\.workflow\s+(status|resume)"),
                 n(r"jobs\.workflow\s+collect"), raw, len(r["denied"]), r["ctx"], r["cost"]))
        for d in r["denied"]:
            print("  DENY:", d)
        for e, c in r["cmds"]:
            print("  $%s %s" % ("" if e is None else "[%s]" % e, c.replace("\n", " ")[:200]))
        print("  FINAL:", r["final"].replace("\n", " ")[:900])


if __name__ == "__main__":
    main()
