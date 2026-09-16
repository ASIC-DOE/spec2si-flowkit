"""Exercise a complete workflow without replacing another consumer's helpers."""
import unittest
from jobs.smoke import run


class IsolatedBundleTest(unittest.TestCase):
    def test_isolated_lifecycle(self):
        result = run(isolated_bundle=True)
        self.assertEqual(result["jobs"], 4)
        self.assertEqual(result["terminal_events"], 4)


if __name__ == "__main__":
    unittest.main()
