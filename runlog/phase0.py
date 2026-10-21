#!/usr/bin/env python3
"""Cross-repo phase-0 sampling for the agent-loop runlogs.

The consumer runlogs deliberately harvest METADATA and never infer terminal
cause. This tool preserves that contract. It draws a small reproducible sample
across repository/domain/tier, adds compact review context from adjacent
attempts and nearby git commit SUBJECTS, and emits the existing manual classify
command. Suggestions are evidence only: this module never writes a cause.

Generated packets go under runlog/work/, which is ignored. Flowkit is public;
consumer design names and commit subjects do not belong in it.

  python3 runlog/phase0.py sample --n 24 --seed 20260902
  python3 runlog/phase0.py report --sample runlog/work/phase0_review.json
"""
from __future__ import print_function

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONSUMERS = os.path.join(ROOT, "consumers.json")
WORK = os.path.join(HERE, "work")
DEFAULT_JSON = os.path.join(WORK, "phase0_review.json")
DEFAULT_MD = os.path.join(WORK, "phase0_review.md")

CAUSES = (
    "closed", "gate-fail", "silent-pass", "engine-defect",
    "stale-artifact", "tool-error", "transport", "abandoned",
    "unclassified",
)

# Explicit subject words only. A match is a suggestion to a reviewer, never a
# classification. Ordered from the narrow failure shapes to broad outcomes.
_HINTS = (
    ("silent-pass", ("silent", "false pass", "passed on nothing",
                     "pass on nothing", "empty input")),
    ("stale-artifact", ("stale", "drift", "old artifact", "wrong artifact",
                        "lost artifact", "not restored")),
    ("transport", ("ssh", "rsync", "sync failure", "remote", "endpoint",
                   "cluster seat", "seat never")),
    ("tool-error", ("licence", "license", "crash", "tool error",
                    "timeout", "expired")),
    ("abandoned", ("abandon", "superseded", "withdrawn", "dropped",
                   "deferred")),
    ("gate-fail", ("gate fail", "refuse", "violation", "mismatch",
                   "not clean", "still fails")),
    ("engine-defect", ("engine defect", "bug", "wrong constant",
                       "hardcoded", "derive", "fix")),
    ("closed", ("closed", "signed off", "done-pass", "all pass",
                "lvs-correct", "drc-clean", "complete")),
)


