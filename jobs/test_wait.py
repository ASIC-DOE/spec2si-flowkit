"""`collect --wait`: a bounded foreground wait, so a one-shot session can collect its own job.

Run: python -m unittest jobs.test_wait -v
"""
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from . import workflow


class Wait(unittest.TestCase):
    def test_polls_until_the_job_is_no_longer_pending(self):
        seen = iter([dict(observation="submitted"), dict(observation="running"),
                     dict(observation="done", engineering="pass")])
        with patch.object(workflow, "WAIT_POLL", 0.01):
            result = workflow.waited(lambda: next(seen), 30)
        self.assertEqual(("done", "pass"), (result["observation"], result["engineering"]))
        self.assertEqual(dict(bound_s=30, still_pending=False), result["waited"])

    def test_the_bound_holds_and_says_it_is_still_pending(self):
        calls = []

        def observe():
            calls.append(1)
            return dict(observation="running")
        with patch.object(workflow, "WAIT_POLL", 0.05):
            t0 = time.monotonic()
            result = workflow.waited(observe, 1)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertTrue(result["waited"]["still_pending"])
        self.assertGreater(len(calls), 2)

    def test_without_wait_it_observes_once(self):
        calls = []
        self.assertNotIn("waited", workflow.waited(lambda: calls.append(1) or dict(observation="running"), None))
        self.assertEqual(1, len(calls))

    def test_the_cli_refuses_wait_outside_collect_and_past_the_bound(self):
        for argv in (["status", "--wait", "10"], ["collect", "--wait", str(workflow.WAIT_MAX + 1)],
                     ["collect", "--wait", "0"]):
            out = io.StringIO()
            with redirect_stdout(out), patch("builtins.open", unittest.mock.mock_open(read_data="{}")), \
                    patch.object(workflow, "Workflow"):
                rc = workflow.main(argv + ["--profile", "p.json", "--task-key", "k", "--state-dir", "/s"])
            self.assertEqual(2, rc, argv)
            self.assertIn("--wait is for collect", json.loads(out.getvalue())["reason"])


if __name__ == "__main__":
    unittest.main()
