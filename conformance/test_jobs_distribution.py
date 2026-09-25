#!/usr/bin/env python3
"""Offline checks for the canonical jobs source and its vendoring seam."""
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "flowkit_sync_for_jobs_test", os.path.join(ROOT, "sync.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


#: How many `jobs/` files sync.py vendors. PINNED on purpose, so adding or
#: dropping one is a decision this test makes someone record -- but pinned in
#: ONE place: f3420a0 added jobs/failure.py and the three literals this
#: replaced (26, 26, and 24 = 26 - 2) all went stale together.
EXPECTED_JOBS_FILES = 27


class JobsDistribution(unittest.TestCase):
    def setUp(self):
        self.pairs = [p for p in sync.FILES if p[0].startswith("jobs/")]

    def test_upstream_mapping_is_complete_and_unique(self):
        self.assertEqual(EXPECTED_JOBS_FILES, len(self.pairs))
        self.assertEqual(EXPECTED_JOBS_FILES,
                         len(set(dst for _, dst in self.pairs)))
        for src, dst in self.pairs:
            self.assertEqual("deployment/bnl/" + src, dst)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, src)), src)
        self.assertNotIn(("jobs/README.md", "deployment/bnl/jobs/README.md"),
                         self.pairs)  # Consumer documentation stays local.

    def test_cluster_bundle_is_lf(self):
        for src, _ in self.pairs:
            if src.startswith("jobs/bin/"):
                with open(os.path.join(ROOT, src), "rb") as fh:
                    data = fh.read()
                self.assertNotIn(b"\r", data, src)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), src)

    def test_vendor_roundtrip_detects_drift_and_missing(self):
        # Never vendor into configured real consumers during a test.
        with tempfile.TemporaryDirectory(prefix="flowkit-jobs-") as dest:
            with mock.patch.object(sync, "FILES", self.pairs):
                with mock.patch("builtins.print"):
                    self.assertEqual(EXPECTED_JOBS_FILES, sync.vendor(dest))
                    self.assertEqual(0, sync.vendor(dest))
                self.assertTrue(all(s == "ok" for _, s in sync.check(dest)))
                changed = self.pairs[0][1]
                missing = self.pairs[1][1]
                with open(os.path.join(dest, changed), "ab") as fh:
                    fh.write(b"\n# deliberate drift\n")
                os.remove(os.path.join(dest, missing))
                states = dict(sync.check(dest))
                self.assertEqual("DRIFTED", states[changed])
                self.assertEqual("MISSING", states[missing])
                self.assertEqual(EXPECTED_JOBS_FILES - 2,  # drifted, missing
                                 sum(s == "ok" for s in states.values()))


if __name__ == "__main__":
    unittest.main()
