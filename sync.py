#!/usr/bin/env python3
"""Vendor the shared policy layer into a consumer repo, and gate its drift.

Chosen over a submodule or a package install because it costs the consumers
NOTHING: a clone stays a clone, the cluster rsync push is unchanged, and the
cluster keeps running raw python3 through its activation wrapper. The price
is that real copies exist -- so the copies are hash-checked, which is the
same shape as every other derived artifact here (a staged value no script
reproduces survives only until it is re-spun).

  python3 sync.py --to C:\\dev\\spec2si-xt011        # vendor / update
  python3 sync.py --check C:\\dev\\spec2si-xt011     # gate: has the copy drifted?
  python3 sync.py --check-all                    # every registered consumer

Consumers are listed in consumers.json (paths are local to this machine and
that file is the only thing anyone needs to edit to add a fourth node).
"""
import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONSUMERS = os.path.join(HERE, "consumers.json")

#: what gets vendored, and where it lands inside a consumer repo
FILES = [
    ("policy/flow_policy.core.json", "policy/flow_policy.core.json"),
    ("conformance/test_policy_conformance.py",
     "policy/test_policy_conformance.py"),
    # The docmeta genre vocabulary. Shared for the same reason as the policy
    # core and found the same way: all three repos adopted the `docmeta`
    # frontmatter convention independently, and by 2026-08-20 twenty-six
    # tracked docs carried a genre spec2si-tsmc65's generator rejects -- so a
    # documentation generator could not be shared across the three repos at
    # all, whatever else was in it. A genre is a STALENESS CONTRACT, and a
    # contract is exactly the kind of thing that must not diverge.
    ("policy/docmeta.core.json", "policy/docmeta.core.json"),
    # The IR solver. The FIRST non-policy thing shared here, and it earns
    # it by touching no PDK: ohms and amps in, volts out. Everything that
    # knows about a process -- the RC card, the Spectre reader, the rail
    # topology -- stays in the consumer's own adapter. See irdrop/solver.py.
    ("irdrop/solver.py", "irdrop/solver.py"),
    ("irdrop/test_solver.py", "irdrop/test_solver.py"),
    # The operating-point reader. Shared for the same reason as the
    # solver: a Spectre oppoint is a SIMULATOR format, not a PDK one --
    # `Vdd:p` means the same thing on all three nodes. How you GET the
    # numbers stays local (PSF vs text oppoint vs transient mean).
    ("irdrop/currents.py", "irdrop/currents.py"),
    ("irdrop/test_currents.py", "irdrop/test_currents.py"),
    # The documentation MODEL -- the node-agnostic half of what was one
    # repo's `docs/gen.py`. A docstring is a docstring on 65 nm and on
    # 28 nm, and `docmeta` frontmatter is already a shared contract (see
    # docmeta.core.json above), so the parse, the link check and the
    # freshness gate are shared and each repo keeps only a thin backend
    # that knows its own areas. This is also what lets the web manual and
    # the PDF hang off ONE extractor instead of three.
    #
    # Stdlib-only and it never IMPORTS the code it documents (static AST),
    # which is why the gate runs in CI with no PDK, no licence and no
    # dependency install. Keep both properties.
    ("docs/docmodel.py", "docs/docmodel.py"),
    # The MARKDOWN backend -- the first of the three the model was split
    # for. Rendering is not repo-specific: three repos rendering three
    # slightly different API pages is the same divergence docmeta.core.json
    # exists to stop, one level up. Each repo's docs/gen.py keeps only its
    # CONFIG (areas, globs, its own JSON cards) and drives this.
    ("docs/mdbackend.py", "docs/mdbackend.py"),
    # The web manual: a Markdown->HTML renderer for the subset these repos
    # actually use, and the static-site backend over it. Both stdlib-only
    # for the same reason as everything else here -- the cluster has no pip
    # and the CI gate must not need one. mdrender is measured against the
    # corpus rather than guessed at, and its tests ship with it because the
    # failure mode is a page that renders WRONG, not one that fails to
    # build.
    ("docs/mdrender.py", "docs/mdrender.py"),
    ("docs/test_mdrender.py", "docs/test_mdrender.py"),
    ("docs/htmlbackend.py", "docs/htmlbackend.py"),
    ("docs/test_site.py", "docs/test_site.py"),
    # The PDF manual: a LaTeX emitter for the SHARED parser, and the backend
    # that assembles a curated method manual from it. XeLaTeX, because the
    # structural markers and the units are not ASCII -- and test_pdf.py reads
    # the build log for `Missing character`, so a glyph the font lacks FAILS
    # instead of vanishing.
    # The reference MODEL: commands, symbols, and the index that makes the
    # manual something you look things UP in. See its docstring for why the
    # first PDF was a transcript.
    ("docs/apiref.py", "docs/apiref.py"),
    ("docs/texrender.py", "docs/texrender.py"),
    ("docs/texbackend.py", "docs/texbackend.py"),
    ("docs/test_texrender.py", "docs/test_texrender.py"),
    ("docs/test_pdf.py", "docs/test_pdf.py"),
    # The RUNNABLE-CLAIM gate. Shared under the same seam as docmodel: it asks
    # only whether a path a doc names still exists and whether a flag a
    # command line passes still appears in that script -- questions with the
    # same answer on every node, needing no PDK, no tool and no cluster.
    # ⭐ It exists because the checks that DID run were green over a how-to
    # guide for three weeks while nothing could say whether its commands still
    # ran: `gen.py check` validates what a document IS (frontmatter, genre,
    # links between docs) and structurally never looks at a `.py` path.
    # Severity is not its own opinion -- it reads docmeta.core.json's
    # per-genre staleness contract, gates `guide`/`overview`, and leaves a
    # `log` (which that file calls "explicitly ALLOWED to be stale") advisory.
    ("docs/test_claims.py", "docs/test_claims.py"),
    # ⭐ THE SHARED HOW-TO SPINE -- the first PROSE in this list, and it earns
    # the slot the same way the code does: these four pages describe the half
    # of the method that does not change when the node does (what a stage is,
    # what a verdict means, how the core gets into a repo, how a port is stood
    # up). Nothing in them names a tool, a deck, a PDK path or a design.
    #
    # They land in `docs/howto/shared/` so they can never collide with a
    # port's OWN how-to pages, which sit in `docs/howto/` and are where every
    # Calibre-vs-Pegasus, PyCell-vs-SKILL difference belongs. A port's index
    # links to both. ⛔ Vendored prose must point at machine-readable things
    # rather than restate them -- a restated signature is exact the day it is
    # written and diverges silently after, and it would now do so in 5 repos.
    # ⚠ AND A VENDORED PAGE MAY ONLY NAME PATHS THAT EXIST IN *EVERY* REPO
    # IT LANDS IN. `conformance/test_policy_conformance.py` is real here and
    # nowhere else -- it vendors to `policy/` -- so naming it in shared prose
    # passed the claims gate in the flowkit and failed it in all four ports.
    # The port-side spelling is the only correct one for a shared page.
    ("docs/howto/shared/README.md", "docs/howto/shared/README.md"),
    ("docs/howto/shared/the-method.md", "docs/howto/shared/the-method.md"),
    ("docs/howto/shared/vendoring.md", "docs/howto/shared/vendoring.md"),
    ("docs/howto/shared/run-an-agent-session.md",
     "docs/howto/shared/run-an-agent-session.md"),
    ("docs/howto/shared/add-a-process-node.md",
     "docs/howto/shared/add-a-process-node.md"),
    ("docs/howto/shared/browse-your-results.md",
     "docs/howto/shared/browse-your-results.md"),
    # The routing core, phase 1: pure geometry and the tier-1 audit engine.
    # Shared under the same seam as the IR solver -- no PDK API, no deck, no
    # PCell, only numbers a caller's `rules` object hands in. The proof the
    # seam holds for routing is in the tree: tsmc28's tile_solver is
    # tsmc65's glue_solver byte-for-byte behind a ten-symbol adapter, and it
    # routed the 28 nm tile. Per-node rule VALUES, probes, drawers and
    # signoff drivers stay in each consumer. Plan: docs/routekit_plan.md;
    # decision: docs/decisions/0002-routekit-vendored-core.md; the corpus
    # that gates every change: routekit/corpus.json (upstream only).
    ("routekit/__init__.py", "routekit/__init__.py"),
    ("routekit/geom.py", "routekit/geom.py"),
    ("routekit/audit.py", "routekit/audit.py"),
    ("routekit/test_geom.py", "routekit/test_geom.py"),
    ("routekit/test_audit.py", "routekit/test_audit.py"),
    # Phase 2: the card contract and the rule probes. card.py loads,
    # validates and binds a RoutingCard (families by membership lists
    # ONLY -- the P_METAL_FAMILY failure made structurally impossible;
    # missing = refusal, measured-absent = answer; tracked/untracked
    # split for NDA values). ruleprobe.py generates card-driven
    # violation/clean geometry pairs and self-checks them against the
    # audit engine -- the offline half of the golden rule-probe gate.
    # Schema: docs/routekit_card_schema.md.
    ("routekit/card.py", "routekit/card.py"),
    ("routekit/ruleprobe.py", "routekit/ruleprobe.py"),
    ("routekit/test_card.py", "routekit/test_card.py"),
    ("routekit/test_ruleprobe.py", "routekit/test_ruleprobe.py"),
    # Phase 3: the search core. glue_solver (signed 136/136) as the
    # tile campaign generalized it (per-tier pads, phase-agnostic
    # arrivals, per-terminal access tiers -- each documented in place
    # to reduce exactly), behind ONE seam: bind(adapter, via_table,
    # route_tiers, ...). Consumers keep a thin binding shim.
    ("routekit/solve.py", "routekit/solve.py"),
    ("routekit/test_solve.py", "routekit/test_solve.py"),
    # The electrical primitives: EM floors (banded-step aware, refuse
    # over hope), ohm pricing from card tables, the R_max budget, the
    # widen fixpoint contract. The route_widen machinery itself lands
    # with its natural gate, the tsmc65 v2 re-route (plan, phase 4).
    ("routekit/elec.py", "routekit/elec.py"),
    ("routekit/test_elec.py", "routekit/test_elec.py"),
    # The minimal GDS writer: rectangles into named cells, for probe
    # geometry that never lives in OA. Round-trip-gated -- the reader
    # half refuses records it does not know, because half-read GDS
    # has lied to these flows before.
    ("routekit/gdsw.py", "routekit/gdsw.py"),
    ("routekit/test_gdsw.py", "routekit/test_gdsw.py"),
    # Cluster housekeeping. Shared for the plainest possible reason: it
    # operates on `~/Documents/*/analog/work` -- EVERY node's tree at once,
    # on one shared NFS home. It was written in the tsmc28 checkout only
    # because that is where the session happened to be sitting, and the
    # motivating example is in xt011: `drc_v1`..`drc_v16`, fifteen of which
    # were obsolete the moment the next one completed.
    #
    # It touches no PDK. `run_features.py` reads mtimes and filenames;
    # `stale_classify.py` decides over those features and nothing else. The
    # one node-shaped thing in either -- the per-tool terminal-file patterns
    # (Calibre DRC.rep, Pegasus <cell>_drc.sum, strmout.log) -- is an
    # argument FOR sharing rather than against it: xt011 is the Pegasus
    # node and tsmc28 the Calibre one, and a completion test that knew only
    # Calibre scored 97 of 468 runs complete where 254 were.
    #
    # What stays in each consumer's own deployment/: installing to ~/bin,
    # the crontab entry, and push.sh's liveness check. Those are site
    # concerns; these four files are the logic.
    # The agent-loop runlog and the transcript reader under it. Shared for
    # the plainest reason of all: it already served every repo, through a
    # `RUNLOG_REPO` env var pointing at ONE checkout's `browse/runlog.py`.
    # Every other consumer's SessionEnd hook reached across the disk into
    # spec2si-tsmc65 -- which is how tsmc28's harvest could be dead for 18
    # days and xt011's never work at all without either being noticed.
    #
    # ⚠️ THE DESTINATION IS `browse/`, NOT `runlog/`. `browse/server.py`
    # imports `agentview` as a sibling, so the pair has to land where that
    # import already resolves; the flowkit-side name is its own so the kit
    # is not implying it owns the whole dashboard. Vendoring the pair also
    # retires the cross-repo path: a consumer's hook becomes
    # `cd <repo>/browse && python3 runlog.py harvest`, with no env var and
    # therefore no `VAR=/unix/path` for MSYS to rewrite.
    #
    # Both are stdlib-only, which is this kit's bar: agentview imports
    # json/os/time, runlog adds hashlib/re/sys and agentview.
    ("browse/agentview.py", "browse/agentview.py"),
    ("browse/runlog.py", "browse/runlog.py"),
    ("browse/test_agentview.py", "browse/test_agentview.py"),
    ("browse/test_runlog.py", "browse/test_runlog.py"),
    # THE ARTIFACT BROWSER, and the cluster transport it reads through.
    # Vendored 2026-09-12 (ADR-0004). Until then one port held the
    # implementation and the other three carried a launcher stub that
    # reached ACROSS THE DISK into it -- and the transport was reached the
    # same way, two hops out. The browser is stdlib-only by construction
    # (that is its own principle 2), knows no PDK, and everything
    # engine-specific it draws -- the transient reader, the abstract's
    # track map -- it LOCATES in the served repo and degrades without.
    # What differs per port is a JSON file it never copies: `roots.json`,
    # which is how a port is now onboarded (add the file, re-vendor).
    ("browse/model.py", "browse/model.py"),
    ("browse/roots.py", "browse/roots.py"),
    ("browse/tools.py", "browse/tools.py"),
    ("browse/cluster.py", "browse/cluster.py"),
    ("browse/estimate.py", "browse/estimate.py"),
    ("browse/launch.py", "browse/launch.py"),
    ("browse/server.py", "browse/server.py"),
    ("browse/test_browse.py", "browse/test_browse.py"),
    # The transport: an ssh round trip that cannot be corrupted by quoting
    # (a script over stdin, values bound through quoted heredocs), the host
    # chooser, the licence-holding process scanner and the job CLI, with
    # the bundle the cluster side runs. A site fact, not a node one: all
    # four ports share the one cluster. Its README stays with the port that
    # wrote it, because it names that port's plan documents.
    ("jobs/__init__.py", "deployment/bnl/jobs/__init__.py"),
    ("jobs/__main__.py", "deployment/bnl/jobs/__main__.py"),
    ("jobs/cli.py", "deployment/bnl/jobs/cli.py"),
    ("jobs/hosts.py", "deployment/bnl/jobs/hosts.py"),
    ("jobs/procscan.py", "deployment/bnl/jobs/procscan.py"),
    ("jobs/remote.py", "deployment/bnl/jobs/remote.py"),
    ("jobs/bin/jobrec.py", "deployment/bnl/jobs/bin/jobrec.py"),
    ("jobs/bin/license.py", "deployment/bnl/jobs/bin/license.py"),
    ("jobs/bin/progress.py", "deployment/bnl/jobs/bin/progress.py"),
    ("jobs/bin/report.sh", "deployment/bnl/jobs/bin/report.sh"),
    ("jobs/bin/runjob", "deployment/bnl/jobs/bin/runjob"),
    ("jobs/test_cli.py", "deployment/bnl/jobs/test_cli.py"),
    ("jobs/test_hosts.py", "deployment/bnl/jobs/test_hosts.py"),
    ("jobs/test_jobrec.py", "deployment/bnl/jobs/test_jobrec.py"),
    ("jobs/test_license.py", "deployment/bnl/jobs/test_license.py"),
    ("jobs/test_procscan.py", "deployment/bnl/jobs/test_procscan.py"),
    ("jobs/test_progress.py", "deployment/bnl/jobs/test_progress.py"),
    ("jobs/test_remote.py", "deployment/bnl/jobs/test_remote.py"),
    ("jobs/test_harness_can_fail.py",
     "deployment/bnl/jobs/test_harness_can_fail.py"),
    ("housekeeping/run_features.py", "housekeeping/run_features.py"),
    ("housekeeping/stale_classify.py", "housekeeping/stale_classify.py"),
    ("housekeeping/attic_sweep.sh", "housekeeping/attic_sweep.sh"),
    ("housekeeping/stale_labels.sample.json",
     "housekeeping/stale_labels.sample.json"),
    # DRC-IN-THE-LOOP. The signoff deck's own markers ARE the positions, so
    # a repair answers THEM rather than re-deriving the violating shapes
    # from the plan -- which is a second model of a question something else
    # has already answered, and the two disagree about which shapes merge,
    # about what a via def draws, and about what a block contributes.
    #
    # Shared on the strongest evidence any file here has: `resultsdb` was
    # written TWICE, independently -- for Calibre 2024.1 in the 65 nm port
    # and for PVS 23.1 / Pegasus in the XT011 one -- and the two came out
    # the same algorithm to within a comment, after the XT011 port wrote
    # down "whether Pegasus emits a Calibre-shaped database" as a question
    # to ANSWER and ran the 65 nm reader against five real Pegasus files.
    #
    # The rule NAMES stay local (`A1M2` / `M2.A.1`) and so does the
    # flattener; what is shared is the parse, the marker geometry, the
    # patch, the attribution and the loop protocol. See docs/drc_loop.md
    # and docs/decisions/0003-drc-in-the-loop.md.
    ("drcloop/__init__.py", "drcloop/__init__.py"),
    ("drcloop/resultsdb.py", "drcloop/resultsdb.py"),
    ("drcloop/markers.py", "drcloop/markers.py"),
    ("drcloop/triage.py", "drcloop/triage.py"),
    ("drcloop/loop.py", "drcloop/loop.py"),
    ("drcloop/test_resultsdb.py", "drcloop/test_resultsdb.py"),
    ("drcloop/test_markers.py", "drcloop/test_markers.py"),
    ("drcloop/test_triage.py", "drcloop/test_triage.py"),
    ("drcloop/test_loop.py", "drcloop/test_loop.py"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def localize(path):
    """`C:\\dev\\X` -> `/mnt/c/dev/X` when running under WSL, which is where
    python actually lives on the Windows box. consumers.json records the
    canonical Windows paths; without this, --check-all silently reports every
    consumer MISSING, which is the worst possible failure for a drift gate."""
    if os.path.isdir("/mnt/c") and re.match(r"^[A-Za-z]:[\\/]", path):
        return "/mnt/{}/{}".format(path[0].lower(),
                                   path[3:].replace("\\", "/"))
    return path


def consumers():
    if not os.path.exists(CONSUMERS):
        return []
    with open(CONSUMERS, encoding="utf-8") as fh:
        out = json.load(fh).get("consumers", [])
    for c in out:
        c["path"] = localize(c["path"])
    return out


def vendor(dest):
    n = 0
    for src_rel, dst_rel in FILES:
        src = os.path.join(HERE, src_rel)
        dst = os.path.join(dest, dst_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst) and sha256(dst) == sha256(src):
            print("  same  " + dst_rel)
            continue
        shutil.copyfile(src, dst)
        print("  wrote " + dst_rel)
        n += 1
    return n


def skips(dest):
    """Prefixes this consumer has DECLARED it does not take, with a reason.

    ⭐ A GAP A PORT HAS DECIDED ON IS NOT DRIFT, and until this existed there
    was no way to say so: `check` reported nine `drcloop/*` files MISSING in
    spec2si-xt011 on every run, forever, because that port reads its PVS
    results through its own `analog/layout/drc_db.py` -- imported by five
    modules -- and adopting the shared core is a MIGRATION, not a delivery.
    A gate that reports a permanent red nobody can clear is a gate that gets
    ignored, and then the real finding beside it is invisible too.

    So a consumer may declare `"skip": {"<prefix>": "<why>"}` in
    consumers.json. It is the same idea as `not-implemented` being a PASSING
    state in flow_policy.json: the gap becomes a sentence someone wrote and a
    number on every run, instead of an absence nobody can see.

    ⛔ It is NOT a suppression. A skipped prefix that turns out to be present
    is still compared, and still reports DRIFTED if it has diverged -- a port
    cannot half-take a file and silence the check on it.
    """
    for c in consumers():
        if os.path.normcase(os.path.abspath(c["path"])) == \
                os.path.normcase(os.path.abspath(dest)):
            return c.get("skip") or {}
    return {}


def check(dest):
    """[(rel, status)] -- 'ok' | 'DRIFTED' | 'MISSING' | 'not taken'."""
    out = []
    declared = skips(dest)
    for src_rel, dst_rel in FILES:
        src = os.path.join(HERE, src_rel)
        dst = os.path.join(dest, dst_rel)
        if not os.path.exists(dst):
            pre = next((p for p in declared if dst_rel.startswith(p)), None)
            out.append((dst_rel, "not taken" if pre else "MISSING"))
        elif sha256(dst) != sha256(src):
            out.append((dst_rel, "DRIFTED"))
        else:
            out.append((dst_rel, "ok"))
    return out


def main(argv):
    if "--to" in argv:
        dest = argv[argv.index("--to") + 1]
        print("vendoring into " + dest)
        vendor(dest)
        return 0
    targets = []
    if "--check" in argv:
        targets = [argv[argv.index("--check") + 1]]
    elif "--check-all" in argv:
        targets = [c["path"] for c in consumers()]
    else:
        print(__doc__)
        return 2
    bad = declined = 0
    for dest in targets:
        print(os.path.basename(dest.rstrip("/\\")) + ":")
        rows = check(dest)
        for rel, status in rows:
            print("  {:9s} {}".format(status, rel))
            bad += status not in ("ok", "not taken")
            declined += status == "not taken"
        for pre, why in sorted(skips(dest).items()):
            if any(r.startswith(pre) and s == "not taken" for r, s in rows):
                print("  -- not taken: {} -- {}".format(pre, why))
    if declined:
        print("\n{} file(s) NOT TAKEN by declaration (consumers.json `skip`) -- "
              "a decided gap, not drift.".format(declined))
    if bad:
        print("\n{} file(s) drifted or missing.".format(bad))
        # ASCII only: this prints to a Windows console at cp1252 and to the
        # cluster's tcsh, and a marker glyph here raised UnicodeEncodeError
        # and took the whole gate down with it.
        print("!! `--to <repo>` copies the WHOLE list unconditionally and will "
              "DISCARD a drifted file rather than report a conflict. Resolve "
              "what `--check` lists first, or copy the one file you changed.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
