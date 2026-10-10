"""Reusable snapshot/deployment boundary for trusted process-local adapters.

Python 3.6+, stdlib. An adapter supplies SPEC, preflight(snapshot), and
execute(workspace, case, manifest). Source and private state stay separate.

A repository may hold several adapters. The first one lives at
deployment/bnl/tracked_job.py and its SPEC names no path. Any further one
lives at deployment/bnl/tracked_jobs/<id>.py and its SPEC says so with
"adapter". Each adapter is packaged, staged and profiled on its own, so one
adapter's declaration never enters another's snapshot or profile. "case_checks"
optionally narrows the checks and corners that one case reports.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import signal
from pathlib import Path
import subprocess
import sys
import time

from .adapter import require_attached
from .remote import Transport
from .state import source_identity
from .workflow import absolute, digest, relative, validate_profile


#: The one adapter a port had before a repository could hold several. A SPEC
#: without "adapter" is this one: its package, profile and digest are unchanged.
LEGACY_ADAPTER = "deployment/bnl/tracked_job.py"
#: Every further adapter: deployment/bnl/tracked_jobs/<SPEC id>.py
ADAPTERS = "deployment/bnl/tracked_jobs/"


def adapter_path(adapter):
    """The adapter's repository path, which is also its path inside a snapshot."""
    s = adapter.SPEC
    if "adapter" not in s:
        return LEGACY_ADAPTER
    if not relative(s["adapter"]) or s["adapter"] != ADAPTERS + str(s.get("id")) + ".py":
        raise ValueError("a further adapter lives at " + ADAPTERS + "<id>.py")
    return s["adapter"]


def requirements(spec):
    """case -> (checks, corners) that case must report. Every case reports the SPEC's checks and
    corners, unless "case_checks" names a subset for it (a post-layout case can report fewer
    checks, or fewer corners, than a schematic case)."""
    narrowed = spec.get("case_checks", {})
    if (not isinstance(narrowed, dict) or not set(narrowed) <= set(spec["cases"])
            or not all(isinstance(e, dict) and set(e) <= {"checks", "corners"} for e in narrowed.values())):
        raise ValueError("case_checks must map declared cases to checks/corners")
    result = {}
    for case in spec["cases"]:
        entry = narrowed.get(case, {})
        pair = []
        for key in ("checks", "corners"):
            values = entry.get(key, spec[key])
            if (not isinstance(values, list) or not values or len(set(values)) != len(values)
                    or not set(values) <= set(spec[key])):
                raise ValueError("case_checks for %s: %s must be a nonempty subset of the SPEC's" % (case, key))
            pair.append(list(values))
        result[case] = tuple(pair)
    return result


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


#: A denied or timed-out licence checkout: report.sh's SIG_LICENSE, which the failure report
#: counts (SPECTRE-209 is what a run whose +lqtimeout expired ends with). Keep the two equal;
#: test_pilot compares them.
LICENSE_DENIED = re.compile(r"""SPECTRE-209|[Ll]icen[cs]e.{0,60}(unavailable|denied|not available|exhausted|expired|could not be checked out|check ?out (failed|error))|([Ff]ail(ed|ure)?|[Cc]annot|[Cc]ould not|[Uu]nable) to (check ?out|obtain|acquire|get) .{0,30}[Ll]icen[cs]e|(FLEXnet|FLEXlm) ([Ll]icensing )?[Ee]rror|[Ll]icen[cs]e server .{0,40}(down|not responding)|[Cc]annot connect to (the )?[Ll]icen[cs]e server|Licensed number of users already reached|No such feature exists""")

#: How much of a segment's output a licence scan reads (its tail: a denial ends the run).
SCAN_TAIL = 1048576


class LicenseWait(RuntimeError):
    """A segment was still waiting on a licence when it ran out of attempts. The case is refused
    (pilot.refuse names it) rather than scored: a queue wait is neither an engineering result nor a
    tool failure, and a run of the same case later can succeed unchanged."""


def _stamp(path):
    try:
        st = os.stat(str(path))
        return st.st_size, st.st_mtime
    except OSError:
        return None


def _denied(segment, before):
    """Did this attempt end on a licence denial? Only what this attempt wrote counts: the output
    from where it started, and a tool log only if the attempt touched it. A denial left by an
    earlier attempt must not turn a later, different failure into a queue wait."""
    texts = []
    out = Path(segment["out"])
    start = before[0][0] if before[0] else 0
    try:
        with out.open("rb") as fh:
            size = out.stat().st_size
            fh.seek(max(start, size - SCAN_TAIL))
            texts.append(fh.read())
    except OSError:
        pass
    for log, stamp in zip(segment.get("logs", []), before[1:]):
        if _stamp(log) in (None, stamp):
            continue
        try:
            with open(str(log), "rb") as fh:
                fh.seek(max(0, os.path.getsize(str(log)) - SCAN_TAIL))
                texts.append(fh.read())
        except OSError:
            pass
    return any(LICENSE_DENIED.search(t.decode("utf-8", "replace")) for t in texts)


