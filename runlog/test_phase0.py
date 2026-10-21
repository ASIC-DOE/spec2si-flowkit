#!/usr/bin/env python3
"""Offline tests for the cross-repo phase-0 sampler."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase0                                            # noqa: E402


def _row(aid, repo="r1", domain="analog", tier="offline",
         cause="unclassified", when="2026-08-01T10:00:00Z", session="s1",
         cell="cell1"):
    return {"id": aid, "repo": repo, "repo_root": "/not/emitted",
            "domain": domain, "tier": tier, "cause": cause, "when": when,
            "session": session, "cell": cell, "actions": 4, "errors": 1,
            "dur_s": 10, "files": ["plan.py"], "tools": [["Read", 1]],
            "thrash": [], "partial": False}


def test_append_only_log_is_last_wins():
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "a", "cause": "unclassified"}) + "\n")
        fh.write("not json\n")
        fh.write(json.dumps({"id": "a", "cause": "engine-defect"}) + "\n")
    assert phase0.current_records(path)["a"]["cause"] == "engine-defect"


def test_windows_consumer_path_maps_under_wsl_only():
    got = phase0.native_path(r"C:\dev\spec2si-tsmc28")
    if os.name == "nt":
        assert got.startswith("C:")
    else:
        assert got == "/mnt/c/dev/spec2si-tsmc28"


def test_allocation_is_exact_and_keeps_every_stratum():
    rows = []
    for i in range(20):
        rows.append(_row("a%d" % i, repo="r1"))
    for i in range(5):
        rows.append(_row("b%d" % i, repo="r2", tier="cluster"))
    for i in range(2):
        rows.append(_row("c%d" % i, repo="r2", domain="digital"))
    picked, strata = phase0.draw(rows, n=10, seed=7)
    assert len(picked) == 10
    assert len(strata) == 3
    assert len(set((r["repo"], r["domain"], r["tier"]) for r in picked)) == 3


def test_draw_excludes_existing_classifications_and_reproduces():
    rows = [_row("a%d" % i) for i in range(8)]
    rows.append(_row("done", cause="closed"))
    a, _ = phase0.draw(rows, n=4, seed=99)
    b, _ = phase0.draw(rows, n=4, seed=99)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert "done" not in [r["id"] for r in a]


def test_subject_words_are_hints_not_decisions():
    assert "stale-artifact" in phase0.hint_causes(
        "the stale route card silently survived")
    rows = [_row("a")]
    picked, strata = phase0.draw(rows, 1, 1)
    old = phase0.git_commits
    phase0.git_commits = lambda *_a: [{
        "commit": "123456789abc", "when": "2026-08-01T11:00:00+00:00",
        "subject": "fix the stale artifact", "files": ["plan.py"]}]
    try:
        packet = phase0.build_packet(rows, picked, [], 1, strata)
    finally:
        phase0.git_commits = old
    item = packet["items"][0]
    assert item["suggested_causes"] == ["stale-artifact", "engine-defect"]
    assert item["review"]["cause"] is None
    assert "classification" in item["commit_evidence"][0]["warning"]


def test_packet_omits_consumer_root_and_has_review_command():
    rows = [_row("a")]
    picked, strata = phase0.draw(rows, 1, 1)
    old = phase0.git_commits
    phase0.git_commits = lambda *_a: []
    try:
        packet = phase0.build_packet(rows, picked, [], 1, strata)
    finally:
        phase0.git_commits = old
    blob = json.dumps(packet)
    assert "/not/emitted" not in blob and "repo_root" not in blob
    assert "runlog.py classify a" in packet["items"][0]["classify_command"]


def test_adjacent_context_does_not_apply_one_label_to_an_episode():
    rows = [_row("a", when="2026-08-01T10:00:00Z"),
            _row("b", when="2026-08-01T10:05:00Z")]
    got = phase0.adjacent(rows[0], rows)
    assert [r["id"] for r in got] == ["b"]
    assert "cause" not in got[0]


def test_report_waits_for_real_log_classifications():
    rows = [_row("a")]
    picked, strata = phase0.draw(rows, 1, 1)
    old = phase0.git_commits
    phase0.git_commits = lambda *_a: []
    try:
        packet = phase0.build_packet(rows, picked, [], 1, strata)
    finally:
        phase0.git_commits = old
    assert "0 of 1" in phase0.report(packet, rows)
    rows[0]["cause"] = "engine-defect"
    text = phase0.report(packet, rows)
    assert "1 of 1" in text and "engine-defect" in text


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  ok   " + name)
        except Exception as exc:                         # noqa: BLE001
            failed += 1
            print("  FAIL {}: {}".format(name, exc))
    print("{}/{} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
