#!/usr/bin/env python3
"""Agent activity -- what the coding agent is doing, from its own transcript.

Plan section 7. The terminal already shows the conversation; what it cannot
show is the JOIN between three streams that only a human currently correlates,
badly, by scrolling: the agent's actions, the cluster jobs those actions
launched, and the artifacts they produced. The browser is already tailing two
of them.

SOURCE: the session transcripts Claude Code already writes to
`~/.claude/projects/<cwd-slug>/<session>.jsonl`. No configuration, no added
latency, and retroactive -- this project alone has 204 MB of them on disk. The
plan's decision stands: tail the transcript, never hook PreToolUse/PostToolUse,
because a hook spawns a subprocess on every tool call, forever, to obtain data
already being written.

=============================== NDA (section 7.2) ==========================
Transcripts contain full prompts, file contents and tool output -- which for
this work means PDK geometry, rule-deck text and simulation results. So this
module emits METADATA ONLY, and it does that with an ALLOWLIST:
`_SAFE_INPUT_KEYS` names the few tool-input fields that may be read, and
everything else is dropped without being looked at.

An allowlist, not a denylist, and the distinction is the whole safety
argument: a denylist is correct only until someone adds a field. `command`,
`old_string`, `new_string`, `content`, `text`, `stdout`, `stderr` and the
thinking blocks are never read here, and cannot become readable by accident.

Read in place. Never copied into the repo, never committed.
============================================================================

Usage:
  python3 agentview.py [cwd]
"""
import json
import os
import time

#: The ONLY tool-input fields that may be read. Paths, because "which file"
#: is the entire point of the view, plus the agent's own one-line description
#: of a shell step, which is written as a UI label and carries no output.
_SAFE_INPUT_KEYS = ("file_path", "path", "notebook_path", "url", "description")

#: Hard cap on a description, so a long one cannot smuggle a paragraph.
_SUMMARY_MAX = 80

#: Never read more than this from the tail of a transcript. The project's
#: transcripts total 204 MB; the newest single file is already 1.5 MB and
#: growing while the agent works.
TAIL_BUDGET = 2 * 1024 * 1024

#: (path, size, mtime) -> parsed events. A re-poll of an unchanged file must
#: not re-parse it.
_CACHE = {}
_CACHE_MAX = 8


def project_slug(cwd):
    """`C:\\dev\\spec2si-tsmc65` -> `C--dev-spec2si-tsmc65`, the on-disk directory name."""
    return "".join(c if c.isalnum() else "-" for c in cwd)


def project_dir(cwd=None, home=None):
    home = home or os.path.expanduser("~")
    cwd = cwd or os.getcwd()
    return os.path.join(home, ".claude", "projects", project_slug(cwd))


#: A session whose file was touched this recently is treated as LIVE. Two
#: agents in one repo is not hypothetical -- measured here, 8d633a4f
#: (10:43-12:07) and 11c4cbb7 (09:29-12:09) overlapped by an hour and a half,
#: and the panel silently showed whichever had touched disk last.
LIVE_WITHIN_S = 300

#: How much of a session's tail to read just to identify it. The title is
#: re-emitted every turn, so the end of the file always has the current one.
_ID_TAIL = 96 * 1024


def _identify(path, size):
    """(title, last_ts) for a session, from a small tail. Cheap enough to do
    for every session in the picker."""
    try:
        with open(path, "rb") as fh:
            if size > _ID_TAIL:
                fh.seek(size - _ID_TAIL)
                raw = fh.read()
                nl = raw.find(b"\n")
                raw = raw[nl + 1:] if nl >= 0 else b""
            else:
                raw = fh.read()
    except OSError:
        return None, None
    title, last = None, None
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        # A session TITLE is a label for the conversation, of the same kind as
        # a file path -- not a message body. It is the only thing that makes a
        # picker of 25 hex ids usable, so it is read, truncated, and named
        # here rather than smuggled through the generic input allowlist.
        if rec.get("type") in ("custom-title", "ai-title"):
            t = rec.get("customTitle") or rec.get("aiTitle") or rec.get("title")
            if isinstance(t, str) and t.strip():
                title = t.strip()[:70]
        if rec.get("timestamp"):
            last = rec["timestamp"]
    return title, last