def segments(work, slots, env=None, timeout=None, license_retries=1, poll=1.0):
    """Run independent segments of one case on this host, at most `slots` at once, and wait for
    all of them. Every segment is its own process group owned by this job: a signal, a timeout or
    an error stops every running group (as foreground does), so nothing outlives the tracked job.

    work: one dict per segment, argv (list) and out (its stdout and stderr), and optionally logs,
    the tool logs to scan as well (spectre's +log). A segment that exits on a licence denial (its
    +lqtimeout ran out) is queued again, up to `license_retries` more times, and is not a failure;
    still denied, LicenseWait is raised before anything can be scored. `timeout` bounds the whole
    batch, licence waits included (+lqtimeout bounds each one).
    -> [dict(index, rc, outcome="done"|"failed", attempts)] in segment order; the adapter decides
    what a failed segment means and runs its scoring step itself."""
    if type(slots) is not int or slots < 1 or not work:
        raise ValueError("segments need work and at least one slot")
    if not all(isinstance(w.get("argv"), list) and w["argv"] and w.get("out") for w in work):
        raise ValueError("every segment needs argv and out")
    pending, running = list(range(len(work))), {}
    attempts, results, denied = [0] * len(work), [None] * len(work), []
    deadline = None if timeout is None else time.monotonic() + timeout
    previous = {}

    def interrupted(number, frame):
        raise SystemExit(128 + number)

    def stop(process, sig):
        try: os.killpg(process.pid, sig)
        except ProcessLookupError: pass

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, interrupted)
        while pending or running:
            while pending and len(running) < slots:
                i = pending.pop(0)
                attempts[i] += 1
                out = Path(work[i]["out"])
                out.parent.mkdir(parents=True, exist_ok=True)
                before = [_stamp(out)] + [_stamp(log) for log in work[i].get("logs", [])]
                with out.open("ab") as fh:
                    process = subprocess.Popen(work[i]["argv"], env=env, stdin=subprocess.DEVNULL, stdout=fh,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                running[i] = (process, before)
            for i, (process, before) in list(running.items()):
                rc = process.poll()
                if rc is None:
                    continue
                # A finished leader must not leave background workers either.
                stop(process, signal.SIGTERM); stop(process, signal.SIGKILL)
                del running[i]
                if rc != 0 and _denied(work[i], before):
                    if attempts[i] <= license_retries:
                        pending.append(i)
                    else:
                        denied.append(i)
                    continue
                results[i] = dict(index=i, rc=rc, outcome="done" if rc == 0 else "failed", attempts=attempts[i])
            if deadline is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired("segments", timeout)
            if running:
                time.sleep(poll)
    finally:
        for process, _ in running.values():
            stop(process, signal.SIGTERM)
        for process, _ in running.values():
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: pass
            stop(process, signal.SIGKILL)
            process.wait()
        for sig, handler in previous.items(): signal.signal(sig, handler)
    if denied:
        raise LicenseWait("%d of %d segments still waiting on a licence after %d attempts (segments %s)"
                          % (len(denied), len(work), license_retries + 1, ",".join(map(str, sorted(denied)))))
    return results


def package(adapter, repo, output):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if output == repo or repo in output.parents:
        raise ValueError("package must be outside source checkout")
    paths = set(adapter.SPEC["files"] + [adapter_path(adapter)])
    # The vendored tests never run on the cluster. Packaging them bound every
    # snapshot to them, so a test-only re-vendor made all deployed profiles stale.
    paths.update(p.relative_to(repo).as_posix() for p in (repo / "deployment/bnl/jobs").rglob("*")
                 if p.is_file() and (p.suffix in (".py", ".sh") or p.name == "runjob")
                 and not p.name.startswith("test_"))
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
    packaged = dict(files)                  # repo files only; remote inputs follow
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
                    adapter_sha256=digest(adapter.SPEC), files=files, packaged=packaged,
                    external=external)
    verify(snapshot, manifest)
    write(snapshot / "manifest.json", manifest)
    return manifest


def profile(adapter, snapshot, host, work_root, mode):
    s = adapter.SPEC
    report = dict(parser="json-v1", path="report.json", design=s["design"], top=s["top"],
                  checks=s["checks"], corners=s["corners"])
    if "case_checks" in s:
        # Every case spelled out: the evidence reader is handed exactly one case's contract.
        report["per_case"] = dict(parameter="case", cases={case: dict(checks=checks, corners=corners)
                                  for case, (checks, corners) in requirements(s).items()})
    return validate_profile(dict(schema=1, id=s["id"], version="1", repository=s["repository"],
        isolated_bundle=True, transport_mode=mode, work_root=work_root, workspace="unique-child",
        lifecycle="foreground", host_policy=dict(allowed=[host], default=host, allow_auto=False),
        parameters={"case": {"type": "string", "choices": s["cases"]}},
        argv=["/usr/bin/python3", "-B", snapshot + "/" + adapter_path(adapter), "run", "--snapshot", snapshot,
              "--case", {"parameter": "case"}], expected_artifacts=["native.json", "report.json"], progress=None,
        engineering_report=report))


