#!/usr/bin/env python3
"""Extract per-run-dir FEATURES for the staleness classifier. Read-only.

    python3 run_features.py > features.json          # on the cluster

Sweeping by age is a classifier with one feature and an unvalidated
threshold, and measured against the real tree it errs in both directions
(2026-08-26: 88 of 319 "stale" dirs hold signoff artifacts, while
`drc_v1`..`drc_v16` all read "fresh" although fifteen were superseded the
moment the next one was written). This dumps the features a real decision
needs, so the classifier can be evaluated instead of asserted.

⚠️ THIS FILE COMPUTES FEATURES AND NOTHING ELSE. It does not decide, and it
never moves a file. Keeping extraction separate from the decision is what
makes a labelled evaluation possible at all: the same feature rows get
scored by any candidate rule, and the rows are cheap to re-score offline
without re-walking NFS.

Python floor is the cluster's 3.6 -- no dataclasses, no f-strings.
"""
import json
import os
import re
import sys
import time

# artifacts that mean "a real result was produced here"
SIGNOFF = re.compile(r"(^DRC\.rep$|^lvs\.rep$|\.gds$|^DRC_RES\.db$|\.sp$"
                     r"|\.oa$|^DRC\.sum$|\.lib$|\.lef$)")
# leftovers that mean "a run died here"
WRECKAGE = re.compile(r"(\.cdslck$|^core\.\d+|^\.nfs[0-9a-f]{8}|\.lck$"
                      r"|^panic.*\.log$)")
# ⚠️ COMPLETION IS TOOL-SPECIFIC, AND KNOWING ONLY ONE TOOL UNDER-DETECTS IT
# BADLY. The first version tested for Calibre's `DRC.rep` alone and scored 97
# of 468 dirs complete while 272 held a real result -- so `superseded_by`,
# which requires a COMPLETED successor, found 13 where `drc_v1`..`drc_v16`
# alone should give fifteen. Measured on this tree, the terminal files are:
#   Calibre  DRC.rep / lvs.rep / DRC_RES.db      (61 dirs)
#   Pegasus  <cell>_drc.sum + *.rdb              (xt011)
#   strmout  strmout.log / strmout.out           (60)
#   Virtuoso worker.raw.txt (a build area, 99)
# A terminal file is written at the END of a run, so its PRESENCE is the
# signal; the content marker is a second confirmation where one exists.
TERMINAL = re.compile(r"(^DRC\.rep$|^lvs\.rep$|\.sum$|^strmout\.log$"
                      r"|^worker\.raw\.txt$|\.lib$)")
DONE_MARK = (("DRC.rep", "TOTAL Result Count"),
             ("lvs.rep", "CORRECT"),
             ("lvs.rep", "INCORRECT"))

MAX_DEPTH = 3           # NFS: a full walk of 468 dirs is minutes
MAX_FILES = 4000        # per dir; only counts and flags need the tail


def walk_limited(root):
    """(files, truncated). Depth- and count-limited: the features below are
    flags and counts, and a dir with 40k files answers all of them in the
    first few thousand."""
    out = []
    root_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - root_depth >= MAX_DEPTH:
            dirnames[:] = []
        for fn in filenames:
            out.append((dirpath, fn))
            if len(out) >= MAX_FILES:
                return out, True
    return out, False


def base_name(name):
    """The run's identity with its version suffix removed, so siblings of
    one campaign group together.

    `drc_v1`..`drc_v16`, `drc_pwr`..`drc_pwr7` and `chk_LVT_TT` are the
    shapes actually on disk. This is the SUPERSESSION key, and it is the
    feature age cannot see: fifteen of sixteen `drc_v*` were garbage the
    moment the next completed, and every one of them looked fresh."""
    n = re.sub(r"[_-]?v?\d+$", "", name)
    return n or name


def features(path, now):
    name = os.path.basename(path.rstrip(os.sep))
    files, truncated = walk_limited(path)
    newest = 0.0
    total = 0
    has_signoff = False
    has_wreckage = False
    completed = False
    confirmed = False
    for dirpath, fn in files:
        full = os.path.join(dirpath, fn)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        total += st.st_size
        if st.st_mtime > newest:
            newest = st.st_mtime
        if SIGNOFF.search(fn):
            has_signoff = True
        if WRECKAGE.search(fn):
            has_wreckage = True
        if TERMINAL.search(fn):
            completed = True
            for want_fn, mark in DONE_MARK:
                if fn == want_fn:
                    try:
                        with open(full, "rb") as fh:
                            # the terminal marker is near the end
                            fh.seek(max(0, st.st_size - 200000))
                            confirmed = confirmed or mark.encode() in fh.read()
                    except (OSError, IOError):
                        pass
    return {
        "kind": "run_dir",
        "name": name,
        "path": path,
        "base": base_name(name),
        "n_files": len(files),
        "truncated": truncated,
        "size_mb": round(total / 1048576.0, 2),
        "age_days": round((now - newest) / 86400.0, 1) if newest else None,
        "mtime": newest,
        "has_signoff": has_signoff,
        "has_wreckage": has_wreckage,
        "completed": completed,
        "confirmed": confirmed,
        "has_keep": os.path.exists(os.path.join(path, ".keep")),
        "empty": len(files) == 0,
    }


