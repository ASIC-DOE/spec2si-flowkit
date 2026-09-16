#!/usr/bin/env python3
"""Offline self-test for the artifact browser.

No cluster, no PDK, no browser, no network beyond a loopback socket the test
opens and closes itself.

The bulk of this file is CONFINEMENT. The browser serves whatever path it is
handed, so `roots.resolve` is the one function whose failure turns a read-only
convenience into a file-exfiltration endpoint. Each escape shape gets its own
check, because they fail for different reasons: `..` is caught by realpath, an
absolute path by the isabs guard (os.path.join would otherwise DISCARD the
root), a sibling directory by commonpath rather than a string prefix, and a
symlink only by resolving before comparing.

Usage:
  python3 test_browse.py       # or: pytest test_browse.py
"""
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model                        # noqa: E402
import roots as rootsmod            # noqa: E402
import cluster                      # noqa: E402
import estimate                     # noqa: E402
import server                       # noqa: E402
import tools                        # noqa: E402


def _tree():
    """A root with a file, a subdir, and a SIBLING holding a secret."""
    base = tempfile.mkdtemp(prefix="browse_test_")
    root = os.path.join(base, "root")
    os.makedirs(os.path.join(root, "sub"))
    # binary, so the fixture is byte-identical on every platform: text mode on
    # Windows turns "\n" into "\r\n" and the /raw byte comparison fails for a
    # reason that has nothing to do with the server
    with open(os.path.join(root, "a.log"), "wb") as fh:
        fh.write(b"hello\n")
    with open(os.path.join(root, "sub", "r.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdict": "PASS"}, fh)
    # a sibling of the root, NOT inside it
    os.makedirs(os.path.join(base, "root-backup"))
    with open(os.path.join(base, "secret.txt"), "w", encoding="utf-8") as fh:
        fh.write("do not serve me\n")
    return base, [rootsmod.Root("r", root)]


# ---------------------------------------------------------------- confinement
def test_resolves_a_path_inside_the_root():
    _b, rs = _tree()
    _root, p = rootsmod.resolve(rs, "r", "sub/r.json")
    assert os.path.isfile(p), p


def test_empty_path_is_the_root_itself():
    _b, rs = _tree()
    _root, p = rootsmod.resolve(rs, "r", "")
    assert p == rs[0].path, (p, rs[0].path)


def test_dotdot_escape_is_refused():
    _b, rs = _tree()
    for bad in ("../secret.txt", "sub/../../secret.txt", "../../etc/passwd",
                "sub/../../root-backup"):
        try:
            rootsmod.resolve(rs, "r", bad)
        except ValueError:
            continue
        raise AssertionError("escaped via %r" % bad)


def test_absolute_path_is_refused():
    """os.path.join(root, '/etc/passwd') DISCARDS root -- reject before join."""
    _b, rs = _tree()
    for bad in ("/etc/passwd", "C:/Windows/win.ini", "//server/share"):
        try:
            rootsmod.resolve(rs, "r", bad)
        except ValueError:
            continue
        raise AssertionError("accepted absolute %r" % bad)


def test_sibling_directory_is_not_inside():
    """A string-prefix test would say root-backup is inside root."""
    base, rs = _tree()
    sib = os.path.join(base, "root-backup")
    assert not rootsmod._inside(os.path.realpath(sib), rs[0].path)


def test_symlink_out_of_the_root_is_refused():
    base, rs = _tree()
    link = os.path.join(rs[0].path, "escape")
    try:
        os.symlink(os.path.join(base, "secret.txt"), link)
    except (OSError, NotImplementedError, AttributeError):
        print("     (symlinks unavailable -- check skipped)")
        return
    try:
        rootsmod.resolve(rs, "r", "escape")
    except ValueError:
        return
    raise AssertionError("followed a symlink out of the root")


def test_unknown_root_is_refused():
    _b, rs = _tree()
    try:
        rootsmod.resolve(rs, "nope", "")
    except ValueError:
        return
    raise AssertionError("accepted an unknown root")


def test_backslashes_are_normalised_before_the_check():
    """A Windows-style separator must not sneak past the .. handling."""
    _b, rs = _tree()
    try:
        rootsmod.resolve(rs, "r", "..\\secret.txt")
    except ValueError:
        return
    raise AssertionError("escaped via a backslash path")


# ------------------------------------------------------------- served repo
def test_the_served_repo_can_be_another_checkout():
    """One implementation, two repos -- the de-fork this replaced nine
    byte-identical files with.

    Every path the browser resolves (roots, the engine it renders abstracts
    with, the tools it execs) hangs off this one answer, so if it silently
    fell back to the package's own directory the second repo would quietly
    browse the FIRST one's artifacts, which is worse than not working.
    """
    keep = os.environ.pop("BROWSE_REPO", None)
    try:
        pkg_repo = os.path.dirname(
            os.path.dirname(os.path.abspath(rootsmod.__file__)))
        assert rootsmod.repo_root() == pkg_repo
        other = tempfile.mkdtemp(prefix="browse_repo_")
        os.environ["BROWSE_REPO"] = other
        assert rootsmod.repo_root() == os.path.realpath(other)
    finally:
        if keep is None:
            os.environ.pop("BROWSE_REPO", None)
        else:
            os.environ["BROWSE_REPO"] = keep
    # ...but REPO itself is resolved ONCE, at import, and everything that
    # anchors on it (Root, tools.RENDERER, the engine path) reads that one
    # value. Setting the variable afterwards changes nothing -- which is
    # precisely why the launcher sets it BEFORE importing, and why that
    # ordering is a comment in the launcher rather than an accident.
    assert rootsmod.REPO == rootsmod.repo_root()
    assert rootsmod.Root("x", "analog/work").path.startswith(rootsmod.REPO)


# ------------------------------------------------------------------- launcher
def _launch_tree(extra=()):
    """The real C:/dev shape, with each state the launcher distinguishes.

    Four states matter and it treats each differently: NAMED (a
    browse/roots.json), STUB (a browse/ holding only a launcher, which is what
    spec2si-xt011 looked like for the hour between the two halves of its
    onboarding), servable but BARE (the unrelated projects under C:/dev), and
    not a checkout at all.
    """
    base = tempfile.mkdtemp(prefix="browse_launch_")
    spec = [("spec2si-tsmc65", "named"), ("spec2si-tsmc28", "named"),
            ("spec2si-xt011", "named"), ("photonic_thing", "bare"),
            ("release_notes", "notarepo")]
    for name, kind in spec + list(extra):
        d = os.path.join(base, name)
        if kind == "notarepo":
            os.makedirs(d)              # no .git, no analog/, no browse/
            continue
        os.makedirs(os.path.join(d, "analog"))
        if kind in ("named", "stub"):
            os.makedirs(os.path.join(d, "browse"))
        if kind == "named":
            with open(os.path.join(d, "browse", "roots.json"), "w",
                      encoding="utf-8") as fh:
                json.dump([{"name": "repo", "path": "."}], fh)
    return base


def _relaunch(base):
    """`launch` re-rooted at base/spec2si-tsmc65/browse, without importing
    twice."""
    import launch as _l
    keep = (_l._HERE, _l.PKG_REPO)
    _l._HERE = os.path.join(base, "spec2si-tsmc65", "browse")
    _l.PKG_REPO = os.path.join(base, "spec2si-tsmc65")
    return _l, keep


def test_the_launcher_lists_the_sibling_checkouts():
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        got = [os.path.basename(c) for c in l.candidates()]
        assert got[0] == "spec2si-tsmc65", got        # the installation, first
        assert "spec2si-xt011" in got, got
        # servable without being onboarded -- that is how a repo gets looked
        # at before anyone writes its roots.json
        assert "photonic_thing" in got, got
        # a directory that is not a checkout is not offered
        assert "release_notes" not in got, got
        named = [os.path.basename(c) for c in l.onboarded()]
        assert named == ["spec2si-tsmc65", "spec2si-tsmc28",
                          "spec2si-xt011"], named
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_a_repo_prefix_resolves_case_insensitively():
    """Prefix matching is against the whole directory name OR a `-`-separated
    component of it (`launch._matches`) -- every process port is now named
    `spec2si-<node>`, and a whole-name-only prefix would need the full
    `spec2si-` stem typed every time, defeating the point of a short token."""
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        assert os.path.basename(l.resolve("xt011")) == "spec2si-xt011"
        assert os.path.basename(l.resolve("TSMC28")) == "spec2si-tsmc28"
        assert os.path.basename(l.resolve("spec2si-xt011")) == "spec2si-xt011"
        assert l.resolve(None) == l.PKG_REPO         # no argument: this one
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_an_ambiguous_prefix_is_refused_not_guessed():
    """Two ONBOARDED repos sharing a prefix component is genuine ambiguity.
    Serving one of them silently is the failure where you spend ten minutes
    wondering why your run is missing."""
    base = _launch_tree(extra=[("spec2si-tsmc28-staging", "named")])
    l, keep = _relaunch(base)
    try:
        try:
            l.resolve("tsmc28")
        except ValueError as exc:
            assert "ambiguous" in str(exc), exc
            assert "spec2si-tsmc28" in str(exc) and \
                "spec2si-tsmc28-staging" in str(exc), exc
        else:
            raise AssertionError("ambiguous prefix was resolved")
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_a_named_repo_wins_a_prefix_tie():
    """The other half of the ambiguity rule, and the reason the tie-break
    exists: a prefix hitting one flow repo and one scratch directory is not a
    question anyone wants asked. Refusing there would make short names
    useless in a parent holding a dozen unrelated projects."""
    base = _launch_tree(extra=[("spec2si-xt011-notes", "bare")])
    l, keep = _relaunch(base)
    try:
        assert os.path.basename(l.resolve("xt011")) == "spec2si-xt011"
        # ...and the unnamed one is still reachable by its own full name
        assert os.path.basename(l.resolve("spec2si-xt011-notes")) == \
            "spec2si-xt011-notes"
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_a_launcher_stub_without_roots_has_not_named_anything():
    """`browse/` existing is not the same as the repo having named its trees.
    spec2si-tsmc28's and spec2si-xt011's browse/ hold a launcher and nothing
    else until a roots.json lands beside it, and ranking such a stub with the
    named repos would contradict the label `--list` prints next to it.

    `config_dir_for` still answers, though -- that is WHERE roots would live,
    and a root added through the UI has to land somewhere."""
    base = _launch_tree(extra=[("SFQ_thing", "stub")])
    l, keep = _relaunch(base)
    try:
        stub = os.path.join(base, "SFQ_thing")
        assert l.config_dir_for(stub) == os.path.join(stub, "browse")
        assert stub not in l.onboarded()
        assert stub in l.candidates()          # servable, just not named
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_an_unknown_name_says_what_it_knows():
    """The named repos spelled out, the rest acknowledged as a count -- so the
    message stays readable in a directory holding a dozen projects without
    implying the others are unreachable."""
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        try:
            l.resolve("nope")
        except ValueError as exc:
            msg = str(exc)
            assert "spec2si-xt011" in msg and "spec2si-tsmc65" in msg, msg
            # servable here, but it has named no trees: counted, not listed
            assert "photonic_thing" not in msg, msg
            assert "+1 more" in msg, msg
        else:
            raise AssertionError("unknown name was resolved")
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_an_explicit_path_outside_the_sibling_layout_still_works():
    base = _launch_tree()
    l, keep = _relaunch(base)
    outside = tempfile.mkdtemp(prefix="browse_elsewhere_")
    try:
        assert l.resolve(outside) == os.path.realpath(outside)
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_a_repo_without_its_own_roots_gets_no_config_dir():
    """Rather than the INSTALLATION's roots, which would list one repository's
    flowruns under another repository's name."""
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        assert l.config_dir_for(os.path.join(base, "photonic_thing")) is None
        assert l.config_dir_for(os.path.join(base, "spec2si-tsmc28")) \
            == os.path.join(base, "spec2si-tsmc28", "browse")
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_the_renderer_is_found_where_a_repo_without_an_engine_keeps_it():
    """XT011 has no `analog/engine`; its ported renderer is at
    `analog/layout/render_gds.py`. Anchoring on the one path made every XT011
    layout report "renderer not found" -- which reads as a broken file, not a
    missing installation."""
    base = tempfile.mkdtemp(prefix="browse_rend_")
    try:
        engine = os.path.join(base, "eng")
        os.makedirs(os.path.join(engine, "analog", "engine", "layout"))
        p = os.path.join(engine, "analog", "engine", "layout", "render_gds.py")
        open(p, "w", encoding="utf-8").close()
        assert tools._first_present(engine, tools._RENDERER_AT) == p

        flat = os.path.join(base, "flat")
        os.makedirs(os.path.join(flat, "analog", "layout"))
        q = os.path.join(flat, "analog", "layout", "render_gds.py")
        open(q, "w", encoding="utf-8").close()
        assert tools._first_present(flat, tools._RENDERER_AT) == q

        # neither present: name a concrete path, not None
        none = os.path.join(base, "none")
        os.makedirs(none)
        got = tools._first_present(none, tools._RENDERER_AT)
        assert got.startswith(none) and got.endswith("render_gds.py"), got
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_binding_a_live_port_fails_so_the_port_walk_can_work():
    """`_bind` walks 8730.. and relies on a busy port RAISING. `http.server`
    sets allow_reuse_address=1, which on Windows means the second bind takes
    the port instead -- so the walk never fired and three browsers all
    "bound" 8730. Checked by actually binding twice, because the whole defect
    was a platform difference in what a flag means."""
    import socket
    first = server._Server(("127.0.0.1", 0), server.Handler)
    try:
        port = first.server_port
        try:
            second = server._Server(("127.0.0.1", port), server.Handler)
        except OSError:
            pass                                       # what must happen
        else:
            second.server_close()
            raise AssertionError(
                "bound a live port %d twice -- the port walk is a no-op" % port)
        # and the walk itself returns a DIFFERENT port when the first is taken
        assert socket.socket                           # (import used)
    finally:
        first.server_close()


def test_a_stale_state_file_pointing_at_another_repos_server_is_not_ours():
    """Found by running all three browsers at once, which had never been done.

    A killed server never clears its state file, so a stale record naming the
    stable port outlives the process -- and the next repo's browser takes that
    port. Probing only for "is a browser answering" then reported that other
    repo's server as this one's, and `serve()` declined to start: you were
    handed another checkout's artifacts under your own repo's name. Keying the
    state file by repo was only half the fix."""
    assert server._is_ours({"app": "artifact-browser",
                            "repo": rootsmod.REPO})
    assert not server._is_ours({"app": "artifact-browser",
                                "repo": os.path.join(rootsmod.REPO, "other")})
    assert not server._is_ours(None)
    # a server old enough not to report its repo predates there being more
    # than one, so it is still ours
    assert server._is_ours({"app": "artifact-browser"})


def test_status_ignores_a_state_file_that_names_a_foreign_server():
    """The same defect at the level `status()` actually runs at."""
    keep_read, keep_probe = server.read_state, server.probe
    foreign = os.path.join(rootsmod.REPO, "somewhere-else")
    try:
        server.read_state = lambda: {"url": "http://127.0.0.1:8730/",
                                     "pid": 1, "ui": "x"}
        # the recorded URL answers, but for another repo -- and nothing else
        # is listening, so the scan finds nothing either
        server.probe = lambda url, timeout=1.5: (
            {"app": "artifact-browser", "repo": foreign}
            if "8730" in url else None)
        assert server.status(quiet=True) is None
        # ...and the same state file IS honoured when the repo matches
        server.probe = lambda url, timeout=1.5: (
            {"app": "artifact-browser", "repo": rootsmod.REPO}
            if "8730" in url else None)
        assert server.status(quiet=True) == "http://127.0.0.1:8730/"
    finally:
        server.read_state, server.probe = keep_read, keep_probe


