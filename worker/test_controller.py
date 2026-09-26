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


class Fixture(unittest.TestCase):
    """A tiny repo with a bug, a protected acceptance check and a passing check."""

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
        self.commands = []

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

        def run(name, prompt, cwd, budget, timeout, record_dir, model=None, transcript=False):
            self.prompts.append(prompt)
            files, prop, cost = next(rounds)
            for rel, text in files.items():
                with open(os.path.join(cwd, rel), "w") as fh:
                    fh.write(text)
            raw = os.path.join(record_dir, "harness.jsonl")
            with open(raw, "w") as fh:
                fh.write(json.dumps(dict(transcript=transcript, commands=self.commands)) + "\n")
            return dict(ok=True, proposal=prop, cost_usd=cost, turns=3, tokens=None, seconds=1.0, error=None, raw=raw)
        return run

    def execute(self, script, **budget):
        return Run(self.contract(**budget), self.state, harness=self.harness(script)).execute()


class Controller(Fixture):
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


#: A stand-in for a repo's tracked_job.py: package makes the directory, deploy
#: writes the profile and manifest the start reads.
FAKE_ADAPTER = """import json, os, sys
a = sys.argv
out = a[a.index("--output") + 1]
os.makedirs(out)
if a[1] == "deploy":
    for name in ("profile.json", "manifest.json"):
        with open(os.path.join(out, name), "w") as fh:
            json.dump({}, fh)
print(json.dumps({"ok": a[1]}))
"""
#: A stand-in for deployment.bnl.jobs.workflow: a start records the request and
#: whether calc.add is right at that moment; collect reports it, with a failure
#: report when it is not.
FAKE_WORKFLOW = """import json, os, sys
a = sys.argv
key = a[a.index("--task-key") + 1]
store = os.environ["FAKE_TRACKER"]
path = os.path.join(store, key + ".json")
if a[1] == "start":
    params = json.load(open(a[a.index("--parameters-file") + 1]))
    sys.path.insert(0, os.getcwd())
    import calc
    json.dump(dict(parameters=params, good=calc.add(2, 3) == 5), open(path, "w"))
    print(json.dumps({"observation": "submitted", "task_key": key}))
else:
    r = json.load(open(path))
    out = dict(observation="done", evidence="tracker-verified", job_id="job-" + key,
               engineering="pass" if r["good"] else "fail", reference=dict(workspace="/remote/runs/" + key))
    if not r["good"]:
        report = os.path.join(store, key + "-failure.md")
        open(report, "w").write("# Failure report\\n\\nFailed check: `sum/tt` (add(2, 3) != 5)\\n")
        out.update(failure_report=report, failed_checks=["sum/tt"])
    print(json.dumps(out))
"""


