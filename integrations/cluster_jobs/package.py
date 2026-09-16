"""Build a reviewable jobs-only rollout tree; never install it in a consumer.

The output must not exist. Existing sync.py remains the distribution authority.
No network, credentials, user settings or private profiles are accessed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build(output):
    output = Path(output).resolve()
    # Require a new directory, so an existing repository/settings tree cannot
    # be overwritten even when the caller supplies the wrong target.
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("flowkit_sync", ROOT / "sync.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    pairs = [(s, d) for s, d in sync.FILES
             if (s.startswith("jobs/") and not s.startswith("jobs/test_"))
             or s.startswith("integrations/cluster_jobs/")]
    pairs += [("docs/" + name, "docs/" + name) for name in
              ("job_tracker_wp3.md", "job_tracker_wp4.md", "job_tracker_wp5.md")]
    pairs += [("integrations/cluster_jobs/templates/" + name, "templates/" + name)
              for name in ("foreground_profile.json", "activation_matrix.json")]
    pairs += [("docs/evidence/job_tracker_wp5_consumers.json", "docs/evidence/job_tracker_wp5_consumers.json")]
    manifest = []
    for src, dest in pairs:
        data = (ROOT / src).read_bytes()
        target = output / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest.append(dict(source=src, path=dest, sha256=hashlib.sha256(data).hexdigest()))
    metadata = dict(schema=1, files=manifest, activation="not-installed",
                    notice="Private profiles, machine settings and licensed-tool validation are not bundled.")
    (output / "rollout-manifest.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# Tracker rollout candidate\n\n"
        "Review docs/job_tracker_wp5.md before activation. Nothing is installed.\n\n"
        "Local POSIX/WSL canary from this directory:\n\n"
        "    python3 -m deployment.bnl.jobs.smoke\n\n"
        "This canary uses local /bin/sh and temporary HOME, never SSH.\n"
        "Use integrations/cluster_jobs/render.py to prepare additive hook settings.\n"
        "Do not copy this README over a consumer README.\n", encoding="utf-8")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    metadata = build(args.output)
    print(json.dumps(dict(files=len(metadata["files"]), activation=metadata["activation"])))
