"""Duplicate-safe submission (agentic workflow report §6.3).

The task id is the tracker's request key; runjob claims it atomically before
launching. These tests break the start at both sides of dispatch, race two
repeated starts, and check that exactly one job ever runs. The POSIX cases run
the real runjob/report.sh on a temporary $HOME; the rest use a mock transport.

Run: python3 -m unittest jobs.test_request_key -v
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from .remote import KNOWN, UNKNOWN, Result, Transport
from .state import TaskStore
from .test_workflow import profile
from .workflow import Workflow

PARAMETERS = {"message": "one job only", "exit_code": 0}


def source(root):
    return dict(root=str(root), head="a" * 40, patch_sha256="b" * 64, untracked_sha256="c" * 64)


@unittest.skipUnless(os.name == "posix" and os.path.isfile("/bin/sh"), "requires POSIX shell")
class RealTracker(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="request-key-")
        self.addCleanup(temp.cleanup)
        self.home = temp.name
        self.p = profile()
        self.p["work_root"] = os.path.join(self.home, "work")
        os.mkdir(self.p["work_root"])
        self.env = dict(os.environ, HOME=self.home)
        for key in ("ASICJOBS_DIR", "ASICJOBS_ID", "ASICJOBS_JOBDIR"):
            self.env.pop(key, None)
        self.store = TaskStore(os.path.join(self.home, "state"))
        self.source = source(os.path.join(self.home, "repo"))
        self.dispatches = 0      # launch scripts sent (attempts, not jobs)
        self.lose_replies = 0    # launch replies to drop after the launch ran
        self.barrier = None      # make concurrent launches overlap
        self.lock = threading.Lock()

    def runner(self, argv, input_bytes, timeout):
        # The launcher line, not the bundle installer (which carries runjob's text).
        launch = input_bytes.startswith(b'exec /bin/sh "$HOME/') and b'/runjob" ' in input_bytes
        if launch:
            with self.lock:
                self.dispatches += 1
            if self.barrier is not None:
                try:
                    self.barrier.wait(5)
                except threading.BrokenBarrierError:
                    pass
        proc = subprocess.Popen(["/bin/sh", "-s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env, cwd=self.home)
        out, err = proc.communicate(input_bytes, timeout=timeout)
        if launch:
            with self.lock:
                lose, self.lose_replies = self.lose_replies > 0, max(0, self.lose_replies - 1)
            if lose:
                # The job was launched; only the acknowledgement is lost.
                raise TimeoutError("reply lost")
        return proc.returncode, out, err

    def workflow(self):
        return Workflow(self.p, lambda host, **kw: Transport(host=host, runner=self.runner))

    @property
    def jobs(self):
        root = os.path.join(self.home, ".asicjobs")
        return sorted(d for d in (os.listdir(root) if os.path.isdir(root) else ())
                      if os.path.isfile(os.path.join(root, d, "meta.json")))

    def start(self, workflow=None):
        return self.store.start(workflow or self.workflow(), "one-request", dict(PARAMETERS),
                                self.source, "d" * 64)

    def collect(self, workflow=None):
        workflow = workflow or self.workflow()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.store.observe(workflow, "one-request", collect=True)
            if result["observation"] in ("done", "failed", "killed"):
                return result
            time.sleep(0.15)
        self.fail("job did not finish: %s" % result)

    def test_lost_ack_is_attached_by_resume_without_dispatch(self):
        self.lose_replies = 1
        first = self.start()
        self.assertEqual("submission-unknown", first["observation"])
        self.assertIsNone(self.store.read("one-request")["reference"]["job_id"])
        self.assertEqual(1, len(self.jobs))
        resumed = self.store.observe(self.workflow(), "one-request")
        self.assertIn(resumed["observation"], ("running", "done"))
        self.assertEqual(self.jobs[0], self.store.read("one-request")["reference"]["job_id"])
        self.assertEqual(1, self.dispatches)
        result = self.collect()
        self.assertEqual("done", result["observation"])
        self.assertEqual("tracker-verified", result["evidence"])
        self.assertEqual(1, len(self.jobs))

    def test_lost_ack_is_attached_by_a_repeated_start(self):
        self.lose_replies = 1
        self.assertEqual("submission-unknown", self.start()["observation"])
        again = self.start()
        self.assertIn(again["observation"], ("running", "done"))
        self.assertEqual(1, self.dispatches)
        self.assertEqual([again["job_id"]], self.jobs)
        self.assertEqual("done", self.collect()["observation"])

    def test_crash_before_dispatch_is_dispatched_once_by_a_repeated_start(self):
        workflow = self.workflow()
        with patch.object(workflow, "dispatch", side_effect=RuntimeError("crash before network")):
            with self.assertRaises(RuntimeError):
                self.start(workflow)
        self.assertEqual([], self.jobs)
        # A read never dispatches: it says the tracker has nothing, and how to proceed.
        looked = self.store.observe(self.workflow(), "one-request")
        self.assertEqual("submission-unknown", looked["observation"])
        self.assertIn("same task-key", looked["reason"])
        self.assertEqual(0, self.dispatches)
        again = self.start()
        self.assertIn(again["observation"], ("submitted", "running", "done"))
        self.assertIsNotNone(again["job_id"])
        self.assertEqual(1, self.dispatches)
        self.assertEqual("done", self.collect()["observation"])
        self.assertEqual(1, len(self.jobs))

    def test_two_concurrent_repeated_starts_launch_one_job(self):
        workflow = self.workflow()
        with patch.object(workflow, "dispatch", side_effect=RuntimeError("crash before network")):
            with self.assertRaises(RuntimeError):
                self.start(workflow)
        self.barrier = threading.Barrier(2)
        results, errors = [], []
        def worker():
            try:
                results.append(self.start())
            except Exception as exc:  # reported below
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual([], errors)
        self.assertEqual(1, len(self.jobs))
        self.assertEqual({self.jobs[0]}, {r["job_id"] for r in results})
        self.assertEqual(self.jobs[0], self.store.read("one-request")["reference"]["job_id"])
        self.assertEqual("done", self.collect()["observation"])

    def test_delayed_duplicate_dispatch_attaches(self):
        transport = Transport(host="example.invalid", runner=self.runner)
        workspace = os.path.join(self.p["work_root"], "task-dup")
        launch = dict(flow="dup", target="run", expect=["result.txt"], workspace=workspace, request="task-dup")
        first = transport.run(["/bin/sh", "-c", "echo x > result.txt"], **launch)
        second = transport.run(["/bin/sh", "-c", "echo x > result.txt"], **launch)
        self.assertEqual(KNOWN, first.status)
        self.assertEqual(KNOWN, second.status)
        self.assertEqual(first.data["jobid"], second.data["jobid"])
        self.assertIs(True, second.data.get("attached"))
        self.assertEqual([first.data["jobid"]], self.jobs)
        found = transport.request("task-dup")
        self.assertEqual(("claimed", first.data["jobid"], True),
                         (found.data["state"], found.data["jobid"], found.data["started"]))
        self.assertEqual("absent", transport.request("task-other").data["state"])

    def test_workspace_collision_is_recorded_as_a_failed_job(self):
        transport = Transport(host="example.invalid", runner=self.runner)
        workspace = os.path.join(self.p["work_root"], "task-collide")
        os.mkdir(workspace)
        res = transport.run(["/bin/sh", "-c", "echo x > result.txt"], flow="collide", target="run",
                            expect=["result.txt"], workspace=workspace, request="task-collide")
        self.assertIs(True, res.data.get("collision"))
        result = transport.why(res.data["jobid"]).data["result"]
        self.assertEqual(("failed", 73), (result["state"], result["rc"]))
        self.assertEqual(os.listdir(workspace), [])


class MockTracker(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="request-key-mock-")
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(os.path.join(temp.name, "state"))
        self.source = source(os.path.join(temp.name, "repo"))
        self.transport = Mock()
        self.transport.run.return_value = Result(UNKNOWN, "example.invalid")
        self.workflow = Workflow(profile(), lambda **kw: self.transport)

    def start(self):
        return self.store.start(self.workflow, "one-request", dict(PARAMETERS), self.source, "d" * 64)

    def test_dispatch_sends_the_task_id_as_request_key(self):
        self.start()
        ref = self.store.read("one-request")["reference"]
        self.assertEqual(ref["task_id"], self.transport.run.call_args[1]["request"])

    def test_unreachable_lookup_never_dispatches(self):
        self.start()
        self.transport.request.return_value = Result(UNKNOWN, "example.invalid")
        again = self.start()
        self.assertEqual(("submission-unknown", "reconcile"), (again["observation"], again["next_action"]))
        self.assertIn("not proof", again["reason"])
        self.assertEqual(1, self.transport.run.call_count)

    def test_claimed_but_not_started_is_not_attached(self):
        self.start()
        key = self.store.read("one-request")["reference"]["task_id"]
        self.transport.request.return_value = Result(KNOWN, "example.invalid", rc=0, data=dict(
            kind="request", key=key, state="claimed", jobid="job-9", started=False))
        again = self.start()
        self.assertEqual("submission-unknown", again["observation"])
        self.assertIn("job-9", again["reason"])
        self.assertIsNone(self.store.read("one-request")["reference"]["job_id"])
        self.assertEqual(1, self.transport.run.call_count)

    def test_record_without_request_keys_is_never_dispatched_again(self):
        self.start()
        path = os.path.join(self.store.path("one-request"), "task.json")
        record = json.loads(Path(path).read_text())
        del record["request_protocol"]
        Path(path).write_text(json.dumps(record))
        self.transport.request.return_value = Result(KNOWN, "example.invalid", rc=0, data=dict(
            kind="request", key=record["reference"]["task_id"], state="absent"))
        self.assertEqual("submission-unknown", self.start()["observation"])
        self.transport.request.assert_not_called()
        self.assertEqual(1, self.transport.run.call_count)


if __name__ == "__main__":
    unittest.main()
