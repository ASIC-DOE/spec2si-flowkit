#!/usr/bin/env python3
"""Listing and type dispatch for the artifact browser -- no HTTP, no sockets.

Kept apart from server.py so the parts with actual logic (what kind of thing is
this, how much of it may be read, how is a directory summarised) are testable
without binding a port.
"""
import json
import os
import re

#: Never auto-read more than this from one file. The flow writes multi-GB
#: Calibre databases and 1.6 MB GDS next to 1 kB reports; a viewer that decides
#: at click time to slurp whichever it lands on is one keystroke from a stall.
BYTE_BUDGET = 512 * 1024

#: extension -> viewer kind. The kinds the phase-3 gate names are image, svg,
#: json, jsonl and text; the rest resolve to a kind the viewer degrades on.
KINDS = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".svg": "svg",
    ".json": "json",
    ".jsonl": "jsonl",
    ".html": "html", ".htm": "html",
    ".md": "text", ".txt": "text", ".log": "text", ".rep": "text",
    ".rpt": "text", ".out": "text", ".sum": "text", ".csv": "text",
    ".tcl": "text", ".py": "text", ".sh": "text", ".v": "text",
    ".sv": "text", ".spice": "text", ".cdl": "text", ".scs": "text",
    ".def": "text", ".lef": "text", ".il": "text", ".cfg": "text",
    ".lyrdb": "text", ".lyp": "text",
    # What the flows write that used to fall through to "unknown" -- every
    # one is plain text, and "unknown" reads as "cannot be shown" to the
    # person who just clicked it. Surveyed 2026-09-12 across the four ports:
    # xt011's signed `.route`/`.escape` plans and `.pins`/`.census` dumps,
    # the CDL/SPICE netlists and Verilog-A the publish step files, the
    # signoff decks and rule files, the timing constraints, the streamer's
    # config, and OA's `master.tag` beside every published view.
    ".route": "text", ".escape": "text", ".pins": "text", ".census": "text",
    ".spi": "text", ".net": "text", ".sp": "text", ".cir": "text",
    ".va": "text", ".pvl": "text", ".rul": "text", ".sdc": "text",
    ".strm": "text", ".ocn": "text", ".csh": "text", ".ps1": "text",
    ".tag": "text", ".map": "text", ".layermap": "text", ".yaml": "text",
    ".yml": "text", ".toml": "text", ".ini": "text", ".rst": "text",
    # Simulator output. A separate kind because the useful view is the CURVE
    # (or, for an operating point, the TABLE), and because these files are
    # read while they are still being written -- see analog/engine/wave.py.
    #
    # `.stb` is here on the same evidence as the rest: a real stability run
    # (`inv_vref_smoke/psf/stb1.stb`) is structurally an AC sweep -- `freq`,
    # grid 3, complex LOOPGAIN -- and there are 8700 of them on the cluster.
    #
    # `.op` is mapped, but the EXTENSION decides nothing: this flow writes its
    # operating point as `op1.dc`, and the reader tells a point from a sweep
    # by structure (no SWEEP section, no TRACE section). The mapping is here
    # so a file that IS named `.op` reaches the reader at all.
    #
    # `.raw` is ONR's characterisation output: `spectre -format nutascii -raw
    # <path>` writes a nutmeg rawfile, often with NO EXTENSION AT ALL. So the
    # extension gets a file to the reader when there is one, and `wave.read_any`
    # decides the actual format by content -- the same rule the operating point
    # forced, for the same reason.
    ".tran": "wave", ".dc": "wave", ".ac": "wave", ".noise": "wave",
    ".stb": "wave", ".op": "wave", ".raw": "wave",
    #
    # Periodic analyses, all verified against the real `ota_pss` bench before
    # being mapped -- the plan refused to add them on faith. Each reads as the
    # sweep its own SWEEP section declares: `td.pss` is time/linear, `fd.pss`
    # is freq/LINEAR (harmonics 0, f0, 2f0 -- not decades, and the file says
    # `grid 1`), `pac1.<h>.pac` is freq/log complex, `pnoise` is freq/log with
    # per-device structs. The bare `pac1.pac` is an INDEX over the eleven
    # per-harmonic files and is recognised as one by structure.
    ".pss": "wave", ".pac": "wave", ".pnoise": "wave",
    ".sweep": "wave", ".montecarlo": "wave",
    ".gds": "binary", ".oa": "binary", ".db": "binary", ".gz": "binary",
}


def kind_of(path):
    if os.path.isdir(path):
        return "dir"
    return kind_of_name(path)


def kind_of_name(name):
    """Viewer kind from the NAME alone -- no filesystem touched.

    The remote lister already knows what is a directory, and a cluster name
    must never be stat'ed locally: `os.path.isdir` on a bare remote name is
    answering a question about the wrong machine, and would occasionally
    answer it wrong (a remote file sharing a name with a local directory).
    """
    return KINDS.get(os.path.splitext(name)[1].lower(), "unknown")


