"""Repo-local hook paths and entry point; process scripts supply route names."""
import json
import os
from pathlib import Path
import sys
from .hook import handle


def config(root, repository, executables):
    root = Path(root).resolve(); name = str(root).replace("\\", "/")
    roots = [name]
    if len(name) > 2 and name[1] == ":": roots.append("/mnt/" + name[0].lower() + name[2:])
    elif name.startswith("/mnt/"): roots.append(name[5].upper() + ":" + name[6:])
    state = root.parent / ".spec2si-job-state" / repository
    profile = str(root / ".tracker-local/profile.json")
    return dict(schema=1, roots=roots, hosts=["asic7"],
                entrypoint=("py -3" if os.name == "nt" else "python3") + " -m deployment.bnl.jobs.workflow",
                receipt_dir=str(state / "receipts"), state_dir=str(state / "tasks"),
                routes=[dict(name=repository, executables=executables, profile=profile),
                        dict(name=repository + "-payload", executables=["tracked_job.py"],
                             argument_prefixes=[["run"]], profile=profile)])


def main(root, repository, executables):
    try:
        data = sys.stdin.read(1048577)
        if len(data) > 1048576: raise ValueError("event too large")
        print(json.dumps(handle(json.loads(data), config(root, repository, executables))))
        return 0
    except (ValueError, OSError, TypeError, KeyError):
        print("Project tracker hook failed; repair the integration before retrying.", file=sys.stderr)
        return 2