def sessions(cwd=None, home=None, limit=25, identify=True):
    """[{id, path, size, mtime, title, live}] newest first.

    `live` is the answer to "is something else also running in this repo right
    now" -- without it the panel picks the newest file and says nothing, which
    is indistinguishable from there being only one session.
    """
    d = project_dir(cwd, home)
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    now = time.time()
    for n in names:
        if not n.endswith(".jsonl"):
            continue
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        e = {"id": n[:-6], "path": p, "size": st.st_size,
             "mtime": int(st.st_mtime), "title": None,
             "live": (now - st.st_mtime) < LIVE_WITHIN_S}
        out.append(e)
    out.sort(key=lambda e: -e["mtime"])
    out = out[:limit]
    if identify:
        for e in out:
            e["title"], _last = _identify(e["path"], e["size"])
    return out


def _safe_input(inp):
    """Allowlisted fields only. Anything not named here is never read."""
    if not isinstance(inp, dict):
        return {}, None
    path = None
    summary = None
    for k in _SAFE_INPUT_KEYS:
        v = inp.get(k)
        if not isinstance(v, str):
            continue
        if k == "description":
            summary = v[:_SUMMARY_MAX]
        elif path is None:
            path = v
    return path, summary


def _events_from(lines):
    """Transcript lines -> metadata events. The only place records are read."""
    events = []
    pending = {}                       # tool_use_id -> event awaiting a result
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue                   # a partial first line, or a torn write
        if not isinstance(rec, dict):
            continue
        ts = rec.get("timestamp")
        side = bool(rec.get("isSidechain"))
        msg = rec.get("message")
        blocks = msg.get("content") if isinstance(msg, dict) else None
        # A human turn arrives with content as a STRING, not a block list --
        # which the list-only guard below skipped entirely, so every action in
        # the tail collapsed into one 225-step "turn". The string is a message
        # body and is never read; only that it happened, and when.
        if (rec.get("type") == "user" and not side
                and isinstance(blocks, str) and blocks.strip()):
            events.append({"t": ts, "kind": "turn", "tool": None,
                           "path": None, "summary": None, "sidechain": False,
                           "id": None, "error": None, "elapsed_s": None,
                           "done": True})
            continue
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if kind == "tool_use":
                path, summary = _safe_input(b.get("input"))
                ev = {"t": ts, "kind": "tool", "tool": b.get("name"),
                      "path": path, "summary": summary, "sidechain": side,
                      "id": b.get("id"), "error": None, "elapsed_s": None,
                      "done": False}
                events.append(ev)
                if b.get("id"):
                    pending[b["id"]] = ev
            elif kind == "tool_result":
                ev = pending.pop(b.get("tool_use_id"), None)
                if ev is not None:
                    ev["done"] = True
                    # is_error is a FLAG, not the message it came with
                    ev["error"] = bool(b.get("is_error"))
                    ev["elapsed_s"] = _delta(ev["t"], ts)
            # 'text' and 'thinking' blocks are deliberately not read at all --
            # they are the message body, and section 7.2 forbids displaying it.
            elif kind == "text" and rec.get("type") == "user" and not side:
                # A USER record carrying text is a human turn -- the boundary
                # that gives every action after it a reason for existing. A
                # user record carrying tool_result blocks is just the agent's
                # own loop and is NOT a boundary. Only the boundary and its
                # time are taken; the prompt itself is a message body and is
                # never read.
                events.append({"t": ts, "kind": "turn", "tool": None,
                               "path": None, "summary": None,
                               "sidechain": side, "id": None,
                               "error": None, "elapsed_s": None, "done": True})
            elif kind == "text" and not side:
                events.append({"t": ts, "kind": "say", "tool": None,
                               "path": None, "summary": None,
                               "sidechain": side, "id": None,
                               "error": None, "elapsed_s": None, "done": True})
    return events


def _delta(a, b):
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            ta = time.strptime(a, fmt)
            tb = time.strptime(b, fmt)
            return max(0, int(time.mktime(tb) - time.mktime(ta)))
        except (TypeError, ValueError):
            continue
    return None


