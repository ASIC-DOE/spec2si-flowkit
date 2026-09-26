"""Render additive hook settings to stdout; never install or edit settings.

Pass --existing to merge a copy, retaining unrelated hooks and settings.
Commands must contain absolute interpreter, hook.py and --config paths, quoted
for the actual host shell. Native Windows and WSL need separate configurations.
"""
import argparse
import copy
import json


def render(harness, command, existing=None):
    settings = copy.deepcopy(existing or {})
    hooks = settings.setdefault("hooks", {})
    for event in ("SessionStart", "PreToolUse", "PostToolUse"):
        matcher = "startup|resume|clear|compact" if event == "SessionStart" else "^(Bash|PowerShell|exec_command|shell|shell_command)$"
        item = {"matcher": matcher, "hooks": [{"type": "command", "command": command, "timeout": 5}]}
        if harness == "claude-windows":
            item["hooks"][0]["shell"] = "powershell"
        entries = hooks.setdefault(event, [])
        if item not in entries:
            entries.append(item)
    if harness != "codex":
        # Claude's Stop hook refuses to end a session holding an uncollected tracked job. Not rendered
        # for Codex yet: a new handler there needs the owner's /hooks trust again.
        item = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
        if harness == "claude-windows":
            item["hooks"][0]["shell"] = "powershell"
        entries = hooks.setdefault("Stop", [])
        if item not in entries:
            entries.append(item)
    return settings


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--harness", required=True, choices=("codex", "claude", "claude-windows"))
    p.add_argument("--command", required=True)
    p.add_argument("--existing")
    args = p.parse_args()
    existing = None
    if args.existing:
        with open(args.existing, encoding="utf-8") as fh:
            existing = json.load(fh)
    print(json.dumps(render(args.harness, args.command, existing), indent=2))


if __name__ == "__main__":
    main()