class TrackedGates(Fixture):
    def setUp(self):
        super().setUp()
        for rel, text in (("adapter.py", FAKE_ADAPTER), ("deployment/__init__.py", ""),
                          ("deployment/bnl/__init__.py", ""), ("deployment/bnl/jobs/__init__.py", ""),
                          ("deployment/bnl/jobs/workflow.py", FAKE_WORKFLOW)):
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)) or self.repo, exist_ok=True)
            with open(os.path.join(self.repo, rel), "w") as fh:
                fh.write(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "fake tracker")
        self.tracker = os.path.join(self.root, "tracker")
        os.mkdir(self.tracker)
        os.environ["FAKE_TRACKER"] = self.tracker
        self.addCleanup(os.environ.pop, "FAKE_TRACKER", None)

    def contract(self, **budget):
        c = dict(schema=1, id="fix-add-cluster", kind="diagnosis", goal="make the cluster sum check pass",
                 repo=self.repo, editable=["calc.py"], protected=["adapter.py", "deployment/*"],
                 gates=[dict(name="cluster", acceptance=True, timeout=60,
                             tracked=dict(adapter="adapter.py", snapshot_root="/remote/snapshots", host="h1",
                                          work_root="/remote/runs", parameters={"case": "tt"},
                                          state_dir=os.path.join(self.root, "tasks"), poll_seconds=0)),
                        dict(name="other", run=[sys.executable, "check_other.py"])],
                 budget=dict(dict(rounds=3, usd=6.0, minutes=30, per_round_usd=2.0, licensed_jobs=3), **budget))
        path = os.path.join(self.root, "contract.json")
        with open(path, "w") as fh:
            json.dump(c, fh)
        return load_contract(path)

    def starts(self):
        return sorted(f for f in os.listdir(self.tracker) if f.endswith(".json"))

    def test_tracked_acceptance_gate_drives_a_diagnosis_to_review(self):
        out = self.execute([({"calc.py": CALC_FIX}, proposal(), 0.5)])
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual(2, out["licensed_jobs"])          # the baseline run and the checking run
        self.assertEqual(2, len(self.starts()))
        self.assertIn("DIAGNOSIS", self.prompts[0])
        self.assertIn("Failed check: `sum/tt`", self.prompts[0])   # the baseline failure report is the input

    def test_licensed_job_budget_stops_before_another_run(self):
        out = self.execute([({"calc.py": CALC_FIX}, proposal(), 0.5)], licensed_jobs=1)
        self.assertEqual("budget", out["stage"])
        self.assertIn("licensed-job", out["reason"])
        self.assertEqual(1, len(self.starts()))
        report = json.load(open(out["failure_report"].replace(".md", ".json")))
        self.assertEqual("gate-fail", report["cause"]["derived"]["cause"])

    def test_one_discriminating_experiment_then_a_patch(self):
        experiment = proposal("experiment", experiment_gate="cluster", experiment_parameters='{"case": "ff"}')
        out = self.execute([({}, experiment, 0.5), ({"calc.py": CALC_FIX}, proposal(), 0.5)])
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual(3, out["licensed_jobs"])
        params = [json.load(open(os.path.join(self.tracker, f)))["parameters"] for f in self.starts()]
        self.assertIn({"case": "ff"}, params)
        self.assertIn("EXPERIMENT RESULTS", self.prompts[1])

    def test_declared_results_come_back_to_the_next_round(self):
        asked = []

        def fetcher(host, mode, workspace, paths):
            asked.append((host, workspace, paths))
            return {"native.json": '{"sum": -1, "expected": 5}', "extra/fit.json": None}
        c = self.contract()
        c["gates"][0]["tracked"]["fetch"] = ["native.json", "extra/fit.json"]
        out = Run(c, self.state, harness=self.harness([({"calc.py": CALC_FIX}, proposal(), 0.5)]),
                  fetcher=fetcher).execute()
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual("h1", asked[0][0])
        self.assertTrue(asked[0][1].startswith("/remote/runs/worker-fix-add-cluster-"))
        self.assertIn('native.json (', self.prompts[0])
        self.assertIn('{"expected":5,"sum":-1}', self.prompts[0])
        self.assertIn("extra/fit.json: absent", self.prompts[0])
        kept = [os.path.join(d, "native.json") for d, _, files in os.walk(self.state) if "native.json" in files]
        self.assertEqual(2, len(kept))     # both runs are kept for the reviewer; only a fail goes in a prompt

    def test_a_fetch_is_a_result_file_never_a_log(self):
        from worker.controller import Refusal
        c = self.contract()
        path = os.path.join(self.root, "contract.json")
        for bad in (["stdout.log"], ["../native.json"], ["/abs/native.json"]):
            data = json.load(open(path))
            data["gates"][0]["tracked"]["fetch"] = bad
            with open(path, "w") as fh:
                json.dump(data, fh)
            with self.assertRaisesRegex(Refusal, "fetch"):
                load_contract(path)

    def test_a_contract_must_budget_each_tracked_gate(self):
        from worker.controller import Refusal
        with self.assertRaisesRegex(Refusal, "licensed_jobs"):
            self.contract(licensed_jobs=0)

    def test_condition_b_with_tracked_gates_needs_claude(self):
        from worker.controller import Refusal
        c = self.contract()
        c["harness"]["name"] = "codex"
        with self.assertRaisesRegex(Refusal, "needs Claude"):
            Run(c, self.state, condition="B")

    def frozen(self):
        report = os.path.join(self.root, "baseline-failure.md")
        with open(report, "w") as fh:
            fh.write("# Failure report\n\nFailed check: `sum/tt` (recorded: add(2, 3) gave -1)\n")
        results = os.path.join(self.root, "baseline-results")
        os.mkdir(results)
        with open(os.path.join(results, "native.json"), "w") as fh:
            fh.write('{"sum": -1}')
        path = os.path.join(self.root, "contract.json")
        data = json.load(open(path))
        data["gates"][0]["tracked"].update(fetch=["native.json"],
                                           frozen_baseline=dict(report=report, results=results))
        with open(path, "w") as fh:
            json.dump(data, fh)
        return load_contract(path)

    def test_a_frozen_baseline_spends_no_licence_and_is_the_input(self):
        self.contract()
        c = self.frozen()
        out = Run(c, self.state, harness=self.harness([({"calc.py": CALC_FIX}, proposal(), 0.5)]),
                  fetcher=lambda *a: {"native.json": '{"sum": 5}'}).execute()
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual(1, out["licensed_jobs"])          # the checking run only
        self.assertEqual(1, len(self.starts()))
        self.assertIn("recorded: add(2, 3) gave -1", self.prompts[0])
        self.assertIn('{"sum":-1}', self.prompts[0])

    def test_condition_b_checks_through_the_tracker_and_is_judged_by_one_more_run(self):
        self.contract()
        c = self.frozen()
        seen = {}

        def plain(name, prompt, cwd, budget, timeout, record_dir, model=None, shell=()):
            seen.update(prompt=prompt, shell=list(shell))
            with open(os.path.join(cwd, "calc.py"), "w") as fh:
                fh.write(CALC_FIX)
            run_dir = os.path.dirname(cwd)                 # B's own launch, as the tracker would record it
            os.makedirs(os.path.join(run_dir, "b-tasks", "request-1"))
            with open(os.path.join(run_dir, "b-tasks", "request-1", "task.json"), "w") as fh:
                json.dump(dict(reference=dict(job_id="job-b1")), fh)
            return dict(ok=True, text="STATUS: done", cost_usd=0.6, turns=9, tokens=None, seconds=3.0, error=None,
                        raw="")
        out = Run(c, self.state, condition="B", plain=plain, fetcher=lambda *a: {"native.json": "{}"}).execute()
        self.assertEqual("ready-for-review", out["status"])
        with open(os.path.join(self.state, out["run"], "outcome.json")) as fh:
            outcome = json.load(fh)
        self.assertEqual((1, 2), (outcome["b_launches"], outcome["licensed_jobs"]))   # its own + the judging run
        self.assertEqual(1, len(self.starts()))            # the controller started only the judging run
        self.assertIn("deployment.bnl.jobs.workflow start", seen["prompt"])
        self.assertIn("recorded: add(2, 3) gave -1", seen["prompt"])   # the same input as C's first round
        self.assertIn('{"sum":-1}', seen["prompt"])
        self.assertIn("py -3 -m deployment.bnl.jobs.workflow:*", seen["shell"])
        self.assertIn("py -3 adapter.py:*", seen["shell"])


