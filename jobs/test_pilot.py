import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from . import pilot
from .remote import Transport
from .state import TaskStore
from .workflow import ContractError, Workflow, digest, validate_profile


LEGACY = dict(id="ota", repository="fixture", design="ota", top="ota", cases=["schematic"],
              checks=["gain", "pm"], corners=["tt"], files=["bench.sh"])
#: A second adapter in the same repository whose post-layout case reports fewer checks/corners.
FURTHER = dict(id="sar8", adapter="deployment/bnl/tracked_jobs/sar8.py", repository="fixture", design="sar8",
               top="sar8_core", cases=["sch", "pex"], checks=["frame", "sndr", "enob"], corners=["tt", "ss"],
               case_checks={"pex": dict(checks=["sndr", "enob"], corners=["tt"])}, files=["sar8/run_core.sh"])


def adapter(spec):
    class Adapter:
        SPEC = spec
        @staticmethod
        def validate_upload(repo, paths):
            pass
    return Adapter


def git_repo(repo, names):
    for name in names:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("x\n")
    commit(repo)


def commit(repo):
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)


class LegacyAdapterTests(unittest.TestCase):
    def test_single_adapter_profile_is_unchanged(self):
        # Spelled out, not recomputed: a deployed legacy profile must keep its digest.
        expected = dict(schema=1, id="ota", version="1", repository="fixture", isolated_bundle=True,
                        transport_mode="ssh", work_root="/r/runs", workspace="unique-child", lifecycle="foreground",
                        host_policy=dict(allowed=["h"], default="h", allow_auto=False),
                        parameters={"case": {"type": "string", "choices": ["schematic"]}},
                        argv=["/usr/bin/python3", "-B", "/r/snap/deployment/bnl/tracked_job.py", "run",
                              "--snapshot", "/r/snap", "--case", {"parameter": "case"}],
                        expected_artifacts=["native.json", "report.json"], progress=None,
                        engineering_report=dict(parser="json-v1", path="report.json", design="ota", top="ota",
                                                checks=["gain", "pm"], corners=["tt"]))
        got = pilot.profile(adapter(LEGACY), "/r/snap", "h", "/r/runs", "ssh")
        self.assertEqual(expected, got)
        self.assertEqual(digest(expected), digest(got))
        self.assertIs(Workflow(got).report_for(dict(request_sha256="0" * 64)), got["engineering_report"])
        self.assertEqual({"schematic": (["gain", "pm"], ["tt"])}, pilot.requirements(LEGACY))


class MultiAdapterTests(unittest.TestCase):
    def test_further_adapter_has_its_own_path(self):
        got = pilot.profile(adapter(FURTHER), "/r/sar8", "h", "/r/runs", "ssh")
        self.assertEqual("/r/sar8/deployment/bnl/tracked_jobs/sar8.py", got["argv"][2])
        self.assertEqual("sar8", got["id"])
        for wrong in ("deployment/bnl/tracked_jobs/other.py", "deployment/bnl/tracked_job.py", "../sar8.py"):
            with self.subTest(path=wrong), self.assertRaises(ValueError):
                pilot.adapter_path(adapter(dict(FURTHER, adapter=wrong)))

    def test_packages_carry_only_their_own_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            git_repo(repo, ["bench.sh", "sar8/run_core.sh", "deployment/bnl/tracked_job.py",
                            "deployment/bnl/tracked_jobs/sar8.py", "deployment/bnl/jobs/pilot.py"])
            ota = pilot.package(adapter(LEGACY), repo, Path(tmp) / "ota-1")
            sar8 = pilot.package(adapter(FURTHER), repo, Path(tmp) / "sar8")
            files = lambda d: json.loads((Path(d) / "source.json").read_text())
            self.assertIn("deployment/bnl/tracked_job.py", files(ota["package"])["files"])
            self.assertNotIn("deployment/bnl/tracked_jobs/sar8.py", files(ota["package"])["files"])
            self.assertNotIn("sar8/run_core.sh", files(ota["package"])["files"])
            self.assertIn("deployment/bnl/tracked_jobs/sar8.py", files(sar8["package"])["files"])
            self.assertNotIn("deployment/bnl/tracked_job.py", files(sar8["package"])["files"])
            self.assertNotEqual(files(ota["package"])["adapter_sha256"], files(sar8["package"])["adapter_sha256"])
            # Editing the second adapter and its bench leaves the first one's package as it was.
            (repo / "deployment/bnl/tracked_jobs/sar8.py").write_text("changed\n")
            (repo / "sar8/run_core.sh").write_text("changed\n")
            commit(repo)
            again = pilot.package(adapter(LEGACY), repo, Path(tmp) / "ota-2")
            self.assertEqual(files(ota["package"])["files"], files(again["package"])["files"])
            self.assertEqual(files(ota["package"])["adapter_sha256"], files(again["package"])["adapter_sha256"])

    def test_deploy_refuses_another_adapters_output(self):
        def no_transport(*args, **kwargs):
            raise AssertionError("deploy reached the transport")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "profile"; output.mkdir()
            pilot.write(output / "profile.json", pilot.profile(adapter(LEGACY), "/r/ota", "h", "/r/runs", "ssh"))
            saved, pilot.Transport = pilot.Transport, no_transport
            try:
                with self.assertRaisesRegex(ValueError, "own output"):
                    pilot.deploy(adapter(FURTHER), tmp, tmp, "/r/sar8", "h", "/r/runs", str(output))
            finally:
                pilot.Transport = saved
            self.assertEqual("ota", pilot.load(output / "profile.json")["id"])


