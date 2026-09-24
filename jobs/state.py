"""Durable local task pointers, not a job database. Standard library, Python 3.6+.

A permanent exclusive directory reservation permits only its creator to submit.
An empty/crashed reservation is ambiguous forever; it never grants retry rights.

Lost acknowledgements (report §6.3). The task id is written before dispatch and
sent as the tracker's request key; runjob claims that key atomically before it
launches anything, so the tracker holds at most one job per task. A task whose
acknowledgement was lost is therefore resolved by the key, never by a new one:
  * resume/status/collect LOOK UP the key and attach the job if one holds it;
  * a repeated start of the same request also looks it up, and dispatches
    again under the same key only when the tracker answers "absent". A
    concurrent or delayed first dispatch then attaches instead of launching.
An unreachable host is never taken as absence. Records made before request
keys existed (no `request_protocol`) are never dispatched again.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

from .remote import bundle_manifest
from .workflow import ContractError, digest, relative, require


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, sort_keys=True))


def atomic_text(path, value):
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        sync_directory(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_directory(path):
    # POSIX directory fsync makes the rename/reservation durable. Windows has
    # no portable stdlib directory fsync; see the documented power-loss limit.
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def source_identity(repo):
    """Fingerprint local HEAD, tracked changes and nonignored untracked files.

    Never persists source content, diffs, remote URLs or credentials. This is
    provenance, not staging or proof of which bytes the remote payload consumed.
    """
    repo = os.path.realpath(repo)

    def git(*args):
        try:
            result = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise ContractError("cannot read Git source identity")
        require(result.returncode == 0, "cannot read Git source identity")
        return result.stdout

    repo = os.path.realpath(os.fsdecode(git("rev-parse", "--show-toplevel")).strip())
    head = git("rev-parse", "HEAD").decode("ascii").strip()
    patch = git("diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--")
    names = sorted(n for n in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if n)
    untracked = hashlib.sha256()
    for name in names:
        path = os.path.join(repo, os.fsdecode(name))
        content = hashlib.sha256()
        if os.path.islink(path):
            content.update(os.fsencode(os.readlink(path)))
        else:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    content.update(chunk)
        untracked.update(name + b"\0" + content.digest())
    return dict(root=repo, head=head, patch_sha256=hashlib.sha256(patch).hexdigest(),
                untracked_sha256=untracked.hexdigest())


def bind_source(repo, manifest):
    """-> the source identity a durable start records for `manifest`.

    A pilot manifest lists the files its snapshot was PACKAGED from
    (`packaged`: repo path -> sha256). Those files are what the job runs, so
    they are what binds it: each must still hash the same in this checkout,
    or the start is refused HERE, before anything is dispatched. The job is
    then stamped with the packaged identity, which is what the snapshot's own
    payload checks. A commit or a session-log rewrite elsewhere in the
    checkout no longer turns a valid start into a job the cluster refuses.

    A manifest without `packaged` (staged before this existed) keeps the
    original contract: the whole checkout's current identity.
    """
    current = source_identity(repo)
    packaged = manifest.get("packaged")
    if packaged is None:
        return current
    require(isinstance(packaged, dict) and packaged and isinstance(manifest.get("source"), dict),
            "invalid packaged-source record")
    changed = []
    for path, want in sorted(packaged.items()):
        require(relative(path), "invalid packaged path")
        full = os.path.join(current["root"], path)
        if os.path.islink(full) or not os.path.isfile(full):
            changed.append(path)
            continue
        h = hashlib.sha256()
        with open(full, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != want:
            changed.append(path)
    require(not changed, "declared source changed since packaging (%s%s); package and deploy "
            "again, or start from the packaged checkout"
            % (", ".join(changed[:5]), ", ..." if len(changed) > 5 else ""))
    return manifest["source"]


class TaskStore:
    def __init__(self, directory):
        require(isinstance(directory, str) and os.path.isabs(directory), "state-dir must be absolute")
        self.directory = os.path.realpath(directory)
        os.makedirs(self.directory, mode=0o700, exist_ok=True)

    def require_external(self, repo):
        root = os.path.realpath(repo)
        try:
            inside = os.path.commonpath([root, self.directory]) == root
        except ValueError:  # Separate Windows drives.
            inside = False
        require(not inside, "state-dir must be outside the source checkout")

    def path(self, key):
        require(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", key),
                "invalid task-key")
        # Hash keys to avoid Windows device names and case-insensitive collisions.
        return os.path.join(self.directory, "request-" + hashlib.sha256(key.encode("utf-8")).hexdigest())

    def read(self, key):
        path = self.path(key)
        require(os.path.isdir(path), "unknown task-key; start only for a new request")
        try:
            with open(os.path.join(path, "task.json"), encoding="utf-8") as fh:
                record = json.load(fh)
            require(record["schema"] == 1 and record["task_key"] == key,
                    "invalid durable task record")
            return record
        except (OSError, ValueError, KeyError, TypeError):
            raise ContractError("reserved task has no readable record; reconcile, never resubmit")

    def start(self, workflow, key, parameters, source, manifest_sha256, host=None, profile_path=None):
        require(isinstance(source, dict) and set(source) ==
                {"root", "head", "patch_sha256", "untracked_sha256"}, "source identity required")
        self.require_external(source["root"])
        require(re.fullmatch(r"[0-9a-f]{64}", manifest_sha256), "manifest digest required")
        signature = digest(dict(profile=workflow.profile, parameters=parameters, source=source,
                                manifest=manifest_sha256, requested_host=host))
        path = self.path(key)
        # Validate everything and select a host before taking the permanent
        # reservation. Probing hosts does not submit compute.
        if os.path.exists(path):
            return self.existing(workflow, key, signature, parameters, source, manifest_sha256)
        ref, argv = workflow.prepare(parameters, host)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            # A competing process owns this request, including an empty/crashed
            # reservation. It alone has submission rights.
            return self.existing(workflow, key, signature, parameters, source, manifest_sha256)
        sync_directory(self.directory)
        record = dict(schema=1, task_key=key, intent_sha256=signature,
                      source=source, profile_id=workflow.profile["id"],
                      profile_version=workflow.profile["version"], profile_path=profile_path,
                      manifest_sha256=manifest_sha256, bundle_manifest=bundle_manifest(),
                      required_artifacts=workflow.profile["expected_artifacts"],
                      created_at=time.time(), reference=ref,
                      submission="submission-unknown", request_protocol=1)
        # Persist uncertainty BEFORE dispatch. The task id in it is the request
        # key the tracker will hold, so a lost acknowledgement stays resolvable.
        atomic_json(os.path.join(path, "task.json"), record)
        result = workflow.dispatch(dict(ref), self.job_argv(workflow, argv, ref, source, manifest_sha256))
        return self.settle(path, record, result)

    @staticmethod
    def job_argv(workflow, argv, ref, source, manifest_sha256):
        if workflow.profile["engineering_report"] is None:
            return argv
        # These are expected identities, NOT measurements of consumed inputs.
        # Report adapters must compare actual inputs before asserting them.
        return ["/usr/bin/env",
                "ASICJOBS_EXPECTED_MANIFEST_SHA256=" + manifest_sha256,
                "ASICJOBS_EXPECTED_REQUEST_SHA256=" + ref["request_sha256"],
                "ASICJOBS_EXPECTED_SOURCE_SHA256=" + digest(source)] + argv

    def settle(self, path, record, result):
        """Record a dispatch or reconciliation outcome; a found job id is kept."""
        if result["reference"]["job_id"] is not None:
            record["reference"] = result["reference"]
            record["submission"] = result["observation"]
            atomic_json(os.path.join(path, "task.json"), record)
        self.record_observation(path, result)
        result["task_key"] = record["task_key"]
        result["state_path"] = os.path.join(path, "task.json")
        return result

    def unresolved(self, key):
        return dict(schema=1, kind="workflow", task_key=key, task_id=None,
                    host=None, job_id=None, observation="submission-unknown",
                    evidence="unchecked", engineering="unchecked", next_action="reconcile",
                    state_path=os.path.join(self.path(key), "task.json"),
                    reason="Reserved task has no readable intent; never resubmit automatically.")

    def existing(self, workflow, key, signature, parameters, source, manifest_sha256):
        try:
            record = self.read(key)
        except ContractError:
            return self.unresolved(key)
        require(record["intent_sha256"] == signature, "task-key belongs to a different request")
        if record["reference"]["job_id"] is None and record.get("request_protocol") == 1:
            # The same request again after a lost acknowledgement: attach, or
            # dispatch under the same key if the tracker has nothing for it.
            argv = self.job_argv(workflow, workflow.build_argv(parameters), record["reference"],
                                 source, manifest_sha256)
            result = self.settle(self.path(key), record, workflow.reconcile(record["reference"], argv))
            if result["reference"]["job_id"] is None:
                return result
        return self.observe(workflow, key)

    def record_observation(self, path, result):
        # Separate from the immutable intent/ack pointer: a concurrent reader
        # can never overwrite a newly acknowledged job with an older null ID.
        atomic_json(os.path.join(path, "observation.json"),
                    dict(observed_at=time.time(), observation=result["observation"],
                         evidence=result["evidence"], engineering=result["engineering"]))

    def observe(self, workflow, key, collect=False):
        try:
            record = self.read(key)
        except ContractError:
            if os.path.isdir(self.path(key)):
                return self.unresolved(key)
            raise
        if record["reference"]["job_id"] is None and record.get("request_protocol") == 1:
            # Look up only: a read never dispatches.
            result = self.settle(self.path(key), record, workflow.reconcile(record["reference"]))
            if result["reference"]["job_id"] is None:
                return result
            record = self.read(key)
        identity = dict(manifest_sha256=record["manifest_sha256"],
                        request_sha256=record["reference"]["request_sha256"],
                        source_sha256=digest(record["source"]))
        result = workflow.observe(record["reference"], collect=collect, identity=identity)
        self.record_observation(self.path(key), result)
        result["task_key"] = key
        result["state_path"] = os.path.join(self.path(key), "task.json")
        if collect:
            result["evidence_summary"] += "\n[Task intent and job reference](task.json) · [Collection JSON](collection.json)\n"
            atomic_json(os.path.join(self.path(key), "collection.json"), result)
            atomic_text(os.path.join(self.path(key), "collection.md"), result["evidence_summary"])
            result["summary_path"] = os.path.join(self.path(key), "collection.md")
        return result

    def listing(self, workflow):
        entries = []
        for name in sorted(os.listdir(self.directory)):
            if not re.fullmatch(r"request-[0-9a-f]{64}", name):
                continue
            path = os.path.join(self.directory, name, "task.json")
            try:
                with open(path, encoding="utf-8") as fh:
                    record = json.load(fh)
                if record["reference"]["repository"] != workflow.profile["repository"]:
                    continue
                entries.append(dict(task_key=record["task_key"], reference=record["reference"],
                                    profile_path=record["profile_path"], state_path=path))
            except (OSError, ValueError, TypeError, KeyError):
                entries.append(dict(state_path=path, observation="unreadable-reservation",
                                    next_action="reconcile"))
        return dict(schema=1, kind="workflow-tasks", tasks=entries,
                    notice="Pointers only; query resume for current state. Unreadable reservations must not be retried.")
