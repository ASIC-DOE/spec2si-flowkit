"""Acceptance for worker attempt 10: `report --note` alone records evidence on a failure report.

Found in live use (2026-09-26): a finished diagnosis had a measured fact to
add to an open report (xt011's buffer linearity), and `report --note` was
refused ("report needs --cause, --contradicts, --question or --close"); the
only way through was to re-declare the cause. The decision:

- A note given alone is appended to the exploration history as
  `field="note"`, with `--by` (or "unspecified"), and changes nothing else:
  no cause declaration is added, the question and status stay as they were.
- A note given with `--cause` stays on that declaration, as before.
- failure.md shows the notes under "For exploration".
- The CLI accepts `report --task-key K --note ...` alone.

Run: python -m unittest jobs.test_failure_note -v
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from . import failure
from .test_failure import Reports
from .test_workflow import profile


class Notes(unittest.TestCase):
    # The Reports fixture (a store and a mocked tracker), without re-running its tests.
    setUp = Reports.setUp
    start = Reports.start

    def collected(self):
        self.start()
        self.store.observe(self.workflow, "one", collect=True)
        return json.loads(Path(self.store.path("one"), "failure.json").read_text())

    def test_a_note_alone_is_evidence_in_the_history(self):
        self.collected()
        self.store.declare_failure("one", question="is the model valid at the large loads?", by="agent")
        self.store.declare_failure("one", note="charge per cycle 0.414 pC at 160 MHz vs C*V 0.624 pC", by="agent")
        report = json.loads(Path(self.store.path("one"), "failure.json").read_text())
        last = report["exploration"]["history"][-1]
        self.assertEqual(("note", "charge per cycle 0.414 pC at 160 MHz vs C*V 0.624 pC", "agent"),
                         (last["field"], last["value"], last["by"]))
        self.assertEqual([], report["cause"]["declared"])
        self.assertEqual("is the model valid at the large loads?", report["exploration"]["question"])
        self.assertEqual("open", report["exploration"]["status"])
        self.assertIn("0.414 pC at 160 MHz", failure.markdown(report))
        self.assertIn("0.414 pC at 160 MHz", Path(self.store.path("one"), "failure.md").read_text())

    def test_a_note_with_a_cause_stays_on_the_declaration(self):
        self.collected()
        self.store.declare_failure("one", cause="tool-error", by="agent", note="licence exhausted")
        report = json.loads(Path(self.store.path("one"), "failure.json").read_text())
        self.assertEqual("licence exhausted", report["cause"]["declared"][-1]["note"])
        self.assertNotIn("note", [h["field"] for h in report["exploration"]["history"]])

    def test_a_note_without_by_is_unspecified(self):
        self.collected()
        self.store.declare_failure("one", note="seen twice this week")
        report = json.loads(Path(self.store.path("one"), "failure.json").read_text())
        self.assertEqual("unspecified", report["exploration"]["history"][-1]["by"])

    def test_the_cli_accepts_a_note_alone(self):
        self.collected()
        p = self.root / "profile.json"
        p.write_text(json.dumps(profile()))
        out = subprocess.run([sys.executable, "-B", "-m", "jobs.workflow", "report", "--profile", str(p),
                              "--state-dir", self.store.directory, "--task-key", "one", "--by", "agent",
                              "--note", "rerun on asic6 failed the same way"],
                             stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(0, out.returncode, out.stdout)
        report = json.loads(Path(self.store.path("one"), "failure.json").read_text())
        self.assertEqual("rerun on asic6 failed the same way", report["exploration"]["history"][-1]["value"])


if __name__ == "__main__":
    unittest.main()