class CaseCheckTests(unittest.TestCase):
    def test_requirements_narrow_per_case(self):
        self.assertEqual({"sch": (["frame", "sndr", "enob"], ["tt", "ss"]), "pex": (["sndr", "enob"], ["tt"])},
                         pilot.requirements(FURTHER))
        for bad in ({"post": dict(checks=["sndr"])}, {"pex": dict(checks=["thd"])}, {"pex": dict(checks=[])},
                    {"pex": dict(corners=["ff"])}, {"pex": dict(checks=["sndr", "sndr"])},
                    {"pex": dict(views=["x"])}, ["pex"]):
            with self.subTest(case_checks=bad), self.assertRaises(ValueError):
                pilot.requirements(dict(FURTHER, case_checks=bad))

    def test_report_contract_follows_the_requested_case(self):
        profile = pilot.profile(adapter(FURTHER), "/r/sar8", "h", "/r/runs", "ssh")
        self.assertEqual(dict(parameter="case", cases={"sch": dict(checks=["frame", "sndr", "enob"], corners=["tt", "ss"]),
                                                       "pex": dict(checks=["sndr", "enob"], corners=["tt"])}),
                         profile["engineering_report"]["per_case"])
        w = Workflow(profile)
        pex = w.report_for(dict(request_sha256=digest({"case": "pex"})))
        self.assertEqual(dict(parser="json-v1", path="report.json", design="sar8", top="sar8_core",
                              checks=["sndr", "enob"], corners=["tt"]), pex)
        sch = w.report_for(dict(request_sha256=digest({"case": "sch"})))
        self.assertEqual((["frame", "sndr", "enob"], ["tt", "ss"]), (sch["checks"], sch["corners"]))
        with self.assertRaises(ContractError):
            w.report_for(dict(request_sha256=digest({"case": "post"})))

    def test_profile_refuses_unsound_per_case(self):
        good = pilot.profile(adapter(FURTHER), "/r/sar8", "h", "/r/runs", "ssh")
        def broken(change):
            p = json.loads(json.dumps(good)); change(p); return p
        cases = [
            lambda p: p["engineering_report"]["per_case"]["cases"].pop("sch"),
            lambda p: p["engineering_report"]["per_case"]["cases"]["pex"].update(checks=["thd"]),
            lambda p: p["engineering_report"]["per_case"]["cases"]["pex"].update(corners=[]),
            lambda p: p["engineering_report"]["per_case"].update(parameter="view"),
            lambda p: p["parameters"].update(seed={"type": "integer"}),
            lambda p: p["parameters"]["case"].pop("choices"),
            lambda p: p["engineering_report"]["per_case"]["cases"]["pex"].update(extra=1),
        ]
        for i, change in enumerate(cases):
            with self.subTest(case=i), self.assertRaises(ContractError):
                validate_profile(broken(change))


