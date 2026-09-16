#!/usr/bin/env python3
"""Artifact browser -- phase 3 of docs/visual_interaction_plan.md.

Three panes over the local artifact trees: roots, listing, type-dispatched
viewer. Stdlib `http.server` bound to 127.0.0.1 on a random port, opened in the
default browser.

  python3 browse/server.py [--port N] [--no-open]

The hole it closes: ~1857 PNGs on disk with no index and no LATEST view, plus
SVG schematics, JSON reports and tool logs that each need a different tool to
look at. A render nobody opens is not feedback.

Why a browser and not tkinter (plan section 5.1, decided): the artifact mix is
PNG + **SVG** + HTML + MD + JSON/JSONL + text, and tkinter cannot show SVG
without a new dependency -- the schematic renderer emits SVG, so that is a hard
blocker on the view we most want. A browser also gives zoom, pan, find and text
selection for free, and shares templates with the static report (principle 4).

READ-ONLY, and structurally so: no route writes, deletes or executes anything.
Every filesystem path arrives through `roots.resolve`, which confines by
resolved path -- see roots.py. Bound to the loopback interface; no auth, because
there is nothing to authorise and a login form would imply otherwise.

Phase 3 landed LOCAL roots; phase 4 adds the semantic badges (verdict, DRC, LVS,
stage and job state read out of the artifacts themselves) and the filter over
them. Cluster roots, GDS rendering and the KLayout handoff are still to come --
each needs the hardened ssh transport (jobs/remote.py) rather than a fourth copy
of the sync idea.

Usage:
  python3 server.py [--port] [--no-open] [--status] [--config-dir]
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# roots FIRST: it resolves which repo is being served (BROWSE_REPO), and
# every path below is relative to that answer rather than to wherever this
# file happens to live.
import roots as rootsmod                               # noqa: E402
import agentview                                       # noqa: E402
import cluster                                         # noqa: E402
# The track map lives beside the code that WRITES the abstract, so there is
# one place that knows what those fields mean (principle 4). It is pure
# stdlib -- no matplotlib -- so importing it here does not break the
# browser's stdlib-only rule.
sys.path.insert(0, os.path.join(rootsmod.REPO, "analog", "engine",
                                "layout"))
try:
    import abstract_svg                                # noqa: E402
except ImportError:                                    # pragma: no cover
    abstract_svg = None


def _find_reader():
    """The transient reader, BY PATH -- and remember which path. -> (mod, src)

    `import wave` is a trap here and it is the silent-pass kind. `wave` is
    also a STDLIB module (the WAV file one), so on a checkout without
    `analog/engine/wave.py` -- ONR has none, and the cluster copy of ms_pilot
    had none either -- the import quietly succeeds and hands back something
    with no `read_psf`. The failure then surfaces as a viewer error on a file,
    never as "this installation has no reader".

    Loading by explicit path and then CHECKING FOR read_psf makes the absence
    a fact at startup. The source text is kept because the remote reader is
    literally this file: `cluster.wave` ships these bytes, so a cluster plot
    and a local plot cannot be drawn by different code.
    """
    import importlib.util
    for base in (rootsmod.REPO, _PKG_REPO):
        p = os.path.join(base, "analog", "engine", "wave.py")
        if not os.path.exists(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("browse_wave", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            with open(p, "r", encoding="utf-8") as fh:
                src = fh.read()
        except Exception:                              # pragma: no cover
            continue
        if hasattr(mod, "read_psf") and hasattr(mod, "choose"):
            return mod, src, p
        # A reader that exists but is too old to use. Skipping it silently
        # would be the same silent pass in a new place: the served repo would
        # look like it had no reader while another checkout's was quietly
        # standing in, and the two would drift without anyone being told.
        sys.stderr.write("[browse] ignoring %s -- it predates choose()/BUDGET; "
                         "falling back\n" % p)
    return None, None, None


_PKG_REPO = os.path.dirname(_HERE)
wavemod, WAVE_SRC, WAVE_PATH = _find_reader()


def _find_mc():
    """The MC aggregator, BY PATH, beside the reader it aggregates.

    Loaded the same way and for the same reason as `wave.py`: putting
    `analog/engine` on `sys.path` would make a bare `import wave` resolve to
    the engine module, which is precisely the accident `_find_reader` exists
    to rule out -- and it silently disarmed the test that proves it.
    """
    if not WAVE_PATH:
        return None
    import importlib.util
    p = os.path.join(os.path.dirname(WAVE_PATH), "mc.py")
    if not os.path.exists(p):
        return None
    try:
        spec = importlib.util.spec_from_file_location("browse_mc", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:                                  # pragma: no cover
        return None
    return mod if hasattr(mod, "iterations") else None


mcmod = _find_mc()
#: The job reader, located exactly where cluster.py found the transport --
#: it IS the transport, and its `list`/`events` wrappers are the whole of
#: what the live panel needs (plan §6.5).
remotemod = getattr(cluster, "_remote", None)
#: The host chooser, out of the same package as the transport. It answers
#: "which host should perform this read", which is a different question from
#: `pick()`'s "where should this job run" -- see hosts.py.
try:
    if cluster.TRANSPORT_DIR and cluster.TRANSPORT_DIR not in sys.path:
        sys.path.insert(0, cluster.TRANSPORT_DIR)
    import hosts as hostsmod                            # noqa: E402
except ImportError:                                     # pragma: no cover
    hostsmod = None
#: WHICH FILESYSTEM the job store is on -- not which host. `roots.json`
#: already declares this (`"fs": "bnl-home"`), and the whole point of the
#: change is that the store is an artifact of the filesystem while the host
#: is provenance.
_JOBS_FS = os.environ.get("BROWSE_JOBS_FS") or "bnl-home"
_SAFE_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: A `list` is 9.85 s measured. Re-reading it on every pane repaint would put
#: a permanent job on the head node; the panel refreshes on its own clock.
JOBS_TTL = 20.0
_JOBS_CACHE = {}
_JOBS_GUARD = threading.Lock()
import estimate                                        # noqa: E402
import model                                           # noqa: E402
if cluster.TRANSPORT_DIR and cluster.TRANSPORT_DIR not in sys.path:
    sys.path.insert(0, cluster.TRANSPORT_DIR)
try:                                                   # same lookup as cluster
    import procscan                                    # noqa: E402
except ImportError:                                    # pragma: no cover
    procscan = None
import tools                                           # noqa: E402

try:                                                   # py3 stdlib split
    from urllib.parse import urlparse, parse_qs, quote, unquote
except ImportError:                                    # pragma: no cover
    from urlparse import urlparse, parse_qs            # type: ignore
    from urllib import quote                           # type: ignore

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>artifact browser</title>
<style>
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;
       height:100vh; display:flex; flex-direction:column; }
#interact { border-top:2px solid #8886; display:flex; flex-direction:column;
            max-height:42vh; }
#itabs { display:flex; align-items:center; gap:.2rem; padding:.25rem .6rem;
         border-bottom:1px solid #8884; }
.itab { padding:.2rem .6rem; cursor:pointer; border-radius:4px 4px 0 0;
        font-size:12px; font-weight:600; color:#8a8a8a; }
.itab.sel { background:#8882; color:inherit; }
.ihide { cursor:pointer; padding:0 .4rem; }
#ibody { overflow:auto; padding:.5rem .6rem; }
#interact.collapsed #ibody { display:none; }
.psess { border:1px solid #8884; border-radius:4px; padding:.35rem .5rem;
         margin-bottom:.35rem; }
.psess h4 { margin:0 0 .2rem; font-size:12.5px; display:flex; gap:.4rem;
            align-items:center; flex-wrap:wrap; }
.pev { font-size:11px; color:#8a8a8a; margin-left:.2rem; }
.pargs { font:10.5px/1.35 ui-monospace,monospace; color:#8a8a8a;
         word-break:break-all; }
.tmfacts { margin:.2rem 0 .4rem; }
.netbar { display:flex; flex-wrap:wrap; gap:.25rem; margin:.3rem 0; }
.netchip { font:11px ui-monospace,monospace; padding:.05rem .35rem;
           border:1px solid #8886; border-radius:3px; cursor:pointer; }
.netchip:hover { background:#8882; }
.netchip.sel { background:#3b82f644; border-color:#3b82f6; font-weight:700; }
/* A trace picker. The box is the state -- ticked or not, drawn by the
   browser -- so nothing here has to make "selected" legible on its own; the
   label just has to stay next to its box and wrap as a unit. `align-items`
   matters: without it a long trace name wraps under the tick. */
.sigbox { display:inline-flex; align-items:center; gap:.2rem;
          font:11px ui-monospace,monospace; padding:.05rem .3rem;
          border:1px solid transparent; border-radius:3px; cursor:pointer;
          white-space:nowrap; }
.sigbox:hover { background:#8882; border-color:#8886; }
.sigbox input { margin:0; cursor:pointer; }
/* Many traces is the normal case (73 on one real run, 98 on another), so the
   picker gets its own scroll rather than pushing the plot off screen. */
.sigs { max-height:7.2rem; overflow-y:auto; align-content:flex-start; }
/* Both cross-probe pictures scale to the pane; the schematic is small and the
   track map is wide, and a fixed width made one of them unreadable. */
.cpview svg { width:100%; height:auto; max-width:100%; }
/* A progress bar only for jobs that publish a fraction. `progress.py` leaves
   `frac` null when the flow cannot say how far along it is, and an invented
   bar there would be the most confident lie on the page. */
.jbar { display:inline-block; width:5rem; height:.5rem; border:1px solid #8886;
        border-radius:2px; overflow:hidden; vertical-align:middle;
        margin-right:.3rem; }
.jbar i { display:block; height:100%; background:#3b82f6; }
/* Scale to the pane, not to the width the emitter happened to choose. Both
   pictures carry a viewBox, so width:100% costs nothing and the fixed 1000 px
   was leaving the x axis short of the frame on a wide window. */
#tmap svg, #wsvg svg { width:100%; height:auto; border:1px solid #8884; }
#tmap .dim { opacity:.07; }
/* Drag must PAN, not select. Without this the gesture grabs the axis labels
   and the plot stays put -- the labels are text, and text is selectable. */
#tmap svg, #wsvg svg, #gdsrender img {
  user-select:none; -webkit-user-select:none; -moz-user-select:none;
  touch-action:none; cursor:grab; }
.zbox { display:flex; gap:.25rem; align-items:center; flex-wrap:wrap;
        margin:.25rem 0; font-size:11px; }
.zbox input { width:8.5ch; font:11px ui-monospace,monospace; padding:.1rem .2rem;
              border:1px solid #8886; border-radius:3px; background:transparent;
              color:inherit; }
header { padding:.4rem .7rem; border-bottom:1px solid #8884;
         display:flex; gap:.8rem; align-items:center; }
header b { font-size:14px; }
#panes { flex:1; display:grid; grid-template-columns:210px 320px 1fr;
         min-height:0; }
#panes > div { overflow:auto; min-height:0; }
#roots { border-right:1px solid #8884; }
#list  { border-right:1px solid #8884; }
#view  { padding:.6rem; }
.item { padding:.22rem .6rem; cursor:pointer; white-space:nowrap;
        overflow:hidden; text-overflow:ellipsis; }
.item:hover { background:#8882; }
.item.sel { background:#3b82f633; font-weight:600; }
.item .meta { float:right; color:#8a8a8a; font-size:11px; padding-left:.6rem; }
.grp { margin-bottom:.5rem; }
.ghead { display:flex; align-items:center; gap:.4rem;
         padding:.35rem .6rem .1rem; font-size:10px; font-weight:700;
         letter-spacing:.09em; text-transform:uppercase; color:#8a8a8a; }
.ghead .gadd { margin-left:auto; cursor:pointer; font-size:14px;
               line-height:1; padding:0 .25rem; border-radius:3px; }
.ghead .gadd:hover { background:#8883; }
.ghost { padding:0 .6rem .2rem; font-size:10px; color:#8a8a8a; }
.addform { padding:.3rem .6rem .5rem; display:flex; flex-direction:column;
           gap:.25rem; }
.addform input { font:11px/1.4 ui-monospace,monospace; padding:.2rem .3rem;
                 border:1px solid #8886; border-radius:3px;
                 background:transparent; color:inherit; width:100%; }
.addmsg { font-size:10px; color:#8a8a8a; }
.dir { font-weight:600; }
.dir::before { content:"\\1F4C1  "; }
.crumb { padding:.3rem .6rem; border-bottom:1px solid #8884; font-size:12px;
         font-family:ui-monospace,monospace; word-break:break-all; }
.crumb a { cursor:pointer; text-decoration:underline; }
/* The location block must not read as chrome: it sat in the same style as the
   filter box directly beneath it, so the one line answering "where am I"
   looked like decoration. */
#crumb { background:#8881; }
.crumbline { display:flex; align-items:center; gap:.35rem; flex-wrap:wrap;
             font-size:12px; font-weight:600; }
.fullpath { margin-top:.15rem; font-size:10.5px; color:#8a8a8a;
            user-select:all; word-break:break-all; }
.b-local { background:#8882; }
pre { font:12px/1.4 ui-monospace,monospace; white-space:pre-wrap;
      word-break:break-word; margin:0; }
img,svg { max-width:100%; }
table { border-collapse:collapse; font-size:12px; }
td,th { border:1px solid #8884; padding:.15rem .4rem; text-align:left;
        vertical-align:top; }
.badge { font-size:10px; font-weight:600; padding:0 .3rem; border-radius:3px;
         margin-left:.3rem; white-space:nowrap; border:1px solid #8886; }
.b-ok   { background:#16a34a33; color:#15803d; }
.b-bad  { background:#dc262633; color:#b91c1c; }
.b-warn { background:#b8860b33; color:#92600b; }
.b-run  { background:#3b82f633; color:#1d4ed8; }
.b-info { background:#8882; }
@media (prefers-color-scheme: dark) {
  .b-ok { color:#4ade80; } .b-bad { color:#f87171; }
  .b-warn { color:#fbbf24; } .b-run { color:#93c5fd; }
}
.warn { background:#b8860b22; border-left:4px solid #b8860b; padding:.4rem .6rem;
        margin:.4rem 0; }
.err  { background:#b0202022; border-left:4px solid #b02020; padding:.4rem .6rem; }
.xok  { background:#16a34a22; border-left:4px solid #16a34a; padding:.4rem .6rem; }
.muted { color:#8a8a8a; }
</style></head><body>
<header><b>artifact browser</b>
  <span class="muted" id="hint">read-only &middot; localhost &middot;
    <span title="build on screen -- if this differs from the one the server printed, reload">ui __UI__</span></span>
  <span style="flex:1"></span>
  <span class="muted" id="status"></span>
</header>
<div id="panes">
  <div id="roots"></div>
  <div id="list"><div class="crumb" id="crumb"></div>
    <div class="crumb"><input id="filter" placeholder="filter (name or badge)"
      style="width:100%;border:0;background:transparent;font:inherit;outline:none"></div>
    <div id="sortbar" class="netbar"></div>
  <div id="mcbar" class="tmfacts" hidden></div>
  <div id="entries"></div></div>
  <div id="view"><p class="muted">Pick a file.</p></div>
</div>
<!-- INTERACTIONS: things you DO, as opposed to things you look at. The file
     browser and the viewers above are read-only; this strip is where the two
     actions with consequences live, and keeping them in their own region is
     the point -- a kill button has no business sitting inside a listing. The
     agent activity view (plan section 7) becomes the second tab. -->
<div id="interact">
  <div id="itabs">
    <span class="itab sel" data-tab="jobs">Jobs</span>
    <span class="itab" data-tab="procs">Processes</span>
    <span class="itab" data-tab="agent">Agent</span>
    <span style="flex:1"></span>
    <span class="muted" id="imsg"></span>
    <span class="ihide" id="itoggle" title="collapse">&#9662;</span>
  </div>
  <div id="ibody"><div id="tab-jobs">
      <p><button id="btn-jobs">Refresh</button>
         <label class="muted"><input type="checkbox" id="jwatch"> watch</label>
         <span class="muted" id="jmsg"></span></p>
      <div id="jobs"><p class="muted">not loaded &mdash; a cluster job list is
         a ten-second read, so it is not fetched until asked. Any host on the
         shared filesystem serves it; the one that answers is named.</p></div>
    </div>
    <div id="tab-procs" hidden>
      <p><button id="btn-scan">Scan cluster for stale tool processes</button>
         <span class="muted">read-only &mdash; nothing is killed without a
         second click</span></p>
      <div id="procs"></div>
    </div>
    <div id="tab-agent" hidden>
      <p><button id="btn-agent">Refresh</button>
         <select id="agent-session"></select>
         <span class="muted">metadata only &mdash; no message bodies, no tool
         output, no file contents ever leave ~/.claude</span></p>
      <div id="agent-other"></div>
      <div id="agent-now"><p class="muted">loading…</p></div>
      <div id="agent-warn"></div>
      <div id="agent-time"></div>
    </div>
  </div>
</div>
<script>
const $ = s => document.querySelector(s);
let ROOTS = [];
let ROOT = null, SLUG = null, REL = "", ENTRIES = [], REMOTE_INFO = "";
let BADGED = null;      // how many rows the server actually badged
let BADGE_SRC = null;   // which badge sources the listing actually read
let SETTINGS = {};      // what the served repo declared (renderer, sources)

const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const kb = n => n == null ? "" :
  n < 1024 ? n + " B" : n < 1048576 ? (n/1024).toFixed(0) + " K"
                                    : (n/1048576).toFixed(1) + " M";

async function j(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}
function status(t) { $("#status").textContent = t || ""; }

async function loadRoots(keepSelection) {
  const d = await j("/api/roots");
  ROOTS = d.roots;
  SETTINGS = {renderer: d.renderer, badge_sources: d.badge_sources || [],
              design: !!d.design};
  paintRoots();
  if (!keepSelection) {
    // open on a LOCAL root: starting on a remote one would make the first
    // paint wait on ssh, and a browser that hangs before it draws reads broken
    const first = ROOTS.find(r => r.exists === true);
    if (first) open(first.name, "");
  }
}

// Grouped, because a local folder and a cluster tree are different KINDS of
// place -- one is on this disk and instant, the other is an ssh round trip
// away -- and a flat list hid that behind identical-looking rows.
function paintRoots() {
  const groups = [];
  for (const r of ROOTS) {
    let g = groups.find(x => x.name === r.group);
    if (!g) groups.push(g = {name: r.group, kind: r.kind, roots: []});
    g.roots.push(r);
  }
  $("#roots").innerHTML = groups.map(g => {
    // One host label per GROUP, not per row: on a shared filesystem the host
    // is only who we ask, and repeating it on every row implied the trees
    // were different places.
    const hosts = [...new Set(g.roots.map(r => r.host).filter(Boolean))];
    const sub = g.kind === "remote"
      ? `<div class="ghost">via ${esc(hosts.join(", "))} · shared</div>` : "";
    return `<div class="grp"><div class="ghead">${esc(g.name)}
        <span class="gadd" data-add="${esc(g.name)}" title="add a folder">+</span>
      </div>${sub}` +
      g.roots.map(r =>
        `<div class="item ${r.exists === false ? "muted" : ""}"
           data-root="${esc(r.name)}"
           title="${esc(r.kind === "remote" ? r.host + ":" + r.path : r.path)}"
           >${esc(r.name)}` +
        (r.exists === false ? ' <span class="meta">missing</span>' : "") +
        `</div>`).join("") +
      `<div class="addform" id="add-${esc(slugify(g.name))}" hidden>
         <input placeholder="${g.kind === "remote"
            ? "~/path/on/the/cluster" : "C:\\\\path\\\\to\\\\folder"}"
           data-path="${esc(g.name)}">
         <input placeholder="name (optional)" data-nm="${esc(g.name)}">
         <button data-go="${esc(g.name)}">Add</button>
         <div class="addmsg" data-msg="${esc(g.name)}"></div>
       </div></div>`;
  }).join("");

  $("#roots").onclick = e => {
    const add = e.target.closest("[data-add]");
    if (add) {
      const f = $("#add-" + slugify(add.dataset.add));
      f.hidden = !f.hidden;
      if (!f.hidden) f.querySelector("[data-path]").focus();
      return;
    }
    const go = e.target.closest("[data-go]");
    if (go) return addRoot(go.dataset.go);
    const el = e.target.closest("[data-root]");
    if (el) open(el.dataset.root, "");
  };
  $("#roots").onkeydown = e => {
    if (e.key === "Enter" && e.target.dataset && e.target.dataset.path)
      addRoot(e.target.dataset.path);
  };
}

const slugify = s => s.replace(/[^a-zA-Z0-9]+/g, "-");

async function addRoot(groupName) {
  const grp = ROOTS.find(r => r.group === groupName) || {};
  const f = $("#add-" + slugify(groupName));
  const path = f.querySelector("[data-path]").value.trim();
  const nameIn = f.querySelector("[data-nm]").value.trim();
  const msg = f.querySelector("[data-msg]");
  if (!path) { msg.textContent = "enter a path"; return; }
  // Default the name to the last path component -- typing a name for every
  // folder you open is exactly the friction being removed here.
  //
  // The FOUR backslashes are not a typo: this script lives inside a Python
  // string, so Python halves them and the browser receives the character
  // class of backslash-or-slash. Written with two, the browser gets a class
  // holding only the forward slash, and every Windows path then "derives" a
  // name equal to the entire path.
  const name = nameIn || path.replace(/[\\\\/]+$/, "").split(/[\\\\/]/).pop() || path;
  const body = {name, path, kind: grp.kind || "local"};
  if (body.kind === "remote") { body.host = grp.host; body.fs = grp.fs;
                                body.group = groupName; }
  msg.textContent = "adding…";
  try {
    const r = await fetch("/api/roots", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)});
    if (!r.ok) { msg.textContent = await r.text(); return; }
    const d = await r.json();
    ROOTS = d.roots;
    paintRoots();
    open(name, "");                       // land in what you just added
  } catch (err) { msg.textContent = err.message; }
}

async function open(root, rel) {
  ROOT = root; REL = rel;
  SLUG = (ROOTS.find(r => r.name === root) || {}).slug;
  document.querySelectorAll("[data-root]").forEach(
    el => el.classList.toggle("sel", el.dataset.root === root));
  const remote = (ROOTS.find(r => r.name === root) || {}).kind === "remote";
  // An ssh round trip is fast when multiplexed and slow when NFS is cold.
  // Say which machine is being waited on rather than showing a dead pane.
  if (remote) status("asking " + root.split(":")[0] + "…");
  let d;
  try { d = await j(`/api/list?root=${encodeURIComponent(root)}&path=${encodeURIComponent(rel)}`); }
  catch (err) {
    status("");
    $("#entries").innerHTML = `<div class="err">${esc(err.message)}</div>`;
    return; }

  const parts = rel ? rel.split("/") : [];
  let acc = "", crumb = `<a data-go="">${esc(root)}</a>`;
  for (const p of parts) {
    acc = acc ? acc + "/" + p : p;
    crumb += ` / <a data-go="${esc(acc)}">${esc(p)}</a>`;
  }
  // WHERE AM I, stated rather than implied. A nickname plus relative parts
  // never said which disk or which machine; the resolved path does, and it is
  // selectable so it can be pasted into a shell.
  const chip = d.root_kind === "remote"
    ? `<span class="badge b-info">${esc(d.root_host || "remote")}</span>`
    : `<span class="badge b-local">local</span>`;
  const full = d.root_kind === "remote"
    ? `${esc(d.root_host)}:${esc(d.abspath || d.root_path)}`
    : esc(d.abspath || d.root_path);
  $("#crumb").innerHTML = `<div class="crumbline">${crumb}${chip}<span id="dcheck"></span></div>` +
    `<div class="fullpath" title="the folder this listing is showing">${full}</div>`;
  $("#crumb").onclick = e => {
    const a = e.target.closest("[data-go]");
    if (a) open(ROOT, a.dataset.go);
  };
  // THE DESIGN RECORD'S ROOT carries the manifest check as a badge -- the
  // gate CI runs on every change to design/, shown where the tree is
  // listed. Fetched separately so the listing paints first; a check that
  // took a second would otherwise be a second before any row appeared.
  if (d.design_tree) designCheckBadge();
  // The tab title too: with several browser windows open on several roots,
  // the taskbar was eight identical "artifact browser" entries.
  document.title = (parts.length ? parts[parts.length - 1] : root)
                 + " — artifact browser";

  ENTRIES = d.entries;
  REMOTE_INFO = d.host ? ` · ${d.host}${d.cached ? " (cached)" : ""}` : "";
  MC = d.mc || null;
  MC_ART = null;
  MC_TRACE = null;
  BADGED = (d.badged == null) ? null : d.badged;
  BADGE_SRC = Array.isArray(d.badge_sources) ? d.badge_sources : null;
  paint();
  const sb = $("#sortbar");
  if (sb) sb.onclick = e => {
    const c = e.target.closest("[data-sort]");
    if (!c) return;
    // Clicking the ACTIVE key reverses it; clicking another switches to it,
    // and each key gets the direction that is useful first -- newest and
    // largest, but names A-Z. Descending names as a default would be an
    // odd thing to hand anyone.
    if (SORT_KEY === c.dataset.sort) SORT_DESC = !SORT_DESC;
    else { SORT_KEY = c.dataset.sort; SORT_DESC = (SORT_KEY !== "name"); }
    paint();
  };
  const mcbar = $("#mcbar");
  if (mcbar) mcbar.onclick = e => {
    const c = e.target.closest("[data-mcart]");
    if (c) mcView(c.dataset.mcart);
  };
  $("#entries").onclick = e => {
    const el = e.target.closest("[data-rel]");
    if (!el) return;
    document.querySelectorAll("#entries .item").forEach(x => x.classList.remove("sel"));
    el.classList.add("sel");
    if (el.dataset.dir === "1") open(ROOT, el.dataset.rel);
    else view(el.dataset.rel);
  };
}

// The filter matches the BADGE TEXT as well as the name, so "FAIL" or
// "INCORRECT" narrows a directory of 200 stamps to the ones worth opening --
// which is the question badges were added to answer.
// ---- listing order ------------------------------------------------------
// It used to be fixed at newest-first and NOT SAID ANYWHERE, which is two
// faults: you could not change it, and you could not tell what it was. The
// default stays newest-first -- a stamp directory sorted alphabetically
// buries today's run under three months of them, which is why `listdir`
// chose it -- but it is now named on screen and switchable.
//
// Sorted HERE, not on the server: the rows are already in hand for both a
// local and a cluster listing, so re-ordering is instant and costs no round
// trip. The one thing that follows the server's order is badging, which is
// capped -- see the note `paint` prints when that starts to matter.
let SORT_KEY = "mtime", SORT_DESC = true;
const SORT_KEYS = [["name", "name"], ["mtime", "modified"], ["size", "size"]];

function sortRows(rows) {
  const k = SORT_KEY, dir = SORT_DESC ? -1 : 1;
  const val = e => k === "name" ? e.name.toLowerCase()
                 : k === "size" ? (e.size == null ? -1 : e.size)
                 : (e.mtime || 0);
  return rows.slice().sort((a, b) => {
    // DIRECTORIES STAY FIRST whatever the key. They are a different kind of
    // thing to click, and interleaving them by size -- where every directory
    // reads as null -- turns a listing into a lucky dip.
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    const va = val(a), vb = val(b);
    if (va < vb) return -1 * dir;
    if (va > vb) return 1 * dir;
    return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
  });
}

function sortBar() {
  const host = $("#sortbar");
  if (!host) return;
  host.innerHTML = `<span class="muted">sort</span>` +
    SORT_KEYS.map(([k, label]) =>
      `<span class="netchip ${SORT_KEY === k ? "sel" : ""}" data-sort="${k}"
         title="click again to reverse">${label}${
         SORT_KEY === k ? (SORT_DESC ? " ▾" : " ▴") : ""}</span>`
    ).join("");
}

function paint() {
  const f = $("#filter").value.trim().toLowerCase();
  let rows = !f ? ENTRIES : ENTRIES.filter(e =>
    e.name.toLowerCase().includes(f) ||
    (e.badges || []).some(b => b.text.toLowerCase().includes(f)));
  rows = sortRows(rows);
  sortBar();
  $("#entries").innerHTML = rows.map(e =>
    `<div class="item ${e.is_dir ? "dir" : ""}" data-rel="${esc(e.rel)}"
       data-dir="${e.is_dir ? 1 : 0}"><span class="meta">${kb(e.size)}</span>${esc(e.name)}` +
    (e.badges || []).map(b =>
      `<span class="badge b-${esc(b.tone)}">${esc(b.text)}</span>`).join("") +
    `</div>`).join("") || '<div class="muted" style="padding:.6rem">empty</div>';
  // NAME THE ORDER. Half the original complaint was that it was invisible:
  // "it seems to be last-modified, I think, it is not clear".
  const how = (SORT_KEYS.find(x => x[0] === SORT_KEY) || [])[1] || SORT_KEY;
  const dir = SORT_DESC ? (SORT_KEY === "name" ? "Z→A" : "newest first")
                        : (SORT_KEY === "name" ? "A→Z" : "oldest first");
  const dirText = SORT_KEY === "size"
    ? (SORT_DESC ? "largest first" : "smallest first") : dir;
  // A row past the badge cap has no verdict because it was never READ, which
  // is a different statement from "this run has no verdict" -- and the two
  // become indistinguishable the moment the pane stops showing the server's
  // own order.
  const capped = (BADGED != null && ENTRIES.length > BADGED &&
                  !(SORT_KEY === "mtime" && SORT_DESC))
    ? ` · badges cover the ${BADGED} newest only` : "";
  // SILENCE IS NOT CLEAN. A listing whose rows carry no badge used to look
  // exactly like a listing of runs that all passed -- and on the ports whose
  // result files were never badge sources, every listing looked like that.
  // So an unbadged listing says what it looked for and did not find.
  const nosrc = (BADGE_SRC !== null && BADGE_SRC.length === 0 &&
                 ENTRIES.length > 0)
    ? ` · no badge source here (reads ${(SETTINGS.badge_sources || [])
         .slice(0, 5).join(", ")}${(SETTINGS.badge_sources || []).length > 5
         ? ", …" : ""})` : "";
  status((f ? `${rows.length}/${ENTRIES.length} entries`
            : `${ENTRIES.length} entries`) +
         ` · by ${how}, ${dirText}` + capped + nosrc + REMOTE_INFO);
  paintMc();
}

async function designCheckBadge() {
  const el = $("#dcheck");
  if (!el) return;
  el.innerHTML = `<span class="badge b-info" title="running designdb --check --fast">checking manifests…</span>`;
  let r;
  try { r = await j("/api/designcheck"); }
  catch (err) { el.innerHTML = `<span class="badge b-bad">${esc(err.message)}</span>`; return; }
  const el2 = $("#dcheck");
  if (!el2) return;                     // the user has moved on
  if (!r.available) {
    el2.innerHTML = `<span class="badge b-warn" title="${esc(r.verdict)}">no designdb</span>`;
    return;
  }
  const tone = r.ok ? "b-ok" : "b-bad";
  el2.innerHTML = `<span class="badge ${tone}" title="${esc(r.verdict)}${r.cached ? " (cached)" : ""}">manifests ${r.ok ? "PASS" : "FAIL"} · ${r.n_manifests}</span>` +
    (r.ok ? "" : `<details style="display:inline-block;margin-left:.4rem"><summary class="muted">what it said</summary><pre>${esc(r.tail || r.verdict)}</pre></details>`);
}

// A read the server says will take a while has been OFFERED and accepted, per
// path. Kept per path so saying yes to one 60 MB transient does not silently
// commit you to the next one -- and so the watch poll, which re-requests the
// same path every few seconds, is not re-asked every time.
let GO = new Set();

// ---- Monte Carlo (plan §10.20) ------------------------------------------
// An MC run is a DIRECTORY, not a file: N sibling iterations, each holding
// ordinary results. So it is offered in the listing of the run — there is no
// single file to click — and the shape of the answer follows the artifact.
// A point file gives one number per iteration and becomes a DISTRIBUTION; a
// sweep gives one curve per iteration and becomes a BAND. Both were asked
// for and neither substitutes: a histogram answers "will it pass", a band
// answers "where does it go wrong".
let MC = null, MC_ART = null, MC_TRACE = null;

function paintMc() {
  const host = $("#mcbar");
  if (!host) return;
  if (!MC) { host.innerHTML = ""; host.hidden = true; return; }
  host.hidden = false;
  host.innerHTML = `<b>Monte Carlo</b>
    <span class="muted">${MC.n} iterations (${esc(MC.first)} … ${esc(MC.last)})</span>
    ` + (MC.artifacts || []).map(a =>
      `<span class="netchip ${MC_ART === a ? "sel" : ""}"
         data-mcart="${esc(a)}">${esc(a)}</span>`).join("") +
    (MC.artifacts && MC.artifacts.length ? "" :
      `<span class="muted">no artifact is present in every iteration</span>`);
}

async function mcView(art) {
  MC_ART = art;
  paintMc();
  $("#view").innerHTML = `<p class="muted">reading ${MC.n} iterations…</p>`;
  let d;
  try {
    d = await j(`/api/mc?root=${encodeURIComponent(ROOT)}` +
      `&path=${encodeURIComponent(REL)}&artifact=${encodeURIComponent(art)}` +
      (MC_TRACE ? `&trace=${encodeURIComponent(MC_TRACE)}` : ""));
  } catch (err) {
    $("#view").innerHTML = `<div class="err">${esc(err.message)}</div>`; return;
  }
  const failed = d.failed && d.failed.length
    ? `<div class="warn">${d.failed.length} iteration(s) had no
       ${esc(d.artifact)}: ${esc(d.failed.slice(0, 8).join(", "))} —
       they are EXCLUDED, not counted as zero.</div>` : "";
  const head = `<div class="crumb">${esc(art)}
      <span class="muted">${d.n_iters} of ${d.n_total} iterations ·
      ${d.seconds}s</span></div>`;
  if (d.kind === "scalar") {
    const rows = (d.rows || []).map(r => `<tr><td>${esc(r.name)}</td>
      <td style="text-align:right">${eng(r.stats.mean)}</td>
      <td style="text-align:right">${eng(r.stats.sigma)}</td>
      <td style="text-align:right">${eng(r.stats.min)}</td>
      <td style="text-align:right">${eng(r.stats.max)}</td>
      <td class="muted">${esc(r.units || "")}</td></tr>`).join("");
    $("#view").innerHTML = head + failed +
      `<table><tr><th>value</th><th>mean</th><th>sigma</th><th>min</th>
         <th>max</th><th>units</th></tr>${rows}</table>` +
      `<div class="cpview">${d.svg || ""}</div>`;
  } else if (d.kind === "vector") {
    const chips = (d.traces || []).map(t =>
      `<span class="netchip ${d.trace === t ? "sel" : ""}"
         data-mctrace="${esc(t)}">${esc(t)}</span>`).join("");
    $("#view").innerHTML = head + failed +
      `<div class="netbar">${chips}</div>
       <p class="muted">shading is <b>min/max</b>, not ±σ — a sigma band tucks
          the worst case out of sight, and the worst case is what a Monte
          Carlo run is for. The ${d.band ? d.band.n : 0} individual iterations
          are drawn underneath.</p>
       <div class="cpview">${d.svg || ""}</div>`;
    const bar = $("#view .netbar");
    if (bar) bar.onclick = e => {
      const c = e.target.closest("[data-mctrace]");
      if (c) { MC_TRACE = c.dataset.mctrace; mcView(art); }
    };
  } else {
    $("#view").innerHTML = head +
      `<div class="warn">${esc(d.reason || "nothing to aggregate")}</div>`;
  }
}

async function view(rel) {
  // path-shaped, so relative refs inside an HTML artifact resolve
  const raw = `/f/${SLUG}/${rel.split("/").map(encodeURIComponent).join("/")}`;
  let d;
  // `nosig=1` carries the ONE case a query string cannot otherwise express:
  // every box unticked. Without it "no sig parameters" would mean both "the
  // user has not chosen" and "the user chose nothing", and the second would
  // silently redraw all of them.
  const sigq = (WAVE_SIGS
      ? (WAVE_SIGS.length
           ? WAVE_SIGS.map(s => "&sig=" + encodeURIComponent(s)).join("")
           : "&nosig=1")
      : "")
    + "&mode=" + encodeURIComponent(WAVE_MODE)
    + "&part=" + encodeURIComponent(WAVE_PART)
    + (WAVE_XLOG === null ? "" : "&xlog=" + (WAVE_XLOG ? 1 : 0))
    + (WAVE_YLOG === null ? "" : "&ylog=" + (WAVE_YLOG ? 1 : 0))
    + (WAVE_X ? "&x0=" + WAVE_X[0] + "&x1=" + WAVE_X[1] : "")
    + (WAVE_Y ? "&y0=" + WAVE_Y[0] + "&y1=" + WAVE_Y[1] : "")
    + (GO.has(ROOT + "\\u0000" + rel) ? "&go=1" : "");
  try { d = await j(`/api/file?root=${encodeURIComponent(ROOT)}&path=${encodeURIComponent(rel)}${sigq}`); }
  catch (err) { $("#view").innerHTML = `<div class="err">${esc(err.message)}</div>`; return; }
  if (d.needs_confirm) { askFirst(d, rel); return; }

  const head = `<div class="crumb">${esc(rel)} <span class="muted">${kb(d.size)}</span>
    &middot; <a href="${raw}" target="_blank">raw</a></div>` + designRef(d, rel);
  // The wave view states the cap in its own terms (how much of the SWEEP was
  // read, and that the run's end is unknown), so the generic banner would be
  // the same fact twice in different words. And "open raw for all of it" is
  // only true locally: a remote raw fetch is itself capped, so promising the
  // whole 7.2 GB file there would be a promise the transport cannot keep.
  const trunc = (d.truncated && d.kind !== "wave")
    ? `<div class="warn">Showing the first ${kb(d.shown)} of ${kb(d.size)} &mdash;
       this file is over the read budget.` +
      (d.remote ? ` The raw fetch is capped too &mdash; the whole file stays on
       ${esc(d.host)}.` : ` Open <a href="${raw}" target="_blank">raw</a>
       for all of it.`) + `</div>` : "";
  let body;
  switch (d.kind) {
    case "image": body = `<img src="${raw}">`; break;
    case "svg":
      body = d.nets ? crossProbe(d, rel) : `<img src="${raw}">`;
      break;
    case "html":  body = `<iframe src="${raw}" style="width:100%;height:80vh;border:1px solid #8884"></iframe>`; break;
    case "json": {
      const special = d.abstract ? trackMap(d.abstract)
        : d.abstract_error ? `<div class="warn">track map: ${esc(d.abstract_error)}</div>`
        : d.design ? designView(d.design, rel)
        : d.design_lib ? libView(d.design_lib, rel) : "";
      body = special +
        `<details ${special ? "" : "open"}><summary>raw JSON</summary>` +
        `<pre>${esc(d.parsed !== null && d.parsed !== undefined
             ? JSON.stringify(d.parsed, null, 1) : d.text)}</pre></details>`;
      break; }
    case "jsonl": {
      const cols = [...new Set(d.rows.flatMap(r => Object.keys(r)))].slice(0, 12);
      body = `<table><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr>` +
        d.rows.map(r => `<tr>${cols.map(c => `<td>${esc(
          r[c] === undefined ? "" : typeof r[c] === "object"
            ? JSON.stringify(r[c]) : r[c])}</td>`).join("")}</tr>`).join("") +
        `</table>`;
      break; }
    case "wave": body = waveView(d, rel); break;
    case "binary": {
      const g = d.gds;
      if (d.remote_gds) {
        // No cell/layer summary: that needs the WHOLE stream, and pulling a
        // layout to count its cells is exactly what the remote render avoids.
        body = gdsActions(d, rel) + `<div id="gdsrender"></div>` +
          `<p class="muted">${kb(d.size)} on ${esc(d.host)}. Rendered on the
             cluster &mdash; only the PNG crosses the wire.</p>`;
      } else if (!g) {
        body = `<p class="muted">Binary file &mdash; no preview.
          <a href="${raw}" target="_blank">Download</a> (${kb(d.size)}).</p>`;
      } else if (g.too_big) {
        body = `<p class="muted">GDS, ${kb(g.size)} &mdash; too large to scan.
          <a href="${raw}" target="_blank">Download</a>.</p>`;
      } else {
        const rows = g.layers.map(([k, n]) =>
          `<tr><td>${esc(k)}</td><td>${n}</td></tr>`).join("");
        body = `<table>
            <tr><th>cells</th><td>${g.n_cells}</td></tr>
            <tr><th>boundaries</th><td>${g.counts.boundary}</td></tr>
            <tr><th>paths</th><td>${g.counts.path}</td></tr>
            <tr><th>refs</th><td>${g.counts.sref}</td></tr>
          </table>
          <p class="muted">Top-level cells are the last few listed.</p>
          <p><b>Cells</b> (${g.n_cells}): ${esc(g.cells.slice(-12).join(", "))}
             ${g.n_cells > 12 ? " &hellip;" : ""}</p>
          <p><b>Layers present</b> (layer/datatype, element count)</p>
          <table><tr><th>layer</th><th>n</th></tr>${rows}</table>`;
        body = gdsActions(d, rel) + `<div id="gdsrender"></div>` + body;
      }
      break; }
    default:
      body = `<pre>${esc(d.text)}</pre>`;
  }
  $("#view").innerHTML = head + trunc + body;
  // A design-record link (a BOM entry, the library) is a path in the SAME
  // root, opened through the same confined route as a listing click.
  $("#view").querySelectorAll("[data-open]").forEach(a => a.onclick = e => {
    e.preventDefault(); view(a.dataset.open); });
  if (d.kind === "binary" && (d.remote_gds || (d.gds && !d.gds.too_big)))
    wireGds(rel);
  if (d.nets) wireCrossProbe(d, rel);
  if (d.abstract) wireTrackMap();
  if (d.wave) wireWave(rel, d);
}

// ---- the design record (designdb: library / cell / view) ------------------
// Three questions, answered from the served repo's own manifests and nothing
// else: which captured view is THIS file, what does a cell.json hold and
// which of its views are bound, and what does a library hold.

// Where the manifest tree is, relative to the current root, derived from the
// path of the manifest being viewed: `<...>/<lib>/<cell>/cell.json` puts the
// tree three components up. Links to sibling cells are then paths in the
// SAME root, confined like any other click.
function designBase(rel, depth) {
  const parts = rel.split("/");
  return parts.slice(0, Math.max(0, parts.length - depth)).join("/");
}
function cellLink(base, lc) {
  const p = (base ? base + "/" : "") + lc + "/cell.json";
  return `<a data-open="${esc(p)}" href="#">${esc(lc)}</a>`;
}

function designRef(d, rel) {
  const r = d.design_ref;
  if (!r || (!r.hits && !r.skipped && !(r.origin_of || []).length)) return "";
  const parts = [];
  const hits = r.hits || [];
  if (hits.length) {
    parts.push(hits.map(h =>
      `captured as <b>${esc(h.library)}/${esc(h.cell)}</b> view <b>${esc(h.view)}</b>` +
      ` (${esc(h.type)})` +
      (h.bound ? ` <span class="badge b-ok">bound</span>`
               : ` <span class="badge b-warn">not the bound ${esc(h.type)}</span>`)
    ).join("<br>"));
  } else if (r.md5) {
    parts.push(`<span class="badge b-info">in no captured view</span> ` +
      `<span class="muted">md5 ${esc(r.md5.slice(0, 12))}… matches no file in design/</span>`);
  } else if (r.skipped) {
    parts.push(`<span class="muted">view lookup skipped: ${esc(r.skipped)}</span>`);
  }
  // The origin join is the other direction: this PATH is where a view was
  // captured from. With the md5 agreeing it is the same statement twice;
  // with it differing it is designdb's own tracked-drift finding, seen from
  // the file that drifted.
  for (const o of (r.origin_of || [])) {
    const same = hits.some(h => h.library === o.library && h.cell === o.cell && h.view === o.view);
    if (same) continue;
    parts.push(`origin of <b>${esc(o.library)}/${esc(o.cell)}</b> view <b>${esc(o.view)}</b>` +
      (r.md5 ? ` <span class="badge b-bad">bytes differ from the captured md5</span>`
             : ` <span class="muted">(md5 not compared)</span>`));
  }
  if (!parts.length) return "";
  return `<div class="muted" style="margin:.2rem 0 .5rem">${parts.join("<br>")}</div>`;
}

function designView(f, rel) {
  const base = designBase(rel, 3);
  const rows = f.views.map(v => {
    const files = v.files.map(x =>
      `<div><code>${esc((x.path || "").split("/").pop() || "")}</code>` +
      ` <span class="muted">${kb(x.bytes)} · ${esc((x.md5 || "").slice(0, 10))}…</span>` +
      (x.origin ? `<div class="muted" style="font-size:10px">from ${esc(x.origin)}</div>` : "") +
      `</div>`).join("");
    const prov = Object.entries(v.provenance || {})
      .filter(([k]) => !["from", "census"].includes(k))
      .map(([k, val]) => `${esc(k)}: ${esc(typeof val === "object" ? JSON.stringify(val) : val)}`)
      .join(" · ");
    return `<tr${v.bound ? ' style="font-weight:600"' : ""}>` +
      `<td>${esc(v.view)}${v.bound ? ' <span class="badge b-ok">bound</span>' : ""}` +
      `${v.superseded ? ' <span class="badge b-info">superseded</span>' : ""}</td>` +
      `<td>${esc(v.type)}</td><td>${files}</td>` +
      `<td>${esc(v.from || "")}${prov ? `<div class="muted" style="font-size:10px">${prov}</div>` : ""}</td></tr>`;
  }).join("");
  const newer = (f.newer || []).map(([t, b, n]) =>
    `<div class="warn"><b>${esc(n)}</b> is a newer ${esc(t)} than the bound <b>${esc(b)}</b>
     and has not been adopted — <code>designdb rebind</code> is the act that would.</div>`).join("");
  const bom = (f.bom || []).map(lc => {
    const cnt = (f.bom_counts || {})[lc.split("/").pop()];
    return `<li>${cellLink(base, lc)}${cnt ? ` <span class="muted">×${cnt}</span>` : ""}</li>`;
  }).join("");
  const ext = Object.entries(f.bom_external || {}).map(([k, n]) =>
    `<li><code>${esc(k)}</code> <span class="muted">×${n}</span></li>`).join("");
  return `<div class="tmfacts"><b>${esc(f.library)}</b> / <b>${esc(f.cell)}</b>
      <span class="muted">· ${f.n_bound} bound of ${f.n_views} views ·
      <a data-open="${esc(base ? base + "/" + f.library + "/lib.json" : f.library + "/lib.json")}" href="#">library</a></span></div>` +
    newer +
    `<table><tr><th>view</th><th>type</th><th>files</th><th>provenance</th></tr>${rows}</table>` +
    (bom ? `<p><b>Bill of materials</b> (cells of this record the layout instantiates)</p><ul>${bom}</ul>` : "") +
    (ext ? `<p><b>External masters</b> (instantiated from libraries outside the record)</p><ul>${ext}</ul>` : "");
}

function libView(l, rel) {
  const base = designBase(rel, 2);
  const cells = (l.cells || []).map(c => `<li>${cellLink(base, l.library + "/" + c)}</li>`).join("");
  return `<div class="tmfacts"><b>${esc(l.library)}</b>
      <span class="muted">· design library · ${(l.cells || []).length} cells</span></div>` +
    (l.project ? `<p>${esc(l.project)}</p>` : "") +
    (l.note ? `<p class="muted">${esc(l.note)}</p>` : "") +
    (cells ? `<ul>${cells}</ul>` : `<p class="muted">no cell.json under this library yet</p>`);
}

// ---- schematic <-> layout cross-probe (plan §6.4) ------------------------
// ONE net selection drives BOTH pictures. Neither SVG knows the other exists:
// the schematic tags its leads and labels with `data-net`, the layout track
// map tags its rectangles the same way, and highlighting is asking both the
// same question. No coordinate mapping, no index built ahead of time — the
// two were generated from one netlist, so the names already agree.
let CP_NET = null;

function crossProbe(d, rel) {
  const nets = d.nets || [];
  const cands = d.crossprobe || [];
  const chips = nets.map(n =>
    `<span class="netchip ${CP_NET === n ? "sel" : ""}"
       data-cpnet="${esc(n)}">${esc(n)}</span>`).join("");
  // The pairing is OFFERED, never assumed. A candidate scoring zero shared
  // nets is not in the list at all, and the score rides on every option so
  // "8 of 10" is visible before you pick it — on the real artifacts the right
  // layout wins 9/10 against 5/10 for the runner-up, and that margin is the
  // evidence the join is sound rather than a filename coincidence.
  const pick = cands.length
    ? `<label class="muted">layout
        <select id="cp-layout"><option value="">— none —</option>` +
      cands.map(c => `<option value="${esc(c.root)}|${esc(c.rel)}">${esc(c.name)}
         (${c.shared}/${c.total} nets, ${esc(c.root)})</option>`).join("") +
      `</select></label>`
    : `<span class="muted">no layout abstract under this root shares a net
       with this schematic</span>`;
  const op = d.op && Object.keys(d.op.nodes || {}).length
    ? `<span class="muted">· op point: ${Object.keys(d.op.nodes).length}
       node voltages, ${Object.keys(d.op.devices || {}).length} devices</span>`
    : "";
  return `<div class="tmfacts">${pick} ${op}
      <span class="muted">click a net to follow it through both views</span>
      </div>
    <div class="netbar">${chips}</div>
    <div id="cp-sch" class="cpview">${d.svg}</div>
    <div id="cp-lay"></div>`;
}

// Dim everything that is not the chosen net, in whichever pictures are on
// screen. Exactly the track map's rule (§10.6) applied to two SVGs at once:
// on vref it dimmed 236 of 249 shapes, and the answer to "which of these is
// vrefn" was immediate.
function cpPaint() {
  for (const box of document.querySelectorAll(".cpview")) {
    for (const el of box.querySelectorAll("[data-net]")) {
      const on = !CP_NET || el.dataset.net === CP_NET;
      el.style.opacity = on ? "" : "0.12";
      el.style.strokeWidth = (CP_NET && el.dataset.net === CP_NET)
        ? "2.6" : "";
    }
  }
  for (const c of document.querySelectorAll("[data-cpnet]"))
    c.classList.toggle("sel", c.dataset.cpnet === CP_NET);
  const m = $("#cp-miss");
  if (m) {
    // WHETHER THE NET IS IN THE OTHER PICTURE AT ALL is the useful answer
    // when it is not. Silence there reads as "nothing highlighted, the
    // feature is broken" rather than "this net is not in that layout".
    const lay = $("#cp-lay");
    const has = CP_NET && lay &&
      lay.querySelector('[data-net="' + CSS.escape(CP_NET) + '"]');
    m.innerHTML = (CP_NET && lay && lay.querySelector("[data-net]") && !has)
      ? `<span class="badge b-warn">${esc(CP_NET)} is not in this layout</span>`
      : "";
  }
}

async function wireCrossProbe(d, rel) {
  const bar = $("#view .netbar");
  if (bar) bar.onclick = e => {
    const c = e.target.closest("[data-cpnet]");
    if (!c) return;
    CP_NET = (CP_NET === c.dataset.cpnet) ? null : c.dataset.cpnet;
    cpPaint();
  };
  const sel = $("#cp-layout");
  if (sel) sel.onchange = async () => {
    const lay = $("#cp-lay");
    if (!sel.value) { lay.innerHTML = ""; cpPaint(); return; }
    lay.innerHTML = `<p class="muted">loading the layout…</p>`;
    try {
      const cut = sel.value.indexOf("|");
      const a = await j(`/api/file?root=${encodeURIComponent(sel.value.slice(0, cut))}` +
        `&path=${encodeURIComponent(sel.value.slice(cut + 1))}`);
      lay.innerHTML = a.abstract
        ? `<div class="crumb">${esc(sel.value.replace("|", " / "))}</div>
           <span id="cp-miss"></span>
           <div class="cpview">${a.abstract.svg}</div>`
        : `<div class="warn">that file has no track map</div>`;
    } catch (err) {
      lay.innerHTML = `<div class="err">${esc(err.message)}</div>`;
    }
    cpPaint();
  };
  cpPaint();
}

// ---- what a click is about to cost (plan §10.9) --------------------------
// The rule: TELL when it is worth mentioning, ASK when the wait would read as
// a hang. Only the second one interrupts, and only once per path -- a warning
// that appears on every refresh is a warning nobody reads.
function askFirst(d, rel) {
  const c = d.cost || {};
  const cap = c.capped
    ? `<p class="muted">Only the first ${kb(c.reads)} is ever read — a
       waveform needs t=0, so this is the head of the file, not the tail.
       That is why the estimate is for ${kb(c.reads)} and not ${kb(c.size)}.</p>`
    : "";
  $("#view").innerHTML = `<div class="crumb">${esc(rel)}
      <span class="muted">${kb(d.size)}</span></div>
    <div class="warn"><b>This one will take a moment.</b>
      ${esc(c.text || "")}${d.host ? " on " + esc(d.host) : ""}.
      <p><button id="btn-go">Read it anyway</button>
      <span class="muted">nothing has been read yet</span></p>${cap}</div>`;
  const b = $("#btn-go");
  if (b) b.onclick = () => { GO.add(ROOT + "\\u0000" + rel); view(rel); };
}

// The same numbers, phrased as a note rather than a question.
function costNote(c) {
  if (!c || !c.mention || c.seconds == null) return "";
  return `<span class="muted" title="${esc(c.basis === "measured"
      ? c.n + " measured run(s) on this machine" : "from a recorded measurement")}">
    ~${c.seconds < 90 ? Math.round(c.seconds) + " s"
                      : Math.round(c.seconds / 60) + " min"}</span>`;
}

// The engine's own model, drawn (plan §6.1). The SVG arrives without a
// script so it stays embeddable anywhere; the interactivity is added HERE,
// by injecting it inline and keying on the net each shape carries.
function trackMap(a) {
  const f = a.facts || {};
  const ov = (f.overlapping_lanes || []).map(l =>
    `<div class="warn" style="margin:.2rem 0"><b>lane y=${l.y}</b> — ` +
    l.overlap.map(o => `${esc(o.nets[0])} × ${esc(o.nets[1])} overlap
      ${o.um} µm`).join("; ") +
    (l.rails.length ? ` <span class="muted">(rail: ${esc(l.rails.join(", "))})</span>` : "") +
    `</div>`).join("");
  const nets = (f.nets || []).map(n =>
    `<span class="netchip" data-hl="${esc(n)}">${esc(n)}</span>`).join("");
  return `<div class="tmfacts" id="tmctl">
      <b>${esc(f.cell || "")}</b>
      <button data-zoom="fit">Fit to lanes</button>
      <button data-zoom="reset">Whole block</button>
      <span class="muted">scroll to zoom · drag to pan</span><br>
      ${zoomBox("tzoom", ["x0 ", "y0 ", "x1 ", "y1 "],
                [f.bbox_x0, f.bbox_y0, f.bbox_x1, f.bbox_y1], "µm")}
      <span class="muted">${f.w_um} × ${f.h_um} µm ·
        ${f.n_pins} pins · ${f.n_rails} rails · ${f.n_tracks} tracks ·
        ${f.n_m3} M3 · keepouts ${f.n_ko4}/${f.n_ko6}/${f.n_ko8} (M4/M6/M8) ·
        ${(f.shared_lanes || []).length} shared lanes</span></div>` +
    ov +
    `<div class="netbar">${nets}</div>` +
    `<div id="tmap">${a.svg}</div>`;
}

// Click a net to isolate it. The abstract is a picture of ~550 rectangles on
// vref; "which of these is vrefn" is the question, and dimming everything
// else answers it far better than a legend.
// ---- a typed view window, shared by every zoomable view ------------------
// Mouse zoom finds a region; it cannot express one. "Show me 0.9 to 1.1 V"
// and "the same window as the last plot" are questions only numbers answer,
// and they are the questions you ask when comparing two runs.
function zoomBox(id, labels, vals, hint) {
  const f = (v) => v == null ? "" : (typeof v === "number" ?
    (Math.abs(v) < 1e-3 || Math.abs(v) >= 1e5 ? v.toExponential(4) :
     String(+v.toPrecision(6))) : v);
  return `<div class="zbox" id="${id}">` +
    labels.map((L, i) =>
      `<label>${esc(L)}<input data-z="${i}" value="${esc(f(vals[i]))}"></label>`
    ).join("") +
    `<button data-zapply="1">Apply</button>
     <button data-zall="1">Full</button>
     <span class="muted">${esc(hint || "")}</span></div>`;
}

//: Read a zoom box -> [numbers] or null if any field is blank/unparseable.
function zoomVals(id) {
  const box = $("#" + id);
  if (!box) return null;
  const out = [];
  for (const el of box.querySelectorAll("[data-z]")) {
    const v = parseFloat(el.value);
    if (!isFinite(v)) return null;
    out.push(v);
  }
  return out;
}

function wireZoomBox(id, onApply, onFull) {
  const box = $("#" + id);
  if (!box) return;
  box.onclick = e => {
    if (e.target.closest("[data-zapply]")) { const v = zoomVals(id);
      if (v) onApply(v); }
    else if (e.target.closest("[data-zall]")) onFull();
  };
  box.onkeydown = e => {
    if (e.key === "Enter") { const v = zoomVals(id); if (v) onApply(v); }
  };
}

// ---- waveforms (plan §6.3), including runs that have not finished --------
let WAVE_TIMER = null, WAVE_SIGS = null;
let WAVE_MODE = "group", WAVE_X = null, WAVE_Y = null;
// The complex part (AC) and the two axis scales. null on a scale means "as
// the analysis wants it" -- the file says whether the sweep is logarithmic
// and that answer must survive until someone actually overrides it.
let WAVE_PART = "db", WAVE_XLOG = null, WAVE_YLOG = null;

// An operating point has no sweep, so there is nothing to plot: the rows do
// not share a unit, let alone an axis. It gets a TABLE — which is what the
// file is. Sorting and filtering, because a chip-level op point is every node
// voltage and every branch current, and the question is always about one of
// them.
let OP_SORT = "file", OP_DESC = false, OP_FILTER = "";

function opView(d, rel) {
  const w = d.wave || {};
  let rows = (w.points || []).map((r, i) => ({...r, i}));
  const f = OP_FILTER.trim().toLowerCase();
  if (f) rows = rows.filter(r => r.name.toLowerCase().includes(f));
  const key = OP_SORT;
  if (key !== "file") rows.sort((a, b) => {
    // Numbers compare as numbers and names as text; sorting "-6.8e-05"
    // lexically would file every negative current under "-".
    const va = key === "value" ? a.value : (a[key] || "");
    const vb = key === "value" ? b.value : (b[key] || "");
    return (va > vb) - (va < vb);
  });
  if (OP_DESC) rows.reverse();
  const cap = w.n_shown < w.n_values
    ? `<div class="warn">Showing ${w.n_shown} of ${w.n_values} values —
       the rest are past the read cap.</div>` : "";
  const head = ["name", "value", "units", "file"].map(k =>
    `<th data-opsort="${k}" style="cursor:pointer">${k === "file" ? "#" : k}
      ${OP_SORT === k ? (OP_DESC ? "▾" : "▴") : ""}</th>`).join("");
  return `<div class="psess"><h4>
      <span class="badge b-ok">operating point</span>
      ${esc(w.name || "")} <span class="muted">${esc(w.analysis || "")} ·
      ${w.n_values} value(s) · no sweep</span></h4></div>` + cap +
    `<div class="netbar"><input id="opfilter" placeholder="filter by name"
       value="${esc(OP_FILTER)}" style="font:11px ui-monospace,monospace">
       <span class="muted">${rows.length} shown</span></div>
     <table id="optab"><tr>${head}</tr>` +
    rows.map(r => `<tr><td>${esc(r.name)}</td>
      <td style="text-align:right">${esc(eng(r.value))}</td>
      <td class="muted">${esc(r.units || r.type || "")}</td>
      <td class="muted">${r.i}</td></tr>`).join("") + `</table>`;
}

function wireOp(rel) {
  const t = $("#optab");
  if (t) t.onclick = e => {
    const h = e.target.closest("[data-opsort]");
    if (!h) return;
    const k = h.dataset.opsort;
    OP_DESC = (OP_SORT === k) ? !OP_DESC : false;
    OP_SORT = k;
    view(rel);
  };
  const f = $("#opfilter");
  if (f) {
    f.oninput = () => { OP_FILTER = f.value; view(rel); };
    // The pane re-renders on every keystroke, so the caret has to be put back
    // or typing a second character lands at the front of the box.
    f.focus();
    f.setSelectionRange(f.value.length, f.value.length);
  }
}

// A file that names what was swept and holds no curves of its own: the bare
// `pac1.pac` over its eleven harmonics, `.montecarlo` over its iterations,
// `.sweep` over three temperatures. Drawing it produced an empty frame that
// said "no samples yet" -- the third time that exact failure appeared, after
// the DC sweep and the operating point.
function indexView(d, rel) {
  const w = d.wave || {};
  const sib = w.siblings || [];
  const vals = (w.index_values || []).map(v => eng(v)).join(", ");
  return `<div class="psess"><h4>
      <span class="badge b-info">index</span>
      ${esc(w.name || "")} <span class="muted">${esc(w.analysis || "")} ·
      ${w.n_index} ${esc(w.sweep || "value")}(s), no curves of its own
      </span></h4></div>
    <p class="muted">${esc(w.sweep || "swept")}: ${esc(vals)}</p>` +
    (sib.length
      ? `<p><b>${sib.length} result file(s)</b> — the curves are in these:</p>
         <div class="netbar">` + sib.map(x =>
           `<span class="netchip" data-open="${esc(x.rel)}">${esc(w.sweep || "")}
             ${esc(x.label)}</span>`).join("") + `</div>`
      : `<p class="muted">No sibling result files here — the run may have been
         cleaned, or its results live one directory up.</p>`);
}

function waveView(d, rel) {
  const w = d.wave || {};
  if (w.is_index) return indexView(d, rel);
  if (w.is_point) return opView(d, rel);
  if (!w.n_traces && !w.n_points)
    return `<div class="pev">${esc(d.text || "no samples yet")}</div>`;
  // A capped read and a partial run produce the SAME fraction and mean
  // entirely different things: one is how much of the sweep exists, the
  // other how much of it we bothered to read. Saying "56% complete" about a
  // finished 7.2 GB file we read 64 MB of would be the worst answer here.
  const pct = w.fraction == null ? "" : (w.state === "capped"
    ? ` · read to <b>${(100 * w.fraction).toFixed(1)}%</b> of ${eng(w.x_stop)}`
    : ` · <b>${(100 * w.fraction).toFixed(1)}%</b> of ${eng(w.x_stop)}`);
  const tone = w.state === "complete" ? "b-ok"
             : w.state === "running" ? "b-run"
             : w.state === "capped" ? "b-warn" : "b-info";
  // The state is stated FIRST and in the same breath as the numbers. A
  // partial curve that looks final is the one way this feature does harm.
  // Where it was read and what that cost. On the cluster this is the whole
  // difference between "the pane is stuck" and "asic7 is parsing 38 MB".
  const where = d.remote
    ? ` · <span class="muted">read on ${esc(d.host)} in
        ${w.elapsed_s}s (parse ${w.remote_parse_s}s, plot ${w.remote_plot_s}s)</span>`
    : "";
  const banner = `<div class="psess"><h4>
      <span class="badge ${tone}">${esc(w.state)}</span>
      ${esc(w.name || "")} <span class="muted">${esc(w.analysis || "")} ·
      ${w.n_points} points · ${w.n_traces} traces${w.n_novalue
        ? ` (${w.n_traces - w.n_novalue} plottable)` : ""} ·
      ${esc(w.sweep || "x")} to ${eng(w.x_last)}${pct}</span>${where}
      ${w.state === "running" || w.state === "starting" ?
        `<label class="muted"><input type="checkbox" id="wwatch" checked>
         watch</label>` : ""}</h4></div>`;
  // WATCH IS NOT OFFERED ON A CAPPED READ, and that is not a UI nicety: the
  // head of a file never changes, so polling one re-reads the same 64 MB to
  // draw the same picture. The refresh that is worth 4 seconds of a machine
  // is the one that can show something new.
  // PER-DEVICE NOISE IS A TABLE. 5886 devices is a ranking, not a picture --
  // and the ranking is the question a noise plot is opened to ask. Verified
  // against the real run: the device contributions sum to the integrated
  // output noise exactly (ratio 1.000000), so the shares are shares of the
  // noise you measure, not of a different denominator.
  const nz = w.noise;
  const nzTable = !nz ? "" :
    `<details open><summary><b>per-device noise</b> —
       ${eng(nz.rms_total)}V rms over ${eng(nz.band[0])}–${eng(nz.band[1])}Hz,
       ${nz.n_devices} device(s)${w.state === "capped"
         ? ' <span class="badge b-warn">band is what was READ</span>' : ""}
       </summary>${w.state === "capped"
         ? `<p class="muted">The read stopped at the budget, so this integral
            covers ${eng(nz.band[1])}Hz and not the whole sweep — the shares
            are still shares, but the rms is of the band shown.</p>` : ""}
     <table><tr><th>device</th><th>share</th><th>rms</th><th>dominant</th>
       <th>V²/Hz at ${eng(w.x_last)}</th><th>master</th></tr>` +
    nz.rows.map(r => `<tr><td>${esc(r.inst)}</td>
      <td style="text-align:right"><b>${(100 * r.share).toFixed(1)}%</b></td>
      <td style="text-align:right">${eng(r.rms)}V</td>
      <td>${esc(r.dominant)}</td>
      <td style="text-align:right" class="muted">${eng(r.last)}</td>
      <td class="muted">${esc(r.type || "")}</td></tr>`).join("") +
    `</table>` + (nz.shown < nz.n_devices
      ? `<p class="muted">showing the ${nz.shown} largest of
         ${nz.n_devices}</p>` : "") + `</details>`;
  const cap = w.state === "capped"
    ? `<div class="warn">Read the first ${kb(d.shown)} of ${kb(d.size)} —
       everything past that is unread, and whether the run FINISHED is
       unknown from this much. The shaded remainder is missing data, not
       necessarily missing time.</div>` : "";
  // A CAP THAT SAYS SO. 73 traces in one frame is not a plot of any of them,
  // so only the first few are drawn -- but a silent cap would be the pane
  // lying about what is in the file, and the chips below make lifting it a
  // click. Measured on the cluster's sar_kernel run: all 73 is a 1.29 MB SVG.
  const capped = w.dropped
    ? `<div class="warn">Drawing ${w.drawn.length} of ${w.n_traces} traces —
       tick the ones you want below, or
       <span class="netchip" data-sigall="1">draw all ${w.n_traces}</span>.</div>`
    : "";
  // CHECKBOXES, not chips. A chip that is "selected" looks exactly like a
  // chip that is the only one left, and on a 98-trace noise file the state of
  // the selection was unreadable at a glance. A box is either ticked or it is
  // not, and the browser draws that better than any class of ours.
  //
  // Only DRAWABLE traces get one. A noise file lists ten traces of which nine
  // are per-device STRUCT blocks with no curve in them; offering a box that
  // cannot change the picture is a control that lies.
  const able = w.traces || [];
  const chosen = WAVE_SIGS || w.drawn || [];
  const boxes = able.map(t => {
    const r = (w.ranges || {})[t];
    const u = (w.units || {})[t] || "";
    return `<label class="sigbox" title="${r ? esc(eng(r[0]) + " … " +
        eng(r[1]) + " " + u) : ""}">
      <input type="checkbox" data-sig="${esc(t)}"
        ${chosen.includes(t) ? "checked" : ""}>${esc(t)}
      ${u ? `<span class="muted">${esc(u)}</span>` : ""}</label>`;
  }).join("");
  const hidden = w.n_novalue || 0;
  const modes = ["group", "split", "overlay"].map(m =>
    `<span class="netchip ${WAVE_MODE === m ? "sel" : ""}"
       data-mode="${m}">${m}</span>`).join("");
  // The complex part is offered ONLY when the file has complex traces, which
  // is to say on an AC sweep. A dB/phase switch on a transient is a control
  // for a question that analysis cannot be asked.
  const parts = w.any_complex
    ? ` <span class="muted">|</span> ` +
      ["db", "mag", "phase", "real", "imag"].map(p =>
        `<span class="netchip ${WAVE_PART === p ? "sel" : ""}"
           data-part="${p}">${p}</span>`).join("")
    : "";
  const scales = `<span class="muted">|</span>
    <label class="sigbox"><input type="checkbox" id="wxlog"
      ${w.xlog ? "checked" : ""}>log ${esc(w.sweep || "x")}</label>
    <label class="sigbox"><input type="checkbox" id="wylog"
      ${w.ylog ? "checked" : ""}>log y</label>`;
  const zoomed = WAVE_X
    ? `<span class="netchip" data-zreset="1">reset zoom</span>
       <span class="muted">${eng(WAVE_X[0])} … ${eng(WAVE_X[1])}</span>` : "";
  // The zoom box defaults to the range the PICTURE spans, which is not always
  // the header's: a noise file declares no `start` at all, so the header
  // answer is 0 — an illegal origin for the log axis it actually has.
  const sv = svgRange(d.svg);
  const vx = WAVE_X || [sv ? sv[0] : (w.x_first != null ? w.x_first : w.x_start),
                        sv ? sv[1] : w.x_stop];
  const xl = (w.sweep || "x") + (w.sweep_units ? " (" + w.sweep_units + ")" : "");
  return banner + cap + nzTable + capped +
    `<div class="netbar">${modes}${parts}${scales}${zoomed}
       <span class="muted">scroll to zoom · drag to pan · shift+scroll
       for a coarse step</span></div>` +
    zoomBox("wzoom", [esc(xl) + " from ", "to ", "y0 ", "y1 "],
            [vx[0], vx[1], WAVE_Y && WAVE_Y[0], WAVE_Y && WAVE_Y[1]],
            "y blank = autoscale per panel") +
    `<div class="netbar sigs" id="wsigs">
       <span class="netchip" data-sigpick="all">all</span>
       <span class="netchip" data-sigpick="none">none</span>
       ${hidden > 0 ? `<span class="muted">${hidden} of ${w.n_traces}
         carry no curve — per-device noise blocks, not plottable</span>` : ""}
       ${boxes}</div>` +
    `<div id="wsvg">${d.svg || ""}</div>`;
}

// The full sweep range, out of the picture's own geometry contract rather
// than re-derived from the summary. One source, and it is the one the zoom
// and pan handlers already trust.
function svgRange(svg) {
  if (!svg) return null;
  const m0 = /data-full0="([^"]*)"/.exec(svg);
  const m1 = /data-full1="([^"]*)"/.exec(svg);
  if (!m0 || !m1) return null;
  const a = parseFloat(m0[1]), b = parseFloat(m1[1]);
  return (isFinite(a) && isFinite(b)) ? [a, b] : null;
}

const eng = v => {
  if (v == null) return "?";
  if (v === 0) return "0";
  const a = Math.abs(v);
  const u = [[1e12,"T"],[1e9,"G"],[1e6,"M"],[1e3,"k"],[1,""],[1e-3,"m"],
             [1e-6,"u"],[1e-9,"n"],[1e-12,"p"],[1e-15,"f"]];
  for (const [lim, s] of u) if (a >= lim) return (v/lim).toPrecision(3) + s;
  // Below femto there is no suffix worth having, and `String(v)` printed a
  // noise density as 4.220888540488141e-17 in a table column. Noise powers
  // live at 1e-17..1e-24 routinely, so the fallback has to be readable
  // rather than complete.
  return v.toExponential(2);
};

// ZOOM AND PAN ON TIME. Re-fetches rather than transforming the SVG: the
// picture is a decimated envelope, so magnifying it shows nothing that was
// not already on screen. Moving the window makes the server re-bucket the
// samples inside it -- on the lif transient a 1% window goes from ~1200
// points across the sweep to every sample it contains.
function wireWaveZoom(rel) {
  const svg = $("#wsvg svg");
  if (!svg) return;
  const L = +svg.dataset.l, PW = +svg.dataset.pw;
  const f0 = +svg.dataset.full0, f1 = +svg.dataset.full1;
  let x0 = +svg.dataset.x0, x1 = +svg.dataset.x1;
  // A LOG AXIS ZOOMS IN DECADES. Interpolating linearly across five decades
  // puts the cursor in the wrong one — at the midpoint of a 1 kHz–100 MHz
  // sweep a linear reading says 50 MHz where the picture says 316 kHz. The
  // renderer states which it drew (`data-xlog`) rather than leaving the
  // client to infer it from the file extension.
  const LOG = svg.dataset.xlog === "1";
  const fwd = v => LOG ? Math.log10(Math.max(v, Number.MIN_VALUE)) : v;
  const inv = v => LOG ? Math.pow(10, v) : v;
  let timer = null;
  const commit = () => {
    clearTimeout(timer);
    // Debounced: a wheel gesture is a burst of events, and one request per
    // notch would queue a dozen parses to draw the last one.
    timer = setTimeout(() => {
      WAVE_X = (x0 <= f0 && x1 >= f1) ? null : [x0, x1];
      view(rel);
    }, 140);
  };
  // Everything below works in the AXIS's own space -- decades when the axis
  // is logarithmic, values when it is not -- so one implementation serves
  // both and neither has a special case in it.
  const timeAt = ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) / r.width * (+svg.getAttribute("width"));
    const f = Math.min(1, Math.max(0, (px - L) / PW));
    return fwd(x0) + f * (fwd(x1) - fwd(x0));
  };
  svg.addEventListener("wheel", e => {
    e.preventDefault();
    const t = timeAt(e);
    const a0 = fwd(x0), a1 = fwd(x1);
    const k = (e.deltaY < 0 ? 1 : -1) * (e.shiftKey ? 1 : 0.35);
    const span = (a1 - a0) * Math.pow(0.5, k);
    // Keep the point under the cursor fixed; anything else feels broken.
    const frac = (t - a0) / (a1 - a0);
    const n0 = t - frac * span;
    x0 = inv(n0); x1 = inv(n0 + span);
    if ((fwd(x1) - fwd(x0)) >= (fwd(f1) - fwd(f0))) { x0 = f0; x1 = f1; }
    commit();
  }, {passive: false});
  // POINTER CAPTURE, not window listeners. The pane re-renders on every zoom
  // notch and every watch refresh, and window-level handlers from the old
  // SVG survive it -- so a stale closure kept firing with its own stale
  // range and fought the live one, which is what broke panning. Capture
  // keeps the whole gesture on the element that started it, and a detached
  // element receives nothing.
  let drag = null;
  svg.addEventListener("pointerdown", e => {
    // preventDefault as well as user-select:none -- the CSS stops the
    // highlight, this stops the browser starting a text-selection drag at
    // all. Without both, a drag grabs the axis labels and the plot stays put.
    e.preventDefault();
    drag = {x: e.clientX, a0: fwd(x0), a1: fwd(x1)};
    svg.setPointerCapture(e.pointerId);
    svg.style.cursor = "grabbing";
  });
  svg.addEventListener("pointerup", e => {
    drag = null;
    try { svg.releasePointerCapture(e.pointerId); } catch (_) {}
    svg.style.cursor = "";
  });
  svg.addEventListener("pointermove", e => {
    if (!drag) return;
    const r = svg.getBoundingClientRect();
    const dt = (e.clientX - drag.x) / r.width * (+svg.getAttribute("width"))
               / PW * (drag.a1 - drag.a0);
    x0 = inv(drag.a0 - dt); x1 = inv(drag.a1 - dt);
    commit();
  });
}

function wireWave(rel, d) {
  const w = d.wave || {};
  if (w.is_index) {
    const bar = $("#view .netbar");
    if (bar) bar.onclick = e => {
      const c = e.target.closest("[data-open]");
      if (c) view(c.dataset.open);
    };
    return;
  }
  if (w.is_point) { wireOp(rel); return; }
  const allBtn = $("[data-sigall]");
  if (allBtn) allBtn.onclick = () => {
    WAVE_SIGS = (w.traces || []).slice();
    view(rel);
  };
  const modebar = $("#view .netbar");
  if (modebar) modebar.onclick = e => {
    const m = e.target.closest("[data-mode]");
    if (m) { WAVE_MODE = m.dataset.mode; view(rel); return; }
    const p = e.target.closest("[data-part]");
    if (p) {
      // The y window belongs to the old quantity. Keeping "0.9 to 1.1" while
      // switching volts to degrees would frame an empty strip of a picture
      // that is fine, and read as a broken plot.
      WAVE_PART = p.dataset.part; WAVE_Y = null; WAVE_YLOG = null;
      view(rel); return;
    }
    if (e.target.closest("[data-zreset]")) { WAVE_X = null; view(rel); }
  };
  const xlog = $("#wxlog"), ylog = $("#wylog");
  if (xlog) xlog.onchange = () => {
    // A log x window taken from a linear view can start at 0, which no log
    // axis can show -- so the switch drops the window rather than clamping it
    // to something the user never asked for.
    WAVE_XLOG = xlog.checked; WAVE_X = null; view(rel);
  };
  if (ylog) ylog.onchange = () => { WAVE_YLOG = ylog.checked; WAVE_Y = null;
    view(rel); };
  wireWaveZoom(rel);
  wireZoomBox("wzoom", v => {
    // t0,t1 always; y0,y1 only when BOTH are given -- a half-specified y
    // range is a question with no answer, so it falls back to autoscale.
    WAVE_X = (v[1] > v[0]) ? [v[0], v[1]] : null;
    WAVE_Y = (v.length > 3 && v[3] > v[2]) ? [v[2], v[3]] : null;
    view(rel);
  }, () => { WAVE_X = null; WAVE_Y = null; view(rel); });
  const bar = $("#wsigs");
  if (bar) {
    bar.onchange = e => {
      const c = e.target.closest("input[data-sig]");
      if (!c) return;
      // The first tick starts from what is ON SCREEN, not from every trace in
      // the file. With a cap in force those differ, and starting from "all"
      // would turn one tick into a 73-curve picture.
      if (!WAVE_SIGS) WAVE_SIGS = (w.drawn || w.traces || []).slice();
      const i = WAVE_SIGS.indexOf(c.dataset.sig);
      if (c.checked) { if (i < 0) WAVE_SIGS.push(c.dataset.sig); }
      else if (i >= 0) WAVE_SIGS.splice(i, 1);
      // An EMPTY selection stays empty. Reverting to "all" when the last box
      // is unticked makes the control fight the user at exactly the moment
      // they are being most explicit.
      view(rel);
    };
    bar.onclick = e => {
      const p = e.target.closest("[data-sigpick]");
      if (!p) return;
      WAVE_SIGS = p.dataset.sigpick === "all"
        ? (w.traces || []).slice() : [];
      view(rel);
    };
  }
  // WATCH. The reason for the whole feature: a long transient can run for an
  // hour, and being able to see the first 10% and kill it is worth more than
  // any amount of polish on the finished picture. Polling stops by itself
  // when the run completes -- an interval that outlives its reason is a leak.
  //
  // THE INTERVAL IS SET BY WHAT THE LAST READ COST. Four seconds is right for
  // a local file that parses in 40 ms; for a 38 MB transient on asic7 that
  // takes 3.7 s a fixed 4 s poll would keep the cluster busy essentially
  // continuously to redraw a picture that moves a few pixels. Poll at four
  // times the last read, so the refresh stays a small fraction of the machine
  // it is asking -- and say the number, because a view that quietly slows
  // down is a view you stop trusting.
  if (WAVE_TIMER) { clearInterval(WAVE_TIMER); WAVE_TIMER = null; }
  const box = $("#wwatch");
  const took = (d.cost && d.cost.measured) || 0;
  const every = Math.min(60000, Math.max(4000, Math.round(took * 4) * 1000));
  if (box) {
    if (every > 4000) box.parentNode.insertAdjacentHTML("beforeend",
      ` <span class="muted">every ${every / 1000}s</span>`);
    WAVE_TIMER = setInterval(() => {
      if (!document.getElementById("wwatch") || !$("#wwatch").checked) {
        clearInterval(WAVE_TIMER); WAVE_TIMER = null; return;
      }
      view(rel);
    }, every);
  }
}

// ZOOM AND PAN. Measured on vref: 87% of 585 elements sit in 35% of the
// height, so at one fixed scale the tracks and their labels collapse into an
// illegible band with empty space above -- the same "mud at one zoom" the
// error atlas had to solve. A crop heuristic would have to guess; letting the
// viewBox move does not.
function wireZoom(svg) {
  // The whitespace class needs its backslash DOUBLED: this script lives
  // inside a Python string, where a lone backslash before "s" is an invalid
  // escape that Python only tolerates today and will reject later. Same trap
  // as the path-splitting regex above -- including in this comment, which
  // cannot spell the bad form either.
  const vb0 = svg.getAttribute("viewBox").split(/\\s+/).map(Number);
  let vb = vb0.slice();
  const apply = () => svg.setAttribute("viewBox", vb.join(" "));
  const at = e => {
    const r = svg.getBoundingClientRect();
    return [vb[0] + (e.clientX - r.left) / r.width * vb[2],
            vb[1] + (e.clientY - r.top) / r.height * vb[3]];
  };
  svg.addEventListener("wheel", e => {
    e.preventDefault();
    const [mx, my] = at(e);
    const k = e.deltaY < 0 ? 0.83 : 1.2;
    // keep the point under the cursor fixed -- anything else feels broken
    vb = [mx - (mx - vb[0]) * k, my - (my - vb[1]) * k, vb[2] * k, vb[3] * k];
    apply();
  }, {passive: false});
  // Pointer capture rather than window listeners -- the pane re-renders and
  // leaves stale closures behind otherwise (see wireWaveZoom).
  let drag = null;
  svg.addEventListener("pointerdown", e => {
    e.preventDefault();                 // see wireWaveZoom
    drag = {x: e.clientX, y: e.clientY, vb: vb.slice()};
    svg.setPointerCapture(e.pointerId);
    svg.style.cursor = "grabbing";
  });
  svg.addEventListener("pointerup", e => {
    drag = null;
    try { svg.releasePointerCapture(e.pointerId); } catch (_) {}
    svg.style.cursor = "";
  });
  svg.addEventListener("pointermove", e => {
    if (!drag) return;
    const r = svg.getBoundingClientRect();
    vb = [drag.vb[0] - (e.clientX - drag.x) / r.width * drag.vb[2],
          drag.vb[1] - (e.clientY - drag.y) / r.height * drag.vb[3],
          drag.vb[2], drag.vb[3]];
    apply();
  });
  return {
    reset: () => { vb = vb0.slice(); apply(); },
    // A box in LAYOUT coordinates. abstract_svg flips y arithmetically
    // (fy = ymax - y), so the layout's TOP is the SVG's smallest y and the
    // conversion has to flip back or every typed box comes out mirrored.
    box: (x0, y0, x1, y1) => {
      const top = +svg.dataset.ytop;
      if (!isFinite(top)) return;
      const sy0 = top - y1, sy1 = top - y0;
      vb = [Math.min(x0, x1), Math.min(sy0, sy1),
            Math.abs(x1 - x0) || 1, Math.abs(sy1 - sy0) || 1];
      apply();
    },
    // Frame THE LANES -- the tracks and rails -- not "everything with a net".
    // Measured: fitting to all net-carrying shapes moved the viewBox by 5%,
    // because the M3 includes vertical risers that reach the top and bottom
    // of the block, so the ink really does span it. The lanes are the thing
    // §6.1 exists to inspect, and on vref they occupy 5 um out of 197.
    fit: () => {
      let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9, n = 0;
      svg.querySelectorAll(".trk, .rail").forEach(el => {
        const b = el.getBBox();
        if (!b.width && !b.height) return;
        n++; x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y);
        x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height);
      });
      if (!n) return;
      const mx = (x1 - x0) * 0.04 + 1, my = (y1 - y0) * 0.08 + 1;
      vb = [x0 - mx, y0 - my, (x1 - x0) + 2 * mx, (y1 - y0) + 2 * my];
      apply();
    }};
}

function wireTrackMap() {
  const bar = $(".netbar"), map = $("#tmap");
  if (!bar || !map) return;
  const svg = map.querySelector("svg");
  const z = svg ? wireZoom(svg) : null;
  const ctl = $("#tmctl");
  if (ctl && z) ctl.onclick = e => {
    const b = e.target.closest("[data-zoom]");
    if (!b) return;
    if (b.dataset.zoom === "fit") z.fit(); else z.reset();
  };
  // A typed window, in LAYOUT micrometres. The SVG's y axis is flipped
  // relative to the layout's, so y1 (the top in layout) becomes the small
  // SVG y -- getting that backwards would silently mirror every typed box.
  if (z) wireZoomBox("tzoom", v => z.box(v[0], v[1], v[2], v[3]),
                     () => z.reset());
  let on = null;
  const apply = () => {
    map.querySelectorAll("[data-net]").forEach(el =>
      el.classList.toggle("dim", !!on && el.dataset.net !== on));
    bar.querySelectorAll("[data-hl]").forEach(el =>
      el.classList.toggle("sel", el.dataset.hl === on));
  };
  bar.onclick = e => {
    const c = e.target.closest("[data-hl]");
    if (!c) return;
    on = (on === c.dataset.hl) ? null : c.dataset.hl;
    apply();
  };
}

// The two things you can DO with a layout, as opposed to read about it.
// The sidecar state is shown before the click, not after: KLayout opening with
// no markers looks identical to a clean cell, and the difference is whether
// this GDS came from a DRC run at all.
function gdsActions(d, rel) {
  const sc = d.sidecars || {};
  // The handoff needs a real local file; a cluster layout has none, and
  // there is no klayout binary on the cluster either. Say which, rather than
  // offering a button that cannot work.
  const kl = d.remote
    ? `<button disabled title="the layout is on ${esc(d.host)} — KLayout needs a local file">Open in KLayout</button>`
    : d.klayout
    ? `<button id="btn-klayout">Open in KLayout</button>`
    : `<button disabled title="KLayout is not installed on this machine">Open in KLayout</button>`;
  const side = d.remote ? "" :
    `<p class="muted">sidecars:
      ${sc.lyp ? "layer map" : "<b>no layer map</b> (unnamed layers)"} &middot;
      ${sc.lyrdb ? "DRC markers" : "<b>no markers</b> (not from a DRC run)"}</p>`;
  // The regression comparison, offered only when there is something to
  // compare against: an empty dropdown beside an XOR button is a promise the
  // directory cannot keep.
  const cands = d.xor_candidates || [];
  const xor = (!d.remote && d.xor && cands.length)
    ? `<p><select id="xor-other">` +
      cands.map(c => `<option value="${esc(c.rel)}">${esc(c.label)}</option>`).join("") +
      `</select> <button id="btn-xor">XOR</button>
       <span class="muted" id="xormsg"></span></p><div id="xorout"></div>`
    : "";
  // The estimate goes ON the button. A 1.65 MB stream is 70 seconds of
  // matplotlib, and "Render" with no number is a click you cannot make an
  // informed decision about -- which is how a slow tool becomes a broken one.
  const est = costNote(d.cost);
  return `<p>
    <button id="btn-render" data-est="${(d.cost && d.cost.seconds) || 0}">Render</button>
    ${est} ${kl}
    <span class="muted" id="gdsmsg"></span></p>` +
    (d.remote ? "" : zoomBox("gzoom", ["x0 ", "y0 ", "x1 ", "y1 "],
       [null, null, null, null], "µm — Apply re-renders just that window")) +
    side + xor;
}

function wireGds(rel) {
  const msg = t => { $("#gdsmsg").textContent = t || ""; };
  const btn = $("#btn-render");
  // A raster has no client-side zoom, so a typed crop has to reach back to
  // the layout and re-render just that window. render_gds already takes the
  // four numbers; the only new thing is asking for them.
  async function doRender(win) {
    // A cold render is a subprocess over a quarter-million polygons. A static
    // "this is slow" is not enough at 70 s: the pane COUNTS, against the
    // estimate, so the wait is visibly progressing rather than merely long.
    // Nothing here can report real progress -- matplotlib does not -- and a
    // counter that admits it is guessing beats a fake progress bar.
    if (btn) btn.disabled = true;
    // THE ESTIMATE IS FOR THE WHOLE DIE ONLY. A crop clips before drawing, so
    // it costs what its window holds — measured, 3–4 s against 72 s — and
    // quoting the die's 72 s over a 4-second wait is a worse answer than
    // quoting nothing. Its cost tracks window size and local polygon density,
    // neither of which the file size predicts, so the counter just counts.
    const est = (btn && !win) ? +btn.dataset.est || 0 : 0;
    const t0 = Date.now();
    const tick = setInterval(() => {
      const s = Math.round((Date.now() - t0) / 1000);
      msg(est ? `rendering… ${s} s of about ${Math.round(est)} s`
              : `rendering that window… ${s} s`);
    }, 1000);
    msg(est ? `rendering… about ${Math.round(est)} s`
            : (win ? "rendering that window…" : "rendering… (first time is slow)"));
    try {
      const wq = win
        ? `&x0=${win[0]}&y0=${win[1]}&x1=${win[2]}&y1=${win[3]}` : "";
      const r = await j(`/api/render?root=${encodeURIComponent(ROOT)}` +
        `&path=${encodeURIComponent(rel)}${wq}`);
      clearInterval(tick);
      if (!r.ok) { msg(""); $("#gdsrender").innerHTML =
        `<div class="warn">${esc(r.error || r.state)}</div>`; }
      else { msg(r.state === "cached" ? "cached"
                 : `rendered in ${Math.round(r.seconds || 0)} s`);
             $("#gdsrender").innerHTML = `<img src="${r.url}">`; }
    } catch (err) { clearInterval(tick); msg("");
      $("#gdsrender").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
    clearInterval(tick);
    if (btn) btn.disabled = false;
  }
  if (btn) btn.onclick = () => doRender(null);
  wireZoomBox("gzoom", v => doRender(v), () => doRender(null));
  const x = $("#btn-xor");
  if (x) x.onclick = async () => {
    const other = $("#xor-other").value;
    x.disabled = true;
    $("#xormsg").textContent = "comparing…";
    $("#xorout").innerHTML = "";
    try {
      const r = await j(`/api/xor?root=${encodeURIComponent(ROOT)}` +
        `&path=${encodeURIComponent(rel)}&other=${encodeURIComponent(other)}`);
      if (!r.ok) {
        $("#xormsg").textContent = "";
        $("#xorout").innerHTML = `<div class="warn">${esc(r.error || "failed")}</div>`;
      } else if (r.identical) {
        // EMPTY IS THE PASS. Say it as a verdict, not as an absence.
        $("#xormsg").textContent = "";
        $("#xorout").innerHTML =
          `<div class="xok"><b>IDENTICAL</b> — no geometry moved.
           This is the assertion a geometry-identical regression makes.</div>`;
      } else {
        $("#xormsg").textContent = "";
        $("#xorout").innerHTML =
          `<div class="err"><b>DIFFERENT</b> — ${r.total} shape(s) moved on ` +
          esc(r.layers.map(l => `${l.layer} (${l.shapes})`).join(", ")) + `</div>` +
          (r.url ? `<img src="${r.url}">`
                 : `<p class="muted">no diff render</p>`);
      }
    } catch (err) {
      $("#xormsg").textContent = "";
      $("#xorout").innerHTML = `<div class="err">${esc(err.message)}</div>`;
    }
    x.disabled = false;
  };
  const k = $("#btn-klayout");
  if (k) k.onclick = async () => {
    k.disabled = true; msg("launching KLayout…");
    try {
      const r = await fetch("/api/klayout", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({root: ROOT, path: rel})});
      const o = await r.json().catch(() => ({ok: false, error: r.statusText}));
      msg(o.ok ? "launched — KLayout opens in its own window"
               : "failed: " + (o.error || ""));
    } catch (err) { msg("failed: " + err.message); }
    k.disabled = false;
  };
}
// ------------------------------------------------------------- interactions
const hhmm = t => (t || "").slice(11, 19) || "";
const clock = m => { const d = new Date(m * 1000);
  return d.toLocaleString([], {month: "short", day: "numeric",
                               hour: "2-digit", minute: "2-digit"}); };
const age = s => s >= 86400 ? (s/86400).toFixed(1) + "d"
               : s >= 3600  ? (s/3600).toFixed(1) + "h" : Math.round(s/60) + "m";

$("#itabs").onclick = e => {
  const t = e.target.closest("[data-tab]");
  if (t) {
    document.querySelectorAll(".itab").forEach(x =>
      x.classList.toggle("sel", x === t));
    $("#tab-jobs").hidden = t.dataset.tab !== "jobs";
    $("#tab-procs").hidden = t.dataset.tab !== "procs";
    $("#tab-agent").hidden = t.dataset.tab !== "agent";
    return;
  }
  if (e.target.closest("#itoggle")) $("#interact").classList.toggle("collapsed");
};

// ---- live jobs (plan §6.5) -----------------------------------------------
// The ETA is the JOB'S OWN, out of its record: progress.py measures a rate
// from the work actually done and publishes `eta_s`, `frac`, `phase`. The
// browser does not re-derive any of it — a second cost model would drift from
// the first, and the flow is the only thing that knows what a phase means.
let JOBS_TIMER = null;

function jobRow(j, now) {
  const age = s => s == null ? "" : s >= 86400 ? (s/86400).toFixed(1) + "d"
                 : s >= 3600 ? (s/3600).toFixed(1) + "h"
                 : s >= 60 ? Math.round(s/60) + "m" : Math.round(s) + "s";
  const tone = {running: "b-run", done: "b-ok", failed: "b-bad",
                queued: "b-info"}[j.state] || "b-info";
  // A HEARTBEAT THAT STOPPED IS NOT A RUNNING JOB. `state` is what the record
  // last managed to write, so a job whose host died stays "running" forever;
  // the age of its heartbeat is the only thing that can contradict it, and
  // saying "stale 2.1h" beside a running badge is the honest reading.
  const beat = j.heartbeat ? now - j.heartbeat : null;
  const stale = j.state === "running" && beat != null && beat > 300;
  const bar = (j.frac != null)
    ? `<div class="jbar"><i style="width:${Math.round(100*j.frac)}%"></i></div>
       <span class="muted">${Math.round(100*j.frac)}%</span>` : "";
  const eta = j.eta_s != null ? `<span class="muted">eta ${age(j.eta_s)}</span>` : "";
  return `<tr><td><span class="badge ${tone}">${esc(j.state || "?")}</span>
      ${stale ? `<span class="badge b-warn" title="the record says running but
        nothing has written to it since">stale ${age(beat)}</span>` : ""}</td>
    <td>${esc(j.flow || "")}/${esc(j.target || "")}</td>
    <td class="muted">${esc(j.host || "")}</td>
    <td>${age(j.elapsed_s)}</td>
    <td>${bar}${eta}</td>
    <td class="muted">${esc(j.phase || "")}</td>
    <td class="muted" title="${esc(j.jobid || "")}">${esc((j.jobid||"").slice(-4))}</td></tr>`;
}

async function loadJobs(refresh) {
  const msg = t => { const e = $("#jmsg"); if (e) e.textContent = t || ""; };
  msg("reading the cluster…");
  let d;
  try { d = await j("/api/jobs" + (refresh ? "?refresh=1" : "")); }
  catch (err) { msg(""); $("#jobs").innerHTML =
    `<div class="err">${esc(err.message)}</div>`; return; }
  const now = d.now || Math.floor(Date.now()/1000);
  const jobs = (d.jobs || []).slice();
  // RUNNING FIRST, then most recent. The panel exists to answer "what is
  // happening", and a finished job from last week outranking a live one by
  // start time is the sort order answering a different question.
  const rank = s => s === "running" ? 0 : s === "queued" ? 1 : 2;
  jobs.sort((a, b) => rank(a.state) - rank(b.state) ||
                      (b.started || 0) - (a.started || 0));
  const live = jobs.filter(x => x.state === "running").length;
  $("#jobs").innerHTML = jobs.length
    ? `<table><tr><th>state</th><th>flow/target</th><th>host</th><th>elapsed</th>
         <th>progress</th><th>phase</th><th>id</th></tr>` +
      jobs.slice(0, 40).map(x => jobRow(x, now)).join("") + `</table>` +
      (jobs.length > 40 ? `<p class="muted">${jobs.length - 40} older job(s)
        not shown</p>` : "")
    : `<p class="muted">no job records on ${esc(d.fs)}</p>`;
  // The host is PROVENANCE -- which box served the read -- not scope. Every
  // host on the filesystem returns the same records, so naming it answers
  // "who answered and how fast", never "what am I not seeing".
  msg(`${live} running of ${jobs.length} on ${d.fs}` +
      (d.cached ? " · cached"
                : ` · read from ${d.host} in ${d.seconds}s`));
  // THE WATCH INTERVAL COMES FROM WHAT THE READ COST, exactly as the waveform
  // watch does. A `list` is ~10 s against the cluster; polling that every few
  // seconds would leave a permanent job on the head node to produce a table
  // nobody is looking at between blinks.
  if (JOBS_TIMER) { clearInterval(JOBS_TIMER); JOBS_TIMER = null; }
  const box = $("#jwatch");
  if (box && box.checked) {
    const every = Math.min(300000, Math.max(30000,
      Math.round((d.seconds || 10) * 4) * 1000));
    msg($("#jmsg").textContent + ` · every ${every/1000}s`);
    JOBS_TIMER = setInterval(() => {
      if (!$("#jwatch") || !$("#jwatch").checked || $("#tab-jobs").hidden) {
        clearInterval(JOBS_TIMER); JOBS_TIMER = null; return;
      }
      loadJobs(true);
    }, every);
  }
}

$("#btn-jobs").onclick = () => loadJobs(true);
$("#jwatch").onchange = () => {
  if ($("#jwatch").checked) loadJobs(false);
  else if (JOBS_TIMER) { clearInterval(JOBS_TIMER); JOBS_TIMER = null; }
};

$("#btn-scan").onclick = async () => {
  const b = $("#btn-scan");
  b.disabled = true; $("#imsg").textContent = "scanning…";
  try {
    const d = await j("/api/procs");
    renderProcs(d.hosts || {});
    $("#imsg").textContent = "";
  } catch (err) {
    $("#imsg").textContent = "";
    $("#procs").innerHTML = `<div class="err">${esc(err.message)}</div>`;
  }
  b.disabled = false;
};

function renderProcs(hosts) {
  let html = "", n = 0;
  for (const [host, r] of Object.entries(hosts)) {
    if (r.error) {
      // NOT "no processes": an unreachable host reported as clean is the
      // exact failure this whole thing exists to prevent.
      html += `<div class="warn"><b>${esc(host)}</b> — could not scan:
               ${esc(r.error)}</div>`;
      continue;
    }
    const groups = (r.sessions || []).filter(g => g.root.suspect || g.safe_to_kill);
    if (!groups.length) { html += `<div class="pev">${esc(host)} — clean</div>`;
                          continue; }
    for (const g of groups) {
      n++;
      const root = g.root;
      const tone = g.safe_to_kill ? "b-bad" : "b-warn";
      const label = g.safe_to_kill ? "abandoned" : "suspect";
      html += `<div class="psess">
        <h4><span class="badge ${tone}">${label}</span>
            <b>${esc(root.name)}</b>
            <span class="muted">${esc(host)} · pid ${root.pid} ·
              ${age(g.age_s)} · ${g.n} proc · ${g.rss_mb} MB ·
              ${esc(root.vendor || "?")}</span>
            ${root.protected.length ? "" :
              `<button data-kill="${esc(host)}" data-pid="${root.pid}"
                 data-expect="${esc(root.name)}">Kill…</button>`}</h4>` +
        root.evidence.map(e =>
          `<div class="pev">• ${esc(e.kind)}: ${esc(e.why)}</div>`).join("") +
        root.protected.map(p =>
          `<div class="pev">✓ protected: ${esc(p)}</div>`).join("") +
        `<div class="pargs">${esc(root.args.slice(0, 160))}</div></div>`;
    }
  }
  $("#procs").innerHTML = html ||
    `<div class="pev">nothing stale found</div>`;
  $("#imsg").textContent = n ? `${n} session(s) worth a look` : "";
}

// ---- agent activity (plan section 7) --------------------------------------
let AGENT_ROOTS = [];

async function loadAgent(session) {
  const b = $("#btn-agent");
  b.disabled = true;
  try {
    const d = await j("/api/agent" + (session ? "?session=" +
                      encodeURIComponent(session) : ""));
    if (d.note) { $("#agent-now").innerHTML =
      `<div class="pev">${esc(d.note)}</div>`; b.disabled = false; return; }
    AGENT_ROOTS = d.roots || [];
    // The picker must NAME the session. Two agents in one repo is not
    // hypothetical -- measured here, two overlapped by an hour and a half --
    // and a list of hex ids gives no way to tell which one you are reading,
    // nor that a second one is running at all.
    const sel = $("#agent-session");
    sel.innerHTML = (d.sessions || []).map(s =>
      `<option value="${esc(s.id)}">${s.live ? "● " : "○ "}` +
      `${esc(s.title || s.id.slice(0, 8))} · ${esc(clock(s.mtime))}` +
      `</option>`).join("");
    sel.value = d.session;
    const others = (d.sessions || []).filter(s => s.live && s.id !== d.session);
    $("#agent-other").innerHTML = others.length
      ? `<div class="warn" style="margin:.2rem 0">also live in this repo:
         ${others.map(s => `<a data-sess="${esc(s.id)}"
           style="cursor:pointer;text-decoration:underline">${esc(s.title
           || s.id.slice(0,8))}</a>`).join(", ")}</div>`
      : "";

    // NOW -- the single line that matters: what is it doing, or what is it
    // blocked on. During a 25-minute LVS this is the difference between
    // "stuck" and "Calibre is just slow".
    const n = d.now, tone = {running: "b-run", "waiting-on-you": "b-warn",
                             idle: "b-info", working: "b-ok"}[n.state] || "b-info";
    $("#agent-now").innerHTML =
      `<div class="psess"><h4><span class="badge ${tone}">${esc(n.state)}</span>
        ${esc(n.detail || "")}
        <span class="muted">${n.since_s != null ? age(n.since_s) : ""}
        · ${d.n_events} events in the tail
        ${d.fleet.n ? "· " + d.fleet.n + " subagent calls (" + d.fleet.open
                      + " open)" : ""}</span></h4></div>`;

    $("#agent-warn").innerHTML = (d.thrash || []).map(t =>
      `<div class="warn" style="margin:.2rem 0">${esc(t.why)}</div>`).join("");

    // GROUPED BY TURN, newest first. A flat list of "Bash 3s / Edit 0s" is a
    // stream with no reason attached to any of it; the turn says these
    // actions all served one request, how long it took, what it touched and
    // whether anything failed.
    $("#agent-time").innerHTML = (d.turns || []).slice().reverse().map(t =>
      `<div class="psess"><h4>
         <span class="badge ${t.errors ? "b-bad" : "b-info"}">${hhmm(t.started)}</span>
         ${t.partial ? "<span class='muted'>(continues from earlier)</span>" : ""}
         <span class="muted">${t.n} actions · ${age(t.dur_s)}
         ${t.errors ? "· " + t.errors + " failed" : ""}
         ${t.files.length ? "· " + esc(t.files.join(", ")) : ""}</span></h4>` +
      t.actions.slice().reverse().map(e => {
        const lab = e.path || e.summary || "";
        const link = e.artifact ? ` data-open="${esc(e.path)}"` : "";
        return `<div class="pev"${link} ${e.artifact ?
          'style="cursor:pointer;text-decoration:underline"' : ""}>` +
          `<span class="muted">${hhmm(e.t)}</span> ` +
          `${e.error ? "✗ " : e.done ? "" : "▶ "}` +
          `<b>${esc(e.tool || "")}</b> ${esc(lab.slice(0, 90))}` +
          `${e.elapsed_s != null ? " · " + e.elapsed_s + "s" : ""}` +
          `${e.sidechain ? " · subagent" : ""}</div>`;
      }).join("") + `</div>`).join("");
  } catch (err) {
    $("#agent-now").innerHTML = `<div class="err">${esc(err.message)}</div>`;
  }
  b.disabled = false;
}

$("#btn-agent").onclick = () => loadAgent($("#agent-session").value);
$("#agent-session").onchange = e => loadAgent(e.target.value);
$("#agent-other").onclick = e => {
  const a = e.target.closest("[data-sess]");
  if (a) loadAgent(a.dataset.sess);
};

// Artifact link-out: "the agent just produced this -- look at it, now". This
// is what makes the browser and the activity view one product rather than two.
$("#agent-time").onclick = async e => {
  const el = e.target.closest("[data-open]");
  if (!el) return;
  const p = el.dataset.open.replace(/\\\\/g, "/");
  for (const r of AGENT_ROOTS) {
    const rp = r.path.replace(/\\\\/g, "/");
    if (!p.toLowerCase().startsWith(rp.toLowerCase() + "/")) continue;
    const rel = p.slice(rp.length + 1);
    // An artifact is a FILE. Listing it 400s -- open its DIRECTORY, then show
    // the file itself, which is what "look at it, now" actually means.
    const cut = rel.lastIndexOf("/");
    await open(r.name, cut < 0 ? "" : rel.slice(0, cut));
    view(rel);
    return;
  }
  $("#imsg").textContent = "not under a configured root";
};

$("#procs").onclick = async e => {
  const b = e.target.closest("[data-kill]");
  if (!b) return;
  // Two clicks, and the second one names what it is about to do. This is the
  // only irreversible thing in the whole browser.
  if (!confirm(`Terminate ${b.dataset.expect} (pid ${b.dataset.pid}) on `
             + `${b.dataset.kill}?\n\nSIGTERM is sent first and the command `
             + `line is re-checked on the host before anything is signalled. `
             + `Any unsaved work in that tool is lost.`)) return;
  b.disabled = true; b.textContent = "…";
  try {
    const r = await fetch("/api/procs/kill", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({host: b.dataset.kill, pid: +b.dataset.pid,
                            expect: b.dataset.expect})});
    const o = r.ok ? await r.json() : {result: await r.text()};
    b.textContent = o.result + (o.note ? " — " + o.note : "");
  } catch (err) { b.textContent = "failed: " + err.message; }
};

// A panel that populates only when you click it looks broken, because an
// empty box and a broken box are the same picture. The agent view reads a
// local file tail and costs ~11 kB, so it loads with the page and the tab is
// never empty when revealed. The process scan stays on its button: that one
// is eleven ssh round trips.
loadAgent();

// STALENESS ANNOUNCES ITSELF. `no-store` stops NEW staleness, but a tab
// cached before that landed keeps serving itself forever, and the symptom is
// a feature that "does nothing" -- which is exactly how this was reported.
// The page knows which build it is; ask the server which build it serves.
const UI_BAKED = "__UI__";
async function checkStale() {
  try {
    const h = await (await fetch("/api/health", {cache: "no-store"})).json();
    if (h.ui && h.ui !== UI_BAKED) {
      $("#hint").innerHTML =
        `<b style="color:#b8860b">this page is stale (ui ${esc(UI_BAKED)},
         server has ${esc(h.ui)}) —
         <a href="" onclick="location.reload(true);return false">reload</a></b>`;
    }
  } catch (err) { /* server gone; the next click will say so */ }
}
checkStale();
setInterval(checkStale, 30000);

$("#filter").oninput = paint;
loadRoots();
</script></body></html>
"""


