import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .package import build


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flowkit-rollout-")
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "candidate"

    def test_package_hashes_imports_and_no_activation(self):
        package = build(self.target)
        for item in package["files"]:
            self.assertEqual(item["sha256"], hashlib.sha256((self.target / item["path"]).read_bytes()).hexdigest())
        self.assertFalse((self.target / ".codex").exists())
        self.assertFalse((self.target / ".claude").exists())
        from jobs.workflow import validate_profile
        validate_profile(json.loads((self.target / "templates/foreground_profile.json").read_text()))
        matrix = json.loads((self.target / "templates/activation_matrix.json").read_text())
        self.assertEqual("not-run", matrix["status"])
        self.assertEqual(10, len(matrix["attempts"]))
        command = [sys.executable, "-B", "-m", "deployment.bnl.jobs.workflow", "--help"]
        result = subprocess.run(command, cwd=str(self.target), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--task-key", result.stdout)
        with self.assertRaises(FileExistsError):
            build(self.target)

    @unittest.skipUnless(os.name == "posix", "requires local POSIX tracker")
    def test_packaged_lifecycle_compatibility(self):
        build(self.target)
        result = subprocess.run([sys.executable, "-B", "-m", "deployment.bnl.jobs.smoke"],
                                cwd=str(self.target), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=90)
        self.assertEqual(0, result.returncode, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(4, summary["jobs"])
        self.assertEqual(4, summary["terminal_events"])
        cases = {c["case"]: c for c in summary["cases"]}
        self.assertEqual("pass", cases["attached"]["engineering"])
        self.assertEqual("pass", cases["campaign"]["engineering"])
        self.assertEqual("failed", cases["disabled"]["observation"])
        self.assertEqual("failed", cases["missing"]["observation"])
        self.assertEqual("refused-before-launch", cases["nested-detach"]["observation"])


if __name__ == "__main__":
    unittest.main()
