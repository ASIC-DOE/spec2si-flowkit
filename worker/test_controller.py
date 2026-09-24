"""The worker controller's decisions, driven by a scripted fake harness (no model, no cost).

Run: python -m unittest worker.test_controller -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from worker.controller import Run, load_contract

CALC_BUG = "def add(a, b):\n    return a - b\n"
CALC_FIX = "def add(a, b):\n    return a + b\n"
ACCEPT = "from calc import add\nassert add(2, 3) == 5, 'add(2, 3) != 5'\nprint('accept ok')\n"
OTHER = "import calc\nprint('other ok')\n"


def git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
                   + list(args), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def proposal(action="patch", **kw):
    p = dict(action=action, summary="did something", observations=["saw it"], hypotheses=["maybe"],
             files_changed=[], stop_reason="", question="")
    p.update(kw)
    return p


class Controller(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="worker-")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.repo = os.path.join(self.root, "repo")
        os.mkdir(self.repo)
        for name, text in (("calc.py", CALC_BUG), ("check_accept.py", ACCEPT), ("check_other.py", OTHER),
                           ("notes.txt", "notes\n")):
            with open(os.path.join(self.repo, name), "w") as fh:
                fh.write(text)
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.state = os.path.join(self.root, "state")
        self.prompts = []

    def contract(self, **budget):
        c = dict(schema=1, id="fix-add", kind="maintenance", goal="make add() add", repo=self.repo,
                 editable=["calc.py"],
                 gates=[dict(name="accept", run=[sys.executable, "check_accept.py"], acceptance=True,
                             protects=["check_accept.py"]),
                        dict(name="other", run=[sys.executable, "check_other.py"])],
                 budget=dict(dict(rounds=3, usd=6.0, minutes=30, per_round_usd=2.0), **budget))
        path = os.path.join(self.root, "contract.json")
        with open(path, "w") as fh:
            json.dump(c, fh)
        return load_contract(path)

    def harness(self, script):
        """script: list of (files to write, proposal) per round."""
        rounds = iter(script)

        def run(name, prompt, cwd, budget, timeout, record_dir, model=None):
            self.prompts.append(prompt)
            files, prop, cost = next(rounds)
            for rel, text in files.items():
                with open(os.path.join(cwd, rel), "w") as fh:
                    fh.write(text)
            return dict(ok=True, proposal=prop, cost_usd=cost, turns=3, tokens=None, seconds=1.0, error=None, raw="")
        return run

    def execute(self, script, **budget):
        return Run(self.contract(**budget), self.state, harness=self.harness(script)).execute()

    def test_fix_in_one_round_is_ready_for_review_on_its_branch(self):
        out = self.execute([({"calc.py": CALC_FIX}, proposal(), 0.5)])
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual(["calc.py"], out["files"])
        self.assertIn("return a + b", open(out["patch"]).read())
        log = subprocess.run(["git", "-C", self.repo, "log", "--oneline", out["branch"]], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout
        self.assertIn("worker fix-add", log)
        self.assertEqual(CALC_BUG, open(os.path.join(self.repo, "calc.py")).read())   # the checkout is untouched
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(out["patch"]), "review.md")))

    def test_gate_failures_feed_the_next_round(self):
        wrong = "def add(a, b):\n    return a * b\n"
        out = self.execute([({"calc.py": wrong}, proposal(), 0.5), ({"calc.py": CALC_FIX}, proposal(), 0.5)])
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual(2, out["rounds"])
        self.assertIn("add(2, 3) != 5", self.prompts[1])

    def test_protected_acceptance_test_edit_stops(self):
        out = self.execute([({"check_accept.py": "print('accept ok')\n"}, proposal(), 0.5)])
        self.assertEqual(("stopped", "scope"), (out["status"], out["stage"]))
        self.assertIn("protected", out["reason"])
        report = json.load(open(os.path.join(os.path.dirname(out["failure_report"]), "failure.json")))
        self.assertEqual("unclassified", report["cause"]["current"])

    def test_edit_outside_editable_paths_stops(self):
        out = self.execute([({"calc.py": CALC_FIX, "notes.txt": "changed\n"}, proposal(), 0.5)])
        self.assertEqual("scope", out["stage"])
        self.assertIn("notes.txt", out["reason"])

    def test_budget_exhaustion_is_a_gate_fail_report(self):
        wrongs = ["def add(a, b):\n    return a * b\n", "def add(a, b):\n    return b - a\n",
                  "def add(a, b):\n    return 0\n"]
        out = self.execute([({"calc.py": w}, proposal(), 0.5) for w in wrongs])
        self.assertEqual(("stopped", "budget"), (out["status"], out["stage"]))
        report = json.load(open(out["failure_report"].replace(".md", ".json")))
        self.assertEqual("gate-fail", report["cause"]["derived"]["cause"])
        self.assertEqual(3, report["attempts"]["count"])
        self.assertIn("## Worker", open(out["failure_report"]).read())

    def test_repeated_diff_stops_for_no_progress(self):
        wrong = "def add(a, b):\n    return a * b\n"
        out = self.execute([({"calc.py": wrong}, proposal(), 0.5), ({"calc.py": wrong}, proposal(), 0.5)])
        self.assertEqual("no-progress", out["stage"])

    def test_worker_stop_carries_its_question(self):
        out = self.execute([({}, proposal("stop", stop_reason="add is used as subtract elsewhere",
                                          question="Should add() keep subtracting for legacy callers?"), 0.5)])
        self.assertEqual("worker-stop", out["stage"])
        report = json.load(open(out["failure_report"].replace(".md", ".json")))
        self.assertEqual("Should add() keep subtracting for legacy callers?", report["exploration"]["question"])

    def test_dollar_budget_stops(self):
        wrong = ["def add(a, b):\n    return a * b\n", "def add(a, b):\n    return b - a\n"]
        out = self.execute([({"calc.py": w}, proposal(), 5.0) for w in wrong], usd=6.0)
        self.assertEqual("budget", out["stage"])
        self.assertIn("dollar", out["reason"])
        self.assertEqual(2, out["rounds"])

    def test_broken_baseline_and_nothing_to_do(self):
        with open(os.path.join(self.repo, "check_other.py"), "w") as fh:
            fh.write("raise SystemExit(3)\n")
        git(self.repo, "commit", "-q", "-am", "break other")
        out = self.execute([])
        self.assertEqual("baseline", out["stage"])
        with open(os.path.join(self.repo, "check_other.py"), "w") as fh:
            fh.write(OTHER)
        with open(os.path.join(self.repo, "calc.py"), "w") as fh:
            fh.write(CALC_FIX)
        git(self.repo, "commit", "-q", "-am", "already fixed")
        self.assertEqual("nothing-to-do", self.execute([])["status"])


if __name__ == "__main__":
    unittest.main()