class ConditionB(Fixture):
    """Condition B of the comparison: one plain session, judged afterwards by the same gates."""

    def session(self, files, text, cost=0.4):
        self.calls = []

        def plain(name, prompt, cwd, budget, timeout, record_dir, model=None, shell=()):
            self.calls.append(dict(prompt=prompt, budget=budget, timeout=timeout, shell=list(shell)))
            for rel, body in files.items():
                with open(os.path.join(cwd, rel), "w") as fh:
                    fh.write(body)
            return dict(ok=True, text=text, cost_usd=cost, turns=5, tokens=None, seconds=2.0, error=None, raw="")
        out = Run(self.contract(), self.state, condition="B", plain=plain).execute()
        with open(os.path.join(self.state, out["run"], "outcome.json")) as fh:
            return out, json.load(fh)

    def test_a_fix_with_a_done_claim_is_accepted(self):
        out, outcome = self.session({"calc.py": CALC_FIX}, "Fixed add().\nSTATUS: done")
        self.assertEqual("ready-for-review", out["status"])
        self.assertEqual(("B", True, "done"), (outcome["condition"], outcome["accepted"], outcome["claimed"]))
        self.assertEqual(["calc.py"], outcome["files"])
        self.assertEqual(1, outcome["rounds"])
        call = self.calls[0]
        self.assertEqual((6.0, 30 * 60), (call["budget"], call["timeout"]))   # the whole budget, in one session
        self.assertIn("check_accept.py", call["prompt"])                    # the gate commands are the briefing
        self.assertIn("STATUS: done", call["prompt"])
        self.assertTrue(call["shell"])

    def test_editing_the_oracle_is_undone_before_judging_and_the_claim_is_false(self):
        out, outcome = self.session({"check_accept.py": "print('accept ok')\n"}, "All checks pass.\n`STATUS: done`")
        self.assertEqual("not-accepted", out["status"])
        self.assertEqual(["check_accept.py"], outcome["protected_touched"])
        self.assertEqual(("done", False), (outcome["claimed"], outcome["accepted"]))   # a false acceptance
        self.assertEqual(ACCEPT, open(os.path.join(out["worktree"], "check_accept.py")).read())

    def test_scope_is_measured_not_enforced(self):
        out, outcome = self.session({"calc.py": CALC_FIX, "notes.txt": "changed\n"}, "STATUS: done")
        self.assertTrue(outcome["accepted"])
        self.assertEqual(["notes.txt"], outcome["outside_editable"])

    def test_a_blocked_answer_is_recorded(self):
        out, outcome = self.session({}, "I could not find calc.\nSTATUS: blocked: where is add()?")
        self.assertEqual(("not-accepted", "blocked"), (out["status"], outcome["claimed"]))

    def test_c_writes_the_same_outcome_record(self):
        out = self.execute([({"calc.py": CALC_FIX}, proposal(), 0.5)])
        with open(os.path.join(self.state, out["run"], "outcome.json")) as fh:
            outcome = json.load(fh)
        self.assertEqual(("C", True, 0.5), (outcome["condition"], outcome["accepted"], outcome["cost_usd"]))


