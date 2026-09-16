#!/usr/bin/env python3
"""The SHARED documentation model: one parse, one link check, one freshness gate.

⛔ VENDORED. This file is a byte-identical copy of spec2si-flowkit's
`docs/docmodel.py`, hash-gated by that repo's `sync.py --check`. Edit it
THERE, never here.

WHY THIS IS A SEPARATE FILE. Each process repo had, or wanted, its own
documentation generator, and a generator is two things welded together: a
MODEL (what the docs and the code actually say) and a BACKEND (how to render
it). The model is node-agnostic -- a docstring is a docstring on 65 nm and on
28 nm, and `docmeta` frontmatter is a contract all three repos already share
via `policy/docmeta.core.json`. The backend is not: it knows the repo's areas,
its machine-read JSON cards, its README block names.

So the model lives here and is vendored, exactly as the policy core is, and
each repo keeps a thin `docs/gen.py` that supplies its own configuration and
renders. That is what lets a second and third backend -- the web manual, the
PDF -- hang off the same parse instead of becoming a second extractor to keep
in step. See ADR-0001 and the packaging plan.

⚠ STDLIB ONLY, AND NO IMPORT OF THE SUBJECT CODE. The API reference is built
by STATIC AST parse because the engines need the PDK/Virtuoso/Calibre
environment that CI does not have. Both properties are load-bearing: they are
why the freshness gate can run on every PR with no dependency install and no
licence. Do not add a third-party import here; put it in a backend.

⚠ PYTHON FLOOR 3.6.8. The cluster runs raw system python with no pip. No
f-strings-with-`=`, no walrus, no `ast.unparse` without the guard below.
"""
import ast
import glob as _glob
import json
import os
import re
import subprocess

#: Used only when the vendored core is absent -- see `DocModel._load_core`.
FALLBACK_GENRES = ("overview", "guide", "reference", "decision", "plan",
                   "study", "finding", "log", "datasheet")
FALLBACK_REQUIRED = ("genre", "status", "updated", "summary")

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


# ---- pure helpers: no repo state, safe to call free ------------------

def parse_frontmatter(text):
    """Return (meta_dict, body). Metadata is a leading HTML comment
    `<!--docmeta ... -->` of simple `key: value` lines -- HTML comments are
    invisible on GitHub (unlike a YAML `---` fence, which GitHub renders as
    a table, or errors on when a value contains a colon). Absent -> ({},
    text)."""
    m = re.match(r"^<!--docmeta\s*\n(.*?)\n-->\s*\n?", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]                     # strip surrounding quotes
        meta[k] = v
    return meta, text[m.end():]


def first_line(doc):
    """First non-empty line of a docstring, trimmed."""
    if not doc:
        return ""
    for ln in doc.strip().splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def first_para(doc):
    """The docstring's leading paragraph, joined to one line.

    ⭐ WHY THIS EXISTS, AND WHY IT IS NOT `full`. The docstrings here are
    narrative -- they argue WHY, with dates and measurements -- and the API
    reference threw all of it away, publishing `first_line` alone. Measured
    2026-08-20 over the 372 modules and 2,366 public symbols the extractor
    covers: first_line is 105 K of text, first_para 199 K, the FULL bodies
    781 K (of which the module docstrings alone are 462 K).

    Full bodies are the right answer for a paginated backend and the wrong
    one here: the API reference is EIGHT area pages, so `full` would put
    ~400 K in hybrid_adc.md alone -- past the size where GitHub renders
    Markdown at all. A paragraph is the natural summary unit, nearly doubles
    what is published, and stays inside one list item because it is joined.

    ⚠ So the split is deliberate: `module_api` carries the FULL docstring
    and each backend decides how much to render. That seam is what lets the
    web and PDF manuals publish the whole narrative later without a second
    extractor -- and it is why this function lives beside first_line rather
    than replacing it.
    """
    if not doc:
        return ""
    out = []
    for ln in doc.strip().splitlines():
        if not ln.strip():
            break
        out.append(ln.strip())
    return " ".join(out)


def signature(node):
    """Render a def/async def signature from its AST without importing."""
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    ret = ""
    if node.returns is not None:
        try:
            ret = " -> " + ast.unparse(node.returns)
        except Exception:
            ret = ""
    return "{}({}){}".format(node.name, args, ret)