# ── digital flows are a DIFFERENT ARTIFACT MODEL ─────────────────────────
#
# ⚠️⚠️ **DO NOT APPLY THE RUN-DIR RULES TO A P&R FLOW.** An analog run dir is
# one run; a digital flow directory is ONE tree holding every stage of one
# run, and the differences are not cosmetic:
#
#   * Stage checkpoints (`*.db` from Innovus, `*.odb` from OpenROAD) are NOT
#     superseded by later stages. Restarting from `place.odb` after `route`
#     fails is the normal way to work, so the supersession rule -- correct
#     for `drc_v1`..`drc_v16` -- is actively WRONG here and would delete the
#     restart points.
#   * `output/` holds the routed Verilog, the DEF and the merged GDS: the
#     six-step result and the only input physical signoff has.
#   * `reports/` is committed evidence; the laptop is the authority for it.
#
# And the precedent is on the record: a push with `--delete` already removed
# `init.odb` and `fplan.odb` from a run that had just produced them, and the
# way it presented was that logs/ and output/ survived while the run itself
# was gone (see the ⚠️⚠️ block in deployment/bnl/push.sh).
#
# So NONE of those classes are enumerated here at all. Only the scratch that
# push.sh already excludes from its rsync as "pure garbage" becomes a
# candidate row -- if it is not in this list it cannot be selected, which is
# a stronger guarantee than a rule that decides to spare it.
DIG_SCRATCH = re.compile(r"^(innovus_temp_.*|\.GenusRestruct_.*|\.simvision"
                         r"|innovus\.(cmd|log|logv)\d*|\.pbs_.*|\.st_launch_.*)$")


SCRIPT_EXT = (".sh", ".csh", ".py", ".il", ".tcl", ".pl", ".awk")


def _tracked_paths(root):
    """Files git tracks under `root`, as absolute paths, or None if `root`
    is not a repo.

    ⚠️ **A TRACKED SCRIPT IS SOURCE, NOT SCRATCH, AND AGE SAYS NOTHING
    ABOUT WHICH.** `photonic_wirebond` holds 13 tracked `.sh` files and they
    look exactly like the 140 loose ones in $HOME: same extension, same
    "untouched for months". Sweeping one moves a file out of a working tree
    and shows up as an unexplained deletion in `git status`, which is a
    worse failure than the clutter it was cleaning."""
    if not os.path.isdir(os.path.join(root, ".git")):
        return None
    try:
        import subprocess
        out = subprocess.check_output(["git", "ls-files"], cwd=root,
                                      stderr=open(os.devnull, "wb"))
    except Exception:                                       # noqa: BLE001
        return set()            # a repo we cannot query: spare everything
    return set(os.path.join(root, p) for p in
               out.decode("utf-8", "replace").split("\n") if p)


def _referenced_names(home):
    """Basenames mentioned by a login file or the crontab. Something the
    shell sources on every login is not scratch whatever its mtime."""
    names = set()
    for f in (".bashrc", ".bash_profile", ".cshrc", ".tcshrc", ".profile"):
        p = os.path.join(home, f)
        try:
            with open(p, "rb") as fh:
                txt = fh.read().decode("utf-8", "replace")
        except (OSError, IOError):
            continue
        for m in re.finditer(r"[\w.\-/]+\.(?:sh|csh|py|il|tcl)", txt):
            names.add(os.path.basename(m.group(0)))
    try:
        import subprocess
        out = subprocess.check_output(["crontab", "-l"],
                                      stderr=open(os.devnull, "wb"))
        for m in re.finditer(r"[\w.\-/]+\.(?:sh|csh|py|il|tcl)",
                             out.decode("utf-8", "replace")):
            names.add(os.path.basename(m.group(0)))
    except Exception:                                       # noqa: BLE001
        pass
    return names


def loose_file_rows(now):
    """Loose files in $HOME and loose SCRIPTS one level into each project.

    Both were previously handled by a `find` inside attic_sweep.sh, i.e. a
    SECOND definition of stale living beside the classifier -- the exact
    thing splitting features from decision was meant to prevent. They are
    rows now, judged by the same cascade as everything else."""
    rows = []
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    referenced = _referenced_names(home)

    def add(p, kind, tracked):
        base = os.path.basename(p)
        if base.startswith("."):
            return                      # dotfiles are never candidates
        try:
            st = os.lstat(p)
        except OSError:
            return
        rows.append({
            "kind": kind, "name": base, "path": p,
            "base": base_name(os.path.splitext(base)[0]),
            "n_files": 1, "truncated": False,
            "size_mb": round(st.st_size / 1048576.0, 2),
            "age_days": round((now - st.st_mtime) / 86400.0, 1),
            "mtime": st.st_mtime, "has_signoff": False,
            "has_wreckage": False, "completed": True, "confirmed": False,
            "has_keep": False, "empty": False,
            "tracked": tracked, "referenced": base in referenced,
        })

    for e in sorted(os.listdir(home)):
        p = os.path.join(home, e)
        if os.path.isfile(p):
            add(p, "home_loose", False)

    if os.path.isdir(docs):
        for proj in sorted(os.listdir(docs)):
            root = os.path.join(docs, proj)
            if not os.path.isdir(root):
                continue
            tracked = _tracked_paths(root)
            try:
                entries = sorted(os.listdir(root))
            except OSError:
                continue
            for e in entries:
                p = os.path.join(root, e)
                if not os.path.isfile(p):
                    continue
                if not e.endswith(SCRIPT_EXT):
                    continue
                add(p, "proj_script",
                    tracked is not None and p in tracked)
    return rows


