#!/usr/bin/env python3
"""Cluster-side reads for the artifact browser -- listing, fetch, render.

Phase 4 of docs/visual_interaction_plan.md. Everything goes through
`deployment/bnl/jobs/remote.py` (plan principle 5): there are already three
copies of the sync idea plus ~75 ad-hoc scripts, and this is emphatically not
the fourth. What that buys, for free, is the whole invariant list -- scripts on
stdin instead of composed shell strings, LF/BOM normalization, a hard
wall-clock timeout, stdout-only envelope parsing, the autofs ENOENT retry, and
a tri-state answer in which "I could not reach the cluster" never collapses
into "there is nothing there".

Four reads, four scripts:

  list    one directory, with the badge sources inlined (see below)
  fetch   one file, size-capped, base64 so a binary survives the envelope
  render  a GDS rendered ON THE CLUSTER, shipping back only the PNG
  wave    a transient PARSED AND PLOTTED on the cluster, shipping back the SVG

The last two are what the plan insists on (section 5.3): never pull a multi-GB
tree to look at it. They differ in one instructive way. The renderer is
LOCATED on the cluster -- `render_gds.py` imports matplotlib, so it has to run
where its dependencies are, and it is the same file the flow itself runs. The
waveform reader is SHIPPED: `wave.py` is stdlib-only and 18 kB, and sending it
buys two things worth more than the bytes.

  1. The cluster checkout does not have it. Measured, not assumed: `wave.py`
     was written on 2026-08-01 and `~/Documents/ms_pilot/analog/engine/` has
     no copy; ONR's checkout has none either. A feature that only works when
     the remote checkout is current is a feature that breaks silently.
  2. The picture is drawn by BYTE-IDENTICAL code. This is the badge argument
     (below) applied to plots: the partial-file rule -- drop the last time
     group while the file is unterminated -- must not be able to differ
     between a local run and a cluster run, because the whole point of the
     view is judging a run that has not finished.

WHY THE BADGE SOURCES COME BACK WITH THE LISTING. Badging a remote directory
the way the local one is badged would be one ssh round trip per entry: a
directory of 200 stamps would be 200 round trips. Instead the lister inlines
the (small, known-named) badge files it finds, and the SAME `model` functions
compute the badges locally. Local and remote badges therefore cannot disagree
about what a verdict means -- there is one implementation, as principle 4
requires.

Usage:
  python3 cluster.py <host> <root> [rel]
"""
import base64
import hashlib
import json
import os
import sys
import threading
import time
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import roots as _roots                                 # noqa: E402

#: The repo being SERVED. The transport is searched for under BOTH it and the
#: checkout this package lives in, because either may hold it.
REPO = _roots.REPO
_PKG_REPO = os.path.dirname(_HERE)

#: Where the hardened transport lives, in search order: the served repo's own
#: `deployment/bnl/jobs/`, then the one beside this package.
#:
#: Until 2026-09-12 this list went on to reach into a SIBLING checkout
#: (`spec2si-tsmc65`, `ms_pilot`), because only one port carried the
#: transport and the browser was served across repos from that one. The
#: transport is vendored from the flowkit into every port now, beside this
#: package, so a repo that cannot find it has a vendoring gap -- and the
#: error names the directories it looked in rather than quietly borrowing a
#: neighbour's. `ASICJOBS_HOME` still overrides, for a checkout laid out some
#: other way.
def _transport_dirs():
    out = []
    env = os.environ.get("ASICJOBS_HOME")
    if env:
        out.append(env)
    out.append(os.path.join(REPO, "deployment", "bnl", "jobs"))
    if _PKG_REPO != REPO:
        out.append(os.path.join(_PKG_REPO, "deployment", "bnl", "jobs"))
    # the flowkit's own layout, where the package is the source rather than a
    # vendored copy: the transport sits at `jobs/` beside `browse/`
    out.append(os.path.join(_PKG_REPO, "jobs"))
    return out