def native_path(path):
    """A consumers.json Windows path as a path usable by this interpreter."""
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path or "")
    if os.name != "nt" and match:
        return "/mnt/{}/{}".format(match.group(1).lower(),
                                   match.group(2).replace("\\", "/"))
    return path


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def current_records(path):
    """Last-wins view of an append-only consumer runlog."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                out[rec["id"]] = rec
    return out


def load_population(config=CONSUMERS):
    """(attempt rows, source manifest), excluding no honest backlog."""
    cfg = _read_json(config)
    rows, sources = [], []
    for c in cfg.get("consumers", []):
        repo = c["name"]
        root = native_path(c["path"])
        log = os.path.join(root, "analog", "specs", "runlog.jsonl")
        if not os.path.exists(log):
            sources.append({"repo": repo, "status": "missing"})
            continue
        recs = current_records(log)
        sources.append({"repo": repo, "status": "read",
                        "sha256": _sha256(log), "records": len(recs)})
        for rec in recs.values():
            row = dict(rec)
            row["repo"] = repo
            row["repo_root"] = root       # runtime only; omitted from packet
            row["domain"] = row.get("domain") or "analog"
            rows.append(row)
    return rows, sources


def _stratum(row):
    return (row["repo"], row.get("domain") or "analog",
            row.get("tier") or "offline")


def _allocate(strata, n):
    """Exact proportional allocation with one seat per non-empty stratum."""
    keys = sorted(strata)
    if not keys:
        return {}
    n = min(max(int(n), len(keys)), sum(len(strata[k]) for k in keys))
    alloc = dict((k, 1) for k in keys)
    left = n - len(keys)
    capacity = dict((k, len(strata[k]) - 1) for k in keys)
    while left:
        total_cap = sum(capacity.values())
        if not total_cap:
            break
        ideal = dict((k, left * capacity[k] / float(total_cap)) for k in keys)
        add = dict((k, min(capacity[k], int(math.floor(ideal[k]))))
                   for k in keys)
        used = sum(add.values())
        for k in keys:
            alloc[k] += add[k]
            capacity[k] -= add[k]
        left -= used
        if not left:
            break
        order = sorted(keys, key=lambda k: (-(ideal[k] - math.floor(ideal[k])),
                                             k))
        progressed = False
        for k in order:
            if left and capacity[k]:
                alloc[k] += 1
                capacity[k] -= 1
                left -= 1
                progressed = True
        if not progressed:
            break
    return alloc


def draw(rows, n=24, seed=20260902):
    """Reproducible sample of the UNCLASSIFIED population."""
    pool = [r for r in rows if r.get("cause") == "unclassified"]
    strata = {}
    for row in pool:
        strata.setdefault(_stratum(row), []).append(row)
    alloc = _allocate(strata, n)
    rnd = random.Random(seed)
    picked = []
    for key in sorted(strata):
        candidates = sorted(strata[key], key=lambda r: (r.get("when", ""),
                                                         r["id"]))
        chosen = rnd.sample(candidates, alloc[key])
        weight = len(candidates) / float(len(chosen))
        for row in chosen:
            q = dict(row)
            q["sample_weight"] = weight
            q["stratum_population"] = len(candidates)
            q["stratum_sample"] = len(chosen)
            picked.append(q)
    picked.sort(key=lambda r: (r.get("when", ""), r["repo"], r["id"]))
    return picked, dict(("/".join(k), len(v)) for k, v in strata.items())


def _time(value):
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+0000"
    elif (len(value) >= 6 and value[-6] in "+-" and value[-3] == ":"):
        value = value[:-3] + value[-2:]
    for form in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(value, form)
        except (TypeError, ValueError):
            pass
    return None


def adjacent(row, rows, limit=2):
    """Nearest attempts in the same session/cell; context, not shared label."""
    peers = [r for r in rows
             if r.get("repo") == row.get("repo")
             and r.get("session") == row.get("session")
             and r.get("cell") == row.get("cell")
             and r.get("id") != row.get("id")]
    target = _time(row.get("when"))
    peers.sort(key=lambda r: abs(((_time(r.get("when")) or target) - target)
                                 .total_seconds()) if target else 0)
    return [compact_attempt(p) for p in peers[:limit]]


def compact_attempt(row):
    """Only fields already allowed by the metadata-only runlog contract."""
    return {
        "id": row.get("id"), "when": row.get("when"),
        "session": row.get("session"), "cell": row.get("cell"),
        "domain": row.get("domain") or "analog", "tier": row.get("tier"),
        "actions": row.get("actions", 0), "errors": row.get("errors", 0),
        "duration_s": row.get("dur_s"), "files": row.get("files", []),
        "tools": row.get("tools", []), "thrash": row.get("thrash", []),
        "partial": bool(row.get("partial")),
    }


def hint_causes(subject):
    text = " " + (subject or "").lower() + " "
    return [cause for cause, words in _HINTS if any(w in text for w in words)]


def git_commits(repo_root, since, until):
    """Commit subjects and changed basenames; no file contents or diffs."""
    cmd = ["git", "log", "--all", "--since=" + since, "--until=" + until,
           "--format=%x1e%H%x1f%aI%x1f%s", "--name-only"]
    try:
        raw = subprocess.check_output(cmd, cwd=repo_root,
                                      stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError):
        return []
    out = []
    for block in raw.decode("utf-8", "replace").split("\x1e"):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        head = lines[0].split("\x1f", 2)
        if len(head) != 3:
            continue
        files = [os.path.basename(p.replace("\\", "/"))
                 for p in lines[1:] if p.strip()]
        out.append({"commit": head[0][:12], "when": head[1],
                    "subject": head[2], "files": sorted(set(files))})
    return out


def commit_evidence(row, commits, limit=3):
    target = _time(row.get("when"))
    wanted = set(f.lower() for f in row.get("files", []))
    ranked = []
    for commit in commits:
        stamp = _time(commit.get("when"))
        if not target or not stamp:
            continue
        hours = (stamp - target).total_seconds() / 3600.0
        if hours < -12 or hours > 96:
            continue
        overlap = sorted(wanted.intersection(
            f.lower() for f in commit.get("files", [])))
        hints = hint_causes(commit.get("subject"))
        if not overlap and not hints:
            continue
        score = 100 * len(overlap) + 20 * len(hints) - abs(hours) / 12.0
        ranked.append((score, {
            "commit": commit["commit"], "when": commit["when"],
            "hours_after_attempt": round(hours, 1),
            "subject": commit["subject"], "file_overlap": overlap,
            "cause_hints": hints,
            "warning": "review evidence only; not a classification",
        }))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["commit"]))
    return [item for _score, item in ranked[:limit]]


def _window(rows):
    times = [_time(r.get("when")) for r in rows]
    times = [t for t in times if t]
    if not times:
        return None, None
    return (min(times) - dt.timedelta(hours=12)).isoformat(), \
        (max(times) + dt.timedelta(hours=96)).isoformat()


def build_packet(rows, picked, sources, seed, strata):
    by_repo = {}
    for row in picked:
        by_repo.setdefault(row["repo"], []).append(row)
    commits = {}
    for repo, selected in by_repo.items():
        since, until = _window(selected)
        commits[repo] = (git_commits(selected[0]["repo_root"], since, until)
                         if since else [])
    items = []
    for row in picked:
        evidence = commit_evidence(row, commits.get(row["repo"], []))
        hints = []
        for ev in evidence:
            for cause in ev["cause_hints"]:
                if cause not in hints:
                    hints.append(cause)
        item = compact_attempt(row)
        item.update({
            "repo": row["repo"], "sample_weight": row["sample_weight"],
            "stratum_population": row["stratum_population"],
            "stratum_sample": row["stratum_sample"],
            "adjacent_attempts": adjacent(row, rows),
            "commit_evidence": evidence,
            "suggested_causes": hints,
            "review": {"cause": None, "note": ""},
            "classify_command": "cd ../{0}/browse && python3 runlog.py classify {1} <cause> [note]".format(
                row["repo"], row["id"]),
        })
        items.append(item)
    classified = [r for r in rows if r.get("cause") != "unclassified"]
    return {
        "schema": "spec2si-agent-memory-phase0/1",
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "seed": seed, "population": len(rows),
        "unclassified_population": len(rows) - len(classified),
        "already_classified": len(classified), "sample_n": len(items),
        "strata": strata, "sources": sources,
        "sampling_note": "first sequential sample; add 16 only if leading causes or promotion choices remain unclear",
        "classification_rule": "suggestions and commit subjects are review evidence only; only review.cause is a human decision",
        "items": items,
    }


def render_markdown(packet):
    lines = [
        "# Phase 0 runlog review",
        "",
        "Generated `{}`; seed `{}`. **{} selected from {} unclassified "
        "attempts** ({} total records).".format(
            packet["generated"], packet["seed"], packet["sample_n"],
            packet["unclassified_population"], packet["population"]),
        "",
        "> Commit subjects and suggested causes are evidence only. They are "
        "never written as classifications. Review the selected attempt itself.",
        "",
        "Use `python3 browse/runlog.py causes` in a consumer for the closed "
        "definitions. Add the planned 16-record increment only if this first "
        "sample does not separate the leading causes.",
        "",
    ]
    for i, item in enumerate(packet["items"], 1):
        lines.extend([
            "## {}. `{}` / `{}` / `{}`".format(
                i, item["repo"], item["cell"], item["id"]),
            "",
            "- `{}`; {}/{}; {} actions, {} failed tool calls, {} s; weight {:.3g}.".format(
                item["when"], item["domain"], item["tier"], item["actions"],
                item["errors"], item["duration_s"], item["sample_weight"]),
            "- Files: " + ", ".join("`{}`".format(f) for f in item["files"]),
        ])
        if item["suggested_causes"]:
            lines.append("- Subject-word hints: **{}** — verify; do not copy blindly."
                         .format(", ".join(item["suggested_causes"])))
        for ev in item["commit_evidence"]:
            lines.append("- Commit `{}` ({:+.1f} h, overlap `{}`): {}"
                         .format(ev["commit"], ev["hours_after_attempt"],
                                 ", ".join(ev["file_overlap"]) or "none",
                                 ev["subject"]))
        if item["adjacent_attempts"]:
            lines.append("- Same-session/cell neighbors: " + ", ".join(
                "`{}` at `{}` ({}/{}; {} actions)".format(
                    a["id"], a["when"], a["domain"], a["tier"], a["actions"])
                for a in item["adjacent_attempts"]))
        lines.extend([
            "- Decision: `________________`  Note: `____________________________`",
            "- Command: `" + item["classify_command"] + "`",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def write_packet(packet, json_path=DEFAULT_JSON, md_path=DEFAULT_MD):
    for path in (json_path, md_path):
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    with open(json_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(packet, fh, indent=1)
        fh.write("\n")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_markdown(packet))


def _wilson(success, total, z=1.96):
    if not total:
        return 0.0, 0.0
    p = success / float(total)
    d = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def report(packet, rows):
    current = dict((r["repo"] + "/" + r["id"], r) for r in rows)
    counts = dict((c, 0) for c in CAUSES if c != "unclassified")
    missing = []
    weighted, total_weight = dict((c, 0.0) for c in counts), 0.0
    for item in packet["items"]:
        rec = current.get(item["repo"] + "/" + item["id"])
        cause = rec.get("cause") if rec else None
        if not cause or cause == "unclassified":
            missing.append(item["repo"] + "/" + item["id"])
            continue
        counts[cause] = counts.get(cause, 0) + 1
        weighted[cause] = weighted.get(cause, 0.0) + item["sample_weight"]
        total_weight += item["sample_weight"]
    reviewed = len(packet["items"]) - len(missing)
    out = ["phase 0: {} of {} sampled attempt(s) classified".format(
        reviewed, len(packet["items"]))]
    if missing:
        out.append("remaining: " + ", ".join(missing))
    if not reviewed:
        return "\n".join(out)
    out.append("sample proportions (Wilson 95% interval; sample, not census):")
    for cause, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if not count:
            continue
        lo, hi = _wilson(count, reviewed)
        wp = weighted[cause] / total_weight if total_weight else 0.0
        out.append("  {:16s} {:2d}/{:<2d} = {:5.1f}%  95% [{:4.1f}, {:4.1f}]  weighted {:5.1f}%"
                   .format(cause, count, reviewed, 100 * count / reviewed,
                           100 * lo, 100 * hi, 100 * wp))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sample_p = sub.add_parser("sample")
    sample_p.add_argument("--n", type=int, default=24)
    sample_p.add_argument("--seed", type=int, default=20260902)
    sample_p.add_argument("--json", default=DEFAULT_JSON)
    sample_p.add_argument("--markdown", default=DEFAULT_MD)
    report_p = sub.add_parser("report")
    report_p.add_argument("--sample", default=DEFAULT_JSON)
    args = parser.parse_args(argv)
    if args.command == "sample":
        rows, sources = load_population()
        picked, strata = draw(rows, args.n, args.seed)
        packet = build_packet(rows, picked, sources, args.seed, strata)
        write_packet(packet, args.json, args.markdown)
        print("phase 0: selected {} of {} unclassified attempt(s) across {} strata"
              .format(packet["sample_n"], packet["unclassified_population"],
                      len(strata)))
        print("review: " + os.path.abspath(args.markdown))
        print("manifest: " + os.path.abspath(args.json))
        return 0
    if args.command == "report":
        rows, _sources = load_population()
        print(report(_read_json(args.sample), rows))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
