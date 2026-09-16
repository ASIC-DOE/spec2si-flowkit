"""Strict evidence tests: actual tracker records plus adversarial report fixtures."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from .workflow import Workflow, digest, ContractError
from .state import TaskStore
from .remote import KNOWN, UNKNOWN, Result
from .test_workflow import profile

spec = importlib.util.spec_from_file_location("evidence_reader", Path(__file__).parent / "bin" / "evidence.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def report_profile():
    p = profile()
    p["engineering_report"] = dict(parser="json-v1", path="report.json", design="demo", top="top",
                                   checks=["timing", "drc"], corners=["tt", "ss"])
    p["expected_artifacts"] = ["report.json"]
    return p


class CollectorFailures(unittest.TestCase):
    def test_unstamped_subset_and_unavailable_reader_never_pass(self):
        for verdict, count in (("UNSTAMPED", 0), ("OK", 0), ("OK", 1)):
            with self.subTest(verdict=verdict, count=count):
                transport = Mock()
                w = Workflow(report_profile(), lambda **kw: transport)
                ref, _ = w.prepare({"message": "demo", "exit_code": 0})
                ref["job_id"] = "job-1"
                status = dict(kind="status", jobid="job-1", state="done")
                result = dict(jobid="job-1", state="done", rc=0,
                              artifacts=[dict(path="report.json", exists=True, jobid="job-1", sha256="a" * 64)])
                transport.status.return_value = Result(KNOWN, ref["host"], rc=0, data=status)
                transport.why.return_value = Result(KNOWN, ref["host"], rc=0, data=dict(jobid="job-1", result=result))
                transport.verify.return_value = Result(KNOWN, ref["host"], rc=0,
                    data=dict(jobid="job-1", verdict=verdict, checked=count, ok=count))
                transport.evidence.return_value = Result(UNKNOWN, ref["host"])
                collected = w.observe(ref, collect=True)
                self.assertEqual("unverified", collected["evidence"])
                self.assertEqual("unchecked", collected["engineering"])
                if count == 0:
                    transport.evidence.assert_not_called()


class ReportFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flowkit-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.job = self.root / "jobs" / "job-1"
        self.job.mkdir(parents=True)
        self.env = patch.dict(os.environ, ASICJOBS_DIR=str(self.job.parent))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.contract = dict(job_id="job-1", workspace=str(self.work), repository="repo",
                             expected_artifacts=["report.json"], report=report_profile()["engineering_report"],
                             identity=dict(manifest_sha256="a" * 64, source_sha256="b" * 64, request_sha256="c" * 64))
        self.report = dict(schema=1, job_id="job-1", repository="repo", design="demo", top="top",
                           checks=[dict(name=n, corner=c, status="pass") for n in ("timing", "drc") for c in ("tt", "ss")],
                           **self.contract["identity"])
        self.meta = dict(schema=1, jobid="job-1", cwd=str(self.work), expect=["report.json"])
        self.result = dict(schema=1, jobid="job-1", state="done", rc=0)
        self.write()

    def write(self, raw=None):
        raw = json.dumps(self.report).encode() if raw is None else raw
        (self.work / "report.json").write_bytes(raw)
        import hashlib
        self.result["artifacts"] = [dict(path="report.json", jobid="job-1", exists=True,
                                         bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())]
        self.records()

    def records(self):
        (self.job / "meta.json").write_text(json.dumps(self.meta))
        (self.job / "result.json").write_text(json.dumps(self.result))
        (self.job / "status.json").write_text(json.dumps(dict(schema=1, jobid="job-1", state=self.result["state"])))

    def test_pass_and_engineering_failure(self):
        self.assertEqual("pass", reader.validate(self.contract)["engineering"])
        self.report["checks"][0]["status"] = "fail"
        self.write()
        verdict = reader.validate(self.contract)
        self.assertEqual("fail", verdict["engineering"])
        self.assertEqual(1, verdict["checks_failed"])
        self.assertEqual("tracker-verified", verdict["evidence"])

    def test_identity_mismatch_matrix(self):
        for key in ("job_id", "repository", "design", "top", "manifest_sha256", "source_sha256", "request_sha256"):
            with self.subTest(key=key):
                old = self.report[key]
                self.report[key] = "wrong"
                self.write()
                self.assertEqual("invalid", reader.validate(self.contract)["engineering"])
                self.report[key] = old

    def test_missing_duplicate_extra_unknown_checks(self):
        original = copy.deepcopy(self.report["checks"])
        variants = [original[:-1], original + [original[0]], original[:-1] + [original[0]],
                    original[:-1] + [dict(name="timing", corner="other", status="pass")],
                    original[:-1] + [dict(name="drc", corner="ss", status="skip")]]
        for checks in variants:
            self.report["checks"] = checks
            self.write()
            self.assertEqual("invalid", reader.validate(self.contract)["engineering"])

    def test_invalid_and_duplicate_json_and_oversize(self):
        for raw in (b'not JSON', b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'{}', b'\xff'):
            self.write(raw)
            self.assertEqual("invalid", reader.validate(self.contract)["engineering"])
        self.write(b' ' * 1048577)
        self.assertEqual("unverified", reader.validate(self.contract)["evidence"])

    def test_unstamped_and_stale_and_wrong_workspace(self):
        self.result["artifacts"][0]["sha256"] = ""
        self.records()
        self.assertEqual("unverified", reader.validate(self.contract)["evidence"])
        self.write()
        (self.work / "report.json").write_text("changed")
        self.assertEqual("unverified", reader.validate(self.contract)["evidence"])
        self.write()
        self.meta["cwd"] = str(self.root)
        self.records()
        self.assertIn("tracker-identity-mismatch", reader.validate(self.contract)["issues"])

    def test_subset_and_no_durable_identity(self):
        self.contract["expected_artifacts"].append("missing.txt")
        self.assertEqual("unverified", reader.validate(self.contract)["evidence"])
        self.contract["expected_artifacts"].pop()
        self.contract["identity"] = None
        self.assertEqual("unchecked", reader.validate(self.contract)["engineering"])

    def test_failed_execution_cannot_have_pass_verdict(self):
        self.result.update(state="failed", rc=7)
        self.records()
        self.assertEqual("invalid", reader.validate(self.contract)["engineering"])

    def test_parser_names_are_closed(self):
        p = report_profile()
        p["engineering_report"]["parser"] = "arbitrary.plugin"
        with self.assertRaises(ContractError):
            Workflow(p)

    @unittest.skipUnless(os.name == "posix", "symlink fixture requires POSIX")
    def test_symlink_artifact_is_rejected(self):
        path = self.work / "report.json"
        outside = self.root / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
        self.assertEqual("unverified", reader.validate(self.contract)["evidence"])


@unittest.skipUnless(os.name == "posix", "requires actual POSIX tracker")
class RealEngineeringJobs(unittest.TestCase):
    def test_reports_through_durable_collection(self):
        from .test_workflow import ExampleJobs
        fixture = ExampleJobs()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        p = report_profile()
        p["work_root"] = fixture.p["work_root"]
        p["parameters"] = {"verdict": {"type": "string", "choices": ["pass", "fail"]}}
        script = '''import os,json,sys
r=dict(schema=1,job_id=os.environ['ASICJOBS_ID'],repository='flowkit-synthetic',design='demo',top='top',
manifest_sha256=os.environ['ASICJOBS_EXPECTED_MANIFEST_SHA256'],source_sha256=os.environ['ASICJOBS_EXPECTED_SOURCE_SHA256'],
request_sha256=os.environ['ASICJOBS_EXPECTED_REQUEST_SHA256'],checks=[dict(name=n,corner=c,status=sys.argv[1]) for n in ['timing','drc'] for c in ['tt','ss']])
with open('report.json','w') as f: json.dump(r,f)
'''
        p["argv"] = ["/usr/bin/python3", "-c", script, {"parameter": "verdict"}]
        w = Workflow(p, fixture.factory)
        store = TaskStore(os.path.join(fixture.home, "private-state"))
        source = dict(root=os.path.join(fixture.home, "source"), head="a" * 40,
                      patch_sha256="b" * 64, untracked_sha256="c" * 64)
        for verdict in ("pass", "fail"):
            start = store.start(w, verdict, {"verdict": verdict}, source, "d" * 64)
            for _ in range(100):
                result = store.observe(w, verdict, collect=True)
                if result["observation"] == "done":
                    break
                time.sleep(0.1)
            self.assertEqual(verdict, result["engineering"], result)
            self.assertEqual("tracker-verified", result["evidence"])
            summary = Path(result["summary_path"]).read_text()
            self.assertIn("Engineering: " + verdict, summary)
            self.assertIn("[Task intent and job reference](task.json)", summary)
            self.assertNotIn('"checks":', json.dumps(result))
        self.assertEqual(2, fixture.launches)


if __name__ == "__main__":
    unittest.main()