TRANSPORT_DIR = None
_remote = None
for _d in _transport_dirs():
    if os.path.exists(os.path.join(_d, "remote.py")):
        if _d not in sys.path:
            sys.path.insert(0, _d)
        try:
            import remote as _remote                  # noqa: F401
            TRANSPORT_DIR = _d
        except ImportError:                           # pragma: no cover
            _remote = None
        break

#: Wall clock for a listing or a fetch. Generous next to the ~0.1-0.4 s a
#: multiplexed round trip actually costs, because the tail is NFS, not ssh.
DEFAULT_TIMEOUT = 25.0
#: A cluster render is a full matplotlib run over a flattened hierarchy; the
#: 1.65 MB ctrl2 pilot takes ~70 s locally and the cluster boxes are not
#: faster per core.
RENDER_TIMEOUT = 300.0

#: Never ship more than this in one fetch envelope. base64 inflates by 4/3,
#: so 512 kB of file is ~683 kB on the wire.
FETCH_BUDGET = 512 * 1024
#: Badge sources inlined with a listing: at most this many entries, and only
#: files at most this big. A stamp's report.json is 2-8 kB, so 64 entries is
#: well under half a megabyte -- and past the cap the rows still list, exactly
#: as they do locally past `model.BADGE_MAX_ENTRIES`.
BADGE_MAX_ENTRIES = 64
BADGE_MAX_BYTES = 16 * 1024

#: Head budget for a remote transient. Bigger than FETCH_BUDGET by three
#: orders of magnitude, and it can be: the file is parsed ON the cluster and
#: only the picture comes back. Measured on asic7's sar_kernel PEX run --
#: 38.1 MB parsed in 2.25 s, plotted in 0.68 s, 3.66 s wall including ssh.
#:
#: A HEAD, not a tail, and that is the whole difference from every other read
#: in this file: a waveform needs t=0, and the reason to look at a growing one
#: is to see how it STARTED and decide whether to let it finish.
WAVE_BUDGET = 64 * 1024 * 1024
#: Wall clock for one. The parse is linear and the transfer is small; this is
#: long enough for a 64 MB head on a loaded box.
WAVE_TIMEOUT = 120.0

#: Listing cache TTL. An ssh round trip is fast but not free, and clicking
#: back up a tree re-lists the same directory; the plan asks for a TTL plus an
#: explicit refresh rather than a live read on every click.
LIST_TTL = 20.0

_CACHE = {}
_CACHE_GUARD = threading.Lock()


class RemoteError(Exception):
    """A cluster read that did not come back KNOWN.

    Deliberately distinct from a local OSError: the UI must be able to say
    "the cluster did not answer" rather than "the file is not there", which
    is remote.py's invariant 5 surfacing all the way to the pane.
    """


# --------------------------------------------------------------- script build

def _heredoc(var, value, tag):
    """Bind a shell variable to an arbitrary value with ZERO interpolation.

    The value goes inside a QUOTED heredoc, so no expansion, no word
    splitting, no metacharacter has any meaning -- the same technique
    remote.py's own bundle installer uses to embed file content. The
    delimiter carries a hash of the value, so it cannot collide with a line
    of the value itself.

    This is why there is no charset whitelist on cluster paths here.
    `_SAFE_PATH` in remote.py exists for tokens dropped into a launcher
    unquoted; a heredoc is strictly stronger, and a path with a space in it
    is a fact about the cluster, not a hostile input to be rejected.
    """
    h = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    delim = "EOF_%s_%s_%s" % (tag, h, len(value))
    return "%s=$(cat <<'%s'\n%s\n%s\n)\n" % (var, delim, value, delim)


