"""baseline.py on a synthetic transcript: exclusions, scripts from before the window, call durations,
and the migrated flows counted by the repo's own hook.

Run: python -m pytest integrations/cluster_jobs/acceptance/test_baseline.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline  # noqa: E402  (acceptance/ is a scripts directory, not a package)

FAKE_HOOK = """import json, sys
e = json.load(sys.stdin)
cmd = e["tool_input"]["command"]
deny = "run_schematic.sh" in cmd and "jobs.workflow" not in cmd
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}} if deny else {}))
"""


def line(sid, ts, kind, **content):
    return json.dumps(dict(sessionId=sid, timestamp=ts, type=kind, entrypoint="claude-desktop",
                           uuid="%s-%s" % (sid, ts), message=dict(content=[content])))


class Baseline(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="baseline-")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        project = os.path.join(self.root, "C--dev-spec2si-sky130")
        os.makedirs(os.path.join(self.root, "repo", "deployment", "bnl"))
        self.hook = os.path.join(self.root, "repo", "deployment", "bnl", "tracker_hook.py")
        open(self.hook, "w").write(FAKE_HOOK)
        os.mkdir(project)
        rows = [
            # the day before the window: a launch script is written, nothing is counted
            line("s1", "2026-10-01T09:00:00Z", "assistant", type="tool_use", id="w1", name="Write",
                 input=dict(file_path="C:/tmp/go.sh", content="spectre run.scs\n")),
            # in the window: the script is fed to ssh, and the call takes 15 minutes
            line("s1", "2026-10-02T10:00:00Z", "assistant", type="tool_use", id="c1", name="Bash",
                 input=dict(command="ssh asic7 bash -s < /c/tmp/go.sh")),
            line("s1", "2026-10-02T10:15:00Z", "user", type="tool_result", tool_use_id="c1", content="ok"),
            # an untracked launch of the migrated flow, refused by the hook in the session
            line("s1", "2026-10-02T11:00:00Z", "assistant", type="tool_use", id="c2", name="Bash",
                 input=dict(command="ssh asic7 sh run_schematic.sh")),
            line("s1", "2026-10-02T11:00:01Z", "user", type="tool_result", tool_use_id="c2", is_error=True,
                 content="Tracked compute required: sky130. Use ..."),
            # one that ran (an escape), and a tracked start
            line("s1", "2026-10-02T12:00:00Z", "assistant", type="tool_use", id="c3", name="Bash",
                 input=dict(command="ssh asic7 sh run_schematic.sh")),
            line("s1", "2026-10-02T12:00:30Z", "user", type="tool_result", tool_use_id="c3", content="done"),
            line("s1", "2026-10-02T13:00:00Z", "assistant", type="tool_use", id="c4", name="Bash",
                 input=dict(command="py -3 -m deployment.bnl.jobs.workflow start --profile p --task-key k")),
            # a development session to leave out
            line("dev", "2026-10-02T10:00:00Z", "assistant", type="tool_use", id="d1", name="Bash",
                 input=dict(command="ssh asic7 spectre x.scs")),
        ]
        open(os.path.join(project, "t.jsonl"), "w").write("\n".join(rows) + "\n")

    def run_it(self, **kw):
        calls, results, sessions = baseline.scan("sky130", "2026-10-02", "2026-10-03", self.root, {"dev"})
        out, _ = baseline.measure(calls, results, sessions)
        return out, calls

    def test_scripts_from_before_the_window_resolve_and_durations_count(self):
        out, _ = self.run_it()
        self.assertEqual(1, out["sessions"])                      # "dev" excluded
        self.assertEqual(0, out["opaque_scripts"])                # go.sh was written the day before
        self.assertGreaterEqual(out["eda_launches"], 1)
        self.assertEqual(1, out["cluster_calls_over_10min"])
        self.assertGreaterEqual(out["cluster_call_hours"], 0.2)

    def test_migrated_flows_are_counted_by_the_repos_own_hook(self):
        _, calls = self.run_it()
        m = baseline.migrated(calls, "sky130", self.hook)
        self.assertEqual(dict(tracked_starts=1, tracked_collects=0, untracked_launch_attempts=2, refused_by_hook=1,
                              untracked_launches_ran=1, tracked_share=0.5), m)


if __name__ == "__main__":
    unittest.main()