def read_events(path, budget=TAIL_BUDGET):
    """Tail one transcript into metadata events, cached by (size, mtime).

    Tail-only and bounded: 204 MB across this project's sessions must never be
    parsed eagerly, and the newest file grows while the agent is working.
    """
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (path, st.st_size, int(st.st_mtime))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        with open(path, "rb") as fh:
            if st.st_size > budget:
                fh.seek(st.st_size - budget)
                raw = fh.read()
                # the first line is almost certainly cut in half
                nl = raw.find(b"\n")
                raw = raw[nl + 1:] if nl >= 0 else b""
            else:
                raw = fh.read()
    except OSError:
        return []
    events = _events_from(raw.decode("utf-8", "replace").splitlines())
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = events
    return events


#: Extensions worth linking straight into the viewer -- the "the agent just
#: produced this, look at it now" join (section 7.3 item 2).
_ARTIFACT_EXT = (".png", ".svg", ".html", ".json", ".jsonl", ".gds", ".md",
                 ".rep", ".log", ".lyrdb", ".csv")


def now_state(events, stale_after=180):
    """What is happening this instant. -> {state, detail, tool, since_s}

    The question the terminal cannot answer during a 25-minute LVS: is it
    stuck, or is Calibre just slow? A tool call with no result yet IS the
    answer, and its age is the ETA nobody had.
    """
    if not events:
        return {"state": "idle", "detail": "no activity in the tail",
                "tool": None, "since_s": None}
    open_calls = [e for e in events if e["kind"] == "tool" and not e["done"]]
    if open_calls:
        e = open_calls[-1]
        age = _delta(e["t"], _now_iso())
        return {"state": "running", "tool": e["tool"], "since_s": age,
                "detail": "%s%s" % (e["tool"],
                                    " · " + label(e) if label(e) else "")}
    last = events[-1]
    age = _delta(last["t"], _now_iso())
    if last["kind"] == "say":
        return {"state": "waiting-on-you", "tool": None, "since_s": age,
                "detail": "the agent replied and is waiting"}
    if age is not None and age > stale_after:
        return {"state": "idle", "tool": None, "since_s": age,
                "detail": "last activity %d s ago" % age}
    return {"state": "working", "tool": last["tool"], "since_s": age,
            "detail": "between steps"}


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def thrash(events, min_repeats=3):
    """Repetition, which is the cheapest early warning there is.

    The vref decap closure took NINE coordinate-diagnosed iterations. A view
    that says "sixth edit to the same file, still failing" catches a wrong
    turn while intervening is still cheap. Pure derived analytics -- no new
    data, no new collection.
    """
    by_path, by_tool_path, fails = {}, {}, {}
    for e in events:
        if e["kind"] != "tool":
            continue
        p = e.get("path")
        if p:
            by_path[p] = by_path.get(p, 0) + 1
            k = (e["tool"], p)
            by_tool_path[k] = by_tool_path.get(k, 0) + 1
        if e.get("error"):
            key = p or e["tool"]
            fails[key] = fails.get(key, 0) + 1
    out = []
    for (tool, p), n in by_tool_path.items():
        if n >= min_repeats:
            out.append({"kind": "repeat", "tool": tool, "path": p, "n": n,
                        "why": "%s touched %s %d times" % (tool, _base(p), n)})
    for p, n in fails.items():
        if n >= 2:
            out.append({"kind": "failing", "tool": None, "path": p, "n": n,
                        "why": "%d failed calls against %s" % (n, _base(p))})
    out.sort(key=lambda x: -x["n"])
    return out[:12]


def _base(p):
    if not p:
        return "?"
    if "://" in p:
        return p[:60]                  # a URL has no useful basename
    return os.path.basename(p.rstrip("/\\")) or p[:40]


def label(ev):
    """What to show for a step: its target if it has one, else its own
    one-line description. A shell step has no file_path, so a path-only label
    rendered every Bash call as "?" -- which is most of the timeline."""
    return ev.get("path") or ev.get("summary") or ""


def fleet(events):
    """Sub-agent activity, from isSidechain. Invisible until they report back."""
    side = [e for e in events if e["sidechain"] and e["kind"] == "tool"]
    if not side:
        return {"n": 0, "tools": [], "open": 0}
    tools = {}
    for e in side:
        tools[e["tool"]] = tools.get(e["tool"], 0) + 1
    return {"n": len(side), "open": sum(1 for e in side if not e["done"]),
            "tools": sorted(tools.items(), key=lambda kv: -kv[1])[:8]}


