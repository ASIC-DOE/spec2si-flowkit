"""One-way Git/LFS synchronization; refuse deletion or history replacement."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys


class DriftError(RuntimeError):
    pass


def git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        raise RuntimeError(f"Git {args[0]} failed: {result.stderr.strip()}")
    return result


def remote_refs(repo, remote):
    output = git(repo, "ls-remote", "--refs", "--heads", "--tags", remote).stdout
    return {ref: oid for oid, ref in (line.split() for line in output.splitlines())}


def local_refs(repo):
    output = git(repo, "for-each-ref", "--format=%(objectname) %(refname)",
                 "refs/heads/", "refs/tags/").stdout
    return {ref: oid for oid, ref in (line.split() for line in output.splitlines())}


def sync_once(repo, destination):
    source = local_refs(repo)
    if not source or "refs/heads/main" not in source:
        raise DriftError("Source has no main branch; refusing to synchronize.")
    target = remote_refs(repo, destination)
    # Fetch only into a private local namespace, never over source branches/tags.
    git(repo, "fetch", "--no-tags", destination,
        "+refs/heads/*:refs/mirror-check/heads/*",
        "+refs/tags/*:refs/mirror-check/tags/*")
    problems = [f"Destination-only ref (possible upstream deletion): {ref}"
                for ref in sorted(target.keys() - source.keys())]
    for ref in sorted(source.keys() & target.keys()):
        if source[ref] == target[ref]:
            continue
        if ref.startswith("refs/tags/"):
            problems.append(f"Tag changed: {ref}")
        elif git(repo, "merge-base", "--is-ancestor", target[ref], source[ref],
                 check=False).returncode != 0:
            problems.append(f"Branch diverged or history was rewritten: {ref}")
    if problems:
        raise DriftError("No refs were pushed. Manual reconciliation required:\n" +
                         "\n".join(problems))
    # Transfer LFS before publishing refs, including objects in non-default branches.
    lfs_files = git(repo, "lfs", "ls-files", "--all", "--long").stdout.splitlines()
    if lfs_files:
        git(repo, "lfs", "fetch", "--all", "origin")
        git(repo, "lfs", "push", "--all", destination)
    changed = [ref for ref in sorted(source) if source[ref] != target.get(ref)]
    if changed:
        # No force or prune: concurrent conflicting changes also cause rejection.
        # Atomic push prevents updating only some branches/tags on rejection.
        git(repo, "push", "--atomic", destination,
            *[f"{ref}:{ref}" for ref in changed])
    actual = remote_refs(repo, destination)
    if actual != source:
        raise DriftError("Post-push verification found different branch/tag IDs.")
    return source, len(changed), len(lfs_files)


def synchronize(repo, destination):
    for attempt in range(3):
        # Force/prune is confined to this disposable local clone of the source.
        git(repo, "fetch", "--prune", "--no-tags", "origin",
            "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
        refs, changed, lfs_count = sync_once(repo, destination)
        if remote_refs(repo, "origin") == refs:
            return refs, changed, lfs_count
        print(f"Source advanced during synchronization; reconciling again ({attempt + 1}/3).")
    raise DriftError("Source kept changing during verification; rerun to reconcile.")


def summary(text):
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    try:
        refs, changed, lfs_count = synchronize(args.repo, args.destination)
        branches = sum(ref.startswith("refs/heads/") for ref in refs)
        tags = len(refs) - branches
        summary(f"Verified {branches} branches and {tags} tags; {changed} refs updated; "
                f"{lfs_count} LFS entries checked.\n\n"
                f"Source and destination match at {datetime.now(timezone.utc).isoformat()}.\n\n"
                f"main: `{refs['refs/heads/main']}`")
    except (RuntimeError, OSError) as exc:
        summary(f"Synchronization failed.\n\n```text\n{exc}\n```\n\n"
                "Inspect source and destination history before reconciling. "
                "This workflow never force-pushes or deletes destination refs.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