def dig_scratch_rows(now):
    """Scratch under `~/Documents/*/dig_flows/*/`, one row per item.

    Numbered tool logs (`innovus.cmd`, `.cmd1`.. `.cmd4`) group under one
    `base` exactly as the run dirs do, so the newest invocation's log is
    kept and the earlier ones read as superseded rather than merely old."""
    rows = []
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(docs):
        return rows
    for proj in sorted(os.listdir(docs)):
        flows = os.path.join(docs, proj, "dig_flows")
        if not os.path.isdir(flows):
            continue
        for flow in sorted(os.listdir(flows)):
            fdir = os.path.join(flows, flow)
            if not os.path.isdir(fdir):
                continue
            try:
                entries = os.listdir(fdir)
            except OSError:
                continue
            for e in entries:
                if not DIG_SCRATCH.match(e):
                    continue
                p = os.path.join(fdir, e)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                size = st.st_size
                newest = st.st_mtime
                nf = 1
                if os.path.isdir(p):
                    files, _trunc = walk_limited(p)
                    nf = len(files)
                    for dirpath, fn in files:
                        try:
                            s2 = os.lstat(os.path.join(dirpath, fn))
                        except OSError:
                            continue
                        size += s2.st_size
                        if s2.st_mtime > newest:
                            newest = s2.st_mtime
                rows.append({
                    "kind": "dig_scratch",
                    "name": e,
                    "path": p,
                    "base": base_name(re.sub(r"\d+$", "", e)),
                    "n_files": nf,
                    "truncated": False,
                    "size_mb": round(size / 1048576.0, 2),
                    "age_days": round((now - newest) / 86400.0, 1),
                    "mtime": newest,
                    "has_signoff": False,
                    "has_wreckage": False,
                    "completed": True,     # a log IS its own terminal artifact
                    "confirmed": False,
                    "has_keep": False,
                    "empty": nf == 0,
                })
    return rows


def main():
    now = time.time()
    roots = []
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    if os.path.isdir(docs):
        for proj in sorted(os.listdir(docs)):
            for sub in ("analog/work", "work"):
                w = os.path.join(docs, proj, sub)
                if os.path.isdir(w):
                    roots.append(w)
    rows = []
    for w in roots:
        for d in sorted(os.listdir(w)):
            p = os.path.join(w, d)
            if os.path.isdir(p):
                try:
                    rows.append(features(p, now))
                except OSError as e:
                    sys.stderr.write("skip %s: %s\n" % (p, e))
    # ⚠️ A TREE WITH NO `analog/work` WAS INVISIBLE, AND THAT IS WHERE THE
    # DATA IS. The roots above are `~/Documents/*/{analog/work,work}`, so
    # `LDRD_2022` and `LDRD_2025` -- which have `dig_flows/` and no
    # `analog/work` -- were never enumerated at all. LDRD_2025/dig_flows is
    # 36 GB, seventy times every analog tree put together, and the sweep had
    # never looked at it.
    n_run = len(rows)
    rows.extend(dig_scratch_rows(now))
    n_dig = len(rows) - n_run
    rows.extend(loose_file_rows(now))
    sys.stderr.write("%d run dirs, %d dig scratch, %d loose files\n"
                     % (n_run, n_dig, len(rows) - n_run - n_dig))

    # ── supersession: a LATER COMPLETED sibling with the same base ───────
    # Computed across the whole set rather than per dir, because it is a
    # relationship: "is there a newer completed run of the same thing".
    # This is the feature that separates 'old' from 'obsolete', and no
    # amount of mtime gives it.
    by_key = {}
    for r in rows:
        by_key.setdefault((os.path.dirname(r["path"]), r["base"]), []).append(r)
    for group in by_key.values():
        newest_done = None
        for r in group:
            if r["completed"] and (newest_done is None or
                                   r["mtime"] > newest_done["mtime"]):
                newest_done = r
        for r in group:
            r["siblings"] = len(group)
            if (newest_done is not None and r is not newest_done and
                    r["mtime"] < newest_done["mtime"]):
                r["superseded_by"] = newest_done["name"]
            else:
                r["superseded_by"] = None

    json.dump({"generated": now, "host": os.uname()[1], "rows": rows},
              sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    sys.stderr.write("%d run dirs, %d roots\n" % (len(rows), len(roots)))


if __name__ == "__main__":
    main()