def looked_at(events, limit=12):
    """Renders the agent actually opened. Principle 3 says renders are
    agent-readable; this is how you audit whether it looked before acting."""
    out = []
    for e in events:
        if e["kind"] == "tool" and e["tool"] in ("Read", "read_file") \
                and e.get("path") and e["path"].lower().endswith(
                    (".png", ".svg", ".jpg", ".gif")):
            out.append({"path": e["path"], "t": e["t"]})
    return out[-limit:]


def turns(events, limit=8):
    """Group actions under the human turn that caused them. -> [turn, ...]

    A flat list of "Bash · 3s / Edit · 0s / Bash · 12s" is a stream with no
    reason attached to any of it. Grouping restores the one piece of context
    the transcript actually carries: these fourteen actions all belong to the
    same request, they took nine minutes between them, they touched four
    files, and two of them failed.
    """
    out, cur = [], None
    for e in events:
        if e["kind"] == "turn":
            cur = {"started": e["t"], "ended": e["t"], "actions": [],
                   "files": [], "errors": 0, "dur_s": 0}
            out.append(cur)
            continue
        if e["kind"] != "tool":
            continue
        if cur is None:                # the tail began mid-turn
            cur = {"started": e["t"], "ended": e["t"], "actions": [],
                   "files": [], "errors": 0, "dur_s": 0, "partial": True}
            out.append(cur)
        cur["actions"].append(e)
        cur["ended"] = e["t"]
        if e.get("error"):
            cur["errors"] += 1
        p = e.get("path")
        if p and p not in cur["files"]:
            cur["files"].append(p)
    for t in out:
        t["dur_s"] = _delta(t["started"], t["ended"]) or 0
        t["n"] = len(t["actions"])
    return [t for t in out if t["actions"]][-limit:]


def summarize(path, budget=TAIL_BUDGET, timeline=40):
    """Everything the panel needs from one transcript, metadata only."""
    events = read_events(path, budget)
    recent = [e for e in events if e["kind"] == "tool"][-timeline:]
    return {
        "now": now_state(events),
        "thrash": thrash(events),
        "fleet": fleet(events),
        "looked_at": looked_at(events),
        "n_events": len(events),
        "turns": [{
            "started": t["started"], "ended": t["ended"], "dur_s": t["dur_s"],
            "n": t["n"], "errors": t["errors"],
            "files": [_base(f) for f in t["files"][:6]],
            "partial": t.get("partial", False),
            "actions": [{
                "t": a["t"], "tool": a["tool"], "path": a["path"],
                "summary": a["summary"], "error": a["error"],
                "elapsed_s": a["elapsed_s"], "done": a["done"],
                "sidechain": a["sidechain"],
                "artifact": bool(a.get("path") and
                                 a["path"].lower().endswith(_ARTIFACT_EXT)),
            } for a in t["actions"]],
        } for t in turns(events)],
        "timeline": [{
            "t": e["t"], "tool": e["tool"], "path": e["path"],
            "summary": e["summary"], "error": e["error"],
            "elapsed_s": e["elapsed_s"], "done": e["done"],
            "sidechain": e["sidechain"],
            "artifact": bool(e.get("path") and
                             e["path"].lower().endswith(_ARTIFACT_EXT)),
        } for e in recent],
    }


def _main(argv=None):
    import sys
    a = argv or sys.argv[1:]
    cwd = a[0] if a else os.getcwd()
    ss = sessions(cwd)
    if not ss:
        print("no transcripts for %s" % project_dir(cwd))
        return 1
    print("%d session(s); newest %s (%.1f MB)"
          % (len(ss), ss[0]["id"][:8], ss[0]["size"] / 1048576.0))
    s = summarize(ss[0]["path"])
    print("now      : %(state)s -- %(detail)s" % s["now"])
    print("events   : %d in the tail" % s["n_events"])
    if s["fleet"]["n"]:
        print("subagents: %d calls, %d open" % (s["fleet"]["n"],
                                                s["fleet"]["open"]))
    for t in s["thrash"]:
        print("thrash   : %s" % t["why"])
    print("\nlast steps:")
    for e in s["timeline"][-12:]:
        lab = e["path"] and _base(e["path"]) or (e["summary"] or "")
        print("   %-26s %-9s %s%s"
              % ((e["tool"] or "")[:26],
                 ("%ds" % e["elapsed_s"]) if e["elapsed_s"] is not None
                 else ("running" if not e["done"] else ""),
                 lab[:52], "  ERROR" if e["error"] else ""))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
