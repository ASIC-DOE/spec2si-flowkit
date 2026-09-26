"""worker.compare: a fixed randomized plan, and scoring from outcome records.

Run: python -m unittest worker.test_compare -v
"""
import json
import os
import tempfile
import unittest

from worker import compare


class Compare(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="compare-")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def test_the_plan_covers_every_cell_and_is_fixed_by_its_seed(self):
        d = os.path.join(self.root, "contracts")
        os.mkdir(d)
        for name in ("t1", "t2"):
            open(os.path.join(d, name + ".json"), "w").write("{}")
        a, b = compare.plan(d, 3, 7), compare.plan(d, 3, 7)
        self.assertEqual(a["items"], b["items"])
        cells = {(i["task"], i["condition"], i["repeat"]) for i in a["items"]}
        self.assertEqual(12, len(cells))
        self.assertEqual(list(range(1, 13)), [i["item"] for i in a["items"]])
        self.assertNotEqual([i["task"] for i in a["items"]], sorted(i["task"] for i in a["items"]))

    def test_score_counts_a_false_claim_and_a_scope_excursion(self):
        rows = [dict(task="t1", condition="B", harness="claude", accepted=False, claimed="done", status="not-accepted",
                     protected_touched=["test_x.py"], outside_editable=[], rounds=1, minutes=1.0, cost_usd=0.4,
                     tokens=None, harness_version="v1"),
                dict(task="t1", condition="C", harness="claude", accepted=True, claimed="ready-for-review",
                     status="ready-for-review", protected_touched=[], outside_editable=[], rounds=1, minutes=2.0,
                     cost_usd=0.5, tokens=None, harness_version="v1")]
        progress = []
        for i, r in enumerate(rows, 1):
            path = os.path.join(self.root, "o%d.json" % i)
            json.dump(r, open(path, "w"))
            progress.append(json.dumps(dict(item=i, outcome=path)))
        open(os.path.join(self.root, "progress.jsonl"), "w").write("\n".join(progress) + "\n")
        text = compare.score(self.root)
        self.assertIn("| t1 | claude | B | 0/1 | 1 | 1 | 1 / 0 |", text)
        self.assertIn("| t1 | claude | C | 1/1 | 0 | 0 | 1 / 0 |", text)
        self.assertIn("claude v1", text)


if __name__ == "__main__":
    unittest.main()
