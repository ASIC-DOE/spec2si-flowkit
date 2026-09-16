#!/usr/bin/env python3
"""Classify run dirs KEEP vs SWEEP from run_features.py output.

    python3 stale_classify.py features.json                 # decisions
    python3 stale_classify.py features.json --vs-baseline   # where it differs
    python3 stale_classify.py features.json --sample 40     # rows to label
    python3 stale_classify.py features.json --labels l.json # confusion matrix

⚠️⚠️ **THE 14-DAY SWEEP WAS ALREADY A CLASSIFIER -- ONE FEATURE, A THRESHOLD
NOBODY VALIDATED, AND NO CONFUSION MATRIX.** That is the actual defect; the
fix is not a cleverer rule but a decision that can be MEASURED. So features,
decision and evaluation are three separate things here, and the decision is
reported with the reason that drove it.

**WHY THE RULES ARE DETERMINISTIC AND NOT LEARNED.** A learned model is the
wrong tool for this specific problem, for reasons that are about the problem
and not about taste:

  1. There are no labels. n=468 with zero ground truth; anyone who can label
     enough to train has already done the work by hand.
  2. The decisive features are FACTS, not evidence. "This run left a lock and
     never wrote a terminal file" and "a later run of the same base name
     completed" are read off the disk, not inferred. Fitting a model over a
     fact you can read is how a check goes vacuous.
  3. The cost asymmetry is extreme, known in advance, and asymmetric in the
     direction accuracy ignores: sweeping a signoff result is expensive,
     leaving junk costs nothing but tidiness. A model tuned for accuracy
     optimises the wrong quantity; here the operating point IS the design.
  4. It must explain itself. "superseded by drc_v16" is actionable at 03:00;
     "p(stale)=0.73" is not.

What a classifier framing DOES buy, and what this file implements: an
explicit feature set, a stated operating point, a labelled evaluation, and a
confusion matrix that says what the rule costs. If the grey band turns out to
be large after labelling, THEN fit something over these same features -- the
extractor is deliberately independent of the decision so that stays possible.
"""
import argparse
import json
import random
import re
import sys

KEEP, SWEEP = "KEEP", "SWEEP"


def classify(r, age_days=14):
    """-> (decision, reason, confidence). Ordered cascade: the first rule
    that fires wins, and the order encodes the cost asymmetry."""
    if r.get("has_keep"):
        return KEEP, "explicit .keep marker", "certain"
    # ⚠️ DIGITAL SCRATCH IS ITS OWN CLASS AND MUST NOT MEET THE RUN-DIR
    # RULES. A P&R flow keeps every stage checkpoint on purpose -- restarting
    # from `place.odb` after routing fails is the normal way to work -- so
    # "superseded by a later stage" is true and CATASTROPHIC there.
    # `run_features.py` never enumerates `output/`, `reports/`, `*.db` or
    # `*.odb` at all, so anything reaching this branch is scratch by
    # construction, and age is then the whole question: the only scratch
    # worth keeping belongs to a run that is still going.
    if r.get("kind") == "dig_scratch":
        if r.get("superseded_by"):
            return SWEEP, "superseded by " + r["superseded_by"], "high"
        age = r.get("age_days") or 0
        if age > age_days:
            return SWEEP, "digital scratch, untouched %.0fd" % age, "high"
        return KEEP, "scratch of a run still active (%.0fd)" % age, "low"
    # ── loose files and scratch scripts ─────────────────────────────────
    # Two guards before age gets a vote, because a loose `.sh` in a project
    # directory is indistinguishable BY AGE from real source:
    #   tracked     -- git has it, so it is source and sweeping it shows up
    #                  as an unexplained deletion in `git status`
    #   referenced  -- a login file or the crontab names it, so something
    #                  runs it whatever its mtime says
    if r.get("kind") in ("home_loose", "proj_script"):
        if r.get("tracked"):
            return KEEP, "tracked by git -- source, not scratch", "certain"
        if r.get("referenced"):
            return KEEP, "referenced by a login file or the crontab", "certain"
        age = r.get("age_days") or 0
        if age > age_days:
            return SWEEP, "loose %s, untouched %.0fd" % (
                "script" if r["kind"] == "proj_script" else "file", age), "medium"
        return KEEP, "loose but recent (%.0fd)" % age, "low"
    if r.get("empty"):
        return SWEEP, "empty directory", "certain"
    if r.get("has_wreckage") and not r.get("completed"):
        # garbage from minute one -- age is irrelevant to a run that died
        return SWEEP, "crashed run (wreckage, no terminal file)", "high"
    if r.get("superseded_by"):
        # THE feature age cannot see: obsolete, not old
        return SWEEP, "superseded by " + r["superseded_by"], "high"
    if r.get("has_signoff"):
        # a finished result is not stale however long it has sat
        return KEEP, "holds a signoff artifact, not superseded", "high"
    age = r.get("age_days") or 0
    if age > age_days:
        return SWEEP, "no result, untouched %.0fd" % age, "medium"
    return KEEP, "active (%.0fd) or unclassified" % age, "low"


