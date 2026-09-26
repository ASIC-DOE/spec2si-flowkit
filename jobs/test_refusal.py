"""The adapter's refusal reason reaches the failure report (study §8.2: a named failure, not a traceback count).

pilot.run leaves refusal.json in the job workspace when the adapter refuses; the evidence reader returns
it, bounded and redacted, for a failed job whose tracker identity holds; collect passes it on; the
failure report names it.

Run: python -m unittest jobs.test_refusal -v
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from . import failure, pilot
from .remote import KNOWN, Result
from . import test_evidence as fixtures
from .workflow import Workflow

reader, report_profile = fixtures.reader, fixtures.report_profile

PDK = "/u/cad/pdk-2020/TSMC65/models/spectre/toplevel.scs"


class Writer(unittest.TestCase):
    def test_redact_cuts_absolute_paths_and_clips(self):
        self.assertEqual("relative DUT dependency requires staging: .../toplevel.scs",
                         pilot.redact("relative DUT dependency requires staging: " + PDK))
        self.assertEqual("deployment/bnl/tracked_job.py stays", pilot.redact("deployment/bnl/tracked_job.py stays"))
        self.assertEqual(pilot.REFUSAL_MAX, len(pilot.redact("x" * 1000)))
        self.assertEqual("two lines", pilot.redact("two\n  lines"))

    def test_a_refused_run_leaves_its_reason_and_still_fails(self):
        class Adapter:
            SPEC = dict(cases=["tt"])
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, ASICJOBS_ID="job-7"):
                    with self.assertRaisesRegex(ValueError, "unsupported case"):
                        pilot.run(Adapter, tmp, "ff")
                data = json.loads(Path(tmp, "refusal.json").read_text())
            finally:
                os.chdir(cwd)
        self.assertEqual(dict(schema=1, kind="refusal", job_id="job-7", stage="identity", error="ValueError",
                              reason="unsupported case"), data)


class Reader(unittest.TestCase):
    # test_evidence's tracker-record fixture, without re-running its tests.
    setUp, write, records = (fixtures.ReportFixtures.setUp, fixtures.ReportFixtures.write,
                             fixtures.ReportFixtures.records)

    def refuse(self, **over):
        data = dict(schema=1, kind="refusal", job_id="job-1", stage="execute", error="ValueError",
                    reason="missing or nonfinite metric while reading " + PDK)
        data.update(over)
        (self.work / "refusal.json").write_text(json.dumps(data))
        self.result.update(state="failed", rc=1)
        self.records()

    def test_a_failed_job_carries_its_refusal_redacted(self):
        self.refuse()
        verdict = reader.validate(self.contract)
        self.assertEqual(dict(stage="execute", error="ValueError",
                              reason="missing or nonfinite metric while reading .../toplevel.scs"), verdict["refusal"])
        self.assertNotIn("/u/cad", json.dumps(verdict))

    def test_another_jobs_a_malformed_or_an_oversized_refusal_is_ignored(self):
        self.refuse(job_id="job-2")
        self.assertNotIn("refusal", reader.validate(self.contract))
        self.refuse(reason=["not", "text"])
        self.assertNotIn("refusal", reader.validate(self.contract))
        (self.work / "refusal.json").write_text("x" * 5000)
        self.assertNotIn("refusal", reader.validate(self.contract))

    def test_a_done_job_is_not_asked(self):
        self.refuse()
        self.result.update(state="done", rc=0)
        self.records()
        self.assertNotIn("refusal", reader.validate(self.contract))


class Collect(unittest.TestCase):
    def test_a_refused_job_is_named_in_the_collection_and_the_failure_report(self):
        transport = Mock()
        w = Workflow(report_profile(), lambda **kw: transport)
        ref, _ = w.prepare({"message": "demo", "exit_code": 0})
        ref["job_id"] = "job-1"
        transport.status.return_value = Result(KNOWN, ref["host"], rc=0,
                                               data=dict(kind="status", jobid="job-1", state="failed"))
        transport.why.return_value = Result(KNOWN, ref["host"], rc=0, data=dict(
            jobid="job-1", result=dict(jobid="job-1", state="failed", rc=1, artifacts=[])))
        refusal = dict(stage="execute", error="ValueError", reason="missing or nonfinite metric")
        transport.evidence.return_value = Result(KNOWN, ref["host"], rc=0, data=dict(
            kind="evidence", jobid="job-1", evidence="unverified", engineering="unchecked", refusal=refusal))
        collected = w.observe(ref, collect=True)
        self.assertEqual(("incomplete", refusal), (collected["evidence"], collected["refusal"]))
        record = dict(task_key="k", reference=ref, created_at=1.0, profile_id="p", profile_version="1")
        report = failure.build(record, collected, [])
        md = failure.markdown(report)
        self.assertIn("Refused by the adapter** (execute stage): `ValueError`: missing or nonfinite metric", md)
        self.assertIn("the adapter refused the run at its execute stage", report["cause"]["derived"]["basis"])

    def test_no_refusal_changes_nothing(self):
        transport = Mock()
        w = Workflow(report_profile(), lambda **kw: transport)
        ref, _ = w.prepare({"message": "demo", "exit_code": 0})
        ref["job_id"] = "job-1"
        transport.status.return_value = Result(KNOWN, ref["host"], rc=0,
                                               data=dict(kind="status", jobid="job-1", state="failed"))
        transport.why.return_value = Result(KNOWN, ref["host"], rc=0, data=dict(
            jobid="job-1", result=dict(jobid="job-1", state="failed", rc=1, artifacts=[])))
        transport.evidence.return_value = Result(KNOWN, ref["host"], rc=0, data=dict(kind="evidence", jobid="job-1"))
        collected = w.observe(ref, collect=True)
        self.assertNotIn("refusal", collected)
        self.assertNotIn("Refused", failure.markdown(failure.build(
            dict(task_key="k", reference=ref, created_at=1.0), collected, [])))


if __name__ == "__main__":
    unittest.main()
