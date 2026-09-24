"""Acceptance test for `--parameters-file` (worker pilot 2, 2026-09-24).

PowerShell mangles quoted JSON on the command line (`'{"case":"tt"}'` arrives
as `{case:tt}` or with `\"`), and sessions lost a turn to it. The decision:
`start` also takes `--parameters-file PATH`, a JSON object in a file (a UTF-8
BOM, which PowerShell 5 writes, is accepted). Giving both `--parameters` and
`--parameters-file` is refused, the read-only operations refuse it like any
request change, and a bad file is a refusal that names `--parameters-file`.
None of these cases reaches the cluster.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .test_workflow import profile


class ParametersFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="flowkit-params-file-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "flow").mkdir(parents=True)
        (self.repo / "flow/run.py").write_text("print('run')")
        git = ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)
        head = subprocess.run(git + ["rev-parse", "HEAD"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
        self.profile = self.root / "profile.json"
        self.profile.write_text(json.dumps(profile()))
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps(dict(
            source=dict(root=str(self.repo), head=head, patch_sha256="0" * 64, untracked_sha256="0" * 64),
            files={}, external={}, packaged={"flow/run.py": hashlib.sha256(b"print('run')").hexdigest()})))

    def cli(self, *args):
        base = [sys.executable, "-B", "-m", "jobs.workflow"]
        out = subprocess.run(base + list(args) + ["--profile", str(self.profile), "--state-dir",
                                                    str(self.root / "state")],
                             stdout=subprocess.PIPE, universal_newlines=True, timeout=60)
        return out.returncode, json.loads(out.stdout)

    def start(self, *extra):
        return self.cli("start", "--task-key", "k1", "--repo", str(self.repo), "--manifest", str(self.manifest), *extra)

    def write(self, name, data, bom=False):
        path = self.root / name
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8"))
        return str(path)

    def test_a_file_is_read_as_the_parameters(self):
        # Parameters that do not match the profile are refused before any dispatch,
        # which shows the file was read and parsed as the request.
        for bom in (False, True):
            with self.subTest(bom=bom):
                rc, out = self.start("--parameters-file", self.write("p.json", json.dumps({"message": "x"}), bom))
                self.assertEqual(2, rc, out)
                self.assertIn("parameters must match profile", out["reason"])

    def test_a_bad_file_is_a_named_refusal(self):
        for name, text in (("bad.json", "{message: x}"), ("list.json", "[1, 2]")):
            with self.subTest(name=name):
                rc, out = self.start("--parameters-file", self.write(name, text))
                self.assertEqual(2, rc)
                self.assertIn("--parameters-file", out["reason"])
        rc, out = self.start("--parameters-file", str(self.root / "missing.json"))
        self.assertEqual(2, rc)
        self.assertIn("--parameters-file", out["reason"])

    def test_both_forms_together_are_refused(self):
        rc, out = self.start("--parameters", "{}", "--parameters-file", self.write("p.json", "{}"))
        self.assertEqual(2, rc)
        self.assertIn("--parameters-file", out["reason"])

    def test_reads_refuse_a_parameters_file(self):
        rc, out = self.cli("status", "--task-key", "k1", "--parameters-file", self.write("p.json", "{}"))
        self.assertEqual(2, rc)
        self.assertEqual("refused", out["observation"])


if __name__ == "__main__":
    unittest.main()