def _script(py, **binds):
    """A POSIX sh wrapper that exports the bound values and runs `py`.

    python3 on the cluster rather than awk: remote.py's own shipped bundle
    already depends on remote python3 (progress.py, jobrec.py), and JSON
    escaping, base64 and realpath confinement in awk would be a re-derivation
    of things python has correct. The values reach python through the
    ENVIRONMENT, never through its source text.
    """
    parts = ["#!/bin/sh", "set -eu"]
    exports = []
    for var, value in sorted(binds.items()):
        if "\n" in value or "\r" in value:
            # A newline would end the heredoc line-wise; nothing in a cluster
            # path legitimately contains one, so refuse rather than mangle.
            raise ValueError("newline in remote path: %r" % value)
        parts.append(_heredoc(var, value, var))
        exports.append('%s="$%s"' % (var, var))
    parts.append("export " + " ".join(exports))
    h = hashlib.sha256(py.encode("utf-8")).hexdigest()[:8]
    parts.append("exec python3 - <<'EOF_PY_%s'\n%s\nEOF_PY_%s" % (h, py, h))
    return "\n".join(parts) + "\n"


#: Shared by every remote script: resolve REL under ROOT and refuse an escape.
#: Byte-for-byte the local rule in roots.resolve -- realpath BOTH sides then
#: commonpath, so `..`, an absolute path and a symlink all fail here too. The
#: local side does its own cheap check first; this one is authoritative,
#: because only the cluster can resolve a cluster symlink.
_PY_RESOLVE = r'''
import base64, json, os, sys

def emit(**kw):
    kw["schema"] = 1
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.exit(0)

def resolve():
    # expanduser BEFORE realpath: the heredoc delivers the configured path
    # literally (that is the point), so a leading ~ arrives as a character.
    # Roots are naturally written "~/Documents/ms_pilot" -- the cluster home
    # differs per host and must not be baked into roots.json.
    root = os.path.realpath(os.path.expanduser(os.environ["BROWSE_ROOT"]))
    rel = os.environ.get("BROWSE_REL", "")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        emit(kind="error", error="absolute path refused")
    target = os.path.realpath(os.path.join(root, rel))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:
        inside = False
    if not inside:
        emit(kind="error", error="path escapes the root")
    return root, target
'''

_PY_LIST = _PY_RESOLVE + r'''
# The sources are the LOCAL model's list, handed over in the environment, so
# a file that badges a local directory badges a cluster one too -- one
# definition (model.exact_badge_sources), not a second list kept in step.
BADGE_NAMES = tuple(n for n in os.environ.get("BROWSE_BADGE_NAMES", "").split(",")
                    if n) or ("report.json", "manifest.json", "status.json")
MAXE = int(os.environ.get("BROWSE_BADGE_ENTRIES", "64"))
MAXB = int(os.environ.get("BROWSE_BADGE_BYTES", "65536"))

root, target = resolve()
if not os.path.isdir(target):
    emit(kind="error", error="not a directory")

entries, badges = [], {}
try:
    names = os.listdir(target)
except OSError as exc:
    emit(kind="error", error=str(exc))

for n in names:
    p = os.path.join(target, n)
    try:
        st = os.stat(p)
        isdir = os.path.isdir(p)
    except OSError:
        continue
    entries.append({"name": n, "is_dir": isdir,
                    "size": None if isdir else st.st_size,
                    "mtime": int(st.st_mtime)})

entries.sort(key=lambda e: (not e["is_dir"], -e["mtime"], e["name"].lower()))

# Inline the badge sources for the first MAXE entries, in the SAME order the
# pane will show them, so the cap truncates the bottom of the list rather than
# an arbitrary subset.
for e in entries[:MAXE]:
    p = os.path.join(target, e["name"])
    cands = []
    if e["is_dir"]:
        cands = [(nm, os.path.join(p, nm)) for nm in BADGE_NAMES]
        lt = os.path.join(p, "LATEST.txt")
        try:
            if os.path.getsize(lt) <= 4096:
                with open(lt) as fh:
                    stamp = fh.read().strip()
                if stamp and "/" not in stamp and not stamp.startswith("."):
                    cands += [(nm, os.path.join(p, stamp, nm))
                              for nm in BADGE_NAMES]
        except (OSError, ValueError):
            pass
    elif e["name"] in BADGE_NAMES:
        cands = [(e["name"], p)]
    for nm, src in cands:
        try:
            if os.path.getsize(src) > MAXB:
                continue
            with open(src, "rb") as fh:
                raw = fh.read(MAXB)
        except OSError:
            continue
        key = e["name"] + "\x00" + nm
        # `latest` marks a verdict read through LATEST.txt, so the pane can
        # label it as the CHILD's, exactly as the local reader does.
        badges[key] = {"text": raw.decode("utf-8", "replace"),
                       "latest": src != os.path.join(p, nm) and e["is_dir"]}
        break

emit(kind="list", root=root, path=target, entries=entries, badges=badges,
     badge_cap=MAXE)
'''

