#!/usr/bin/env python3
"""A gate on the TESTS THEMSELVES: every test must be able to fail.

The defect, found 2026-08-01 in this very package: `check(cond, msg)` counted
a failure, printed it, and returned. Run as `python3 test_cli.py` that gives an
honest tally -- but the project runs pytest, where a test function returning
None has PASSED. Six files and **66 tests** were green by construction, and a
deliberately reverted bug in `cli.py` was caught by neither the suite nor its
own negative control. A gate that cannot fail is worse than no gate: it is a
green light that means nothing, and it had been on for months.

This walks the repo and refuses a `test_*` function from which no
AssertionError can escape -- directly, or through a helper defined in the same
module. It is deliberately CONSERVATIVE: anything it cannot see through (an
imported helper, a lambda, a call it does not recognise) counts as guarded, so
it under-reports rather than crying wolf. That is the right bias for a gate
whose failure mode would otherwise be "everyone learns to ignore it".

It has no allowlist. When `test_browse.py`'s escape check turned up -- it
asserts by `compile()`ing the server with SyntaxWarning promoted to an error --
the answer was to teach the scanner that `compile` raises, not to name the
test and move on. An exception list is how a gate stops meaning anything.

  python3 test_harness_can_fail.py     # or: pytest test_harness_can_fail.py
"""
import ast
import os
import sys

#: Calls that ARE the assertion. `compile` is here because promoting a warning
#: to an error and compiling is a real check; the others are the usual
#: explicit-failure spellings.
RAISING_CALLS = frozenset(("fail", "skip", "xfail", "exit", "compile"))

SKIP_DIRS = frozenset((".git", "node_modules", "worktrees", "__pycache__",
                       ".venv", ".tox", "build", "dist"))


def repo_root():
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.abspath(__file__))


def _can_raise(node):
    for n in ast.walk(node):
        if isinstance(n, (ast.Assert, ast.Raise)):
            return True
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if nm in RAISING_CALLS:
                return True
    return False


def _local_calls(node, known):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "id", None)
            if nm in known:
                out.add(nm)
    return out


def unguarded(path):
    """test_* functions in `path` from which no failure can escape."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        tree = ast.parse(fh.read(), path)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    memo = {}

    def raises(name, seen, depth=0):
        if name in memo:
            return memo[name]
        fn = funcs.get(name)
        if fn is None or depth > 6 or name in seen:
            return True                      # cannot see it -> assume guarded
        ok = _can_raise(fn) or any(
            raises(c, seen | {name}, depth + 1)
            for c in _local_calls(fn, funcs) if c != name)
        memo[name] = ok
        return ok

    return sorted(n for n in funcs
                  if n.startswith("test_") and not raises(n, frozenset()))


def scan_repo(root=None):
    root = root or repo_root()
    bad = {}
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in files:
            if not (fn.startswith("test_") and fn.endswith(".py")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                got = unguarded(p)
            except SyntaxError:
                continue                     # not our gate to enforce
            if got:
                bad[os.path.relpath(p, root).replace("\\", "/")] = got
    return bad


def test_every_test_in_this_repo_can_fail():
    bad = scan_repo()
    assert not bad, (
        "these test functions cannot fail under pytest -- a helper reports a "
        "failure without raising, so the function returns None and pytest "
        "calls it passed:\n" +
        "\n".join("  %s: %s" % (f, ", ".join(t[:6])) for f, t in
                  sorted(bad.items())))


def test_the_gate_itself_catches_the_shape_it_was_written_for():
    """The negative control. Written as the real defect looked -- a counting
    `check` and a test that only calls it -- so the gate is proven against the
    thing it exists to stop rather than against a strawman."""
    import tempfile
    d = tempfile.mkdtemp(prefix="harness_gate_")
    p = os.path.join(d, "test_sample.py")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("_FAIL = 0\n\n\n"
                 "def check(cond, msg):\n"
                 "    global _FAIL\n"
                 "    if not cond:\n"
                 "        _FAIL += 1\n"
                 "        print('FAIL:', msg)\n\n\n"
                 "def test_cannot_fail():\n"
                 "    check(1 == 2, 'never seen')\n\n\n"
                 "def test_can_fail():\n"
                 "    assert 1 == 1\n")
    got = unguarded(p)
    assert got == ["test_cannot_fail"], got


def main():
    bad = scan_repo()
    for f, tests in sorted(bad.items()):
        print("%s" % f)
        for t in tests:
            print("    UNGUARDED %s" % t)
    print("\n%d file(s) with tests that cannot fail" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
