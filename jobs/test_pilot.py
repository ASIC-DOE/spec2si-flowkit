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
from .workflow import Workflow


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

    def test_real_parent_and_adapter_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package = root / "package"; package.mkdir(); (root / "runs").mkdir()
            runtime = Path(__file__).parent
            files = {}
            for p in runtime.glob("*.py"):
                if p.name.startswith("test_"): continue
                dest = "deployment/bnl/jobs/" + p.name
                q = package / dest; q.parent.mkdir(parents=True, exist_ok=True); q.write_bytes(p.read_bytes()); files[dest] = pilot.sha(q)
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
            path = package / "deployment/bnl/tracked_job.py"; path.write_text(code); files["deployment/bnl/tracked_job.py"] = pilot.sha(path)
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
            store = TaskStore(str(root / 'state'))
            for case in adapter.SPEC['cases']:
                snapshot = root / ('snapshot-' + case)
                manifest = pilot.stage(adapter, package, snapshot)
                if case == 'changed': (snapshot / 'deployment/bnl/tracked_job.py').write_text(code+'\n#changed\n')
                profile = pilot.profile(adapter, str(snapshot), 'synthetic.invalid', str(root/'runs'), 'ssh')
                w = Workflow(profile, lambda host, **kw: Transport(host=host, runner=runner, **kw))
                first = store.start(w, case, {'case':case}, source, pilot.digest(manifest))
                self.assertEqual(first['observation'], 'submitted')
                deadline = time.monotonic()+25
                while time.monotonic()<deadline:
                    result = store.observe(w, case, collect=True)
                    if result['observation'] in ('done','failed','killed'): break
                    time.sleep(.1)
                self.assertEqual(result['engineering'],case if case in ('pass','fail') else 'unchecked')
                self.assertEqual(store.start(w,case,{'case':case},source,pilot.digest(manifest))['reference'],result['reference'])
            self.assertEqual(len(list((root/'.asicjobs').glob('*/meta.json'))),4)
            self.assertEqual(len((root/'.asicjobs/events.jsonl').read_text().splitlines()),4)
            self.assertFalse((root/'.asicjobs/bin').exists())

    def test_copy_rejects_changed_bytes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input';p.write_text('old');expected=pilot.sha(p);p.write_text('new')
            with self.assertRaises(ValueError):pilot.copy_checked(p,Path(tmp)/'out',expected)
            q=Path(tmp)/'link';q.symlink_to(p)
            with self.assertRaises(ValueError):pilot.copy_checked(q,Path(tmp)/'out',pilot.sha(q))


if __name__ == '__main__': unittest.main()
