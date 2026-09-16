"""Contract tests and actual harmless POSIX jobs; no SSH or EDA tools used.

Run: python3 -m unittest jobs.test_workflow -v
"""
import copy
import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock

from .remote import Transport, Result, UNKNOWN, KNOWN
from .workflow import Workflow, ContractError


def profile():
    with open(os.path.join(os.path.dirname(__file__), "example_profile.json")) as fh:
        return json.load(fh)


class ContractTests(unittest.TestCase):
    def test_invalid_profiles(self):
        cases = [("unknown_field", True), ("schema", 2), ("workspace", "shared"), ("lifecycle", "detached"),
                 ("work_root", "relative"), ("expected_artifacts", ["../escape"]),
                 ("expected_artifacts", ["a", "a"]), ("argv", ["python3"]),
                 ("progress", {"tool": "unknown", "log": "log"})]
        for key, value in cases:
            p = profile()
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                Workflow(p)

    def test_refuses_before_transport(self):
        factory = Mock()
        w = Workflow(profile(), factory)
        for params, host in [({}, None), ({"message": "ok", "exit_code": True}, None),
                             ({"message": "ok", "exit_code": 3}, None),
                             ({"message": "ok", "exit_code": 0}, "auto"),
                             ({"message": "ok", "exit_code": 0}, "other")]:
            with self.assertRaises(ContractError):
                w.start(params, host)
        factory.assert_not_called()

    def test_profile_transport_selection(self):
        p = profile()
        p.update(isolated_bundle=True, transport_mode="winssh")
        factory = Mock()
        Workflow(p, factory).transport("example.invalid")
        factory.assert_called_once_with(host="example.invalid", isolated_bundle=True, mode="winssh")
        p["transport_mode"] = "arbitrary-command"
        with self.assertRaises(ContractError):
            Workflow(p)

    def test_uncertain_submission_never_retries(self):
        transport = Mock()
        transport.run.return_value = Result(UNKNOWN, "example.invalid")
        w = Workflow(profile(), lambda **kw: transport)
        start = w.start({"message": "ok", "exit_code": 0})
        self.assertEqual("submission-unknown", start["observation"])
        self.assertEqual("reconcile", w.observe(start["reference"])["next_action"])
        self.assertEqual(1, transport.run.call_count)
        transport.status.assert_not_called()

    def test_explicit_host_does_not_invoke_picker(self):
        p = profile()
        p["host_policy"]["allow_auto"] = True
        p["host_policy"]["default"] = "auto"
        transport = Mock()
        transport.run.return_value = Result(UNKNOWN, "example.invalid")
        picker = Mock(return_value=("example.invalid", []))
        w = Workflow(p, lambda **kw: transport, picker)
        w.start({"message": "ok", "exit_code": 0}, "example.invalid")
        picker.assert_not_called()
        w.start({"message": "ok", "exit_code": 0})
        picker.assert_called_once_with(hosts=["example.invalid"])

    def test_unknown_read_does_not_resubmit(self):
        transport = Mock()
        transport.run.return_value = Result(KNOWN, "example.invalid", rc=0,
                                            data={"kind": "launched", "jobid": "job-1"})
        transport.status.return_value = Result(UNKNOWN, "example.invalid")
        w = Workflow(profile(), lambda **kw: transport)
        ref = w.start({"message": "ok", "exit_code": 0})["reference"]
        result = w.observe(ref, collect=True)
        self.assertEqual("unknown", result["observation"])
        self.assertEqual("unchecked", result["evidence"])
        self.assertEqual(1, transport.run.call_count)
        transport.verify.assert_not_called()

    def test_nonzero_launch_envelope_is_not_acknowledgement(self):
        transport = Mock()
        transport.run.return_value = Result(KNOWN, "example.invalid", rc=1,
                                            data={"kind": "launched", "jobid": "job-1"})
        result = Workflow(profile(), lambda **kw: transport).start(
            {"message": "ok", "exit_code": 0})
        self.assertEqual("submission-unknown", result["observation"])


