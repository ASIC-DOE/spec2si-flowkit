#!/usr/bin/env python3
"""Offline self-test for the agent activity view.

No cluster, no network, and no real transcript: every fixture is synthetic and
deliberately STUFFED with material that must never come out the other side --
rule-deck text, layout coordinates, simulation numbers, file contents. Half of
this file is one assertion written several ways, because section 7.2 is the
non-negotiable constraint on this view and "we render metadata only" is worth
exactly as much as the test that proves it.

  python3 test_agentview.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentview                        # noqa: E402

#: Strings planted in the fixtures. Not one may appear in any output.
SECRETS = [
    "M2.S.1 spacing violation at 316.78 7128.4",     # rule-deck text
    "vdd 1.2 tran 0 10n gain=44.7mV",                # sim result
    "DENSITY OD 0.42 WINDOW 100 STEP 50",            # deck rule
    "def secret_geometry(): return 0.065",           # file content
    "TSMC65 confidential layer table",
]


def _rec(**kw):
    base = {"type": "assistant", "timestamp": "2026-07-31T10:00:00.000Z",
            "sessionId": "s1", "isSidechain": False, "cwd": "C:\\dev\\X"}
    base.update(kw)
    return json.dumps(base)


def _tool_use(name, tid, inp, ts="2026-07-31T10:00:00.000Z", side=False):
    return _rec(timestamp=ts, isSidechain=side, message={
        "content": [{"type": "tool_use", "id": tid, "name": name,
                     "input": inp}]})


def _tool_result(tid, ts="2026-07-31T10:00:05.000Z", is_error=False,
                 content="OUTPUT"):
    return _rec(type="user", timestamp=ts, message={
        "content": [{"type": "tool_result", "tool_use_id": tid,
                     "is_error": is_error, "content": content}]})


def _loaded(lines):
    """Events, plus the JSON the panel would actually serialise."""
    evs = agentview._events_from(lines)
    return evs, json.dumps(evs)


# ============================ THE NDA CONSTRAINT ============================
def test_no_tool_output_ever_reaches_the_events():
    """tool_result content is where deck text and sim numbers live."""
    lines = [_tool_use("Bash", "t1", {"command": "calibre -drc deck.rul",
                                      "description": "run DRC"}),
             _tool_result("t1", content=SECRETS[0])]
    evs, blob = _loaded(lines)
    for s in SECRETS:
        assert s not in blob, s
    assert "calibre -drc deck.rul" not in blob, "the COMMAND leaked"
    assert evs[0]["summary"] == "run DRC"


def test_no_file_content_ever_reaches_the_events():
    """Edit carries the old and new text of a file; Write carries all of it."""
    lines = [_tool_use("Edit", "t1", {
                 "file_path": "/repo/deck.rul",
                 "old_string": SECRETS[2], "new_string": SECRETS[3]}),
             _tool_use("Write", "t2", {"file_path": "/repo/x.py",
                                       "content": SECRETS[4]})]
    _evs, blob = _loaded(lines)
    for s in SECRETS:
        assert s not in blob, s
    # the PATH survives, because "which file" is the whole point
    assert "/repo/deck.rul" in blob and "/repo/x.py" in blob


def test_message_text_and_thinking_are_never_read():
    """The body of what the agent says, and its reasoning, are not metadata."""
    lines = [_rec(message={"content": [
        {"type": "text", "text": SECRETS[1]},
        {"type": "thinking", "thinking": SECRETS[0]}]})]
    evs, blob = _loaded(lines)
    for s in SECRETS:
        assert s not in blob, s
    # a `say` event is recorded -- that it SPOKE is metadata, what it said is not
    assert [e["kind"] for e in evs] == ["say"]
    assert evs[0]["summary"] is None


def test_an_unknown_input_field_is_dropped_not_shown():
    """THE reason this is an allowlist. A denylist is correct only until
    someone adds a field -- so a field nobody has thought about must be
    invisible by default, not visible by default."""
    lines = [_tool_use("FutureTool", "t1", {
        "file_path": "/repo/a.gds",
        "brand_new_field_nobody_allowlisted": SECRETS[0],
        "another_one": SECRETS[1]})]
    _evs, blob = _loaded(lines)
    for s in SECRETS:
        assert s not in blob, s
    assert "/repo/a.gds" in blob


def test_the_allowlist_is_small_and_contains_no_content_field():
    named = set(agentview._SAFE_INPUT_KEYS)
    for forbidden in ("command", "content", "text", "old_string",
                      "new_string", "prompt", "script", "body", "stdout"):
        assert forbidden not in named, forbidden
    assert named <= {"file_path", "path", "notebook_path", "url",
                     "description"}, named


def test_a_description_cannot_smuggle_a_paragraph():
    long = "x" * 5000
    lines = [_tool_use("Bash", "t1", {"description": long})]
    evs, _blob = _loaded(lines)
    assert len(evs[0]["summary"]) <= agentview._SUMMARY_MAX


def test_the_whole_summary_payload_is_metadata_only():
    """End to end, through the function the server actually calls."""
    d = tempfile.mkdtemp(prefix="agentview_")
    p = os.path.join(d, "s.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join([
            _tool_use("Bash", "t1", {"command": SECRETS[0],
                                     "description": "run the deck"}),
            _tool_result("t1", content=SECRETS[1]),
            _tool_use("Edit", "t2", {"file_path": "/r/a.py",
                                     "old_string": SECRETS[2],
                                     "new_string": SECRETS[3]}),
            _rec(message={"content": [{"type": "text", "text": SECRETS[4]}]}),
        ]) + "\n")
    blob = json.dumps(agentview.summarize(p))
    for s in SECRETS:
        assert s not in blob, s


def test_the_emitted_KEY_SET_is_closed():
    """Values are checked above; this pins the SHAPE.

    A leak would most likely arrive as a new key carrying a payload, so the
    set of keys the panel can emit is asserted whole. Adding one is then a
    deliberate act with a test to update, not something that happens because
    a field was passed through.
    """
    d = tempfile.mkdtemp(prefix="agentview_")
    p = os.path.join(d, "s.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join([
            _tool_use("Bash", "t1", {"command": SECRETS[0],
                                     "description": "d"}),
            _tool_result("t1", is_error=True, content=SECRETS[1]),
            _tool_use("Read", "t2", {"file_path": "/r/render.png"}),
            _tool_use("Grep", "t3", {"path": "/r"}, side=True),
        ]) + "\n")

    def keys(o, acc):
        if isinstance(o, dict):
            for k, v in o.items():
                acc.add(k)
                keys(v, acc)
        elif isinstance(o, list):
            for v in o:
                keys(v, acc)
        return acc

    got = keys(agentview.summarize(p), set())
    allowed = {"now", "state", "detail", "tool", "since_s",
               "thrash", "kind", "path", "n", "why",
               "fleet", "open", "tools",
               "looked_at", "t", "n_events",
               "timeline", "summary", "error", "elapsed_s", "done",
               "sidechain", "artifact",
               # turn grouping -- added deliberately, which is the point of
               # pinning the set: new keys arrive with a test to update
               "turns", "started", "ended", "dur_s", "errors", "files",
               "actions", "partial"}
    assert got <= allowed, got - allowed


# ================================ BEHAVIOUR =================================
def test_an_unfinished_tool_call_is_what_running_means():
    """The question the terminal cannot answer during a 25-minute LVS: is it
    stuck, or is the tool just slow? A call with no result yet IS the answer."""
    lines = [_tool_use("Bash", "t1", {"description": "run LVS"})]
    now = agentview.now_state(agentview._events_from(lines))
    assert now["state"] == "running" and now["tool"] == "Bash"


def test_a_finished_call_is_not_still_running():
    lines = [_tool_use("Bash", "t1", {"description": "run LVS"}),
             _tool_result("t1")]
    evs = agentview._events_from(lines)
    assert evs[0]["done"] is True and evs[0]["elapsed_s"] == 5
    assert agentview.now_state(evs)["state"] != "running"


def test_a_reply_means_waiting_on_you():
    lines = [_tool_use("Bash", "t1", {}), _tool_result("t1"),
             _rec(message={"content": [{"type": "text", "text": "done"}]})]
    got = agentview.now_state(agentview._events_from(lines))
    assert got["state"] == "waiting-on-you", got


def test_an_error_result_is_a_flag_not_its_message():
    lines = [_tool_use("Bash", "t1", {"description": "drc"}),
             _tool_result("t1", is_error=True, content=SECRETS[0])]
    evs, blob = _loaded(lines)
    assert evs[0]["error"] is True
    assert SECRETS[0] not in blob


def test_thrash_counts_repetition_which_is_the_early_warning():
    """The vref decap closure took NINE coordinate-diagnosed iterations. A
    view that says "sixth edit, still failing" catches a wrong turn while
    intervening is still cheap."""
    lines = []
    for i in range(6):
        lines.append(_tool_use("Edit", "t%d" % i, {"file_path": "/r/vref.py"}))
        lines.append(_tool_result("t%d" % i))
    got = agentview.thrash(agentview._events_from(lines))
    assert any(t["kind"] == "repeat" and t["n"] == 6 for t in got), got
    assert "vref.py" in got[0]["why"]


def test_repeated_failures_are_called_out_separately():
    lines = []
    for i in range(3):
        lines.append(_tool_use("Bash", "f%d" % i, {"file_path": "/r/a.gds"}))
        lines.append(_tool_result("f%d" % i, is_error=True))
    got = agentview.thrash(agentview._events_from(lines))
    assert any(t["kind"] == "failing" and t["n"] == 3 for t in got), got


def test_one_off_work_is_not_thrash():
    lines = [_tool_use("Edit", "t1", {"file_path": "/r/a.py"}),
             _tool_result("t1")]
    assert agentview.thrash(agentview._events_from(lines)) == []


def test_subagents_are_visible_before_they_report_back():
    lines = [_tool_use("Grep", "s1", {"path": "/r"}, side=True),
             _tool_use("Read", "s2", {"file_path": "/r/a.py"}, side=True),
             _tool_result("s2")]
    got = agentview.fleet(agentview._events_from(lines))
    assert got["n"] == 2 and got["open"] == 1, got


def test_renders_the_agent_opened_are_auditable():
    """Principle 3 says renders are agent-readable; this is how you check it
    actually looked before acting."""
    lines = [_tool_use("Read", "t1", {"file_path": "/r/render_full.png"}),
             _tool_use("Read", "t2", {"file_path": "/r/notes.md"})]
    got = agentview.looked_at(agentview._events_from(lines))
    assert [g["path"] for g in got] == ["/r/render_full.png"], got


def test_artifacts_are_flagged_for_link_out():
    d = tempfile.mkdtemp(prefix="agentview_")
    p = os.path.join(d, "s.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(_tool_use("Write", "t1", {"file_path": "/r/report.html"})
                 + "\n" + _tool_use("Bash", "t2", {"description": "ls"}) + "\n")
    tl = agentview.summarize(p)["timeline"]
    assert tl[0]["artifact"] is True and tl[1]["artifact"] is False


# ============================== TURNS & SESSIONS ============================
def _user_turn(ts):
    """A human turn: content is a STRING, not a block list."""
    return _rec(type="user", timestamp=ts, message={"content": "do the thing"})


def test_a_human_turn_groups_the_actions_it_caused():
    """A flat list of "Bash 3s / Edit 0s" is a stream with no reason attached
    to any of it. The turn says: these actions served one request."""
    lines = [_user_turn("2026-07-31T10:00:00.000Z"),
             _tool_use("Edit", "a", {"file_path": "/r/x.py"},
                       ts="2026-07-31T10:00:01.000Z"),
             _tool_result("a", ts="2026-07-31T10:00:02.000Z"),
             _tool_use("Bash", "b", {"description": "test"},
                       ts="2026-07-31T10:00:03.000Z"),
             _tool_result("b", ts="2026-07-31T10:00:20.000Z", is_error=True),
             _user_turn("2026-07-31T10:05:00.000Z"),
             _tool_use("Edit", "c", {"file_path": "/r/y.py"},
                       ts="2026-07-31T10:05:01.000Z")]
    ts = agentview.turns(agentview._events_from(lines))
    assert len(ts) == 2, [t["n"] for t in ts]
    assert ts[0]["n"] == 2 and ts[0]["errors"] == 1, ts[0]
    assert ts[0]["files"] == ["/r/x.py"], ts[0]["files"]
    assert ts[0]["dur_s"] == 3, ts[0]["dur_s"]
    assert ts[1]["n"] == 1


def test_the_prompt_itself_is_never_read_only_that_it_happened():
    lines = [_rec(type="user", timestamp="2026-07-31T10:00:00.000Z",
                  message={"content": SECRETS[0]}),
             _tool_use("Bash", "a", {"description": "x"})]
    evs, blob = _loaded(lines)
    assert SECRETS[0] not in blob
    assert [e["kind"] for e in evs] == ["turn", "tool"]


def test_a_tool_result_carrier_is_not_a_human_turn():
    """user records carrying tool_result blocks are the agent's own loop.
    Treating them as boundaries would put every action in its own turn."""
    lines = [_user_turn("2026-07-31T10:00:00.000Z")]
    for i in range(3):
        lines.append(_tool_use("Bash", "t%d" % i, {"description": "s"}))
        lines.append(_tool_result("t%d" % i))
    ts = agentview.turns(agentview._events_from(lines))
    assert len(ts) == 1 and ts[0]["n"] == 3, [t["n"] for t in ts]


def test_a_tail_that_begins_mid_turn_says_so():
    """The tail is bounded, so the first turn in view usually has no start."""
    lines = [_tool_use("Bash", "a", {"description": "x"}),
             _user_turn("2026-07-31T10:05:00.000Z"),
             _tool_use("Bash", "b", {"description": "y"})]
    ts = agentview.turns(agentview._events_from(lines))
    assert ts[0].get("partial") is True and ts[1].get("partial", False) is False


def test_concurrent_sessions_in_one_repo_are_both_visible():
    """Two agents in one repo is not hypothetical -- measured here, two
    overlapped by an hour and a half, and the panel showed whichever had
    touched disk last while saying nothing about the other."""
    import time as _t
    d = tempfile.mkdtemp(prefix="agentview_home_")
    proj = os.path.join(d, ".claude", "projects", "C--x")
    os.makedirs(proj)
    for name, title, agesec in (("aaa", "live one", 1),
                                ("bbb", "also live", 30),
                                ("ccc", "old one", 99999)):
        p = os.path.join(proj, name + ".jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "custom-title",
                                 "customTitle": title,
                                 "sessionId": name}) + "\n")
        t = _t.time() - agesec
        os.utime(p, (t, t))
    ss = agentview.sessions("C:\\x", home=d)
    assert [s["id"] for s in ss] == ["aaa", "bbb", "ccc"], ss
    assert [s["live"] for s in ss] == [True, True, False], ss
    # and a hex id is not a usable label for choosing between them
    assert [s["title"] for s in ss] == ["live one", "also live", "old one"]


def test_a_session_without_a_title_still_lists():
    d = tempfile.mkdtemp(prefix="agentview_home_")
    proj = os.path.join(d, ".claude", "projects", "C--x")
    os.makedirs(proj)
    with open(os.path.join(proj, "zz.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(_tool_use("Bash", "t", {"description": "x"}) + "\n")
    ss = agentview.sessions("C:\\x", home=d)
    assert len(ss) == 1 and ss[0]["title"] is None


# ================================= TAILING ==================================
def test_a_huge_transcript_is_tailed_not_parsed_whole():
    """204 MB across this project's sessions must never be read eagerly."""
    d = tempfile.mkdtemp(prefix="agentview_")
    p = os.path.join(d, "big.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for i in range(4000):
            fh.write(_tool_use("Edit", "old%d" % i,
                               {"file_path": "/r/ancient.py"}) + "\n")
        fh.write(_tool_use("Bash", "new", {"description": "the recent one"})
                 + "\n")
    evs = agentview.read_events(p, budget=4096)
    assert evs, "the tail produced nothing"
    assert evs[-1]["summary"] == "the recent one"
    assert len(evs) < 100, "read far more than the budget allowed"


def test_a_torn_first_line_does_not_break_the_tail():
    """Seeking into the middle of a file lands mid-record by definition."""
    d = tempfile.mkdtemp(prefix="agentview_")
    p = os.path.join(d, "t.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("x" * 5000 + "\n")
        fh.write(_tool_use("Bash", "t1", {"description": "after the tear"})
                 + "\n")
    evs = agentview.read_events(p, budget=2048)
    assert [e["summary"] for e in evs] == ["after the tear"], evs


def test_an_unchanged_transcript_is_not_reparsed():
    d = tempfile.mkdtemp(prefix="agentview_")
    p = os.path.join(d, "s.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(_tool_use("Bash", "t1", {"description": "x"}) + "\n")
    agentview._CACHE.clear()
    a = agentview.read_events(p)
    b = agentview.read_events(p)
    assert a is b, "the tail was parsed twice for an unchanged file"


def test_a_missing_transcript_degrades_silently():
    """The rest of the browser must be unaffected if there is nothing to tail."""
    assert agentview.read_events("/no/such/file.jsonl") == []
    assert agentview.sessions("C:\\nowhere", home="/no/such/home") == []
    assert agentview.now_state([])["state"] == "idle"


def test_the_project_slug_matches_the_on_disk_layout():
    assert agentview.project_slug("C:\\dev\\spec2si-tsmc65") == "C--dev-spec2si-tsmc65"


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
