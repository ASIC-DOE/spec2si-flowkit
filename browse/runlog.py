#!/usr/bin/env python3
"""The cross-session AGENT-LOOP occurrence log -- verify_log's counterpart.

`analog/specs/verify_log.jsonl` records what the TOOLS said: 206 DRC/LVS
verdicts, deck-hash partitioned, classified against one RULE_TABLE. There is
no equivalent for the loop that PRODUCED those verdicts. That history lives in
289 KB of RESUME.md prose, and the claims we make from it are the ones that
justify the whole two-tier gate architecture:

    "~10 offline iterations for zero cluster runs; six prior blind cluster
     attempts had failed."
    "via_plates reproduced lock_detector_1's last marker in milliseconds
     after three sessions of cluster round trips had failed to close it."

Both are quoted from memory. Neither sits in a file that can be counted. We
are running a long, expensive experiment in AI-driven design with no
experimental record of it (docs/sable_comparison.md 4.4).

WHAT THIS IS
    One record per ATTEMPT: a turn of the agent loop that touched a known
    cell. The skeleton is HARVESTED (how many actions, how long, how many
    failed, which files, offline or cluster) because the transcript is the
    one chokepoint written unconditionally -- the same reason agentview
    tails it instead of hooking PreToolUse. The TERMINAL CAUSE is DECLARED,
    from a fixed enumeration, because it is a judgement and inferring it
    would be inventing data.

    A harvested-but-unclassified attempt is not a gap in the log, it is a
    measured backlog: stats prints the count, so "we have not classified
    anything in three weeks" is visible instead of silent. Same contract as
    verify_log -- THE LOG DECIDES NOTHING. It counts.

=============================== NDA ========================================
Every field here is METADATA. The transcript is read only through
agentview's allowlist (`_SAFE_INPUT_KEYS`), so tool output, file contents,
commands and message bodies are never seen by this module either. Paths are
reduced to a cell name and basenames before they are written.

`note` is the one free-text field and it is agent-written, so it inherits the
repo's disclosure predicate (docs/threat_model.md I1): a rule IDENTIFIER may
be written down, a rule VALUE may not. `NOTE_MAX` caps it, and note_is_safe()
rejects the shape "identifier followed by a number" outright.
============================================================================

WHAT TRIGGERS A HARVEST
    Automatically: the SessionEnd hook in `.claude/settings.json` (once per
    Claude Code session in this repo), backed up by an optional post-commit
    hook for sessions whose cwd is elsewhere but which commit here
    (`--install-hook`). Both are best-effort and can never fail what they
    are attached to. Manually: `harvest`, any time.

⚠️⚠️ **THE TRANSCRIPTS ARE NOT DURABLE, AND THIS FILE USED TO SAY THEY
WERE.** It read "nothing is ever lost by not running it, because the
transcripts are the durable record and a harvest six months late still
recovers everything". That is FALSE: Claude Code deletes transcripts after
`cleanupPeriodDays`, which defaults to 30 and is unset here. Measured
2026-08-26 -- the oldest surviving tsmc65 transcript was 2026-07-28 while
the committed log reached back to 2026-07-10, so seven weeks of sessions
existed only in the log.

Two consequences, both paid for:

  * A REBUILD IS NOT SAFE. Acting on the old sentence, a rebuild-from-
    transcripts dropped 100 records naming real cells (`preamp` alone had
    24) and they were recoverable only because the log was committed.
    `retier` exists so a rule change can be re-derived for what is still
    reachable WITHOUT deleting what is not.
  * THE HARVEST HOOK IS LOAD-BEARING, not a convenience. When it was dead
    for 18 days in tsmc28 the gap happened to fall inside the retention
    window; a fortnight more and those attempts would have been gone for
    good. COMMIT the log -- it is the only copy past 30 days.

  python3 runlog.py harvest            # skeleton records from the transcripts
  python3 runlog.py stats              # roll-up + the offline:cluster ratio
  python3 runlog.py causes             # the enumeration, with meanings
  python3 runlog.py open               # attempts still unclassified
  python3 runlog.py sample [n] [seed]  # a stratified random sample to
                                       # classify INSTEAD of the census
  python3 runlog.py classify <id> <cause> [note]
  python3 runlog.py retier             # re-derive tier after a rule change
  python3 runlog.py --install-hook     # the post-commit backup trigger

Usage:
  python3 runlog.py classify <id> <cause> [note]
"""
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentview                                        # noqa: E402

