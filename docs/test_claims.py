#!/usr/bin/env python3
"""Gate for RUNNABLE CLAIMS in prose: do the paths and flags still exist?

⛔ VENDORED. This file lives in spec2si-flowkit and is copied byte-identically
into each process port, hash-gated by that repo's `sync.py --check`.

⭐ WHY THIS EXISTS. `docmodel`'s checks and `gen.py check` were green over a
how-to guide for three weeks while nobody could say whether its commands still
ran. That is not a bug in them -- they check what a document IS (frontmatter,
genre, links between docs), not what it CLAIMS about the code. A page can tell
you to run a script that was deleted, with a flag that was renamed, and every
existing gate passes: the Markdown is fine, the frontmatter is fine, and the
link checker never looks at a `.py` path because it is not a link to a doc.

So this asserts the one thing the others structurally cannot:

  PATHS   every repo-relative file named in a doc exists in the tree
  SCRIPTS every script a runnable code block invokes exists
  FLAGS   every flag on such a command line appears in that script's source

⚠ DELIBERATELY CONSERVATIVE, because a doc gate that cries wolf gets ignored
and then the real finding is invisible too. Three rules were added after a
first draft produced 35 findings of which all but one were its own noise:

  * a "path" must contain a `/`. A bare `run.py` in prose is a NAME -- the
    sentence "see the Commands chapter for `run.py`'s flags" names no path
    and must not be flagged;
  * a flag counts as DEFINED if its literal string appears ANYWHERE in the
    script, NOT just in an `add_argument` call. Several runners here hand-roll
    `sys.argv` and document their flags in the module docstring; an argparse-
    only check called all six of `analog/engine/run.py`'s real flags dead;
  * a flag is attributed to a script only when they share a command line, or
    when the whole doc invokes exactly one script. Otherwise a page that
    mentions `rsync -n --delete` beside a python command blames the wrong
    tool.

Anything naming a cluster or absolute path (`$TSMC_DIG_LIBS/...`, `~/...`,
`/u/cad/...`), a placeholder (`<circuit>`), or a build product that is
gitignored until generated is out of scope by construction -- this gate runs
offline, on a clone, with no PDK.

  python3 test_claims.py <repo-root> [--quiet]
  python3 test_claims.py --self-test      # negative control, no repo needed
"""
import os
import re
import shutil
import sys
import tempfile

PATH_RE = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)+"
                     r"\.(?:py|sh|md|json|tcl|v|sv|il|yml|yaml))`")
CMD_RE = re.compile(r"(?:^|\s)python3?\s+"
                    r"([A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)*\.py)")
FLAG_RE = re.compile(r"(--[a-z][a-z0-9-]*)")
TICK_FLAG_RE = re.compile(r"`(--[a-z][a-z0-9-]*)")
FENCE_RE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
CD_RE = re.compile(r"cd\s+(\S+)")

#: never generated at clone time -- naming one is not a stale claim. ⚠ This
#: list is a FALLBACK ONLY: the authority is the repo's own `.gitignore`, asked
#: via `git check-ignore`, because guessing got it wrong -- `dig_flows/*_work/`
#: holds P&R output that no clone has and no hardcoded prefix predicted.
BUILD_DIRS = ("docs/site/", "docs/manual/", "work/", "results/", "build/")

#: ⚠ Metasyntactic placeholders. `GETTING_STARTED.md` walks you through making
#: `foo/spec/foo.json` -- a file the READER creates, named for a circuit that
#: does not exist and never should. Flagging those called a working tutorial
#: broken in five places.
PLACEHOLDER_HEADS = frozenset(("foo", "bar", "baz", "qux", "mycircuit",
                               "example", "template"))
SKIP_DIRS = frozenset((".git", "__pycache__", "node_modules", "site",
                       "manual", "attic", ".attic"))

#: ⭐ SEVERITY IS NOT THIS TOOL'S OPINION -- it is `policy/docmeta.core.json`,
#: the vendored vocabulary, which already publishes a staleness contract per
#: genre. Quoting it: a `log` is "a disposable snapshot, explicitly ALLOWED to
#: be stale"; a `guide` "ages when the procedure it describes changes". So a
#: dead path in a dated log is a historical record and gating on it is noise,
#: while the same dead path in a guide is the defect this gate exists for.
#: Gate the genres that tell a reader to DO something now; report the rest.
GATING = ("guide", "overview")
#: generated from code; a stale claim here is fixed by REGENERATING, and
#: `reference` says so itself: "cannot go stale by construction".
SKIP_GENRES = ("reference",)