def deploy(adapter, repo, package_dir, snapshot, host, work_root, output):
    if not absolute(snapshot) or not absolute(work_root):
        raise ValueError("absolute POSIX remote paths required")
    mode = "winssh" if os.name == "nt" else "ssh"
    # Validate the profile BEFORE anything is uploaded: a declaration the
    # workflow will refuse (e.g. a corner that is not an identifier) used to
    # leave a staged snapshot behind with no usable profile.
    new_profile = profile(adapter, snapshot, host, work_root, mode)
    # One output directory is one adapter's current profile. Deploying a second adapter over it
    # would silently retarget every task that names this profile.
    current = Path(output).resolve() / "profile.json"
    if current.exists() and load(current).get("id") != new_profile["id"]:
        raise ValueError("output holds the profile of adapter %r; deploy %r to its own output"
                         % (load(current).get("id"), new_profile["id"]))
    package_dir = Path(package_dir).resolve()
    source = load(package_dir / "source.json")
    if source["adapter_sha256"] != digest(adapter.SPEC):
        raise ValueError("checkout changed since packaging")
    # The packaged files bind the deployment, not the whole checkout (see
    # state.bind_source): an unrelated commit between package and deploy is fine.
    for p, expected in source["files"].items():
        if not relative(p) or not (Path(repo) / p).is_file() or sha(Path(repo) / p) != expected:
            raise ValueError("checkout changed since packaging: " + p)
    paths = sorted(source["files"])
    adapter.validate_upload(Path(repo).resolve(), paths)
    for p in paths:
        if not relative(p) or sha(package_dir / p) != source["files"][p]:
            raise ValueError("package changed")
    request = dict(snapshot=snapshot, work_root=work_root, adapter=adapter_path(adapter),
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
adapter=pilot.module(p/r['adapter'])
snapshot=Path(r['snapshot'])
if snapshot.exists():
 m=pilot.load(snapshot/'manifest.json'); pilot.verify(snapshot,m); packaged=pilot.load(p/'source.json')
 if m['source']!=packaged['source']: raise ValueError('snapshot source differs')
 if m['adapter_sha256']!=packaged['adapter_sha256']: raise ValueError('snapshot holds another adapter')
else: m=pilot.stage(adapter,p,snapshot)
Path(r['work_root']).mkdir(parents=True,exist_ok=True)
print(json.dumps(dict(schema=1,manifest=m)))
'''.replace("REQUEST", encoded)
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
    write(output / "profile.json", new_profile)
    return dict(profile=str(output / "profile.json"), preflight="passed", licensed_tools_used=False)


#: A refusal reason is one line of at most this many characters.
REFUSAL_MAX = 300


def redact(text):
    """One line, clipped, every absolute path cut to `.../<basename>`. A refusal reason is the
    adapter's own message, but it can quote a path (a model include, a tool install) that must
    stay on the cluster, as the logs do."""
    text = " ".join(str(text).split())
    text = re.sub(r"(?<![\w.~])/(?:[^\s/'\"]+/)+([^\s/'\"]*)", r".../\1", text)
    return text[:REFUSAL_MAX]


def refuse(stage, exc):
    """Leave the refusal's reason where the tracker's evidence reader looks for it (refusal.json in the
    job workspace). The job still fails as before; the failure report can then NAME the refusal
    instead of showing only "execution failed" and a traceback count (study §8.2: named failure).
    Only inside a tracked job (ASICJOBS_ID set): an untracked invocation's working directory is
    somebody's checkout, not a job workspace (a test run left one in tsmc28's deployment/bnl)."""
    if not os.environ.get("ASICJOBS_ID"):
        return
    try:
        write(Path.cwd() / "refusal.json", dict(schema=1, kind="refusal", job_id=os.environ.get("ASICJOBS_ID"),
                                                stage=stage, error=type(exc).__name__, reason=redact(exc)))
    except OSError:
        pass


def run(adapter, snapshot, case):
    stage = ["identity"]
    try:
        return _run(adapter, snapshot, case, stage)
    except Exception as exc:   # SystemExit (a signal) is not a refusal
        refuse(stage[0], exc)
        raise


def _run(adapter, snapshot, case, stage):
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
    stage[0] = "execute"
    checks = adapter.execute(work, case, manifest)
    stage[0] = "checks"
    rec.progress(1, 2, "stages")
    verify(snapshot, manifest)
    names, corners = requirements(s)[case]
    required = {(n, c) for n in names for c in corners}
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