#: The repo this harvest is FOR, which is not necessarily the checkout this
#: file sits in -- one implementation, located rather than copied, exactly as
#: `browse/launch.py` serves any repo through BROWSE_REPO.
#:
#: `AIML_ROOT` still works and is checked first, because the installed
#: SessionEnd hook uses it (the name predates the spec2si-* rename and is
#: kept only for that hook); `RUNLOG_REPO` is the name to use from here on,
#: since "AIML_ROOT=/…/spec2si-tsmc28" reads as a mistake.
#:
#: Resolved at IMPORT, like roots.REPO, so everything below agrees about which
#: repo it is describing. A hook must set the variable before importing.
ROOT = (os.environ.get("AIML_ROOT") or os.environ.get("RUNLOG_REPO")
        or os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..")))
SPECS = os.path.join(ROOT, "analog", "specs")
LOG = os.path.join(SPECS, "runlog.jsonl")
STATS = os.path.join(SPECS, "runlog_stats.json")

#: The terminal-cause enumeration. Fixed and small on purpose: SABLE 8 turns
#: 22 runs into four counted causes precisely because the set is closed, and
#: an open one degenerates into free text that cannot be summed. Every entry
#: here names a class this repo has actually paid for.
CAUSES = {
    "closed": "the goal was reached and a TRUSTED-side verdict says so "
              "(Calibre signoff, spec.evaluate, a gate that passed)",
    "gate-fail": "a gate ran and said no -- the good failure: real "
                 "violations, found where they were supposed to be found",
    "silent-pass": "a gate reported success against nothing: an empty GDS "
                   "from a bogus -layerMap, a deck run against a file "
                   "literally named GDSFILENAME, terminals that read as "
                   "finished because OUTN/OUTP were routed",
    "engine-defect": "our own code was wrong -- a constant that was correct "
                     "for the geometry that existed when it was written",
    "stale-artifact": "the thing a gate protects was lost, stale or "
                      "hand-edited, and the good path still passed "
                      "(the class `negative-control` exists for)",
    "tool-error": "the EDA tool itself failed: licence, crash, environment, "
                  "a PCell that would not evaluate",
    "transport": "ssh / sync / cluster / endpoint failure -- nothing to do "
                 "with the design (SABLE measures this at 1-in-11)",
    "abandoned": "stopped without a verdict: superseded, out of budget, or "
                 "the human went somewhere else",
    "unclassified": "harvested, not yet judged. Counted, never hidden",
}

#: The causes an AGENT may declare about its own attempt. Chosen 2026-08-26
#: as the answer to a real conflict of interest: declaring at the end of the
#: turn is the only way to stop the backlog growing (the actor is the one
#: who knows, and the harvested skeleton deliberately excludes tool output
#: and message bodies, so a later reader judges from LESS than the actor
#: had) -- but an agent grading its own turn will over-report `closed`.
#:
#: These three are MECHANICAL: they are about what the machinery did, not
#: about whether the work was any good, and each is checkable against the
#: skeleton afterwards. The judgement-heavy rest -- `closed`, `silent-pass`,
#: `abandoned`, `engine-defect`, `stale-artifact` -- stay human, because
#: those are exactly where a self-interested declarer would be wrong and
#: where being wrong corrupts the claim the log exists to support.
#:
#: Enforced in classify(), not documented: an agent that may declare
#: `closed` eventually will.
MECHANICAL_CAUSES = ("tool-error", "transport", "gate-fail")

#: Cap on the one free-text field. Long enough for a sentence, short enough
#: that a paragraph of report text cannot be pasted in.
NOTE_MAX = 140

#: A rule IDENTIFIER may be recorded; a rule VALUE may not. This is the
#: disclosure predicate as a regex: `M1.S.1 0.09` and `VIAx.R.2=0.05` are the
#: shape that must never be written down; `VIA3.EN.1 again, riser past the
#: track end` is fine and is most of what anyone actually wants to write.
#:
#: Two details, both learned by getting them wrong: the value must be
#: SEPARATED from the identifier (`\s` or `=`/`:`), or the identifier's own
#: trailing `.1` reads as a fractional value and every note gets redacted;
#: and only a FRACTIONAL number counts, because thresholds here are
#: fractional um while "across 44 runs" is a count and must stay writable.
_RULE_VALUE = re.compile(
    r"\b[A-Z]{1,5}[0-9x]*(?:\.[A-Za-z0-9]{1,4}){1,3}"    # a rule identifier
    r"(?:\s*[:=]\s*|\s+)"                                # separated from
    r"[^0-9\n]{0,10}"                                    # ... and close to
    r"[-+]?\d*\.\d+")                                    # a fractional value

#: Directories whose immediate children are cells. Read from the tree, not
#: enumerated here: the roster has to grow when the library does, or the log
#: quietly stops counting the newest work -- which is the failure mode it
#: exists to prevent.
#: Extra cell homes beyond the discovered ones. `analog/lib/<pkg>` is found by
#: looking rather than by naming, because the package is per repo
#: (`aiml_analog` here, `onr_analog` next door) and a hard-coded name is
#: exactly what stopped this file working for any repo but its own.
_CELL_HOMES = (
    ("hybrid_adc", "control_layout", "lib", "aiml_control"),
    ("hybrid_adc", "lib"),
)

#: A repo with no cell LIBRARY can still have blocks worth counting -- XT011's
#: are pad rings and clock generators, named in its flow rather than laid out
#: one directory per cell. One name per line; blank lines and `#` ignored.
#: Without it that repo harvests an empty roster and records nothing, which is
#: honest and useless.
#:
#: ⚠️ A NAME IS NOT ENOUGH FOR CHIP-LEVEL WORK, and that is where a port's
#: busiest sessions go. Measured 2026-09-12 on xt011: 618 turns since
#: 2026-09-03, 603 of them matching no roster name -- the die's channel
#: routing, its pin frames and its LVS repairs live in `analog/layout/`,
#: `chip/floorplan/` and `tech/probes/`, flow-level files whose paths name
#: no cell at all. The harvest had been running on every session end and
#: recording nothing, which read as a dead hook. So the file also takes:
#:
#:     <cell> = <glob>, <glob>, ...      a PATH RULE: any touched path that
#:                                       matches (repo-relative, forward
#:                                       slashes, fnmatch) is an attempt at
#:                                       <cell>; the name joins the roster
#:     @former-root <path>               a directory this repo USED TO BE
#:                                       checked out at (ADR-0001 renamed all
#:                                       five on 2026-08-24), so a transcript
#:                                       from before the rename still
#:                                       relativises against it
#:
#: Rules are tried after the component/stem match, in file order, first
#: match wins. The design record (`design/<lib>/<cell>/cell.json`) is a
#: roster too, and a view's tracked ORIGIN file attributes to its cell
#: without anyone declaring it -- see `rules_for`.
_DECLARED_CELLS = ("analog", "specs", "runlog_cells.txt")
#: The design record: one directory per library, one per cell under it.
_DESIGN_DIR = "design"

#: Names that are directories in a cell home but are not cells.
#:
#: ⚠️ THE SECOND GROUP IS THE VIEW-PER-CELL SHAPE, and leaving it out made
#: this file record the wrong noun entirely. sky130 lays one cell out as
#: `analog/lib/sky130_ota6/{layout,schematic,simulation,render}` -- so
#: `sky130_ota6` IS the cell and `analog/lib` holds exactly one, where
#: tsmc28 and tsmc65 put a PACKAGE there (`onr_analog/<cell>`,
#: `aiml_analog/<cell>`). Discovered against the package shape, sky130's
#: first harvest attributed 26 attempts to cells named "layout" and
#: "schematic" (2026-08-26). A repo with the view-per-cell shape declares
#: its cells in runlog_cells.txt; these names are never cells anywhere.
_NOT_CELLS = {"const", "common", "docs", "results", "work", "analysis",
              "spec", "lib", "__pycache__",
              "layout", "schematic", "simulation", "render", "sim",
              "bench", "veriloga", "tests", "test"}

#: Signals that an attempt reached the cluster. Heuristic, and recorded as
#: such: `tier_basis` names which one fired, so a wrong call is auditable
#: instead of just wrong.
#:
#: ⚠️⚠️ **TIGHTENED 2026-08-26, AND THE TWO HINTS REMOVED WERE CARRYING 110
#: OF 162 CLUSTER TAGS.** The list held the bare substrings `cluster` and
#: `deployment/`, which match a great deal that never leaves the laptop:
#: `CLUSTER.md`, `cluster-access-this-machine.md`, `deployment/bnl/push.sh`,
#: and any action whose summary merely says the word. A file UNDER
#: `deployment/` is a script ABOUT the cluster; editing it is not a round
#: trip.
#:
#: The cost was precision on the only claim this log exists to test.
#: docs/runlog_first_analysis.md measured "~10 offline iterations per
#: cluster run" at anywhere from 2.18 to 8.76 offline turns per round trip
#: depending solely on whether those two counted -- a factor of four,
#: bracketing the claim, and unnarrowable by collecting more data.
#:
#: A regex now, not substrings, so a host is matched as a TOKEN. That also
#: fixes a second miss: only `asic6`/`asic7` were listed, while the cluster
#: is `asic1`..`asic10` plus `exxact` and `dgx-spark`, so sky130's Pegasus
#: work on `asic9` was scored offline.
#:
#: ⚠️ `pmos` and `asicdesign` are cluster hosts and are deliberately NOT
#: matched bare: `pmos` is a transistor type and appears in nearly every
#: analog file in these repos. They count only behind a transport verb or
#: the site domain.
_CLUSTER_RE = re.compile(
    r"\bssh\s|\bscp\s|\brsync\s"                    # a transport verb
    r"|\basic(?:10|[1-9])\b"                        # the compute hosts
    r"|\bexxact\b|\bdgx-spark\b"
    r"|\.inst\.bnl\.gov\b"                          # the site domain
    r"|\b(?:calibre|pegasus|virtuoso|spectre|innovus|genus|strmout|qrc)\b"
    r"|sync[/\\](?:push|pull)")


def transcript_home(cwd=None):
    """Where `.claude/projects/` lives, or None for the default.

    Under WSL `expanduser("~")` is /home/<user> and the transcripts are on
    /mnt/c, so a harvest there finds nothing. Rather than hard-code a
    personal path into a committed hook, find the Windows home that
    actually holds THIS project's transcript directory -- the check
    validates the guess instead of trusting it. CLAUDE_HOME overrides."""
    env = os.environ.get("CLAUDE_HOME")
    if env:
        return env
    slug = agentview.project_slug(cwd or transcript_cwd())
    if os.path.isdir(os.path.join(os.path.expanduser("~"), ".claude",
                                  "projects", slug)):
        return None                      # the default home is correct
    users = "/mnt/c/Users"
    if os.path.isdir(users):
        for name in sorted(os.listdir(users)):
            home = os.path.join(users, name)
            if os.path.isdir(os.path.join(home, ".claude", "projects", slug)):
                return home
    return None


def transcript_cwd(root=None):
    """The cwd Claude Code slugged when it named the transcript directory --
    always the WINDOWS path, because that is where the agent runs. Under WSL
    this file's own root is `/mnt/c/dev/spec2si-tsmc65`, which slugs to
    `-mnt-c-dev-spec2si-tsmc65` and finds nothing; the transcripts are
    under `C--dev-spec2si-tsmc65`. Harvesting silently returned zero until this existed.
    Override with CLAUDE_CWD."""
    env = os.environ.get("CLAUDE_CWD")
    if env:
        return env
    p = (root or ROOT).replace("\\", "/")
    m = re.match(r"^/mnt/([a-z])/(.*)$", p)
    if m:
        return "{}:\\{}".format(m.group(1).upper(),
                                m.group(2).replace("/", "\\"))
    return root or ROOT


def _base(p):
    """Basename, on a Windows path read from Linux too. os.path.basename
    does not split on `\\` under WSL, so a record harvested there kept the
    FULL path -- the one thing this module promises never to write down.
    The transcripts are written on Windows and this runs on both."""
    return re.split(r"[\\/]", (p or "").rstrip("\\/"))[-1]


def cell_homes(root=None):
    """[abspath] of directories whose children are cells, for THIS repo.

    `analog/lib/<pkg>` is DISCOVERED rather than named: the package differs per
    repo (`aiml_analog`, `onr_analog`) and hard-coding one is what confined
    this file to a single checkout.
    """
    root = root or ROOT
    out = []
    lib = os.path.join(root, "analog", "lib")
    if os.path.isdir(lib):
        for n in sorted(os.listdir(lib)):
            d = os.path.join(lib, n)
            if os.path.isdir(d) and not n.startswith("."):
                out.append(d)
    for rel in _CELL_HOMES:
        d = os.path.join(root, *rel)
        if os.path.isdir(d):
            out.append(d)
    return out


def _declared_lines(root=None):
    """The meaningful lines of runlog_cells.txt, comments stripped."""
    p = os.path.join(root or ROOT, *_DECLARED_CELLS)
    out = []
    try:
        with open(p, encoding="utf-8") as fh:
            for ln in fh:
                s = ln.split("#", 1)[0].strip()
                if s:
                    out.append(s)
    except OSError:
        pass
    return out


def declared_cells(root=None):
    """Cell names a repo states outright, for repos with no cell library.

    A path-rule line (`cell = glob, ...`) contributes its CELL; a
    `@former-root` line contributes nothing here."""
    out = set()
    for s in _declared_lines(root):
        if s.startswith("@"):
            continue
        name = s.split("=", 1)[0].strip()
        if name:
            out.add(name)
    return out


def declared_rules(root=None):
    """([(cell, [glob, ...])], [former_root, ...]) from runlog_cells.txt."""
    rules, former = [], []
    for s in _declared_lines(root):
        if s.startswith("@former-root"):
            v = s[len("@former-root"):].strip()
            if v:
                former.append(v)
        elif "=" in s:
            name, _, globs = s.partition("=")
            globs = [g.strip().replace("\\", "/") for g in globs.split(",")]
            globs = [g for g in globs if g]
            if name.strip() and globs:
                rules.append((name.strip(), globs))
    return rules, former


def record_cells(root=None):
    """{cell: library} from the design record. Since 2026-09-12 every port
    has one, and it names exactly the cells the flow has published, in the
    spelling their files carry -- a roster nobody has to maintain."""
    base = os.path.join(root or ROOT, _DESIGN_DIR)
    out = {}
    try:
        libs = sorted(os.listdir(base))
    except OSError:
        return out
    for lib in libs:
        ld = os.path.join(base, lib)
        if not os.path.isdir(ld) or lib.startswith("."):
            continue
        try:
            cells_ = sorted(os.listdir(ld))
        except OSError:
            continue
        for c in cells_:
            if os.path.isfile(os.path.join(ld, c, "cell.json")):
                out[c] = lib
    return out


def record_origins(root=None):
    """{repo-relative origin path: cell} for every view file the record
    captured from a TRACKED file. A turn that edits the file a view was
    captured from is an attempt at that cell, by the record's own word."""
    base = os.path.join(root or ROOT, _DESIGN_DIR)
    out = {}
    for c, lib in record_cells(root).items():
        p = os.path.join(base, lib, c, "cell.json")
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        views = d.get("views") if isinstance(d, dict) else None
        if not isinstance(views, dict):
            continue
        for v in views.values():
            files = v.get("files") if isinstance(v, dict) else None
            for f in files or []:
                o = f.get("origin") if isinstance(f, dict) else None
                if (isinstance(o, str) and o and not o.startswith("/")
                        and not (len(o) > 1 and o[1] == ":")):
                    out.setdefault(o.replace("\\", "/"), c)
    return out


class Rules(object):
    """What attributes a touched PATH to a cell, beyond its components: the
    record's origins (exact) and the declared globs (in order), each tried
    against the path relativised to this repo's root or a former one."""

    def __init__(self, root, former, origins, globs):
        self.root = root
        self.roots = [root] + list(former)
        self.origins = origins
        self.globs = globs

    def relativise(self, p):
        q = (p or "").replace("\\", "/")
        low = q.lower()
        for r in self.roots:
            rr = r.replace("\\", "/").rstrip("/").lower() + "/"
            if low.startswith(rr):
                return q[len(rr):]
        return q

    def cell_for(self, paths):
        import fnmatch
        rel = [self.relativise(p) for p in paths]
        for q in rel:
            if q in self.origins:
                return self.origins[q]
        for cell, globs in self.globs:
            for q in rel:
                for g in globs:
                    if fnmatch.fnmatchcase(q, g):
                        return cell
        return None


_RULES_CACHE = {}


def rules_for(root=None):
    """The `Rules` for a repo, cached per root for the life of a harvest."""
    root = root or ROOT
    if root not in _RULES_CACHE:
        globs, former = declared_rules(root)
        _RULES_CACHE[root] = Rules(root, former, record_origins(root), globs)
    return _RULES_CACHE[root]


#: flow_designs() walks three directories, and it is called once per
#: RECORD. Cached per root: a harvest writes hundreds of records and the
#: tree does not change underneath one.
_FLOW_CACHE = {}


def flow_designs(root=None):
    """{name: 'digital'|'mixed'} for designs OUTSIDE the analog cell library.

    ⚠️ **THE ROSTER REACHED ONLY `analog/lib`, SO HALF THE LOOP WAS
    INVISIBLE.** Measured 2026-08-26: tsmc28's `dig_flows/` held 7
    directories and tsmc65's 16, with mixed_signal on top, and NOT ONE was a
    known cell -- so every turn spent on a P&R flow or an AMS testcase was
    scanned, found to touch no cell, and dropped. It landed in the turns
    denominator and never in the numerator, which reads as "the agent does
    not work on digital" rather than "the log cannot see it".

    Discovered rather than listed, for the reason `cell_homes` gives: a hand
    list stops counting the newest work the day someone adds a flow.

    DIGITAL -- a `dig_flows/` child with `flow` as an underscore-delimited
    token. That is this tree's own naming (`sar_flow`, `sar_flow_or`,
    `dec2s_flow_work`, `aiml65p2_chip_flow`) and it excludes the
    infrastructure beside them without a maintained denylist: `steps`,
    `steps_or`, `tools`, `resources`, `sync`, and `hep-digital-flow-*`
    (hyphens, so no `flow` TOKEN). `dig_tools_dig_flow_16.x_17.x` is the one
    name that satisfies the token test and is still a tool kit, so it is
    excluded by prefix.

    MIXED -- `mixed_signal/results/<name>/` and the stems of
    `mixed_signal/flow/testcases/*.py`. Both name the same designs, and
    having both means a testcase that has not been run yet still counts.

    A flow and its run area (`dec2s_flow`, `dec2s_flow_work`) stay SEPARATE
    names on purpose: they are different directories and the record's
    `files` says which was touched. Merging them would be a guess about
    identity, and this module does not guess -- see `cause`.
    """
    root = root or ROOT
    if root in _FLOW_CACHE:
        return _FLOW_CACHE[root]
    out = {}
    d = os.path.join(root, "dig_flows")
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if not os.path.isdir(os.path.join(d, n)) or n.startswith("."):
                continue
            if n.startswith("dig_tools"):
                continue
            if "flow" in n.split("_"):
                out[n] = "digital"
    ms = os.path.join(root, "mixed_signal")
    res = os.path.join(ms, "results")
    if os.path.isdir(res):
        for n in sorted(os.listdir(res)):
            if os.path.isdir(os.path.join(res, n)) and not n.startswith("."):
                out.setdefault(n, "mixed")
    tc = os.path.join(ms, "flow", "testcases")
    if os.path.isdir(tc):
        for n in sorted(os.listdir(tc)):
            if not n.endswith(".py") or n.startswith(("test_", "__")):
                continue
            out.setdefault(n[:-3], "mixed")
    _FLOW_CACHE[root] = out
    return out


def domain_of(cell, root=None):
    """'analog' | 'digital' | 'mixed' for a roster name.

    Kept as a derived lookup rather than a stored roster so an existing log
    re-reads correctly: records written before this field existed are analog
    by construction, because nothing else could ever match."""
    return flow_designs(root).get(cell, "analog")


def cells(root=None):
    """The known cell roster, from the tree rather than a hand list."""
    root = root or ROOT
    out = set()
    for d in cell_homes(root):
        for n in os.listdir(d):
            if (n not in _NOT_CELLS and not n.startswith(".")
                    and os.path.isdir(os.path.join(d, n))):
                out.add(n)
    out |= set(flow_designs(root))
    out |= declared_cells(root)
    out |= set(record_cells(root))
    return out


def cell_of(paths, roster, rules=None):
    """The cell an attempt was about, or None.

    Any path COMPONENT that names a cell wins, which is what actually works
    across the shapes in this tree: `lib/aiml_analog/vref/...`,
    `work/vref_lvs/...`, `control_layout/lib/aiml_control/clk_eoc/...`, and
    `.../strongarm.py`. A directory match beats a filename stem, and a turn
    that touched two cells is recorded against the first -- a known
    approximation, and the reason `files` is kept beside it.

    Then the `rules` (rules_for): a view's tracked origin file, and the
    port's declared path globs -- the only way chip-level work, whose paths
    name no cell, gets counted at all."""
    stems = []
    for p in paths:
        parts = [q for q in re.split(r"[\\/]", p or "") if q]
        for q in parts[:-1]:
            if q in roster:
                return q
        if parts:
            stems.append(parts[-1].split(".")[0])
    for s in stems:
        if s in roster:
            return s
    if rules is not None:
        return rules.cell_for(paths)
    return None


def _tier(actions):
    """('offline'|'cluster', basis, evidence). The claim this log exists to
    measure is 'N offline iterations for zero cluster runs', so which tier
    an attempt ran in is the single most load-bearing derived field.

    ⚠️ **THE EVIDENCE IS RETURNED BECAUSE 110 TAGS COULD NOT BE AUDITED.**
    The old version recorded only which hint fired, and the hint that fired
    most (`cluster`) matched action SUMMARIES, which this module never
    stores -- so two thirds of the cluster tags rested on a string nobody
    could look at afterwards. `tier_evidence` is the basename of the action
    that matched, which is the same reduction `files` already ships under
    the NDA allowlist, and it makes a wrong tag findable instead of merely
    suspected."""
    for a in actions:
        hay = " ".join(filter(None, (a.get("path"), a.get("summary")))).lower()
        m = _CLUSTER_RE.search(hay)
        if m:
            return "cluster", m.group(0).strip(), _base(a.get("path")) or ""
    return "offline", "no cluster signal", ""


def note_is_safe(note):
    """False if `note` carries a number attributed to a rule (I1)."""
    return not bool(_RULE_VALUE.search(note or ""))


def _clean_note(note):
    note = " ".join((note or "").split())[:NOTE_MAX]
    return note if note_is_safe(note) else "[redacted: rule value in note]"


# ---- the log ---------------------------------------------------------

def all_records(path=None):
    out = []
    p = path or LOG
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        pass
    return out


def current():
    """{id: record} with LAST-WINS. The log is append-only; classifying an
    attempt appends a new revision of it rather than rewriting history, so
    the file stays an audit trail and the roll-up stays current."""
    out = {}
    for r in all_records():
        if r.get("id"):
            out[r["id"]] = r
    return out


def _aid(session, started, cell):
    key = "|".join([str(session), str(started), str(cell)])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def append(rec):
    os.makedirs(SPECS, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def classify(aid, cause, note="", when=None, by=None):
    """Declare an attempt's terminal cause. Appends a revision.

    `by` records WHO declared it -- "human" by default, or an agent
    identity. That is the `model-is-a-variable` rule applied to this log:
    if the actor ever declares causes for its own attempts, the record has
    an obvious conflict of interest (an agent grading its own turn will
    call it `closed`), and the only way that stays analysable is if the two
    populations can be separated afterwards. A field costs nothing now and
    cannot be reconstructed later."""
    if cause not in CAUSES:
        raise ValueError("unknown cause {!r} -- one of: {}".format(
            cause, ", ".join(sorted(CAUSES))))
    who = by or os.environ.get("RUNLOG_DECLARED_BY") or "human"
    if who != "human" and cause not in MECHANICAL_CAUSES:
        raise ValueError(
            "{!r} may declare only {} -- {!r} is a judgement about whether "
            "the work was good, and an actor grading its own turn is not the "
            "one to make it. Leave it `unclassified` for a human; the log "
            "counts that and never hides it.".format(
                who, "/".join(MECHANICAL_CAUSES), cause))
    cur = current().get(aid)
    if cur is None:
        raise KeyError("no attempt {!r} in the log".format(aid))
    rec = dict(cur)
    rec["cause"] = cause
    rec["note"] = _clean_note(note) or cur.get("note", "")
    rec["classified"] = when or time.strftime("%Y-%m-%d %H:%M:%S")
    rec["source"] = "declared"
    rec["declared_by"] = who
    return append(rec)


# ---- harvest ---------------------------------------------------------

#: The panel's TAIL_BUDGET (2 MB) exists so a poll stays cheap while the
#: agent is working. A harvest is a batch job over history and wants the
#: OPPOSITE trade: seeding at the tail budget saw 105 turns across 22
#: sessions and found 4 attempts, because it was reading roughly the last
#: tenth of each session. Reading whole files costs seconds, once.
FULL_BUDGET = 1 << 40


def harvest_session(path, session_id, roster, seen, budget=None,
                    _count=False):
    """One transcript -> provisional attempt records. Metadata only: the
    events come from agentview, which never reads a message body.

    With `_count`, also returns (turns scanned, attempts matched). Both
    matter and they are different numbers: turns is the DENOMINATOR, and
    matched counts every attempt found -- including ones already in the log
    -- so a re-harvest reports the same 10 attempts rather than 0 new ones
    and quietly zeroing the denominator's partner."""
    events = agentview.read_events(path, budget or FULL_BUDGET)
    if not events:
        return ([], 0, 0) if _count else []
    # agentview's `why` string is built for the panel, which renders on
    # Windows where basename() splits a Windows path. Read under WSL it does
    # not, and the full path went straight into the record -- so the text is
    # rebuilt here from _base() rather than copied.
    thrash = {t.get("path"): "{}{} x{} on {}".format(
        t["kind"], "" if t.get("tool") is None else " " + t["tool"],
        t["n"], _base(t.get("path"))) for t in agentview.thrash(events)}
    out = []
    matched = 0
    turns = agentview.turns(events, limit=10 ** 6)
    rules = rules_for()
    for t in turns:
        paths = [p for p in t["files"] if p]
        cell = cell_of(paths, roster, rules)
        if cell is None:
            continue                     # not an attempt at a known cell
        aid = _aid(session_id, t["started"], cell)
        matched += 1
        if aid in seen:
            continue
        tier, basis, evidence = _tier(t["actions"])
        tools = {}
        for a in t["actions"]:
            if a.get("tool"):
                tools[a["tool"]] = tools.get(a["tool"], 0) + 1
        out.append({
            "id": aid,
            "when": t["started"],
            "session": session_id[:8],
            "cell": cell,
            "domain": domain_of(cell),
            "tier": tier,
            "tier_basis": basis,
            "tier_evidence": evidence,
            "cause": "unclassified",
            "note": "",
            "actions": t["n"],
            "errors": t["errors"],
            "dur_s": t["dur_s"],
            "tools": sorted(tools.items(), key=lambda kv: -kv[1])[:6],
            "files": sorted({_base(p) for p in paths})[:8],
            "thrash": sorted({v for k, v in thrash.items()
                              if k in paths})[:4],
            "partial": bool(t.get("partial")),
            "source": "harvest",
        })
        seen.add(aid)
    return (out, len(turns), matched) if _count else out


def transcript_cwds(root=None):
    """Every cwd whose transcripts can contain work on THIS repo.

    The repo's own slug, and its PARENT directory's. A session started in
    `C:\\dev` that edits `C:\\dev\\spec2si-tsmc65` all day is filed under `C--dev`,
    and harvesting only the repo's own slug missed every one of them --
    measured at 126 attempts on AIML cells sitting in `C--dev` alone, against
    37 recorded in total.

    ⚠️ NOT `os.path.dirname`. These are WINDOWS paths and the harvest runs
    under WSL, where a backslash is an ordinary character -- so dirname
    returned "" and the parent leg silently vanished. Caught only because the
    per-directory `dirs` breakdown made a missing leg visible; the record
    count alone looked plausible. Same hazard `_base` above exists for.
    """
    own = transcript_cwd(root)
    q = own.replace("/", "\\").rstrip("\\")
    parent = q.rsplit("\\", 1)[0] if "\\" in q else ""
    out = [own]
    # "C:" is a drive root, not a project -- slugging it would sweep in every
    # checkout on the machine
    if parent and parent != own and "\\" in parent:
        out.append(parent)
    return out


#: Scan EVERY session by default. It was 25, which is not a lot when the newest
#: project directory holds 52 -- and nothing ever looks back, so an attempt
#: that falls out of the window is lost for good. Measured at 155 findable
#: attempts behind the old ceiling. A full scan of all 52 takes 2.56 s against
#: the hook's 60 s timeout, so the ceiling was buying nothing.
DEFAULT_LIMIT = None


def harvest(cwd=None, limit=DEFAULT_LIMIT, budget=None, home=None):
    """Every session for this project -> new attempt records appended.

    `home` is where `.claude/projects/` lives; it defaults to whichever home
    actually holds this project's transcripts (see transcript_home)."""
    roster = cells()
    seen = set(current())
    scan = {"sessions": 0, "turns": 0, "attempts": 0, "new": 0,
            "budget": budget or FULL_BUDGET, "cells_known": len(roster),
            "dirs": []}
    cwds = [cwd] if cwd else transcript_cwds()
    for one in cwds:
        h = home or transcript_home(one)
        n_before = scan["sessions"]
        for s in agentview.sessions(one, home=h, limit=limit,
                                    identify=False):
            scan["sessions"] += 1
            recs, turns, matched = harvest_session(s["path"], s["id"], roster,
                                                   seen, budget, _count=True)
            scan["turns"] += turns
            scan["attempts"] += matched
            for rec in recs:
                append(rec)
                scan["new"] += 1
        # recorded per directory, so "we scanned nothing there" is visible
        # rather than indistinguishable from "there was nothing to find"
        scan["dirs"].append({"cwd": one,
                             "slug": agentview.project_slug(one),
                             "sessions": scan["sessions"] - n_before})
    return scan


def retier(cwd=None, limit=DEFAULT_LIMIT, budget=None, home=None):
    """Re-derive `tier` for attempts ALREADY in the log, from the transcripts.

    ⚠️ **A RULE CHANGE THAT ONLY APPLIES GOING FORWARD LEAVES THE CLAIM
    UNRESOLVED.** `_CLUSTER_RE` was tightened on 2026-08-26 because the two
    substrings removed carried 110 of 162 cluster tags, and the whole point
    was to collapse a factor-of-four band on "offline iterations per cluster
    round trip". Applied only to new records, the 343 already harvested
    would keep the loose tag forever and the band would never close --
    which is the same shape as a checker that goes vacuous when its input
    changes.

    Re-derived rather than edited: the transcripts are the durable record,
    so the tier is recomputed from the same actions the original harvest
    saw. A change is APPENDED as a revision, like a classification, so the
    old tag stays readable and the log remains append-only. `cause` and
    `note` are carried across untouched -- this re-reads a derived field
    and never a declared one.
    """
    roster = cells()
    cur = current()
    out = {"scanned": 0, "changed": 0, "to_offline": 0, "to_cluster": 0}
    for one in ([cwd] if cwd else transcript_cwds()):
        h = home or transcript_home(one)
        for s in agentview.sessions(one, home=h, limit=limit, identify=False):
            events = agentview.read_events(s["path"], budget or FULL_BUDGET)
            if not events:
                continue
            for t in agentview.turns(events, limit=10 ** 6):
                cell = cell_of([p for p in t["files"] if p], roster,
                               rules_for())
                if cell is None:
                    continue
                rec = cur.get(_aid(s["id"], t["started"], cell))
                if rec is None:
                    continue
                out["scanned"] += 1
                tier, basis, ev = _tier(t["actions"])
                if (tier == rec.get("tier")
                        and basis == rec.get("tier_basis")
                        and ev == rec.get("tier_evidence", "")):
                    continue
                new = dict(rec)
                new["tier"], new["tier_basis"], new["tier_evidence"] = (
                    tier, basis, ev)
                new["source"] = "retier"
                new["retiered"] = time.strftime("%Y-%m-%d %H:%M:%S")
                append(new)
                out["changed"] += 1
                if tier != rec.get("tier"):
                    out["to_" + tier] += 1
    return out


# ---- roll-up ---------------------------------------------------------

def _prev_scan():
    try:
        with open(STATS, encoding="utf-8") as fh:
            return json.load(fh).get("harvest")
    except (OSError, ValueError):
        return None


def rebuild_stats(scan=None):
    recs = list(current().values())
    by_cause, by_tier, by_cell, by_domain = {}, {}, {}, {}
    for r in recs:
        by_cause[r["cause"]] = by_cause.get(r["cause"], 0) + 1
        by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + 1
        # ⚠️ THE OFFLINE:CLUSTER RATIO IS NOT COMPARABLE ACROSS DOMAINS, and
        # rolling them together would quietly corrupt the one claim this log
        # exists to support. An analog layout attempt can iterate offline
        # against the audit gates all day; a P&R flow attempt runs Innovus
        # or Genus, so it is cluster almost by definition. Pooled, a busy
        # digital week reads as "the offline gates stopped working".
        # A record written before this field existed is analog by
        # construction -- nothing else could match the roster then.
        dom = r.get("domain") or "analog"
        d = by_domain.setdefault(dom, {"attempts": 0, "offline": 0,
                                       "cluster": 0, "unclassified": 0})
        d["attempts"] += 1
        d[r["tier"]] = d.get(r["tier"], 0) + 1
        if r["cause"] == "unclassified":
            d["unclassified"] += 1
        c = by_cell.setdefault(r["cell"], {
            "attempts": 0, "offline": 0, "cluster": 0, "actions": 0,
            "errors": 0, "unclassified": 0, "causes": {}})
        c["attempts"] += 1
        c[r["tier"]] = c.get(r["tier"], 0) + 1
        c["actions"] += r.get("actions", 0)
        c["errors"] += r.get("errors", 0)
        c["causes"][r["cause"]] = c["causes"].get(r["cause"], 0) + 1
        if r["cause"] == "unclassified":
            c["unclassified"] += 1
    # THE CLAIM: offline iterations per cluster round trip, per cell. The
    # thing every "we caught it offline" statement in RESUME.md asserts and
    # none of them counts. A cell with no cluster attempt reports null, not
    # infinity -- an unbounded ratio is not evidence.
    for c in by_cell.values():
        c["offline_per_cluster"] = (round(c["offline"] / c["cluster"], 2)
                                    if c["cluster"] else None)
    for d in by_domain.values():
        d["offline_per_cluster"] = (round(d["offline"] / d["cluster"], 2)
                                    if d.get("cluster") else None)
    stats = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_attempts": len(recs),
        "unclassified": by_cause.get("unclassified", 0),
        "by_cause": by_cause,
        "by_tier": by_tier,
        "by_domain": by_domain,
        "by_cell": by_cell,
        # the DENOMINATOR: how many turns were scanned to find these
        # attempts. Without it "31 attempts" is a number with no scale.
        "harvest": scan if scan is not None else _prev_scan(),
        "caveat": "one attempt = one agent turn that touched a known cell; "
                  "turns that touched none are scanned and not recorded, so "
                  "`harvest.turns` is the denominator. `partial` marks a "
                  "turn a bounded read began inside. Counts are a floor, "
                  "never a total.",
    }
    # Only write when something actually CHANGED. `generated` is a
    # timestamp, so an unconditional write makes every harvest dirty the
    # tree -- and the post-commit trigger runs after every commit, which
    # would leave a modified file sitting in `git status` forever. The
    # timestamp is excluded from the comparison and carried forward.
    try:
        with open(STATS, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = None
    if old is not None:
        a = {k: v for k, v in old.items() if k != "generated"}
        b = {k: v for k, v in stats.items() if k != "generated"}
        if a == b:
            return old
    os.makedirs(SPECS, exist_ok=True)
    with open(STATS, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=1)
    return stats


def report(stats=None):
    s = stats or rebuild_stats()
    h = s.get("harvest") or {}
    L = ["runlog: {} attempt(s), {} unclassified{}".format(
        s["n_attempts"], s["unclassified"],
        "  (from {} turn(s) across {} session(s))".format(
            h["turns"], h["sessions"]) if h.get("turns") else "")]
    L.append("  tier: " + ", ".join("{} {}".format(v, k)
                                    for k, v in sorted(s["by_tier"].items())))
    if len(s.get("by_domain") or {}) > 1:
        L.append("  domain (the ratio is NOT comparable across these):")
        for dom, d in sorted((s["by_domain"]).items(),
                             key=lambda kv: -kv[1]["attempts"]):
            L.append("    {:9s} {:3d} attempt(s)  {:3d} offline / {:3d}"
                     " cluster  ratio {:>5}".format(
                         dom, d["attempts"], d.get("offline", 0),
                         d.get("cluster", 0),
                         "-" if d.get("offline_per_cluster") is None
                         else d["offline_per_cluster"]))
    L.append("  cause:")
    for k, v in sorted(s["by_cause"].items(), key=lambda kv: -kv[1]):
        L.append("    {:16s} {}".format(k, v))
    L.append("  per cell (offline:cluster is THE claim):")
    for cell, c in sorted(s["by_cell"].items(),
                          key=lambda kv: -kv[1]["attempts"]):
        L.append("    {:16s} {:3d} attempt(s)  {:3d} offline / {:3d} cluster"
                 "  ratio {:>5}  {} unclassified".format(
                     cell, c["attempts"], c["offline"], c["cluster"],
                     c["offline_per_cluster"]
                     if c["offline_per_cluster"] is not None else "-",
                     c["unclassified"]))
    L.append("  " + s["caveat"])
    return "\n".join(L)


def install_hook():
    """Copy hooks/post-commit into .git/hooks -- the BACKUP trigger. The
    primary one is the SessionEnd hook in .claude/settings.json; this covers
    a session whose cwd is not this repo but which commits to it."""
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "hooks", "post-commit")
    gitdir = os.path.join(ROOT, ".git")
    if os.path.isfile(gitdir):                  # worktree: .git is a file
        with open(gitdir, encoding="utf-8") as fh:
            gitdir = fh.read().split(":", 1)[1].strip()
    if not os.path.exists(src):
        print("no hook template at " + src)
        return 1
    dst = os.path.join(gitdir, "hooks", "post-commit")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, encoding="utf-8") as fh:
        data = fh.read()
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(data)
    try:
        os.chmod(dst, 0o755)
    except OSError:
        pass
    print("installed post-commit hook -> " + dst)
    return 0


