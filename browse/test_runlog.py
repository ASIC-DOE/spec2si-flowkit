#!/usr/bin/env python3
"""Offline self-test for the agent-loop occurrence log.

No cluster, no network, no real transcript. Two things are being proved:

  1. the NDA constraint survives the second reader -- runlog goes through
     agentview's allowlist, so the fixtures here are stuffed with the same
     material test_agentview.py plants, and none of it may reach a record;
  2. the log COUNTS rather than DECIDES -- a harvested attempt is
     `unclassified` until a human says otherwise, the enumeration is closed,
     and classifying appends a revision instead of rewriting history.

  python3 test_runlog.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runlog                                           # noqa: E402

SECRETS = [
    "M2.S.1 spacing violation at 316.78 7128.4",
    "vdd 1.2 tran 0 10n gain=44.7mV",
    "DENSITY OD 0.42 WINDOW 100 STEP 50",
    "TSMC65 confidential layer table",
]
ROSTER = {"strongarm", "vref", "comparator"}


def _rec(**kw):
    base = {"type": "assistant", "timestamp": "2026-08-05T10:00:00.000Z",
            "sessionId": "s1", "isSidechain": False}
    base.update(kw)
    return json.dumps(base)


def _turn(ts="2026-08-05T10:00:00.000Z"):
    return _rec(type="user", timestamp=ts, message={"content": "do the thing"})


def _use(name, tid, inp, ts):
    return _rec(timestamp=ts, message={
        "content": [{"type": "tool_use", "id": tid, "name": name,
                     "input": inp}]})


def _res(tid, ts, is_error=False, content="OUTPUT"):
    return _rec(type="user", timestamp=ts, message={
        "content": [{"type": "tool_result", "tool_use_id": tid,
                     "is_error": is_error, "content": content}]})


def _transcript(lines):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _sandbox():
    """Point the log at a temp file -- a test must never append to the
    committed record."""
    d = tempfile.mkdtemp(prefix="runlog_")
    runlog.SPECS = d
    runlog.LOG = os.path.join(d, "runlog.jsonl")
    runlog.STATS = os.path.join(d, "runlog_stats.json")
    return d


OFFLINE = [
    _turn(),
    _use("Read", "t1",
         {"file_path": "C:\\dev\\spec2si-tsmc65\\analog\\lib\\aiml_analog\\"
                       "strongarm\\analysis\\plan.py",
          "description": "read the plan"}, "2026-08-05T10:00:01.000Z"),
    _res("t1", "2026-08-05T10:00:03.000Z", content=SECRETS[0]),
    _use("Edit", "t2",
         {"file_path": "C:\\dev\\spec2si-tsmc65\\analog\\lib\\aiml_analog\\"
                       "strongarm\\strongarm_def.py",
          "old_string": SECRETS[1], "new_string": SECRETS[2]},
         "2026-08-05T10:00:05.000Z"),
    _res("t2", "2026-08-05T10:00:06.000Z", is_error=True,
         content=SECRETS[3]),
]

CLUSTER = [
    _turn("2026-08-05T11:00:00.000Z"),
    _use("Bash", "t3",
         {"path": "C:\\dev\\spec2si-tsmc65\\analog\\work\\vref\\lvs.rep",
          "command": "ssh asic7 calibre -drc " + SECRETS[2],
          "description": "run calibre on asic7"},
         "2026-08-05T11:00:01.000Z"),
    _res("t3", "2026-08-05T11:02:01.000Z"),
]


# ============================ THE NDA CONSTRAINT ============================
def test_no_secret_reaches_a_record():
    _sandbox()
    recs = runlog.harvest_session(_transcript(OFFLINE + CLUSTER), "sess0001",
                                  ROSTER, set())
    blob = json.dumps(recs)
    for s in SECRETS:
        assert s not in blob, s
    # and the fields that carry them are not there under any name
    for banned in ("command", "old_string", "new_string", "content",
                   "stdout", "stderr"):
        assert banned not in blob


def test_paths_are_reduced_to_cell_and_basenames():
    _sandbox()
    recs = runlog.harvest_session(_transcript(OFFLINE), "sess0001",
                                  ROSTER, set())
    assert len(recs) == 1
    assert recs[0]["cell"] == "strongarm"
    assert recs[0]["files"] == ["plan.py", "strongarm_def.py"]
    assert "C:\\dev" not in json.dumps(recs)


def test_no_path_separator_survives_anywhere_in_a_record():
    """The leak this caught: agentview's thrash `why` is rendered for the
    panel, which runs on Windows where basename() splits a Windows path.
    Harvesting under WSL it does not, and the FULL path went into the
    record. Repetition is needed to make thrash fire, so this fixture
    reads one file five times."""
    _sandbox()
    p = ("C:\\dev\\spec2si-tsmc65\\analog\\lib\\aiml_analog\\comparator\\"
         "analysis\\bench.py")
    lines = [_turn()]
    for i in range(5):
        ts = "2026-08-05T10:00:%02d.000Z" % (i * 2)
        lines.append(_use("Read", "r%d" % i,
                          {"file_path": p, "description": "look again"}, ts))
        lines.append(_res("r%d" % i, ts, content=SECRETS[0]))
    recs = runlog.harvest_session(_transcript(lines), "s", ROSTER, set())
    assert recs and recs[0]["thrash"], "the fixture must trigger thrash"
    blob = json.dumps(recs)
    assert "\\\\" not in blob and "C:" not in blob
    assert "/analog/" not in blob and "aiml_analog" not in blob
    assert "bench.py" in blob                    # the basename is the point


def test_a_note_may_name_a_rule_but_never_its_value():
    assert runlog.note_is_safe("VIA3.EN.1 again, riser past the track end")
    assert runlog.note_is_safe("LUP.6 across 44 runs; deferred")
    assert not runlog.note_is_safe("M1.S.1 0.09 is the spacing")
    assert not runlog.note_is_safe("VIAx.R.2=0.05")
    assert runlog._clean_note("M1.S.1 0.09").startswith("[redacted")


def test_a_note_cannot_smuggle_a_paragraph():
    long_note = "x" * (runlog.NOTE_MAX * 3)
    assert len(runlog._clean_note(long_note)) == runlog.NOTE_MAX


# ============================ COUNTS, NOT VERDICTS ==========================
def test_harvest_never_guesses_a_cause():
    _sandbox()
    recs = runlog.harvest_session(_transcript(OFFLINE + CLUSTER), "sess0001",
                                  ROSTER, set())
    assert [r["cause"] for r in recs] == ["unclassified"] * len(recs)
    assert all(r["source"] == "harvest" for r in recs)


def test_the_enumeration_is_closed():
    _sandbox()
    for r in runlog.harvest_session(_transcript(OFFLINE), "s", ROSTER, set()):
        runlog.append(r)
    aid = list(runlog.current())[0]
    try:
        runlog.classify(aid, "it-was-fine")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown cause must be rejected")


def test_classifying_appends_a_revision_and_last_wins():
    _sandbox()
    for r in runlog.harvest_session(_transcript(OFFLINE), "s", ROSTER, set()):
        runlog.append(r)
    aid = list(runlog.current())[0]
    runlog.classify(aid, "gate-fail", "VIA3.EN.1 on the east riser")
    runlog.classify(aid, "engine-defect", "landing window from the wrong track")
    assert len(runlog.all_records()) == 3          # history is intact
    assert len(runlog.current()) == 1              # roll-up is current
    cur = runlog.current()[aid]
    assert cur["cause"] == "engine-defect" and cur["source"] == "declared"
    assert cur["actions"] == 2                    # skeleton survived


def test_unclassified_is_counted_not_hidden():
    _sandbox()
    for r in runlog.harvest_session(_transcript(OFFLINE + CLUSTER),
                                    "s", ROSTER, set()):
        runlog.append(r)
    s = runlog.rebuild_stats()
    assert s["unclassified"] == 2 == s["by_cause"]["unclassified"]
    assert "unclassified" in runlog.report(s)


# ============================ THE CLAIM =====================================
def test_tier_is_derived_and_its_basis_is_recorded():
    _sandbox()
    recs = runlog.harvest_session(_transcript(OFFLINE + CLUSTER), "s",
                                  ROSTER, set())
    by_cell = {r["cell"]: r for r in recs}
    assert by_cell["strongarm"]["tier"] == "offline"
    assert by_cell["vref"]["tier"] == "cluster"
    # a heuristic must say which signal fired, or it cannot be audited --
    # and after the 2026-08-26 tightening it must also say WHERE, because
    # the hint that carried 110 of 162 tags matched action summaries this
    # module never stores, so those tags could not be checked afterwards
    assert runlog._CLUSTER_RE.search(by_cell["vref"]["tier_basis"])
    assert "tier_evidence" in by_cell["vref"]


def test_the_cluster_rule_does_not_fire_on_talking_about_the_cluster():
    """The two substrings removed on 2026-08-26 were carrying two thirds of
    the cluster tags, and they matched a great deal that never leaves the
    laptop. A file UNDER deployment/ is a script ABOUT the cluster; reading
    CLUSTER.md is not a round trip. This is the poison for the regression
    that would put them back -- and for the one that would add `pmos` as a
    bare host name, which is a transistor type in every analog file here."""
    for benign in ("deployment/bnl/push.sh", "docs/cluster-access.md",
                   "cluster.md", "notes about the cluster", "pmos_sizing.py",
                   "analog/lib/asicdesign_notes.md"):
        assert not runlog._CLUSTER_RE.search(benign), benign
    for real in ("ssh asic7 bash -s", "asic9.inst.bnl.gov", "calibre -drc",
                 "innovus -init", "strmout.log", "scp x asic10:~/y",
                 "virtuoso -nograph", "exxact", "pegasus", "sync/push"):
        assert runlog._CLUSTER_RE.search(real), real


def test_offline_per_cluster_is_null_when_there_is_no_cluster_run():
    """An unbounded ratio is not evidence. 10 offline / 0 cluster is the
    claim we most want to make and the one that must not become 'inf'."""
    _sandbox()
    for r in runlog.harvest_session(_transcript(OFFLINE), "s", ROSTER, set()):
        runlog.append(r)
    s = runlog.rebuild_stats()
    assert s["by_cell"]["strongarm"]["offline_per_cluster"] is None
    assert s["by_cell"]["strongarm"]["cluster"] == 0


def test_harvest_is_idempotent():
    _sandbox()
    path = _transcript(OFFLINE + CLUSTER)
    seen = set()
    first = runlog.harvest_session(path, "s", ROSTER, seen)
    again = runlog.harvest_session(path, "s", ROSTER, seen)
    assert len(first) == 2 and again == []


def test_a_turn_that_touches_no_known_cell_is_not_an_attempt():
    _sandbox()
    lines = [_turn(),
             _use("Read", "t9", {"file_path": "/repo/docs/README.md"},
                  "2026-08-05T10:00:01.000Z"),
             _res("t9", "2026-08-05T10:00:02.000Z")]
    assert runlog.harvest_session(_transcript(lines), "s",
                                 ROSTER, set()) == []


def test_an_unchanged_rebuild_does_not_rewrite_the_stats_file():
    """The post-commit trigger runs after EVERY commit. If a no-op harvest
    rewrote the `generated` timestamp, it would leave a modified file in
    git status forever and the trigger would be worse than no trigger."""
    _sandbox()
    for r in runlog.harvest_session(_transcript(OFFLINE), "s", ROSTER, set()):
        runlog.append(r)
    runlog.rebuild_stats({"sessions": 1, "turns": 1, "attempts": 1})
    before = os.stat(runlog.STATS).st_mtime_ns
    body = open(runlog.STATS, encoding="utf-8").read()
    runlog.rebuild_stats({"sessions": 1, "turns": 1, "attempts": 1})
    assert os.stat(runlog.STATS).st_mtime_ns == before
    assert open(runlog.STATS, encoding="utf-8").read() == body
    # ... but a real change still lands
    runlog.rebuild_stats({"sessions": 2, "turns": 9, "attempts": 1})
    assert '"turns": 9' in open(runlog.STATS, encoding="utf-8").read()


def test_the_stats_declare_that_counts_are_a_floor():
    _sandbox()
    s = runlog.rebuild_stats()
    assert "floor" in s["caveat"] and "denominator" in s["caveat"]


def test_the_scan_denominator_is_carried_into_the_stats():
    """`10 attempts` means nothing without `out of 219 turns scanned`."""
    _sandbox()
    path = _transcript(OFFLINE + CLUSTER)
    recs, turns, matched = runlog.harvest_session(path, "s", ROSTER, set(),
                                                  _count=True)
    assert turns == 2 and matched == 2 and len(recs) == 2
    # a re-harvest finds the same attempts and appends none: `matched` must
    # hold, or the denominator's partner silently drops to zero
    seen = {r["id"] for r in recs}
    recs2, _t, matched2 = runlog.harvest_session(path, "s", ROSTER, seen,
                                                 _count=True)
    assert matched2 == 2 and recs2 == []
    for r in recs:
        runlog.append(r)
    s = runlog.rebuild_stats({"sessions": 1, "turns": 7, "attempts": 2})
    assert s["harvest"]["turns"] == 7
    assert "7 turn(s)" in runlog.report(s)
    # and a plain stats rebuild must not silently drop it
    assert runlog.rebuild_stats()["harvest"]["turns"] == 7


# ----------------------------------------------------- one runlog per repo
def _fake_repo(pkg="onr_analog", cells=("vco", "charge_pump"), declared=None):
    d = tempfile.mkdtemp(prefix="runlog_repo_")
    for c in cells:
        os.makedirs(os.path.join(d, "analog", "lib", pkg, c))
    os.makedirs(os.path.join(d, "analog", "specs"), exist_ok=True)
    if declared is not None:
        with open(os.path.join(d, "analog", "specs", "runlog_cells.txt"),
                  "w", encoding="utf-8") as fh:
            fh.write(declared)
    return d


def test_the_cell_home_is_discovered_not_named():
    """`analog/lib/aiml_analog` was hard-coded, so the roster was empty for
    every repo but this one -- ONR's package is `onr_analog`. Discovering the
    package is what makes one implementation serve all three."""
    d = _fake_repo()
    homes = [os.path.basename(h) for h in runlog.cell_homes(d)]
    assert "onr_analog" in homes, homes
    assert runlog.cells(d) == {"vco", "charge_pump"}, runlog.cells(d)


def test_a_repo_with_no_cell_library_may_declare_its_blocks():
    """XT011 has no per-cell directories at all, so a discovered roster is
    empty and it would record nothing for ever."""
    d = _fake_repo(cells=())
    assert runlog.cells(d) == set()
    d2 = _fake_repo(cells=(), declared="# blocks\nnvg\n\npadring\n")
    assert runlog.cells(d2) == {"nvg", "padring"}, runlog.cells(d2)


def test_declared_cells_survive_a_missing_file():
    assert runlog.declared_cells(tempfile.mkdtemp()) == set()


def test_the_parent_directory_is_harvested_too():
    r"""A session started in C:\dev that edits C:\dev\spec2si-tsmc65 all day is
    filed under `C--dev`. Harvesting only the repo's own slug missed 126
    attempts on AIML cells sitting there, against 37 recorded in total."""
    got = runlog.transcript_cwds(r"C:\dev\spec2si-tsmc65")
    assert got[0].endswith("spec2si-tsmc65"), got
    assert any(g.rstrip("\\").endswith("dev") for g in got[1:]), got


def test_the_parent_of_a_drive_root_is_not_harvested():
    r"""`C:\` has no parent worth scanning, and slugging one would sweep in
    every project on the machine."""
    got = runlog.transcript_cwds(r"C:\repo")
    assert len(got) == 1, got


def test_there_is_no_session_ceiling_by_default():
    """It was 25 against 52 sessions, and nothing ever looks back -- 155
    findable attempts were behind it. A full scan measured 2.56 s against the
    hook's 60 s timeout, so the ceiling bought nothing."""
    assert runlog.DEFAULT_LIMIT is None
    import inspect
    sig = inspect.signature(runlog.harvest)
    assert sig.parameters["limit"].default is None


def test_the_repo_is_selectable_by_environment():
    """One implementation, located rather than copied -- the same contract
    browse/launch.py has with BROWSE_REPO. Checked in a subprocess because
    the paths resolve at IMPORT."""
    import subprocess
    d = _fake_repo()
    here = os.path.dirname(os.path.abspath(runlog.__file__))
    code = ("import os, sys; os.environ['RUNLOG_REPO']=%r; "
            "os.environ.pop('AIML_ROOT', None); sys.path.insert(0, %r); "
            "import runlog; print(runlog.LOG); print(sorted(runlog.cells()))"
            % (d, here))
    out = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT).stdout.decode("utf-8", "replace")
    assert d in out, out[:400]
    assert "charge_pump" in out, out[:400]


def test_the_parent_is_split_with_WINDOWS_semantics():
    """The harvest runs under WSL against WINDOWS paths, where `os.path` uses
    POSIX rules and a backslash is an ordinary character. `os.path.dirname`
    returned "" there, so the parent leg vanished on the machine that actually
    runs the hook while passing every test on Windows.

    Asserted on the STRING rather than by calling os.path, so it fails on
    either platform if the split goes back to os.path."""
    import posixpath
    p = r"C:\dev\spec2si-tsmc28"
    assert posixpath.dirname(p) == "", "this test no longer proves anything"
    got = runlog.transcript_cwds(p)
    assert len(got) == 2, got
    assert got[1] == r"C:\dev", got


def test_the_design_record_is_a_roster_and_its_origins_attribute():
    """Every port carries design/<lib>/<cell>/cell.json since 2026-09-12: the
    cells it names are the roster nobody maintains, and a view captured from
    a TRACKED file attributes that file's edits to the cell."""
    d = _fake_repo(cells=())
    os.makedirs(os.path.join(d, "design", "lib", "ring"))
    with open(os.path.join(d, "design", "lib", "ring", "cell.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"library": "lib", "cell": "ring", "views": {
            "power": {"type": "route", "files": [
                {"path": "lib/ring/power/ring_power.route",
                 "origin": "chip/floorplan/ring_power.route"}]},
            "layout": {"type": "layout", "files": [
                {"path": "lib/ring/layout/layout.oa",
                 "origin": "/u/home/x/analog/oa/lib/ring/layout/layout.oa"}]}}},
            fh)
    assert runlog.cells(d) == {"ring"}, runlog.cells(d)
    rules = runlog.rules_for(d)
    # the tracked origin, in any spelling of the root; not the cluster one
    assert rules.cell_for([d + "\\chip\\floorplan\\ring_power.route"]) == "ring"
    assert rules.cell_for(["/u/home/x/analog/oa/lib/ring/layout/layout.oa"]) is None
    assert runlog.cell_of([d + "/chip/floorplan/ring_power.route"],
                          runlog.cells(d), rules) == "ring"
    assert runlog.cell_of([d + "/chip/floorplan/other.py"],
                          runlog.cells(d), rules) is None


def test_a_declared_path_rule_counts_chip_level_work():
    """xt011's die work lives in analog/layout and chip/floorplan under
    names like check_top_channel.py -- no roster name in any path, so 603 of
    618 turns went unrecorded while the hook fired on every session end. A
    rule maps the globs to the cell, and a former checkout root (ADR-0001's
    rename) still relativises."""
    d = _fake_repo(cells=(), declared=(
        "# rules\n"
        "@former-root C:/dev/OLD_NAME\n"
        "top = analog/layout/*channel*, analog/layout/gen_*_template.py\n"
        "core = chip/floorplan/core_*\n"
        "padring\n"))
    assert runlog.declared_cells(d) == {"top", "core", "padring"}, \
        runlog.declared_cells(d)
    globs, former = runlog.declared_rules(d)
    assert [c for c, _g in globs] == ["top", "core"] and former == ["C:/dev/OLD_NAME"]
    rules = runlog.rules_for(d)
    roster = runlog.cells(d)
    here = d.replace("/", "\\")
    assert runlog.cell_of([here + "\\analog\\layout\\check_top_channel.py"],
                          roster, rules) == "top"
    assert runlog.cell_of([here + "\\analog\\layout\\gen_right_template.py"],
                          roster, rules) == "top"
    assert runlog.cell_of([r"C:\dev\OLD_NAME\chip\floorplan\core_route.py"],
                          roster, rules) == "core"
    # a name-level match still wins over a rule, and a miss is a miss
    assert runlog.cell_of([here + "\\work\\padring\\x.il"], roster, rules) == "padring"
    assert runlog.cell_of([here + "\\analog\\layout\\render_gds.py"],
                          roster, rules) is None
    # a rule's glob is never mistaken for a cell name
    assert "analog/layout/*channel*" not in roster


def test_path_rules_relativise_against_the_windows_root_under_wsl():
    """The hook runs under WSL, where the repo root is /mnt/c/dev/<repo>,
    and the transcripts carry C:\\dev\\<repo>\\... -- so a rule that
    relativised against the WSL root alone matched 32 attempts from the
    hook against 123 from Windows, on the same transcripts."""
    rules = runlog.Rules("/mnt/c/dev/repo",
                         [runlog.transcript_cwd("/mnt/c/dev/repo")],
                         {}, [("top", ["analog/layout/*channel*"])])
    assert rules.cell_for([r"C:\dev\repo\analog\layout\check_top_channel.py"]) == "top"
    # and rules_for adds that spelling itself
    got = runlog.rules_for("/mnt/c/dev/never-such-repo")
    assert got.relativise(r"C:\dev\never-such-repo\x\y.py") == "x/y.py", \
        got.relativise(r"C:\dev\never-such-repo\x\y.py")


def main():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ok   %s" % name)
        except Exception as exc:                       # noqa: BLE001
            bad += 1
            print("  FAIL %s: %s" % (name, exc))
    print("%d/%d passed" % (len(fns) - bad, len(fns)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