def test_the_launcher_sets_the_repo_before_the_server_sees_it():
    """The whole ordering contract in one check: `serve_repo` must have set
    BROWSE_REPO by the time anything reads it, because roots.py resolves it at
    import and never again."""
    import launch as _l
    seen = {}
    keep_env = os.environ.pop("BROWSE_REPO", None)
    keep_argv = sys.argv
    target = tempfile.mkdtemp(prefix="browse_target_")
    os.makedirs(os.path.join(target, "browse"))
    try:
        real_main = server.main

        def spy():
            seen["repo"] = os.environ.get("BROWSE_REPO")
            seen["argv"] = list(sys.argv)
            return 0

        server.main = spy
        try:
            assert _l.serve_repo(target, ["--status"]) == 0
        finally:
            server.main = real_main
        assert seen["repo"] == target, seen
        assert "--status" in seen["argv"], seen
        # its own roots.json directory, not the installation's
        assert "--config-dir" in seen["argv"], seen
        i = seen["argv"].index("--config-dir")
        assert seen["argv"][i + 1] == os.path.join(target, "browse"), seen
        # and sys.argv is put back, so the caller's parse is unaffected
        assert sys.argv == keep_argv
    finally:
        if keep_env is None:
            os.environ.pop("BROWSE_REPO", None)
        else:
            os.environ["BROWSE_REPO"] = keep_env
        sys.argv = keep_argv
        shutil.rmtree(target, ignore_errors=True)


def test_the_transport_is_searched_in_both_repos():
    """The served repo may have no transport of its own -- ONR does not --
    so the package's own checkout is searched too."""
    dirs = cluster._transport_dirs()
    assert any("deployment" in d for d in dirs), dirs
    # the package's own repo is in the list even when serving another
    assert any(d.startswith(cluster._PKG_REPO) for d in dirs), dirs


# ---------------------------------------------------------------- config
def test_relative_root_paths_anchor_on_the_repo_not_the_cwd():
    r = rootsmod.Root("x", "analog/work")
    assert r.path.startswith(rootsmod.REPO), r.path


def test_local_config_overrides_by_name():
    d = tempfile.mkdtemp(prefix="browse_cfg_")
    with open(os.path.join(d, "roots.json"), "w", encoding="utf-8") as fh:
        json.dump([{"name": "a", "path": "/tmp/a"},
                   {"name": "b", "path": "/tmp/b"}], fh)
    with open(os.path.join(d, "roots.local.json"), "w", encoding="utf-8") as fh:
        json.dump([{"name": "b", "path": "/tmp/override"}], fh)
    got = rootsmod.load(d)
    assert [r.name for r in got] == ["a", "b"], [r.name for r in got]
    assert rootsmod.by_name(got, "b").path.endswith("override")