def listdir(abspath, relpath=""):
    """[{name, rel, kind, size, mtime, is_dir}] -- directories first, then
    NEWEST FIRST.

    Newest-first because the question is almost always "what did the last run
    do", and a stamp dir sorted alphabetically buries today's run under three
    months of them.
    """
    out = []
    try:
        names = os.listdir(abspath)
    except OSError as exc:
        raise ValueError(str(exc))
    for n in names:
        p = os.path.join(abspath, n)
        try:
            st = os.stat(p)
            is_dir = os.path.isdir(p)
            out.append({"name": n,
                        "rel": (relpath + "/" + n).lstrip("/"),
                        "kind": "dir" if is_dir else kind_of(p),
                        "size": None if is_dir else st.st_size,
                        "mtime": int(st.st_mtime),
                        "is_dir": is_dir})
        except OSError:
            # a file that vanished between listdir and stat, or one we may not
            # stat: skip it rather than fail the whole listing
            continue
    out.sort(key=lambda e: (not e["is_dir"], -e["mtime"], e["name"].lower()))
    return out


def read_text(abspath, budget=BYTE_BUDGET):
    """(text, truncated_bool, size). Head-only within the budget.

    Decoded with errors='replace': these are tool logs, and a stray byte in a
    Calibre report must not turn into a 500.
    """
    size = os.path.getsize(abspath)
    with open(abspath, "rb") as fh:
        raw = fh.read(budget + 1)
    truncated = len(raw) > budget
    if truncated:
        raw = raw[:budget]
    return raw.decode("utf-8", "replace"), truncated, size


def read_json(abspath, budget=BYTE_BUDGET):
    """(obj_or_None, text, truncated, size).

    Returns the parsed object when it parses AND fits, so the viewer can render
    a tree; otherwise the raw head, so a malformed or oversized file still
    shows something instead of an error page.
    """
    text, truncated, size = read_text(abspath, budget)
    if truncated:
        return None, text, True, size
    try:
        return json.loads(text), text, False, size
    except ValueError:
        return None, text, False, size


def read_jsonl(abspath, budget=BYTE_BUDGET, max_rows=2000):
    """([row, ...], truncated, size) -- one parsed object per line.

    Unparsable lines are kept as {"_raw": line} rather than dropped: in an
    append-only log (verify_log.jsonl, events.jsonl) a corrupt line is a fact
    about the run, and silently hiding it is how you lose an hour.
    """
    text, truncated, size = read_text(abspath, budget)
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if len(rows) >= max_rows:
            truncated = True
            break
        try:
            rows.append(json.loads(ln))
        except ValueError:
            rows.append({"_raw": ln})
    return rows, truncated, size


#: Above this, do not walk a GDS at all. The record scan is linear and cheap,
#: but a chip-level stream is hundreds of MB and the viewer must not stall on a
#: click. Bigger files get the plain size card.
GDS_SCAN_LIMIT = 96 * 1024 * 1024

# GDS record types needed for a summary -- see render_gds.py for the full set.
_BGNSTR, _STRNAME, _BOUNDARY, _PATH, _SREF, _LAYER, _DATATYPE = (
    0x05, 0x06, 0x08, 0x09, 0x0A, 0x0D, 0x0E)
#: SNAME: the structure an SREF/AREF instantiates. A structure that no SNAME
#: names is a TOP cell -- derived from the stream, which is the only place it
#: is stated, rather than guessed from "the last one listed".
_SNAME = 0x12


def gds_summary(abspath, limit=GDS_SCAN_LIMIT):
    """What a GDS actually CONTAINS: cells, element counts, layers present.

    A hex dump of a layout is noise -- it answers nothing anybody opens a GDS
    to ask. The useful questions are "is this the cell I think it is", "did the
    stream come out empty", and "is M2 in here at all", and all three fall out
    of one linear record walk.

    Deliberately reimplemented here in ~30 lines rather than importing
    render_gds: that module pulls matplotlib at import time, and the browser is
    stdlib-only by design. It reads the same record stream, so the two agree
    about what a GDS holds without one depending on the other.
    """
    size = os.path.getsize(abspath)
    if size > limit:
        return {"too_big": True, "size": size}
    cells, layers = [], {}
    counts = {"boundary": 0, "path": 0, "sref": 0}
    referenced = set()
    cur_layer = None
    with open(abspath, "rb") as fh:
        data = fh.read()
    i, n = 0, len(data)
    while i + 4 <= n:
        rlen = int.from_bytes(data[i:i + 2], "big")
        rtyp = data[i + 2]
        if rlen < 4:
            break                       # malformed: stop rather than spin
        payload = data[i + 4:i + rlen]
        if rtyp == _STRNAME:
            cells.append(payload.rstrip(b"\x00").decode("ascii", "replace"))
        elif rtyp == _SNAME:
            referenced.add(payload.rstrip(b"\x00").decode("ascii", "replace"))
        elif rtyp == _BOUNDARY:
            counts["boundary"] += 1; cur_layer = None
        elif rtyp == _PATH:
            counts["path"] += 1; cur_layer = None
        elif rtyp == _SREF:
            counts["sref"] += 1
        elif rtyp == _LAYER and len(payload) >= 2:
            cur_layer = int.from_bytes(payload[:2], "big", signed=True)
        elif rtyp == _DATATYPE and len(payload) >= 2 and cur_layer is not None:
            dt = int.from_bytes(payload[:2], "big", signed=True)
            key = "%d/%d" % (cur_layer, dt)
            layers[key] = layers.get(key, 0) + 1
        i += rlen
    return {"too_big": False, "size": size, "cells": cells,
            "n_cells": len(cells), "counts": counts,
            "top": [c for c in cells if c not in referenced],
            "layers": sorted(layers.items(),
                             key=lambda kv: (-kv[1], kv[0]))}