#: Short content hash of the page itself, shown in the header and reported by
#: /api/health. This exists because "am I looking at the new version?" was not
#: answerable: a stale tab is pixel-identical to a fresh one, and the only way
#: to tell was to notice a feature missing. Now the header says which build is
#: on screen, and it changes whenever the UI does.
#: Hashed over the page WITH its placeholder still in it, so the value does
#: not depend on itself.
UI_VERSION = hashlib.sha256(PAGE.encode("utf-8")).hexdigest()[:7]


class Handler(BaseHTTPRequestHandler):
    roots = []
    settings = {}

    # ---- plumbing --------------------------------------------------------
    def log_message(self, fmt, *args):
        pass                                    # one line per fetch is noise

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # NO-STORE, and this is not belt-and-braces. The page and every API
        # answer describe live state -- a directory listing, a verdict, the UI
        # itself. Served without cache headers a browser may heuristically
        # cache them, so a reload can hand back yesterday's page and the user
        # has no way to tell: a stale tab is pixel-identical to a fresh one.
        # Content-addressed bytes under /render/ are exempt; they are named by
        # their own hash, so a cached copy is by definition the right one.
        if not self.path.startswith("/render/"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
        # This server renders files the user points it at. A restrictive CSP
        # keeps a stray <script> in some tool's HTML output from running with
        # access to the browser's own origin.
        self.send_header("Content-Security-Policy",
                         "default-src 'self' data:; style-src 'unsafe-inline' 'self'; "
                         "script-src 'unsafe-inline' 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass                                # user navigated away mid-send

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _err(self, code, msg):
        self._send(code, msg, "text/plain; charset=utf-8")

    def _resolve(self, q):
        return rootsmod.resolve(self.roots, (q.get("root") or [""])[0],
                                (q.get("path") or [""])[0])

    # ---- routes ----------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE.replace("__UI__", UI_VERSION),
                                  "text/html; charset=utf-8")
            if u.path == "/api/health":
                return self._json({"app": "artifact-browser",
                                   "ui": UI_VERSION, "pid": os.getpid(),
                                   "repo": rootsmod.REPO})
            if u.path == "/api/roots":
                return self._json({
                    "roots": [r.as_dict() for r in self.roots],
                    # what this repo DECLARED, so the pane can say which
                    # renderer a click will run and which files it badges
                    "renderer": tools.RENDERER_SPEC.as_dict(),
                    "badge_sources": [p for p, _r in model.BADGE_SOURCES],
                    "design": bool(model.design_signature(rootsmod.REPO))})
            if u.path == "/api/list":
                return self._api_list(q)
            if u.path == "/api/file":
                return self._api_file(q)
            if u.path == "/api/designcheck":
                return self._api_designcheck(q)
            if u.path == "/api/render":
                return self._api_render(q)
            if u.path == "/api/xor":
                return self._api_xor(q)
            if u.path == "/api/mc":
                return self._api_mc(q)
            if u.path == "/api/jobs":
                return self._api_jobs(q)
            if u.path == "/api/procs":
                return self._api_procs(q)
            if u.path == "/api/agent":
                return self._api_agent(q)
            if u.path.startswith("/render/"):
                return self._render_bytes(u.path[len("/render/"):])
            if u.path.startswith("/f/"):
                return self._file_bytes(u.path[3:])
            if u.path == "/raw":
                # legacy query form, kept so an old bookmark still resolves.
                # New links use /f/ -- see _file_bytes.
                _root, abspath = self._resolve(q)
                return self._bytes(abspath)
            return self._err(404, "no such route")
        except ValueError as exc:
            # every confinement failure lands here -- 403, not 500: it is a
            # refusal, not a crash, and the distinction matters in a log
            return self._err(403, str(exc))
        except OSError as exc:
            return self._err(404, str(exc))

    # The browser still WRITES NOTHING. The one POST below execs the KLayout
    # launcher, which is the single action plan section 5.4 permits; PUT and
    # DELETE remain unimplemented, and no route modifies a browsed tree.
    def do_POST(self):
        u = urlparse(self.path)
        if u.path not in ("/api/klayout", "/api/roots", "/api/procs/kill"):
            self._drain()               # same reason as the refusal below
            return self._err(404, "no such route")
        try:
            self._guard_same_origin()
            if u.path == "/api/klayout":
                return self._api_klayout()
            if u.path == "/api/procs/kill":
                return self._api_kill()
            return self._api_add_root()
        except ValueError as exc:
            # DRAIN FIRST. Refusing without reading the body leaves the client
            # still sending into a socket we then close, and the client sees
            # an aborted connection instead of the 403 -- intermittently,
            # depending on whether its send had completed. It showed up as a
            # flaky test (WinError 10053) rather than as a bug, which is how
            # it survived this long.
            self._drain()
            return self._err(403, str(exc))

    def _drain(self, limit=1 << 20):
        """Consume the request body we are about to refuse to act on."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        while n > 0:
            chunk = self.rfile.read(min(n, 65536))
            if not chunk:
                break
            n -= len(chunk)
            limit -= len(chunk)
            if limit <= 0:
                break

    def _body(self, limit=8192):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(min(n, limit)).decode("utf-8"))
        except (ValueError, OSError):
            raise ValueError("bad request body")

    def _api_add_root(self):
        """Add a browsable root, persisted to roots.local.json.

        Behind the same same-origin guard as the KLayout exec, and for a
        related reason: this one widens what the server will serve, so a page
        on another origin must not be able to point it at a new tree and then
        read it.
        """
        body = self._body()
        try:
            self.__class__.roots = rootsmod.add(body)
        except ValueError as exc:
            return self._err(400, str(exc))
        except OSError as exc:
            return self._err(500, "could not write config: %s" % exc)
        return self._json({"ok": True,
                           "roots": [r.as_dict() for r in self.roots],
                           "added": body.get("name")})

    def _guard_same_origin(self):
        """Refuse a cross-site POST.

        GET routes only read, so they need no guard. This one launches a
        program, and a page on some other origin CAN send a simple POST to
        127.0.0.1 without a preflight -- it cannot read the reply, but the
        side effect would already have happened. Two cheap, independent
        checks: the browser's own Sec-Fetch-Site, and the Origin.

        The page sends application/json, which is not a simple content type,
        so a genuine cross-site attempt would have to pass a CORS preflight
        this server never answers.
        """
        site = self.headers.get("Sec-Fetch-Site")
        if site and site != "same-origin":
            raise ValueError("cross-site request refused")
        origin = self.headers.get("Origin")
        if origin and origin != "http://%s" % self.headers.get("Host", ""):
            raise ValueError("cross-origin request refused")
        if self.headers.get("Content-Type", "").split(";")[0].strip() \
                != "application/json":
            raise ValueError("expected application/json")

    def _root_of(self, q):
        r = rootsmod.by_name(self.roots, (q.get("root") or [""])[0])
        if r is None:
            raise ValueError("unknown root: %r" % (q.get("root") or [""])[0])
        return r

    def _api_list(self, q):
        if self._root_of(q).kind == "remote":
            return self._api_list_remote(q)
        _root, abspath = self._resolve(q)
        if not os.path.isdir(abspath):
            return self._err(400, "not a directory")
        rel = (q.get("path") or [""])[0].strip("/")
        entries = model.listdir(abspath, rel)
        model.annotate(entries, abspath)
        # A directory named for a design library, or for the scratch OA
        # library, is told apart from any other directory -- the question a
        # listing of `analog/oa/` exists to answer.
        model.annotate_libraries(entries, rootsmod.REPO)
        out = {"entries": entries,
               # HOW MANY ROWS ACTUALLY GOT BADGED. Badging is capped, and
               # the cap applies in the order the SERVER produced -- newest
               # first. That was invisible while the pane showed the same
               # order; once the pane can re-sort, an unbadged row further
               # down means "past the cap", not "no verdict", and only this
               # number lets the pane tell the difference.
               "badged": min(len(entries), model.BADGE_MAX_ENTRIES),
               # WHICH SOURCES WERE READ. Empty is a statement -- "nothing
               # here is a badge source" -- and the pane prints it, because
               # an unbadged listing is otherwise indistinguishable from a
               # listing of things that all passed.
               "badge_sources": model.badge_sources_present(entries),
               "order": "mtime-desc",
               **self._where(_root, abspath)}
        # THE DESIGN RECORD'S ROOT DIRECTORY gets the manifest check as a
        # badge (tools.design_check) -- fetched by the pane on a separate
        # request so the listing itself stays instant.
        if (os.path.realpath(abspath)
                == os.path.realpath(os.path.join(rootsmod.REPO, model.DESIGN_DIR))):
            out["design_tree"] = True
        # A MONTE CARLO RUN IS A DIRECTORY, not a file -- N sibling iteration
        # dirs, each holding ordinary results. It is offered where it lives,
        # in the listing of the run, because there is no single file to click.
        if mcmod is not None:
            iters = mcmod.iterations(abspath)
            if iters:
                out["mc"] = {"n": len(iters),
                             "first": iters[0][0], "last": iters[-1][0],
                             "artifacts": mcmod.artifacts(iters)}
        return self._json(out)

    def _where(self, root, abspath=None):
        """Where the listing actually IS, in terms the user can act on.

        A root is configured under a NICKNAME -- "flowruns", "ms_pilot" -- and
        the breadcrumb showed only that plus the relative parts. Which left the
        question the panel most needs to answer unanswered: `flowruns` on which
        disk? `ms_pilot` on which machine? The real path was reachable only by
        hovering for a tooltip.
        """
        return {"root_name": root.name,
                "root_path": root.path,
                "root_kind": root.kind,
                "root_host": root.host,
                "abspath": abspath}

    def _api_list_remote(self, q):
        """A cluster directory, badged from sources the lister inlined."""
        root, rel = rootsmod.resolve_remote(
            self.roots, (q.get("root") or [""])[0], (q.get("path") or [""])[0])
        refresh = (q.get("refresh") or ["0"])[0] not in ("0", "", "false")
        try:
            entries, badge_src, cached = cluster.listdir(
                root.host, root.path, rel, refresh=refresh, fs=root.fs,
                badge_names=model.exact_badge_sources())
        except cluster.RemoteError as exc:
            # 503, not 404: the cluster failing to answer is NOT the same as
            # the directory being absent, and the pane must not say it is.
            return self._err(503, "cluster: %s" % exc)
        out = []
        for e in entries:
            src = badge_src.get(e["name"]) or {}
            out.append({
                "name": e["name"],
                "rel": (rel + "/" + e["name"]).lstrip("/"),
                "kind": "dir" if e["is_dir"] else model.kind_of_name(e["name"]),
                "size": e["size"], "mtime": e["mtime"], "is_dir": e["is_dir"],
                # the SAME badge definition the local reader uses
                "badges": model.badges_from_text(
                    src.get("source", ""), src.get("text", ""),
                    latest=bool(src.get("latest"))) if src else []})
        # The cluster's `analog/oa/` holds the published libraries beside the
        # scratch one, and the served repo's manifests are what say which is
        # which -- so the name badges come from the LOCAL record, applied to
        # the REMOTE listing.
        model.annotate_libraries(out, rootsmod.REPO)
        # The cluster path is composed rather than round-tripped: the root's
        # own path plus the relative part is exactly what the user typed or
        # configured, and it stays readable ("~/Documents/ms_pilot/...")
        # instead of expanding to a home directory they never wrote.
        return self._json({"entries": out, "cached": cached,
                           "host": root.host,
                           "badged": min(len(out), cluster.BADGE_MAX_ENTRIES),
                           "badge_sources": model.badge_sources_present(out),
                           "order": "mtime-desc",
                           **self._where(root, (root.path.rstrip("/") + "/" + rel)
                                         if rel else root.path)})

    def _api_file(self, q):
        if self._root_of(q).kind == "remote":
            return self._api_file_remote(q)
        _root, abspath = self._resolve(q)
        if os.path.isdir(abspath):
            return self._err(400, "is a directory")
        kind = model.kind_of(abspath)
        size = os.path.getsize(abspath)
        out = {"kind": kind, "size": size, "truncated": False,
               "shown": size, "text": "", "parsed": None, "rows": []}
        # WHICH CAPTURED VIEW IS THIS FILE. Asked of every local file, by
        # md5 against the served repo's manifests, so a `.gds` under
        # analog/work can say "I am ancasic_p2/ancBrain_Buffer view stream,
        # bound" -- or "in no view", which is the answer that used to take a
        # shell loop. Skipped past the md5 limit, and the pane says so.
        try:
            out["design_ref"] = model.which_view(rootsmod.REPO, abspath)
        except (OSError, ValueError) as exc:          # never fail the view
            out["design_ref"] = {"skipped": str(exc)}
        if kind == "svg":
            # A GENERATED SVG THAT TAGS ITS NETS IS NOT JUST A PICTURE. The
            # schematic renderer and the layout track map both put `data-net`
            # on every shape they draw, which is the entire join §6.4 asks
            # for -- so this one is inlined rather than dropped into an <img>,
            # and the pane can highlight a net in it. A plain SVG (an atlas
            # crop, a report figure) has no nets and still goes to /raw.
            text, trunc, _s = model.read_text(abspath)
            nets = model.svg_nets(text)
            if nets:
                out.update(svg=text, nets=nets, truncated=trunc)
                # ACROSS EVERY LOCAL ROOT, not just this one. The schematics
                # are published to `analog/results/schematic` and the layout
                # abstracts live under `analog/work/layout_cc` -- different
                # roots by design, so a same-root search finds nothing and the
                # feature looks broken on exactly the artifacts it was built
                # for. Remote roots are skipped: pairing would be one ssh read
                # per candidate.
                out["crossprobe"] = _crossprobe_all(self.roots, nets)
                # An op point published beside the schematic, by the step that
                # back-annotated it. Read only if it is actually there.
                opp = os.path.splitext(abspath)[0] + ".json"
                if os.path.exists(opp):
                    parsed, _t, ot, _os = model.read_json(opp)
                    if isinstance(parsed, dict) and not ot:
                        out["op"] = {"nodes": parsed.get("nodes") or {},
                                     "devices": parsed.get("devices") or {}}
        elif kind in ("image", "html"):
            pass                                # the viewer fetches /raw
        elif kind == "json":
            parsed, text, trunc, _s = model.read_json(abspath)
            out.update(parsed=parsed, text=text, truncated=trunc)
            # The special-cased view §5.2 promised: the engine's own model,
            # drawn. Best-effort -- a viewer must never be the thing that
            # fails on a half-written file.
            if (abstract_svg is not None and isinstance(parsed, dict)
                    and abspath.lower().endswith(".abstract.json")):
                try:
                    out["abstract"] = {
                        "svg": abstract_svg.render(parsed),
                        "facts": abstract_svg.facts(parsed)}
                except Exception as exc:               # noqa: BLE001
                    out["abstract_error"] = str(exc)
            # THE DESIGN RECORD'S OWN FILE gets its own view: the views
            # table with the bound one marked, the BOM as links to the
            # sibling cells, and the one finding worth a badge -- a newer
            # generation nobody adopted. Recognised by SHAPE (a `views`
            # dict), not by name, so a copy filed under another name still
            # reads as what it is.
            if (isinstance(parsed, dict) and isinstance(parsed.get("views"), dict)
                    and os.path.basename(abspath).lower() == "cell.json"):
                facts = model.cell_facts(parsed)
                facts["newer"] = [list(t) for t in facts["newer"]]
                out["design"] = facts
            elif (isinstance(parsed, dict)
                    and os.path.basename(abspath).lower() == "lib.json"
                    and isinstance(parsed.get("library"), str)):
                idx = model.design_index(rootsmod.REPO)
                lib = idx["libraries"].get(parsed["library"]) or {}
                out["design_lib"] = {"library": parsed["library"],
                                     "project": parsed.get("project"),
                                     "note": parsed.get("note"),
                                     "cells": sorted(lib.get("cells") or [])}
        elif kind == "jsonl":
            rows, trunc, _s = model.read_jsonl(abspath)
            out.update(rows=rows, truncated=trunc)
        elif kind == "wave":
            if wavemod is None:
                out.update(text="no waveform reader on this installation "
                                "(analog/engine/wave.py not found under %s)"
                                % rootsmod.REPO)
            else:
                # WHAT IT WILL COST, before it costs it. The read is head-
                # capped, so the honest denominator is min(size, budget) and
                # not the file size -- a 500 MB PSF file costs what 64 MB
                # costs. `go` is the user having been told and said yes.
                out["cost"] = estimate.cost("wave", "local", size,
                                            will_read=wavemod.BUDGET)
                if out["cost"]["confirm"] and not _yes(q, "go"):
                    out["needs_confirm"] = True
                    return self._json(out)
                try:
                    w, secs = _wave_cached(abspath)
                    if secs is not None:
                        estimate.record("wave", "local",
                                        min(size, wavemod.BUDGET), secs)
                    out.update(_wave_payload(wavemod, w, q))
                    if out["wave"].get("is_index"):
                        out["wave"]["siblings"] = model.index_siblings(
                            abspath, (q.get("path") or [""])[0])
                    if out["wave"].get("is_index"):
                        out["wave"]["siblings"] = model.index_siblings(
                            abspath, (q.get("path") or [""])[0])
                except Exception as exc:               # noqa: BLE001
                    out.update(text="waveform: %s" % exc)
        elif kind == "binary":
            # NOT a hex dump. Printing 4000 characters of hex answered no
            # question anyone opens a layout to ask, and filled the pane with
            # noise. A GDS gets a summary of what it actually contains; every
            # other binary gets an honest "no preview" card.
            if abspath.lower().endswith(".gds"):
                out["gds"] = model.gds_summary(abspath)
                # What KLayout would actually load, reported before the click:
                # no .lyp means an unnamed rainbow, no .lyrdb means an empty
                # Marker Browser.
                sc = tools.sidecars(abspath)
                out["sidecars"] = {"lyp": bool(sc["lyp"]),
                                   "lyrdb": bool(sc["lyrdb"])}
                out["klayout"] = tools.klayout_available()
                # What the Render button is about to cost. This is the one
                # click here that genuinely takes a minute -- 44.6 s/MB
                # measured -- so the number goes ON the button rather than
                # into a warning nobody reads.
                out["cost"] = estimate.cost("render", "local", size)
                out["xor"] = tools.xor_available()
                out["xor_candidates"] = model.xor_candidates(
                    abspath, (q.get("path") or [""])[0])
        else:
            text, trunc, _s = model.read_text(abspath)
            out.update(text=text, truncated=trunc)
        if out["truncated"]:
            out["shown"] = min(size, model.BYTE_BUDGET)
        return self._json(out)

    def _api_file_remote(self, q):
        """One size-capped cluster read, dispatched to the same viewers.

        The kind comes from the NAME, because deciding it would otherwise cost
        a second round trip -- and the local reader's own dispatch is by
        extension anyway.
        """
        root, rel = rootsmod.resolve_remote(
            self.roots, (q.get("root") or [""])[0], (q.get("path") or [""])[0])
        kind = model.kind_of_name(rel)
        out = {"kind": kind, "size": 0, "truncated": False, "shown": 0,
               "text": "", "parsed": None, "rows": [], "remote": True,
               "host": root.host}
        if kind in ("image", "svg", "html"):
            # fetched as bytes by /f/, exactly like a local one
            return self._json(out)
        if kind == "wave":
            return self._api_wave_remote(q, root, rel, out)
        try:
            raw, trunc, size = cluster.fetch(root.host, root.path, rel)
        except cluster.RemoteError as exc:
            return self._err(503, "cluster: %s" % exc)
        out.update(size=size, truncated=trunc, shown=len(raw))
        if kind == "binary":
            if rel.lower().endswith(".gds"):
                # No summary: a GDS record walk needs the WHOLE stream, and
                # pulling a layout to count its cells is precisely what the
                # remote render exists to avoid.
                out["gds"] = None
                out["remote_gds"] = True
                out["cost"] = estimate.cost("render", "remote", size)
            return self._json(out)
        text = raw.decode("utf-8", "replace")
        if kind == "json":
            out["text"] = text
            if not trunc:
                try:
                    out["parsed"] = json.loads(text)
                except ValueError:
                    pass
        elif kind == "jsonl":
            rows = []
            for ln in text.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    rows.append({"_raw": ln})
            out["rows"] = rows
        else:
            out["text"] = text
        return self._json(out)

    def _api_wave_remote(self, q, root, rel, out):
        """A transient on the cluster: parsed there, only the picture returns.

        Plan 10.8, and the case the partial view was built for -- the
        hour-long run is on asic7, not here. Three things make it different
        from every other remote read in this file:

        - it reads the HEAD of a growing file, not the tail;
        - the reader is SHIPPED (`cluster.wave`), so the remote picture is
          drawn by the same bytes as the local one;
        - the file is STATTED FIRST and the read can be declined on the same
          round trip, because a 60 MB transient is a six-second click and
          six seconds of silence reads as a hang.
        """
        if wavemod is None:
            out["text"] = ("no waveform reader on this installation "
                           "(analog/engine/wave.py not found under %s)"
                           % rootsmod.REPO)
            return self._json(out)
        # The gate is a NUMBER computed here and enforced there. Asking the
        # cluster for a size and then asking again for the data would be two
        # round trips to answer a question that is usually "yes".
        gate = 0 if _yes(q, "go") else (
            estimate.confirm_bytes("wave", "remote") or 0)
        view = _wave_view(q)
        t0 = time.time()
        try:
            got = cluster.wave(root.host, root.path, rel, WAVE_SRC, view=view,
                               budget=wavemod.BUDGET, gate_bytes=gate)
        except cluster.RemoteError as exc:
            return self._err(503, "cluster: %s" % exc)
        elapsed = time.time() - t0
        size = int(got.get("size") or 0)
        reads = int(got.get("reads") or 0) or min(size, wavemod.BUDGET)
        out.update(size=size, shown=reads,
                   cost=estimate.cost("wave", "remote", size,
                                      will_read=wavemod.BUDGET))
        if got.get("kind") == "gated":
            # Nothing was read. The round trip that said so cost ~0.4 s, and
            # it is NOT a sample of the parse -- recording it would drag the
            # rate towards zero and stop the gate ever firing again.
            out["needs_confirm"] = True
            return self._json(out)
        estimate.record("wave", "remote", reads, elapsed)
        w = got.get("summary") or {}
        w["mode"] = view["mode"]
        w["xlog"] = got.get("xlog")
        w["ylog"] = got.get("ylog")
        w["drawn"] = got.get("drawn") or []
        w["dropped"] = int(got.get("dropped") or 0)
        w["remote_parse_s"] = got.get("t_parse")
        w["remote_plot_s"] = got.get("t_plot")
        w["elapsed_s"] = round(elapsed, 2)
        out["wave"] = w
        out["svg"] = got.get("svg") or ""
        out["truncated"] = bool(w.get("truncated"))
        # The cost is now a measurement rather than a prediction; say the
        # thing that actually happened.
        out["cost"]["measured"] = round(elapsed, 2)
        return self._json(out)

    def _api_agent(self, q):
        """Agent activity, METADATA ONLY (plan 7.2).

        Everything that could carry deck text, file contents or tool output is
        dropped inside agentview by an allowlist, before it ever reaches this
        route -- so there is nothing here to filter, and nothing a change here
        could accidentally expose.
        """
        ss = agentview.sessions(rootsmod.REPO)
        if not ss:
            return self._json({"sessions": [], "note":
                               "no transcripts for %s"
                               % agentview.project_dir(rootsmod.REPO)})
        want = (q.get("session") or [""])[0]
        chosen = next((s for s in ss if s["id"] == want), ss[0])
        out = agentview.summarize(chosen["path"])
        # title and live MUST survive the projection: they are the whole
        # reason the picker is usable, and the only signal that a second agent
        # is running in this repo right now.
        out["sessions"] = [{"id": s["id"], "size": s["size"],
                            "mtime": s["mtime"], "title": s["title"],
                            "live": s["live"]} for s in ss]
        out["session"] = chosen["id"]
        # so the UI can turn an absolute path from the timeline into a click
        out["roots"] = [{"name": r.name, "path": r.path}
                        for r in self.roots if r.kind == "local"]
        return self._json(out)

    def _api_mc(self, q):
        """Aggregate one artifact across a Monte Carlo run's iterations.

        Two answers, because two questions were asked and neither substitutes
        for the other: a POINT file gives one number per iteration and becomes
        a distribution (histogram, mean, sigma, min/max); a SWEEP file gives
        one curve per iteration and becomes a band. The individual iterations
        are drawn under the band and listed beside the histogram either way --
        an aggregate is exactly what hides the outlier, and the outlier is
        usually why someone opened a Monte Carlo run.
        """
        if mcmod is None:
            return self._err(503, "no MC aggregator on this installation")
        root = self._root_of(q)
        if root.kind == "remote":
            # Reading N iteration directories over ssh is N round trips.
            # Honest refusal beats a view that takes a minute and looks broken.
            return self._err(400, "MC aggregation is local-only for now -- "
                                  "it reads every iteration, and doing that "
                                  "remotely is one round trip per directory")
        _root, abspath = self._resolve(q)
        art = (q.get("artifact") or [""])[0]
        if not art or ".." in art.replace("\\", "/").split("/"):
            return self._err(400, "an artifact name is required")
        trace = (q.get("trace") or [""])[0]
        part = (q.get("part") or ["db"])[0]
        t0 = time.time()
        got = mcmod.collect(abspath, art,
                            budget=wavemod.BUDGET if wavemod else None)
        elapsed = time.time() - t0
        out = {"kind": got["kind"], "artifact": art,
               "seconds": round(elapsed, 2),
               "n_iters": got.get("n_iters"), "n_total": got.get("n_total"),
               "failed": got.get("failed") or []}
        if got["kind"] == "scalar":
            out["rows"] = [{k: v for k, v in r.items() if k != "hist"}
                           for r in got["rows"]]
            out["svg"] = mcmod.render_hist(got["rows"])
        elif got["kind"] == "vector":
            c0 = got["curves"][0]
            names = [n for n in c0["order"]
                     if any(c["y"].get(n) for c in got["curves"])]
            pick = trace if trace in names else (names[0] if names else None)
            out["traces"] = names
            out["trace"] = pick
            out["sweep"] = c0["sweep"]
            b = mcmod.band(got["curves"], pick) if pick else None
            out["band"] = {k: v for k, v in (b or {}).items()
                           if k in ("n", "dropped", "trace")}
            out["svg"] = mcmod.render_band(b, got["curves"], pick,
                                           xlog=c0["xlog"],
                                           units=c0["units"]) if b else ""
        else:
            out["reason"] = got.get("reason")
        estimate.record("mc", "local", max(1, got.get("n_iters") or 1), elapsed)
        return self._json(out)

    def _api_jobs(self, q):
        """Live job state (plan §6.5) -- `remote.list()`, cached and measured.

        The panel is the last of Phase 5's read-only views and the cheapest to
        get wrong in one specific way: `list` is **9.85 s** against the
        cluster (measured on asic6 -- it stats every job record), where
        `events` is 0.11 s. An "ambient awareness" pane that re-polls a
        ten-second call every few seconds is not ambient, it is a second job
        running on the head node forever.

        So it is TTL-cached like a remote listing, its cost is recorded in
        `estimate.py`, and the client sets its own refresh interval from what
        the last poll actually took -- the same rule the waveform watch
        already follows. No second cost model (§6.5), and no fixed interval
        chosen from a guess about how fast the cluster is today.
        """
        if remotemod is None:
            return self._err(503, "no cluster transport -- see cluster.py")
        # THE JOB STORE IS A FILESYSTEM, NOT A HOST. `$JOBS` lives on the one
        # shared NFS home, so every host returns the same records -- measured:
        # asic6, asic7 and asic8 each return the identical 389-job set (sha
        # b7a1a6ab8836), and those records name 11 different hosts as where
        # the work actually ran. Only the latency differs (5.9 / 13.9 / 23.0 s).
        #
        # So the cache is keyed on the FS, exactly as the render cache is
        # (roots.py: "a wrong guess would serve one machine's layout as
        # another's"), and the reading host is chosen, reported as provenance,
        # and failed over -- never configured as if it decided what you see.
        fs, hosts = self._jobs_fs(q)
        prefer = (q.get("host") or [""])[0] or None
        if prefer and not _SAFE_HOST.match(prefer):
            return self._err(400, "not a host name")
        refresh = _yes(q, "refresh")
        now = time.time()
        with _JOBS_GUARD:
            hit = _JOBS_CACHE.get(fs)
        if hit and not refresh and now - hit[0] < JOBS_TTL:
            out = dict(hit[1])
            out["cached"] = True
            return self._json(out)
        res, host, elapsed = hostsmod.shared_read(
            lambda h: remotemod.Transport(host=h, timeout=60).list(),
            hosts=hosts, fs=fs, prefer=prefer)
        if res is None or not res.ok:
            # Not "no jobs". Every candidate host failed to answer, which is
            # UNKNOWN -- and an empty jobs table would read as a quiet cluster
            # (remote.py invariant 5, carried to the panel).
            tried = ", ".join(hosts[:6]) + ("…" if len(hosts) > 6 else "")
            return self._err(503, "no host on %s answered (tried %s)"
                             % (fs, tried))
        estimate.record("jobs", "remote", 1, elapsed)
        data = res.data or {}
        out = {"fs": fs, "host": host, "hosts": hosts,
               "now": data.get("now") or int(now),
               "jobs": data.get("jobs") or [], "cached": False,
               "seconds": round(elapsed, 2)}
        with _JOBS_GUARD:
            _JOBS_CACHE[fs] = (now, out)
        return self._json(out)

    def _jobs_fs(self, q):
        """(fs, [candidate hosts]) for the job store.

        The filesystem comes from `roots.json` -- it is already the place this
        installation declares which hosts see the same files, and inventing a
        second declaration beside it is how the two drift. Candidates are
        every host configured on that fs, then the transport's own list, so a
        site that configures one root still gets failover.
        """
        fs = (q.get("fs") or [_JOBS_FS])[0]
        # ORDER MATTERS AND `hosts.py` OWNS IT. Its CANDIDATES list is curated
        # and deliberately ordered; `roots.json` names browsing roots, and its
        # order is about how someone wants to see their trees. Taking roots
        # first put asic7 at the head -- measured at 13.5 s against asic6's
        # 5.9 s for the same 389 records -- so the panel habitually asked the
        # slowest box. Roots still CONTRIBUTE hosts, which is how a site that
        # configures a machine hosts.py has never heard of still gets one.
        out = list(hostsmod.candidates()) if hostsmod else []
        for r in self.roots:
            if r.kind == "remote" and r.fs == fs and r.host not in out:
                out.append(r.host)
        return fs, out

    def _api_procs(self, q):
        """Scan cluster hosts for abandoned tool processes. READ ONLY.

        The scan is a GET because it only looks; the kill is a POST behind the
        same-origin guard, because it is the only thing this server does that
        cannot be undone.
        """
        if procscan is None:
            return self._err(503, "procscan is not importable -- it lives "
                                  "beside the cluster transport")
        hosts = [h for h in (q.get("host") or []) if h]
        if not hosts:
            # The CLUSTER's host list, not just the hosts our roots happen to
            # name. Scoping the scan to configured roots missed asic8 entirely
            # -- which is where the 62-day Virtuoso was. A stale process is
            # wherever it was launched, and that is rarely where you are
            # browsing.
            hosts = list(procscan.default_hosts())
            for r in self.roots:
                if r.kind == "remote" and r.host and r.host not in hosts:
                    hosts.append(r.host)
        if not hosts:
            return self._json({"hosts": {}, "note": "no hosts to scan"})
        return self._json({"hosts": procscan.scan(hosts)})

    def _api_kill(self):
        """Terminate ONE process, after re-verifying what it is.

        Every guard that matters is on the far side (ownership, command-line
        match, TERM before KILL) -- see procscan._PY_KILL. This route only
        carries the request, and refuses to carry one that names no
        expectation, because an unverified kill is the whole hazard.
        """
        if procscan is None:
            return self._err(503, "procscan is not importable")
        body = self._body()
        host = (body.get("host") or "").strip()
        pid = body.get("pid")
        expect = (body.get("expect") or "").strip()
        if not host or not isinstance(pid, int):
            return self._err(400, "host and integer pid are required")
        if not expect:
            return self._err(400, "an 'expect' substring is required -- a "
                                  "kill that does not re-verify the command "
                                  "line can hit a recycled pid")
        try:
            got = procscan.kill_procs(host, pid, expect,
                                      bool(body.get("force")))
        except procscan.ScanError as exc:
            return self._err(503, "cluster: %s" % exc)
        return self._json(got)

    def _api_designcheck(self, q):
        """`designdb --check --fast` on the served repo, cached per manifest
        signature -- the badge on the `design` root. A GET, and read-only
        in effect: the check writes nothing (tools.design_check)."""
        sig = model.design_signature(rootsmod.REPO)
        got = tools.design_check(rootsmod.REPO, signature=sig)
        got["n_manifests"] = len(sig)
        return self._json(got)

    def _api_render(self, q):
        """Render a GDS on demand and hand back a URL for the cached PNG."""
        if self._root_of(q).kind == "remote":
            return self._api_render_remote(q)
        _root, abspath = self._resolve(q)
        if not abspath.lower().endswith(".gds"):
            return self._err(400, "not a layout")
        win = [_f((q.get(k) or [None])[0]) for k in ("x0", "y0", "x1", "y1")]
        win = win if all(v is not None for v in win) else None
        size = os.path.getsize(abspath)
        t0 = time.time()
        got = tools.render_gds(abspath, win=win)
        elapsed = time.time() - t0
        out = {"ok": got["ok"], "state": got["state"], "error": got["error"],
               "url": None, "seconds": round(elapsed, 2)}
        if got["ok"]:
            out["url"] = "/render/" + os.path.basename(got["png"])
        # ONLY a real render is a sample. A cache hit is 0.001 s and recording
        # it would teach the estimator that rendering is instant -- after
        # which the pane would promise an instant render of a stream it has
        # never drawn.
        #
        # A CROP IS NOT A SAMPLE OF THE WHOLE, and now really is not: the
        # crop clips before drawing, so a 20 um window off the ctrl2 pilot is
        # 3.1 s against 72.3 s for the die. It gets no estimate of its own
        # either -- its cost tracks the window size and the local polygon
        # density, not the file size, so a rate per megabyte would be the
        # "scary number that means nothing" from the other direction. The
        # counter reports what it actually took.
        if got["state"] == "rendered" and not win:
            estimate.record("render", "local", size, elapsed)
        return self._json(out)

    def _api_xor(self, q):
        """Geometric XOR of two local layouts -- the visual regression (6.2).

        LOCAL only, like the KLayout handoff and for the same reason: strmxor
        ships with KLayout and there is no KLayout on the cluster. A cluster
        pair would need both streams pulled first, which is what 5.3 says not
        to do.
        """
        root = self._root_of(q)
        if root.kind == "remote":
            return self._err(400, "XOR is local-only -- strmxor ships with "
                                  "KLayout, and the cluster has no KLayout")
        _r, a = self._resolve(q)
        # the compare target is confined through the SAME resolve, in the same
        # root: a second path in a request gets no more trust than the first
        _r2, b = rootsmod.resolve(self.roots, (q.get("root") or [""])[0],
                                  (q.get("other") or [""])[0])
        for p in (a, b):
            if not p.lower().endswith(".gds"):
                return self._err(400, "not a layout")
        t0 = time.time()
        got = tools.xor_gds(a, b)
        estimate.record("xor", "local", os.path.getsize(a) + os.path.getsize(b),
                        time.time() - t0)
        out = {"ok": got["ok"], "identical": got["identical"],
               "layers": got["layers"], "error": got["error"], "url": None,
               "total": sum(l["shapes"] for l in got["layers"])}
        if got["ok"] and not got["identical"] and got["out"]:
            # the diff is itself a GDS, so the ORDINARY renderer draws it --
            # one renderer, and the picture is in the same visual language as
            # every other layout view (principle 4)
            r = tools.render_gds(got["out"])
            out["url"] = ("/render/" + os.path.basename(r["png"])
                          if r["ok"] else None)
            if not r["ok"]:
                out["error"] = "diff rendered empty: %s" % r["error"]
        return self._json(out)

    def _api_render_remote(self, q):
        """Render a cluster GDS ON THE CLUSTER; only the PNG crosses the wire.

        The rule the plan states and the reason this is worth building: at
        28 nm every layout is remote, and pulling one to look at it moves
        hundreds of megabytes to produce a three-megabyte picture.
        """
        root, rel = rootsmod.resolve_remote(
            self.roots, (q.get("root") or [""])[0], (q.get("path") or [""])[0])
        if not rel.lower().endswith(".gds"):
            return self._err(400, "not a layout")
        # The cache key names the FILESYSTEM, not the host that read it. The
        # 28 nm divider renders byte-identically from asic6, asic7 and asic8
        # (sha 1dd0e73d3d48) because all three see one shared home through one
        # shared miniforge -- so a render is an artifact, and the host that
        # produced it is provenance. Sites that do not share get per-host keys
        # by default (roots.py: fs defaults to host).
        key = "r" + hashlib.sha256(
            ("%s|%s|%s" % (root.fs, root.path, rel)).encode()).hexdigest()[:31]
        out = os.path.join(tools.cache_dir(), key + ".png")
        if os.path.exists(out) and os.path.getsize(out):
            return self._json({"ok": True, "state": "cached", "error": None,
                               "url": "/render/" + key + ".png"})
        t0 = time.time()
        try:
            spec = tools.RENDERER_SPEC
            png, info = cluster.render(
                root.host, root.path, rel,
                # the declared renderer, as the repo-relative path it was
                # declared under -- the cluster checkout is somewhere else
                renderer_rel=(os.path.relpath(spec.path, rootsmod.REPO)
                              if spec.declared else None),
                render_argv=spec.argv if spec.declared else None)
        except cluster.RemoteError as exc:
            return self._json({"ok": False, "state": "failed", "url": None,
                               "error": str(exc)})
        # The stream's size comes back WITH the picture -- the remote script
        # already stat'ed it against the render limit, so the estimator learns
        # the rate without a second round trip to ask how big it was.
        estimate.record("render", "remote", int(info.get("gds_size") or 0),
                        time.time() - t0)
        try:
            os.makedirs(tools.cache_dir(), exist_ok=True)
            tmp = out + ".part.png"
            with open(tmp, "wb") as fh:
                fh.write(png)
            os.replace(tmp, out)
        except OSError as exc:
            return self._json({"ok": False, "state": "error", "url": None,
                               "error": str(exc)})
        return self._json({"ok": True, "state": "rendered", "url":
                           "/render/" + key + ".png", "error": None,
                           "where": "%s (%s)" % (root.host,
                                                 info.get("python") or "?")})

    #: A cache filename is hex, optionally prefixed `r` for a REMOTE render,
    #: and nothing else. The cache lives OUTSIDE every configured root, so
    #: roots.resolve does not apply to it and this pattern is the whole guard
    #: -- it admits no separator, no dot segment and no extension but .png.
    #:
    #: The `r` had to be added here, not just minted upstream: the remote
    #: renders came back fine and then every one of them 403'd on the way to
    #: the <img>, because "r" is not a hex digit. A guard and the thing it
    #: guards have to be changed together.
    #:
    #: IT HAPPENED AGAIN, and the second time it hid for longer. A CROP is
    #: cached as `<hash>_<window hash>.png` -- the window is part of the
    #: picture's identity -- and an underscore is not a hex digit either. So
    #: every cropped render since the crop was added completed, was written to
    #: disk, reported "rendered" to the pane, and then 403'd on the way to the
    #: <img>: a success message beside a blank box. The button and the picture
    #: are different subsystems, and only the picture is evidence.
    _CACHE_NAME = re.compile(r"^r?[0-9a-f]{1,64}(_[0-9a-f]{1,32})?\.png$")

    def _render_bytes(self, name):
        name = unquote(name)
        if not self._CACHE_NAME.match(name):
            return self._err(403, "not a render")
        p = os.path.join(tools.cache_dir(), name)
        if not os.path.isfile(p):
            return self._err(404, "no such render")
        return self._bytes(p)

    def _api_klayout(self):
        """Hand the selected layout to the KLayout GUI. The one exec."""
        try:
            body = self._body()
        except ValueError as exc:
            return self._err(400, str(exc))
        q = {"root": [body.get("root") or ""], "path": [body.get("path") or ""]}
        _root, abspath = self._resolve(q)          # same confinement as GET
        if not abspath.lower().endswith(".gds"):
            return self._err(400, "not a layout")
        got = tools.open_in_klayout(abspath)
        return self._json({"ok": got["ok"], "error": got["error"]})

    def _file_bytes(self, tail):
        """GET /f/<root-slug>/<relpath> -- serve a file under a PATH-shaped URL.

        This exists because the query form cannot host an HTML artifact. A
        report.html served from `/raw?root=X&path=d/report.html` is a page whose
        URL has no directory, so its relative `<img src="render_full.png">`
        resolves to `/render_full.png` and 404s -- every image in an embedded
        report came out broken, which is exactly how it was found. Under
        `/f/<slug>/d/report.html` the same reference resolves to
        `/f/<slug>/d/render_full.png` and just works.

        Confinement is unchanged: the tail is URL-decoded and handed to the same
        roots.resolve, so `..`, absolute paths and symlinks are refused here for
        the same reasons.
        """
        tail = unquote(tail)
        slug, _, rel = tail.partition("/")
        root = rootsmod.by_slug(self.roots, slug)
        if root is None:
            return self._err(403, "unknown root: %r" % slug)
        if root.kind == "remote":
            # Same size cap as the viewer: an <img> tag must not be able to
            # start an unbounded pull just by existing on the page.
            _r, relp = rootsmod.resolve_remote(self.roots, root.name, rel)
            try:
                raw, _trunc, _size = cluster.fetch(root.host, root.path, relp)
            except cluster.RemoteError as exc:
                return self._err(503, "cluster: %s" % exc)
            ctype = (mimetypes.guess_type(relp)[0]
                     or ("text/plain; charset=utf-8"
                         if model.kind_of_name(relp) == "text"
                         else "application/octet-stream"))
            return self._send(200, raw, ctype)
        _root, abspath = rootsmod.resolve_slug(self.roots, slug, rel)
        return self._bytes(abspath)

    def _bytes(self, abspath):
        if os.path.isdir(abspath):
            return self._err(400, "is a directory")
        # mimetypes does not know .log/.rep/.rpt/.out/.sum/.lyrdb and returns
        # octet-stream, which makes a browser offer to DOWNLOAD a tool log
        # instead of showing it. Our own kind table already knows these are
        # text, so let it win for that case.
        if model.kind_of(abspath) == "text":
            ctype = "text/plain; charset=utf-8"
        else:
            ctype = mimetypes.guess_type(abspath)[0] or "application/octet-stream"
        with open(abspath, "rb") as fh:
            data = fh.read()
        # inline, not attachment: /raw is what the <img> tags point at
        return self._send(200, data, ctype,
                          {"Content-Disposition":
                           'inline; filename="%s"' % quote(
                               os.path.basename(abspath))})


#: A FIXED default port, not an OS-chosen one.
#:
#: The original design took a free port from the OS, which is tidy and was the
#: wrong call: the URL then exists only in one line of stdout. Lose the
#: terminal and the running server is unreachable -- there is no way to ask
#: "where is it?" -- and a bookmark is invalid the next time it starts. A
#: stable port means the URL is always the same and a reload always works.
DEFAULT_PORT = 8730
#: If the default is taken, walk a small range before giving up on being
#: predictable. Beyond that fall back to an OS port, and SAY so.
PORT_SPAN = 10

#: How this instance was started, quoted back in the "not running" and "lost
#: the terminal" lines. A variable because the front door moved: `launch.py
#: <repo>` serves any checkout, and telling someone to run `browse/server.py`
#: in a repo that only has a launcher stub sends them somewhere that cannot
#: start what they were just looking at. The launcher overwrites it.
START_HINT = "python browse/server.py"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


#: One parsed sweep, keyed by (path, size, mtime). Zooming re-renders from
#: the samples, so a wheel gesture is several requests against the same file;
#: a 38 ms parse each time is affordable but pointless. Keyed on size AND
#: mtime so a GROWING file -- the case this whole view exists for -- is
#: correctly re-read rather than served stale.
_WAVE_CACHE = {}


def _wave_cached(path):
    """-> (Wave, seconds_spent_parsing or None)

    None for a cache hit, and the caller must not record it: a hit is a
    dictionary lookup, and feeding 0.0 s into the estimator would teach it
    that parsing is free -- exactly the wrong lesson for the file the watch
    checkbox is re-reading every few seconds.
    """
    try:
        st = os.stat(path)
        key = (path, st.st_size, int(st.st_mtime))
    except OSError:
        key = (path, None, None)
    hit = _WAVE_CACHE.get(key)
    if hit is not None:
        return hit, None
    t0 = time.time()
    # read_any, not read_psf: ONR's characterisation flow asks spectre for
    # `-format nutascii` where the analog flow asks for psfascii, and the
    # format is decided by CONTENT because spectre is told `-raw <path>` with
    # no extension at all.
    hit = wavemod.read_any(path)
    secs = time.time() - t0
    _WAVE_CACHE.clear()                 # one file at a time is the usage
    _WAVE_CACHE[key] = hit
    return hit, secs


#: One walk per (root, nets) is enough -- the abstracts do not move while a
#: pane is open, and re-walking every root on every schematic click would turn
#: a highlight into a filesystem crawl.
_CP_CACHE = {}
_CP_GUARD = threading.Lock()


def _crossprobe_all(roots, nets):
    """Layout abstracts sharing nets, across every LOCAL root. -> [cand]

    Each candidate carries the root it was found in, because the client has
    to ask for it by (root, path) and a relative path alone is ambiguous the
    moment more than one root is configured -- which is the normal case here.
    """
    key = (tuple(sorted(nets)),
           tuple(r.name for r in roots if r.kind != "remote"))
    with _CP_GUARD:
        hit = _CP_CACHE.get(key)
    if hit is not None:
        return hit
    out, seen = [], set()
    for r in roots:
        if r.kind == "remote" or not os.path.isdir(r.path):
            continue
        for c in model.crossprobe_candidates(r.path, nets):
            # ROOTS OVERLAP -- `repo` is the checkout and `analog/work` is
            # inside it -- so the same abstract is found twice and the picker
            # offered every layout as a duplicate pair. Deduped on the real
            # path, keeping the FIRST root that yielded it: roots.json lists
            # the specific ones before the catch-all, so the offer names the
            # tree the user actually thinks in.
            key = os.path.realpath(os.path.join(r.path, c["rel"]))
            if key in seen:
                continue
            seen.add(key)
            c["root"] = r.name
            out.append(c)
    out.sort(key=lambda c: (-c["shared"], c["name"]))
    out = out[:8]
    with _CP_GUARD:
        _CP_CACHE[key] = out
    return out


def _yes(q, name):
    """A query flag that is only true when it says so."""
    return (q.get(name) or ["0"])[0] not in ("0", "", "false", "no")


def _wave_view(q):
    """The view controls, parsed once. -> dict

    Local and remote take the SAME dict -- the remote half ships it to the
    cluster as JSON — so a control cannot come to mean two things.

    `signals` is None when the request names none at all and a LIST (possibly
    empty) when it does. The distinction is the whole of the checkbox
    behaviour: `None` means "you choose, and cap it", `[]` means the user
    unticked every box and wants an empty frame. Collapsing them would make
    the last untick silently redraw everything.
    """
    sig = [s for s in (q.get("sig") or []) if s]
    return {"signals": sig if ("sig" in q or _yes(q, "nosig")) else None,
            "mode": (q.get("mode") or ["group"])[0],
            "part": (q.get("part") or ["db"])[0],
            "xlog": _tri(q, "xlog"), "ylog": _tri(q, "ylog"),
            "x0": _f((q.get("x0") or [None])[0]),
            "x1": _f((q.get("x1") or [None])[0]),
            "y0": _f((q.get("y0") or [None])[0]),
            "y1": _f((q.get("y1") or [None])[0])}


def _tri(q, name):
    """None / True / False -- an axis the user has NOT touched must keep the
    per-analysis default, and `absent` is the only way to say that."""
    v = (q.get(name) or [None])[0]
    if v is None or v == "":
        return None
    return v not in ("0", "false", "no")


def _wave_payload(wm, w, q):
    """The wave half of a file response -- ONE builder, local and remote.

    The remote side computes this on the cluster with the same `choose` and
    the same `render`; keeping the local assembly in one function is what
    stops the two drifting into different pictures of the same file.
    """
    v = _wave_view(q)
    part = v["part"]
    if w.is_point or w.is_index:
        # A table, or a list of what was swept. Either way no picture is
        # built: everything below this line is about curves.
        return {"wave": w.summary(part), "svg": ""}
    names, dropped = wm.choose(w, v["signals"], part=part)
    # `summary` already carries the units for the drawable traces, keyed on
    # the chosen part -- `out` is volts as a magnitude and dB as a gain. It is
    # NOT re-derived here over w.order: doing that put a 5886-entry dict back
    # on the wire for a picture of three curves.
    s = w.summary(part)
    s["mode"] = v["mode"]
    s["drawn"] = names
    s["dropped"] = dropped
    xlog, ylog = wm.default_scales(w, part)
    s["xlog"] = xlog if v["xlog"] is None else v["xlog"]
    s["ylog"] = ylog if v["ylog"] is None else v["ylog"]
    return {"wave": s,
            "svg": wm.render(w, names, mode=v["mode"], part=part,
                             xlog=v["xlog"], ylog=v["ylog"],
                             x0=v["x0"], x1=v["x1"],
                             y0=v["y0"], y1=v["y1"])}


def state_path():
    """Where the running server records its URL -- outside the repo.

    So that `--status` can answer "where is it?" from any shell, and so a
    second launch can find the first instead of racing it.

    KEYED BY THE SERVED REPO. One installation now serves several checkouts,
    and they share a cache directory -- so a single state file made them lie
    about each other: launching the ONR browser on 8745 overwrote the record,
    and the next AIML launch probed it, found a live server, and exited as
    "already running" against a browser serving a different repository.
    """
    tag = hashlib.sha256(rootsmod.REPO.encode("utf-8")).hexdigest()[:8]
    return os.path.join(tools.cache_dir(), "browse-server-%s.json" % tag)


def read_state():
    try:
        with open(state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def probe(url, timeout=1.5):
    """Is one of OUR servers answering at `url`? -> the health dict or None."""
    try:
        from urllib.request import urlopen
        with urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            got = json.loads(r.read().decode("utf-8"))
        return got if got.get("app") == "artifact-browser" else None
    except Exception:                              # noqa: BLE001
        return None


def _is_ours(got):
    """Does this health reply come from a server serving THIS repo?

    `None` is accepted because a server old enough not to report its repo
    predates there being more than one.
    """
    return bool(got) and got.get("repo") in (None, rootsmod.REPO)


def status(quiet=False):
    """Print where the browser is running, if it is. -> url or None."""
    st = read_state() or {}
    url = st.get("url")
    # THE REPO IS CHECKED HERE TOO, not just in the scan below. A killed server
    # never clears its state file, so a stale record routinely outlives the
    # process -- and the port it names is the stable one, which the NEXT repo's
    # browser will take. Probing for "is a browser answering" then found that
    # other repo's server, reported it as this repo's, and made `serve()`
    # decline to start: you were handed another checkout's artifacts under your
    # own repo's name. Keying the state file by repo (state_path) was only half
    # the fix; this branch has to use the answer.
    if url and _is_ours(probe(url)):
        if not quiet:
            print("[browse] running at %s (pid %s, ui %s)"
                  % (url, st.get("pid"), st.get("ui")))
        return url
    # The state file can be stale -- a killed server never gets to clear it --
    # so a recorded URL that does not answer is reported as gone, not as the
    # answer. Then try the stable port anyway, in case the file was lost, and
    # accept it only if it is serving THIS repo: a browser on the next port
    # may perfectly well be another checkout's.
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN):
        cand = "http://127.0.0.1:%d/" % port
        if _is_ours(probe(cand, timeout=0.4)):
            if not quiet:
                print("[browse] running at %s" % cand)
            return cand
    if not quiet:
        print("[browse] not running -- start it with: %s" % START_HINT)
    return None


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that will not steal a port already in use.

    `http.server` sets `allow_reuse_address = 1`, and that flag means two
    different things. On POSIX it lets a restart re-bind through TIME_WAIT and
    a bind to a LIVE port still fails, which is what `_bind`'s port walk is
    built on. On Windows SO_REUSEADDR means the second bind SUCCEEDS and takes
    the port -- so the walk never fired: three browsers all "bound" 8730,
    reported 8730, and requests went to whichever socket the kernel picked.

    Found by running all three repos at once. With one server it looks fine
    forever, which is why it survived until there were three repos to run.

    Conditional rather than just False, because on POSIX the flag is doing
    real work: this server is restarted constantly on 127.0.0.1, which is
    exactly the TIME_WAIT case it exists for.
    """
    allow_reuse_address = (os.name != "nt")


def _bind(port):
    """(httpd, note) -- prefer the stable port, fall back honestly."""
    if port is not None:
        return _Server(("127.0.0.1", port), Handler), ""
    for p in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN):
        try:
            return _Server(("127.0.0.1", p), Handler), (
                "" if p == DEFAULT_PORT else
                " (port %d was busy)" % DEFAULT_PORT)
        except OSError:
            continue
    httpd = _Server(("127.0.0.1", 0), Handler)
    return httpd, " (ports %d-%d all busy)" % (DEFAULT_PORT,
                                               DEFAULT_PORT + PORT_SPAN - 1)


