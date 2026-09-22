"""Reusable snapshot/deployment boundary for trusted process-local adapters.

Python 3.6+, stdlib. An adapter supplies SPEC, preflight(snapshot), and
execute(workspace, case, manifest). Source and private state stay separate.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys

from .adapter import require_attached
from .remote import Transport
from .state import source_identity
from .workflow import absolute, digest, relative, validate_profile


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def copy_checked(src, dst, expected):
    if Path(src).is_symlink():
        raise ValueError("symlink input: " + str(src))
    data = Path(src).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("input changed: " + str(src))
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(data)


def module(path):
    spec = importlib.util.spec_from_file_location("process_adapter", str(path))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def wrapper_probe(wrapper, script):
    """Pass probe source on stdin: tcsh activation must not reinterpret -c quotes."""
    result = subprocess.run([wrapper, "python3", "-"], input=script,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=45)
    if result.returncode:
        raise RuntimeError("wrapper preflight failed: " + result.stderr[-1500:])
    return result.stdout


def foreground(argv, env=None, timeout=None):
    """Wait for an owned process group; cancellation/timeout cannot orphan tools."""
    process = subprocess.Popen(argv, env=env, start_new_session=True)
    previous = {}
    def interrupted(number, frame):
        raise SystemExit(128 + number)
    def stop_group(sig):
        try: os.killpg(process.pid, sig)
        except ProcessLookupError: pass
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, interrupted)
        return process.wait(timeout=timeout)
    finally:
        # Even a successful foreground leader must not leave background workers.
        stop_group(signal.SIGTERM)
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired: pass
        stop_group(signal.SIGKILL)
        process.wait()
        for sig, handler in previous.items(): signal.signal(sig, handler)


def package(adapter, repo, output):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if output == repo or repo in output.parents:
        raise ValueError("package must be outside source checkout")
    paths = set(adapter.SPEC["files"] + ["deployment/bnl/tracked_job.py"])
    paths.update(p.relative_to(repo).as_posix() for p in (repo / "deployment/bnl/jobs").rglob("*")
                 if p.is_file() and (p.suffix in (".py", ".sh") or p.name == "runjob"))
    if not all(relative(p) for p in paths):
        raise ValueError("invalid package paths")
    # The process owns disclosure policy; an upload must never bypass it.
    adapter.validate_upload(repo, sorted(paths))
    source = source_identity(str(repo))
    files = {p: sha(repo / p) for p in sorted(paths)}
    output.mkdir(parents=True, exist_ok=False)
    for p, expected in files.items():
        copy_checked(repo / p, output / p, expected)
    write(output / "source.json", dict(schema=1, source=source, files=files,
                                       adapter_sha256=digest(adapter.SPEC)))
    return dict(package=str(output), source_sha256=digest(source))


def verify(snapshot, manifest):
    if not all(relative(p) for p in manifest["files"]):
        raise ValueError("invalid manifest paths")
    for p, expected in manifest["files"].items():
        if (snapshot / p).is_symlink() or sha(snapshot / p) != expected:
            raise ValueError("snapshot changed: " + p)
    for p, expected in manifest["external"].items():
        if not absolute(p) or sha(p) != expected:
            raise ValueError("external dependency changed: " + p)


def stage(adapter, package_dir, snapshot):
    """Only additive private files. Never update a live work tree or OA library."""
    package_dir, snapshot = Path(package_dir), Path(snapshot)
    source = load(package_dir / "source.json")
    if source["adapter_sha256"] != digest(adapter.SPEC):
        raise ValueError("adapter declaration differs")
    snapshot.mkdir(parents=True, exist_ok=False)
    files = dict(source["files"])
    for p, expected in files.items():
        if not relative(p):
            raise ValueError("invalid source path")
        copy_checked(package_dir / p, snapshot / p, expected)
    for dest, src in adapter.SPEC.get("remote_inputs", {}).items():
        if not relative(dest) or not absolute(src) or dest in files:
            raise ValueError("invalid remote input mapping")
        files[dest] = sha(src)
        copy_checked(src, snapshot / dest, files[dest])
    # Probe imports/environment under the real process wrapper, without compute.
    external = adapter.preflight(snapshot)
    manifest = dict(schema=1, repository=adapter.SPEC["repository"], source=source["source"],
                    adapter_sha256=digest(adapter.SPEC), files=files, external=external)
    verify(snapshot, manifest)
    write(snapshot / "manifest.json", manifest)
    return manifest


def profile(adapter, snapshot, host, work_root, mode):
    s = adapter.SPEC
    return validate_profile(dict(schema=1, id=s["id"], version="1", repository=s["repository"],
        isolated_bundle=True, transport_mode=mode, work_root=work_root, workspace="unique-child",
        lifecycle="foreground", host_policy=dict(allowed=[host], default=host, allow_auto=False),
        parameters={"case": {"type": "string", "choices": s["cases"]}},
        argv=["/usr/bin/python3", snapshot + "/deployment/bnl/tracked_job.py", "run", "--snapshot", snapshot,
              "--case", {"parameter": "case"}], expected_artifacts=["native.json", "report.json"], progress=None,
        engineering_report=dict(parser="json-v1", path="report.json", design=s["design"], top=s["top"],
                                checks=s["checks"], corners=s["corners"])))


def deploy(adapter, repo, package_dir, snapshot, host, work_root, output):
    if not absolute(snapshot) or not absolute(work_root):
        raise ValueError("absolute POSIX remote paths required")
    package_dir = Path(package_dir).resolve()
    source = load(package_dir / "source.json")
    if source["source"] != source_identity(str(repo)) or source["adapter_sha256"] != digest(adapter.SPEC):
        raise ValueError("checkout changed since packaging")
    paths = sorted(source["files"])
    adapter.validate_upload(Path(repo).resolve(), paths)
    for p in paths:
        if not relative(p) or sha(package_dir / p) != source["files"][p]:
            raise ValueError("package changed")
    request = dict(snapshot=snapshot, work_root=work_root,
                   files={p: base64.b64encode((package_dir / p).read_bytes()).decode("ascii")
                          for p in paths + ["source.json"]})
    encoded = base64.b64encode(json.dumps(request).encode()).decode("ascii")
    script = '''import base64, json, sys
from pathlib import Path
r=json.loads(base64.b64decode("REQUEST"))
p=Path(r['snapshot']+'.package'); p.mkdir(parents=True,exist_ok=True)
for name,encoded in r['files'].items():
 target=p/name; data=base64.b64decode(encoded); target.parent.mkdir(parents=True,exist_ok=True)
 if target.exists():
  if target.read_bytes()!=data: raise ValueError('immutable upload differs')
 else:
  with target.open('xb') as f: f.write(data)
sys.path.insert(0,str(p/'deployment/bnl'))
from jobs import pilot
adapter=pilot.module(p/'deployment/bnl/tracked_job.py')
snapshot=Path(r['snapshot'])
if snapshot.exists():
 m=pilot.load(snapshot/'manifest.json'); pilot.verify(snapshot,m)
 if m['source']!=pilot.load(p/'source.json')['source']: raise ValueError('snapshot source differs')
else: m=pilot.stage(adapter,p,snapshot)
Path(r['work_root']).mkdir(parents=True,exist_ok=True)
print(json.dumps(dict(schema=1,manifest=m)))
'''.replace("REQUEST", encoded)
    mode = "winssh" if os.name == "nt" else "ssh"
    result = Transport(host=host, mode=mode, isolated_bundle=True, timeout=60).run_sh(
        "python3 - <<'PY'\n" + script + "\nPY\n", retry_enoent=False)
    if not result.ok:
        raise RuntimeError("deployment not confirmed; inspect snapshot: " + str(result.reason) + " " + result.stderr[-1800:])
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    # Preserve earlier task profiles when updating the convenient current alias.
    if (output / "profile.json").exists() and (output / "manifest.json").exists():
        old_profile = load(output / "profile.json")
        archive = output / "revisions" / digest(old_profile)
        archive.mkdir(parents=True, exist_ok=True)
        write(archive / "profile.json", old_profile)
        write(archive / "manifest.json", load(output / "manifest.json"))
    write(output / "manifest.json", result.data["manifest"])
    write(output / "profile.json", profile(adapter, snapshot, host, work_root, mode))
    return dict(profile=str(output / "profile.json"), preflight="passed", licensed_tools_used=False)


def run(adapter, snapshot, case):
    s = adapter.SPEC
    if case not in s["cases"]:
        raise ValueError("unsupported case")
    snapshot = Path(snapshot).resolve(); manifest = load(snapshot / "manifest.json")
    if (digest(manifest) != os.environ["ASICJOBS_EXPECTED_MANIFEST_SHA256"]
            or digest(manifest["source"]) != os.environ["ASICJOBS_EXPECTED_SOURCE_SHA256"]
            or manifest["adapter_sha256"] != digest(s)):
        raise ValueError("submitted identity differs from snapshot")
    verify(snapshot, manifest)
    recorder = module(Path(os.environ["ASICJOBS_BINDIR"]) / "jobrec.py")
    rec = require_attached(recorder.begin(flow=s["id"], target=case, total=2))
    work = Path.cwd().resolve()
    if any(work.iterdir()):
        raise ValueError("workspace is not empty")
    for p, expected in manifest["files"].items():
        copy_checked(snapshot / p, work / "source" / p, expected)
    rec.progress(0, 2, "stages")
    checks = adapter.execute(work, case, manifest)
    rec.progress(1, 2, "stages")
    verify(snapshot, manifest)
    required = {(n, c) for n in s["checks"] for c in s["corners"]}
    if (len(checks) != len(required) or {(c["name"], c["corner"]) for c in checks} != required
            or any(c["status"] not in ("pass", "fail") for c in checks)):
        raise ValueError("adapter omitted declared checks")
    write(work / "report.json", dict(schema=1, job_id=os.environ["ASICJOBS_ID"], repository=s["repository"],
          design=s["design"], top=s["top"], manifest_sha256=digest(manifest), source_sha256=digest(manifest["source"]),
          request_sha256=os.environ["ASICJOBS_EXPECTED_REQUEST_SHA256"], checks=checks))
    rec.finalize(0)
    return 0


def main(adapter):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd")
    q = sub.add_parser("package"); q.add_argument("--repo", required=True); q.add_argument("--output", required=True)
    q = sub.add_parser("deploy")
    for name in ("repo", "package", "snapshot", "host", "work-root", "output"):
        q.add_argument("--" + name, required=True)
    q = sub.add_parser("run"); q.add_argument("--snapshot", required=True); q.add_argument("--case", required=True)
    a = p.parse_args()
    if a.cmd == "run": return run(adapter, a.snapshot, a.case)
    if a.cmd == "package": result = package(adapter, a.repo, a.output)
    elif a.cmd == "deploy": result = deploy(adapter, a.repo, a.package, a.snapshot, a.host, a.work_root, a.output)
    else: p.error("choose package, deploy or run")
    print(json.dumps(result, indent=2)); return 0