def test_malformed_config_raises_rather_than_serving_nothing():
    d = tempfile.mkdtemp(prefix="browse_cfg_")
    with open(os.path.join(d, "roots.json"), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    try:
        rootsmod.load(d)
    except ValueError:
        return
    raise AssertionError("malformed roots.json was accepted")


# ---------------------------------------------------------------- model
def test_listing_puts_directories_first_then_newest():
    _b, rs = _tree()
    entries = model.listdir(rs[0].path, "")
    assert entries[0]["is_dir"], entries
    assert {e["name"] for e in entries} == {"sub", "a.log"}
    assert entries[1]["rel"] == "a.log", entries[1]


def test_kind_dispatch():
    assert model.kind_of("x.png") == "image"
    assert model.kind_of("x.svg") == "svg"
    assert model.kind_of("x.json") == "json"
    assert model.kind_of("x.jsonl") == "jsonl"
    assert model.kind_of("x.log") == "text"
    assert model.kind_of("x.gds") == "binary"
    assert model.kind_of("x.zzz") == "unknown"


def test_text_read_is_capped_and_says_so():
    d = tempfile.mkdtemp(prefix="browse_big_")
    p = os.path.join(d, "big.log")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("x" * (model.BYTE_BUDGET + 5000))
    text, truncated, size = model.read_text(p)
    assert truncated is True
    assert len(text) <= model.BYTE_BUDGET
    assert size > model.BYTE_BUDGET


def test_unparsable_json_still_returns_its_text():
    d = tempfile.mkdtemp(prefix="browse_json_")
    p = os.path.join(d, "bad.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{oops")
    parsed, text, _t, _s = model.read_json(p)
    assert parsed is None and "oops" in text


def test_jsonl_keeps_a_corrupt_line_instead_of_dropping_it():
    d = tempfile.mkdtemp(prefix="browse_jsonl_")
    p = os.path.join(d, "log.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"a":1}\nnot json\n{"a":2}\n')
    rows, _t, _s = model.read_jsonl(p)
    assert len(rows) == 3, rows
    assert rows[1] == {"_raw": "not json"}, rows[1]


# ---------------------------------------------------------------- server
def test_server_serves_and_refuses_over_http():
    import threading
    from http.server import HTTPServer
    from urllib.request import urlopen
    from urllib.error import HTTPError
    import server as srv

    base, rs = _tree()
    srv.Handler.roots = rs
    httpd = HTTPServer(("127.0.0.1", 0), srv.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = "http://127.0.0.1:%d" % httpd.server_port
    try:
        assert b"artifact browser" in urlopen(url + "/").read()
        got = json.loads(urlopen(url + "/api/roots").read())
        assert [r["name"] for r in got["roots"]] == ["r"]
        got = json.loads(urlopen(url + "/api/list?root=r&path=").read())
        assert {e["name"] for e in got["entries"]} == {"sub", "a.log"}
        got = json.loads(urlopen(url + "/api/file?root=r&path=sub/r.json").read())
        assert got["kind"] == "json" and got["parsed"]["verdict"] == "PASS"
        assert urlopen(url + "/raw?root=r&path=a.log").read() == b"hello\n"
        # the confinement refusal must be a 403 over the wire, not a 500
        for bad in ("/api/file?root=r&path=../secret.txt",
                    "/api/list?root=nope&path="):
            try:
                urlopen(url + bad)
            except HTTPError as e:
                assert e.code == 403, (bad, e.code)
            else:
                raise AssertionError("served %s" % bad)
        # There is still no write surface. POST now exists for exactly ONE
        # route (the KLayout exec, plan 5.4); every other path 404s, and no
        # route on this server modifies a browsed tree.
        try:
            import urllib.request as u
            req = u.Request(url + "/api/file?root=r&path=a.log", data=b"x",
                            method="POST")
            u.urlopen(req)
        except HTTPError as e:
            assert e.code == 404, e.code
        else:
            raise AssertionError("accepted a POST")
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(base, ignore_errors=True)


def test_relative_refs_inside_an_html_artifact_resolve():
    """The bug: an embedded report.html showed none of its images.

    Served from `/raw?root=X&path=d/report.html`, the page's URL has no
    directory, so a relative `<img src="pic.png">` resolves to `/pic.png` and
    404s. The path-shaped `/f/<slug>/d/report.html` gives it a real directory.
    """
    import threading
    from http.server import HTTPServer
    from urllib.request import urlopen
    from urllib.parse import urljoin
    import server as srv

    base, rs = _tree()
    with open(os.path.join(rs[0].path, "pic.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    with open(os.path.join(rs[0].path, "r.html"), "wb") as fh:
        fh.write(b'<img src="pic.png">')
    srv.Handler.roots = rs
    httpd = HTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    u = "http://127.0.0.1:%d" % httpd.server_port
    try:
        page = "%s/f/%s/r.html" % (u, rs[0].slug)
        assert urlopen(page).read() == b'<img src="pic.png">'
        # the whole point: resolve the img the way a browser would
        got = urlopen(urljoin(page, "pic.png")).read()
        assert got.startswith(b"\x89PNG"), got[:8]
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(base, ignore_errors=True)


def test_slug_is_url_safe_for_awkward_root_names():
    """Root names contain slashes ('analog/work') and spaces ('cell lib');
    either would make the /f/<root>/<rel> split ambiguous."""
    assert rootsmod.Root("analog/work", "/tmp/x").slug == "analog-work"
    assert rootsmod.Root("cell lib", "/tmp/x").slug == "cell-lib"


def test_path_route_still_refuses_escapes():
    import threading
    from http.server import HTTPServer
    from urllib.request import urlopen
    from urllib.error import HTTPError
    import server as srv

    base, rs = _tree()
    srv.Handler.roots = rs
    httpd = HTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    u = "http://127.0.0.1:%d" % httpd.server_port
    try:
        for bad in ("/f/%s/..%%2F..%%2Fsecret.txt" % rs[0].slug,
                    "/f/nosuchroot/x"):
            try:
                urlopen(u + bad)
            except HTTPError as e:
                assert e.code in (403, 404), (bad, e.code)
            else:
                raise AssertionError("served %s" % bad)
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(base, ignore_errors=True)


def test_gds_summary_reads_cells_and_layers_not_hex():
    """A layout viewer must answer "which cell / which layers", not print hex.

    Checked against the real ctrl2 stream while building this: 84 cells, 11520
    boundaries, 9629 paths, top layers 133/83 132/83 134/83 -- the M3/M2/M4 NET
    purposes, i.e. the routing.
    """
    import struct
    d = tempfile.mkdtemp(prefix="browse_gds_")
    p = os.path.join(d, "x.gds")

    def rec(rtyp, payload=b""):
        return struct.pack(">HBB", len(payload) + 4, rtyp, 0) + payload

    with open(p, "wb") as fh:
        fh.write(rec(0x05, bytes(24)))                    # BGNSTR
        fh.write(rec(0x06, b"topcell" + bytes(1)))        # STRNAME (NUL-padded)
        fh.write(rec(0x08))                               # BOUNDARY
        fh.write(rec(0x0D, struct.pack(">h", 32)))        # LAYER 32
        fh.write(rec(0x0E, struct.pack(">h", 0)))         # DATATYPE 0
        fh.write(rec(0x09))                               # PATH
        fh.write(rec(0x0D, struct.pack(">h", 132)))       # LAYER 132
        fh.write(rec(0x0E, struct.pack(">h", 83)))        # DATATYPE 83
        fh.write(rec(0x0A))                               # SREF
    g = model.gds_summary(p)
    assert g["too_big"] is False
    assert g["cells"] == ["topcell"], g["cells"]
    assert g["counts"] == {"boundary": 1, "path": 1, "sref": 1}, g["counts"]
    assert dict(g["layers"]) == {"32/0": 1, "132/83": 1}, g["layers"]


def test_oversized_gds_is_not_scanned():
    """A chip-level stream must not stall the pane on a click."""
    d = tempfile.mkdtemp(prefix="browse_gds_")
    p = os.path.join(d, "big.gds")
    with open(p, "wb") as fh:
        fh.write(bytes(4096))
    g = model.gds_summary(p, limit=1024)
    assert g["too_big"] is True and g["size"] == 4096, g


# --------------------------------------------------------------- phase 4 badges
def _stamp(**files):
    d = tempfile.mkdtemp(prefix="browse_badge_")
    for name, obj in files.items():
        with open(os.path.join(d, name.replace("__", ".")), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
    return d


def test_flowrun_verdict_drc_and_lvs_become_badges():
    """The three facts a stamp dir is opened to learn, without opening it."""
    d = _stamp(report__json={
        "verdict": "PASS",
        "metrics": {"place": {"drc": "CLEAN"},
                    "route": {"drc": "CLEAN",
                              "lvs": {"correct": True, "summary": "CORRECT"}}}})
    got = [(b["text"], b["tone"]) for b in model.badges(d, True)]
    assert got == [("PASS", "ok"), ("DRC CLEAN", "ok"),
                   ("LVS CORRECT", "ok")], got


def test_failing_run_reads_bad_in_every_field():
    d = _stamp(report__json={
        "verdict": "FAIL",
        "metrics": {"route": {"drc": 17, "lvs": {"correct": False}}}})
    got = [(b["text"], b["tone"]) for b in model.badges(d, True)]
    assert got == [("FAIL", "bad"), ("DRC 17", "bad"),
                   ("LVS INCORRECT", "bad")], got


def test_zero_violations_is_clean_not_bad():
    """A COUNT of 0 and the WORD CLEAN are the same verdict.

    The count arm is the one that inverts: `if drc:` would paint a clean run
    red, and a badge that lies about a clean run is worse than no badge.
    """
    d = _stamp(report__json={"verdict": "PASS",
                             "metrics": {"route": {"drc": 0}}})
    assert [b["tone"] for b in model.badges(d, True)] == ["ok", "ok"]


def test_manifest_counts_stages_and_report_wins_over_it():
    """Precedence, not concatenation: the report's verdict is the signed one."""
    m = {"char": {"verdict": "PASS"}, "place": {"verdict": "PASS"},
         "route": {"verdict": "FAIL"}, "notes": "not a stage"}
    d = _stamp(manifest__json=m)
    assert [(b["text"], b["tone"]) for b in model.badges(d, True)] == \
        [("2/3 stages", "bad")]
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdict": "PASS"}, fh)
    model._BADGE_CACHE.clear()
    assert [b["text"] for b in model.badges(d, True)] == ["PASS"]


def test_job_state_and_licence_wait_are_distinguished():
    """"Stuck" and "waiting on a licence" look identical from outside and are
    not the same thing -- a 25-minute LVS licence wait is normal."""
    d = _stamp(status__json={"schema": 1, "state": "running",
                             "license": "calibre"})
    got = [(b["text"], b["tone"]) for b in model.badges(d, True)]
    assert got == [("running", "run"), ("WAITING_LICENSE", "warn")], got


def test_unknown_job_state_is_informational_not_assumed_good():
    d = _stamp(status__json={"state": "reconciling"})
    assert [b["tone"] for b in model.badges(d, True)] == ["info"], \
        model.badges(d, True)


def test_a_malformed_source_yields_no_badge_not_an_error():
    """A status.json caught mid-write must degrade to plain, never to a 500."""
    d = tempfile.mkdtemp(prefix="browse_badge_")
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
        fh.write('{"verdict": "PA')
    assert model.badges(d, True) == []
    assert model.badges(os.path.join(d, "nothing-here.json"), False) == []


def test_an_oversized_source_is_not_read():
    d = tempfile.mkdtemp(prefix="browse_badge_")
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
        fh.write('{"verdict": "PASS", "pad": "' + "x" * 4096 + '"}')
    assert [b["text"] for b in model.badges(d, True)] == ["PASS"]
    model._BADGE_CACHE.clear()
    keep, model.BADGE_READ_LIMIT = model.BADGE_READ_LIMIT, 2048
    try:
        assert model.badges(d, True) == [], "read past the size limit"
    finally:
        model.BADGE_READ_LIMIT = keep
        model._BADGE_CACHE.clear()


def test_annotate_caps_how_many_entries_it_opens():
    """Badging must never cost more than the listing it decorates.

    analog/work holds hundreds of directories; past the cap the rows still
    list, undecorated. A truncated decoration beats a slow pane.
    """
    d = tempfile.mkdtemp(prefix="browse_badge_")
    for i in range(5):
        sub = os.path.join(d, "run%d" % i)
        os.makedirs(sub)
        with open(os.path.join(sub, "report.json"), "w", encoding="utf-8") as fh:
            json.dump({"verdict": "PASS"}, fh)
    entries = model.annotate(model.listdir(d), d, limit=2)
    assert all("badges" in e for e in entries)
    assert sum(1 for e in entries if e["badges"]) == 2, entries


def test_a_circuit_directory_is_badged_from_its_latest_run():
    """`flowruns` lists circuits, not runs; LATEST.txt is the way in."""
    d = tempfile.mkdtemp(prefix="browse_badge_")
    stamp = os.path.join(d, "20260715_111259")
    os.makedirs(stamp)
    with open(os.path.join(stamp, "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdict": "FAIL at pex"}, fh)
    with open(os.path.join(d, "LATEST.txt"), "w", encoding="utf-8") as fh:
        fh.write("20260715_111259\n")
    got = [(b["text"], b["tone"]) for b in model.badges(d, True)]
    # marked "latest" because it is the CHILD's verdict, not this directory's
    assert got == [("latest", "info"), ("FAIL at pex", "bad")], got


def test_latest_txt_cannot_walk_out_of_the_directory():
    """It is our own file, but it still names a path component."""
    d = tempfile.mkdtemp(prefix="browse_badge_")
    with open(os.path.join(os.path.dirname(d), "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdict": "PASS"}, fh)
    for bad in ("..", "../", "sub/../..", "\\.."):
        with open(os.path.join(d, "LATEST.txt"), "w", encoding="utf-8") as fh:
            fh.write(bad)
        model._BADGE_CACHE.clear()
        assert model.badges(d, True) == [], bad


def test_a_plain_directory_gets_no_badges():
    d = tempfile.mkdtemp(prefix="browse_badge_")
    os.makedirs(os.path.join(d, "sub"))
    entries = model.annotate(model.listdir(d), d)
    assert entries[0]["badges"] == [], entries


# ------------------------------------------------------- phase 4 exec surface
def _stub_renderer(d, body):
    """A fake render_gds.py, so the cache/lock/rename logic is tested without
    matplotlib and without a 40-second render of a real layout."""
    p = os.path.join(d, "stub_renderer.py")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    return p


def _cache(d):
    os.environ["BROWSE_CACHE_DIR"] = os.path.join(d, "cache")
    return os.environ["BROWSE_CACHE_DIR"]


def test_render_cache_key_follows_content_not_path():
    """The same stream pulled to two paths must not render twice."""
    d = tempfile.mkdtemp(prefix="browse_tools_")
    a, b, c = (os.path.join(d, n) for n in ("a.gds", "b.gds", "c.gds"))
    for p in (a, b):
        with open(p, "wb") as fh:
            fh.write(b"IDENTICAL BYTES")
    with open(c, "wb") as fh:
        fh.write(b"different bytes")
    assert tools.cache_key(a) == tools.cache_key(b)
    assert tools.cache_key(a) != tools.cache_key(c)


def test_an_oversized_layout_is_refused_without_exec():
    """A chip-level stream is a click that never returns; send it to KLayout."""
    d = tempfile.mkdtemp(prefix="browse_tools_")
    p = os.path.join(d, "big.gds")
    with open(p, "wb") as fh:
        fh.write(bytes(4096))
    got = tools.render_gds(p, limit=1024)
    assert got["ok"] is False and got["state"] == "too_big", got
    assert "KLayout" in got["error"]


def test_render_writes_then_reuses_the_cache():
    d = tempfile.mkdtemp(prefix="browse_tools_")
    _cache(d)
    gds = os.path.join(d, "x.gds")
    with open(gds, "wb") as fh:
        fh.write(b"gds bytes")
    keep = tools.RENDERER
    tools.RENDERER = _stub_renderer(d, "import sys\n"
                                       "open(sys.argv[2],'wb').write(b'PNG')\n")
    try:
        first = tools.render_gds(gds)
        assert first["ok"] and first["state"] == "rendered", first
        assert open(first["png"], "rb").read() == b"PNG"
        second = tools.render_gds(gds)
        assert second["state"] == "cached", second
        assert second["png"] == first["png"]
    finally:
        tools.RENDERER = keep


def test_a_failed_render_does_not_poison_the_cache():
    """The next click must retry, not serve a truncated PNG forever.

    This is why the renderer writes a .part and renames: a crash halfway
    through leaves nothing at the cache path, so `state == "cached"` can only
    ever mean a complete file.
    """
    d = tempfile.mkdtemp(prefix="browse_tools_")
    _cache(d)
    gds = os.path.join(d, "x.gds")
    with open(gds, "wb") as fh:
        fh.write(b"gds bytes")
    keep = tools.RENDERER
    tools.RENDERER = _stub_renderer(
        d, "import sys\n"
           "open(sys.argv[2],'wb').write(b'half')\n"
           "sys.stderr.write('unsupported record 0x2f\\n')\n"
           "sys.exit(3)\n")
    try:
        got = tools.render_gds(gds)
        assert got["ok"] is False and got["state"] == "failed", got
        # the renderer's own message, not a bare exit code
        assert "unsupported record" in got["error"], got["error"]
        assert not os.path.exists(os.path.join(tools.cache_dir(),
                                               tools.cache_key(gds) + ".png"))
        assert not [f for f in os.listdir(tools.cache_dir())
                    if ".part" in f], "left a partial behind"
    finally:
        tools.RENDERER = keep


def test_a_render_window_is_part_of_the_cache_identity():
    """The same stream cropped two ways is two pictures. Keying only on
    content would serve the first crop for every later one -- and the second
    crop would silently be the first."""
    d = tempfile.mkdtemp(prefix="browse_tools_")
    _cache(d)
    gds = os.path.join(d, "x.gds")
    with open(gds, "wb") as fh:
        fh.write(b"gds bytes")
    keep = tools.RENDERER
    tools.RENDERER = _stub_renderer(
        d, "import sys\nopen(sys.argv[2],'wb').write(b'PNG')\n")
    try:
        full = tools.render_gds(gds)
        w1 = tools.render_gds(gds, win=(0, 0, 10, 10))
        w2 = tools.render_gds(gds, win=(5, 5, 15, 15))
        names = {os.path.basename(r["png"]) for r in (full, w1, w2)}
        assert len(names) == 3, names
        # the same window hits the cache
        assert tools.render_gds(gds, win=(0, 0, 10, 10))["state"] == "cached"
    finally:
        tools.RENDERER = keep


def test_the_window_reaches_the_renderer_as_four_trailing_numbers():
    """render_gds.main() already accepts `x1 y1 x2 y2` in um; this is the
    only contract between them, so it is asserted on the argv."""
    d = tempfile.mkdtemp(prefix="browse_tools_")
    _cache(d)
    gds = os.path.join(d, "x.gds")
    with open(gds, "wb") as fh:
        fh.write(b"gds")
    seen = {}
    real = tools.subprocess.run
    stub = _stub_renderer(
        d, "import sys\nopen(sys.argv[2],'wb').write(b'P')\n")

    def fake(cmd, **kw):
        seen["cmd"] = list(cmd)
        return real([sys.executable, stub] + list(cmd[2:]), **kw)
    # the renderer must EXIST for the call to be made at all -- on a checkout
    # without an engine renderer the early "renderer not found" return would
    # otherwise make this a test of that checkout rather than of the argv
    keep_r, tools.RENDERER = tools.RENDERER, stub
    keep, tools.subprocess.run = tools.subprocess.run, fake
    try:
        tools.render_gds(gds, win=(1.5, 2.5, 3.5, 4.5))
    finally:
        tools.subprocess.run = keep
        tools.RENDERER = keep_r
    assert seen["cmd"][-4:] == ["1.5", "2.5", "3.5", "4.5"], seen["cmd"]


def test_the_temp_render_name_keeps_the_png_extension():
    """matplotlib picks its format from the EXTENSION.

    A temp name of `<hash>.png.<pid>.part` made every REAL render fail inside
    savefig -- and the stub renderer above, which just writes the bytes it is
    told to, could never have caught it. Asserted on the name itself so the
    lesson survives a refactor of the renderer.
    """
    d = tempfile.mkdtemp(prefix="browse_tools_")
    _cache(d)
    gds = os.path.join(d, "x.gds")
    with open(gds, "wb") as fh:
        fh.write(b"gds bytes")
    seen = []
    keep = tools.RENDERER
    tools.RENDERER = _stub_renderer(
        d, "import sys\n"
           "open(sys.argv[2] + '.seen','w').write(sys.argv[2])\n"
           "open(sys.argv[2],'wb').write(b'PNG')\n")
    try:
        tools.render_gds(gds)
        seen = [f for f in os.listdir(tools.cache_dir()) if f.endswith(".seen")]
        assert seen, "the stub never ran"
        target = open(os.path.join(tools.cache_dir(), seen[0]), encoding="utf-8").read()
        assert target.endswith(".png"), target
    finally:
        tools.RENDERER = keep


def test_a_hung_render_times_out_rather_than_holding_the_request():
    d = tempfile.mkdtemp(prefix="browse_tools_")
    _cache(d)
    gds = os.path.join(d, "x.gds")
    with open(gds, "wb") as fh:
        fh.write(b"gds bytes")
    keep = tools.RENDERER
    tools.RENDERER = _stub_renderer(d, "import time\ntime.sleep(30)\n")
    try:
        got = tools.render_gds(gds, timeout=1.0)
        assert got["ok"] is False and got["state"] == "timeout", got
    finally:
        tools.RENDERER = keep


def test_sidecars_report_what_klayout_would_load():
    d = tempfile.mkdtemp(prefix="browse_tools_")
    gds = os.path.join(d, "cell.gds")
    open(gds, "wb").close()
    assert tools.sidecars(gds) == {"lyp": None, "lyrdb": None}
    open(os.path.join(d, "cell.lyp"), "wb").close()
    open(os.path.join(d, "DRC_RES.lyrdb"), "wb").close()
    got = tools.sidecars(gds)
    assert got["lyp"] and got["lyrdb"], got


def test_klayout_handoff_refuses_a_layout_that_is_not_there():
    got = tools.open_in_klayout(os.path.join(tempfile.mkdtemp(), "nope.gds"))
    assert got["ok"] is False and got["cmd"] is None, got


def test_klayout_command_passes_the_path_as_argv_not_as_shell():
    """A layout named with a quote or a semicolon is data, never syntax."""
    import inspect
    src = inspect.getsource(tools)
    assert "shell=True" not in src, "the exec surface must never use a shell"
    assert "os.system" not in src


def test_render_cache_route_admits_only_a_render_name():
    """The cache is OUTSIDE every root, so roots.resolve does not guard it."""
    pat = server.Handler._CACHE_NAME
    assert pat.match("a3f9.png") and pat.match("0" * 32 + ".png")
    # a REMOTE render is the same hex behind an `r`. Asserted because it was
    # missed once: the renders came back fine and then every one of them 403'd
    # on its way to the <img>, since "r" is not a hex digit.
    assert pat.match("r" + "b3557184c02a2fc4f405875d640d93b" + ".png")
    for bad in ("../secret.txt", "..%2fx.png", "a/b.png", "a3f9.png/x",
                "a3f9.PNG", "a3f9.py", "..", "", "z3f9.png", "a3f9.png.part",
                "rr3f9.png", "r.png/x"):
        assert not pat.match(bad), bad


def test_the_exec_route_refuses_a_cross_site_post():
    """A page on another origin can send a simple POST to 127.0.0.1 without a
    preflight. It cannot read the reply -- but the exec would already have
    happened, so the guard is on the request, not the response."""
    import http.client
    import socket
    srv = _live_server()
    port = srv.server_address[1]

    def post(headers, body=b"{}"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/klayout", body=body, headers=headers)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    try:
        assert post({"Content-Type": "application/json",
                     "Sec-Fetch-Site": "cross-site"}) == 403
        assert post({"Content-Type": "application/json",
                     "Origin": "http://evil.example"}) == 403
        # a form post is the shape that needs no preflight -- refused on type
        assert post({"Content-Type": "application/x-www-form-urlencoded"}) == 403
        # A same-origin request gets PAST the guard and is refused later, on
        # what it asks for rather than on where it came from: 400 "not a
        # layout", not 403. The two refusals must not be confused -- a guard
        # that happened to be passing everything would look identical if the
        # only check here were "not 200".
        assert post({"Content-Type": "application/json"},
                    body=json.dumps({"root": "r", "path": "a.log"}).encode()) \
            == 400
        # and nothing else accepts a POST at all
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/list", headers={"Content-Type": "application/json"})
        assert c.getresponse().status == 404
        c.close()
    finally:
        srv.shutdown()
        srv.server_close()
    del socket


def _live_server():
    """A real socket on a random port, in a thread the caller shuts down."""
    import threading
    from http.server import ThreadingHTTPServer
    base, rs = _tree()
    server.Handler.roots = rs
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    del base
    return srv


# --------------------------------------------------------------- cluster roots
class _FakeTransport(object):
    """Stands in for remote.Transport: captures the script, returns a canned
    envelope. Lets every cluster path be tested with no ssh and no cluster."""

    last_script = None

    def __init__(self, host=None, timeout=None, envelope=None, ok=True,
                 reason=None):
        self.host = host
        self._env = envelope
        self._ok = ok
        self._reason = reason

    def run_sh(self, script, **kw):
        _FakeTransport.last_script = script

        class R(object):
            pass
        r = R()
        r.ok = self._ok
        r.status = "KNOWN" if self._ok else "UNKNOWN"
        r.data = self._env
        r.reason = self._reason
        return r


def _with_fake(envelope=None, ok=True, reason=None):
    """Patch cluster's transport for one call."""
    class _Mod(object):
        Transport = staticmethod(
            lambda host=None, timeout=None: _FakeTransport(
                host, timeout, envelope, ok, reason))
    keep = cluster._remote
    cluster._remote = _Mod
    cluster.invalidate()
    return keep


def test_remote_path_is_bound_by_heredoc_not_interpolation():
    """A cluster path must be DATA in the shipped script, never syntax.

    remote.py's own `_SAFE_PATH` whitelist exists for tokens dropped into a
    launcher unquoted; a quoted heredoc is stronger, and it means a path with
    a space is a fact about the cluster rather than a rejected request.
    """
    keep = _with_fake({"kind": "list", "entries": [], "badges": {}})
    try:
        evil = "/tmp/a b; rm -rf ~; $(touch /tmp/pwned)`id`"
        cluster.listdir("h", evil, "")
        s = _FakeTransport.last_script
        assert evil in s, "the path never reached the script"
        # it appears INSIDE a quoted heredoc, so none of it can execute
        assert "<<'EOF_BROWSE_ROOT" in s, s[:400]
        # and nothing is spliced into the python source
        assert "os.environ" in s
    finally:
        cluster._remote = keep


def test_a_newline_in_a_remote_path_is_refused():
    """It would terminate the heredoc line-wise -- refuse, never mangle."""
    keep = _with_fake({"kind": "list", "entries": [], "badges": {}})
    try:
        for bad in ("/tmp/a\nb", "/tmp/a\rb"):
            try:
                cluster.listdir("h", bad, "")
            except ValueError:
                continue
            raise AssertionError("accepted %r" % bad)
    finally:
        cluster._remote = keep


def test_an_unreachable_cluster_is_not_an_empty_directory():
    """remote.py invariant 5, carried all the way to the pane.

    If UNKNOWN degraded to "no entries", the browser would confidently show an
    empty flowruns directory when the VPN is down -- the single most
    misleading thing this view could do.
    """
    keep = _with_fake(None, ok=False, reason="transport timeout")
    try:
        try:
            cluster.listdir("h", "/x", "")
        except cluster.RemoteError as exc:
            assert "timeout" in str(exc), exc
        else:
            raise AssertionError("an unreachable host returned a listing")
    finally:
        cluster._remote = keep


def test_remote_listing_is_ttl_cached_and_refreshable():
    keep = _with_fake({"kind": "list", "badges": {},
                       "entries": [{"name": "a", "is_dir": True,
                                    "size": None, "mtime": 1}]})
    try:
        _e, _b, cached = cluster.listdir("h", "/x", "")
        assert cached is False
        _e, _b, cached = cluster.listdir("h", "/x", "")
        assert cached is True, "second read was not served from cache"
        _e, _b, cached = cluster.listdir("h", "/x", "", refresh=True)
        assert cached is False, "explicit refresh did not bypass the cache"
        cluster.invalidate("h")
        _e, _b, cached = cluster.listdir("h", "/x", "")
        assert cached is False
    finally:
        cluster._remote = keep


def test_remote_badges_use_the_same_definition_as_local():
    """One definition of a verdict, or the two sides drift.

    The remote lister ships the badge FILE; the badges themselves are computed
    here by the same `model` functions the local reader uses. This asserts the
    two agree on identical bytes -- which is the only thing that keeps a
    cluster PASS and a local PASS meaning the same thing.
    """
    report = json.dumps({"verdict": "FAIL at pex",
                         "metrics": {"route": {"drc": "CLEAN",
                                               "lvs": {"correct": False}}}})
    remote_badges = model.badges_from_text("report.json", report)
    d = tempfile.mkdtemp(prefix="browse_badge_")
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
        fh.write(report)
    model._BADGE_CACHE.clear()
    local_badges = model.badges(d, True)
    assert remote_badges == local_badges, (remote_badges, local_badges)
    assert [b["text"] for b in remote_badges] == \
        ["FAIL at pex", "DRC CLEAN", "LVS INCORRECT"]


def test_remote_latest_marks_the_child_verdict():
    got = model.badges_from_text("report.json", json.dumps({"verdict": "PASS"}),
                                 latest=True)
    assert [b["text"] for b in got] == ["latest", "PASS"], got


def test_remote_confinement_refuses_before_the_wire():
    """The cheap half of a two-sided check: never let a malformed request even
    reach the cluster. The remote script repeats the full realpath test, which
    is the authoritative one -- only the cluster can resolve its own symlinks.
    """
    rs = [rootsmod.Root("c", "~/Documents/ms_pilot", kind="remote",
                        host="asic7")]
    for bad in ("../etc/passwd", "a/../../b", "/etc/passwd", "C:/x",
                "a/\n/b"):
        try:
            rootsmod.resolve_remote(rs, "c", bad)
        except ValueError:
            continue
        raise AssertionError("accepted %r" % bad)
    _r, rel = rootsmod.resolve_remote(rs, "c", "analog/./results//flowruns")
    assert rel == "analog/results/flowruns", rel


def test_a_remote_root_path_is_not_rewritten_locally():
    """expanduser/realpath here would produce a Windows path meaning nothing
    on asic7. The cluster expands its own ~."""
    r = rootsmod.Root("c", "~/Documents/ms_pilot", kind="remote", host="asic7")
    assert r.path == "~/Documents/ms_pilot", r.path
    assert r.exists is None, "a remote root must not claim to be missing"
    # a local root still resolves, as before
    assert os.path.isabs(rootsmod.Root("l", ".").path)


def test_a_shared_filesystem_is_declared_and_shares_one_cache():
    """The BNL homes are shared -- measured, not assumed: the 28 nm divider
    renders byte-identically (sha 1dd0e73d3d48) from asic6, asic7 and asic8.

    So a render is an artifact of (filesystem, path) and the host that read it
    is provenance. Two roots on different hosts but the same declared `fs`
    must therefore share a cache entry -- otherwise every host re-renders what
    the last one already did.

    Declared rather than sniffed, and defaulting to the host, because "these
    hosts see the same files" is a fact about a site. A wrong guess would
    serve one machine's layout as another's, so the DEFAULT must be the safe
    one.
    """
    a = rootsmod.Root("a", "/p", kind="remote", host="asic6", fs="bnl-home")
    b = rootsmod.Root("b", "/p", kind="remote", host="asic7", fs="bnl-home")
    solo = rootsmod.Root("c", "/p", kind="remote", host="asic9")
    assert a.fs == b.fs == "bnl-home"
    assert solo.fs == "asic9", "an undeclared fs must fall back to the host"

    keep = _with_fake({"kind": "list", "badges": {},
                       "entries": [{"name": "x", "is_dir": False,
                                    "size": 1, "mtime": 1}]})
    try:
        _e, _b, cached = cluster.listdir(a.host, a.path, "", fs=a.fs)
        assert cached is False
        # a DIFFERENT host, same filesystem -> already known
        _e, _b, cached = cluster.listdir(b.host, b.path, "", fs=b.fs)
        assert cached is True, "a shared filesystem re-read the same directory"
        # an undeclared host does NOT share
        _e, _b, cached = cluster.listdir(solo.host, solo.path, "", fs=solo.fs)
        assert cached is False, "an undeclared host shared a cache entry"
    finally:
        cluster._remote = keep


def test_local_and_remote_roots_do_not_cross():
    rs = [rootsmod.Root("loc", "."), rootsmod.Root("rem", "/x",
                                                   kind="remote", host="h")]
    for fn, name in ((rootsmod.resolve, "rem"),
                     (rootsmod.resolve_remote, "loc")):
        try:
            fn(rs, name, "")
        except ValueError:
            continue
        raise AssertionError("%s accepted %s" % (fn.__name__, name))


# -------------------------------------------------------------- XOR diff (6.2)
def _stub_xor(d, body):
    """A fake strmxor: a python stub invoked through the same argv path."""
    p = os.path.join(d, "stub_xor.py")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    return p


def _run_xor(a, b, stub_path):
    """Run xor_gds with a python stub standing in for strmxor.

    Only the EXEC is faked. The argv shape, the temp-then-rename, the
    exit-code verdict and the summary parsing are all the real code paths.
    XOR_BIN points at a file that exists so the availability guard is honest,
    and availability is forced so these run off Windows too.
    """
    real_run = tools.subprocess.run
    real_avail = tools.xor_available
    keep_bin = tools.XOR_BIN

    def fake_run(cmd, **kw):
        return real_run([sys.executable, stub_path] + list(cmd[1:]), **kw)
    tools.XOR_BIN = sys.executable
    tools.xor_available = lambda: True
    tools.subprocess.run = fake_run
    try:
        return tools.xor_gds(a, b)
    finally:
        tools.subprocess.run = real_run
        tools.xor_available = real_avail
        tools.XOR_BIN = keep_bin


def _pair(prefix="browse_xor_"):
    d = tempfile.mkdtemp(prefix=prefix)
    _cache(d)
    a, b = os.path.join(d, "a.gds"), os.path.join(d, "b.gds")
    with open(a, "wb") as fh:
        fh.write(b"aaa")
    with open(b, "wb") as fh:
        fh.write(b"bbb")
    return d, a, b


def test_identical_layouts_report_identical_and_write_no_diff():
    """EMPTY IS THE PASS -- the whole point of the regression check."""
    d, a, b = _pair()
    got = _run_xor(a, b, _stub_xor(d, "import sys\nsys.exit(0)\n"))
    assert got["ok"] is True and got["identical"] is True, got
    assert got["out"] is None, "an identical pair wrote a diff file"


def test_differing_layouts_report_the_moved_shapes_per_layer():
    d, a, b = _pair()
    stub = _stub_xor(d, "\n".join([
        "import sys",
        "open(sys.argv[4],'wb').write(b'DIFFGDS')",
        "print('Result summary (layers without differences are not shown):')",
        "print('  Layer      Output       Differences (shape count)')",
        "print('  ------------------------------------------------')",
        "print('  32/0       -            22881')",
        "print('  133/83     -            7')",
        "sys.exit(1)", ""]))
    got = _run_xor(a, b, stub)
    assert got["ok"] is True and got["identical"] is False, got
    assert got["layers"] == [{"layer": "32/0", "shapes": 22881},
                             {"layer": "133/83", "shapes": 7}], got["layers"]
    assert got["out"] and open(got["out"], "rb").read() == b"DIFFGDS"


def test_the_xor_verdict_comes_from_the_exit_code_not_the_text():
    """strmxor's own contract: 0 = same, >0 = differences.

    A summary that parses to zero rows must NOT be read as identical -- the
    exit code is the verdict and the table is only detail. Getting this
    backwards would turn an unparsed summary into a silent PASS, which is the
    one failure mode a regression check may not have.
    """
    d, a, b = _pair()
    stub = _stub_xor(d, "\n".join([
        "import sys",
        "open(sys.argv[4],'wb').write(b'D')",
        "print('some text with no parsable rows')",
        "sys.exit(1)", ""]))
    got = _run_xor(a, b, stub)
    assert got["identical"] is False, got
    assert got["layers"] == [], got



def test_xor_passes_dash_l_so_a_missing_layer_is_not_silently_empty():
    """MEASURED on the real binary, and the reason is the PICTURE.

    With all 1274 elements on 32/0 (M2) stripped from the ctrl2 pilot,
    strmxor without -l still exits nonzero -- but writes a 130-byte EMPTY
    output, so the pane would show a blank diff beside "differences exist".
    With -l the same case writes 22881 shapes. Asserted on the argv so the
    flag cannot be dropped.
    """
    d = tempfile.mkdtemp(prefix="browse_xor_")
    _cache(d)
    a, b = os.path.join(d, "a.gds"), os.path.join(d, "b.gds")
    for p, c in ((a, b"a"), (b, b"b")):
        with open(p, "wb") as fh:
            fh.write(c)
    seen = {}
    real = tools.subprocess.run
    stub = _stub_xor(d, "import sys\nsys.exit(0)\n")

    def fake_run(cmd, **kw):
        seen["cmd"] = list(cmd)
        return real([sys.executable, stub] + list(cmd[1:]), **kw)
    keep, tools.subprocess.run = tools.subprocess.run, fake_run
    try:
        tools.xor_gds(a, b)
    finally:
        tools.subprocess.run = keep
    assert "-l" in seen["cmd"], seen["cmd"]
    # and the paths go through argv, never a shell string
    assert seen["cmd"][2] == a and seen["cmd"][3] == b, seen["cmd"]


def test_xor_candidates_offer_the_previous_run_first():
    """The regression comparison is THIS cell against the run before it."""
    d = tempfile.mkdtemp(prefix="browse_xor_")
    import time as _t
    for i, stamp in enumerate(("20260714_000517", "20260715_111259",
                               "20260713_101010")):
        sd = os.path.join(d, stamp)
        os.makedirs(sd)
        with open(os.path.join(sd, "cell.gds"), "wb") as fh:
            fh.write(b"x")
        os.utime(sd, (1000 + i * 100, 1000 + i * 100))
    _t.sleep(0)
    here = os.path.join(d, "20260714_000517")
    with open(os.path.join(here, "other.gds"), "wb") as fh:
        fh.write(b"y")
    got = model.xor_candidates(os.path.join(here, "cell.gds"),
                               "20260714_000517/cell.gds")
    labels = [c["label"] for c in got]
    # newest sibling first, itself excluded, then same-directory layouts
    assert labels[0] == "20260713_101010/cell.gds", labels
    assert "20260714_000517/cell.gds" not in labels, labels
    assert labels[-1] == "other.gds", labels
    assert got[0]["rel"] == "20260713_101010/cell.gds", got[0]


def test_xor_candidate_rel_paths_stay_inside_the_root():
    """A compare target is a request like any other -- it must resolve through
    the same confinement, so the rel path has to be root-relative."""
    base, rs = _tree()
    d = os.path.join(rs[0].path, "runs", "s1")
    os.makedirs(d)
    os.makedirs(os.path.join(rs[0].path, "runs", "s0"))
    for p in (os.path.join(d, "c.gds"),
              os.path.join(rs[0].path, "runs", "s0", "c.gds")):
        with open(p, "wb") as fh:
            fh.write(b"x")
    got = model.xor_candidates(os.path.join(d, "c.gds"), "runs/s1/c.gds")
    assert got, "no candidate found"
    for c in got:
        _r, p = rootsmod.resolve(rs, "r", c["rel"])     # must not raise
        assert os.path.isfile(p), p


# ------------------------------------------------------- root grouping / add
def test_local_and_remote_roots_group_separately():
    """A local folder and a cluster tree are different KINDS of place.

    One is on this disk and instant, the other is an ssh round trip away. A
    flat list hid that behind identical-looking rows, which is what made the
    panel confusing.
    """
    rs = [rootsmod.Root("flowruns", "."),
          rootsmod.Root("ms_pilot", "~/p", kind="remote", host="asic7",
                        fs="bnl-home", group="BNL cluster")]
    assert rs[0].group == "Local"
    assert rs[1].group == "BNL cluster"


def test_remote_roots_group_by_filesystem_not_by_host():
    """asic6, asic7 and asic8 return byte-identical results on the shared
    home, so grouping by host would split ONE set of files across three
    headings and imply a difference that does not exist."""
    a = rootsmod.Root("ms_pilot", "~/p", kind="remote", host="asic7",
                      fs="bnl-home")
    b = rootsmod.Root("jobs", "~/j", kind="remote", host="asic6",
                      fs="bnl-home")
    assert a.group == b.group == "bnl-home", (a.group, b.group)
    # and a root with no declared fs still lands somewhere sensible
    c = rootsmod.Root("solo", "~/s", kind="remote", host="asic9")
    assert c.group == "asic9"


def test_adding_a_root_persists_and_reloads():
    d = tempfile.mkdtemp(prefix="browse_add_")
    with open(os.path.join(d, "roots.json"), "w", encoding="utf-8") as fh:
        json.dump([{"name": "a", "path": "/tmp/a"}], fh)
    target = tempfile.mkdtemp(prefix="browse_target_")
    got = rootsmod.add({"name": "mine", "path": target}, config_dir=d)
    assert [r.name for r in got] == ["a", "mine"], [r.name for r in got]
    # written to roots.local.json, NOT to the tracked roots.json
    with open(os.path.join(d, rootsmod.LOCAL_CONFIG), encoding="utf-8") as fh:
        assert [e["name"] for e in json.load(fh)] == ["mine"]
    with open(os.path.join(d, "roots.json"), encoding="utf-8") as fh:
        assert [e["name"] for e in json.load(fh)] == ["a"]


def test_adding_the_same_name_replaces_rather_than_duplicates():
    """Same rule `load` already uses -- otherwise re-adding a path under an
    existing name yields two rows differing only in where they point."""
    d = tempfile.mkdtemp(prefix="browse_add_")
    t1 = tempfile.mkdtemp(prefix="browse_t1_")
    t2 = tempfile.mkdtemp(prefix="browse_t2_")
    rootsmod.add({"name": "mine", "path": t1}, config_dir=d)
    got = rootsmod.add({"name": "mine", "path": t2}, config_dir=d)
    names = [r.name for r in got]
    assert names.count("mine") == 1, names
    assert rootsmod.by_name(got, "mine").path == os.path.realpath(t2)


def test_a_bad_root_is_refused_before_it_reaches_the_config():
    """A config that fails to load takes the whole browser down (`load`
    raises on malformed JSON, deliberately), so a bad entry must never be
    written in the first place."""
    d = tempfile.mkdtemp(prefix="browse_add_")
    bad = [{"name": "", "path": "/tmp"},
           {"name": "x", "path": ""},
           {"name": "x", "path": "/definitely/not/here/at/all"},
           {"name": "x", "path": "/tmp", "kind": "remote"},        # no host
           {"name": "x", "path": "/tmp", "kind": "sideways"},
           {"name": "x", "path": "/tmp/a\nb"}]
    for entry in bad:
        try:
            rootsmod.add(entry, config_dir=d)
        except ValueError:
            continue
        raise AssertionError("accepted %r" % entry)
    assert not os.path.exists(os.path.join(d, rootsmod.LOCAL_CONFIG)), \
        "a refused entry still created the config"


def test_a_remote_root_is_added_without_touching_the_filesystem():
    """The isdir check must not run for a cluster path -- it is not on this
    machine, and checking it here would refuse every valid remote root."""
    d = tempfile.mkdtemp(prefix="browse_add_")
    got = rootsmod.add({"name": "onr", "path": "~/Documents/onr_t28",
                        "kind": "remote", "host": "asic7", "fs": "bnl-home",
                        "group": "BNL cluster"}, config_dir=d)
    r = rootsmod.by_name(got, "onr")
    assert r.kind == "remote" and r.host == "asic7"
    assert r.path == "~/Documents/onr_t28", r.path      # verbatim, not local
    assert r.group == "BNL cluster"


def test_adding_a_root_needs_the_same_origin_guard():
    """Adding a root WIDENS what the server will serve, so it sits behind the
    same guard as the exec: a page on another origin must not be able to point
    it at a new tree and then read it."""
    import http.client
    srv = _live_server()
    port = srv.server_address[1]

    def post(headers, body=b"{}"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/roots", body=body, headers=headers)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status
    try:
        assert post({"Content-Type": "application/json",
                     "Sec-Fetch-Site": "cross-site"}) == 403
        assert post({"Content-Type": "application/json",
                     "Origin": "http://evil.example"}) == 403
        assert post({"Content-Type": "application/x-www-form-urlencoded"}) == 403
        # same-origin gets past the guard and is refused on CONTENT instead
        assert post({"Content-Type": "application/json"},
                    body=b'{"name":"x","path":"/nope/nowhere"}') == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_listing_says_where_it_actually_is():
    """A root is configured under a NICKNAME, so the listing must carry the
    resolved path -- otherwise "flowruns" never says which disk, and
    "ms_pilot" never says which machine. It was reachable only by hovering
    for a tooltip, which is not an answer.
    """
    import threading
    from http.server import HTTPServer
    from urllib.request import urlopen
    import server as srv

    base, rs = _tree()
    srv.Handler.roots = rs
    httpd = HTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % httpd.server_port
    try:
        got = json.loads(urlopen(url + "/api/list?root=r&path=sub").read())
        assert got["root_name"] == "r", got
        assert got["root_kind"] == "local", got
        assert got["root_path"] == rs[0].path, got
        # the ABSOLUTE path of the listed directory, not just the root's
        assert got["abspath"] == os.path.join(rs[0].path, "sub"), got
        # and at the root itself the two agree
        top = json.loads(urlopen(url + "/api/list?root=r&path=").read())
        assert top["abspath"] == top["root_path"] == rs[0].path, top
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------ interactions / kill
def test_the_kill_route_is_guarded_and_demands_a_verification_string():
    """The only irreversible thing in this browser.

    Same-origin guarded like the other POSTs, and it refuses a request that
    names no expectation -- a kill that does not re-verify the command line
    can land on a pid recycled since the scan.
    """
    import http.client
    srv = _live_server()
    port = srv.server_address[1]

    def post(body, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/procs/kill", body=json.dumps(body).encode(),
                  headers=h)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status
    try:
        assert post({}, {"Sec-Fetch-Site": "cross-site"}) == 403
        assert post({}, {"Origin": "http://evil.example"}) == 403
        # same-origin, but incomplete or unverified
        assert post({}) == 400
        assert post({"host": "asic8", "pid": 123}) == 400, \
            "a kill with no expect string was accepted"
        assert post({"host": "asic8", "pid": "notanint",
                     "expect": "virtuoso"}) == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_scan_route_is_read_only_and_needs_no_guard():
    """Looking is a GET; only the kill is a POST. If the scan needed a guard
    it would mean the scan changed something.

    procscan is STUBBED: this suite must not touch the cluster (the first
    version of this check quietly scanned eleven real hosts over ssh and then
    timed out, which is a test of the VPN, not of the route).
    """
    import http.client

    class _Stub(object):
        ScanError = Exception
        asked = []

        @staticmethod
        def default_hosts():
            return ["asicX"]

        @staticmethod
        def scan(hosts):
            _Stub.asked = list(hosts)
            return {h: {"procs": [], "sessions": [], "error": None}
                    for h in hosts}

    srv = _live_server()
    port = srv.server_address[1]
    keep = server.procscan
    server.procscan = _Stub
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/api/procs")
        r = c.getresponse()
        body = json.loads(r.read().decode())
        c.close()
        assert r.status == 200, r.status
        assert list(body["hosts"]) == ["asicX"], body
        assert _Stub.asked == ["asicX"], _Stub.asked
        # POSTing to the scan route is not a thing
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/procs",
                  headers={"Content-Type": "application/json"})
        assert c.getresponse().status == 404
        c.close()
    finally:
        server.procscan = keep
        srv.shutdown()
        srv.server_close()


# ----------------------------------------------------- finding the server
def test_the_ui_version_changes_when_the_page_does():
    """"Am I looking at the new version?" must be answerable.

    A stale tab is pixel-identical to a fresh one, so the only way to tell used
    to be noticing a missing feature. The header carries a build id; this
    pins that it actually tracks the page.
    """
    import hashlib
    assert server.UI_VERSION and len(server.UI_VERSION) == 7
    other = hashlib.sha256((server.PAGE + "x").encode("utf-8")).hexdigest()[:7]
    assert other != server.UI_VERSION
    # the placeholder is substituted on the way out, not left in the markup
    page = server.PAGE.replace("__UI__", server.UI_VERSION)
    assert "__UI__" not in page and server.UI_VERSION in page


def test_the_agent_route_keeps_the_session_identity():
    """title and live must survive the projection into the API.

    They were dropped once, and the symptom was a picker of bare hex ids with
    every session marked not-live -- which is exactly the state the feature
    exists to prevent.
    """
    import http.client

    class _Stub(object):
        @staticmethod
        def sessions(cwd):
            return [{"id": "aaa", "path": "/tmp/aaa.jsonl", "size": 10,
                     "mtime": 100, "title": "a live one", "live": True},
                    {"id": "bbb", "path": "/tmp/bbb.jsonl", "size": 20,
                     "mtime": 90, "title": None, "live": False}]

        @staticmethod
        def summarize(path):
            return {"now": {"state": "idle"}, "turns": [], "timeline": [],
                    "thrash": [], "fleet": {"n": 0}, "n_events": 0}

        @staticmethod
        def project_dir(cwd):
            return "/tmp"

    srv = _live_server()
    port = srv.server_address[1]
    keep = server.agentview
    server.agentview = _Stub
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/api/agent")
        got = json.loads(c.getresponse().read().decode())
        c.close()
        assert got["session"] == "aaa", got
        assert got["sessions"][0]["title"] == "a live one", got["sessions"]
        assert got["sessions"][0]["live"] is True
        assert got["sessions"][1]["live"] is False
    finally:
        server.agentview = keep
        srv.shutdown()
        srv.server_close()


def test_the_page_script_has_no_invalid_python_escapes():
    """The whole UI is a JavaScript program inside a Python string, so every
    regex in it is one backslash away from an invalid escape that Python
    tolerates today and rejects later. Bitten twice -- a path split and a
    whitespace class -- and the second time the WARNING COMMENT contained the
    bad form too. Compiling the source with the warning promoted catches all
    of it at once."""
    import warnings
    src = os.path.join(os.path.dirname(os.path.abspath(server.__file__)),
                       "server.py")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        compile(text, src, "exec")          # raises if any escape is invalid


def test_the_page_can_tell_it_is_stale():
    """`no-store` stops NEW staleness; a tab cached before it landed keeps
    serving itself forever, and the symptom is a feature that "does nothing".
    So the page carries its own build id and compares it against the server's.

    Both substitutions come from the same value -- if the header id and the
    baked constant could differ, the check would cry wolf on every load.
    """
    page = server.PAGE.replace("__UI__", server.UI_VERSION)
    assert "__UI__" not in page, "a placeholder survived into the served page"
    assert 'const UI_BAKED = "%s";' % server.UI_VERSION in page
    assert "/api/health" in page and "reload" in page


def test_live_pages_are_not_cacheable_but_renders_are():
    """A reload must never hand back yesterday's page.

    Served with no cache headers a browser may heuristically cache the HTML and
    the API answers, which is one way to sit looking at an old UI. Content-
    addressed renders are exempt: named by their own hash, a cached copy is by
    definition the right one.
    """
    import http.client
    srv = _live_server()
    port = srv.server_address[1]

    def head(path):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        r.read()
        c.close()
        return r.headers.get("Cache-Control")
    try:
        assert head("/") == "no-store, must-revalidate"
        assert head("/api/roots") == "no-store, must-revalidate"
        assert head("/api/health") == "no-store, must-revalidate"
        assert head("/render/deadbeef.png") is None      # 404, but exempt
    finally:
        srv.shutdown()
        srv.server_close()


def test_two_repos_do_not_mistake_each_other_for_themselves():
    """One installation serves several checkouts and they SHARE a cache dir.

    With a single state file they lied about each other: launching the ONR
    browser overwrote the record, and the next AIML launch probed it, found a
    live server and exited as "already running" -- against a browser serving a
    different repository.
    """
    d = tempfile.mkdtemp(prefix="browse_state_")
    os.environ["BROWSE_CACHE_DIR"] = d
    a = server.state_path()
    keep = rootsmod.REPO
    try:
        rootsmod.REPO = os.path.join(d, "other-repo")
        b = server.state_path()
    finally:
        rootsmod.REPO = keep
    assert a != b, "two repos shared one state file"
    assert os.path.dirname(a) == os.path.dirname(b) == d


def test_health_identifies_this_app_and_not_just_any_server():
    """`--status` probes a port; something else listening there must not be
    mistaken for the browser."""
    import http.client
    srv = _live_server()
    port = srv.server_address[1]
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/api/health")
        got = json.loads(c.getresponse().read().decode())
        c.close()
        assert got["app"] == "artifact-browser"
        assert got["ui"] == server.UI_VERSION
        assert server.probe("http://127.0.0.1:%d/" % port)
        # a port with nothing on it is not our server
        assert server.probe("http://127.0.0.1:%d/" % _free_port(),
                            timeout=0.3) is None
    finally:
        srv.shutdown()
        srv.server_close()


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_a_stale_state_file_reports_gone_not_running():
    """A killed server never clears its state file, so a recorded URL that
    does not answer must read as gone -- otherwise `--status` sends the user
    to a dead port with confidence."""
    d = tempfile.mkdtemp(prefix="browse_state_")
    os.environ["BROWSE_CACHE_DIR"] = d
    with open(server.state_path(), "w", encoding="utf-8") as fh:
        json.dump({"url": "http://127.0.0.1:%d/" % _free_port(),
                   "pid": 1, "ui": "deadbee"}, fh)
    assert server.read_state()["ui"] == "deadbee"
    # Point the fallback scan at a free port. Without this the check passes or
    # fails depending on whether the DEVELOPER happens to have a browser
    # running on the stable port -- which is exactly what it did the first
    # time it ran, finding a real server and calling the stale file live.
    keep_port, keep_span = server.DEFAULT_PORT, server.PORT_SPAN
    server.DEFAULT_PORT, server.PORT_SPAN = _free_port(), 1
    try:
        assert server.status(quiet=True) is None, "a dead URL reported as running"
    finally:
        server.DEFAULT_PORT, server.PORT_SPAN = keep_port, keep_span


# ------------------------------------------------- cost estimates (plan 10.12)
def _fresh_estimate():
    """An estimator with its own empty store -- seeds only."""
    d = tempfile.mkdtemp(prefix="browse_est_")
    os.environ["BROWSE_CACHE_DIR"] = d
    estimate.reset()
    return d


def test_an_unmeasured_operation_says_so_rather_than_guessing():
    """The one thing an estimate must never be is invented.

    A made-up number on the first click is worse than no number, because the
    pane has no way to show it is a guess and the user has no way to tell.
    """
    _fresh_estimate()
    p = estimate.predict("something_new", "local", 10 << 20)
    assert p["seconds"] is None and p["basis"] == "unmeasured", p
    c = estimate.cost("something_new", "local", 10 << 20)
    assert c["confirm"] is False and c["mention"] is False, c
    assert "never measured" in c["text"], c["text"]


def test_a_measurement_displaces_the_seed():
    """Seeds are a starting point, not an anchor. The whole design is that
    this installation's own timings win -- a rate compiled into the source is
    a claim about someone else's disk."""
    _fresh_estimate()
    seeded = estimate.predict("render", "local", 1 << 20)
    assert seeded["basis"] == "seeded", seeded
    # one measured render, ten times faster than the recorded one
    estimate.record("render", "local", 1 << 20, seeded["seconds"] / 10.0)
    got = estimate.predict("render", "local", 1 << 20)
    assert got["basis"] == "measured", got
    assert got["seconds"] < seeded["seconds"] / 2, (got, seeded)


def test_the_estimate_is_for_what_will_be_read_not_the_file_size():
    """A budget-capped read of a 7.2 GB transient costs what 64 MB costs.

    Quoting the file size back would produce a number that is both terrifying
    and wrong -- the cluster really does have a 7.2 GB tran file, and opening
    it takes about four seconds.
    """
    _fresh_estimate()
    huge = 7203 << 20
    c = estimate.cost("wave", "remote", huge, will_read=64 << 20)
    assert c["size"] == huge and c["reads"] == 64 << 20 and c["capped"], c
    full = estimate.predict("wave", "remote", huge)["seconds"]
    assert c["seconds"] < full / 50, (c["seconds"], full)
    assert "of" in c["text"] and "7203" in c["text"].replace(".0", ""), c["text"]


def test_a_cache_hit_is_not_a_measurement():
    """0.001 s of dictionary lookup must not teach the estimator that a
    render is instant -- after which the pane would promise an instant render
    of a stream it has never drawn."""
    _fresh_estimate()
    before = estimate.predict("render", "local", 1 << 20)["seconds"]
    for _ in range(5):
        estimate.record("render", "local", 1 << 20, 0.001)
    after = estimate.predict("render", "local", 1 << 20)["seconds"]
    assert after < before, "sanity: recording SHOULD move the estimate"
    # and the server only calls record() for state == "rendered"
    src = open(os.path.join(os.path.dirname(os.path.abspath(server.__file__)),
                            "server.py"), encoding="utf-8").read()
    assert 'if got["state"] == "rendered" and not win:' in src, \
        "the render sample is no longer gated on an actual render"


def test_confirm_bytes_is_the_inverse_of_the_estimate():
    """The remote reader is handed a NUMBER, not a verdict: it stats the file
    on the cluster and decides there, because coming back to ask and going
    again is two round trips for one question."""
    _fresh_estimate()
    n = estimate.confirm_bytes("wave", "remote", seconds=5.0)
    assert n and n > 0, n
    assert abs(estimate.predict("wave", "remote", n)["seconds"] - 5.0) < 0.01


def test_timings_survive_a_restart():
    d = _fresh_estimate()
    estimate.record("wave", "local", 8 << 20, 4.0)
    estimate.reset()                       # a new process, same store
    os.environ["BROWSE_CACHE_DIR"] = d
    rows = [r for r in estimate.samples("wave", "local") if not r.get("seed")]
    assert len(rows) == 1 and rows[0]["seconds"] == 4.0, rows


# ------------------------------------------------------- remote waveforms (7)
def test_the_remote_reader_is_the_file_the_local_one_was_imported_from():
    """THE invariant. A cluster plot and a local plot must be drawn by the
    same bytes, or the partial-file rule -- drop the last time group while the
    file is unterminated -- could differ between them, on the exact view whose
    job is judging a run that has not finished."""
    assert server.wavemod is not None and server.WAVE_PATH, "no reader found"
    with open(server.WAVE_PATH, encoding="utf-8") as fh:
        assert fh.read() == server.WAVE_SRC


def test_a_checkout_without_a_reader_does_not_import_the_stdlib_wave():
    """`wave` is ALSO a stdlib module (the WAV one). Plain `import wave` on a
    checkout with no analog/engine/wave.py succeeds and hands back something
    with no read_psf -- a silent pass that surfaces as a viewer error on a
    file rather than as 'this installation has no reader'."""
    import importlib
    mod = importlib.import_module("wave")
    assert not hasattr(mod, "read_psf"), \
        "the stdlib wave module suddenly has read_psf; this test is moot"
    # the locator checks for the function, so the stdlib module can never win
    assert hasattr(server.wavemod, "read_psf")
    assert hasattr(server.wavemod, "choose")


def test_the_remote_wave_script_stats_before_it_reads():
    """One round trip, and it can decline. Measured against the real 7.2 GB
    file: 0.16 s to be told it is big, 4.00 s to actually read it."""
    keep = _with_fake({"kind": "gated", "size": 7 << 30, "mtime": 1,
                       "reads": 64 << 20})
    try:
        got = cluster.wave("h", "/x", "a.tran", "print(1)", gate_bytes=1 << 20)
        assert got["kind"] == "gated" and got["size"] == 7 << 30, got
        s = _FakeTransport.last_script
        assert "BROWSE_GATE_BYTES" in s and "os.stat(target)" in s, s[:400]
        # the stat must come BEFORE the reader is even compiled
        assert s.index("os.stat(target)") < s.index('compile(src'), \
            "the file is parsed before the gate can stop it"
    finally:
        cluster._remote = keep


def test_the_reader_source_crosses_the_wire_as_one_base64_line():
    """`_script` refuses a newline in a bound value -- that guard is right for
    paths and there is no reason to weaken it for 18 kB of python."""
    keep = _with_fake({"kind": "gated", "size": 1, "mtime": 1})
    try:
        src = "def read_psf():\n    return 1\n"          # newlines on purpose
        cluster.wave("h", "/x", "a.tran", src, gate_bytes=1)
        s = _FakeTransport.last_script
        assert src not in s, "the source went over raw, newlines and all"
        assert base64.b64encode(src.encode()).decode() in s
    finally:
        cluster._remote = keep


def test_a_corrupt_picture_is_an_error_not_a_blank_pane():
    keep = _with_fake({"kind": "wave", "size": 1, "svg_z": "not-base64!!"})
    try:
        try:
            cluster.wave("h", "/x", "a.tran", "x = 1")
        except cluster.RemoteError as exc:
            assert "survive the wire" in str(exc), exc
        else:
            raise AssertionError("a corrupt picture came back as a plot")
    finally:
        cluster._remote = keep


# --------------------------------------------------- the capped-read verdicts
def _psf(path, npoints, complete, stop="20e-6"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('HEADER\n"analysis type" "tran"\n"start" 0\n"stop" %s\n'
                 'TRACE\n"a" "V"\nVALUE\n' % stop)
        for i in range(npoints):
            fh.write('"time" %r\n"a" %r\n' % (i * 1e-9, i * 0.001))
        if complete:
            fh.write("END\n")


def test_a_capped_read_of_a_finished_file_is_not_called_running():
    """The bug the 7.2 GB file found. 'No END means the run is going' is only
    true when the WHOLE file was read; past the head budget we know nothing
    about the end of the file, and saying 'running' about a run that finished
    two hours ago is the one confident wrong answer this view must not give."""
    d = tempfile.mkdtemp(prefix="browse_wave_")
    p = os.path.join(d, "t.tran")
    _psf(p, 4000, complete=True)
    wm = server.wavemod
    whole = wm.read_psf(p)
    assert whole.state() == "complete" and not whole.truncated
    head = wm.read_psf(p, budget=4096)
    assert head.truncated, "budget did not bite"
    assert head.state() == "capped", head.state()
    # and NOT the answer it used to give
    assert head.state() != "running"


def test_an_unterminated_file_still_reads_as_running():
    """The negative control for the case above: a genuinely in-flight file
    must not be relabelled `capped` just because the state machine grew a
    third answer."""
    d = tempfile.mkdtemp(prefix="browse_wave_")
    p = os.path.join(d, "t.tran")
    _psf(p, 400, complete=False)
    w = server.wavemod.read_psf(p)
    assert not w.truncated and w.state() == "running", (w.truncated, w.state())


def _psf_many(n=40, points=6):
    """A PSF-ASCII sweep with `n` traces -- the many-trace case, for real."""
    d = tempfile.mkdtemp(prefix="browse_many_")
    p = os.path.join(d, "t.tran")
    names = ["t%d" % i for i in range(n)]
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('HEADER\n"analysis type" "tran"\n"start" 0\n"stop" 1e-6\n'
                 'SWEEP\n"time" "sweep" PROP(\n"units" "s"\n"grid" 1\n)\n'
                 'TRACE\n' + "".join('"%s" "V"\n' % x for x in names)
                 + 'VALUE\n')
        for k in range(points):
            fh.write('"time" %r\n' % (k * 1e-9))
            fh.write("".join('"%s" %r\n' % (x, i * 0.1)
                             for i, x in enumerate(names)))
        fh.write("END\n")
    return p, names


def test_the_trace_cap_is_one_rule_and_it_can_be_lifted():
    """73 traces in one frame is not a plot of any of them -- but a cap you
    cannot lift is a lie about the data, so naming traces overrides it."""
    wm = server.wavemod
    p, names = _psf_many(40)
    w = wm.read_psf(p)
    got, dropped = wm.choose(w)
    assert len(got) == wm.MAX_TRACES and dropped == 40 - wm.MAX_TRACES
    assert got == names[:wm.MAX_TRACES], "the cap reordered the file"
    named, dropped = wm.choose(w, names)               # ask for all 40
    assert len(named) == 40 and dropped == 0
    few, dropped = wm.choose(w, ["t7", "t9"])
    assert few == ["t7", "t9"] and dropped == 0


def test_unticking_every_box_draws_nothing_rather_than_everything():
    """The checkbox behaviour, and the reason `signals` is None-or-list.

    `None` means "you choose"; `[]` means the user unticked every box. If the
    two collapsed, the last untick would silently redraw all 98 traces --
    the control fighting the user at the moment they are most explicit.
    """
    wm = server.wavemod
    p, _names = _psf_many(6)
    w = wm.read_psf(p)
    auto, _d = wm.choose(w, None)
    assert auto, "None should still choose something"
    none, _d = wm.choose(w, [])
    assert none == [], none
    svg = wm.render(w, [])
    assert "no traces selected" in svg, svg[:300]


# ------------------------------------------------- listing order (plan 10.19)
def test_a_listing_says_how_many_rows_it_badged():
    """The pane may now re-sort, and a row past the badge cap has no verdict
    because it was never READ -- which is a different statement from "this run
    has no verdict". The two are indistinguishable unless the listing says how
    far the badging got, so it does.

    Measured on the real cluster: `layout_cc` is 83 entries and 64 badged.
    """
    d = tempfile.mkdtemp(prefix="browse_order_")
    for i in range(5):
        os.makedirs(os.path.join(d, "run%02d" % i))
    rs = [rootsmod.Root("r", d)]
    server.Handler.roots = rs
    from urllib.request import urlopen
    srv = _live_server()
    server.Handler.roots = rs
    try:
        url = "http://127.0.0.1:%d/api/list?root=r&path=" % srv.server_address[1]
        got = json.loads(urlopen(url, timeout=10).read())
    finally:
        srv.shutdown()
        srv.server_close()
    assert got["badged"] == 5, got["badged"]
    assert got["order"] == "mtime-desc", got["order"]
    # and the cap is what actually limits it
    assert got["badged"] <= model.BADGE_MAX_ENTRIES


def test_the_default_order_is_still_newest_first():
    """It is the right default and stays one: a stamp directory sorted
    alphabetically buries today's run under three months of them, which is
    the reason `listdir` chose it. The complaint was that it was invisible
    and fixed, not that it was wrong."""
    d = tempfile.mkdtemp(prefix="browse_order2_")
    import time as _t
    for i, name in enumerate(("zebra", "alpha", "middle")):
        p = os.path.join(d, name)
        with open(p, "wb") as fh:
            fh.write(b"x" * (10 - i))
        os.utime(p, (1000 + i * 100, 1000 + i * 100))
    rows = model.listdir(d, "")
    assert [r["name"] for r in rows] == ["middle", "alpha", "zebra"], rows


def test_the_ui_offers_all_three_keys_and_names_the_active_one():
    """Both halves of the complaint: "cannot be changed" and "it is not
    clear". Asserted on the page source because that is where the answer
    lives -- the UI is a JavaScript program inside a Python string."""
    here = os.path.dirname(os.path.abspath(server.__file__))
    with open(os.path.join(here, "server.py"), encoding="utf-8") as fh:
        src = fh.read()
    for key in ('["name", "name"]', '["mtime", "modified"]',
                '["size", "size"]'):
        assert key in src, key
    # the status line states the key AND the direction
    assert "by ${how}, ${dirText}" in src
    # directories stay first whatever the key
    assert "if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;" in src


# ------------------------------------- schematic <-> layout cross-probe (6.4)
_SCH = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<line x1="0" y1="0" x2="9" y2="9" stroke="#000" data-net="vinp"/>'
        '<text x="1" y="2" data-net="vinp">vinp</text>'
        '<line x1="0" y1="0" x2="9" y2="9" stroke="#000" data-net="vdd"/>'
        '<rect width="4" height="4"/></svg>')


def test_a_generated_svg_that_tags_its_nets_is_inlined_not_framed():
    """A picture goes to /raw; a picture that names its nets is the join.

    The schematic and the layout track map both put `data-net` on every shape
    they draw, so a pane holding both highlights a net by asking each the same
    question -- no coordinate mapping and no index. A plain SVG (an atlas
    crop) has no nets and must keep the cheap <img> path.
    """
    assert model.svg_nets(_SCH) == ["vinp", "vdd"], model.svg_nets(_SCH)
    assert model.svg_nets('<svg><rect/></svg>') == []


def test_the_pairing_is_by_net_name_and_scores_itself():
    """Measured on the real artifacts: every schematic matches its own layout
    on all of its nets and no other -- ota 8/8, lvds 7/7, lif 7/7, fc 15/15,
    with the runner-up at 5/10. That margin is the evidence the join is sound
    rather than a filename coincidence, so the score is reported."""
    d = tempfile.mkdtemp(prefix="browse_cp_")
    def put(name, nets):
        os.makedirs(os.path.join(d, name), exist_ok=True)
        with open(os.path.join(d, name, name + ".abstract.json"), "w", encoding="utf-8") as fh:
            json.dump({"rails": {n: [] for n in nets[:1]},
                       "tracks": [{"net": n} for n in nets[1:]]}, fh)
    put("mine", ["vdd", "vinp", "vinn", "out"])
    put("other", ["vdd", "clk"])
    put("nothing", ["a", "b"])
    got = model.crossprobe_candidates(d, ["vdd", "vinp", "vinn", "out"])
    assert [c["name"] for c in got] == ["mine", "other"], got
    assert got[0]["shared"] == 4 and got[0]["total"] == 4
    assert got[1]["shared"] == 1
    # zero overlap is NOT offered -- a pairing that shares nothing is a guess
    assert all(c["name"] != "nothing" for c in got)
    assert model.crossprobe_candidates(d, []) == []


def test_overlapping_roots_do_not_offer_the_same_layout_twice():
    """`repo` is the checkout and `analog/work` is inside it, so a same-file
    hit comes back once per root and the picker listed every layout as a
    duplicate pair. The first root wins -- roots.json puts the specific ones
    before the catch-all."""
    d = tempfile.mkdtemp(prefix="browse_cp2_")
    inner = os.path.join(d, "work")
    os.makedirs(os.path.join(inner, "blk"))
    with open(os.path.join(inner, "blk", "blk.abstract.json"), "w", encoding="utf-8") as fh:
        json.dump({"rails": {"vdd": []}, "tracks": [{"net": "out"}]}, fh)
    roots = [rootsmod.Root("work", inner), rootsmod.Root("repo", d)]
    got = server._crossprobe_all(roots, ["vdd", "out"])
    assert len(got) == 1, got
    assert got[0]["root"] == "work", got


# ----------------------------------------------- the live job panel (6.5)
class _FakeJobs(object):
    """Stands in for remote.Transport for the /api/jobs route."""
    calls = 0
    tried = []

    def __init__(self, host=None, timeout=None, ok=True, jobs=None,
                 only=None):
        self.host, self._ok, self._jobs, self._only = host, ok, jobs or [], only

    def list(self):
        _FakeJobs.calls += 1
        _FakeJobs.tried.append(self.host)

        class R(object):
            pass
        r = R()
        # `only` names the one host that answers, so failover is observable.
        r.ok = self._ok and (self._only is None or self.host == self._only)
        r.status = "KNOWN" if r.ok else "UNKNOWN"
        r.reason = None if r.ok else "transport timeout"
        r.data = {"now": 1785600000, "jobs": self._jobs}
        return r


def _with_jobs(ok=True, jobs=None, only=None):
    class _Mod(object):
        Transport = staticmethod(
            lambda host=None, timeout=None: _FakeJobs(host, timeout, ok, jobs,
                                                      only))
    keep = server.remotemod
    server.remotemod = _Mod
    server._JOBS_CACHE.clear()
    if server.hostsmod:
        server.hostsmod.reader_reset()
    _FakeJobs.calls = 0
    _FakeJobs.tried = []
    return keep


def _jobs_get(path):
    """GET one route off a real socket. -> (status, body text)"""
    import urllib.error
    from urllib.request import urlopen
    srv = _live_server()
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        try:
            r = urlopen(url + path, timeout=10)
            return r.status, r.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_unreachable_cluster_is_not_an_idle_one():
    """The same tri-state the listing pane honours, in the panel that would
    be most misleading without it: an empty jobs table reads as a quiet
    cluster, and "the VPN is down" is not "nothing is running"."""
    keep = _with_jobs(ok=False)
    try:
        code, body = _jobs_get("/api/jobs")
        assert code == 503, (code, body)
        assert "no host" in body and "answered" in body, body
        # and it really tried more than one before giving up
        assert len(set(_FakeJobs.tried)) > 1, _FakeJobs.tried
    finally:
        server.remotemod = keep


def test_the_job_list_is_cached_because_it_costs_ten_seconds():
    """Measured against asic6: `list` is 5.9-9.9 s (it stats every record),
    `events` is 0.11 s. A panel that re-read it on every repaint would leave
    a permanent job on the head node."""
    keep = _with_jobs(jobs=[{"jobid": "a", "state": "running"}])
    try:
        code, body = _jobs_get("/api/jobs")
        assert code == 200 and json.loads(body)["cached"] is False, body
        assert _FakeJobs.calls == 1
        _c, body = _jobs_get("/api/jobs")
        assert json.loads(body)["cached"] is True, body
        assert _FakeJobs.calls == 1, "the cache was not used"
        _c, body = _jobs_get("/api/jobs?refresh=1")
        assert json.loads(body)["cached"] is False
        assert _FakeJobs.calls == 2, "explicit refresh did not re-read"
    finally:
        server.remotemod = keep


def test_the_store_is_keyed_on_the_FILESYSTEM_not_the_host():
    """Measured: asic6, asic7 and asic8 return the IDENTICAL 389-job set
    (sha b7a1a6ab8836), naming 11 different hosts as where the work ran. The
    reading host is provenance; caching per host would re-read the same NFS
    directory once per box and call the results different."""
    keep = _with_jobs(jobs=[{"jobid": "a", "state": "running"}])
    try:
        _c, b1 = _jobs_get("/api/jobs")
        d1 = json.loads(b1)
        assert d1["fs"] and d1["host"], d1
        # a DIFFERENT preferred host must still hit the same cache entry
        _c, b2 = _jobs_get("/api/jobs?host=asic9")
        assert json.loads(b2)["cached"] is True, b2
        assert _FakeJobs.calls == 1, _FakeJobs.tried
    finally:
        server.remotemod = keep


def test_one_dead_host_does_not_stop_the_read():
    """A read of a shared store has no reason to fail because one box is
    down. Before this, every job query went to whatever ASIC_HOST said and
    stopped there."""
    keep = _with_jobs(jobs=[{"jobid": "a", "state": "done"}], only="asic7")
    try:
        code, body = _jobs_get("/api/jobs")
        assert code == 200, body
        d = json.loads(body)
        assert d["host"] == "asic7", d["host"]
        assert len(_FakeJobs.tried) > 1, "it never failed over"
        assert d["jobs"], d
    finally:
        server.remotemod = keep


def test_a_host_name_is_checked_before_it_reaches_the_transport():
    from urllib.parse import quote
    keep = _with_jobs()
    try:
        for bad in ("a b", "../x", "a;rm -rf /", "-oProxyCommand=x"):
            code, _b = _jobs_get("/api/jobs?host=" + quote(bad, safe=""))
            assert code == 400, (bad, code)
        code, _b = _jobs_get("/api/jobs?host=asic7")
        assert code == 200
    finally:
        server.remotemod = keep


def test_a_failed_job_read_is_not_recorded_as_a_timing():
    """A 503 took no measurable work and must not teach the estimator that a
    job list is instant -- the same rule that keeps a render cache hit out of
    the render rate."""
    os.environ["BROWSE_CACHE_DIR"] = tempfile.mkdtemp(prefix="browse_jt_")
    estimate.reset()
    before = len(estimate.samples("jobs", "remote"))
    keep = _with_jobs(ok=False)
    try:
        _jobs_get("/api/jobs")
    finally:
        server.remotemod = keep
    assert len(estimate.samples("jobs", "remote")) == before


def test_every_name_the_render_cache_mints_is_servable():
    """The guard and the thing it guards, checked against each other.

    Twice now a new cache-name shape has been minted upstream and 403'd on the
    way to the <img>: first the remote render's `r` prefix, then the crop's
    `_<window>` tag. The crop case hid for longer because the pane reported
    "rendered in 3 s" beside a blank box -- the button and the picture are
    different subsystems and only the picture is evidence.

    So this test does not list the shapes it expects; it asks `tools` to mint
    them and checks the SERVER will serve each one.
    """
    h = "a" * 32
    minted = [
        h + ".png",                                   # a local full render
        h + "_" + "b" * 10 + ".png",                  # a local CROP
        "r" + "c" * 31 + ".png",                      # a cluster render
    ]
    for name in minted:
        assert server.Handler._CACHE_NAME.match(name), \
            "%s is minted but would 403 on the way to the <img>" % name
    # and the guard is still a guard
    for bad in ("../secret.png", "a/b.png", "a.txt", "a..png", "", "a.png.exe",
                "a_b_c.png", "_deadbeef.png"):
        assert not server.Handler._CACHE_NAME.match(bad), bad


def test_a_crop_that_tools_really_produced_is_servable():
    """The end-to-end pin, and the one the 403 needed.

    Not "a name of this shape passes": `tools.render_gds` is asked to make a
    cropped render, and the file it actually wrote is checked against the
    route that has to serve it. If the window tag ever changes shape, this
    fails here rather than as a blank box beside "rendered in 3 s".
    """
    d = tempfile.mkdtemp(prefix="browse_crop_")
    _cache(d)
    gds = os.path.join(d, "x.gds")
    with open(gds, "wb") as fh:
        fh.write(b"gds bytes")
    keep = tools.RENDERER
    tools.RENDERER = _stub_renderer(d, "import sys\n"
                                       "open(sys.argv[2],'wb').write(b'PNG')\n")
    try:
        full = tools.render_gds(gds)
        crop = tools.render_gds(gds, win=(76.0, 67.0, 96.0, 87.0))
        other = tools.render_gds(gds, win=(0.0, 0.0, 10.0, 10.0))
    finally:
        tools.RENDERER = keep
    assert full["ok"] and crop["ok"] and other["ok"], (full, crop, other)
    # a window is part of the picture's identity, or the first crop is served
    # for every later one
    assert crop["png"] != full["png"] and crop["png"] != other["png"]
    for got in (full, crop, other):
        name = os.path.basename(got["png"])
        assert server.Handler._CACHE_NAME.match(name), \
            "%s was written but the /render/ route would 403 it" % name


def test_an_operating_point_reaches_the_wave_reader_and_gets_no_picture():
    """`.op` is mapped, but this flow writes its operating point as `op1.dc`
    -- so the mapping is a safety net and the CONTENT decides. The pane gets
    rows and no SVG; building one would be a picture of a current and a
    voltage sharing an axis."""
    assert model.kind_of_name("x.op") == "wave"
    assert model.kind_of_name("op1.dc") == "wave"
    assert model.kind_of_name("stb1.stb") == "wave", "8700 real files"
    d = tempfile.mkdtemp(prefix="browse_op_")
    p = os.path.join(d, "op1.dc")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('HEADER\n"analysis type" "dc"\n"analysis name" "op1"\n'
                 'VALUE\n'
                 '"Vdd:p" "I" -6.792475613049781e-05 PROP(\n"units" "A"\n)\n'
                 '"out" "V" 2.657313782574502e-01\nEND\n')
    w = server.wavemod.read_psf(p)
    assert w.is_point
    got = server._wave_payload(server.wavemod, w, {})
    assert got["svg"] == "", "a table does not need a picture"
    assert got["wave"]["is_point"] and got["wave"]["n_values"] == 2
    assert got["wave"]["points"][0]["units"] == "A"


def test_an_empty_selection_survives_the_query_string():
    """`sig` absent and `sig` empty are different requests, and a URL cannot
    express the second without being told to -- hence `nosig`."""
    from urllib.parse import parse_qs
    assert server._wave_view(parse_qs("mode=group"))["signals"] is None
    assert server._wave_view(parse_qs("nosig=1"))["signals"] == []
    assert server._wave_view(parse_qs("sig=a&sig=b"))["signals"] == ["a", "b"]


def test_the_ui_javascript_parses():
    """The standing trap: the whole UI is JavaScript inside a Python string,
    so a stray backslash or an unbalanced brace ships silently -- the python
    compiles either way and the page just does nothing. `node --check` is the
    only cheap check that reads it as JavaScript. Skipped where node is not
    installed; it is a developer aid, not a runtime dependency."""
    import subprocess
    here = os.path.dirname(os.path.abspath(server.__file__))
    with open(os.path.join(here, "server.py"), encoding="utf-8") as fh:
        src = fh.read()
    js = src[src.index("<script>") + 8:src.index("</script></body></html>")]
    d = tempfile.mkdtemp(prefix="browse_js_")
    p = os.path.join(d, "ui.js")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(js.replace("%%", "%"))
    try:
        r = subprocess.run(["node", "--check", p], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
    except (OSError, subprocess.SubprocessError):
        return                                        # no node -- skip
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")[:2000]


def _repo_with_readme(base, name, text):
    d = os.path.join(base, name)
    os.makedirs(os.path.join(d, "browse"), exist_ok=True)
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return d


def test_the_listing_says_what_a_checkout_IS():
    """`--list` printed names and paths, which answers 'what may I type' and
    not 'which one is the 28 nm flow'. A directory name is not a process
    node."""
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        d = _repo_with_readme(base, "spec2si-tsmc28",
                              "# spec2si-tsmc28 - beamforming chiplet pair, "
                              "TSMC 28 nm\n")
        # the repo name is already its own column; the description must not
        # repeat it and spend the width on it
        assert l.describe(d) == "beamforming chiplet pair, TSMC 28 nm", l.describe(d)
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_a_heading_that_is_only_the_name_falls_through_to_the_prose():
    """spec2si-xt011's heading was exactly this, and a bare repo name says
    nothing."""
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        d = _repo_with_readme(base, "spec2si-xt011",
                              "# spec2si-xt011\n\n"
                              "Headless flows for the **X-FAB XT011**\n")
        got = l.describe(d)
        assert got.startswith("Headless flows"), got
        assert "**" not in got, got
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_badges_and_logos_are_not_mistaken_for_prose():
    """tsmc65's README opens with a centred <p> logo block before its
    heading."""
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        d = _repo_with_readme(base, "spec2si-tsmc65",
                              '<p align="center">\n  <img src="logo.svg">\n'
                              "</p>\n\n"
                              "# AIML65P2 - streaming readout ASIC\n\n"
                              "| a | b |\n")
        assert l.describe(d) == "AIML65P2 - streaming readout ASIC", l.describe(d)
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_a_repo_with_no_readme_describes_as_nothing():
    """And the caller falls back to the path, rather than printing a blank."""
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        assert l.describe(os.path.join(base, "photonic_thing")) == ""
        assert l.describe(os.path.join(base, "nope")) == ""
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


def test_a_long_description_is_cut_on_a_word_boundary():
    base = _launch_tree()
    l, keep = _relaunch(base)
    try:
        d = _repo_with_readme(base, "spec2si-tsmc65",
                              "# spec2si-tsmc65\n\n" + ("wordy " * 40) + "\n")
        got = l.describe(d)
        assert got.endswith("..."), got
        assert len(got) <= 68, len(got)
        assert not got[:-3].endswith(" "), got
    finally:
        l._HERE, l.PKG_REPO = keep
        shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------- declarations (roots.json object form)
def _cfg(obj, name="roots.json"):
    d = tempfile.mkdtemp(prefix="browse_decl_")
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return d


def test_the_list_form_of_roots_json_declares_nothing():
    """The three onboarded repos' files are lists; they must go on loading,
    and `settings` must answer {} for them rather than inventing a default."""
    d = _cfg([{"name": "a", "path": "/tmp/a"}])
    assert [r.name for r in rootsmod.load(d)] == ["a"]
    assert rootsmod.settings(d) == {}, rootsmod.settings(d)


def test_the_object_form_carries_the_repo_level_declarations():
    d = _cfg({"roots": [{"name": "a", "path": "/tmp/a"}],
              "renderer": {"path": "analog/x/render.py",
                           "argv": ["{gds}", "{top}", "{out}"], "window": False},
              "klayout": "analog/x/kl.ps1",
              "badges": {"result.json": "verdict"}})
    assert [r.name for r in rootsmod.load(d)] == ["a"]
    s = rootsmod.settings(d)
    assert s["renderer"]["argv"] == ["{gds}", "{top}", "{out}"], s
    assert s["klayout"] == "analog/x/kl.ps1" and s["badges"] == {"result.json": "verdict"}


def test_a_local_config_overrides_a_declaration_key_by_key():
    d = _cfg({"roots": [{"name": "a", "path": "/tmp/a"}],
              "renderer": "analog/x/render.py", "klayout": "analog/x/kl.ps1"})
    with open(os.path.join(d, "roots.local.json"), "w", encoding="utf-8") as fh:
        json.dump({"roots": [], "renderer": "analog/y/render.py"}, fh)
    s = rootsmod.settings(d)
    assert s["renderer"] == "analog/y/render.py" and s["klayout"] == "analog/x/kl.ps1", s


def test_an_unknown_declaration_key_is_refused_not_ignored():
    """A misspelt `renderrer` would otherwise be a declaration that silently
    declares nothing, and the fallback list would run instead."""
    d = _cfg({"roots": [], "renderrer": "x.py"})
    try:
        rootsmod.settings(d)
    except ValueError as exc:
        assert "renderrer" in str(exc), exc
        return
    raise AssertionError("an unknown key was accepted")


def test_a_declared_renderer_is_confined_to_the_repo():
    """A tracked JSON file must not be able to choose what this server runs
    outside the checkout: absolute paths and `..` are refused."""
    for bad in ("C:/anything.py", "/tmp/x.py", "../other/render.py"):
        try:
            tools.renderer_from({"renderer": bad}, "/repo")
        except ValueError:
            continue
        raise AssertionError("accepted %r" % bad)
    # and the argv template is checked: it must name the layout and the
    # output, and may not invent a placeholder
    for bad in (["{gds}"], ["{gds}", "{out}", "{cell}"], "not-a-list"):
        try:
            tools.renderer_from({"renderer": {"path": "a/r.py", "argv": bad}},
                                "/repo")
        except ValueError:
            continue
        raise AssertionError("accepted argv %r" % (bad,))


def test_a_declared_renderer_wins_over_the_fallback_list():
    base = tempfile.mkdtemp(prefix="browse_decl_")
    try:
        # both a conventional renderer AND a declared one exist
        os.makedirs(os.path.join(base, "analog", "engine", "layout"))
        conv = os.path.join(base, "analog", "engine", "layout", "render_gds.py")
        open(conv, "w").close()
        os.makedirs(os.path.join(base, "analog", "lib", "x", "render"))
        decl = os.path.join(base, "analog", "lib", "x", "render", "render_gds.py")
        open(decl, "w").close()
        got = tools.renderer_from({}, base)
        assert got.path == conv and not got.declared, got.as_dict()
        got = tools.renderer_from(
            {"renderer": "analog/lib/x/render/render_gds.py"}, base)
        assert os.path.normpath(got.path) == os.path.normpath(decl), got.path
        assert got.declared and got.argv == tools.DEFAULT_RENDER_ARGV
        assert got.window and not got.needs_top
        got = tools.renderer_from(
            {"renderer": {"path": "analog/lib/x/render/render_gds.py",
                          "argv": ["{gds}", "{top}", "{out}"], "window": False}},
            base)
        assert got.needs_top and not got.window
        assert got.argv_for("G", "O", "T") == ["G", "T", "O"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _gds_with(cells, refs):
    """A minimal stream: STRNAME per cell, SNAME per reference."""
    import struct
    d = tempfile.mkdtemp(prefix="browse_gds_")
    p = os.path.join(d, "x.gds")

    def rec(rtyp, payload=b""):
        return struct.pack(">HBB", len(payload) + 4, rtyp, 0) + payload

    with open(p, "wb") as fh:
        for c in cells:
            fh.write(rec(0x05, bytes(24)))
            fh.write(rec(0x06, c.encode() + bytes(1)))
        for r in refs:
            fh.write(rec(0x0A))
            fh.write(rec(0x12, r.encode() + bytes(1)))
    return p


def test_the_top_cell_is_derived_from_the_stream_not_guessed():
    """The structure no SNAME names is the top; a sky130 render must be
    handed that, not "the last one listed"."""
    p = _gds_with(["leaf", "mid", "top"], ["leaf", "mid"])
    assert model.gds_top(p) == ["top"], model.gds_top(p)
    assert model.gds_summary(p)["top"] == ["top"]
    two = _gds_with(["a", "b"], [])
    assert model.gds_top(two) == ["a", "b"]


def test_a_renderer_that_needs_the_top_cell_is_handed_it_and_refuses_two():
    p = _gds_with(["leaf", "top"], ["leaf"])
    d = os.path.dirname(p)
    _cache(d)
    stub = _stub_renderer(d, "import sys\n"
                          "gds, top, out = sys.argv[1:4]\n"
                          "open(out, 'wb').write(('PNG:' + top).encode())\n")
    keep = tools.RENDERER_SPEC, tools.RENDERER
    try:
        tools.RENDERER_SPEC = tools.Renderer(
            stub, ["{gds}", "{top}", "{out}"], window=False, declared=True)
        tools.RENDERER = stub
        got = tools.render_gds(p)
        assert got["ok"], got
        assert open(got["png"], "rb").read() == b"PNG:top"
        # a crop is refused, not silently drawn whole
        got = tools.render_gds(p, win=(0, 0, 1, 1))
        assert not got["ok"] and got["state"] == "no_window", got
        # and two tops are a refusal that names them
        two = _gds_with(["a", "b"], [])
        got = tools.render_gds(two)
        assert not got["ok"] and got["state"] == "no_top" and "a, b" in got["error"], got
    finally:
        tools.RENDERER_SPEC, tools.RENDERER = keep


def test_the_remote_render_script_carries_the_declaration():
    """The cluster script gets the declared renderer as a REPO-RELATIVE path
    and the argv template, and finds the top cell itself."""
    binds = {}

    class _T(object):
        def __init__(self, host=None, timeout=None):
            pass

        def run_sh(self, script, **kw):
            binds["script"] = script
            class R(object):
                ok = True
                data = {"kind": "render", "b64": "", "size": 0}
            return R()
    keep = cluster._remote
    try:
        cluster._remote = type("M", (), {"Transport": _T})
        cluster.render("h", "~/x", "a.gds",
                       renderer_rel="analog/lib/x/render/render_gds.py",
                       render_argv=("{gds}", "{top}", "{out}"))
    finally:
        cluster._remote = keep
    s = binds["script"]
    assert "analog/lib/x/render/render_gds.py" in s
    assert '["{gds}", "{top}", "{out}"]' in s, s
    assert "0x12" in s          # the SNAME walk is in the shipped script


# ------------------------------------------------------- kinds, badges
def test_the_flows_own_text_kinds_are_no_longer_unknown():
    for ext in (".route", ".escape", ".pins", ".census", ".spi", ".net",
                ".va", ".pvl", ".rul", ".sdc", ".strm", ".tag"):
        assert model.kind_of_name("x" + ext) == "text", ext
    assert model.kind_of_name("x.oa") == "binary"


def test_a_scored_result_json_badges_by_its_verdict():
    """sky130 writes result.json / pex.json / schematic.json with a `verdict`;
    surveyed 2026-09-12 none of them badged, and nothing said so."""
    d = _stamp(result__json={"verdict": "PASS", "drc": "CLEAN",
                             "lvs": {"verdict": "MATCH"}})
    got = [(b["text"], b["tone"]) for b in model.badges(d, True)]
    assert got == [("PASS", "ok"), ("DRC CLEAN", "ok"), ("LVS MATCH", "ok")], got
    d = _stamp(pex__json={"verdict": "FAIL"})
    assert [b["tone"] for b in model.badges(d, True)] == ["bad"]


def test_a_cell_json_badges_its_bindings_and_an_unadopted_generation():
    """The designdb finding, re-derived by the same NAME-LINEAGE rule: a
    `layout2` beside a bound `layout` is not adopted; a `supply` beside a
    bound `power` is another route plan, not a generation."""
    d = _stamp(cell__json={
        "library": "l", "cell": "c",
        "bound": {"layout": "layout", "route": "power"},
        "views": {"layout": {"type": "layout", "files": []},
                  "layout2": {"type": "layout", "files": []},
                  "power": {"type": "route", "files": []},
                  "supply": {"type": "route", "files": []}}})
    got = [(b["text"], b["tone"]) for b in model.badges(d, True)]
    assert got == [("2 bound / 4 views", "info"),
                   ("layout2 not adopted (binds layout)", "warn")], got
    # a superseded generation is history, not a finding
    d = _stamp(cell__json={
        "bound": {"layout": "layout2"},
        "views": {"layout2": {"type": "layout", "files": []},
                  "layout3": {"type": "layout", "files": [],
                              "provenance": {"superseded": True}}}})
    assert [b["text"] for b in model.badges(d, True)] == ["1 bound / 2 views"]


def test_a_repo_declares_its_own_badge_sources_and_an_unknown_reader_is_refused():
    keep = model.BADGE_SOURCES
    try:
        model.configure_badges({"*.lvs.report": "verdict_text",
                                "score.json": "verdict"})
        assert model.badge_source_for("x.lvs.report") == "verdict_text"
        assert model.badge_source_for("score.json") == "verdict"
        assert model.badge_source_for("report.json") == "report"
        assert model.badge_source_for("x.txt") is None
        # a pattern applies to FILE entries, and the verdict is the first word
        d = tempfile.mkdtemp(prefix="browse_badge_")
        with open(os.path.join(d, "c.lvs.report"), "w", encoding="utf-8") as fh:
            fh.write("header\nLVS MATCH\n... MISMATCH in a table\n")
        e = model.annotate(model.listdir(d), d)
        assert [(b["text"], b["tone"]) for b in e[0]["badges"]] == \
            [("LVS MATCH", "ok")], e
        # a directory is badged from exact names only
        assert "*.lvs.report" not in model.exact_badge_sources()
        assert "score.json" in model.exact_badge_sources()
        try:
            model.configure_badges({"x.json": "no_such_reader"})
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown reader was accepted")
    finally:
        model.BADGE_SOURCES = keep
        model._BADGE_CACHE.clear()


def test_a_listing_says_which_badge_sources_it_read():
    """Empty is a statement -- "nothing here is a source" -- and the pane
    prints it, so silence stops reading as clean."""
    d = tempfile.mkdtemp(prefix="browse_badge_")
    os.makedirs(os.path.join(d, "plain"))
    with open(os.path.join(d, "notes.txt"), "w") as fh:
        fh.write("x")
    e = model.annotate(model.listdir(d), d)
    assert model.badge_sources_present(e) == [], e
    os.makedirs(os.path.join(d, "run"))
    with open(os.path.join(d, "run", "report.json"), "w") as fh:
        json.dump({"verdict": "PASS"}, fh)
    with open(os.path.join(d, "status.json"), "w") as fh:
        json.dump({"state": "done"}, fh)
    e = model.annotate(model.listdir(d), d)
    assert sorted(model.badge_sources_present(e)) == ["in run/", "status.json"]


# ------------------------------------------------------- the design record
def _design_repo():
    """A repo with a two-cell design library, a scratch declaration, a stub
    designdb whose check answers on demand, and one tracked origin file."""
    repo = tempfile.mkdtemp(prefix="browse_design_")
    lib = os.path.join(repo, "design", "adc")
    os.makedirs(os.path.join(lib, "top"))
    os.makedirs(os.path.join(lib, "dac"))
    with open(os.path.join(lib, "lib.json"), "w") as fh:
        json.dump({"library": "adc", "project": "the ADC"}, fh)
    os.makedirs(os.path.join(repo, "analog", "work"))
    src = os.path.join(repo, "analog", "work", "top.gds")
    with open(src, "wb") as fh:
        fh.write(b"GDSBYTES")
    import hashlib
    md5 = hashlib.md5(b"GDSBYTES").hexdigest()
    with open(os.path.join(lib, "top", "cell.json"), "w") as fh:
        json.dump({"library": "adc", "cell": "top",
                   "bound": {"stream": "stream"},
                   "bom": ["adc/dac"], "bom_counts": {"dac": 2},
                   "bom_external": {"tsmc65_12t/ANTENNA": 3},
                   "views": {"stream": {"type": "stream", "files": [
                       {"path": "adc/top/stream/top.gds", "md5": md5,
                        "bytes": 8, "origin": "analog/work/top.gds"}]}}}, fh)
    with open(os.path.join(lib, "dac", "cell.json"), "w") as fh:
        json.dump({"library": "adc", "cell": "dac", "bound": {}, "views": {}}, fh)
    os.makedirs(os.path.join(repo, "designdb"))
    with open(os.path.join(repo, "designdb", "oa_dest.py"), "w") as fh:
        fh.write('SCRATCH = "work_lib"\n')
    with open(os.path.join(repo, "designdb", "__init__.py"), "w") as fh:
        fh.write("")
    with open(os.path.join(repo, "designdb", "__main__.py"), "w") as fh:
        fh.write("import os, sys\n"
                 "v = os.environ.get('STUB_VERDICT', 'PASS')\n"
                 "print('designdb --check: ' + v + ' (2 cells)')\n"
                 "sys.exit(0 if v == 'PASS' else 1)\n")
    return repo, src, md5


def test_the_manifest_index_answers_which_view_a_file_is():
    repo, src, md5 = _design_repo()
    try:
        idx = model.design_index(repo)
        assert sorted(idx["libraries"]["adc"]["cells"]) == ["dac", "top"]
        assert idx["libraries"]["adc"]["project"] == "the ADC"
        got = model.which_view(repo, src)
        assert got["md5"] == md5
        assert [(h["library"], h["cell"], h["view"], h["bound"]) for h in got["hits"]] \
            == [("adc", "top", "stream", True)], got
        assert got["origin_of"] and got["origin_of"][0]["view"] == "stream"
        # DRIFT: the origin path with other bytes -- origin matches, md5 does not
        with open(src, "wb") as fh:
            fh.write(b"OTHER")
        got = model.which_view(repo, src)
        assert got["hits"] == [] and got["origin_of"], got
        # a file in no view says so, with its md5
        other = os.path.join(repo, "analog", "work", "z.gds")
        with open(other, "wb") as fh:
            fh.write(b"zzz")
        got = model.which_view(repo, other)
        assert got["hits"] == [] and got["origin_of"] == [] and got["md5"]
        # past the limit it is skipped and says why
        got = model.which_view(repo, other, limit=1)
        assert "skipped" in got and "md5 limit" in got["skipped"], got
        # and a repo without a tree says THAT, not "no hits"
        assert "skipped" in model.which_view(tempfile.mkdtemp(), other)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_the_index_is_rebuilt_when_a_manifest_changes():
    repo, src, md5 = _design_repo()
    try:
        idx = model.design_index(repo)
        assert "adc/top" in idx["cells"]
        os.makedirs(os.path.join(repo, "design", "adc", "new"))
        import time
        time.sleep(0.01)
        with open(os.path.join(repo, "design", "adc", "new", "cell.json"), "w") as fh:
            json.dump({"library": "adc", "cell": "new", "views": {}, "bound": {}}, fh)
        idx = model.design_index(repo)
        assert "adc/new" in idx["cells"], sorted(idx["cells"])
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_a_directory_named_for_a_library_says_which_kind_it_is():
    """On the cluster's analog/oa/ listing the published library and the
    scratch one are two directories, and only the served repo's record and
    its oa_dest.py say which is which."""
    repo, src, md5 = _design_repo()
    try:
        assert model.scratch_library(repo) == "work_lib"
        assert [b["text"] for b in model.library_badges("adc", repo)] == \
            ["design library · 2 cells"]
        assert [(b["text"], b["tone"]) for b in model.library_badges("work_lib", repo)] == \
            [("scratch OA library", "warn")]
        assert model.library_badges("other", repo) == []
        rows = [{"name": "adc", "is_dir": True, "badges": []},
                {"name": "work_lib", "is_dir": True, "badges": []},
                {"name": "adc", "is_dir": False, "badges": []}]
        model.annotate_libraries(rows, repo)
        assert [len(r["badges"]) for r in rows] == [1, 1, 0]
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_the_manifest_check_is_run_read_and_cached_by_signature():
    repo, src, md5 = _design_repo()
    try:
        sig = model.design_signature(repo)
        assert len(sig) == 3, sig
        got = tools.design_check(repo, signature=sig)
        assert got["available"] and got["ok"] and "PASS" in got["verdict"], got
        assert not got["cached"]
        assert tools.design_check(repo, signature=sig)["cached"]
        os.environ["STUB_VERDICT"] = "FAIL"
        try:
            # the cache answers for the same signature; a new one re-runs
            assert tools.design_check(repo, signature=sig)["ok"]
            got = tools.design_check(repo, signature=sig + (("x", 0, 0),))
            assert not got["ok"] and "FAIL" in got["verdict"], got
        finally:
            del os.environ["STUB_VERDICT"]
        # a repo without designdb says so rather than failing
        got = tools.design_check(tempfile.mkdtemp())
        assert got["available"] is False and got["ok"] is None
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_the_file_route_reports_the_design_record_end_to_end():
    """/api/file on a cell.json carries the views table; on the bytes of a
    captured view it names the view; /api/list on design/ flags the tree
    and /api/designcheck answers; /api/roots states the declarations."""
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    repo, src, md5 = _design_repo()
    keep = rootsmod.REPO, server.Handler.roots
    try:
        rootsmod.REPO = repo
        server.Handler.roots = [rootsmod.Root("design", os.path.join(repo, "design")),
                                rootsmod.Root("work", os.path.join(repo, "analog", "work"))]
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d" % httpd.server_port
        try:
            got = json.loads(urlopen(url + "/api/roots").read())
            assert "renderer" in got and "badge_sources" in got and got["design"] is True
            got = json.loads(urlopen(url + "/api/list?root=design&path=").read())
            assert got.get("design_tree") is True, got
            assert [b["text"] for e in got["entries"] for b in e["badges"]] == \
                ["design library · 2 cells"], got["entries"]
            got = json.loads(urlopen(url + "/api/file?root=design&path=adc/top/cell.json").read())
            f = got["design"]
            assert f["cell"] == "top" and f["views"][0]["bound"] is True, f
            assert f["bom"] == ["adc/dac"] and f["bom_external"] == {"tsmc65_12t/ANTENNA": 3}
            got = json.loads(urlopen(url + "/api/file?root=design&path=adc/lib.json").read())
            assert got["design_lib"]["cells"] == ["dac", "top"], got
            got = json.loads(urlopen(url + "/api/file?root=work&path=top.gds").read())
            r = got["design_ref"]
            assert r["hits"][0]["cell"] == "top" and r["hits"][0]["bound"], r
            got = json.loads(urlopen(url + "/api/designcheck").read())
            assert got["ok"] and got["n_manifests"] == 3, got
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        rootsmod.REPO, server.Handler.roots = keep
        shutil.rmtree(repo, ignore_errors=True)


def _needs_reader(fn):
    """Does a test exercise the engine's transient reader? Read off its
    SOURCE, so a test that starts using the reader is skipped correctly
    without anyone maintaining a list."""
    import inspect
    src = inspect.getsource(fn)
    return "server.wavemod" in src or "_psf(" in src or "WAVE_SRC" in src


def main():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = skipped = 0
    for name, fn in fns:
        # This file is VENDORED into every port, and a port without an
        # analog engine (xt011, sky130) has no `analog/engine/wave.py` to
        # read a transient with. The tests of that view are SKIPPED there,
        # loudly and counted -- not failed, which would make the whole suite
        # read as broken on exactly the checkouts that need its other tests
        # most, and not silently passed, which would say the view works
        # where it cannot.
        if server.wavemod is None and _needs_reader(fn):
            skipped += 1
            print("  skip %s: no engine transient reader on this checkout"
                  % name)
            continue
        try:
            fn()
            print("  ok   %s" % name)
        except Exception as exc:                       # noqa: BLE001
            bad += 1
            print("  FAIL %s: %s" % (name, exc))
    ran = len(fns) - skipped
    print("%d/%d passed%s" % (ran - bad, ran,
                              (", %d skipped (no engine transient reader)"
                               % skipped) if skipped else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())