def gds_top(abspath, limit=GDS_SCAN_LIMIT):
    """The structures nothing in the stream instantiates -> [names].

    One name is the usual answer and the one a renderer that must be TOLD
    the top cell (sky130's) needs. Zero or several is reported as such, so
    the caller refuses rather than picks: a stream with two tops rendered
    as one of them is a picture of half the file.
    """
    got = gds_summary(abspath, limit)
    if got.get("too_big"):
        return []
    return got["top"]


#: Files that say something about a whole directory, as (pattern, reader).
#: Order is precedence: a flowrun stamp holds both a report and a manifest,
#: and the report's verdict is the one that was signed off.
#:
#: The first three are tsmc65 flowrun conventions. Surveyed 2026-09-12, they
#: were the ONLY sources -- and xt011 and sky130 never write them, so both
#: ports browsed unbadged, with nothing to say so. The rest are what the
#: other ports do write: sky130's scored `result.json`/`pex.json`/
#: `schematic.json` (a `verdict` key), and the design record's `cell.json`
#: in all four. A repo adds its own through `roots.json`'s `badges` key
#: (`configure_badges`); a pattern with a `*` matches FILE entries only.
_BUILTIN_BADGE_SOURCES = (
    ("report.json", "report"),
    ("manifest.json", "manifest"),
    ("status.json", "status"),
    ("result.json", "verdict"),
    ("pex.json", "verdict"),
    ("schematic.json", "verdict"),
    ("cell.json", "cell"),
)
BADGE_SOURCES = tuple(_BUILTIN_BADGE_SOURCES)
#: The readers a `badges` declaration may name.
BADGE_READERS = ("report", "manifest", "status", "verdict", "cell",
                 "verdict_text")


def configure_badges(extra):
    """Add a repo's declared badge sources ({pattern: reader}) ahead of the
    built-ins, so a repo's own convention wins over a generic one. Raises on
    an unknown reader: a declaration that names a reader nobody wrote would
    otherwise be a badge that silently never appears."""
    global BADGE_SOURCES
    added = []
    for pat, reader in (extra or {}).items():
        if reader not in BADGE_READERS:
            raise ValueError("roots.json: badges[%r] = %r -- known readers: %s"
                             % (pat, reader, ", ".join(BADGE_READERS)))
        if not isinstance(pat, str) or not pat.strip() or "/" in pat:
            raise ValueError("roots.json: a badge source is a file NAME or "
                             "glob, not a path: %r" % (pat,))
        added.append((pat.strip().lower(), reader))
    BADGE_SOURCES = tuple(added) + tuple(_BUILTIN_BADGE_SOURCES)
    _BADGE_CACHE.clear()
    return BADGE_SOURCES


def badge_source_for(name):
    """The reader for a file NAME, or None if it is not a badge source."""
    import fnmatch
    base = os.path.basename(name).lower()
    for pat, reader in BADGE_SOURCES:
        if base == pat or ("*" in pat and fnmatch.fnmatchcase(base, pat)):
            return reader
    return None


def exact_badge_sources():
    """The sources a DIRECTORY is badged from: exact names only, in order.

    A pattern needs the directory listed to be applied and a directory badge
    is computed for every entry of a listing, so patterns apply to file
    entries only; this list is also what the cluster lister is told to inline.
    """
    return [pat for pat, _r in BADGE_SOURCES if "*" not in pat]

#: A badge must never cost more than the listing it decorates. Both caps are
#: about the same failure: `analog/work` holds hundreds of directories, and a
#: pane that opens a JSON in each one before painting is a pane that hangs.
BADGE_MAX_ENTRIES = 300
BADGE_READ_LIMIT = 2 * 1024 * 1024

#: (abspath, mtime, size) -> badges. Re-listing the same directory is the
#: common case (click into a stamp, click back), and the source files do not
#: change under us mid-session.
_BADGE_CACHE = {}


def _badge(text, tone):
    return {"text": text, "tone": tone}


def _report_badges(d):
    """Flowrun report.json -> verdict, DRC and LVS state.

    DRC and LVS are read out of `metrics.<stage>` rather than the top level
    because that is where the flow records them, and they are the two facts a
    PASS does not tell you: a run can be PASS overall with the layout stages
    never having run at all.
    """
    out = []
    verdict = d.get("verdict")
    if isinstance(verdict, str):
        out.append(_badge(verdict, "ok" if verdict.upper() == "PASS" else "bad"))
    metrics = d.get("metrics")
    if not isinstance(metrics, dict):
        return out
    drc, lvs = None, None
    for stage in metrics.values():
        if not isinstance(stage, dict):
            continue
        if "drc" in stage and drc is None:
            drc = stage["drc"]
        got = stage.get("lvs")
        if isinstance(got, dict) and "correct" in got and lvs is None:
            lvs = got["correct"]
    if isinstance(drc, str):
        out.append(_badge("DRC " + drc,
                          "ok" if drc.upper() == "CLEAN" else "bad"))
    elif isinstance(drc, int):
        # a count, not a word: 0 is the clean case and must not read as bad
        out.append(_badge("DRC %d" % drc, "ok" if drc == 0 else "bad"))
    if lvs is not None:
        out.append(_badge("LVS " + ("CORRECT" if lvs else "INCORRECT"),
                          "ok" if lvs else "bad"))
    return out