_PY_FETCH = _PY_RESOLVE + r'''
budget = int(os.environ.get("BROWSE_BUDGET", "524288"))
root, target = resolve()
if os.path.isdir(target):
    emit(kind="error", error="is a directory")
try:
    size = os.path.getsize(target)
    with open(target, "rb") as fh:
        raw = fh.read(budget + 1)
except OSError as exc:
    emit(kind="error", error=str(exc))
truncated = len(raw) > budget
raw = raw[:budget]
emit(kind="file", size=size, truncated=truncated, shown=len(raw),
     b64=base64.b64encode(raw).decode("ascii"))
'''

#: The render runs where the layout is. Only the PNG comes back.
#:
#: The renderer is found relative to the ROOT's repo rather than assumed at a
#: fixed path, because the cluster checkout lives at a different place per
#: host and per repo (ms_pilot vs onr_t28). If it is not found the answer says
#: so, instead of a traceback about a missing module.
_PY_RENDER = _PY_RESOLVE + r'''
import subprocess, tempfile

root, target = resolve()
if not target.lower().endswith(".gds"):
    emit(kind="error", error="not a layout")
limit = int(os.environ.get("BROWSE_RENDER_LIMIT", "33554432"))
try:
    size = os.path.getsize(target)
except OSError as exc:
    emit(kind="error", error=str(exc))
if size > limit:
    emit(kind="error",
         error="%.1f MB is over the remote render limit" % (size / 1048576.0))

renderer = os.environ.get("BROWSE_RENDERER", "")
# The repo-relative places a renderer may be, DECLARED first (the served
# repo's roots.json `renderer`, handed over as BROWSE_RENDERER_REL) and then
# the two conventional ones -- the same list tools.py resolves locally.
rels = [r for r in os.environ.get("BROWSE_RENDERER_REL", "").split(":") if r]
rels += ["analog/engine/layout/render_gds.py", "analog/layout/render_gds.py"]
if not renderer or not os.path.exists(renderer):
    # Walk up from the layout looking for the checkout, so one root entry
    # works for every flow tree under it.
    d = os.path.dirname(target)
    renderer = ""
    while d and d != "/" and not renderer:
        for r in rels:
            cand = os.path.join(d, *r.split("/"))
            if os.path.exists(cand):
                renderer = cand
                break
        d = os.path.dirname(d)
if not renderer:
    emit(kind="error", error="no renderer found above the layout (looked for %s)"
         % ", ".join(rels))

# How it is called: the argv template the repo declared, else <gds> <out>.
# `{top}` is the one structure nothing in the stream instantiates, derived
# here from the SNAME records because a renderer that must be told the top
# cell (sky130's) cannot be handed a guess.
argv_t = json.loads(os.environ.get("BROWSE_RENDER_ARGV") or '["{gds}", "{out}"]')
top = ""
if "{top}" in argv_t:
    names, refd = [], set()
    with open(target, "rb") as fh:
        data = fh.read()
    i, n = 0, len(data)
    while i + 4 <= n:
        rlen = int.from_bytes(data[i:i + 2], "big")
        rtyp = data[i + 2]
        if rlen < 4:
            break
        pl = data[i + 4:i + rlen].rstrip(b"\x00")
        if rtyp == 0x06:
            names.append(pl.decode("ascii", "replace"))
        elif rtyp == 0x12:
            refd.add(pl.decode("ascii", "replace"))
        i += rlen
    tops = [c for c in names if c not in refd]
    if len(tops) != 1:
        emit(kind="error", error="the renderer needs ONE top cell; the stream "
             "has %d unreferenced structure(s): %s" % (len(tops), ", ".join(tops[:6])))
    top = tops[0]

py = os.environ.get("BROWSE_PYTHON") or ""
if not py:
    # The system python3 is 3.6.8 with no matplotlib; miniforge is where the
    # scientific stack lives (plan section 2). Prefer it, fall back honestly.
    for cand in (os.path.expanduser("~/miniforge3/bin/python3"),
                 os.path.expanduser("~/miniforge3/bin/python"), "python3"):
        if cand == "python3" or os.path.exists(cand):
            py = cand
            break

out = os.path.join(tempfile.mkdtemp(prefix="browse_render_"), "r.png")
subst = {"{gds}": target, "{out}": out, "{top}": top}
try:
    proc = subprocess.Popen([py, renderer] + [subst.get(a, a) for a in argv_t],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = proc.communicate()[0].decode("utf-8", "replace")
except OSError as exc:
    emit(kind="error", error="%s: %s" % (py, exc))
if proc.returncode != 0 or not os.path.exists(out):
    emit(kind="error", error=(log[-800:] or "renderer exited %d"
                              % proc.returncode), python=py, renderer=renderer)
with open(out, "rb") as fh:
    png = fh.read()
os.unlink(out)
os.rmdir(os.path.dirname(out))
emit(kind="render", size=len(png), gds_size=size, python=py,
     renderer=renderer, b64=base64.b64encode(png).decode("ascii"))
'''