class LicenseSignatureTests(unittest.TestCase):
    def test_license_pattern_is_report_sh_signature(self):
        text = (Path(__file__).parent / "bin" / "report.sh").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if l.startswith("SIG_LICENSE="))
        self.assertEqual(line[len("SIG_LICENSE='"):-1], pilot.LICENSE_DENIED.pattern)
        self.assertTrue(pilot.LICENSE_DENIED.search("ERROR (SPECTRE-209): license queue timed out"))
        self.assertFalse(pilot.LICENSE_DENIED.search("Checking out license Virtuoso_Multi_mode_Simulation"))


class DeployOrderTests(unittest.TestCase):
    def test_invalid_declaration_refused_before_any_upload(self):
        class Adapter:
            SPEC = dict(id="fixture", repository="fixture", design="d", top="t", cases=["a"],
                        checks=["c"], corners=["-40"], files=[])
        def no_transport(*args, **kwargs):
            raise AssertionError("deploy reached the transport")
        saved, pilot.Transport = pilot.Transport, no_transport
        try:
            with self.assertRaises(ValueError):
                pilot.deploy(Adapter, "/nonexistent-repo", "/nonexistent-package",
                             "/remote/snapshot", "h", "/remote/runs", "/nonexistent-output")
        finally:
            pilot.Transport = saved


class PackageTests(unittest.TestCase):
    def test_vendored_tests_are_not_packaged(self):
        class Adapter:
            SPEC = dict(files=["bench.sh"])
            @staticmethod
            def validate_upload(repo, paths):
                pass
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            jobs = repo / "deployment/bnl/jobs"
            (jobs / "bin").mkdir(parents=True)
            for name in ("bench.sh", "deployment/bnl/tracked_job.py", "deployment/bnl/jobs/pilot.py",
                         "deployment/bnl/jobs/test_pilot.py", "deployment/bnl/jobs/bin/runjob"):
                (repo / name).write_text("x\n")
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)
            pilot.package(Adapter, repo, Path(tmp) / "package")
            files = json.loads((Path(tmp) / "package/source.json").read_text())["files"]
            self.assertIn("deployment/bnl/jobs/pilot.py", files)
            self.assertIn("deployment/bnl/jobs/bin/runjob", files)
            self.assertNotIn("deployment/bnl/jobs/test_pilot.py", files)


