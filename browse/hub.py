#!/usr/bin/env python3
"""The hub: one page over every repo's artifact browser.

    python browse/hub.py            # http://127.0.0.1:8729, from any checkout
    python browse/hub.py --status   # where it is, if it is up

WHY THIS EXISTS. Each repo runs its own browser -- deliberately, because a
server's confinement boundary is the repo it was started on (`roots.REPO`
is fixed at import) and making that per-request is the one thing roots.py
refuses. With five ports that turned into five tabs on five ports, a page
that did not say which repo it was (fixed separately), five copies of the
same cluster job list each paying its own ten-second read, and an agent
panel that could not see sessions started in the parent directory.

So the hub does NOT browse. It serves no roots and cannot read a single
file in any repo. It answers four questions and links out for everything
else:

  * which repos exist, what each one IS (its README line), which branch it
    is on and how far behind its upstream it was at the last fetch -- a
    stale checkout made the design-record badges cry wolf once, and that is
    the first thing to rule out;
  * which of their browsers are running, where, and on which build; it can
    START a stopped one (the repo's own vendored `browse/server.py`, so each
    checkout still serves itself, ADR-0004);
  * the cluster's jobs and stale processes, ONCE for the machine -- the
    same panel the repo pages carry, reading the same shared cache file;
  * what the agents are doing in every repo at once, including sessions
    started in the parent directory, with an artifact link that opens the
    right repo's browser at that file (a deep link, `#r=&p=&v=`).

The port sits just below the repo browsers' range (8730..8739) so a repo
server's port walk can never land on it and `--status` scans never mistake
it for one: its health reply says `artifact-hub`, not `artifact-browser`.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse, parse_qs, quote

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# The hub serves no repo; pin the import-time REPO to this installation so
# nothing below resolves against whatever directory it was started from.
# setdefault: a process that already chose a repo (a test) keeps its choice.
os.environ.setdefault("BROWSE_REPO", os.path.dirname(_HERE))

import launch                                          # noqa: E402
import server                                          # noqa: E402
import agentview                                       # noqa: E402
import tools                                           # noqa: E402

HUB_PORT = 8729
#: How long a "start" is given to produce a reachable server before the page
#: says it did not, and points at the log rather than spinning forever.
START_GRACE_S = 25.0
_STARTING = {}                                         # repo path -> started at
_STARTING_GUARD = threading.Lock()


# ----------------------------------------------------------------- the repos
def _git(repo, *args, timeout=5):
    try:
        r = subprocess.run(["git", "-C", repo] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 \
        else None


def git_state(repo):
    """{branch, ahead, behind, dirty} from LOCAL refs -- no fetch.

    Behind is "as of the last fetch", and the page says so: fetching five
    remotes on every refresh would make the hub a network tool, and the
    point is a glance. None for a directory that is not a checkout.
    """
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return None
    ahead = behind = None
    lr = _git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if lr:
        try:
            ahead, behind = (int(x) for x in lr.split())
        except ValueError:
            pass
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return {"branch": branch, "ahead": ahead, "behind": behind,
            "dirty": len(dirty.splitlines()) if dirty else 0}


def _same(a, b):
    return os.path.normcase(os.path.realpath(a or "")) == \
        os.path.normcase(os.path.realpath(b or ""))


def _parallel(fn, items):
    """[fn(x) for x in items], concurrently. Each item is a local probe or a
    local git call, and on Windows a refused localhost connection alone
    costs ~0.5 s -- serially, ten ports times five repos was half a minute
    per page refresh."""
    out = [None] * len(items)

    def run(i, x):
        out[i] = fn(x)
    ts = [threading.Thread(target=run, args=(i, x), daemon=True)
          for i, x in enumerate(items)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    return out


def live_browsers(extra_urls=()):
    """[{url, ui, pid, repo}] of every artifact browser answering locally.

    The browsers' port range plus whatever URLs the repos' state files name
    (a browser started with --port lives outside the range). The answer is
    each server's OWN health reply -- which names the repo it serves, so a
    port is attributed by what it says, never by which port it happens to be.
    """
    urls = ["http://127.0.0.1:%d/" % p for p in
            range(server.DEFAULT_PORT, server.DEFAULT_PORT + server.PORT_SPAN)]
    urls += [u for u in extra_urls if u and u not in urls]
    got = _parallel(lambda u: server.probe(u, timeout=1.0), urls)
    return [dict(h, url=u) for u, h in zip(urls, got) if h]


def _state_url(repo):
    try:
        with open(server.state_path(repo), encoding="utf-8") as fh:
            return json.load(fh).get("url")
    except (OSError, ValueError):
        return None


def browser_state(repo, live=None):
    """{url, ui, pid} of the browser serving `repo`, or None."""
    if live is None:
        live = live_browsers([_state_url(repo)])
    for h in live:
        if _same(h.get("repo"), repo):
            return {"url": h["url"], "ui": h.get("ui"), "pid": h.get("pid")}
    return None


def repos():
    """Every onboarded checkout, with what it is and whether it is up."""
    out = []
    rs = launch.onboarded()
    live = live_browsers([_state_url(r) for r in rs])
    gits = _parallel(git_state, rs)
    for r, g in zip(rs, gits):
        name = os.path.basename(r)
        b = browser_state(r, live)
        with _STARTING_GUARD:
            t0 = _STARTING.get(r)
            if b and t0:
                _STARTING.pop(r, None)
                t0 = None
        starting = bool(t0) and time.time() - t0 < START_GRACE_S
        out.append({"name": name, "tag": server.repo_tag(name), "path": r,
                    "describe": launch.describe(r, maxlen=90),
                    "git": g, "browser": b,
                    "starting": starting,
                    "start_failed": bool(t0) and not starting,
                    "log": _log_path(r)})
    return out


def _log_path(repo):
    return os.path.join(tools.cache_dir(),
                        "hub-%s.log" % server.repo_tag(os.path.basename(repo)))


def start(repo):
    """Start the browser for `repo`, detached. -> what was run.

    The repo's OWN vendored `browse/server.py`, run in the repo: each
    checkout serves itself (ADR-0004), so the browser the hub starts is the
    same one `python browse/server.py` would have started there. Only an
    onboarded checkout can be named -- the hub starts nothing else.
    """
    own = os.path.join(repo, "browse", "server.py")
    if os.path.exists(own):
        cmd = [sys.executable, own, "--no-open"]
    else:
        cmd = [sys.executable, os.path.join(_HERE, "launch.py"), repo,
               "--no-open"]
    os.makedirs(tools.cache_dir(), exist_ok=True)
    log = open(_log_path(repo), "ab")
    kw = {"cwd": repo, "stdin": subprocess.DEVNULL, "stdout": log,
          "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                               | getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        kw["start_new_session"] = True
    env = dict(os.environ)
    env.pop("BROWSE_REPO", None)       # the hub's pin must not leak into it
    kw["env"] = env
    subprocess.Popen(cmd, **kw)
    log.close()
    with _STARTING_GUARD:
        _STARTING[repo] = time.time()
    return cmd


def locate(path):
    """A deep link into whichever repo's browser holds `path`. -> dict

    The longest local root of that repo's RUNNING browser that contains the
    path, asked of that browser (its /api/roots) -- the hub does not guess
    another repo's root names.
    """
    from urllib.request import urlopen
    norm = os.path.normcase(os.path.normpath(path))
    owner = None
    for r in launch.onboarded():
        rr = os.path.normcase(os.path.normpath(r))
        if norm == rr or norm.startswith(rr + os.sep):
            owner = r
    if owner is None:
        return {"error": "not inside any onboarded repo"}
    b = browser_state(owner)
    if not b:
        return {"error": "%s's browser is not running -- start it first"
                % os.path.basename(owner), "repo": owner}
    try:
        with urlopen(b["url"].rstrip("/") + "/api/roots", timeout=5) as fh:
            roots = json.loads(fh.read().decode("utf-8"))["roots"]
    except Exception as exc:                           # noqa: BLE001
        return {"error": "could not ask %s for its roots: %s" % (b["url"], exc)}
    best = None
    for rt in roots:
        if rt.get("kind") != "local":
            continue
        rp = os.path.normcase(os.path.normpath(rt["path"]))
        if (norm == rp or norm.startswith(rp + os.sep)) and \
                (best is None or len(rp) > len(best[1])):
            best = (rt, rp)
    if best is None:
        return {"url": b["url"], "note": "no root of that browser holds it"}
    rel = os.path.relpath(path, best[0]["path"]).replace(os.sep, "/")
    d = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return {"url": b["url"] + "#r=%s&p=%s&v=%s"
            % (quote(best[0]["name"]), quote(d), quote(rel))}


# ----------------------------------------------------------------- the server
class HubHandler(server.Handler):
    """The browser's handler, narrowed to the hub's routes.

    Jobs, processes, the kill and the agent view are the browser's own
    routes, inherited unchanged; everything that reads a repo is refused.
    """
    roots = []
    _PASS_GET = ("/api/jobs", "/api/procs", "/api/agent")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, hub_page(), "text/html; charset=utf-8")
            if u.path == "/api/health":
                return self._json({"app": "artifact-hub", "ui": HUB_UI,
                                   "pid": os.getpid()})
            if u.path == "/api/hub":
                return self._json({"repos": repos(), "ui": server.UI_VERSION})
            if u.path == "/api/hub/locate":
                return self._json(locate((q.get("path") or [""])[0]))
            if u.path in self._PASS_GET:
                return server.Handler.do_GET(self)
            return self._err(404, "the hub serves no files -- open a repo's "
                                  "browser for that")
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:                       # noqa: BLE001
            # a hub that drops the connection reads as a hub that is down
            return self._err(500, "%s: %s" % (type(exc).__name__, exc))

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/procs/kill":
            return server.Handler.do_POST(self)
        if u.path != "/api/hub/start":
            self._drain()
            return self._err(404, "no such route")
        try:
            self._guard_same_origin()
        except ValueError as exc:
            self._drain()
            return self._err(403, str(exc))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (ValueError, OSError):
            return self._err(400, "expected a JSON body")
        want = body.get("repo") if isinstance(body, dict) else None
        repo = next((r for r in launch.onboarded()
                     if os.path.basename(r) == want), None)
        if repo is None:
            return self._err(400, "not an onboarded repo: %r" % (want,))
        if browser_state(repo):
            return self._json({"ok": True, "already": True})
        cmd = start(repo)
        return self._json({"ok": True, "cmd": cmd, "log": _log_path(repo)})

    def _agent_sessions(self):
        """Every repo's sessions and the parent directory's, labelled.

        Started-in is the label: `[tsmc65]` for a session started in that
        repo, `[C:/dev]`-style for one started in a parent directory. Newest
        first, live ones naturally on top because they are the newest.
        """
        out, seen, parents = [], set(), []
        for r in launch.onboarded():
            tag = server.repo_tag(os.path.basename(r))
            for e in agentview.sessions(r):
                if e["id"] not in seen:
                    seen.add(e["id"])
                    e["repo"] = tag
                    out.append(e)
            par = os.path.dirname(os.path.normpath(r))
            if par and par not in parents:
                parents.append(par)
        for par in parents:
            for e in agentview.sessions(par):
                if e["id"] not in seen:
                    seen.add(e["id"])
                    e["repo"] = par.replace("\\", "/")
                    out.append(e)
        return sorted(out, key=lambda e: -e["mtime"])[:30]


HUB_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>hub — artifact browser</title>
<style>
""" + server.STYLE_CSS + """
#hubmain { flex:1; overflow:auto; padding:.6rem .8rem; min-height:0; }
#repos td { vertical-align: top; }
#repos .tag { font-weight:600; }
#repos button { font:inherit; }
</style></head><body>
<header><b>spec2si hub</b>
  <span class="muted">every repo's artifact browser &middot; read-only &middot;
    localhost &middot; ui __UI__</span>
  <span style="flex:1"></span>
  <span class="muted" id="status"></span>
</header>
<div id="hubmain">
  <table id="repos"><tr><td class="muted">loading…</td></tr></table>
  <p class="muted">Each repo's browser serves only that repo. <b>Open</b> goes
    to it; <b>Start</b> runs the repo's own <code>browse/server.py</code>.
    Behind counts are as of each checkout's last fetch.</p>
</div>
""" + server.PANEL_HTML + """<script>
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function j(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}
function status(t) { const e = $("#status"); e.textContent = e.title = t || ""; }
const UI_REPOS = "__REPO_UI__";

// The artifact link-out, hub flavour: ask the hub which repo's browser holds
// the file, and open THAT browser at it.
async function agentOpen(path) {
  try {
    const d = await j("/api/hub/locate?path=" + encodeURIComponent(path));
    if (d.url) { window.open(d.url, "_blank"); return; }
    $("#imsg").textContent = d.error || "not found";
  } catch (err) { $("#imsg").textContent = err.message; }
}

function gitCell(g) {
  if (!g) return '<span class="muted">not a checkout</span>';
  const parts = [`<code>${esc(g.branch)}</code>`];
  if (g.behind) parts.push(`<span class="badge b-warn" title="as of the last
    fetch -- a stale checkout makes its design record look incomplete">${g.behind}
    behind</span>`);
  if (g.ahead) parts.push(`<span class="badge b-info">${g.ahead} ahead</span>`);
  if (g.dirty) parts.push(`<span class="muted">${g.dirty} modified</span>`);
  return parts.join(" ");
}

function browserCell(r) {
  if (r.browser) {
    const b = r.browser;
    const port = (b.url.match(/:(\\d+)\\//) || [])[1] || "";
    const old = b.ui && b.ui !== UI_REPOS
      ? ` <span class="badge b-warn" title="that browser runs ui ${esc(b.ui)};
          this installation's is ${esc(UI_REPOS)} -- restart it, or re-vendor">
          older build</span>` : "";
    return `<span class="badge b-ok">running</span> :${esc(port)}${old}`;
  }
  if (r.starting) return '<span class="badge b-run">starting…</span>';
  if (r.start_failed) return `<span class="badge b-bad" title="${esc(r.log)}">
    did not come up</span> <span class="muted">see ${esc(r.log)}</span>`;
  return '<span class="muted">stopped</span>';
}

let POLL = null;
async function loadRepos() {
  let d;
  try { d = await j("/api/hub"); }
  catch (err) { $("#repos").innerHTML =
    `<tr><td class="err">${esc(err.message)}</td></tr>`; return; }
  $("#repos").innerHTML =
    `<tr><th></th><th>repo</th><th>what it is</th><th>checkout</th>
         <th>browser</th><th></th></tr>` +
    d.repos.map(r => `<tr>
      <td class="tag">${esc(r.tag)}</td>
      <td title="${esc(r.path)}">${esc(r.name)}</td>
      <td class="muted">${esc(r.describe || "")}</td>
      <td>${gitCell(r.git)}</td>
      <td>${browserCell(r)}</td>
      <td>${r.browser
        ? `<a href="${esc(r.browser.url)}" target="_blank"><button>Open</button></a>`
        : `<button data-start="${esc(r.name)}" ${r.starting ? "disabled" : ""}>Start</button>`}</td>
    </tr>`).join("");
  const up = d.repos.filter(r => r.browser).length;
  status(`${up} of ${d.repos.length} browsers running`);
  // poll quickly only while something is starting; otherwise a slow glance
  if (POLL) clearTimeout(POLL);
  POLL = setTimeout(loadRepos, d.repos.some(r => r.starting) ? 1500 : 15000);
}

$("#repos").onclick = async e => {
  const b = e.target.closest("[data-start]");
  if (!b) return;
  b.disabled = true; b.textContent = "starting…";
  try {
    const r = await fetch("/api/hub/start", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({repo: b.dataset.start})});
    if (!r.ok) throw new Error(await r.text());
  } catch (err) { status("start failed: " + err.message); }
  loadRepos();
};

""" + server.PANEL_JS + """
loadRepos();
</script></body></html>
"""