#: The transient reader, run on the cluster from source we ship.
#:
#: The source arrives base64-encoded in ONE line, because `_script` refuses a
#: newline in a bound value -- that guard is right for paths and there is no
#: reason to weaken it for this. It is the same technique remote.py uses to
#: put a command on the wire.
#:
#: THE STAT COMES FIRST AND CAN STOP THE READ. `BROWSE_GATE_BYTES` is the size
#: over which the caller wants to be asked rather than kept waiting; past it
#: the script emits what it knows and does nothing else. That is deliberately
#: one round trip: statting from here and then deciding would be two, for a
#: question whose answer is usually "go ahead".
_PY_WAVE = _PY_RESOLVE + r'''
import time, zlib
root, target = resolve()
try:
    st = os.stat(target)
except OSError as exc:
    emit(kind="error", error=str(exc))
size, mtime = st.st_size, int(st.st_mtime)
budget = int(os.environ.get("BROWSE_WAVE_BUDGET", "67108864"))
gate = int(os.environ.get("BROWSE_GATE_BYTES", "0"))
if gate and min(size, budget) > gate:
    emit(kind="gated", size=size, mtime=mtime, reads=min(size, budget))

view = json.loads(os.environ.get("BROWSE_VIEW") or "{}")
ns = {"__name__": "browse_wave"}
src = base64.b64decode(os.environ["BROWSE_WAVE_SRC"]).decode("utf-8")
exec(compile(src, "wave.py", "exec"), ns)

t1 = time.time()
w = ns["read_any"](target, budget=budget)
t2 = time.time()
part = view.get("part") or "db"
if w.is_point:
    # An operating point is a table -- same answer as the local path, which
    # builds no picture either. Two behaviours for one file kind is how a
    # "why is this empty here and not there" hunt starts.
    emit(kind="wave", size=size, mtime=mtime, reads=w.bytes_read,
         summary=w.summary(part), dropped=0, drawn=[], svg_z="",
         xlog=False, ylog=False, svg_bytes=0, t_parse=round(t2 - t1, 3),
         t_plot=0.0, py=sys.version.split()[0])
# `signals` is None or a LIST -- `or None` here would turn "the user unticked
# everything" back into "draw a capped default", which is the one thing the
# checkbox must not do.
sigs = view.get("signals")
names, dropped = ns["choose"](w, sigs, part=part)
svg = ns["render"](w, names, mode=view.get("mode") or "group", part=part,
                   xlog=view.get("xlog"), ylog=view.get("ylog"),
                   x0=view.get("x0"), x1=view.get("x1"),
                   y0=view.get("y0"), y1=view.get("y1"))
summary = w.summary(part)
dxl, dyl = ns["default_scales"](w, part)
# zlib before base64: an SVG is repetitive path text and compresses ~6.5x on
# the real 73-trace file (1.29 MB -> 198 kB on the wire), which matters when
# the watch checkbox asks for it again every few seconds.
blob = base64.b64encode(zlib.compress(svg.encode("utf-8"), 6)).decode("ascii")
emit(kind="wave", size=size, mtime=mtime, reads=w.bytes_read,
     summary=summary, dropped=dropped, drawn=names, svg_z=blob,
     xlog=dxl if view.get("xlog") is None else view["xlog"],
     ylog=dyl if view.get("ylog") is None else view["ylog"],
     svg_bytes=len(svg), t_parse=round(t2 - t1, 3),
     t_plot=round(time.time() - t2, 3), py=sys.version.split()[0])
'''