@unittest.skipUnless(os.name == "posix", "POSIX lifecycle")
class PilotTests(unittest.TestCase):
    def test_timeout_reaps_tool_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile=Path(tmp)/'pid'
            command="sleep 30 & echo $! > " + str(pidfile) + "; wait"
            with self.assertRaises(subprocess.TimeoutExpired):
                pilot.foreground(['/bin/sh','-c',command],timeout=.3)
            pid=int(pidfile.read_text())
            stat=Path('/proc')/str(pid)/'stat'
            # A briefly unreaped zombie has stopped executing and cannot own a license.
            self.assertTrue(not stat.exists() or stat.read_text().split()[2]=='Z')

    def test_wrapper_probe_preserves_quoted_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper=Path(tmp)/'wrapper';wrapper.write_text('#!/bin/sh\nexec "$@"\n');wrapper.chmod(0o700)
            self.assertEqual(pilot.wrapper_probe(str(wrapper),"print('a \\\"quoted\\\" value')\n").strip(),'a "quoted" value')

    def fixture(self, root, adapter_path, code):
        """A package holding the vendored runtime and one adapter, and a store whose runner is a
        local /bin/sh standing in for the cluster host."""
        package = root / "package"; package.mkdir(); (root / "runs").mkdir()
        runtime = Path(__file__).parent
        files = {}
        for p in runtime.glob("*.py"):
            if p.name.startswith("test_"): continue
            dest = "deployment/bnl/jobs/" + p.name
            q = package / dest; q.parent.mkdir(parents=True, exist_ok=True); q.write_bytes(p.read_bytes()); files[dest] = pilot.sha(q)
        path = package / adapter_path; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code); files[adapter_path] = pilot.sha(path)
        sys.path.insert(0, str(runtime.parent))
        adapter = pilot.module(path)
        source = dict(root=str(root / "checkout"), head="a" * 40, patch_sha256="b" * 64, untracked_sha256="c" * 64)
        pilot.write(package / "source.json", dict(source=source, files=files, adapter_sha256=pilot.digest(adapter.SPEC)))
        # The remote runtime uses its installed helper bundle, not package/bin.
        env = {k: v for k, v in os.environ.items() if not k.startswith("ASICJOBS")}
        env['HOME'] = str(root)
        def runner(argv, data, timeout):
            p = subprocess.run(['/bin/sh', '-s'], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        return package, adapter, source, runner, TaskStore(str(root / 'state'))

    def collect(self, store, w, case, source, manifest):
        first = store.start(w, case, {'case': case}, source, pilot.digest(manifest))
        self.assertEqual(first['observation'], 'submitted')
        deadline = time.monotonic()+25
        while time.monotonic()<deadline:
            result = store.observe(w, case, collect=True)
            if result['observation'] in ('done','failed','killed'): break
            time.sleep(.1)
        return result

    def test_real_parent_and_adapter_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = '''import sys
from jobs import pilot
SPEC=dict(id='fixture',repository='fixture',design='fixture',top='fixture',cases=['pass','fail','missing','changed'],checks=['result'],corners=['test'],remote_inputs={})
def preflight(snapshot): return {}
def execute(work,case,manifest):
 if case=='missing': raise ValueError('missing report')
 pilot.write(work/'native.json',dict(case=case))
 return [dict(name='result',corner='test',status=case)]
if __name__=='__main__': sys.exit(pilot.main(sys.modules[__name__]))
'''
            package, adapter, source, runner, store = self.fixture(root, "deployment/bnl/tracked_job.py", code)
            for case in adapter.SPEC['cases']:
                snapshot = root / ('snapshot-' + case)
                manifest = pilot.stage(adapter, package, snapshot)
                if case == 'changed': (snapshot / 'deployment/bnl/tracked_job.py').write_text(code+'\n#changed\n')
                profile = pilot.profile(adapter, str(snapshot), 'synthetic.invalid', str(root/'runs'), 'ssh')
                w = Workflow(profile, lambda host, **kw: Transport(host=host, runner=runner, **kw))
                result = self.collect(store, w, case, source, manifest)
                self.assertEqual(result['engineering'],case if case in ('pass','fail') else 'unchecked')
                self.assertEqual(store.start(w,case,{'case':case},source,pilot.digest(manifest))['reference'],result['reference'])
            self.assertEqual(len(list((root/'.asicjobs').glob('*/meta.json'))),4)
            self.assertEqual(len((root/'.asicjobs/events.jsonl').read_text().splitlines()),4)
            self.assertFalse((root/'.asicjobs/bin').exists())

    def test_further_adapter_reports_per_case_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A further adapter's own directory is not deployment/bnl, so it puts the vendored
            # runtime on sys.path itself (the line every tracked_jobs/<id>.py carries).
            code = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs import pilot
SPEC=dict(id='sar8',adapter='deployment/bnl/tracked_jobs/sar8.py',repository='fixture',design='sar8',top='sar8_core',
          cases=['sch','pex','pexfail','pexwide'],checks=['frame','sndr'],corners=['tt','ss'],
          case_checks=dict(pex=dict(checks=['sndr'],corners=['tt']),pexfail=dict(checks=['sndr'],corners=['tt']),
                           pexwide=dict(checks=['sndr'],corners=['tt'])),remote_inputs={})
def preflight(snapshot): return {}
def execute(work,case,manifest):
 pilot.write(work/'native.json',dict(case=case))
 if case in ('sch','pexwide'):
  return [dict(name=n,corner=c,status='pass') for n in SPEC['checks'] for c in SPEC['corners']]
 return [dict(name='sndr',corner='tt',status='fail' if case=='pexfail' else 'pass')]
if __name__=='__main__': sys.exit(pilot.main(sys.modules[__name__]))
'''
            package, adapter, source, runner, store = self.fixture(root, "deployment/bnl/tracked_jobs/sar8.py", code)
            snapshot = root / 'snapshot-sar8'
            manifest = pilot.stage(adapter, package, snapshot)
            self.assertFalse((snapshot / 'deployment/bnl/tracked_job.py').exists())
            profile = pilot.profile(adapter, str(snapshot), 'synthetic.invalid', str(root/'runs'), 'ssh')
            w = Workflow(profile, lambda host, **kw: Transport(host=host, runner=runner, **kw))
            # pexwide reports the schematic set where its case declares fewer: refused, never scored.
            for case, engineering, passed, failed in (('sch', 'pass', 4, 0), ('pex', 'pass', 1, 0),
                                                      ('pexfail', 'fail', 0, 1), ('pexwide', 'unchecked', 0, 0)):
                with self.subTest(case=case):
                    result = self.collect(store, w, case, source, manifest)
                    self.assertEqual(engineering, result['engineering'])
                    self.assertEqual((passed, failed), (result.get('checks_passed', 0), result.get('checks_failed', 0)))
                    self.assertEqual([], result.get('missing_checks', []))
            self.assertIn("omitted declared checks", (result.get('refusal') or {}).get('reason', ''))

    def test_deploy_stages_each_adapter_on_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root / "repo"
            runtime = Path(__file__).parent
            for p in runtime.glob("*.py"):
                if not p.name.startswith("test_"):
                    q = repo / "deployment/bnl/jobs" / p.name; q.parent.mkdir(parents=True, exist_ok=True)
                    q.write_bytes(p.read_bytes())
            body = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[{up}]))
from jobs import pilot
SPEC=dict(id='{id}',{path}repository='fixture',design='d',top='t',cases=['a'],checks=['c'],corners=['tt'],files=['bench.sh'])
def validate_upload(repo, paths): pass
def preflight(snapshot): return {{}}
'''
            (repo / "bench.sh").write_text("x\n")
            (repo / "deployment/bnl/tracked_job.py").write_text(body.format(up=0, id="ota", path=""))
            (repo / "deployment/bnl/tracked_jobs").mkdir()
            (repo / "deployment/bnl/tracked_jobs/sar8.py").write_text(
                body.format(up=1, id="sar8", path="adapter='deployment/bnl/tracked_jobs/sar8.py',"))
            commit(repo)
            sys.path.insert(0, str(runtime.parent))
            ota = pilot.module(repo / "deployment/bnl/tracked_job.py")
            sar8 = pilot.module(repo / "deployment/bnl/tracked_jobs/sar8.py")
            env = {k: v for k, v in os.environ.items() if not k.startswith("ASICJOBS")}
            env['HOME'] = str(root)
            def runner(argv, data, timeout):
                p = subprocess.run(['/bin/sh', '-s'], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
                return p.returncode, p.stdout, p.stderr
            saved, pilot.Transport = pilot.Transport, lambda **kw: Transport(runner=runner, **kw)
            try:
                for a in (ota, sar8):
                    name = a.SPEC["id"]
                    pilot.package(a, repo, root / ("pkg-" + name))
                    pilot.deploy(a, repo, root / ("pkg-" + name), str(root / ("snap-" + name)), "h",
                                 str(root / "runs"), root / ("out-" + name))
                # Deploying the same package again finds its own snapshot and accepts it.
                pilot.deploy(ota, repo, root / "pkg-ota", str(root / "snap-ota"), "h", str(root / "runs"), root / "out-ota")
                # Another adapter's package cannot land on a staged snapshot.
                with self.assertRaises(RuntimeError):
                    pilot.deploy(sar8, repo, root / "pkg-sar8", str(root / "snap-ota"), "h", str(root / "runs"), root / "out-x")
            finally:
                pilot.Transport = saved
            manifests = {n: pilot.load(root / ("snap-" + n) / "manifest.json") for n in ("ota", "sar8")}
            self.assertIn("deployment/bnl/tracked_job.py", manifests["ota"]["files"])
            self.assertNotIn("deployment/bnl/tracked_jobs/sar8.py", manifests["ota"]["files"])
            self.assertIn("deployment/bnl/tracked_jobs/sar8.py", manifests["sar8"]["files"])
            self.assertNotIn("deployment/bnl/tracked_job.py", manifests["sar8"]["files"])
            self.assertEqual(pilot.digest(sar8.SPEC), manifests["sar8"]["adapter_sha256"])
            self.assertEqual(str(root / "snap-sar8/deployment/bnl/tracked_jobs/sar8.py"),
                             pilot.load(root / "out-sar8/profile.json")["argv"][2])
            self.assertEqual(str(root / "snap-ota/deployment/bnl/tracked_job.py"),
                             pilot.load(root / "out-ota/profile.json")["argv"][2])
            self.assertFalse((root / "out-x").exists())

    def segment(self, root, name, body):
        script = root / (name + ".sh"); script.write_text(body)
        return dict(argv=["/bin/sh", str(script)], out=str(root / (name + ".out")))

    def test_segments_run_in_slots_and_report_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Each segment records when it ran; at no instant may more than two overlap.
            body = 'date +%s.%N > {r}/{i}.span; sleep .3; date +%s.%N >> {r}/{i}.span; exit {rc}\n'
            work = [self.segment(root, "s%d" % i, body.format(r=root, i=i, rc=1 if i == 2 else 0)) for i in range(4)]
            results = pilot.segments(work, 2, poll=.05)
            self.assertEqual([("done", 1), ("done", 1), ("failed", 1), ("done", 1)],
                             [(r["outcome"], r["attempts"]) for r in results])
            self.assertEqual([0, 1, 2, 3], [r["index"] for r in results])
            spans = [[float(t) for t in (root / ("%d.span" % i)).read_text().split()] for i in range(4)]
            self.assertLessEqual(max(sum(a <= start < b for a, b in spans) for start, _ in spans), 2)

    def test_license_wait_is_requeued_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # First attempt: the queue wait runs out (+lqtimeout); the second gets a seat.
            body = ('if [ -f {r}/seat ]; then exit 0; fi; touch {r}/seat; '
                    'echo "ERROR (SPECTRE-209): license queue timed out" >&2; exit 1\n').format(r=root)
            results = pilot.segments([self.segment(root, "s0", body)], 1, poll=.05)
            self.assertEqual(("done", 2), (results[0]["outcome"], results[0]["attempts"]))

    def test_license_wait_that_never_ends_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "spectre.log"
            seg = self.segment(root, "s0", 'echo "Licensed number of users already reached" > %s; exit 1\n' % log)
            seg["out"] = str(root / "s0.out"); seg["logs"] = [str(log)]
            ok = self.segment(root, "s1", "exit 0\n")
            with self.assertRaisesRegex(pilot.LicenseWait, r"1 of 2 segments .* 3 attempts \(segments 0\)"):
                pilot.segments([seg, ok], 2, license_retries=2, poll=.05)

    def test_stale_denial_does_not_mask_a_real_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "spectre.log"
            # Attempt 1 is denied (in its log); attempt 2 fails differently and never touches the log.
            body = ('if [ -f {r}/again ]; then echo "netlist error" >&2; exit 2; fi; touch {r}/again; '
                    'echo "SPECTRE-209" > {log}; exit 1\n').format(r=root, log=log)
            seg = self.segment(root, "s0", body); seg["logs"] = [str(log)]
            results = pilot.segments([seg], 1, license_retries=3, poll=.05)
            self.assertEqual(("failed", 2, 2), (results[0]["outcome"], results[0]["attempts"], results[0]["rc"]))

    def test_segment_timeout_reaps_every_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = [self.segment(root, "s%d" % i, "sleep 30 & echo $! > %s/pid%d; wait\n" % (root, i)) for i in range(2)]
            with self.assertRaises(subprocess.TimeoutExpired):
                pilot.segments(work, 2, timeout=.5, poll=.05)
            for i in range(2):
                stat = Path('/proc') / (root / ("pid%d" % i)).read_text().strip() / 'stat'
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z')

    def test_copy_rejects_changed_bytes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input';p.write_text('old');expected=pilot.sha(p);p.write_text('new')
            with self.assertRaises(ValueError):pilot.copy_checked(p,Path(tmp)/'out',expected)
            q=Path(tmp)/'link';q.symlink_to(p)
            with self.assertRaises(ValueError):pilot.copy_checked(q,Path(tmp)/'out',pilot.sha(q))


if __name__ == '__main__': unittest.main()
