"""The Stop hook closes the one-shot trap: a session may not end holding a tracked job it started
and never collected (B versus C, 2026-09-26: 4 of 6 B runs on 1-3 minute jobs ended on a
background timer with their own run unread).

Run: python -m unittest integrations.cluster_jobs.test_stop -v
"""
import json
from pathlib import Path
import unittest

from .hook import STOP_BLOCKS_MAX, handle
from .render import render
from . import test_hooks

START = "python3 -m jobs.workflow start --profile p --task-key k-1"
COLLECT = "python3 -m jobs.workflow collect --profile p --task-key k-1 --wait 540"


class Stop(unittest.TestCase):
    # test_hooks' config and event fixtures, without re-running its tests.
    setUp = test_hooks.HookTests.setUp
    event = test_hooks.HookTests.event
    envelope = test_hooks.HookTests.envelope

    def capture(self, command, **fields):
        e = self.envelope()
        e.update(task_key="k-1", **fields)
        handle(self.event(command, kind="PostToolUse", tool_response={"stdout": json.dumps(e)}), self.cfg)

    def stop(self, said="All done."):
        return handle(self.event(kind="Stop", stop_hook_active=False, last_assistant_message=said), self.cfg)

    def test_an_uncollected_job_blocks_the_end_and_says_how_to_wait(self):
        self.capture(START)
        out = self.stop("The timer is running; I'll collect again when it finishes.")
        self.assertEqual("block", out["decision"])
        self.assertIn("k-1", out["reason"])
        self.assertIn("--wait 540", out["reason"])

    def test_a_collected_job_lets_the_session_end(self):
        self.capture(START)
        self.capture(COLLECT, observation="done", next_action="report-result", engineering="pass")
        self.assertEqual({}, self.stop())

    def test_a_status_read_after_collect_does_not_reopen_it(self):
        self.capture(START)
        self.capture(COLLECT, observation="done", next_action="report-result")
        self.capture("python3 -m jobs.workflow status --profile p --task-key k-1", observation="done",
                     next_action="collect")
        self.assertEqual({}, self.stop())

    def test_a_terminal_status_is_not_a_collect(self):
        self.capture(START)
        self.capture("python3 -m jobs.workflow status --profile p --task-key k-1", observation="done",
                     next_action="collect")
        self.assertEqual("block", self.stop()["decision"])

    def test_a_named_handoff_lets_a_long_job_outlive_the_session(self):
        self.capture(START)
        said = ("The ADC run takes about two hours. Task key k-1 is pending; collect it with "
                "`python3 -m jobs.workflow collect --profile p --state-dir S --task-key k-1`.")
        self.assertEqual({}, self.stop(said))

    def test_the_hook_lets_go_after_a_bounded_number_of_refusals(self):
        self.capture(START)
        for _ in range(STOP_BLOCKS_MAX):
            self.assertEqual("block", self.stop()["decision"])
        self.assertEqual({}, self.stop())

    def test_the_last_message_is_read_from_the_transcript_when_the_event_lacks_it(self):
        self.capture(START)
        transcript = Path(self.tmp.name) / "t.jsonl"
        transcript.write_text("\n".join(json.dumps(x) for x in (
            dict(type="user", message=dict(content="go")),
            dict(type="assistant", message=dict(content=[dict(type="text", text="k-1 is pending; collect later")])))))
        event = self.event(kind="Stop", transcript_path=str(transcript))
        self.assertEqual({}, handle(event, self.cfg))

    def test_another_session_and_old_receipts_are_not_held(self):
        self.capture(START)
        self.assertEqual({}, handle(self.event(kind="Stop", session_id="other", last_assistant_message=""), self.cfg))
        receipt = next(Path(self.tmp.name).rglob("task-*.json"))
        data = json.loads(receipt.read_text())
        data.pop("settled")                                   # written before this rule
        receipt.write_text(json.dumps(data))
        self.assertEqual({}, self.stop())

    def test_out_of_scope_sessions_are_untouched(self):
        self.capture(START)
        self.assertEqual({}, handle(self.event(kind="Stop", cwd="/elsewhere", last_assistant_message=""), self.cfg))

    def test_claude_settings_render_the_stop_hook_and_codex_does_not(self):
        claude = render("claude-windows", "py -3 hook.py")
        self.assertEqual("py -3 hook.py", claude["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertNotIn("matcher", claude["hooks"]["Stop"][0])
        self.assertNotIn("Stop", render("codex", "py -3 hook.py")["hooks"])


if __name__ == "__main__":
    unittest.main()
