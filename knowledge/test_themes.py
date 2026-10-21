#!/usr/bin/env python3
"""Offline tests for periodic Markdown theme mining."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import themes                                             # noqa: E402


def _doc(repo, path, genre, text):
    return {"repo": repo, "path": path, "title": path, "genre": genre,
            "weight": themes.GENRE_WEIGHT[genre],
            "phrases": themes._phrases(text)}


def test_cross_repo_theme_ranks_separately_from_local_theme():
    docs = [
        _doc("r1", "a.md", "finding",
             "Silent failure. A stale artifact needs a negative control. "
             "The negative control exposes the stale artifact."),
        _doc("r2", "b.md", "study",
             "A stale artifact survived. Add a negative control for the "
             "silent failure and verify the artifact."),
        _doc("r1", "c.md", "finding",
             "Tub isolation attribution. Tub isolation needs a probe."),
        _doc("r1", "d.md", "study",
             "Tub isolation failed. Tub isolation is process specific."),
    ]
    general, local = themes.rank_themes(docs, top=20, min_repos=2)
    assert any(t["repo_count"] == 2 for t in general)
    assert any("tub isolation" in t["label"] for t in local)
    assert all(t["repo_count"] >= 2 for t in general)
    assert all(t["repo_count"] == 1 for t in local)


def test_windows_consumer_path_maps_under_wsl_only():
    got = themes.native_path(r"C:\dev\spec2si-tsmc65")
    if os.name == "nt":
        assert got.startswith("C:")
    else:
        assert got == "/mnt/c/dev/spec2si-tsmc65"


def test_git_discovery_excludes_ignored_generated_markdown():
    import subprocess
    root = tempfile.mkdtemp(prefix="themegit_")
    subprocess.check_call(["git", "init", "-q"], cwd=root)
    os.makedirs(os.path.join(root, "docs"))
    os.makedirs(os.path.join(root, "knowledge", "work"))
    with open(os.path.join(root, ".gitignore"), "w") as fh:
        fh.write("knowledge/work/\n")
    with open(os.path.join(root, "docs", "kept.md"), "w") as fh:
        fh.write("negative control\n")
    with open(os.path.join(root, "knowledge", "work", "ignored.md"), "w") as fh:
        fh.write("self generated\n")
    got = [p.replace("\\", "/") for p in themes._repo_markdown(root)]
    assert "docs/kept.md" in got
    assert "knowledge/work/ignored.md" not in got


def test_logs_are_discounted_against_findings():
    docs = [_doc("r1", "log.md", "log", "restore point. restore point."),
            _doc("r2", "log.md", "log", "restore point. restore point."),
            _doc("r1", "finding.md", "finding", "negative control. negative control."),
            _doc("r2", "finding.md", "finding", "negative control. negative control.")]
    general, _ = themes.rank_themes(docs, top=20, min_repos=2)
    score = dict((t["label"], t["score"]) for t in general)
    assert score["negative control"] > score["restore point"]


def test_code_fences_and_urls_do_not_become_themes():
    got = themes._phrases("""Use [negative control](https://secret.example/x).
```python
super_secret_numeric_rule = 0.123
```
Negative controls catch stale artifacts.
""")
    blob = " ".join(got)
    assert "secret" not in blob and "numeric" not in blob
    assert any("negative control" in p for p in got)


def test_stable_id_does_not_depend_on_rank():
    assert themes._theme_id("negative control") == themes._theme_id("negative control")
    assert themes._theme_id("negative control") != themes._theme_id("stale artifact")


def test_delta_records_added_dropped_and_rank_move():
    old = [{"id": "a", "label": "a", "rank": 1},
           {"id": "b", "label": "b", "rank": 2}]
    new = [{"id": "b", "label": "b", "rank": 1},
           {"id": "c", "label": "c", "rank": 2}]
    delta = themes._delta(old, new)
    assert delta["added"] == ["c"] and delta["dropped"] == ["a"]
    assert delta["rank_changes"][0]["id"] == "b"


def test_check_detects_changed_markdown():
    root = tempfile.mkdtemp(prefix="themes_")
    os.makedirs(os.path.join(root, "docs"))
    md = os.path.join(root, "docs", "a.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# A\nnegative control\n")
    old_repos = themes.repositories
    themes.repositories = lambda *_a: [{"name": "r", "path": root}]
    snapshot = os.path.join(root, "themes.json")
    try:
        snap = {"schema": themes.SCHEMA,
                "corpus": themes.corpus_manifest(themes.repositories())}
        with open(snapshot, "w", encoding="utf-8") as fh:
            json.dump(snap, fh)
        ok, _ = themes.check(snapshot)
        assert ok
        with open(md, "a", encoding="utf-8") as fh:
            fh.write("stale artifact\n")
        ok, message = themes.check(snapshot)
        assert not ok and "STALE" in message and "1 changed" in message
    finally:
        themes.repositories = old_repos


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