def module_api(path):
    """(module_doc, [public functions], [public classes]) via AST.

    Docstrings come out WHOLE. This is the model, not a rendering: the
    caller decides how much of each to publish (see `first_para`), so a
    backend that can paginate -- the web manual, the PDF -- can print the
    full narrative without a second extractor that would then be a second
    thing to keep in step.
    """
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None
    mdoc = (ast.get_docstring(tree) or "").strip()
    funcs, classes = [], []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not n.name.startswith("_"):
                funcs.append((signature(n),
                              (ast.get_docstring(n) or "").strip()))
        elif isinstance(n, ast.ClassDef):
            if n.name.startswith("_"):
                continue
            meths = []
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and (not m.name.startswith("_")
                             or m.name == "__init__"):
                    meths.append((signature(m),
                                  (ast.get_docstring(m) or "").strip()))
            classes.append((n.name, (ast.get_docstring(n) or "").strip(),
                            meths))
    return mdoc, funcs, classes


# ---- the model: everything that needs to know where the repo is ------

class DocModel(object):
    """One repo's documentation, parsed once.

    Construct with the repo root and the few facts that are genuinely local
    -- which trees are not hand-authored, where the index lives -- and every
    backend shares the resulting parse, link check and freshness gate.

    ⚠ `apply` is not a convenience. A `check` run must be READ-ONLY: when it
    is false, `write_if_changed` records drift and writes nothing, because a
    gate that REPAIRS the tree it is auditing passes on run 2 while the fix
    is never staged -- and CI only ever gets run 1.
    """

    def __init__(self, root, index=None, doc_exclude=(), core=None):
        self.root = root
        self.index = index
        self.doc_exclude = tuple(doc_exclude)
        self.core_path = core if core is not None else os.path.join(
            root, "policy", "docmeta.core.json")
        self.genres, self.aliases, self.required = self._load_core()
        self.dirty = []
        self.apply = True
        self._doc_paths = None
        self._tracked = False          # sentinel: not yet loaded

    # -- shared vocabulary ---------------------------------------------

    def _load_core(self):
        """(genres, aliases, required) from the vendored shared vocabulary.

        Falls back to the built-ins when the vendored core is absent or
        unreadable, so a generator still runs standalone in a checkout that
        has not adopted the flowkit -- the same property `doc_paths` keeps
        when git is unavailable. It does NOT fall back on a core that parses
        but is missing `genres`: that is a corrupt shared contract, not an
        absent one, and silently substituting a different vocabulary for it
        is how a gate starts passing for the wrong reason.
        """
        if not os.path.exists(self.core_path):
            return FALLBACK_GENRES, {}, FALLBACK_REQUIRED
        core = json.load(open(self.core_path, encoding="utf-8"))
        genres = tuple(g["id"] for g in core["genres"])
        aliases = dict(core.get("aliases") or {})
        required = tuple(core.get("required") or FALLBACK_REQUIRED)
        for a, target in aliases.items():
            if target not in genres:
                raise ValueError(
                    "docmeta.core.json: alias {!r} maps to {!r}, which is "
                    "not a genre".format(a, target))
        return genres, aliases, required

    def canon_genre(self, g):
        """The canonical genre id for a raw frontmatter value; '' if unknown.

        An alias is a spelling the gate accepts and folds in for GROUPING --
        it is not a second contract. The index still prints what the doc
        actually says; only the ordering and the per-area counts
        canonicalize, so a `howto` and a `guide` are not filed as two
        different things.
        """
        if not g:
            return ""
        if g in self.genres:
            return g
        return self.aliases.get(g, "")

    # -- discovery -----------------------------------------------------

    def rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def _keep_doc(self, p):
        """Is this .md a hand-authored doc? (shared by both enumerators)"""
        if any(x in p for x in self.doc_exclude):
            return False
        if self.index is None:
            return True
        return os.path.abspath(p) != os.path.abspath(self.index)

    def doc_paths(self):
        """Sorted absolute paths of every hand-authored .md. Cheapest first.

        `os.walk(root)` costs ~47 s on the WSL/NTFS checkout: 15.7 k files,
        of which 8.9 k are under docs/ alone -- the generated API pages and
        the vendor manuals that `doc_exclude` then throws away. Pruning the
        walk does NOT fix it (measured: 49.0 -> 47.7 s); the cost is
        per-entry stat, not the excluded subtrees.

        git already has the list. `ls-files --cached --others
        --exclude-standard` is tracked plus untracked-not-ignored, which is
        ~0.9 s and -- measured 2026-08-03 -- the SAME 153 docs, zero
        difference either way. It is also the better definition: a .md
        inside a gitignored work dir was never meant to be indexed, and
        `check_links` already treats untracked targets as dead. Falls back
        to the walk when git cannot answer (no repo, no git on PATH), so the
        generator still runs standalone.

        -z because the filter is by suffix, not by parse: git quotes paths
        with spaces or non-ASCII otherwise, and docs/ has both.

        CACHED, because a check run enumerates three times (build +
        check_links + check_frontmatter) and paid the walk for each. Only
        the LIST is cached; `iter_docs` re-reads every file on each pass,
        because a build with apply=True rewrites README.md's generated block
        and a later pass must see it.
        """
        if self._doc_paths is not None:
            return self._doc_paths
        out = None
        try:
            raw = subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others",
                 "--exclude-standard"], cwd=self.root)
            names = [n for n in raw.decode("utf-8", "replace").split("\0")
                     if n]
            out = [os.path.join(self.root, n.replace("/", os.sep))
                   for n in names if n.endswith(".md")]
        except Exception:
            out = None
        if out is None:                   # no git: walk, as before
            out = []
            for dp, dns, fns in os.walk(self.root):
                dns[:] = [d for d in dns if d not in (".git", "node_modules")]
                out += [os.path.join(dp, fn) for fn in fns
                        if fn.endswith(".md")]
        self._doc_paths = sorted(p for p in out if self._keep_doc(p))
        return self._doc_paths

    def iter_docs(self):
        """Yield (path, meta, body) for every hand-authored .md in the repo."""
        for p in self.doc_paths():
            try:
                text = open(p, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            meta, body = parse_frontmatter(text)
            yield p, meta, body

    def iter_py(self, globs):
        """Expand an area's globs to a sorted list of .py files."""
        out = set()
        for g in globs:
            full = os.path.join(self.root, g.replace("/", os.sep))
            if os.path.isfile(full) and full.endswith(".py"):
                out.add(full)
            elif os.path.isdir(full):
                for dp, dns, fns in os.walk(full):
                    dns[:] = [d for d in dns
                              if d not in (".git", "__pycache__")]
                    for fn in fns:
                        if fn.endswith(".py"):
                            out.add(os.path.join(dp, fn))
            else:
                for hit in _glob.glob(full):
                    if os.path.isdir(hit):
                        for dp, _, fns in os.walk(hit):
                            for fn in fns:
                                if fn.endswith(".py"):
                                    out.add(os.path.join(dp, fn))
                    elif hit.endswith(".py"):
                        out.add(hit)
        return sorted(out)

    def tracked(self):
        """(files, dirs) tracked by git, or None if git is unavailable. Link
        targets are validated against THIS -- not the local filesystem -- so
        a link to a gitignored/untracked artifact (which a fresh CI checkout
        will not have) fails locally exactly as it does in CI."""
        if self._tracked is not False:
            return self._tracked
        try:
            out = subprocess.check_output(["git", "ls-files"], cwd=self.root,
                                          universal_newlines=True)
        except Exception:
            self._tracked = None
            return None
        files, dirs = set(), set()
        for ln in out.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            files.add(ln)
            d = os.path.dirname(ln)
            while d:
                dirs.add(d)
                d = os.path.dirname(d)
        self._tracked = (files, dirs)
        return self._tracked

    # -- output --------------------------------------------------------

    def reset(self, apply=True):
        """Start a build pass: clear recorded drift, set read-only or not."""
        del self.dirty[:]
        self.apply = apply

    def write_if_changed(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old = open(path, encoding="utf-8").read() \
            if os.path.exists(path) else None
        if old == content:
            return False
        self.dirty.append(self.rel(path))
        if not self.apply:
            return True
        # always LF (repo convention, enforced by .gitattributes) so the
        # tool can't reintroduce CRLF when run on Windows
        open(path, "w", encoding="utf-8", newline="\n").write(content)
        return True

    # -- checks --------------------------------------------------------

    def check_links(self):
        """Dead internal-link check over hand docs. Returns list of problems."""
        bad = []
        tr = self.tracked()
        for p, _meta, body in self.iter_docs():
            for m in LINK_RE.finditer(body):
                tgt = m.group(1).split("#")[0].strip()
                if not tgt or tgt.startswith(("http://", "https://",
                                              "mailto:", "$")):
                    continue
                if tgt.startswith("<") or "${" in tgt:
                    continue
                # a real intra-repo link has a path shape (slash or
                # extension); a bareword like `out` in an ASCII signal
                # diagram -- e.g. `data[31:0](out)` -- is not a link.
                if "/" not in tgt and "." not in tgt:
                    continue
                dest = os.path.normpath(os.path.join(os.path.dirname(p), tgt))
                reldest = os.path.relpath(dest, self.root).replace(os.sep, "/")
                if reldest.startswith(".."):
                    continue                   # points outside the repo
                if tr is not None:
                    ok = reldest in tr[0] or reldest in tr[1]
                else:
                    ok = os.path.exists(dest)  # no git: best-effort
                if not ok:
                    bad.append("{}: dead link -> {} (untracked/missing)"
                               .format(self.rel(p), tgt))
        return bad

    def check_frontmatter(self):
        """Malformed frontmatter on docs that HAVE it (missing required field
        or unknown genre). Untagged docs only warn; so does an alias
        spelling, which is accepted and counted so the vocabulary can
        converge without failing a build over a synonym."""
        bad, warn, aliased = [], 0, {}
        for p, meta, _body in self.iter_docs():
            if not meta:
                warn += 1
                continue
            for f in self.required:
                if not meta.get(f):
                    bad.append("{}: frontmatter missing '{}'".format(
                        self.rel(p), f))
            g = meta.get("genre")
            if g and not self.canon_genre(g):
                bad.append("{}: unknown genre '{}' (want {})".format(
                    self.rel(p), g, "/".join(self.genres)))
            elif g and g not in self.genres:
                aliased[g] = aliased.get(g, 0) + 1
        bad += self._check_supersession()
        return bad, warn, aliased

    def _check_supersession(self):
        """⛔ A SUPERSEDED DOC MUST NAME ITS SUCCESSOR, AND THE PAIR MUST AGREE.

        The `decision` contract says a decision is never edited to reflect a
        new choice -- it is SUPERSEDED, and its own status becomes
        `superseded`. Nothing enforced the second half, and nothing recorded
        WHICH decision replaced it, so a reader had no way to tell a live
        decision from a dead one except by date.

        ⛔ THAT IS NOT HYPOTHETICAL. `POWER_PLAN.md` (decision, 2026-07-25,
        the v1 chip's mesh geometry) sat at `status: active` after its
        successor was accepted on 2026-09-04, and was then read as current
        guidance and used to derive a rule about the v6 chip. Two live
        decisions on one question is the defect; the date is not a status.

        Three rules, each checkable:
          * `status: superseded` requires `superseded_by:`
          * the target must exist
          * the target must not itself be superseded -- a pointer into a dead
            document is worse than none, because it reads as an answer
        And symmetrically, a `supersedes:` target must actually say it is
        superseded, so the pair cannot drift apart.
        """
        out = []
        meta_by_rel = {}
        for p, meta, _b in self.iter_docs():
            if meta:
                meta_by_rel[self.rel(p).replace("\\", "/")] = meta

        def _resolve(rel, target):
            t = str(target).strip().replace("\\", "/")
            if t in meta_by_rel:
                return t
            base = os.path.dirname(rel)
            j = os.path.normpath(os.path.join(base, t)).replace("\\", "/")
            return j if j in meta_by_rel else None

        for rel, meta in sorted(meta_by_rel.items()):
            st = str(meta.get("status", "")).strip()
            by = meta.get("superseded_by")
            if st == "superseded" and not by:
                out.append("{}: status is 'superseded' but no 'superseded_by:'"
                           " -- name the decision that replaced it".format(rel))
            if by:
                tgt = _resolve(rel, by)
                if tgt is None:
                    out.append("{}: superseded_by '{}' does not resolve to a "
                               "tagged doc".format(rel, by))
                elif str(meta_by_rel[tgt].get("status", "")) == "superseded":
                    out.append("{}: superseded_by '{}' is ITSELF superseded -- "
                               "a pointer into a dead document reads as an "
                               "answer".format(rel, by))
            sup = meta.get("supersedes")
            if sup:
                for one in (sup if isinstance(sup, list) else [sup]):
                    tgt = _resolve(rel, one)
                    if tgt is None:
                        out.append("{}: supersedes '{}' does not resolve"
                                   .format(rel, one))
                    elif str(meta_by_rel[tgt].get("status", "")) != "superseded":
                        out.append("{}: supersedes '{}', but that doc's status "
                                   "is '{}' -- both halves or neither"
                                   .format(rel, one,
                                           meta_by_rel[tgt].get("status")))
        return out

    def coverage(self):
        """(tagged, untagged, by_genre) -- the adoption number, for the
        conformance report.

        Exists so a repo that falls behind on documentation REPORTS A
        NUMBER rather than going quiet, which is the same reason
        `test_policy_conformance` prints the policy adoption gap instead of
        just passing or failing.
        """
        tagged = untagged = 0
        by_genre = {}
        for _p, meta, _body in self.iter_docs():
            if not meta:
                untagged += 1
                continue
            tagged += 1
            g = self.canon_genre(meta.get("genre")) or "?"
            by_genre[g] = by_genre.get(g, 0) + 1
        return tagged, untagged, by_genre