@unittest.skipUnless(os.name == "posix" and os.path.isfile("/bin/sh"), "requires POSIX shell")
class ExampleJobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-example-")
        self.addCleanup(self.temp.cleanup)
        self.home = self.temp.name
        self.p = profile()
        # Exercise safe shell quoting of the work root as well as argv.
        self.p["work_root"] = os.path.join(self.home, "work ' $ root")
        os.mkdir(self.p["work_root"])
        self.env = dict(os.environ, HOME=self.home)
        for key in ("ASICJOBS_DIR", "ASICJOBS_ID", "ASICJOBS_JOBDIR"):
            self.env.pop(key, None)
        self.launches = 0

    def factory(self, host):
        def runner(argv, input_bytes, timeout):
            if input_bytes.startswith(b"mkdir -- ") and b"--cmd64" in input_bytes:
                self.launches += 1
            proc = subprocess.Popen(["/bin/sh", "-s"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=self.env, cwd=self.home)
            try:
                out, err = proc.communicate(input_bytes, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise TimeoutError("local runner timeout")
            return proc.returncode, out, err
        return Transport(host=host, runner=runner)

    def finish(self, p=None, code=0):
        w = Workflow(p or self.p, self.factory)
        message = "literal ' $(touch SHOULD_NOT_EXIST) ; & $HOME\nsecond line"
        start = w.start({"message": message, "exit_code": code})
        self.assertEqual("submitted", start["observation"], start)
        # Serialize/reload exactly what another chat process would receive.
        ref = json.loads(json.dumps(start))["reference"]
        resumed = Workflow(p or self.p, self.factory)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = resumed.observe(ref)
            if status["observation"] in ("done", "failed", "killed"):
                break
            time.sleep(0.15)
        else:
            self.fail("example job did not finish")
        collected = resumed.observe(ref, collect=True)
        print("EXAMPLE", json.dumps({k: collected[k] for k in
                                    ("observation", "evidence", "engineering", "next_action")}))
        return resumed, ref, collected, message

    def test_success_resume_and_literal_parameters(self):
        w, ref, result, message = self.finish()
        self.assertEqual("done", result["observation"])
        self.assertEqual("tracker-verified", result["evidence"])
        self.assertEqual("unchecked", result["engineering"])
        with open(os.path.join(ref["workspace"], "result.txt")) as fh:
            self.assertEqual(message, fh.read())
        self.assertFalse(os.path.exists(os.path.join(ref["workspace"], "SHOULD_NOT_EXIST")))
        self.assertEqual(1, self.launches)
        changed = copy.deepcopy(self.p)
        changed["version"] = "2"
        with self.assertRaises(ContractError):
            Workflow(changed, self.factory).observe(ref)

    def test_failed_job_is_not_engineering_pass(self):
        _, _, result, _ = self.finish(code=7)
        self.assertEqual("failed", result["observation"])
        self.assertEqual(7, result["execution_rc"])
        self.assertEqual("tracker-verified", result["evidence"])
        self.assertEqual("unchecked", result["engineering"])

    def test_missing_artifact_subset_refused(self):
        self.p["expected_artifacts"].append("missing.txt")
        _, _, result, _ = self.finish()
        self.assertEqual("incomplete", result["evidence"])

    def test_changed_artifact_refused(self):
        w, ref, _, _ = self.finish()
        with open(os.path.join(ref["workspace"], "result.txt"), "a") as fh:
            fh.write("changed")
        self.assertEqual("unverified", w.observe(ref, collect=True)["evidence"])

    def test_no_expected_artifacts_not_verified(self):
        self.p["expected_artifacts"] = []
        _, _, result, _ = self.finish()
        self.assertEqual("incomplete", result["evidence"])

    def test_workspace_collision_does_not_launch(self):
        transport = self.factory("example.invalid")
        result = transport.run(["/bin/true"], workspace=self.p["work_root"])
        self.assertEqual(UNKNOWN, result.status)
        self.assertEqual(73, result.rc)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".asicjobs", "events.jsonl")))


if __name__ == "__main__":
    unittest.main()