#: where WSL mounts the Windows drives. A module global, not a literal inside
#: `localize`, only so the self-test can point it at a fixture tree.
MNT = "/mnt"


def localize(path):
    """`C:\\dev\\X` -> `/mnt/c/dev/X` when running under WSL, which is where
    python actually lives on the Windows box; any other path is returned as-is.

    ⭐ THE ONE COPY. `sync.py` imports this rather than keeping its own: it
    lives in the flowkit beside this file, while this file is vendored and must
    stand alone in every port, so the dependency can only point this way.

    ⚠ Without it, every Windows-spelled path a doc or a registry names is
    looked for as a relative directory called `C:` -- the `cd C:\\dev\\
    spec2si-flowkit` in the vendored guides read as two false GATING findings
    in every port, and `consumers.json` resolved to no ports at all."""
    if os.path.isdir(os.path.join(MNT, "c")) and \
            re.match(r"^[A-Za-z]:[\\/]", path):
        return "{}/{}/{}".format(MNT, path[0].lower(),
                                 path[3:].replace("\\", "/"))
    return path


def _resolves_in(root, cwd, rel):
    """True when `rel` exists under a directory a code block `cd`-ed into.

    An absolute `cd` is taken literally -- after `localize`, so a drive-letter
    `cd` finds the checkout under WSL. A RELATIVE one is resolved against
    the repo root, which is the convention every doc in this family already
    follows -- they write `python3 analog/engine/run.py`, not a path from
    wherever the reader happens to stand. That makes `cd ..\\spec2si-tsmc65`
    resolve to the sibling checkout, which is what the sentence means and
    what a reader would do.
    """
    cwd = localize(cwd).replace("\\", os.sep)
    if len(cwd) > 2 and (cwd[1] == ":" or cwd.startswith("/")):
        base = cwd
    else:
        base = os.path.join(root, cwd)
    return os.path.exists(os.path.join(base, rel.replace("/", os.sep)))


def _skip(rel):
    return ("<" in rel or ">" in rel or rel.startswith("..")
            or rel.startswith("~") or rel.startswith("$") or rel.startswith("/")
            or rel.split("/")[0] in PLACEHOLDER_HEADS
            or any(rel.startswith(b) for b in BUILD_DIRS))


def _consumer_roots(root):
    """Where this repo's registered consumers live, per `consumers.json`.

    ⭐ The flowkit's docs legitimately describe what a PORT contains -- "each
    port then runs its own `docs/gen.py`" is true and useful, and there is no
    `docs/gen.py` here BY DESIGN (each port writes its own; the model is what
    gets vendored). Resolving those against the flowkit alone reported a
    correct README as broken in five places. `consumers.json` is a
    machine-readable registry of exactly where the ports are, so the gate asks
    it rather than special-casing a repo name.
    """
    path = os.path.join(root, "consumers.json")
    if not os.path.exists(path):
        return []
    try:
        import json
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for c in data.get("consumers", []):
        p = localize(c.get("path") or "")
        if p and os.path.isdir(p):
            out.append(p)
    return out


#: `_sibling`'s answer for a same-family repo that is not cloned beside this one
UNCLONED = "uncloned"


def _sibling(root, rel):
    """Resolve a CROSS-REPO path. True = found, False = repo gone, None = n/a,
    UNCLONED = a `spec2si-*` sibling this machine does not have.

    ⭐ These five repos cite each other constantly, and the citations are
    exactly what ADR-0001's rename broke: `AIML_ASIC/docs/threat_model.md` and
    `ONR_ADFT_ASIC/GIT_SETUP.md` still appear in live guides, naming
    directories that have not existed since 2026-08-24. Treating any
    cross-repo path as unverifiable would have hidden that; resolving it
    against the actual neighbours turns a whole family of doc rot into a
    finding, and keeps a correct pointer silent.

    Only the first segment is treated as a repo name, and only when a sibling
    directory family is recognisable -- otherwise this says nothing (None).
    """
    parts = rel.split("/")
    if len(parts) < 2:
        return None
    head, rest = parts[0], "/".join(parts[1:])
    parent = os.path.dirname(os.path.abspath(root))
    if not head.startswith("spec2si-") and not head.isupper() \
            and "_" not in head:
        return None
    cand = os.path.join(parent, head)
    if os.path.isdir(cand):
        return os.path.exists(os.path.join(cand, rest))
    # A sibling of the same family that simply is not cloned here says nothing.
    # ⚠ It used to return None, which `audit` reads as "not cross-repo" and
    # reports as a plain missing path -- so every correct `spec2si-*/...`
    # citation failed in CI, where no sibling is ever cloned, and in any
    # scratch worktree. The renamed-repo case above is unaffected: an old
    # name is not `spec2si-*`, so it still comes back False and still gates.
    if head.startswith("spec2si-"):
        return UNCLONED
    return False