def open_attempts(limit=20):
    out = [r for r in current().values() if r["cause"] == "unclassified"]
    out.sort(key=lambda r: r.get("when") or "")
    return out[-limit:]


SAMPLE = os.path.join(SPECS, "runlog_sample.json")


def sample(n=40, seed=None):
    """A STRATIFIED RANDOM SAMPLE of the unclassified backlog.

    ⚠️⚠️ **THE BACKLOG WAS DESIGNED AS AN UNBOUNDED CENSUS, WHICH IS WHY IT
    HAS NEVER BEEN STARTED.** Measured 2026-08-26: 343 unclassified across
    three repos, growing ~7.9/day. At a minute each the census is ~5.7
    hours AND you must sustain 8/day merely to hold even -- a chore that
    grows faster than it drains never gets a first hour spent on it, and
    the log has sat at 100% unclassified since it was built.

    A census is also not what the claims need. Everything this log is for
    -- "what fraction of attempts end in silent-pass", "offline iterations
    per cluster round trip" -- is a PROPORTION, and a proportion is what a
    sample estimates. n=40 puts a 50% rate inside about +/-15 points at
    95%; n=100 inside about +/-10. Classifying all 343 to learn the same
    number is work nobody owes.

    So: draw a fixed, reproducible sample, classify THAT, and let the rest
    stay honestly `unclassified` -- which this log already counts and never
    hides. The sample is written to runlog_sample.json with its seed and
    strata so the estimate is auditable: a proportion from a sample nobody
    can reconstruct is not evidence, and a sample chosen after seeing the
    records is not random.

    Stratified by (domain, tier) because those are the two axes the claims
    are about, and an unstratified draw of 40 from a population that is 76%
    analog can miss the digital cells entirely.
    """
    import random
    rows = [r for r in current().values() if r["cause"] == "unclassified"]
    if not rows:
        return {"n": 0, "ids": [], "strata": {}}
    seed = seed if seed is not None else int(time.strftime("%Y%m%d"))
    rnd = random.Random(seed)
    strata = {}
    for r in rows:
        strata.setdefault((r.get("domain") or "analog", r["tier"]), []).append(r)
    # proportional allocation, at least one from every stratum that exists
    picked, keys = [], sorted(strata)
    for k in keys:
        share = max(1, int(round(n * len(strata[k]) / float(len(rows)))))
        pool = sorted(strata[k], key=lambda r: r["id"])   # order, then draw
        picked.extend(rnd.sample(pool, min(share, len(pool))))
    picked.sort(key=lambda r: (r.get("when") or "", r["id"]))
    out = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed,
        "population": len(rows),
        "n": len(picked),
        "strata": dict(("%s/%s" % k, len(v)) for k, v in strata.items()),
        "ids": [r["id"] for r in picked],
        "_what": "a stratified random sample of the unclassified backlog. "
                 "Classify these; the estimate is over this sample, and the "
                 "population it estimates is `population`. Re-running with "
                 "the same seed reproduces the draw.",
    }
    os.makedirs(SPECS, exist_ok=True)
    with open(SAMPLE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    return out


def _main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    cmd = a[0] if a else "stats"
    if cmd == "harvest":
        scan = harvest()
        print("scanned {} session(s), {} turn(s) -> {} attempt(s) at a known "
              "cell, {} new".format(scan["sessions"], scan["turns"],
                                    scan["attempts"], scan["new"]))
        print(report(rebuild_stats(scan)))
    elif cmd == "stats":
        print(report())
    elif cmd == "causes":
        for k in sorted(CAUSES):
            print("  {:16s} {}".format(k, CAUSES[k]))
    elif cmd == "open":
        for r in open_attempts():
            print("  {}  {:14s} {:8s} {:3d} action(s) {:2d} error(s)  {}"
                  .format(r["id"], r["cell"], r["tier"], r["actions"],
                          r["errors"], ", ".join(r["files"][:3])))
    elif cmd == "sample":
        n = int(a[1]) if len(a) > 1 else 40
        seed = int(a[2]) if len(a) > 2 else None
        smp = sample(n, seed)
        if not smp["n"]:
            print("nothing unclassified")
            return 0
        cur = current()
        print("stratified sample: {} of {} unclassified, seed {}"
              .format(smp["n"], smp["population"], smp["seed"]))
        print("strata (domain/tier): " + ", ".join(
            "{} {}".format(v, k) for k, v in sorted(smp["strata"].items())))
        print("written to " + SAMPLE)
        print("")
        for aid in smp["ids"]:
            r = cur[aid]
            print("  {}  {:16s} {:8s} {:8s} {:3d} action(s) {:2d} error(s)  {}"
                  .format(aid, r["cell"], r.get("domain", "analog"), r["tier"],
                          r["actions"], r["errors"], ", ".join(r["files"][:3])))
        print("")
        print("classify each:  python3 runlog.py classify <id> <cause> [note]")
        print("causes:         python3 runlog.py causes")
    elif cmd == "retier":
        r = retier()
        print("re-derived tier for {} attempt(s): {} changed "
              "({} -> offline, {} -> cluster)".format(
                  r["scanned"], r["changed"], r["to_offline"],
                  r["to_cluster"]))
        print(report(rebuild_stats()))
    elif cmd == "--install-hook":
        return install_hook()
    elif cmd == "classify":
        if len(a) < 3:
            print("usage: runlog.py classify <id> <cause> [note]")
            return 2
        rec = classify(a[1], a[2], " ".join(a[3:]))
        print("{} -> {}  {}".format(rec["id"], rec["cause"], rec["note"]))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
