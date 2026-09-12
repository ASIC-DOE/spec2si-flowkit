#!/usr/bin/env python3
"""The browser's ENTIRE exec surface -- GDS render, KLayout handoff, XOR diff.

Everything else in `browse/` reads files. This module runs programs, so it is
one file rather than two: the whole question "what can this server execute" is
answered by reading it, and a third entry point here should have to argue for
itself in review.

All three are whitelisted by construction. None takes a command, a template or
a shell string from the request -- the request supplies layout PATHS, each one
already confined by `roots.resolve`, and they are passed through argv. No shell
is involved anywhere in this file.

Why a subprocess for the render at all: `render_gds.py` imports matplotlib, and
the browser is stdlib-only by design (plan principle 2 and section 5.1). Calling
it out of process keeps that true, and keeps a renderer crash from taking the
server with it.
"""
import hashlib
import os
import re
import subprocess
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import roots as _roots                                 # noqa: E402

#: The repo being SERVED, which is not necessarily the checkout this file
#: lives in -- one installation can now serve another (roots.repo_root).
REPO = _roots.REPO

#: The programs this module may run, resolved from the repo rather than from
#: PATH or from anything in the request.
#:
#: A repo DECLARES its renderer and its KLayout launcher in `browse/roots.json`
#: (`roots.SETTINGS_KEYS`); these FIXED LISTS of repo-relative locations are
#: the fallback for a repo that has not, tried in order -- not a search. The
#: 65 nm and 28 nm flows both keep the renderer at `analog/engine/layout/`;
#: XT011 has no `engine` layer at all and keeps its port at `analog/layout/`,
#: so anchoring on one path made every XT011 layout report "renderer not
#: found". sky130 then kept its renderer at a fourth place with a DIFFERENT
#: ARGUMENT ORDER (`render_gds.py <gds> <top> <out>`, because SKY130 needs the
#: top cell named), which is where guessing stopped being a plan and the
#: declaration came in. Enumerated rather than globbed because what this
#: module may execute has to be answerable by reading this page (see the
#: module docstring) plus one tracked JSON file: a glob would make it depend
#: on what happens to be on disk.
_RENDERER_AT = (("analog", "engine", "layout", "render_gds.py"),
                ("analog", "layout", "render_gds.py"))
_KLAYOUT_AT = (("analog", "engine", "layout", "klayout_open.ps1"),
               ("analog", "layout", "klayout_open.ps1"))

#: What a renderer is called with when the repo does not say: the layout, the
#: output, and (when the pane asks for a crop) four trailing numbers.
DEFAULT_RENDER_ARGV = ("{gds}", "{out}")
#: The placeholders an argv template may use. `{top}` is filled from the
#: stream itself (model.gds_top), never typed.
RENDER_PLACEHOLDERS = ("{gds}", "{out}", "{top}")


def _first_present(repo, places):
    """The first of `places` that exists under `repo`, else the first one.

    Falling back to places[0] rather than None keeps the "not found" error
    naming a concrete path, which is the only useful thing it can say.
    """
    paths = [os.path.join(repo, *p) for p in places]
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[0]


class Renderer(object):
    """One renderer: where it is, how it is called, whether it can crop."""

    def __init__(self, path, argv=None, window=True, declared=False):
        self.path = path
        self.argv = tuple(argv or DEFAULT_RENDER_ARGV)
        self.window = bool(window)
        self.declared = bool(declared)
        self.needs_top = "{top}" in self.argv

    def argv_for(self, gds, out, top=None):
        """The renderer's arguments (without the interpreter) for one call."""
        subst = {"{gds}": gds, "{out}": out, "{top}": top or ""}
        return [subst.get(a, a) for a in self.argv]

    def as_dict(self):
        return {"path": self.path, "argv": list(self.argv),
                "window": self.window, "declared": self.declared,
                "present": os.path.exists(self.path)}


