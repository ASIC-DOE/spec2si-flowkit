"""Structured failure reports (study §4.11): collect writes one for every finished
job that is not a verified pass; judgement is declared, with the agent limited
to mechanical causes. The POSIX case runs the real runjob/report.sh.

Run: python3 -m unittest jobs.test_failure -v
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from . import failure
from .remote import KNOWN, Result, Transport
from .state import TaskStore
from .test_workflow import profile
from .workflow import ContractError, Workflow

PARAMETERS = {"message": "a private parameter value", "exit_code": 0}


def source(root):
    return dict(root=str(root), head="a" * 40, patch_sha256="b" * 64, untracked_sha256="c" * 64)


class Classification(unittest.TestCase):
    def test_which_results_need_a_report(self):
        self.assertFalse(failure.needs_report(dict(observation="done", evidence="tracker-verified", engineering="pass")))
        self.assertFalse(failure.needs_report(dict(observation="running", evidence="unchecked", engineering="unchecked")))
        self.assertFalse(failure.needs_report(dict(observation="unknown", evidence="unchecked", engineering="unchecked")))
        for r in (dict(observation="done", evidence="tracker-verified", engineering="fail"),
                  dict(observation="failed", evidence="incomplete", engineering="unchecked"),
                  dict(observation="done", evidence="unverified", engineering="pass"),
                  dict(observation="killed", evidence="unchecked", engineering="unchecked")):
            self.assertTrue(failure.needs_report(r), r)

    def test_derived_causes_are_mechanical_only(self):
        verified_fail = dict(engineering="fail", evidence="tracker-verified", failed_checks=["gain/tt"])
        self.assertEqual("gate-fail", failure.derive_cause(verified_fail, None)[0])
        crashed = dict(engineering="unchecked", evidence="incomplete")
        self.assertEqual("tool-error", failure.derive_cause(crashed, dict(license=2, traceback=2))[0])
        self.assertEqual("tool-error", failure.derive_cause(crashed, dict(crash=1))[0])
        cause, basis = failure.derive_cause(crashed, dict(traceback=3))
        self.assertEqual("unclassified", cause)   # our code or theirs: a judgement
        self.assertIn("traceback 3", basis)
        self.assertEqual("unclassified", failure.derive_cause(crashed, None)[0])


class Reports(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="failure-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = TaskStore(str(self.root / "state"))
        self.transport = Mock()
        self.transport.run.return_value = Result(KNOWN, "example.invalid", rc=0, data={"kind": "launched", "jobid": "job-1"})
        self.transport.status.return_value = Result(KNOWN, "example.invalid", rc=0,
                                                    data={"kind": "status", "jobid": "job-1", "state": "failed"})
        self.transport.why.return_value = Result(KNOWN, "example.invalid", rc=0, data={
            "jobid": "job-1", "result": {"jobid": "job-1", "state": "failed", "rc": 7, "artifacts": []}})
        self.transport.signatures.return_value = Result(KNOWN, "example.invalid", rc=0, data=dict(
            kind="signatures", jobid="job-1", files=2, license=1, crash=0, environment=0, traceback=0,
            timeout=0, memory=0, disk=0))
        self.workflow = Workflow(profile(), lambda **kw: self.transport)

    def start(self, key="one"):
        return self.store.start(self.workflow, key, dict(PARAMETERS), source(self.root / "repo"), "d" * 64)

    def test_collect_writes_a_report_and_links_it(self):
        self.start()
        result = self.store.observe(self.workflow, "one", collect=True)
        self.assertEqual("tool-error", result["failure_cause"])
        report = json.loads(Path(self.store.path("one"), "failure.json").read_text())
        self.assertEqual(("failed", 7), (report["outcome"]["observation"], report["outcome"]["execution_rc"]))
        self.assertEqual(1, report["evidence"]["log_signatures"]["license"])
        self.assertEqual("open", report["exploration"]["status"])
        self.assertIn("Failure report", Path(self.store.path("one"), "collection.md").read_text())
        self.assertIn("tool-error", Path(result["failure_report"]).read_text())
        stored = "".join(p.read_text() for p in Path(self.store.path("one")).glob("failure.*"))
        self.assertNotIn(PARAMETERS["message"], stored)

    def test_an_unfinished_job_writes_no_report(self):
        self.transport.status.return_value = Result(KNOWN, "example.invalid", rc=0,
                                                    data={"kind": "status", "jobid": "job-1", "state": "running"})
        self.start()
        self.store.observe(self.workflow, "one", collect=True)
        self.assertFalse(Path(self.store.path("one"), "failure.json").exists())
        self.transport.signatures.assert_not_called()

    def test_declarations_limits_history_and_listing(self):
        self.start()
        self.store.observe(self.workflow, "one", collect=True)
        with self.assertRaisesRegex(ContractError, "judgement for a human"):
            self.store.declare_failure("one", cause="engine-defect", by="agent")
        with self.assertRaisesRegex(ContractError, "--by"):
            self.store.declare_failure("one", cause="tool-error")
        self.store.declare_failure("one", cause="tool-error", by="agent", note="licence exhausted 16:33-16:41")
        self.store.declare_failure("one", question="is asic7 the only host with free seats?", by="agent")
        # Re-collecting refreshes the facts and keeps the declarations.
        self.store.observe(self.workflow, "one", collect=True)
        report = json.loads(Path(self.store.path("one"), "failure.json").read_text())
        self.assertEqual(["agent"], [d["by"] for d in report["cause"]["declared"]])
        self.assertEqual("is asic7 the only host with free seats?", report["exploration"]["question"])
        self.assertEqual(["one"], [r["task_key"] for r in self.store.failures(self.workflow)["reports"]])
        self.store.declare_failure("one", close="rerun on asic6 passed", by="human")
        self.assertEqual([], self.store.failures(self.workflow)["reports"])
        self.assertEqual(1, len(self.store.failures(self.workflow, include_closed=True)["reports"]))

    def test_earlier_attempts_at_the_same_request_are_listed(self):
        self.start("first")
        self.store.observe(self.workflow, "first", collect=True)
        time.sleep(0.01)
        self.start("second")
        self.store.observe(self.workflow, "second", collect=True)
        report = json.loads(Path(self.store.path("second"), "failure.json").read_text())
        self.assertEqual(2, report["attempts"]["count"])
        self.assertEqual(["first"], [r["task_key"] for r in report["attempts"]["earlier"]])

    def test_cli_report_and_failures(self):
        self.start()
        self.store.observe(self.workflow, "one", collect=True)
        p = self.root / "profile.json"
        p.write_text(json.dumps(profile()))
        base = [sys.executable, "-B", "-m", "jobs.workflow"]
        common = ["--profile", str(p), "--state-dir", self.store.directory]
        out = subprocess.run(base + ["report"] + common + ["--task-key", "one", "--cause", "abandoned", "--by", "agent"],
                             stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(2, out.returncode)
        self.assertIn("judgement for a human", out.stdout)
        out = subprocess.run(base + ["report"] + common + ["--task-key", "one", "--cause", "abandoned", "--by", "human",
                                                           "--question", "drop this corner?"],
                             stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(0, out.returncode, out.stdout)
        self.assertEqual("abandoned", json.loads(out.stdout)["cause"])
        out = subprocess.run(base + ["failures"] + common, stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual("drop this corner?", json.loads(out.stdout)["reports"][0]["question"])


# Log-line SHAPES for report.sh's licence signature (values invented, no paths).
# Every tool announces a successful checkout; only a denial is a licence failure.
LICENCE_CHATTER = [
    "License checkout complete (2 seconds elapsed).",
    "Checking out license: Genus_Synthesis",
    "Virtuoso Framework License (111) was checked out successfully. Total checkout time was 0.21s.",
    "<CMD> setLicenseCheck -checkout invs_dfm",
    "\t\tinvs\tInnovus Implementation System\t25.1\tcheckout succeeded",
    "[12:00:00.000] Configured Lic search path (25.01-s002): 5280@licsrv.example.invalid",
    "No more licenses are available for multi-cpu acceleration.",
    "The number of CPUs is reduced from 8 to 4 due to license limit",
    "calibrelvs license acquired.",
    "Time spent in licensing: elapsed = 12 ms.",
    "Using FLEXlm version 11.19.0",
    "FLEXnet Publisher client initialised",
]
LICENCE_DENIALS = [
    "ERROR (SPECTRE-209): license checkout failed",
    "FATAL (SPECTRE-18): Cannot run the simulation because the required license could not be checked out.",
    "**ERROR: (IMPLIC-10):\tOption license 'x' (Innovus_DFM; INVS; 25.1) is not available on the license server.",
    "Failed to check out license: Genus_Synthesis",
    "Unable to obtain a license for feature calibrelvs",
    "FLEXnet Licensing error:-5,357",
    "FLEXlm error: -15,10",
    "License server machine is down or not responding.",
    "Cannot connect to license server system.",
    "Licensed number of users already reached.",
    "No such feature exists.",
    "license for Virtuoso_XL has expired",
    "License request denied for feature Innovus_Impl",
]
REPORT_SH = os.path.join(os.path.dirname(__file__), "bin", "report.sh")


@unittest.skipUnless(os.name == "posix" and os.path.isfile("/bin/sh"), "requires POSIX shell")
class LicenceSignature(unittest.TestCase):
    """report.sh signatures, run for real on fixture log text: chatter scores 0,
    each denial scores exactly 1."""

    def license_count(self, lines):
        temp = tempfile.TemporaryDirectory(prefix="licsig-")
        self.addCleanup(temp.cleanup)
        job = os.path.join(temp.name, "job-1")
        os.mkdir(job)
        Path(job, "stdout.log").write_text("".join(line + "\n" for line in lines))
        Path(job, "meta.json").write_text('{"jobid":"job-1"}\n')
        env = dict(os.environ, ASICJOBS_DIR=temp.name, HOME=temp.name)
        out = subprocess.run(["/bin/sh", REPORT_SH, "signatures", "job-1"], env=env,
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)["license"]

    def test_checkout_success_chatter_is_not_a_licence_failure(self):
        for line in LICENCE_CHATTER:
            with self.subTest(line=line):
                self.assertEqual(0, self.license_count([line]))

    def test_each_denial_counts(self):
        for line in LICENCE_DENIALS:
            with self.subTest(line=line):
                self.assertEqual(1, self.license_count([line]))
        self.assertEqual(len(LICENCE_DENIALS), self.license_count(LICENCE_CHATTER + LICENCE_DENIALS))


@unittest.skipUnless(os.name == "posix" and os.path.isfile("/bin/sh"), "requires POSIX shell")
class RealTracker(unittest.TestCase):
    def run_job(self, key, message):
        temp = tempfile.TemporaryDirectory(prefix="failure-real-")
        self.addCleanup(temp.cleanup)
        home = temp.name
        p = profile()
        p["work_root"] = os.path.join(home, "work")
        os.mkdir(p["work_root"])
        # Print the message to the job's stdout.log as well, then exit 7.
        p["argv"][2] = "printf '%s\\n' \"$1\"; printf '%s' \"$1\" > result.txt; exit \"$2\""
        env = dict(os.environ, HOME=home)
        for key in ("ASICJOBS_DIR", "ASICJOBS_ID", "ASICJOBS_JOBDIR"):
            env.pop(key, None)

        def runner(argv, input_bytes, timeout):
            proc = subprocess.Popen(["/bin/sh", "-s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env, cwd=home)
            out, err = proc.communicate(input_bytes, timeout=timeout)
            return proc.returncode, out, err
        workflow = Workflow(p, lambda host, **kw: Transport(host=host, runner=runner))
        store = TaskStore(os.path.join(home, "state"))
        store.start(workflow, key, {"message": message, "exit_code": 7},
                    source(os.path.join(home, "repo")), "d" * 64)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = store.observe(workflow, key, collect=True)
            if result["observation"] in ("done", "failed", "killed"):
                break
            time.sleep(0.15)
        self.assertEqual("failed", result["observation"])
        report = json.loads(Path(store.path(key), "failure.json").read_text())
        self.assertEqual(7, report["outcome"]["execution_rc"])
        return result, report

    def test_licence_failure_is_a_tool_error_from_log_signatures(self):
        result, report = self.run_job("licence", "\n".join(LICENCE_CHATTER[:3] + LICENCE_DENIALS[:1]))
        self.assertEqual("tool-error", result["failure_cause"])
        self.assertEqual(1, report["evidence"]["log_signatures"]["license"])

    def test_checkout_chatter_leaves_an_early_failure_unclassified(self):
        # 2026-09-26: a passing Genus run read license 9 from its own "checkout
        # complete" lines, so any early failure was called a licence tool-error.
        result, report = self.run_job("chatter", "\n".join(LICENCE_CHATTER))
        self.assertEqual("unclassified", result["failure_cause"])
        self.assertEqual(0, report["evidence"]["log_signatures"]["license"])


if __name__ == "__main__":
    unittest.main()