class Replay(Fixture):
    """A past fix re-done blind: the workspace holds the fix's parent plus its tests, never the fix."""

    def setUp(self):
        super().setUp()
        os.remove(os.path.join(self.repo, "check_accept.py"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "before the fix")
        for name, text in (("calc.py", CALC_FIX), ("check_accept.py", ACCEPT)):
            with open(os.path.join(self.repo, name), "w") as fh:
                fh.write(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "THE FIX: add adds")
        self.fix = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                                  universal_newlines=True).stdout.strip()

    def replay(self, script, **options):
        c = self.contract()
        c["replay"] = dict(dict(fix=self.fix, oracle_files=["check_accept.py"]), **options)
        path = os.path.join(self.root, "contract.json")
        with open(path, "w") as fh:
            json.dump(c, fh)
        return Run(load_contract(path), self.state, harness=self.harness(script)).execute()

    def test_the_workspace_holds_the_parent_and_the_tests_but_not_the_fix(self):
        out =self.replay([({"calc.py": CALC_FIX}, proposal(), 0.5)])
        self.assertEqual("ready-for-review", out["status"])
        wt = out["worktree"]
        history = subprocess.run(["git", "-C", wt, "log", "--all", "--format=%H %s"], stdout=subprocess.PIPE,
                                 universal_newlines=True).stdout
        self.assertNotIn(self.fix, history)
        self.assertNotIn("THE FIX", history)
        self.assertIn("replay oracle", history)
        self.assertEqual("", subprocess.run(["git", "-C", wt, "remote"], stdout=subprocess.PIPE,
                                            universal_newlines=True).stdout.strip())
        self.assertNotIn(self.repo.replace("\\", "/"), self.prompts[0].replace("\\", "/"))
        self.assertIn("fresh clone", self.prompts[0])
        real = open(out["replay"]["fix_patch"]).read()
        self.assertIn("return a + b", real)
        self.assertNotIn("check_accept", real)
        self.assertEqual([], out["replay"]["outside_paths"])
        self.assertIn("fix.patch", open(os.path.join(os.path.dirname(out["patch"]), "review.md")).read())

    def test_shallow_hides_the_history_before_the_parent(self):
        # An injected fault is the parent commit; its own diff must not be one `git log -p` away.
        out = self.replay([({"calc.py": CALC_FIX}, proposal(), 0.5)], shallow=True)
        self.assertEqual("ready-for-review", out["status"])
        history = subprocess.run(["git", "-C", out["worktree"], "log", "--all", "--format=%s"],
                                 stdout=subprocess.PIPE, universal_newlines=True).stdout.split("\n")
        self.assertEqual(4, len(history))       # the worker's commit, the oracle, the base, and nothing older
        self.assertEqual(["replay oracle: the acceptance tests", "replay base", ""], history[1:])
        parent = subprocess.run(["git", "-C", self.repo, "rev-parse", self.fix + "^"], stdout=subprocess.PIPE,
                                universal_newlines=True).stdout.strip()
        objects = subprocess.run(["git", "-C", out["worktree"], "cat-file", "--batch-check", "--batch-all-objects"],
                                 stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertNotIn(parent, objects)       # not even the parent commit object: no message to read

    def test_the_audit_reads_what_was_asked_not_what_came_back(self):
        from worker.controller import Run as R
        run = R.__new__(R)
        run.dir, run.c = os.path.join(self.root, "state", "run-x"), dict(repo=self.repo)
        outside = os.path.join(self.repo, "calc.py").replace("\\", "/")
        raw = os.path.join(self.root, "t.jsonl")
        events = [dict(type="assistant", message=dict(content=[dict(type="tool_use", name="Read",
                                                                    input=dict(file_path=run.dir + "/worktree/a.py"))])),
                  dict(type="user", message=dict(content=[dict(type="tool_result", content="see " + outside)]))]
        with open(raw, "w") as fh:
            fh.write("\n".join(json.dumps(e) for e in events) + "\n")
        self.assertEqual([], run.audit(raw))               # quoted in a result, never visited
        events[0]["message"]["content"][0]["input"]["file_path"] = outside
        with open(raw, "w") as fh:
            fh.write("\n".join(json.dumps(e) for e in events) + "\n")
        self.assertEqual(1, len(run.audit(raw)))

    def test_the_oracle_is_protected(self):
        out = self.replay([({"check_accept.py": "print('accept ok')\n"}, proposal(), 0.5)])
        self.assertEqual(("stopped", "scope"), (out["status"], out["stage"]))

    def test_a_look_at_the_source_checkout_is_recorded(self):
        self.commands.append("cat " + os.path.join(self.repo, "calc.py"))
        self.commands.append("cat ../../../repo/calc.py")
        out = self.replay([({"calc.py": CALC_FIX}, proposal(), 0.5)])
        seen = out["replay"]["outside_paths"]
        self.assertTrue(any(p.endswith("/repo/calc.py") and not p.startswith("..") for p in seen), seen)
        self.assertIn("../../../repo/calc.py", seen)


if __name__ == "__main__":
    unittest.main()
