"""Acceptance for worker attempt 9: a Codex run is measured in tokens, never shown as a $0.00 cost.

Codex reports tokens and no price, so a Codex run's review and failure report
read "$0.00" (attempt 4's did), which says the run was free. The decision:
the worker's facts carry the total tokens across rounds, and the review and
failure report state tokens when the harness gave no cost. A Claude run keeps
its dollar figure.

Run: python -m unittest worker.test_cost_report -v
"""
import json
import os
import unittest

from worker.controller import Run
from worker.test_controller import CALC_FIX, Fixture, proposal

WRONG = "def add(a, b):\n    return a * b\n"


class CostReport(Fixture):
    def go(self, harness_name, rounds):
        """rounds: list of (calc.py text or None, proposal, cost_usd, tokens)."""
        script = iter(rounds)

        def run(name, prompt, cwd, budget, timeout, record_dir, model=None, transcript=False):
            text, prop, cost, tokens = next(script)
            if text is not None:
                with open(os.path.join(cwd, "calc.py"), "w") as fh:
                    fh.write(text)
            return dict(ok=True, proposal=prop, cost_usd=cost, turns=None, tokens=tokens, seconds=1.0,
                        error=None, raw="")
        c = self.contract()
        c["harness"]["name"] = harness_name
        return Run(c, self.state, harness=run).execute()

    def text(self, out, name):
        with open(os.path.join(self.state, out["run"], name), encoding="utf-8") as fh:
            return fh.read()

    def test_a_codex_review_states_the_tokens_of_every_round(self):
        out = self.go("codex", [(WRONG, proposal(), None, 1000), (CALC_FIX, proposal(), None, 2345)])
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual(3345, out["tokens"])
        review = self.text(out, "review.md")
        self.assertIn("3345 tokens", review)
        self.assertNotIn("$0.00", review)
        with open(os.path.join(self.state, out["run"], "review.json"), encoding="utf-8") as fh:
            self.assertEqual(3345, json.load(fh)["tokens"])

    def test_a_codex_stop_states_its_tokens_in_the_failure_report(self):
        stop = proposal("stop", stop_reason="cannot", question="which?")
        out = self.go("codex", [(None, stop, None, 777)])
        self.assertEqual("stopped", out["status"])
        report = self.text(out, "failure.md")
        self.assertIn("777 tokens", report)
        self.assertNotIn("$0.00", report)

    def test_a_claude_run_keeps_its_dollars(self):
        out = self.go("claude", [(CALC_FIX, proposal(), 0.5, None)])
        self.assertIn("$0.50", self.text(out, "review.md"))
        self.assertIsNone(out["tokens"])


if __name__ == "__main__":
    unittest.main()