HUB_UI = server.hashlib.sha256(HUB_PAGE.encode("utf-8")).hexdigest()[:7]


def hub_page():
    return (HUB_PAGE.replace("__UI__", HUB_UI)
            .replace("__REPO_UI__", server.UI_VERSION))


# ----------------------------------------------------------------- running it
def status(quiet=False):
    url = "http://127.0.0.1:%d/" % HUB_PORT
    try:
        from urllib.request import urlopen
        with urlopen(url + "api/health", timeout=1.0) as fh:
            ok = json.loads(fh.read().decode("utf-8")).get("app") == "artifact-hub"
    except Exception:                                  # noqa: BLE001
        ok = False
    if not quiet:
        print("[hub] running at %s" % url if ok else "[hub] not running")
    return url if ok else None


def serve(port=HUB_PORT, open_browser=True):
    running = status(quiet=True)
    if running and port == HUB_PORT:
        print("[hub] already running at %s" % running, flush=True)
        if open_browser:
            webbrowser.open(running)
        return 0
    httpd = server._Server(("127.0.0.1", port), HubHandler)
    url = "http://127.0.0.1:%d/" % httpd.server_port
    print("[hub] %s" % url, flush=True)
    for r in launch.onboarded():
        print("   %-18s %s" % (os.path.basename(r), launch.describe(r)))
    print("[hub] serves no files; Ctrl-C to stop", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[hub] stopped")
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="one page over every repo's "
                                             "artifact browser")
    ap.add_argument("--port", type=int, default=HUB_PORT)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    if a.status:
        return 0 if status() else 1
    return serve(a.port, not a.no_open)


if __name__ == "__main__":
    sys.exit(main())