#: a name no doc, no rule and no build writes -- probed under each top-level
#: directory to find prefixes that `.gitignore` swallows wholesale
_SENTINEL = "claims-gate-probe-no-such-file"


def _raw_ignored(root, rels):
    """The subset of `rels` git ignores, asked literally, in one batch."""
    if not rels:
        return frozenset()
    try:
        import subprocess
        out = subprocess.run(["git", "-C", root, "check-ignore", "--stdin"],
                             input="\n".join(sorted(rels)).encode("utf-8"),
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return frozenset()
    return frozenset(l.strip().replace("\\", "/")
                     for l in out.stdout.decode("utf-8", "replace").splitlines()
                     if l.strip())


def _ignored(root, rels):
    """The subset git itself calls ignored -- build products, not stale docs.

    ⚠ A CLAIM IS TESTED UNDER EVERY TOP-LEVEL DIRECTORY, not just at the root,
    because `.gitignore` is PATH-SENSITIVE and the doc writes the path the way
    a reader standing in that subtree would. `GETTING_STARTED.md` names
    `engine/cards/passives_card.json` *in a sentence explaining that it is
    ignored*; the rule that ignores it lives in `analog/.gitignore`, so asking
    from the root answers "not ignored" and the gate calls a correct sentence
    a defect. Deliberately untracked is not stale.

    Asked in ONE batch through `check-ignore --stdin`; a per-path call costs a
    process each and this runs over thousands of claims.
    """
    # ⚠ `exists`, not `isdir`: in a `git worktree` (and a submodule) `.git` is
    # a FILE pointing at the real one, and `isdir` skipped the probe there --
    # every deliberately ignored NDA card came back as a GATING finding.
    if not rels or not os.path.exists(os.path.join(root, ".git")):
        return frozenset()
    # ⛔⛔ A PREFIX THAT IS ITSELF IGNORED MATCHES EVERYTHING. `work/` is
    # ignored wholesale in every port, so probing `work/<claim>` answered
    # "ignored" for ANY claim at all -- xt011 went from 6 real gating findings
    # to a green 0/0 PASS in one edit, which is precisely the green-gate-over-
    # a-wrong-artifact failure this whole gate exists to catch. Probe only
    # under directories git actually tracks.
    #
    # ⛔ And ask it of a CHILD, not of the directory. xt011 TRACKS files under
    # `work/` (force-added) while `.gitignore` still says `work/`, so git calls
    # `work` itself "not ignored" -- it holds tracked content -- yet ignores
    # every `work/<claim>`, and the gate read 17 gating findings as a clean
    # PASS. A prefix under which a name nobody wrote is ignored would swallow
    # any claim, so that is the test, and it covers the wholly-ignored case too.
    tops = [d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")]
    sentinel = {"{}/{}".format(t, _SENTINEL): t for t in tops}
    blind = set(sentinel[s] for s in _raw_ignored(root, list(sentinel))
                if s in sentinel)
    tops = [t for t in tops if t not in blind]
    probe, origin = [], {}
    for rel in sorted(rels):
        for cand in [rel] + ["{}/{}".format(t, rel) for t in tops]:
            probe.append(cand)
            origin[cand] = rel
    try:
        import subprocess
        out = subprocess.run(["git", "-C", root, "check-ignore", "--stdin"],
                             input="\n".join(probe).encode("utf-8"),
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return frozenset()
    hit = set()
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        key = line.strip().replace("\\", "/")
        if key in origin:
            hit.add(origin[key])
    return frozenset(hit)


def docs_under(root):
    """The TRACKED docs, and only those.

    ⚠ A plain walk is wrong here and quietly inflates everything: this repo
    family keeps agent worktrees under `.claude/worktrees/<name>/`, each a
    FULL SECOND COPY of the tree, so walking tsmc65 found 1133 documents
    where `git ls-files` finds 277 -- and every finding in a worktree copy is
    a duplicate of one in the real tree, reported against a path nobody edits.
    Fall back to a walk only outside a checkout (the self-test's temp dir).
    """
    tracked = _git_tracked(root, "*.md")
    if tracked is not None:
        for rel in tracked:
            yield os.path.join(root, rel)
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def _git_tracked(root, pattern=None):
    """Tracked paths, repo-relative. `pattern` None means EVERY tracked file.

    ⚠ The index and the doc corpus are two different questions and sharing
    one query broke the gate: built from `ls-files *.md`, the index held no
    `.py` or `.json` at all, so every source path a doc named looked deleted
    -- 1,252 findings, all of them the tool's own.
    """
    if not os.path.isdir(os.path.join(root, ".git")):
        return None
    try:
        import subprocess
        cmd = ["git", "-C", root, "ls-files"]
        if pattern:
            cmd.append(pattern)
        out = subprocess.run(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    if out.returncode != 0:
        return None
    rels = [l.strip() for l in out.stdout.decode("utf-8", "replace").splitlines()
            if l.strip()]
    return [r for r in rels if os.path.exists(os.path.join(root, r))]


def source_of(root, rel, cache):
    if rel not in cache:
        try:
            with open(os.path.join(root, rel), encoding="utf-8",
                      errors="replace") as fh:
                cache[rel] = fh.read()
        except OSError:
            cache[rel] = ""
    return cache[rel]


def _index(root):
    """Every tracked file, indexed by its path suffixes.

    ⚠ THE POINT: a doc legitimately names a path relative to a working
    directory it just `cd`-ed into, or relative to the subtree it documents.
    `GETTING_STARTED.md` says `engine/cards/em_card.json` and means
    `analog/engine/cards/em_card.json`; `dig_flows/scloop_flow/README.md`
    says `run.py` after a `cd`. Resolving those against the repo ROOT called
    514 live files dead -- the tool's own defect, not the docs'. So the
    question this gate actually asks is the one that means something offline:
    **does a file by this path still exist ANYWHERE in the tree?** A rename
    or a delete still fails; a working directory no longer does.
    """
    tracked = _git_tracked(root)
    if tracked is None:
        tracked = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                tracked.append(os.path.relpath(full, root).replace("\\", "/"))
    idx = {}
    for rel in tracked:
        parts = rel.split("/")
        for i in range(len(parts)):
            idx.setdefault("/".join(parts[i:]), []).append(rel)
    return idx


def _genre_reader(root):
    """-> f(text) = canonical genre. CALLS docmodel; never copies it.

    House rule `gate-calls-the-code`: a replica is exact the day it is written
    and diverges silently after. The alias table in particular is a shared
    contract (`howto` and `recipe` both fold to `guide`) and restating it here
    would let this gate disagree with the generator about what a document IS.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import docmodel
        dm = docmodel.DocModel(root)

        def read(text):
            meta, _body = docmodel.parse_frontmatter(text)
            return dm.canon_genre(meta.get("genre")) or ""
        read("")                     # prove the wiring before trusting it
        return read
    except Exception:
        def read(text):                      # standalone: no vendored core
            m = re.search(r"^genre:\s*(\S+)", text, re.M)
            return m.group(1) if m else ""
        return read


def audit(root):
    """-> (n_docs, [(doc, genre, kind, detail)]) -- kind in path/script/flag."""
    findings = []
    cache = {}
    index = _index(root)
    genre_of = _genre_reader(root)
    ports = _consumer_roots(root)
    docs = sorted(docs_under(root))
    for path in docs:
        rel_doc = os.path.relpath(path, root).replace("\\", "/")
        here = os.path.dirname(path)
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        genre = genre_of(text)
        if genre in SKIP_GENRES:
            continue

        for m in PATH_RE.finditer(text):
            rel = m.group(1)
            if _skip(rel) or rel in index:
                continue
            if os.path.exists(os.path.join(here, rel)):
                continue
            if any(os.path.exists(os.path.join(p, rel.replace("/", os.sep)))
                   for p in ports):
                continue                 # a path the registered ports have
            sib = _sibling(root, rel)
            if sib is True or sib == UNCLONED:   # resolved, or unanswerable
                continue
            if sib is False:             # names a repo that is not there
                findings.append((rel_doc, genre, "path",
                                 rel + "   (no such sibling repo -- renamed?)"))
                continue
            findings.append((rel_doc, genre, "path", rel))

        # command lines inside runnable blocks
        # ⭐ HONOUR THE `cd` THE BLOCK JUST DID. A runnable block routinely
        # changes directory first, and in this family it often changes REPO:
        # `add-a-process-node.md` says `cd C:\dev\spec2si-flowkit` and then
        # `python3 sync.py`, which is correct -- sync.py vendors OUT of the
        # flowkit and deliberately has no copy in any port. Reading the
        # invocation without the cd calls that guide broken for saying the
        # true thing.
        cmdlines, scripts = [], set()
        for body in FENCE_RE.findall(text):
            cwd = None
            for line in body.splitlines():
                m = CD_RE.match(line.strip())
                if m:
                    cwd = m.group(1).strip().rstrip("/\\")
                for s in CMD_RE.findall(line):
                    if _skip(s):
                        continue
                    if cwd and _resolves_in(root, cwd, s):
                        continue
                    cmdlines.append((s, line))
                    scripts.add(s)

        # ⚠ AMBIGUITY IS NOT A FINDING, AND IT IS NOT A PASS EITHER -- it is
        # an unanswerable question. Sixteen directories here hold a `run.py`;
        # picking one and checking its flags invented 722 findings in a single
        # pass. A bare name still proves the file EXISTS (the script check),
        # but its flags are only checkable when the name resolves UNIQUELY.
        live, ambiguous = {}, set()
        for s in sorted(scripts):
            if s in index:
                hits = index[s]
                live[s] = hits[0]
                if len(hits) > 1:
                    ambiguous.add(s)
            elif any(os.path.exists(os.path.join(p, s.replace("/", os.sep)))
                     for p in ports):
                live[s] = None           # a port's script; flags not checkable
                ambiguous.add(s)
            elif os.path.exists(os.path.join(here, s)):
                live[s] = os.path.relpath(os.path.join(here, s),
                                          root).replace("\\", "/")
            else:
                findings.append((rel_doc, genre, "script", s))

        # ⚠ ONLY flags on an actual command line. A page that says "run this
        # exact line" is making a checkable claim; a flag merely MENTIONED in
        # prose belongs to whichever tool the sentence is about, and guessing
        # blamed `dig_flows/run.py` for rsync's `--delete` in three READMEs.
        for s, line in cmdlines:
            if s not in live or s in ambiguous:
                continue
            src = source_of(root, live[s], cache)
            for fl in FLAG_RE.findall(line):
                if fl not in src:
                    findings.append((rel_doc, genre, "flag",
                                     "{} on `{}`".format(fl, live[s])))

    # ⭐ let the repo answer, in one batch: a path git IGNORES is a build
    # product the clone was never meant to have, not a claim that rotted.
    cand = set(d for _doc, _g, k, d in findings if k in ("path", "script"))
    ignored = _ignored(root, cand)
    findings = [f for f in findings
                if not (f[2] in ("path", "script") and f[3] in ignored)]
    return len(docs), findings


def self_test():
    """Negative control: a gate is not believed until it has been made to fail."""
    global MNT
    tmp = tempfile.mkdtemp(prefix="claims-selftest-")
    mnt = tempfile.mkdtemp(prefix="claims-selftest-mnt-")
    sep = tempfile.mkdtemp(prefix="claims-selftest-sep-")
    saved_mnt = MNT
    try:
        os.makedirs(os.path.join(tmp, "engine"))
        with open(os.path.join(tmp, "engine", "run.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('import sys\nif "--real" in sys.argv:\n    pass\n')
        fm = "<!--docmeta\ngenre: {}\nstatus: active\nupdated: 2026-01-01\n" \
             "summary: s\n-->\n"
        good = (fm.format("guide") +
                "# good\n\n```bash\npython3 engine/run.py --real\n```\n\n"
                "See `run.py` for more, and `engine/run.py`.\n")
        bad = (fm.format("guide") +
               "# bad\n\n```bash\npython3 engine/run.py --bogus\n```\n\n"
               "Path: `engine/gone.py`\n\n```bash\npython3 engine/absent.py\n```\n")
        with open(os.path.join(tmp, "good.md"), "w", encoding="utf-8") as fh:
            fh.write(good)
        _, f_good = audit(tmp)
        if f_good:
            print("SELF-TEST FAIL: clean corpus produced findings: %r" % (f_good,))
            return 1

        # ⭐ CROSS-REPO CITATIONS, both halves. A `spec2si-*` sibling that is
        # not cloned beside this tree is unanswerable and must stay silent (CI
        # never has one); an old-style repo name that is not there is the
        # ADR-0001 rename rot and must still gate.
        with open(os.path.join(tmp, "sib.md"), "w", encoding="utf-8") as fh:
            fh.write(fm.format("guide") + "# sib\n\nSee "
                     "`spec2si-claims-selftest-absent/docs/x.md` and "
                     "`CLAIMS_SELFTEST_GONE/docs/x.md`.\n")
        _, f_sib = audit(tmp)
        os.remove(os.path.join(tmp, "sib.md"))
        if [x.split()[0] for _d, _g, _k, x in f_sib] != \
                ["CLAIMS_SELFTEST_GONE/docs/x.md"]:
            print("SELF-TEST FAIL: an uncloned spec2si-* sibling must be "
                  "silent and a gone old-style repo must gate: %r" % (f_sib,))
            return 1

        # ⭐ A DRIVE-LETTER `cd` MUST RESOLVE. The vendored guides say
        # `cd C:\dev\spec2si-flowkit` then `python3 sync.py`, and read
        # literally under WSL that is a directory named `C:` -- two false
        # GATING findings in every port. The fixture stands in for `/mnt` and
        # sits OUTSIDE the corpus, so `_index` cannot find `sync.py` by name
        # and only the `cd` can make it resolve. The negative control is the
        # same block pointed at a checkout that is not there: mapping the
        # drive must not turn into accepting any drive-letter path.
        os.makedirs(os.path.join(mnt, "c", "dev", "kit"))
        with open(os.path.join(mnt, "c", "dev", "kit", "sync.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("")
        MNT = mnt
        drive = fm.format("guide") + \
            "# drive\n\n```bash\ncd C:\\dev\\{}\npython3 sync.py --to x\n```\n"
        with open(os.path.join(tmp, "drive.md"), "w", encoding="utf-8") as fh:
            fh.write(drive.format("kit"))
        _, f_drive = audit(tmp)
        if f_drive:
            print("SELF-TEST FAIL: a drive-letter `cd` to a real checkout did "
                  "not resolve: %r" % (f_drive,))
            return 1
        with open(os.path.join(tmp, "drive.md"), "w", encoding="utf-8") as fh:
            fh.write(drive.format("gone"))
        _, f_gone = audit(tmp)
        if [(k, x) for _d, _g, k, x in f_gone] != [("script", "sync.py")]:
            print("SELF-TEST FAIL: a drive-letter `cd` to a missing checkout "
                  "must still report the script: %r" % (f_gone,))
            return 1
        os.remove(os.path.join(tmp, "drive.md"))
        MNT = saved_mnt

        with open(os.path.join(tmp, "bad.md"), "w", encoding="utf-8") as fh:
            fh.write(bad)
        _, f_bad = audit(tmp)
        kinds = sorted(set(k for _d, _g, k, _x in f_bad))
        if kinds != ["flag", "path", "script"]:
            print("SELF-TEST FAIL: expected flag+path+script, got %r (%r)"
                  % (kinds, f_bad))
            return 1
        if not all(g == "guide" for _d, g, _k, _x in f_bad):
            print("SELF-TEST FAIL: genre not carried: %r" % (f_bad,))
            return 1

        # ⭐ the severity split is itself a claim, so control it too: the SAME
        # broken text filed as a `log` must be reported and must NOT gate.
        os.remove(os.path.join(tmp, "bad.md"))
        with open(os.path.join(tmp, "aslog.md"), "w", encoding="utf-8") as fh:
            fh.write(fm.format("log") + bad.split("-->\n", 1)[1])
        _, f_log = audit(tmp)
        if not f_log:
            print("SELF-TEST FAIL: a stale log should still be REPORTED")
            return 1
        if any(g in GATING for _d, g, _k, _x in f_log):
            print("SELF-TEST FAIL: a log must not gate: %r" % (f_log,))
            return 1
        # ⛔ CONTROL FOR THE IGNORE-PROBE BLINDNESS. `_ignored` tests a claim
        # under each top-level directory because .gitignore is path-sensitive;
        # a prefix that is itself ignored (`work/`) then matches EVERY claim
        # and the gate silently passes. This asserts a real finding survives a
        # tree that has one. Needs a git repo, so it is skipped without one.
        os.remove(os.path.join(tmp, "aslog.md"))
        with open(os.path.join(tmp, "bad.md"), "w", encoding="utf-8") as fh:
            fh.write(bad)
        try:
            import subprocess
            q = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if subprocess.run(["git", "-C", tmp, "init"], **q).returncode == 0:
                os.makedirs(os.path.join(tmp, "work"))
                with open(os.path.join(tmp, ".gitignore"), "w",
                          encoding="utf-8") as fh:
                    fh.write("work/\n")
                subprocess.run(["git", "-C", tmp, "add", "-A"], **q)
                _, f_ig = audit(tmp)
                if not any(k == "path" for _d, _g, k, _x in f_ig):
                    print("SELF-TEST FAIL: an ignored top-level dir blinded "
                          "the ignore probe -- findings vanished: %r" % (f_ig,))
                    return 1
                # ...and the xt011 shape: `work/` still ignored, but holding a
                # FORCE-ADDED tracked file, so git calls `work` itself not
                # ignored while swallowing every `work/<claim>`.
                with open(os.path.join(tmp, "work", "keep.sh"), "w",
                          encoding="utf-8") as fh:
                    fh.write("")
                subprocess.run(["git", "-C", tmp, "add", "-f",
                                "work/keep.sh"], **q)
                _, f_tr = audit(tmp)
                if not any(k == "path" for _d, _g, k, _x in f_tr):
                    print("SELF-TEST FAIL: an ignored `work/` with a tracked "
                          "file in it blinded the ignore probe: %r" % (f_tr,))
                    return 1

            # ⭐ THE PROBE MUST RUN WHERE `.git` IS A FILE (a worktree, a
            # submodule). `--separate-git-dir` makes one with no commit needed.
            # A deliberately ignored card named in a guide is out of scope, so
            # this corpus must come back clean -- and did not, before.
            repo = os.path.join(sep, "repo")
            if subprocess.run(["git", "init", "--separate-git-dir",
                               os.path.join(sep, "meta"), repo],
                              **q).returncode == 0:
                with open(os.path.join(repo, ".gitignore"), "w",
                          encoding="utf-8") as fh:
                    fh.write("secret/\n")
                with open(os.path.join(repo, "card.md"), "w",
                          encoding="utf-8") as fh:
                    fh.write(fm.format("guide") + "# card\n\nThe card "
                             "`secret/card.json` is never committed.\n")
                subprocess.run(["git", "-C", repo, "add", "-A"], **q)
                n_sep, f_sep = audit(repo)
                if n_sep != 1 or f_sep:
                    print("SELF-TEST FAIL: with `.git` a file, an ignored "
                          "path was reported: %r" % (f_sep,))
                    return 1
        except (OSError, ValueError):
            pass

        print("self-test PASS: clean corpus 0 findings; seeded bad flag, bad "
              "path and missing script all caught in a `guide`; a drive-letter "
              "`cd` resolves to a real checkout and not to a missing one; the "
              "same text as a `log` is reported but does not gate; an uncloned "
              "`spec2si-*` sibling is silent and a gone repo gates; an ignored "
              "`work/`, wholly or around a tracked file, does not blind the "
              "ignore probe; and the probe runs where `.git` is a file")
        return 0
    finally:
        MNT = saved_mnt
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(mnt, ignore_errors=True)
        shutil.rmtree(sep, ignore_errors=True)


def main(argv):
    if "--self-test" in argv:
        return self_test()
    args = [a for a in argv if not a.startswith("-")]
    root = args[0] if args else "."
    quiet = "--quiet" in argv
    n, findings = audit(root)
    name = os.path.basename(os.path.abspath(root))
    gating = [f for f in findings if f[1] in GATING]
    advisory = [f for f in findings if f[1] not in GATING]
    verdict = "FAIL" if gating else "PASS"
    print("claims check: {}  {} doc(s), {} gating, {} advisory  [{}]"
          .format(verdict, n, len(gating), len(advisory), name))
    if not quiet:
        for label, group in (("GATING", gating), ("advisory", advisory)):
            if not group:
                continue
            print("-- {} ({}) --".format(label, len(group)))
            for doc, genre, kind, detail in group:
                print("  {:7s} {:8s} {:46s} {}"
                      .format(kind, genre or "?", doc, detail))
    if advisory and not gating:
        print("advisory only: {} claim(s) in genres the vocabulary lets age "
              "(log/plan/study/finding/decision).".format(len(advisory)))
    return 1 if gating else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
