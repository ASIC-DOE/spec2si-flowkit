#!/usr/bin/env python3
"""Start the artifact browser on a named repository.

    python3 browse/launch.py                  # this checkout
    python3 browse/launch.py xt011            # a sibling checkout, by prefix
    python3 browse/launch.py tsmc28 --status  # server flags pass straight through
    python3 browse/launch.py --list           # what can be served, and where

WHY THIS EXISTS. Until now "switch repositories" meant: stop the server, cd to
another checkout, start it again -- and a checkout with no `browse/` launcher
(spec2si-xt011, then) could not be served at all, even though nothing about it
was missing except the two files that say where it is. The implementation was
already locatable rather than copied (`roots.repo_root` reads `BROWSE_REPO`);
what was missing was a way to SAY which repo without changing directory.

SINCE 2026-09-12 THE PACKAGE IS VENDORED from spec2si-flowkit into every
port, so each checkout serves itself with `browse/server.py` and needs no
sibling. This launcher is kept for the convenience it was written for --
serving a sibling by name from wherever you are -- and for `--list`.

WHY IT STILL RESTARTS RATHER THAN SWITCHING IN THE PAGE. `roots.REPO` is
resolved once, at import, and `tools`, `cluster` and `server` all take their
answer from it -- that is what makes the confinement boundary a fixed set of
trees for the life of the process rather than something a request can move.
Making the repo per-request would put the served repo inside the request, which
is the one place `roots.py` is careful to keep it out of. Two servers on two
ports is cheap; a movable confinement root is not.

SO THEY RUN SIDE BY SIDE, deliberately. `server.state_path()` is already keyed
by the served repo and `serve()` only refuses a second server for the SAME
repo, so `launch.py tsmc65` and `launch.py xt011` coexist on 8730 and 8731 and
each `--status` finds its own. Switching is then a browser tab, not a restart.

Usage:
  python3 launch.py [--list] [-l] [-h] [--help]
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
#: The checkout this implementation lives in. Not necessarily the served one.
PKG_REPO = os.path.dirname(_HERE)


def _looks_like_repo(path):
    """Is `path` a checkout we could serve?

    Deliberately weak: a directory with a `.git` or an `analog/` is enough. A
    stricter test (require `browse/`, require a flowruns tree) would refuse
    exactly the case this file was written for -- a repo that has not been
    onboarded yet. The roots fall back to `roots._defaults()` when there is no
    `roots.json`, so an un-onboarded checkout is browsable, just plainer.
    """
    if not os.path.isdir(path):
        return False
    return (os.path.isdir(os.path.join(path, ".git"))
            or os.path.isdir(os.path.join(path, "analog"))
            or os.path.isdir(os.path.join(path, "browse")))


def candidates():
    """[abspath] of servable checkouts -- this one and its siblings, sorted.

    Siblings of the INSTALLATION, because that is the layout every one of these
    repos is checked out in (C:/dev/spec2si-tsmc65, C:/dev/spec2si-tsmc28, ...). A
    checkout somewhere else is still reachable by passing its path.
    """
    out = [PKG_REPO]
    parent = os.path.dirname(PKG_REPO)
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        names = []
    for n in names:
        p = os.path.join(parent, n)
        if p != PKG_REPO and _looks_like_repo(p):
            out.append(p)
    return out


def _matches(name, tok):
    """Does `tok` prefix-match `name` as a whole, or one of its `-`-separated
    components? All three process ports are now named `spec2si-<node>`
    (`spec2si-tsmc65`, `spec2si-tsmc28`, `spec2si-xt011`) -- a whole-name
    prefix match alone would need the full `spec2si-` stem typed every time,
    which defeats the short token this function exists for. A name with no
    hyphen splits to itself, so this is a strict superset of the old
    behaviour, not a special case for the new one.
    """
    return name.startswith(tok) or any(p.startswith(tok) for p in name.split("-"))


def resolve(token):
    """A repo name, prefix or path -> the checkout to serve. Raises ValueError.

    Matching is case-insensitive and by prefix -- against the whole directory
    name or any `-`-separated component of it (`_matches`) -- so `xt011`
    finds `spec2si-xt011` and `tsmc28` finds `spec2si-tsmc28` without anyone
    maintaining an alias table: an alias table is a second place to add a
    repo, and forgetting it is how this kind of launcher rots. An AMBIGUOUS
    prefix is an error listing what it matched, never a pick: silently
    serving one of two repos is the failure mode where you spend ten minutes
    wondering why your run is missing.
    """
    if not token:
        return PKG_REPO
    # An explicit path wins outright, so a checkout outside the sibling layout
    # is never unreachable.
    expanded = os.path.expanduser(token)
    if os.sep in token or "/" in token or os.path.isabs(expanded):
        p = os.path.realpath(expanded)
        if not os.path.isdir(p):
            raise ValueError("not a directory: %s" % token)
        return p

    tok = token.lower()
    cands = candidates()
    exact = [c for c in cands if os.path.basename(c).lower() == tok]
    if exact:
        return exact[0]
    hits = [c for c in cands if _matches(os.path.basename(c).lower(), tok)]
    # A NAMED checkout wins a tie. C:/dev holds a dozen unrelated projects and
    # every one of them is technically servable, so a prefix that hits one flow
    # repo and one photonics checkout is not really ambiguous -- but a prefix
    # hitting two NAMED repos still is, and stays an error.
    named = onboarded()
    if len(hits) > 1:
        hit_named = [c for c in hits if c in named]
        if len(hit_named) == 1:
            return hit_named[0]
        if hit_named:
            hits = hit_named
    if len(hits) == 1:
        return hits[0]
    if not hits:
        # The named repos spelled out, the rest as a COUNT. Listing only the
        # named ones is right for the common typo, but saying nothing about
        # the other seven servable checkouts would imply they are unreachable.
        names = [os.path.basename(c) for c in named]
        extra = len(cands) - len(names)
        raise ValueError(
            "no checkout matches %r -- known: %s%s"
            % (token, ", ".join(names) or "(none named)",
               "  (+%d more, see --list)" % extra if extra else ""))
    raise ValueError("%r is ambiguous: %s"
                     % (token, ", ".join(os.path.basename(h) for h in hits)))


#: A repo is ONBOARDED when it has NAMED its own trees. The `browse/` directory
#: alone is not enough: ONR's and XT011's hold a launcher stub, and a stub
#: without a roots file still gets nothing but the defaults -- so ranking it
#: with the named repos would contradict the label `--list` prints next to it.
#:
#: The two names are repeated from `roots.load` rather than imported, because
#: importing `roots` here would resolve `roots.REPO` before `serve_repo` has
#: set BROWSE_REPO -- which is the one ordering this whole file exists to
#: protect.
ROOTS_FILES = ("roots.json", "roots.local.json")


def onboarded():
    """The checkouts that have named their own trees -- the real repo list."""
    out = []
    for c in candidates():
        d = config_dir_for(c)
        if d and any(os.path.exists(os.path.join(d, f)) for f in ROOTS_FILES):
            out.append(c)
    return out


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def describe(repo, maxlen=64):
    """One line saying what a checkout IS, or "" if it does not say.

    `--list` used to print names and paths, which answers "what may I type"
    and not "which one is the 28 nm flow" -- and those are two different
    questions. A directory name is not a process node.

    Read from the repo's own README rather than a table here, because a table
    here is a second place to update and the one that rots. Two rules, both
    needed by the real READMEs: take the first `# ` heading, but if that
    heading is just the directory name (XT011's is) it says nothing, so take
    the first line of prose under it instead.
    """
    h1 = prose = ""
    try:
        with open(os.path.join(repo, "README.md"),
                  encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                s = ln.strip()
                if not s:
                    continue
                if s.startswith("# ") and not h1:
                    h1 = s[2:].strip()
                    continue
                # skip badges, logos, tables, rules and frontmatter
                if h1 and not prose and s[0] not in "#<>|!-*=[":
                    prose = s
                    break
    except OSError:
        return ""
    text = h1
    if not text or _norm(text) == _norm(os.path.basename(repo)):
        text = prose
    # the name is already its own column; a "spec2si-tsmc65 -- ..." prefix on
    # the description repeats it and costs the width the description needs
    for dash in ("—", "–", " - ", ":"):
        head, sep, tail = text.partition(dash)
        if sep and _norm(head) == _norm(os.path.basename(repo)):
            text = tail.strip()
            break
    text = text.replace("**", "").replace("`", "").strip()
    if len(text) > maxlen:
        cut = text[:maxlen].rsplit(" ", 1)[0]
        text = (cut or text[:maxlen]).rstrip(",;") + "..."
    return text


def config_dir_for(repo):
    """WHERE that repo's roots would live -- not whether it has any.

    Deliberately weaker than `onboarded()`: this is the directory handed to
    `roots.load` and written by `roots.add`, so a `browse/` holding only a
    launcher stub is still the right answer, and a root added through the UI
    lands beside it. Onboarded-ness is a separate question about content.

    THAT repo's `browse/` when it has one. Otherwise None, which makes
    `roots.load` fall back to defaults anchored on the served repo rather than
    quietly serving the INSTALLATION's roots, which would list another
    repository's flowruns under this repository's name.
    """
    d = os.path.join(repo, "browse")
    if os.path.isdir(d):
        return d
    return None


def serve_repo(repo, argv=None):
    """Set BROWSE_REPO and hand off to `server.main()`. -> exit code.

    Also the entry point the per-repo launchers call, so the discovery logic
    above lives once even though the bootstrap stub cannot.
    """
    argv = list(argv or [])
    # BEFORE the import: roots.py resolves the served repo at import time and
    # tools/cluster/server all take their answer from it.
    os.environ["BROWSE_REPO"] = repo
    if "--config-dir" not in argv:
        cd = config_dir_for(repo)
        if cd:
            argv += ["--config-dir", cd]
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import server                                       # noqa: E402
    # Quote back the command that started THIS instance. The default names
    # `browse/server.py`, which in a launcher-stub repo cannot start what the
    # reader is looking at.
    server.START_HINT = "python browse/launch.py %s" % os.path.basename(repo)
    keep = sys.argv
    sys.argv = ["browse"] + argv
    try:
        return server.main()
    finally:
        sys.argv = keep


def _print_list():
    import server                                       # noqa: E402
    print("servable checkouts (implementation: %s)" % _HERE)
    known = onboarded()
    for c in known:
        # what it IS, not just where it is -- a directory name does not say
        # which process node you are about to open
        print("   %-18s %s" % (os.path.basename(c), describe(c) or c))
    # The rest are listed but not promoted. They ARE servable -- that is
    # deliberate, it is how a repo gets looked at before anyone writes its
    # roots.json -- but printing a dozen unrelated projects at the same weight
    # as the three flow repos buries the answer the list exists to give.
    rest = [c for c in candidates() if c not in known]
    if rest:
        print("   -- no browse/roots.json (servable, default roots only):")
        print("      %s" % ", ".join(os.path.basename(c) for c in rest))
    print("\nstart one with:  python browse/launch.py <name>")
    print("running now:")
    # Ask each repo where ITS server is: the state file is keyed by served repo
    # (server.state_path), so this is the only way to see more than one.
    keep = os.environ.get("BROWSE_REPO")
    any_up = False
    for c in candidates():
        os.environ["BROWSE_REPO"] = c
        # roots.REPO is import-time, and state_path() reads it live -- so
        # re-resolve it for each probe rather than trusting the first import.
        import roots as rootsmod                        # noqa: E402
        rootsmod.REPO = rootsmod.repo_root()
        url = server.status(quiet=True)
        if url:
            any_up = True
            print("   %-18s %s" % (os.path.basename(c), url))
    if keep is None:
        os.environ.pop("BROWSE_REPO", None)
    else:
        os.environ["BROWSE_REPO"] = keep
    if not any_up:
        print("   (none)")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--list", "-l"):
        return _print_list()
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    # A leading bare word is the repo; anything starting with "-" is the
    # server's. Positional-first keeps `launch.py xt011 --status` reading the
    # way it is meant to and leaves server.main()'s parser untouched.
    token = None
    if argv and not argv[0].startswith("-"):
        token = argv.pop(0)
    try:
        repo = resolve(token)
    except ValueError as exc:
        sys.stderr.write("browse: %s\n" % exc)
        return 2
    return serve_repo(repo, argv)


if __name__ == "__main__":
    sys.exit(main())