def _manifest_badges(d):
    """Stage manifest -> "n/m stages". One number, because the per-stage detail
    is one click away in the viewer and a listing row has no room for eight."""
    total = passed = 0
    for stage in d.values():
        if not isinstance(stage, dict) or "verdict" not in stage:
            continue
        total += 1
        if str(stage["verdict"]).upper() == "PASS":
            passed += 1
    if not total:
        return []
    return [_badge("%d/%d stages" % (passed, total),
                   "ok" if passed == total else "bad")]


#: job state -> tone. Anything unlisted is informational rather than assumed
#: good: an unrecognised state is exactly when you want to look.
_JOB_TONE = {"done": "ok", "ok": "ok", "failed": "bad", "error": "bad",
             "killed": "bad", "timeout": "bad", "running": "run",
             "queued": "info", "submitted": "info"}


def _status_badges(d):
    out = []
    state = d.get("state")
    if isinstance(state, str):
        out.append(_badge(state, _JOB_TONE.get(state.lower(), "info")))
    # WAITING_LICENSE is the state that looks like a hang and is not one --
    # jobs/remote.py already distinguishes it, so surface it rather than
    # letting a 25-minute licence wait read as a stuck job.
    if d.get("license"):
        out.append(_badge("WAITING_LICENSE", "warn"))
    return out


def _latest_badges(abspath):
    """A circuit directory badged from the run LATEST.txt points at.

    One indirection, and it is the whole question: `analog/results/flowruns`
    lists eight circuits, and without this the pane says only that they are
    directories. `run.py` writes LATEST.txt beside the stamps, so the newest
    verdict is one read away -- but it is the CHILD's verdict, so it is marked
    as such rather than presented as the directory's own.
    """
    p = os.path.join(abspath, "LATEST.txt")
    try:
        if os.path.getsize(p) > 4096:
            return []
        with open(p, encoding="utf-8", errors="replace") as fh:
            stamp = fh.read().strip()
    except OSError:
        return []
    # LATEST.txt is written by us, but it still names a path component: a
    # separator or a `..` in it would walk out of the directory being listed.
    if not stamp or "/" in stamp or "\\" in stamp or stamp.startswith("."):
        return []
    got = _badges_uncached(os.path.join(abspath, stamp), True)
    # marked, not merged: this is the CHILD's verdict, not this directory's
    return [_badge("latest", "info")] + got if got else []


#: Verdict words and their tones, for the generic readers. A word not here is
#: shown as informational rather than assumed either way.
_VERDICT_TONE = {"pass": "ok", "passed": "ok", "clean": "ok", "match": "ok",
                 "correct": "ok", "ok": "ok", "complete": "ok",
                 "fail": "bad", "failed": "bad", "mismatch": "bad",
                 "incorrect": "bad", "error": "bad", "abort": "bad",
                 "aborted": "bad", "incomplete": "bad", "not completed": "bad"}


def _verdict_badges(d):
    """A scored JSON with a `verdict` (sky130's result.json / pex.json /
    schematic.json, i2c's signoff) -> the verdict, plus DRC/LVS if the file
    states them at the top level or one level down."""
    out = []
    v = d.get("verdict")
    if isinstance(v, str) and v.strip():
        out.append(_badge(v.strip(), _VERDICT_TONE.get(v.strip().lower(), "info")))
    elif isinstance(d.get("pass"), bool):
        out.append(_badge("PASS" if d["pass"] else "FAIL",
                          "ok" if d["pass"] else "bad"))
    for key in ("drc", "lvs"):
        got = d.get(key)
        if isinstance(got, dict):
            got = got.get("verdict", got.get("status"))
        if isinstance(got, str) and got.strip():
            out.append(_badge("%s %s" % (key.upper(), got.strip()),
                              _VERDICT_TONE.get(got.strip().lower(), "info")))
        elif isinstance(got, bool):
            out.append(_badge("%s %s" % (key.upper(), "CLEAN" if got else "FAIL"),
                              "ok" if got else "bad"))
        elif isinstance(got, int) and not isinstance(got, bool):
            out.append(_badge("%s %d" % (key.upper(), got),
                              "ok" if got == 0 else "bad"))
    return out


_VERDICT_LINE = re.compile(
    r"\b(LVS\s+(?:MATCH|MISMATCH)|MATCH|MISMATCH|INCORRECT|CORRECT|PASS|FAIL"
    r"|Not Completed)\b")


def _verdict_text_badges(text):
    """A report whose verdict is a WORD on a line (an LVS report, a job log):
    the first recognised word wins, so a summary's own `MATCH` is not
    overruled by a later `MISMATCH` inside a per-cell table."""
    m = _VERDICT_LINE.search(text[:65536])
    if not m:
        return []
    word = m.group(1)
    return [_badge(word, _VERDICT_TONE.get(word.split()[-1].lower(), "info"))]


