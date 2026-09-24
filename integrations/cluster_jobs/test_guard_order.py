"""Acceptance test for the guard's argument order (worker pilot 1, 2026-09-24).

A route's argument prefix used to match only as the command's FIRST arguments,
so tsmc65's guarded `run.py smoketest_flow` escaped as `run.py --phase syn
smoketest_flow`. The decision: the prefix may also follow LEADING OPTIONS,
where every earlier argument is an option (starts with `-`) or the value
directly after one. A positional word before the prefix still means another
subcommand, which stays unrouted.
"""
import json
from pathlib import Path
import tempfile
import unittest

from .hook import handle

HERE = Path(__file__).resolve().parent


class GuardArgumentOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="flowkit-guard-order-")
        self.addCleanup(self.tmp.cleanup)
        self.cfg = json.loads((HERE / "example_config.json").read_text())
        self.cfg["receipt_dir"] = self.tmp.name
        self.cfg["wrappers"] = ["asic_tools.csh"]
        self.cfg["routes"][0]["argument_prefixes"] = [["smoketest_flow"], ["launch", "normal"]]

    def decision(self, command):
        event = dict(cwd="/example/repo", session_id="t", hook_event_name="PreToolUse", tool_name="Bash",
                     tool_input={"command": command})
        return handle(event, self.cfg).get("hookSpecificOutput", {}).get("permissionDecision")

    def test_prefix_after_leading_options_is_routed(self):
        for command in ("synthetic-eda smoketest_flow",
                        "synthetic-eda smoketest_flow --phase syn",
                        "synthetic-eda --phase syn smoketest_flow",
                        "synthetic-eda --dry-run smoketest_flow",
                        "synthetic-eda --phase=syn smoketest_flow",
                        "synthetic-eda -j 4 --phase syn smoketest_flow --top x",
                        "python3 digital/run.py --phase syn smoketest_flow",
                        "ssh compute.example.invalid 'cd w && asic_tools.csh synthetic-eda --phase syn smoketest_flow'",
                        "synthetic-eda --mode fast launch normal"):
            with self.subTest(command=command):
                self.assertEqual("deny", self.decision(command))

    def test_a_positional_word_before_the_prefix_is_another_subcommand(self):
        for command in ("synthetic-eda other_flow smoketest_flow",
                        "synthetic-eda status --run-id smoketest_flow",
                        "synthetic-eda --phase syn other_flow",
                        "synthetic-eda launch control",
                        "synthetic-eda --mode fast launch control"):
            with self.subTest(command=command):
                self.assertIsNone(self.decision(command))


if __name__ == "__main__":
    unittest.main()