def serve(port=None, open_browser=True, config_dir=None):
    # If one of ours is already up, do not start a second: two servers on two
    # ports is exactly how you end up looking at a stale tab and wondering why
    # your change is missing.
    running = status(quiet=True)
    if running and port is None:
        print("[browse] already running at %s" % running, flush=True)
        if open_browser:
            webbrowser.open(running)
        return 0

    Handler.roots = rootsmod.load(config_dir)
    # The served repo's DECLARATIONS (roots.SETTINGS_KEYS): which renderer,
    # how it is called, which files are badge sources. Applied here, after
    # the roots, and refused loudly -- a declaration that fails to parse
    # would otherwise be a browser quietly running on the fallback list.
    Handler.settings = rootsmod.settings(config_dir)
    tools.configure(Handler.settings)
    model.configure_badges(Handler.settings.get("badges"))
    # THREADING, not the plain HTTPServer: a GDS render is a subprocess that
    # runs for tens of seconds, and on a single-threaded server that request
    # blocks every other one -- the whole browser freezes, including the
    # listing you would use to go somewhere else while you wait.
    httpd, note = _bind(port)
    url = "http://127.0.0.1:%d/" % httpd.server_port
    try:
        os.makedirs(tools.cache_dir(), exist_ok=True)
        with open(state_path(), "w", encoding="utf-8") as fh:
            json.dump({"url": url, "port": httpd.server_port,
                       "pid": os.getpid(), "ui": UI_VERSION}, fh)
    except OSError:
        pass                        # discoverability is a convenience, not a gate

    # flush: python block-buffers stdout when it is not a tty, so piping or
    # redirecting the server swallowed exactly the line the user needs.
    print("[browse] %s%s" % (url, note), flush=True)
    print("[browse] ui %s" % UI_VERSION, flush=True)
    for r in Handler.roots:
        where = ("%s:%s" % (r.host, r.path) if r.kind == "remote" else r.path)
        # exists is None for a remote root -- unknown, not missing (roots.py)
        print("   %-18s %s%s" % (r.name, where,
                                 "   (missing)" if r.exists is False else ""))
    print("[browse] read-only; Ctrl-C to stop.  Lost this line? "
          "%s --status" % START_HINT, flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[browse] stopped")
    finally:
        httpd.server_close()
        try:
            os.unlink(state_path())
        except OSError:
            pass
    return 0


def main():
    ap = argparse.ArgumentParser(description="local artifact browser")
    ap.add_argument("--port", type=int, default=None,
                    help="default: %d, or the next free port after it"
                         % DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--status", action="store_true",
                    help="print where the browser is running, and exit")
    ap.add_argument("--config-dir", default=None,
                    help="directory holding roots.json (default: this one). "
                         "Set by the launcher when one installation serves "
                         "another checkout.")
    a = ap.parse_args()
    if a.status:
        return 0 if status() else 1
    return serve(a.port, not a.no_open, config_dir=a.config_dir)


if __name__ == "__main__":
    sys.exit(main())