def baseline(r, age_days=14):
    """What the deployed sweep does today: age alone."""
    if r.get("has_keep"):
        return KEEP
    return SWEEP if (r.get("age_days") or 0) > age_days else KEEP


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("features")
    ap.add_argument("--age-days", type=int, default=14)
    ap.add_argument("--vs-baseline", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--labels")
    ap.add_argument("--sweep-list", action="store_true",
                    help="print one path per line for attic_sweep.sh")
    ap.add_argument("--seed", type=int, default=20260826)
    a = ap.parse_args(argv)

    with open(a.features, encoding="utf-8") as fh:
        rows = json.load(fh)["rows"]
    for r in rows:
        r["decision"], r["reason"], r["confidence"] = classify(r, a.age_days)
        r["baseline"] = baseline(r, a.age_days)

    if a.sample:
        # STRATIFIED by (decision, confidence): a uniform sample of 468 is
        # mostly the easy majority class and measures nothing about the band
        # where the rule is actually uncertain.
        random.seed(a.seed)
        strata = {}
        for r in rows:
            strata.setdefault((r["decision"], r["confidence"]), []).append(r)
        out, per = [], max(1, a.sample // max(1, len(strata)))
        for k in sorted(strata):
            out.extend(random.sample(strata[k], min(per, len(strata[k]))))
        print(json.dumps(
            [{"path": r["path"], "predicted": r["decision"],
              "reason": r["reason"], "confidence": r["confidence"],
              "age_days": r["age_days"], "size_mb": r["size_mb"],
              "has_signoff": r["has_signoff"], "completed": r["completed"],
              "superseded_by": r["superseded_by"], "label": None}
             for r in out], indent=1))
        sys.stderr.write("%d rows across %d strata -- set every \"label\" to "
                         "KEEP or SWEEP\n" % (len(out), len(strata)))
        return 0

    if a.labels:
        with open(a.labels, encoding="utf-8") as fh:
            truth = dict((x["path"], x["label"]) for x in json.load(fh)
                         if x.get("label"))
        if not truth:
            sys.stderr.write("no labels set in %s\n" % a.labels)
            return 2
        cells = {}
        for r in rows:
            t = truth.get(r["path"])
            if t is None:
                continue
            for nm, pred in (("rules", r["decision"]),
                             ("age-only", r["baseline"])):
                cells.setdefault(nm, {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
                c = cells[nm]
                if pred == SWEEP and t == SWEEP:
                    c["tp"] += 1
                elif pred == SWEEP and t == KEEP:
                    c["fp"] += 1        # ⚠️ the expensive error
                elif pred == KEEP and t == KEEP:
                    c["tn"] += 1
                else:
                    c["fn"] += 1
        print("labelled rows: %d\n" % len(truth))
        for nm in ("age-only", "rules"):
            c = cells[nm]
            prec = c["tp"] / float(c["tp"] + c["fp"] or 1)
            rec = c["tp"] / float(c["tp"] + c["fn"] or 1)
            print("%-9s  swept-correctly %3d  WRONGLY-SWEPT %3d  "
                  "kept %3d  missed %3d   precision %.2f  recall %.2f"
                  % (nm, c["tp"], c["fp"], c["tn"], c["fn"], prec, rec))
        print("\nWRONGLY-SWEPT is the column that matters: it is a result "
              "moved out from under you.\nRecall only buys tidiness.")
        return 0

    if a.sweep_list:
        # the sweep consumes this instead of doing its own age test, so
        # there is exactly ONE definition of stale in the system
        for r in rows:
            if r["decision"] == SWEEP:
                print(r["path"])
        return 0

    n_sweep = sum(1 for r in rows if r["decision"] == SWEEP)
    n_base = sum(1 for r in rows if r["baseline"] == SWEEP)
    print("%d dirs: rules sweep %d, age-only sweeps %d" %
          (len(rows), n_sweep, n_base))
    by = {}
    for r in rows:
        # group by RULE, not by the reason's payload: "untouched 27d" and
        # "untouched 41d" are one rule firing twice, and listing them
        # separately turns a 5-line summary into 25 lines of noise.
        rule = re.sub(r"\d+", "N", r["reason"].split(" (")[0].split(" by ")[0])
        by.setdefault((r["decision"], rule), []).append(r)
    print("\nby rule:")
    for k in sorted(by, key=lambda k: -len(by[k])):
        print("  %-5s %-46s %4d" % (k[0], k[1], len(by[k])))

    if a.vs_baseline:
        diff = [r for r in rows if r["decision"] != r["baseline"]]
        print("\n%d disagreements with the deployed age-only rule:" % len(diff))
        saved = [r for r in diff if r["decision"] == KEEP]
        caught = [r for r in diff if r["decision"] == SWEEP]
        print("  %d that age-only would sweep and the rules KEEP "
              "(rescued results)" % len(saved))
        for r in saved[:8]:
            print("      %-42s %s" % (r["name"], r["reason"]))
        print("  %d that age-only calls fresh and the rules SWEEP "
              "(obsolete, not old)" % len(caught))
        for r in caught[:8]:
            print("      %-42s %s" % (r["name"], r["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
