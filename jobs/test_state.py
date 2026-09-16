"""Crash, concurrency and recovery tests for local durable task pointers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from .remote import KNOWN, UNKNOWN, Result
from .state import TaskStore, atomic_json, source_identity
from .workflow import Workflow, ContractError
from .test_workflow import profile


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="flowkit-state-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = TaskStore(str(self.root / "state"))
        self.source = dict(root=str(self.root / "repo"), head="a" * 40,
                           patch_sha256="b" * 64, untracked_sha256="c" * 64)
        self.transport = Mock()
        self.transport.run.return_value = Result(KNOWN, "example.invalid", rc=0,
                                                 data={"kind": "launched", "jobid": "job-1"})
        self.transport.status.return_value = Result(KNOWN, "example.invalid", rc=0,
                                                    data={"kind": "status", "jobid": "job-1", "state": "done"})
        self.workflow = Workflow(profile(), lambda **kw: self.transport)
        self.parameters = {"message": "private parameter never stored", "exit_code": 0}

    def start(self, **kw):
        return self.store.start(self.workflow, "request-one", self.parameters,
                                self.source, "d" * 64, **kw)

    def test_intent_precedes_dispatch_and_resume_is_live(self):
        def launch(*args, **kw):
            record = self.store.read("request-one")
            self.assertEqual("submission-unknown", record["submission"])
            self.assertIsNone(record["reference"]["job_id"])
            self.assertEqual("d" * 64, record["manifest_sha256"])
            return Result(KNOWN, "example.invalid", rc=0, data={"kind": "launched", "jobid": "job-1"})
        self.transport.run.side_effect = launch
        first = self.start()
        self.assertEqual("submitted", first["observation"])
        result = self.start()
        self.assertEqual("done", result["observation"])
        self.assertEqual(1, self.transport.run.call_count)
        self.assertEqual(1, self.transport.status.call_count)
        record = self.store.read("request-one")
        self.assertEqual(self.source, record["source"])
        self.assertEqual(["result.txt"], record["required_artifacts"])
        content = "".join(p.read_text() for p in (self.root / "state").rglob("*.json"))
        self.assertNotIn(self.parameters["message"], content)
        self.assertIn("observed_at", content)
        self.assertEqual("job-1", TaskStore(str(self.root / "state")).listing(self.workflow)["tasks"][0]["reference"]["job_id"])

    def test_lost_ack_does_not_retry(self):
        self.transport.run.return_value = Result(UNKNOWN, "example.invalid")
        self.assertEqual("submission-unknown", self.start()["observation"])
        self.assertEqual("reconcile", self.start()["next_action"])
        self.assertEqual(1, self.transport.run.call_count)
        self.transport.status.assert_not_called()

    def test_crash_after_reservation_before_intent(self):
        with patch("jobs.state.atomic_json", side_effect=OSError("disk failed")):
            with self.assertRaises(OSError):
                self.start()
        self.transport.run.assert_not_called()
        self.assertEqual("reconcile", self.start()["next_action"])
        self.assertEqual("unreadable-reservation", self.store.listing(self.workflow)["tasks"][0]["observation"])

    def test_crash_before_dispatch_and_after_ack(self):
        for after in (False, True):
            with self.subTest(after=after):
                self.store = TaskStore(str(self.root / ("after" if after else "before")))
                if after:
                    original = atomic_json
                    def writer(path, value):
                        if value.get("submission") == "submitted":
                            raise OSError("crash saving acknowledgement")
                        original(path, value)
                    with patch("jobs.state.atomic_json", side_effect=writer):
                        with self.assertRaises(OSError):
                            self.start()
                else:
                    with patch.object(self.workflow, "dispatch", side_effect=RuntimeError("crash before network")):
                        with self.assertRaises(RuntimeError):
                            self.start()
                before = self.transport.run.call_count
                self.assertEqual("submission-unknown", self.start()["observation"])
                self.assertEqual(before, self.transport.run.call_count)

    def test_changed_intent_refused_and_unknown_read_is_not_terminal(self):
        self.start()
        self.parameters["message"] = "different"
        with self.assertRaisesRegex(ContractError, "different request"):
            self.start()
        self.transport.status.return_value = Result(UNKNOWN, "example.invalid")
        self.assertEqual("unknown", self.store.observe(self.workflow, "request-one")["observation"])
        self.assertEqual("job-1", self.store.read("request-one")["reference"]["job_id"])
        changed = profile()
        changed["version"] = "2"
        with self.assertRaisesRegex(ContractError, "profile changed"):
            self.store.observe(Workflow(changed), "request-one")
        self.assertEqual(1, self.transport.run.call_count)

    def test_concurrent_call_has_no_submission_rights(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def run(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timed out")
            return Result(KNOWN, "example.invalid", rc=0, data={"kind": "launched", "jobid": "job-1"})
        self.transport.run.side_effect = run
        def worker():
            try:
                self.start()
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual("submission-unknown", self.start()["observation"])
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(1, self.transport.run.call_count)
        self.assertEqual("job-1", self.store.read("request-one")["reference"]["job_id"])

    def test_key_paths_and_external_store(self):
        for key in ("../escape", "", "a/b", "a\\b"):
            with self.assertRaises(ContractError):
                self.store.path(key)
        self.assertNotEqual(self.store.path("KEY"), self.store.path("key"))
        with self.assertRaises(ContractError):
            self.store.require_external(str(self.root))

    def test_two_processes_share_one_reservation(self):
        worker = '''
import json, sys, time
from jobs.state import TaskStore
from jobs.workflow import Workflow
from jobs.test_workflow import profile
from jobs.remote import Result, KNOWN
class Transport:
    def run(self, *args, **kwargs):
        with open(sys.argv[2], "a") as fh:
            fh.write("launch\\n")
        time.sleep(0.2)
        return Result(KNOWN, "example.invalid", rc=0, data={"kind":"launched", "jobid":"job-1"})
    def status(self, job):
        return Result(KNOWN, "example.invalid", rc=0, data={"kind":"status", "jobid":job, "state":"done"})
w = Workflow(profile(), lambda **kwargs: Transport())
print(json.dumps(TaskStore(sys.argv[1]).start(w, "request-one", json.loads(sys.argv[3]), json.loads(sys.argv[4]), "d"*64)))
'''
        marker = self.root / "launches"
        argv = [sys.executable, "-B", "-c", worker, self.store.directory, str(marker),
                json.dumps(self.parameters), json.dumps(self.source)]
        processes = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      universal_newlines=True) for _ in range(2)]
        for proc in processes:
            out, err = proc.communicate(timeout=10)
            self.assertEqual(0, proc.returncode, err)
            self.assertIn(json.loads(out)["observation"], ("submitted", "done", "submission-unknown"))
        self.assertEqual(["launch"], marker.read_text().splitlines())

    def test_atomic_failure_preserves_previous_record(self):
        target = str(self.root / "record.json")
        atomic_json(target, {"value": 1})
        with patch("jobs.state.os.replace", side_effect=OSError("failed replacement")):
            with self.assertRaises(OSError):
                atomic_json(target, {"value": 2})
        self.assertEqual({"value": 1}, json.loads(Path(target).read_text()))
        self.assertEqual([], list(self.root.glob(".pending-*")))

    def test_git_provenance_tracks_content_without_storing_it(self):
        repo = self.root / "git-repo"
        repo.mkdir()
        def git(*args):
            subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git("init")
        (repo / "tracked").write_text("original")
        git("add", "tracked")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
        a = source_identity(str(repo))
        (repo / "tracked").write_text("private change")
        b = source_identity(str(repo))
        self.assertNotEqual(a["patch_sha256"], b["patch_sha256"])
        (repo / "untracked").write_text("private input")
        c = source_identity(str(repo))
        self.assertNotEqual(b["untracked_sha256"], c["untracked_sha256"])
        self.assertNotIn("private", json.dumps(c))

    def test_cli_requires_durability_and_can_list_across_processes(self):
        p = self.root / "profile.json"
        p.write_text(json.dumps(profile()))
        result = subprocess.run([sys.executable, "-B", "-m", "jobs.workflow", "start", "--profile", str(p)],
                                stdout=subprocess.PIPE, universal_newlines=True, timeout=10)
        self.assertEqual(2, result.returncode)
        self.assertIn("durable start requires", result.stdout)
        self.start()
        result = subprocess.run([sys.executable, "-B", "-m", "jobs.workflow", "tasks", "--profile", str(p),
                                 "--state-dir", self.store.directory], stdout=subprocess.PIPE,
                                universal_newlines=True, timeout=10)
        self.assertEqual(0, result.returncode)
        self.assertEqual("request-one", json.loads(result.stdout)["tasks"][0]["task_key"])

    @unittest.skipUnless(os.name == "posix", "requires actual local POSIX tracker")
    def test_real_job_survives_lost_stdout(self):
        from .test_workflow import ExampleJobs
        fixture = ExampleJobs()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        workflow = Workflow(fixture.p, fixture.factory)
        # Discard the first return value, just as if the chat lost its output.
        self.store.start(workflow, "real-job", {"message": "hello", "exit_code": 0}, self.source, "d" * 64)
        fresh = TaskStore(self.store.directory)
        ref = fresh.listing(workflow)["tasks"][0]["reference"]
        self.assertIsNotNone(ref["job_id"])
        import time
        for _ in range(100):
            result = fresh.observe(workflow, "real-job", collect=True)
            if result["observation"] == "done":
                break
            time.sleep(0.1)
        self.assertEqual("tracker-verified", result["evidence"])
        self.assertEqual(1, fixture.launches)


if __name__ == "__main__":
    unittest.main()