# ------------------------------------------------------------------ transport

def _run(host, script, timeout):
    """One cluster read -> the envelope dict. Raises RemoteError otherwise."""
    if _remote is None:
        raise RemoteError(
            "no cluster transport found -- looked in %s. Set ASICJOBS_HOME to "
            "a checkout's deployment/bnl/jobs. (It is located, never copied: "
            "one transport, plan principle 5.)"
            % ", ".join(_transport_dirs()))
    res = _remote.Transport(host=host, timeout=timeout).run_sh(script)
    if not res.ok:
        # STALE and UNKNOWN both land here. Neither is "the file is missing":
        # the message says what actually happened so the pane can too.
        raise RemoteError("%s: %s" % (res.status, res.reason or "no reason"))
    data = res.data or {}
    if data.get("kind") == "error":
        raise RemoteError(data.get("error") or "remote error")
    return data


def listdir(host, root_path, rel="", timeout=DEFAULT_TIMEOUT, ttl=LIST_TTL,
            refresh=False, fs=None, badge_names=None):
    """Remote listing, TTL-cached. -> (entries, badge_sources, cached_bool)

    `badge_sources` maps entry name -> {text, latest}; the CALLER turns those
    into badges with the local `model` functions, so there is one definition
    of what a verdict looks like. `badge_names` is which files to inline --
    the caller hands over `model.exact_badge_sources()` for the same reason.
    """
    # Keyed on the FILESYSTEM, not the host: the cluster homes are shared, so
    # the same directory read through an asic6 root and an asic7 root is the
    # same directory. `fs` defaults to the host, so a site that does NOT share
    # keeps per-host caching by doing nothing.
    key = (fs or host, root_path, rel)
    now = time.time()
    if not refresh:
        with _CACHE_GUARD:
            hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], hit[2], True
    binds = {"BROWSE_ROOT": root_path, "BROWSE_REL": rel}
    if badge_names:
        binds["BROWSE_BADGE_NAMES"] = ",".join(badge_names)
    data = _run(host, _script(_PY_LIST, **binds), timeout)
    entries = data.get("entries") or []
    raw = data.get("badges") or {}
    badges = {}
    for key_str, val in raw.items():
        # the remote key is "<entry>\0<source filename>" -- WHICH source it
        # was decides how it is read, so keep it rather than the name alone
        name, _, source = key_str.partition("\x00")
        val["source"] = source
        badges[name] = val
    with _CACHE_GUARD:
        _CACHE[key] = (now, entries, badges)
    return entries, badges, False


def fetch(host, root_path, rel, budget=FETCH_BUDGET, timeout=DEFAULT_TIMEOUT):
    """Size-capped remote read. -> (bytes, truncated, size)"""
    data = _run(host, _script(_PY_FETCH, BROWSE_ROOT=root_path,
                              BROWSE_REL=rel,
                              BROWSE_BUDGET=str(int(budget))), timeout)
    return (base64.b64decode(data.get("b64") or ""),
            bool(data.get("truncated")), int(data.get("size") or 0))