def _stem(name):
    """A view name minus its trailing generation digits: `layout2` -> `layout`.

    The designdb rule (db._stem in every port): only a view named like the
    bound one with a higher trailing number is a GENERATION of it; `power`
    and `supply` are two `route` plans, not two generations of one.
    """
    return re.sub(r"\d+$", "", name or "")


def _gen(name):
    m = re.search(r"(\d+)$", name or "")
    return int(m.group(1)) if m else 0


def cell_facts(d):
    """What a `cell.json` says, in the shape the badge and the viewer share.

    -> {library, cell, n_views, n_bound, bound: {type: view}, views: [row],
        newer: [(type, bound_view, newer_view)], bom: [...], bom_counts,
        bom_external: {...}}

    `newer` is the designdb `--check` finding re-derived here from the same
    rule -- a view of the bound view's NAME LINEAGE with a higher generation
    number, not adopted, and not filed as `superseded` -- because the browser
    cannot import a served repo's designdb and must not report a different
    answer from it.
    """
    views = d.get("views") if isinstance(d.get("views"), dict) else {}
    bound = d.get("bound") if isinstance(d.get("bound"), dict) else {}
    rows = []
    for name in sorted(views):
        v = views[name] if isinstance(views[name], dict) else {}
        files = v.get("files") if isinstance(v.get("files"), list) else []
        prov = v.get("provenance") if isinstance(v.get("provenance"), dict) else {}
        vtype = v.get("type") if isinstance(v.get("type"), str) else "?"
        rows.append({
            "view": name, "type": vtype,
            "bound": bound.get(vtype) == name,
            "n_files": len(files),
            "bytes": sum(int(f.get("bytes") or 0) for f in files
                         if isinstance(f, dict)),
            "files": [{"path": f.get("path"), "md5": f.get("md5"),
                       "bytes": f.get("bytes"), "origin": f.get("origin")}
                      for f in files if isinstance(f, dict)],
            "from": prov.get("from"),
            "superseded": bool(prov.get("superseded")),
            "provenance": prov,
        })
    newer = []
    for vtype, bname in sorted(bound.items()):
        if not isinstance(bname, str) or bname not in views:
            continue
        stem, gen = _stem(bname), _gen(bname)
        for r in rows:
            if (r["type"] == vtype and r["view"] != bname
                    and _stem(r["view"]) == stem and _gen(r["view"]) > gen
                    and not r["superseded"]):
                newer.append((vtype, bname, r["view"]))
    return {"library": d.get("library"), "cell": d.get("cell"),
            "n_views": len(rows), "n_bound": len(bound), "bound": bound,
            "views": rows, "newer": newer,
            "bom": d.get("bom") if isinstance(d.get("bom"), list) else [],
            "bom_counts": d.get("bom_counts")
            if isinstance(d.get("bom_counts"), dict) else {},
            "bom_external": d.get("bom_external")
            if isinstance(d.get("bom_external"), dict) else {}}


def _cell_badges(d):
    """A design-record cell.json -> `n bound / m views`, and the finding that
    matters: a newer generation of a bound view that nobody has adopted."""
    if not isinstance(d.get("views"), dict):
        return []
    f = cell_facts(d)
    out = [_badge("%d bound / %d views" % (f["n_bound"], f["n_views"]),
                  "info" if f["n_bound"] else "warn")]
    for vtype, bname, nname in f["newer"]:
        out.append(_badge("%s not adopted (binds %s)" % (nname, bname), "warn"))
    return out


_READERS = {"report": _report_badges, "status": _status_badges,
            "manifest": _manifest_badges, "verdict": _verdict_badges,
            "cell": _cell_badges}


def badges_from_text(name, text, latest=False):
    """Badges from the CONTENT of a badge source -- the ONE definition.

    Both readers land here: the local one after opening the file, the cluster
    one after the remote lister inlined it (cluster.py explains why it comes
    back with the listing rather than in N round trips). Because the parsing
    and the tone rules live here and nowhere else, a local PASS and a remote
    PASS cannot come to mean different things.
    """
    reader = badge_source_for(name)
    if reader is None:
        return []
    if reader == "verdict_text":
        got = _verdict_text_badges(text if isinstance(text, str) else "")
    else:
        try:
            d = json.loads(text)
        except (ValueError, TypeError):
            return []
        if not isinstance(d, dict):
            return []
        got = _READERS[reader](d)
    if got and latest:
        return [_badge("latest", "info")] + got
    return got


