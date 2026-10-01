import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .hook import handle, inspect_command, load_config
from .render import render

HERE = Path(__file__).resolve().parent


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="flowkit-hook-")
        self.addCleanup(self.tmp.cleanup)
        self.cfg = json.loads((HERE / "example_config.json").read_text())
        self.cfg["receipt_dir"] = self.tmp.name

    def event(self, command=None, kind="PreToolUse", tool="Bash", **kw):
        e = dict(cwd="/example/repo", session_id="test-session", hook_event_name=kind,
                 tool_name=tool, tool_input={"command": command})
        e.update(kw)
        return e

    def decision(self, command, **kw):
        return handle(self.event(command, **kw), self.cfg).get("hookSpecificOutput", {}).get("permissionDecision")

    def test_bypass_matrix(self):
        commands = [
            "synthetic-eda input", "env LICENSE=local synthetic-eda input",
            "python3 digital/run.py --top demo", "/bin/bash campaign/run.sh",
            "ssh compute.example.invalid synthetic-eda input",
            "ssh -o BatchMode=yes user@compute.example.invalid 'synthetic-eda input'",
            "wsl.exe -e bash -lc 'synthetic-eda input'",
            '& "C:\\tools\\synthetic-eda.exe" input',
            'powershell.exe -Command "synthetic-eda input"',
            "nohup synthetic-eda input &", "setsid synthetic-eda input",
            "Start-Process synthetic-eda", "Start-Job { synthetic-eda input }",
            "python3 -m jobs.workflow status --profile p; synthetic-eda input",
            "echo jobs.workflow && synthetic-eda input", "pwd\nsynthetic-eda input",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual("deny", self.decision(command))
        reason = handle(self.event("synthetic-eda input"), self.cfg)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("start --profile", reason)
        self.assertIn("--host", reason)

    def test_configured_project_wrappers_are_transparent(self):
        # Unconfigured, an activation wrapper hides the launch (the old behaviour).
        self.assertIsNone(self.decision("asic_tools.csh synthetic-eda input"))
        self.cfg["wrappers"] = ["asic_tools.csh", "remote_task.sh"]
        for command in ("asic_tools.csh synthetic-eda input",
                        "~/tools/asic_tools.csh python3 digital/run.py --top demo",
                        "ssh compute.example.invalid 'cd w && ~/tools/asic_tools.csh synthetic-eda input'",
                        "bash deployment/remote_task.sh job --put a.sh -- synthetic-eda input",
                        "tcsh -c 'synthetic-eda input'", "csh -c 'synthetic-eda input'"):
            with self.subTest(command=command):
                self.assertEqual("deny", self.decision(command))
        for command in ("asic_tools.csh which genus", "bash deployment/remote_task.sh job -- ls -la",
                        "tcsh -c 'echo $PATH'"):
            with self.subTest(command=command):
                self.assertIsNone(self.decision(command))
        self.cfg["wrappers"] = "asic_tools.csh"
        path = Path(self.tmp.name) / "bad.json"
        path.write_text(json.dumps(self.cfg))
        with self.assertRaises(ValueError):
            load_config(str(path))

    def test_compute_subcommand_preserves_preparation_and_status(self):
        self.cfg["routes"][0]["argument_prefixes"] = [["run"], ["launch", "normal"]]
        for command in ("synthetic-eda run", "python3 digital/run.py run --snapshot p",
                        "synthetic-eda launch normal"):
            self.assertEqual("deny", self.decision(command))
        for command in ("synthetic-eda status", "synthetic-eda profile", "synthetic-eda package",
                        "synthetic-eda stage", "synthetic-eda launch control"):
            self.assertIsNone(self.decision(command))

    def test_read_only_and_out_of_scope_matrix(self):
        for command in ("ssh compute.example.invalid ls -la", "ssh compute.example.invalid ps -ef",
                        "ssh elsewhere synthetic-eda input", "cat digital/run.py", "rg synthetic-eda .",
                        "echo synthetic-eda", "git status", "nohup sleep 20 &", "python3 -m jobs.workflow status --profile p",
                        "python3 -m jobs.workflow start --profile p --parameters '{}'"):
            with self.subTest(command=command):
                self.assertIsNone(self.decision(command))
        self.assertIsNone(self.decision("synthetic-eda input", cwd="/example/repo-other"))
        self.assertEqual("deny", self.decision("synthetic-eda input", cwd="C:\\example\\repo\\subdir"))
        self.assertEqual("deny", self.decision("synthetic-eda input", cwd="/mnt/c/example/repo"))

    def test_native_exec_and_nested_call_normalization(self):
        # Codex's documented nested-call interception delivers the shell call,
        # not JavaScript source. Also accept the native exec cmd/workdir shape.
        e = self.event(tool="exec_command", tool_input={"cmd": "synthetic-eda input", "workdir": "/example/repo"})
        self.assertEqual("deny", handle(e, self.cfg)["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual("deny", self.decision("synthetic-eda input", tool="PowerShell"))
        self.assertEqual({}, handle(self.event(tool="functions.exec", tool_input={"code": "hidden()"}), self.cfg))

    def test_opaque_shapes_are_explicitly_advisory(self):
        for command in ("ssh compute.example.invalid sh -s < payload.sh", 'python3 -c "hidden()"', "bash -s"):
            result = handle(self.event(command), self.cfg)["hookSpecificOutput"]
            self.assertNotIn("permissionDecision", result)
            self.assertIn("advisory", result["additionalContext"])

    def envelope(self):
        task = "task-" + "a" * 32
        ref = dict(schema=1, task_id=task, host="compute.example.invalid", job_id="job-1",
                   profile_sha256="b" * 64, repository="synthetic", workspace="/tmp/" + task,
                   request_sha256="c" * 64)
        return dict(schema=1, kind="workflow", task_id=task, host=ref["host"], job_id="job-1",
                    reference=ref, observation="submitted", evidence="unchecked", engineering="unchecked")

    def test_foreign_repository_reference_is_not_captured(self):
        self.cfg["repository"] = "spec2si-own"
        envelope = self.envelope()                       # repository="synthetic"
        result = handle(self.event("python3 -m jobs.workflow start --profile p", kind="PostToolUse",
                                   tool_response={"stdout": json.dumps(envelope)}), self.cfg)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("No workflow reference captured", text)
        self.assertIn("another repository", text)
        self.assertEqual([], list(Path(self.tmp.name).rglob("task-*.json")))
        envelope["reference"]["repository"] = "spec2si-own"
        result = handle(self.event("python3 -m jobs.workflow start --profile p", kind="PostToolUse",
                                   tool_response={"stdout": json.dumps(envelope)}), self.cfg)
        self.assertIn("captured", result["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(1, len(list(Path(self.tmp.name).rglob("task-*.json"))))

    def test_session_start_carries_the_one_shot_rule(self):
        message = handle(self.event(kind="SessionStart", source="startup"), self.cfg)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("do not end on a background poll", message)

    def test_session_start_names_the_host_picker(self):
        message = handle(self.event(kind="SessionStart", source="startup"), self.cfg)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("python3 -m jobs hosts --threads N --pick", message)

    # --- host advice for hand launches (2026-10-01: the asic8 run) ---------

    def survey(self, picked, rows, age=60.0, threads=16):
        import time
        path = Path(self.tmp.name) / "survey.json"
        path.write_text(json.dumps(dict(schema=1, time=time.time() - age, threads=threads,
                                        flow=None, picked=picked, rows=rows)))
        os.environ["SPEC2SI_HOST_SURVEY"] = str(path)
        self.addCleanup(os.environ.pop, "SPEC2SI_HOST_SURVEY", None)

    def script(self, name, text):
        p = Path(self.tmp.name) / name
        p.write_text(text)
        return str(p).replace("\\", "/")

    def advice(self, command):
        out = handle(self.event(command), self.cfg).get("hookSpecificOutput", {})
        self.assertNotIn("permissionDecision", out)          # advice, never a denial
        return out.get("additionalContext", "")

    def test_hand_launch_on_a_slower_host_quotes_the_survey(self):
        self.cfg["hosts"] = ["compute.example.invalid", "fast.example.invalid"]
        self.survey("fast.example.invalid", [
            dict(host="fast.example.invalid", cpu="9950X3D", free=31.6, wallx=1.0),
            dict(host="compute.example.invalid", cpu="W-2155", free=19.9, wallx=1.91)])
        s = self.script("go.sh", "cd w\nsetsid nohup spectre +aps +mt=16 run.scs -format psfascii &\n")
        text = self.advice("ssh compute.example.invalid bash -s < " + s)
        self.assertIn("picks fast", text)
        self.assertIn("1.91x", text)
        self.assertIn("W-2155", text)
        self.assertIn("python3 -m jobs hosts --threads N --pick", text)
        # the same launch on the pick is confirmed, not argued with
        text = self.advice("ssh fast.example.invalid bash -s < " + s)
        self.assertIn("fast is the current pick", text)

    def test_hand_launch_without_a_recent_survey_says_how_to_get_one(self):
        self.survey("fast.example.invalid", [], age=3600.0)
        s = self.script("go.sh", "bash enob_run.sh run1 label\n")
        text = self.advice("ssh compute.example.invalid bash -s < " + s)
        self.assertIn("no host survey", text)
        self.assertIn("hosts --threads N --pick", text)

    def test_no_advice_for_file_checks_unreadable_scripts_or_other_hosts(self):
        self.survey("fast.example.invalid", [])
        reads = self.script("ls.sh", "ls -la run/spectre.out\npython3 strobe_swing.py run/psf\n"
                                     "grep -c spectre run.log\n")
        self.assertNotIn("Host check", self.advice("ssh compute.example.invalid bash -s < " + reads))
        self.assertNotIn("Host check", self.advice("ssh compute.example.invalid bash -s < $UNSET_VAR/x.sh"))
        go = self.script("go.sh", "spectre +aps run.scs\n")
        self.assertNotIn("Host check", self.advice("ssh elsewhere.invalid bash -s < " + go))
        self.assertNotIn("Host check", self.advice("cat " + go))

    def test_inline_compute_is_seen_without_a_script(self):
        self.survey("fast.example.invalid", [], age=7200.0)
        self.assertIn("Host check", self.advice("ssh compute.example.invalid 'cd w && spectre +aps run.scs'"))

    def test_advice_failure_never_blocks_a_command(self):
        from . import hook as H
        orig = H.launch_advice
        H.launch_advice = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            out = handle(self.event("ssh compute.example.invalid bash -s < x.sh"), self.cfg)
            self.assertNotIn("permissionDecision", out.get("hookSpecificOutput", {}))
        finally:
            H.launch_advice = orig

    def test_survey_path_is_the_one_jobs_hosts_writes(self):
        """The hook and jobs/hosts.py each spell the cache path (the hook
        cannot import the jobs package: it lands at a different path in every
        consumer). Held equal here, env override included."""
        import importlib.util
        hosts_py = HERE.parent.parent / "jobs" / "hosts.py"
        if not hosts_py.exists():
            self.skipTest("jobs package not beside this integration")
        spec = importlib.util.spec_from_file_location("flowkit_jobs_hosts", str(hosts_py))
        sys.path.insert(0, str(hosts_py.parent))
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.pop(0)
        from . import hook as H
        self.assertEqual(H.SURVEY_ENV, mod.SURVEY_ENV)
        os.environ.pop("SPEC2SI_HOST_SURVEY", None)
        self.assertEqual(H.survey_cache_path(), mod.survey_cache_path())
        os.environ["SPEC2SI_HOST_SURVEY"] = "/x/y.json"
        try:
            self.assertEqual("/x/y.json", H.survey_cache_path())
            self.assertEqual("/x/y.json", mod.survey_cache_path())
        finally:
            os.environ.pop("SPEC2SI_HOST_SURVEY", None)

    def test_post_capture_resume_and_disclosure(self):
        envelope = self.envelope()
        envelope["raw_log"] = "DO_NOT_STORE"
        for response in ({"stdout": json.dumps(envelope)}, {"output": json.dumps(envelope)},
                         [{"type": "text", "text": json.dumps(envelope)}]):
            result = handle(self.event("python3 -m jobs.workflow start --profile p", kind="PostToolUse",
                                       tool_response=response), self.cfg)
            self.assertIn("captured", result["hookSpecificOutput"]["additionalContext"])
        files = list(Path(self.tmp.name).rglob("task-*.json"))
        self.assertEqual(1, len(files))
        self.assertNotIn("DO_NOT_STORE", files[0].read_text())
        self.assertEqual(envelope["reference"], json.loads(files[0].read_text())["reference"])
        for source in ("startup", "resume", "compact", "clear"):
            message = handle(self.event(kind="SessionStart", source=source), self.cfg)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(str(files[0]).replace("\\", "\\\\"), message)
            self.assertIn("query resume", message)
        other = handle(self.event(kind="SessionStart", session_id="other"), self.cfg)
        self.assertNotIn("task-" + "a" * 32, json.dumps(other))

    def test_no_capture_from_unrelated_command_or_stderr(self):
        e = self.envelope()
        self.assertEqual({}, handle(self.event("cat receipt.json", kind="PostToolUse", tool_response=e), self.cfg))
        msg = handle(self.event("python3 -m jobs.workflow start --profile p", kind="PostToolUse",
                               tool_response={"stderr": json.dumps(e)}), self.cfg)
        self.assertIn("No workflow reference", msg["hookSpecificOutput"]["additionalContext"])
        self.assertEqual([], list(Path(self.tmp.name).rglob("*.json")))

    def test_durable_store_guidance_crosses_session_boundary(self):
        self.cfg["state_dir"] = os.path.join(self.tmp.name, "durable")
        reservation = Path(self.cfg["state_dir"]) / ("request-" + "a" * 64)
        reservation.mkdir(parents=True)
        for session in ("original", "new-session"):
            result = handle(self.event(kind="SessionStart", session_id=session), self.cfg)
            message = result["hookSpecificOutput"]["additionalContext"]
            self.assertIn("tasks --profile", message)
            self.assertIn("stable --task-key", message)
            self.assertIn(json.dumps(self.cfg["state_dir"]), message)
            self.assertIn("request-" + "a" * 64, message)
        denial = handle(self.event("synthetic-eda input"), self.cfg)["hookSpecificOutput"]
        self.assertIn("--task-key", denial["permissionDecisionReason"])

    def test_settings_merge_preserves_existing_guards(self):
        existing = {"permissions": {"deny": ["example"]}, "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "existing-ssh-guard"}]}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": "harvest"}]}]}}
        before = copy.deepcopy(existing)
        for harness in ("codex", "claude", "claude-windows"):
            settings = render(harness, "python /private/hook.py --config /private/config.json", existing)
            self.assertEqual(before, existing)
            self.assertEqual(existing["hooks"]["PreToolUse"][0], settings["hooks"]["PreToolUse"][0])
            self.assertEqual(existing["hooks"]["SessionEnd"], settings["hooks"]["SessionEnd"])
            self.assertEqual(settings, render(harness, "python /private/hook.py --config /private/config.json", settings))
        self.assertNotIn('"permissionDecision": "allow"', json.dumps(handle(self.event("pwd"), self.cfg)))

    def test_command_hook_canary_and_malformed_input(self):
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps(self.cfg))
        def invoke(value):
            return subprocess.run([sys.executable, str(HERE / "hook.py"), "--config", str(config)],
                                  input=value, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True, timeout=5)
        denied = invoke(json.dumps(self.event("synthetic-eda input")))
        self.assertEqual(0, denied.returncode)
        self.assertEqual("deny", json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual({}, json.loads(invoke(json.dumps(self.event("pwd"))).stdout))
        broken = invoke("not JSON")
        self.assertEqual(2, broken.returncode)
        self.assertIn("could not validate", broken.stderr)
        self.assertEqual(self.cfg, load_config(str(config)))

    @unittest.skipUnless(os.name == "posix", "requires actual local POSIX tracker")
    def test_actual_job_receipt_recovery(self):
        from jobs.test_workflow import ExampleJobs
        fixture = ExampleJobs()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        workflow, ref, result, _ = fixture.finish()
        handle(self.event("python3 -m jobs.workflow collect --profile p", kind="PostToolUse",
                          tool_response={"stdout": json.dumps(result)}), self.cfg)
        paths = list(Path(self.tmp.name).rglob("task-*.json"))
        self.assertEqual(1, len(paths))
        cached = json.loads(paths[0].read_text())["reference"]
        self.assertEqual(ref, cached)
        self.assertEqual("done", workflow.observe(cached)["observation"])
        self.assertEqual(1, fixture.launches)


if __name__ == "__main__":
    unittest.main()