def _declared_path(repo, value, what):
    """A declared repo-relative program path -> absolute, or ValueError.

    Relative to the served repo and confined to it, for the same reason the
    browsed paths are: a declaration that could name `C:/anything.py` would
    turn a tracked config file into a way of choosing what this server runs.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("roots.json: %s must be a repo-relative path" % what)
    rel = value.strip().replace("\\", "/")
    if os.path.isabs(rel) or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise ValueError("roots.json: %s must be relative to the repo, not %r"
                         % (what, value))
    if any(p == ".." for p in rel.split("/")):
        raise ValueError("roots.json: %s must not leave the repo: %r"
                         % (what, value))
    return os.path.join(repo, *rel.split("/"))


def renderer_from(settings, repo=None):
    """The renderer a repo declares, else the first fallback that exists."""
    repo = repo or REPO
    decl = (settings or {}).get("renderer")
    if decl is None:
        return Renderer(_first_present(repo, _RENDERER_AT))
    if isinstance(decl, str):
        return Renderer(_declared_path(repo, decl, "renderer"), declared=True)
    if not isinstance(decl, dict):
        raise ValueError("roots.json: renderer must be a path or an object")
    argv = decl.get("argv") or DEFAULT_RENDER_ARGV
    if (not isinstance(argv, (list, tuple)) or not argv
            or not all(isinstance(a, str) for a in argv)):
        raise ValueError("roots.json: renderer.argv must be a list of strings")
    for need in ("{gds}", "{out}"):
        if need not in argv:
            raise ValueError("roots.json: renderer.argv must contain %s" % need)
    for a in argv:
        if a.startswith("{") and a not in RENDER_PLACEHOLDERS:
            raise ValueError("roots.json: unknown renderer placeholder %r "
                             "-- known: %s" % (a, ", ".join(RENDER_PLACEHOLDERS)))
    return Renderer(_declared_path(repo, decl.get("path"), "renderer.path"),
                    argv, decl.get("window", True), declared=True)


def klayout_from(settings, repo=None):
    repo = repo or REPO
    decl = (settings or {}).get("klayout")
    if decl is None:
        return _first_present(repo, _KLAYOUT_AT)
    return _declared_path(repo, decl, "klayout")


RENDERER_SPEC = renderer_from({})
#: The renderer's PATH, kept under its old name because the error messages and
#: the tests name it.
RENDERER = RENDERER_SPEC.path
KLAYOUT_LAUNCHER = klayout_from({})


def configure(settings):
    """Apply a repo's declarations (roots.settings). Called once by the server
    after it has loaded the roots, so a served repo's own `renderer` wins over
    the fallback list resolved at import."""
    global RENDERER_SPEC, RENDERER, KLAYOUT_LAUNCHER
    RENDERER_SPEC = renderer_from(settings)
    RENDERER = RENDERER_SPEC.path
    KLAYOUT_LAUNCHER = klayout_from(settings)
    return RENDERER_SPEC

#: Do not render a stream bigger than this on demand. `render_gds.py` is a
#: pure-python reader that flattens the hierarchy into matplotlib patches; the
#: 1.65 MB ctrl2 pilot is 259 247 polygons and takes tens of seconds. A
#: chip-level stream would be a click that never returns. Those go to KLayout,
#: which is what KLayout is for.
RENDER_SIZE_LIMIT = 32 * 1024 * 1024
#: Wall clock for one render. Past this the click has already failed as
#: feedback, whatever the process is still doing.
RENDER_TIMEOUT = 180.0

#: Per-output locks, so two clicks on the same GDS do not start two renders.
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def cache_dir():
    """Where renders are cached -- NEVER inside a flow tree.

    The browser is read-only with respect to everything it browses (plan
    principle 6). A cache written beside the GDS would put a file into a signed
    stamp directory, which is exactly the thing that must not happen: those
    directories are compared byte-for-byte across runs.
    """
    d = os.environ.get("BROWSE_CACHE_DIR")
    if not d:
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(os.path.expanduser("~"), ".cache"))
        d = os.path.join(base, "browse-gds-cache")
    return d


def cache_key(abspath, limit=RENDER_SIZE_LIMIT):
    """Content hash, so an edited GDS re-renders and a moved one does not.

    Keyed on CONTENT rather than (path, mtime): the same stream is routinely
    pulled from the cluster to two different local paths, and re-rendering it
    the second time is pure waste. It also means a flow that rewrites a GDS
    with identical bytes -- the geometry-identical regression this repo runs
    before every engine change -- reuses the render, which is the case where
    reuse is most obviously correct.
    """
    h = hashlib.sha256()
    with open(abspath, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:32]


def _lock_for(path):
    with _LOCKS_GUARD:
        if path not in _LOCKS:
            _LOCKS[path] = threading.Lock()
        return _LOCKS[path]


def render_gds(abspath, timeout=RENDER_TIMEOUT, limit=RENDER_SIZE_LIMIT,
               win=None):
    """Render a GDS to a cached PNG. -> {ok, png, state, error}

    `state` is "cached" or "rendered", because the difference is the difference
    between an instant pane and a 40-second one, and a user who cannot tell
    which they are waiting for will assume it hung.
    """
    size = os.path.getsize(abspath)
    if size > limit:
        return {"ok": False, "png": None, "state": "too_big",
                "error": "%.1f MB is over the %.0f MB on-demand render limit "
                         "-- open it in KLayout instead"
                         % (size / 1048576.0, limit / 1048576.0)}
    # `RENDERER` (the path) is still honoured when it has been REASSIGNED --
    # the tests stub the renderer that way -- and it then runs with the
    # default argv; the declared spec carries the argv for its own path.
    spec = (RENDERER_SPEC if RENDERER == RENDERER_SPEC.path
            else Renderer(RENDERER))
    if not os.path.exists(spec.path):
        return {"ok": False, "png": None, "state": "no_renderer",
                "error": "renderer not found: %s%s"
                         % (spec.path, " (declared in roots.json)"
                            if spec.declared else "")}
    if win and not spec.window:
        # Refused, not silently drawn whole: a full render served for a crop
        # request is the wrong picture with the right caption.
        return {"ok": False, "png": None, "state": "no_window",
                "error": "this repo's renderer takes no crop window "
                         "(roots.json renderer.window is false)"}
    top = None
    if spec.needs_top:
        import model                                   # stdlib-only sibling
        tops = model.gds_top(abspath)
        if len(tops) != 1:
            return {"ok": False, "png": None, "state": "no_top",
                    "error": "the renderer needs ONE top cell and the stream "
                             "has %d unreferenced structure(s): %s"
                             % (len(tops), ", ".join(tops[:6]) or "(none)")}
        top = tops[0]

    # A WINDOW is part of the cache identity: the same stream cropped two ways
    # is two pictures, and keying only on content would serve the first crop
    # for every later one.
    tag = ""
    if win:
        tag = "_" + hashlib.sha256(
            ("%r" % (tuple(win),)).encode()).hexdigest()[:10]
    out = os.path.join(cache_dir(), cache_key(abspath) + tag + ".png")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return {"ok": True, "png": out, "state": "cached", "error": None}

    with _lock_for(out):
        # Re-check inside the lock: the render we were queued behind may be
        # exactly the one we wanted.
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return {"ok": True, "png": out, "state": "cached", "error": None}
        try:
            os.makedirs(cache_dir(), exist_ok=True)
        except OSError as exc:
            return {"ok": False, "png": None, "state": "error",
                    "error": "cache dir: %s" % exc}
        # Render to a temp name and rename: a half-written PNG must never be
        # served, and a killed render must not poison the cache entry.
        #
        # The temp name still ends in .png, and must: matplotlib picks its
        # output format from the EXTENSION, so a plain `.part` suffix made
        # every real render fail inside savefig -- while a stub renderer that
        # ignored the extension passed the tests happily.
        tmp = out + ".%d.part.png" % os.getpid()
        try:
            # The default renderer takes the layout and the output, and an
            # optional crop as four trailing arguments, x1 y1 x2 y2 in um. A
            # declared one is called the way its argv template says.
            argv = [sys.executable, spec.path] + spec.argv_for(abspath, tmp, top)
            if win:
                argv += ["%r" % float(v) for v in win]
            proc = subprocess.run(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout)
        except subprocess.TimeoutExpired:
            _unlink(tmp)
            return {"ok": False, "png": None, "state": "timeout",
                    "error": "render exceeded %.0f s" % timeout}
        except OSError as exc:
            _unlink(tmp)
            return {"ok": False, "png": None, "state": "error",
                    "error": str(exc)}
        if proc.returncode != 0 or not os.path.exists(tmp):
            _unlink(tmp)
            # The renderer's own message is the useful part -- a bare exit code
            # says nothing about an unsupported record or a missing layer.
            tail = _decode(proc.stdout)[-800:]
            return {"ok": False, "png": None, "state": "failed",
                    "error": tail or "renderer exited %d" % proc.returncode}
        try:
            os.replace(tmp, out)
        except OSError as exc:
            _unlink(tmp)
            return {"ok": False, "png": None, "state": "error",
                    "error": str(exc)}
    return {"ok": True, "png": out, "state": "rendered", "error": None}


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


#: Wall clock for the manifest check. Measured at well under a second on the
#: largest tree (tsmc65, 15 cells); the cap is for a hung interpreter, not
#: for the work.
DESIGN_CHECK_TIMEOUT = 60.0
#: (repo, manifest signature) -> result. The manifests change when a capture
#: runs and at no other time, so the signature is the cache key.
_DESIGN_CHECK_CACHE = {}
_DESIGN_CHECK_GUARD = threading.Lock()


def design_check(repo=None, timeout=DESIGN_CHECK_TIMEOUT, signature=None):
    """`python -m designdb --check --fast` on the served repo.

    -> {available, ok, verdict, tail, cached}

    The fourth exec, and it argues for itself on the same terms as the other
    three: a fixed program (the served repo's OWN `designdb` package, run
    through this interpreter), fixed arguments, nothing from the request, no
    shell. It is the check CI runs on every change to `design/` -- manifests
    only, since the bytes are on the cluster -- and surfacing it as a badge
    on the `design` root is what turns "the manifests are consistent" from a
    thing you go and run into a thing the listing says.

    `--fast` is not optional here: the full check re-measures every stored
    file's md5, which is right on the cluster and meaningless on a laptop
    that does not have the store.
    """
    repo = repo or REPO
    if not os.path.exists(os.path.join(repo, "designdb", "__main__.py")):
        return {"available": False, "ok": None, "cached": False, "tail": "",
                "verdict": "no designdb package in this repo"}
    key = (repo, signature)
    if signature is not None:
        with _DESIGN_CHECK_GUARD:
            hit = _DESIGN_CHECK_CACHE.get(key)
        if hit is not None:
            return dict(hit, cached=True)
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "designdb", "--check", "--fast"],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"available": True, "ok": False, "cached": False, "tail": "",
                "verdict": "designdb --check --fast exceeded %.0f s" % timeout}
    except OSError as exc:
        return {"available": True, "ok": False, "cached": False, "tail": "",
                "verdict": str(exc)}
    text = _decode(proc.stdout)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # The LAST line is the verdict: `designdb --check: PASS ...` or the
    # refusal. An empty output with rc 0 is reported as what it is rather
    # than read as a pass -- the absence of the line is a failure.
    verdict = lines[-1] if lines else "(no output)"
    ok = proc.returncode == 0 and bool(lines) and "PASS" in verdict
    out = {"available": True, "ok": ok, "cached": False,
           "verdict": verdict[:240], "tail": text[-2000:]}
    if signature is not None:
        with _DESIGN_CHECK_GUARD:
            _DESIGN_CHECK_CACHE[key] = out
    return out


def _decode(b):
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return b or ""


def sidecars(abspath):
    """Which KLayout inputs exist beside a layout: {lyp, lyrdb}.

    Surfaced in the pane because their absence is silent and consequential: no
    .lyp means M1-M9 come up in an arbitrary colour cycle with no names, and no
    .lyrdb means the Marker Browser is empty. Both are emitted by
    verify_layout.run_drc, so missing usually means "this GDS did not come from
    a DRC run", which is itself worth knowing before you go looking for markers.
    """
    d = os.path.dirname(abspath)
    stem = os.path.splitext(os.path.basename(abspath))[0]
    lyp = os.path.join(d, stem + ".lyp")
    rdb = os.path.join(d, "DRC_RES.lyrdb")
    return {"lyp": lyp if os.path.exists(lyp) else None,
            "lyrdb": rdb if os.path.exists(rdb) else None}


def klayout_available():
    """Windows + an installed KLayout. Reported, not assumed.

    asic7 has Xvfb and vncserver but NO klayout binary (plan section 2), so on
    the cluster side this is correctly false rather than a launch that fails
    somewhere the user cannot see.
    """
    if os.name != "nt":
        return False
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return False
    return os.path.exists(os.path.join(appdata, "KLayout", "klayout_app.exe"))


#: The XOR binary, shipped with KLayout. A third exec, and it argues for
#: itself on the same terms as the other two: a fixed local binary, arguments
#: that are already-confined paths, no shell, and it answers a question the
#: repo's own policy asks on every engine change ("geometry-identical
#: regression before any engine change lands" -- analog/CLAUDE.md).
XOR_BIN = os.path.join(os.environ.get("APPDATA", ""), "KLayout", "strmxor.exe")

#: Wall clock for one XOR. Measured: the 1.65 MB ctrl2 pilot against itself is
#: a few seconds; the pathological case is two large layouts that differ
#: everywhere.
XOR_TIMEOUT = 300.0

#: `  32/0       -            22881`
_XOR_ROW = re.compile(r"^\s*(\d+/\d+)\s+(\S+)\s+(\d+)\s*$")


def xor_available():
    return os.name == "nt" and bool(os.environ.get("APPDATA")) \
        and os.path.exists(XOR_BIN)


def xor_gds(a, b, timeout=XOR_TIMEOUT):
    """Geometric XOR of two layouts. -> {ok, identical, out, layers, error}

    `identical` is THE assertion: the policy is that an engine change which
    should move no geometry moves none, and until now that was checked by
    reading coordinates. Empty is the pass.

    When they differ, `out` is a GDS holding EXACTLY the shapes that moved,
    which the ordinary renderer then draws -- a reviewable picture instead of
    a wall of numbers, and no second renderer (principle 4).
    """
    if not xor_available():
        return {"ok": False, "identical": None, "out": None, "layers": [],
                "error": "strmxor.exe not found -- it ships with KLayout "
                         "(%s)" % XOR_BIN}
    for p in (a, b):
        if not os.path.exists(p):
            return {"ok": False, "identical": None, "out": None, "layers": [],
                    "error": "no such layout: %s" % os.path.basename(p)}

    key = "x" + hashlib.sha256(
        (cache_key(a) + "|" + cache_key(b)).encode()).hexdigest()[:30]
    out = os.path.join(cache_dir(), key + ".xor.gds")
    try:
        os.makedirs(cache_dir(), exist_ok=True)
    except OSError as exc:
        return {"ok": False, "identical": None, "out": None, "layers": [],
                "error": "cache dir: %s" % exc}

    with _lock_for(out):
        tmp = out + ".%d.part.gds" % os.getpid()
        # -l is MANDATORY, and the reason is not the verdict but the PICTURE.
        # Without it, a layer present in only one layout is skipped: measured
        # on the ctrl2 pilot with all 1274 elements on 32/0 (M2) removed,
        # strmxor still exits nonzero -- but writes a 130-byte EMPTY output.
        # The pane would then show a blank diff beside "differences exist",
        # which is the worst of both answers. With -l the same case writes
        # 22881 shapes, and they render as the M2 routing that went missing.
        cmd = [XOR_BIN, "-l", a, b, tmp]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            _unlink(tmp)
            return {"ok": False, "identical": None, "out": None, "layers": [],
                    "error": "XOR exceeded %.0f s" % timeout}
        except OSError as exc:
            _unlink(tmp)
            return {"ok": False, "identical": None, "out": None, "layers": [],
                    "error": str(exc)}
        text = _decode(proc.stdout)
        # Exit status IS the verdict: 0 = identical, >0 = differences exist.
        identical = proc.returncode == 0
        layers = []
        for line in text.splitlines():
            m = _XOR_ROW.match(line)
            if m:
                layers.append({"layer": m.group(1), "shapes": int(m.group(3))})
        if identical:
            _unlink(tmp)
            return {"ok": True, "identical": True, "out": None, "layers": [],
                    "error": None, "summary": text.strip()}
        if not os.path.exists(tmp):
            return {"ok": False, "identical": False, "out": None,
                    "layers": layers, "error": text.strip()[-800:]
                    or "strmxor exited %d with no output" % proc.returncode}
        try:
            os.replace(tmp, out)
        except OSError as exc:
            _unlink(tmp)
            return {"ok": False, "identical": False, "out": None,
                    "layers": layers, "error": str(exc)}
    return {"ok": True, "identical": False, "out": out, "layers": layers,
            "error": None, "summary": text.strip()}


def open_in_klayout(abspath):
    """Hand a layout to the KLayout GUI. -> {ok, error, cmd}

    Fire-and-forget: KLayout is a GUI that stays open for as long as the user
    wants it, so waiting for it would hang the request forever. What we DO
    report is whether the launcher started at all, since "nothing happened" is
    otherwise indistinguishable from "it is still loading a 200 MB stream".

    The launcher (klayout_open.ps1) resolves the .lyp and DRC_RES.lyrdb sidecars
    itself and passes them as -l and -m; that logic stays in one place rather
    than being duplicated here.
    """
    if not os.path.exists(abspath):
        return {"ok": False, "error": "no such layout", "cmd": None}
    if not klayout_available():
        return {"ok": False, "cmd": None,
                "error": "KLayout is not installed on this machine "
                         "(expected %%APPDATA%%\\KLayout\\klayout_app.exe)"
                         if os.name == "nt" else
                         "the KLayout handoff is Windows-only -- on the "
                         "cluster there is no klayout binary"}
    if not os.path.exists(KLAYOUT_LAUNCHER):
        return {"ok": False, "cmd": None,
                "error": "launcher not found: %s" % KLAYOUT_LAUNCHER}
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", KLAYOUT_LAUNCHER, abspath]
    try:
        # No shell, no string interpolation: the path is one argv element, so a
        # layout whose name contains a quote or a semicolon is data, not syntax.
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "cmd": cmd}
    return {"ok": True, "error": None, "cmd": cmd}
