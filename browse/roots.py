#!/usr/bin/env python3
"""Root configuration and path confinement for the artifact browser.

This module is the browser's security boundary and nothing else. It answers two
questions: which trees may be browsed, and does a requested path lie inside one
of them. Everything the server does with a filesystem path goes through
`resolve()`.

Confinement is by RESOLVED path, not by string prefix. A prefix check passes
`/repo/../etc/passwd` and follows a symlink out of the tree; `os.path.realpath`
collapses both before the comparison. The server is bound to 127.0.0.1 and is
read-only, so this is defence in depth rather than the only guard -- but a
browser that will happily open any file it is asked for is one URL away from
being a file-exfiltration endpoint, and it costs nothing to not be that.

Config: `browse/roots.json`, tracked, with an untracked `browse/roots.local.json`
overriding or extending it -- the same convention as `analog/engine/sync/
sync.local`, so a machine-specific path never has to be committed.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def repo_root():
    """The repository whose artifacts this browser is serving.

    Normally the checkout this package sits in. `BROWSE_REPO` overrides it so
    that ONE installation can serve ANOTHER checkout -- which is how the
    second repo stopped needing its own copy of all of this. The alternative
    was nine byte-identical files kept in step by hand, and the cost was
    growing with every feature.
    """
    env = os.environ.get("BROWSE_REPO")
    if env:
        return os.path.realpath(os.path.expanduser(env))
    return os.path.dirname(_HERE)


#: Resolved once, at import. The launcher sets BROWSE_REPO before importing
#: anything from here, so every module sees the same answer.
REPO = repo_root()


class Root(object):
    """One browsable tree."""

    def __init__(self, name, path, kind="local", host=None, fs=None,
                 group=None):
        self.name = name
        self.kind = kind
        self.host = host
        #: Heading this root is displayed under. Local roots and cluster roots
        #: are different KINDS of place -- one is on this disk and instant, the
        #: other is an ssh round trip away -- and a single flat list hid that
        #: distinction behind identical-looking rows.
        self._group = group
        #: Which FILESYSTEM this root's path lives on. The cluster homes are
        #: shared, so a layout rendered from asic6 and the same layout
        #: rendered from asic7 are the same artifact and must share one cache
        #: entry -- measured, not assumed: the 28 nm divider renders
        #: BYTE-IDENTICAL (sha 1dd0e73d3d48) from asic6, asic7 and asic8, all
        #: through the one shared ~/miniforge3.
        #:
        #: DECLARED rather than inferred, and defaulting to the host, because
        #: "these two hosts see the same files" is a fact about a site, not
        #: something a browser may guess. A wrong guess would serve one
        #: machine's layout as another's.
        self.fs = fs or host
        if kind != "local":
            # A REMOTE path is a cluster path and must survive verbatim.
            # expanduser/realpath here would rewrite "~/Documents/ms_pilot"
            # against the LOCAL home and then against the local filesystem,
            # producing a Windows path that means nothing on asic7. The
            # cluster expands its own ~ (see cluster.py's resolver).
            self.path = path
            return
        path = os.path.expanduser(path)
        # A RELATIVE path in roots.json is relative to the REPO, not the cwd.
        # Anchoring on cwd works right up until someone starts the browser from
        # somewhere other than the repo root, at which point a tracked config
        # silently points at different trees -- or at nothing.
        if not os.path.isabs(path):
            path = os.path.join(REPO, path)
        # Store the REAL path once; every later comparison uses it.
        self.path = os.path.realpath(path)

    @property
    def exists(self):
        """True / False for a local root, None for a remote one.

        NOT False for remote: that rendered every cluster root as "missing",
        which is a claim we have not checked and usually a false one. Whether
        a cluster path is there costs an ssh round trip, and the root list must
        not block on N of them at startup -- the answer arrives when the root
        is opened, as a real listing or a real error.
        """
        if self.kind != "local":
            return None
        return os.path.isdir(self.path)

    @property
    def group(self):
        """The heading this root appears under.

        Remote roots group by FILESYSTEM, never by host. On a shared home the
        host is only the machine that happens to run the read -- asic6, asic7
        and asic8 return byte-identical results -- so grouping by host would
        split one set of files across three headings and imply a difference
        that does not exist. It is surfaced as a per-root detail instead, for
        when a read fails and you need to know who was asked.
        """
        if self._group:
            return self._group
        if self.kind == "local":
            return "Local"
        return self.fs or self.host or "Remote"

    @property
    def slug(self):
        """URL-safe stand-in for the display name.

        Files are served under a PATH-shaped URL (/f/<slug>/<relpath>) so that
        relative references inside an HTML artifact resolve. A display name
        cannot go there directly: names like "analog/work" contain a slash and
        "cell lib" a space, either of which makes the split between root and
        relative path ambiguous.
        """
        return "".join(c if c.isalnum() or c in "._-" else "-"
                       for c in self.name).strip("-") or "root"

    def as_dict(self):
        return {"name": self.name, "slug": self.slug, "kind": self.kind,
                "host": self.host, "fs": self.fs, "path": self.path,
                "group": self.group, "exists": self.exists}


def _defaults():
    """Roots that are useful on any checkout, so the browser works unconfigured.

    Deliberately the artifact trees, not the whole repo: the browser exists to
    answer "what did the last run do", and a root list that opens on 40,000
    source files buries the eight that matter.
    """
    return [
        {"name": "flowruns", "path": os.path.join(REPO, "analog", "results",
                                                  "flowruns")},
        {"name": "analog/work", "path": os.path.join(REPO, "analog", "work")},
        # The design record (docs/decisions/0003, and its adoptions in every
        # port): one `cell.json` per published cell. Since 2026-09-12 every
        # port has one, so it is a default rather than a per-repo courtesy.
        {"name": "design", "path": os.path.join(REPO, "design")},
        {"name": "repo", "path": REPO},
    ]


#: Repo-level keys a roots file may carry when it is written in OBJECT form:
#:
#:   {"roots": [...],
#:    "renderer": "analog/lib/sky130_ota6/render/render_gds.py"
#:             | {"path": ..., "argv": ["{gds}", "{top}", "{out}"],
#:                "window": false},
#:    "klayout":  "analog/engine/layout/klayout_open.ps1",
#:    "badges":   {"result.json": "verdict", "*.lvs.report": "verdict_text"}}
#:
#: A DECLARATION per repo, the same move `designdb/oa_dest.py` made for OA
#: libraries: `tools._RENDERER_AT` was a list of places to guess, and the
#: third port (sky130) kept its renderer at a fourth path with a different
#: argument order, so guessing had run out. The list form of roots.json is
#: still accepted unchanged -- it is the object form with only `roots` set.
SETTINGS_KEYS = ("renderer", "klayout", "badges")


def _read(d, fn):
    """(roots_list, settings_dict) from one config file, or (None, None)."""
    p = os.path.join(d, fn)
    if not os.path.exists(p):
        return None, None
    try:
        with open(p, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("%s: %s" % (fn, exc))
    if isinstance(got, list):
        return got, {}
    if isinstance(got, dict) and isinstance(got.get("roots"), list):
        extra = {k: got[k] for k in SETTINGS_KEYS if k in got}
        unknown = sorted(k for k in got if k not in SETTINGS_KEYS
                         and k != "roots" and not k.startswith("_"))
        if unknown:
            # A misspelt key would otherwise be a declaration that silently
            # declares nothing -- exactly the failure the declaration exists
            # to remove.
            raise ValueError("%s: unknown key(s) %s -- known: roots, %s"
                             % (fn, ", ".join(unknown),
                                ", ".join(SETTINGS_KEYS)))
        return got["roots"], extra
    raise ValueError("%s: expected a JSON list of root objects, or an object "
                     "with a \"roots\" list" % fn)


def settings(config_dir=None):
    """The repo-level declarations (SETTINGS_KEYS) from roots.json, with
    roots.local.json overriding key by key. {} when the file is a list."""
    d = config_dir or _HERE
    out = {}
    for fn in ("roots.json", "roots.local.json"):
        _r, extra = _read(d, fn)
        if extra:
            out.update(extra)
    return out


def load(config_dir=None):
    """[Root] from roots.json + roots.local.json, defaults if neither exists.

    A local entry with the same name REPLACES the tracked one, so overriding a
    single path does not mean restating the whole list.
    """
    d = config_dir or _HERE
    entries = []
    for fn in ("roots.json", "roots.local.json"):
        got, _extra = _read(d, fn)
        if got is None:
            continue
        for e in got:
            entries = [x for x in entries if x.get("name") != e.get("name")]
            entries.append(e)
    if not entries:
        entries = _defaults()

    out = []
    for e in entries:
        if not e.get("name") or not e.get("path"):
            continue
        out.append(Root(e["name"], e["path"], e.get("kind", "local"),
                        e.get("host"), e.get("fs"), e.get("group")))
    return out


#: The ONE file this browser writes, and the only one it ever may.
#:
#: This is not a hole in the read-only principle: `roots.local.json` is the
#: browser's OWN untracked config, never a browsed tree, and it exists
#: precisely so a machine-specific path does not have to be committed. What
#: read-only protects is the artifacts -- no flowrun stamp, layout or log is
#: ever touched. Making the user hand-edit JSON to keep a folder they just
#: opened is the friction that made the root list feel fixed in the first place.
LOCAL_CONFIG = "roots.local.json"


def add(entry, config_dir=None):
    """Append a root to roots.local.json and return the reloaded list.

    Validates before persisting, because a config that fails to load takes the
    whole browser down with it (`load` raises on malformed JSON, deliberately)
    -- so a bad entry must never reach the file.
    """
    d = config_dir or _HERE
    name = (entry.get("name") or "").strip()
    path = (entry.get("path") or "").strip()
    kind = entry.get("kind") or "local"
    if not name or not path:
        raise ValueError("a root needs a name and a path")
    if kind not in ("local", "remote"):
        raise ValueError("kind must be local or remote")
    if kind == "remote" and not (entry.get("host") or "").strip():
        raise ValueError("a remote root needs a host")
    if "\n" in path or "\r" in path or "\n" in name:
        raise ValueError("newline in name or path")
    if kind == "local" and not os.path.isdir(os.path.expanduser(path)):
        # Checked HERE rather than at first click: "the folder you added is
        # not there" belongs to the moment you added it.
        raise ValueError("not a directory: %s" % path)

    p = os.path.join(d, LOCAL_CONFIG)
    existing = []
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                existing = json.load(fh)
            if not isinstance(existing, list):
                raise ValueError("expected a list")
        except (OSError, ValueError) as exc:
            raise ValueError("%s: %s" % (LOCAL_CONFIG, exc))

    rec = {"name": name, "path": path}
    if kind == "remote":
        rec["kind"] = "remote"
        rec["host"] = entry["host"].strip()
        if entry.get("fs"):
            rec["fs"] = entry["fs"].strip()
    if entry.get("group"):
        rec["group"] = entry["group"].strip()

    # Same rule `load` uses: same name REPLACES, so re-adding a path under an
    # existing name corrects it instead of producing two rows that differ only
    # in where they point.
    existing = [e for e in existing if e.get("name") != name] + [rec]

    tmp = p + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, p)                  # never leave a half-written config
    return load(d)


def by_name(rootlist, name):
    for r in rootlist:
        if r.name == name:
            return r
    return None


def by_slug(rootlist, slug):
    for r in rootlist:
        if r.slug == slug:
            return r
    return None


def resolve_slug(rootlist, slug, relpath):
    """resolve() keyed by URL slug rather than display name."""
    root = by_slug(rootlist, slug)
    if root is None:
        raise ValueError("unknown root: %r" % slug)
    return resolve(rootlist, root.name, relpath)


def resolve(rootlist, name, relpath):
    """(Root, abspath) for `relpath` inside root `name`, or raise ValueError.

    THE gate. Rejects an unknown root, an absolute or drive-qualified relpath,
    and anything that resolves outside the root -- whether by `..`, by a symlink,
    or by both.
    """
    root = by_name(rootlist, name)
    if root is None:
        raise ValueError("unknown root: %r" % name)
    if root.kind != "local":
        raise ValueError("root %r is not local" % name)

    relpath = (relpath or "").replace("\\", "/")
    # Reject absolute input BEFORE any stripping, and reject rather than
    # reinterpret. Order matters and got this wrong once: lstrip("/") first
    # turns "/etc/passwd" into "etc/passwd", so os.path.isabs never fires and
    # the request is quietly served as <root>/etc/passwd. That is confined, so
    # not an escape -- but silently redefining what the caller asked for is the
    # kind of contract that becomes an escape the next time this is edited.
    if (os.path.isabs(relpath) or relpath.startswith("/")
            or (len(relpath) > 1 and relpath[1] == ":")):
        raise ValueError("relpath must be relative: %r" % relpath)
    relpath = relpath.lstrip("/")

    target = os.path.realpath(os.path.join(root.path, relpath))
    if not _inside(target, root.path):
        raise ValueError("path escapes root %r: %r" % (name, relpath))
    return root, target


def resolve_remote(rootlist, name, relpath):
    """(Root, posix_relpath) for a REMOTE root, or raise ValueError.

    The local half of a two-sided check, and only the local half. It cannot
    resolve a cluster symlink -- only the cluster can -- so it does the cheap,
    certain rejections here (unknown root, absolute path, any `..` segment,
    an embedded newline that would break the heredoc) and the remote script
    repeats the FULL realpath+commonpath test on the far side, where it is
    authoritative.

    Two checks rather than one because they fail differently: this one keeps a
    malformed request from ever reaching the cluster, and that one is the
    thing that is actually true about the cluster's filesystem.
    """
    root = by_name(rootlist, name)
    if root is None:
        raise ValueError("unknown root: %r" % name)
    if root.kind != "remote":
        raise ValueError("root %r is not remote" % name)
    rel = (relpath or "").replace("\\", "/")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise ValueError("relpath must be relative: %r" % rel)
    if "\n" in rel or "\r" in rel:
        raise ValueError("newline in path")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("path escapes root %r: %r" % (name, relpath))
    return root, "/".join(parts)


def _inside(target, root):
    """Is `target` the root itself or beneath it?

    os.path.commonpath, not startswith: a prefix test says /repo-backup is
    inside /repo. Both arguments are already realpath'd by the callers.
    """
    if target == root:
        return True
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        # different drives on Windows -- definitively not inside
        return False