def render(host, root_path, rel, timeout=RENDER_TIMEOUT, renderer_rel=None,
           render_argv=None):
    """Render a remote GDS ON THE CLUSTER. -> (png_bytes, info)

    The plan's rule (section 5.3), and the reason the browser is useful for
    28 nm at all: ONR has no local layouts, and pulling one to look at it
    would move hundreds of megabytes to produce a 3 MB picture.

    `renderer_rel` is the served repo's DECLARED renderer as a repo-relative
    path, and `render_argv` its argv template, both from roots.json -- the
    remote script looks for that path above the layout before the two
    conventional ones, and calls it the way the template says.
    """
    binds = {"BROWSE_ROOT": root_path, "BROWSE_REL": rel}
    if renderer_rel:
        binds["BROWSE_RENDERER_REL"] = renderer_rel.replace("\\", "/")
    if render_argv:
        binds["BROWSE_RENDER_ARGV"] = json.dumps(list(render_argv))
    data = _run(host, _script(_PY_RENDER, **binds), timeout)
    return (base64.b64decode(data.get("b64") or ""),
            {"python": data.get("python"), "renderer": data.get("renderer"),
             "size": data.get("size"), "gds_size": data.get("gds_size")})


def wave(host, root_path, rel, reader_src, view=None, budget=WAVE_BUDGET,
         gate_bytes=0, timeout=WAVE_TIMEOUT):
    """Parse and plot a remote transient ON the cluster. -> dict

    `reader_src` is the SOURCE TEXT of the wave reader, and the caller is
    expected to hand over the very bytes it imported its own reader from --
    that is the invariant, not "a copy of wave.py". The remote picture and the
    local picture then cannot be drawn by different code, which for a view
    whose job is judging an unfinished run is the difference between a partial
    curve and a wrong one.

    Returns either {"kind": "gated", size, mtime, reads} -- the file is bigger
    than `gate_bytes` and nothing was read -- or {"kind": "wave", ...} with the
    summary, the trace selection, and the SVG already decompressed.
    """
    binds = {"BROWSE_ROOT": root_path, "BROWSE_REL": rel,
             "BROWSE_WAVE_SRC": base64.b64encode(
                 reader_src.encode("utf-8") if not isinstance(reader_src, bytes)
                 else reader_src).decode("ascii"),
             "BROWSE_WAVE_BUDGET": str(int(budget)),
             "BROWSE_GATE_BYTES": str(int(gate_bytes or 0)),
             # One line by construction: json.dumps never emits a raw newline,
             # so the view survives the same no-newline rule paths obey.
             "BROWSE_VIEW": json.dumps(view or {}, sort_keys=True)}
    data = _run(host, _script(_PY_WAVE, **binds), timeout)
    if data.get("kind") == "gated":
        return data
    blob = data.pop("svg_z", "") or ""
    if not blob:
        data["svg"] = ""            # an operating point has no picture
        return data
    try:
        data["svg"] = zlib.decompress(base64.b64decode(blob)).decode("utf-8")
    except (ValueError, zlib.error) as exc:
        raise RemoteError("waveform picture did not survive the wire: %s" % exc)
    return data


def invalidate(fs=None):
    """Drop cached listings -- the explicit refresh the plan asks for."""
    with _CACHE_GUARD:
        for k in [k for k in _CACHE if fs is None or k[0] == fs]:
            del _CACHE[k]


def _main(argv=None):
    """Smoke test: python3 cluster.py <host> <root> [rel]"""
    a = (argv or sys.argv[1:])
    if len(a) < 2:
        print(_main.__doc__)
        return 2
    entries, badges, cached = listdir(a[0], a[1], a[2] if len(a) > 2 else "")
    print("%d entries (cached=%s), %d badge sources"
          % (len(entries), cached, len(badges)))
    for e in entries[:20]:
        print("  %-46s %s" % (e["name"], "dir" if e["is_dir"] else e["size"]))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