def _badges_uncached(abspath, is_dir):
    if is_dir:
        for name in exact_badge_sources():
            got = _badges_uncached(os.path.join(abspath, name), False)
            if got:
                return got
        return _latest_badges(abspath)
    if badge_source_for(abspath) is None:
        return []
    try:
        if os.path.getsize(abspath) > BADGE_READ_LIMIT:
            return []
        with open(abspath, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return badges_from_text(abspath, text)


def badges(abspath, is_dir=None):
    """[{text, tone}] read OUT OF the artifacts -- the listing's whole point.

    A file manager can already show names, sizes and times. What it cannot say
    is "this run is FAIL", "LVS INCORRECT" or "still waiting on a licence", and
    those are the only reasons anyone opens the directory.
    """
    if is_dir is None:
        is_dir = os.path.isdir(abspath)
    try:
        st = os.stat(abspath)
    except OSError:
        return []
    key = (abspath, int(st.st_mtime), st.st_size, is_dir)
    if key not in _BADGE_CACHE:
        # A directory's own mtime does not change when report.json is rewritten
        # in place, so its cache entry is keyed on the directory but computed
        # from the source file -- acceptable here because the flow writes a
        # stamp once and never edits it. Job status.json IS rewritten, and it
        # is a file entry, whose own mtime is in the key.
        _BADGE_CACHE[key] = _badges_uncached(abspath, is_dir)
    return _BADGE_CACHE[key]


def annotate(entries, parent_abspath, limit=BADGE_MAX_ENTRIES):
    """Add `badges` to each listing entry, in place, and return it.

    Separate from `listdir` so the cheap operation stays cheap: listing is
    stat-only and always completes, badging opens files and is capped. Past
    `limit` entries the rows still list, just undecorated -- a truncated
    decoration is better than a slow directory.
    """
    for i, e in enumerate(entries):
        if i >= limit:
            e["badges"] = []
            continue
        e["badges"] = badges(os.path.join(parent_abspath, e["name"]),
                             e["is_dir"])
    return entries


def badge_sources_present(entries):
    """Which badge sources this listing actually READ -- the names of the
    file entries that are sources, plus 'in <dir>' for each directory whose
    badge came from a source inside it. Empty means the listing had nothing
    to read, and the pane says so: an unbadged row used to read as "nothing
    to report", which on xt011 and sky130 was true of every row for a month
    because their result files were never sources at all."""
    out = []
    for e in entries:
        if not e.get("is_dir") and badge_source_for(e["name"]) is not None:
            out.append(e["name"])
        elif e.get("is_dir") and e.get("badges"):
            out.append("in " + e["name"] + "/")
    return out


# ------------------------------------------------------------ design record
#: The manifest tree, relative to a repo (docs/decisions/0001 in xt011 and
#: sky130, 0003 here, design_library_adoption in tsmc28 -- one shape).
DESIGN_DIR = "design"
#: Do not md5 a file bigger than this to ask which view it is. 32 MB hashes
#: in well under a second; a chip stream is hundreds and the answer is not
#: worth a stalled click -- the pane says the lookup was skipped and why.
DESIGN_MD5_LIMIT = 32 * 1024 * 1024
_DESIGN_CACHE = {}


def design_signature(repo):
    """(path, mtime, size) of every manifest under <repo>/design, as a tuple
    -- the cache key for everything derived from the tree, and the thing the
    check badge is keyed on. () when there is no tree."""
    base = os.path.join(repo, DESIGN_DIR)
    sig = []
    try:
        libs = sorted(os.listdir(base))
    except OSError:
        return ()
    for lib in libs:
        ld = os.path.join(base, lib)
        if not os.path.isdir(ld):
            continue
        for name in ("lib.json",):
            p = os.path.join(ld, name)
            try:
                st = os.stat(p)
                sig.append((lib + "/" + name, int(st.st_mtime), st.st_size))
            except OSError:
                pass
        try:
            cells = sorted(os.listdir(ld))
        except OSError:
            continue
        for cell in cells:
            p = os.path.join(ld, cell, "cell.json")
            try:
                st = os.stat(p)
            except OSError:
                continue
            sig.append((lib + "/" + cell + "/cell.json", int(st.st_mtime),
                        st.st_size))
    return tuple(sig)


def design_index(repo):
    """Everything the browser needs to know about a repo's design record.

    -> {"libraries": {lib: {"cells": [..], "project": str}},
        "cells": {"lib/cell": facts},          # cell_facts per manifest
        "md5": {md5: [hit, ...]},              # every file of every view
        "origin": {repo-relative origin: [hit, ...]},
        "signature": sig}

    A `hit` is {library, cell, view, type, bound, path, md5, bytes,
    origin}. Rebuilt only when a manifest changes (design_signature); the
    whole tree is a few dozen small JSON files, and reading them once per
    change is what lets "which captured view is this file" be answered on
    every click for free.
    """
    sig = design_signature(repo)
    hit = _DESIGN_CACHE.get(repo)
    if hit and hit["signature"] == sig:
        return hit
    base = os.path.join(repo, DESIGN_DIR)
    idx = {"libraries": {}, "cells": {}, "md5": {}, "origin": {},
           "signature": sig}
    for relp, _m, _s in sig:
        parts = relp.split("/")
        p = os.path.join(base, *parts)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        lib = parts[0]
        entry = idx["libraries"].setdefault(lib, {"cells": [], "project": ""})
        if parts[-1] == "lib.json":
            entry["project"] = str(d.get("project") or d.get("note") or "")
            continue
        cell = parts[1]
        entry["cells"].append(cell)
        facts = cell_facts(d)
        idx["cells"][lib + "/" + cell] = facts
        for r in facts["views"]:
            for f in r["files"]:
                h = {"library": lib, "cell": cell, "view": r["view"],
                     "type": r["type"], "bound": r["bound"],
                     "path": f.get("path"), "md5": f.get("md5"),
                     "bytes": f.get("bytes"), "origin": f.get("origin")}
                if isinstance(f.get("md5"), str):
                    idx["md5"].setdefault(f["md5"].lower(), []).append(h)
                o = f.get("origin")
                if isinstance(o, str) and o and not os.path.isabs(o) \
                        and not o.startswith("/"):
                    idx["origin"].setdefault(o.replace("\\", "/"), []).append(h)
    _DESIGN_CACHE[repo] = idx
    return idx


def file_md5(abspath, limit=DESIGN_MD5_LIMIT):
    """md5 hex of a file, or None past `limit`."""
    import hashlib
    if os.path.getsize(abspath) > limit:
        return None
    h = hashlib.md5()
    with open(abspath, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def which_view(repo, abspath, limit=DESIGN_MD5_LIMIT):
    """Which captured view, if any, THIS file is.

    -> {"skipped": reason} | {"md5", "hits": [hit], "origin_of": [hit]}

    Two joins, and they answer different questions. By md5: "these bytes ARE
    view X of cell Y" -- the question every signoff in xt011 turned into a
    shell loop over the manifests. By origin: "this repo-relative path is
    where view X was captured FROM", which with a differing md5 is the
    tracked-drift finding designdb's own check reports, seen from the file.
    """
    idx = design_index(repo)
    if not idx["signature"]:
        return {"skipped": "no design/ tree in this repo"}
    try:
        size = os.path.getsize(abspath)
    except OSError as exc:
        return {"skipped": str(exc)}
    origin_of = []
    try:
        rel = os.path.relpath(os.path.realpath(abspath),
                              os.path.realpath(repo)).replace("\\", "/")
        if not rel.startswith(".."):
            origin_of = idx["origin"].get(rel, [])
    except ValueError:
        pass
    if size > limit:
        return {"skipped": "%.0f MB is over the %.0f MB md5 limit"
                % (size / 1048576.0, limit / 1048576.0),
                "origin_of": origin_of}
    md5 = file_md5(abspath, limit)
    return {"md5": md5, "hits": idx["md5"].get(md5, []),
            "origin_of": origin_of}


#: The scratch OA library's name, read out of the served repo's own
#: `designdb/oa_dest.py` (`SCRATCH = "..."`) -- the one place it is declared.
_SCRATCH_RE = re.compile(r'^\s*SCRATCH\s*=\s*["\']([^"\']+)["\']', re.M)


def scratch_library(repo):
    p = os.path.join(repo, "designdb", "oa_dest.py")
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            m = _SCRATCH_RE.search(fh.read(65536))
    except OSError:
        return None
    return m.group(1) if m else None


def library_badges(name, repo):
    """A DIRECTORY named for a design library, or for the scratch OA library,
    says so -- on the cluster's `analog/oa/` listing that is the difference
    between a published cellview and a work copy, and nothing else in the
    listing states it. Exact name match only."""
    if not name or "/" in name:
        return []
    idx = design_index(repo)
    lib = idx["libraries"].get(name)
    if lib is not None:
        return [_badge("design library · %d cell%s"
                       % (len(lib["cells"]), "" if len(lib["cells"]) == 1 else "s"),
                       "info")]
    if name == scratch_library(repo):
        return [_badge("scratch OA library", "warn")]
    return []


def annotate_libraries(entries, repo):
    """Add the library-name badges to the directory entries, in place."""
    if not repo:
        return entries
    for e in entries:
        if e.get("is_dir"):
            got = library_badges(e["name"], repo)
            if got:
                e["badges"] = list(e.get("badges") or []) + got
    return entries


#: How many sibling directories to look through for a previous version of a
#: layout. Flowrun stamps accumulate; the useful comparison is against a recent
#: run, and scanning three months of them to populate a dropdown is not.
XOR_SIBLING_LIMIT = 12


#: Elements in a generated SVG carry `data-net`; that is the whole join.
_SVG_NET = re.compile(r'data-net="([^"]*)"')
#: Never walk further than this looking for a layout to pair with. A root can
#: be a whole repo, and a viewer must not turn one click into a filesystem
#: crawl -- the answer is worth a bounded search and no more.
CROSSPROBE_MAX_SCAN = 4000
CROSSPROBE_MAX_BYTES = 512 * 1024


def svg_nets(text, cap=4000):
    """Net names an inlined SVG tags its own shapes with. -> [names]"""
    out, seen = [], set()
    for m in _SVG_NET.finditer(text):
        n = m.group(1)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
            if len(out) >= cap:
                break
    return out


def abstract_nets(parsed):
    """Net names a layout abstract mentions, from every field that names one.

    Kept beside `svg_nets` because the cross-probe is exactly the claim that
    these two return comparable things. They do -- both sides were emitted
    from the same netlist -- and the measurement that says so is in §10.13:
    on the real artifacts every schematic matches its own layout on ALL of
    its nets (ota 8/8, lvds 7/7, lif 7/7, fc 15/15) and no other layout.
    """
    nets = set()
    if not isinstance(parsed, dict):
        return nets
    for key in ("rails", "tracks", "pins", "m3", "nets"):
        v = parsed.get(key)
        if isinstance(v, dict):
            nets |= {k for k in v if isinstance(k, str)}
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, dict) and isinstance(x.get("net"), str):
                    nets.add(x["net"])
                elif isinstance(x, str):
                    nets.add(x)
    return nets


def index_siblings(abspath, rel, limit=64):
    """Files that an index file indexes. -> [{label, rel}]

    `pac1.pac` indexes `pac1.-5.pac` .. `pac1.5.pac`: same stem, same
    extension, one extra dotted part in between. Discovered from the
    directory rather than from the index's own values, because the values say
    what was swept and only the filesystem says what actually got written --
    a killed run has fewer files than harmonics.
    """
    d = os.path.dirname(abspath)
    base = os.path.basename(abspath)
    stem, ext = os.path.splitext(base)
    if not stem or not ext:
        return []
    reldir = os.path.dirname(rel.replace("\\", "/"))
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for n in sorted(names):
        if n == base or not n.endswith(ext) or not n.startswith(stem + "."):
            continue
        mid = n[len(stem) + 1:-len(ext)]
        if not mid or "." in mid:
            continue
        out.append({"label": mid, "rel": (reldir + "/" + n).lstrip("/")})
        if len(out) >= limit:
            break
    # numeric where they are numbers (-5..5 sorts wrong as text)
    def key(r):
        try:
            return (0, float(r["label"]))
        except ValueError:
            return (1, 0.0)
    out.sort(key=key)
    return out


def crossprobe_candidates(root_path, nets, limit=8):
    """Layout abstracts under `root_path` that share nets with `nets`.

    -> [{rel, name, shared, total}] best first.

    THE JOIN IS THE NET NAME and nothing else -- no coordinates, no cell-name
    convention, no index built ahead of time. That is what makes §6.4 "free":
    both sides were generated from one netlist, so the names already agree,
    and a pairing that scores zero shared nets is correctly not offered rather
    than guessed at from a filename that happens to look similar.
    """
    want = set(nets)
    if not want:
        return []
    seen = 0
    out = []
    for dirpath, dirnames, files in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in files:
            seen += 1
            if seen > CROSSPROBE_MAX_SCAN:
                dirnames[:] = []
                break
            if not fn.endswith(".abstract.json"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(p) > CROSSPROBE_MAX_BYTES:
                    continue
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    parsed = json.load(fh)
            except (OSError, ValueError):
                continue
            have = abstract_nets(parsed)
            shared = len(want & have)
            if not shared:
                continue
            out.append({"rel": os.path.relpath(p, root_path).replace("\\", "/"),
                        "name": fn[:-len(".abstract.json")],
                        "shared": shared, "total": len(want)})
        if seen > CROSSPROBE_MAX_SCAN:
            break
    out.sort(key=lambda r: (-r["shared"], r["name"]))
    return out[:limit]


def xor_candidates(abspath, rel):
    """What this layout could sensibly be XOR'd against. -> [{label, rel}]

    Two sources, in the order they answer the question:

    1. **The same file name in a sibling directory**, newest first. This is
       the regression comparison the policy actually asks for -- "geometry-
       identical before any engine change lands" means *this* cell, *this*
       run, against the run before it, and flowrun stamps are exactly that
       shape.
    2. Other layouts in the same directory, for the ad-hoc comparison
       (a variant against its baseline).

    Returns paths RELATIVE to the same root, so the caller confines them
    through the identical `roots.resolve` the original path went through --
    a compare target is a request like any other and gets no special trust.
    """
    out, seen = [], set()
    name = os.path.basename(abspath)
    d = os.path.dirname(abspath)
    reldir = os.path.dirname(rel.replace("\\", "/"))
    parent = os.path.dirname(d)
    relparent = os.path.dirname(reldir)

    try:
        sibs = sorted(
            (s for s in os.listdir(parent)
             if os.path.isdir(os.path.join(parent, s))),
            key=lambda s: os.path.getmtime(os.path.join(parent, s)),
            reverse=True)
    except OSError:
        sibs = []
    for s in sibs[:XOR_SIBLING_LIMIT]:
        cand = os.path.join(parent, s, name)
        if os.path.realpath(cand) == os.path.realpath(abspath):
            continue                            # itself, not a comparison
        if not os.path.isfile(cand):
            continue
        r = ((relparent + "/" if relparent else "") + s + "/" + name)
        if r in seen:
            continue
        seen.add(r)
        out.append({"label": s + "/" + name, "rel": r})

    try:
        here = sorted(n for n in os.listdir(d) if n.lower().endswith(".gds"))
    except OSError:
        here = []
    for n in here:
        if n == name:
            continue
        r = (reldir + "/" if reldir else "") + n
        if r in seen:
            continue
        seen.add(r)
        out.append({"label": n, "rel": r})
    return out


def head_tail(abspath, budget=BYTE_BUDGET):
    """(head_bytes, tail_bytes, size) for a binary. Never the whole file."""
    size = os.path.getsize(abspath)
    half = max(1, budget // 2)
    with open(abspath, "rb") as fh:
        head = fh.read(half)
        if size > budget:
            fh.seek(max(0, size - half))
            tail = fh.read(half)
        else:
            tail = b""
    return head, tail, size
