#!/usr/bin/env python3
"""Periodically mine ranked themes from the five repositories' Markdown.

This is candidate generation, not knowledge promotion. The miner is
deterministic and stdlib-only. It ranks cross-repository themes separately
from local ones, discounts disposable logs, records a corpus digest, and keeps
stable theme ids plus rank deltas across refreshes.

The combined corpus includes private process ports. Outputs therefore live in
the ignored knowledge/work directory and must not be committed to the public
flowkit. A reviewed and sanitized lesson can later be promoted explicitly.

  python3 knowledge/themes.py refresh --top 30
  python3 knowledge/themes.py check
  python3 knowledge/themes.py show
"""
from __future__ import print_function

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONSUMERS = os.path.join(ROOT, "consumers.json")
WORK = os.path.join(HERE, "work")
SNAPSHOT = os.path.join(WORK, "themes.json")
REPORT = os.path.join(WORK, "themes.md")

SCHEMA = "spec2si-markdown-themes/4"
GENRE_WEIGHT = {
    "finding": 1.45, "decision": 1.35, "study": 1.30,
    "guide": 1.05, "plan": 0.90, "overview": 0.75,
    "reference": 0.55, "log": 0.35, "unknown": 0.60,
}

# Language glue plus corpus nouns too broad to be useful as themes. Terms such
# as stale, artifact, negative, control, restore and signoff deliberately stay.
STOP = set("""
a about above after again against all also am an and any are as at be because
been before being below between both but by can could did do does doing down
during each few for from further had has have having he her here hers herself
him himself his how i if in into is it its itself just me more most my myself
no nor not now of off on once only or other our ours ourselves out over own
same she should so some such than that the their theirs them themselves then
there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your yours
repo repository repositories file files section sections step steps result
results work works working design designs flow flows cell cells block blocks
analog digital spec specs code script scripts python markdown document documents
tsmc skywater sky xt nm um md json read write first new one two three four
spec2si tsmc65 tsmc28 xt011 sky130 flowkit per side every
""".split())

ACTION = set("""
silent stale artifact negative control verify verification gate refuse refusal
derive derived restore provenance signoff audit baseline render hash drift
failure error wrong correction lesson check mismatch missing measured evidence
superseded invariant preflight regression validity trusted
""".split())

SKIP_DIRS = {".git", "site", "manual", "__pycache__", ".pytest_cache"}
DOCMETA = re.compile(r"^<!--docmeta\s*\n(.*?)\n-->\s*", re.DOTALL)
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
CODE_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
TOKEN = re.compile(r"[a-z][a-z0-9]{2,}")


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = json.dumps(value, indent=1, sort_keys=False) + "\n"
    old = None
    try:
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    except OSError:
        pass
    if old == body:
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return True


def _write_text(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = None
    try:
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    except OSError:
        pass
    if old == body:
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return True


def _sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def native_path(path):
    """A consumers.json Windows path as a path usable under Windows or WSL."""
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path or "")
    if os.name != "nt" and match:
        return "/mnt/{}/{}".format(match.group(1).lower(),
                                   match.group(2).replace("\\", "/"))
    return path


def repositories(config=CONSUMERS):
    cfg = _read_json(config)
    out = [{"name": "spec2si-flowkit", "path": ROOT}]
    out.extend({"name": c["name"], "path": native_path(c["path"])}
               for c in cfg.get("consumers", []))
    return out


def _repo_markdown(root):
    """Tracked plus non-ignored untracked Markdown; never generated ignores."""
    cmd = ["git", "ls-files", "-z", "--cached", "--others",
           "--exclude-standard", "--", "*.md"]
    try:
        import subprocess
        raw = subprocess.check_output(cmd, cwd=root, stderr=subprocess.STDOUT)
        return sorted(p for p in raw.decode("utf-8", "replace").split("\0")
                      if p and os.path.isfile(os.path.join(root, p)))
    except (OSError, subprocess.CalledProcessError):
        out = []
        for base, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs
                             if d not in SKIP_DIRS and d != "work")
            for name in sorted(files):
                if name.lower().endswith(".md"):
                    out.append(os.path.relpath(os.path.join(base, name), root))
        return sorted(out)


def markdown_files(repos):
    """[(repo, root, relative path, absolute path)] in stable order."""
    out = []
    for repo in repos:
        root = repo["path"]
        if not os.path.isdir(root):
            continue
        for rel in _repo_markdown(root):
            rel = rel.replace("\\", "/")
            out.append((repo["name"], root, rel,
                        os.path.join(root, rel.replace("/", os.sep))))
    return sorted(out, key=lambda x: (x[0], x[2]))


def corpus_manifest(repos):
    files, h = [], hashlib.sha256()
    for repo, _root, rel, path in markdown_files(repos):
        try:
            raw = open(path, "rb").read()
        except OSError:
            continue
        digest = _sha256_bytes(raw)
        key = repo + "/" + rel
        files.append({"key": key, "sha256": digest, "bytes": len(raw)})
        h.update((key + "\0" + digest + "\n").encode("utf-8"))
    return {"digest": h.hexdigest(), "file_count": len(files),
            "bytes": sum(f["bytes"] for f in files), "files": files}


def _meta(text):
    match = DOCMETA.match(text)
    out = {}
    if not match:
        return out, text
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out, text[match.end():]


def _stem(word):
    special = {"failures": "failure", "failed": "failure",
               "failing": "failure", "derived": "derive",
               "deriving": "derive", "verified": "verify",
               "verifying": "verify", "corrections": "correction",
               "lessons": "lesson", "controls": "control",
               "artifacts": "artifact", "artefact": "artifact",
               "artefacts": "artifact", "checks": "check",
               "gated": "gate", "gating": "gate",
               "identically": "identical", "silently": "silent",
               "staleness": "stale"}
    if word in special:
        return special[word]
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _phrases(text):
    """Counter of normalized one-to-three-token phrases."""
    text = CODE_FENCE.sub(" ", text)
    text = HTML_COMMENT.sub(" ", text)
    text = LINK.sub(lambda m: " " + m.group(1) + " ", text)
    text = text.replace("_", " ").replace("-", " ").lower()
    spans = []
    # Sentence punctuation and stopwords both break phrases; whitespace must
    # not, or every candidate collapses to a unigram. Commas stay inside a
    # sentence but become token separators through TOKEN.findall().
    for chunk in re.split(r"[.!?;:\n\r]+", text):
        current = []
        words = [_stem(w) for w in TOKEN.findall(chunk)]
        for word in words:
            if word in STOP or len(word) < 3:
                if current:
                    spans.append(current)
                    current = []
            else:
                current.append(word)
        if current:
            spans.append(current)
    out = collections.Counter()
    for span in spans:
        for size in (1, 2, 3):
            for i in range(0, len(span) - size + 1):
                phrase = " ".join(span[i:i + size])
                out[phrase] += 1
    return out


def parse_document(repo, root, rel, path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    meta, body = _meta(text)
    headings = "\n".join(line.lstrip("# ") for line in body.splitlines()
                           if line.startswith("#"))
    title = meta.get("title") or (headings.splitlines()[0]
                                  if headings.splitlines() else rel)
    genre = meta.get("genre", "unknown").lower()
    if genre not in GENRE_WEIGHT:
        genre = "unknown"
    phrases = _phrases(body)
    # Title, summary and headings express deliberate subjects. Add them again
    # rather than letting a large log's body drown its own declared subject.
    phrases.update(_phrases("\n".join(
        [title, meta.get("summary", ""), headings])))
    return {"repo": repo, "path": rel, "title": title, "genre": genre,
            "weight": GENRE_WEIGHT[genre], "phrases": phrases}


def documents(repos):
    out = []
    for repo, root, rel, path in markdown_files(repos):
        doc = parse_document(repo, root, rel, path)
        if doc:
            out.append(doc)
    return out


def _theme_id(label):
    return "theme-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]


def _candidate(label, support, n_docs):
    docs = support["docs"]
    repos = sorted(support["repos"])
    doc_count = len(docs)
    repo_count = len(repos)
    idf = math.log((n_docs + 1.0) / (doc_count + 0.5)) + 1.0
    mean_weight = sum(d["weight"] for d in docs.values()) / doc_count
    length_boost = {1: 0.65, 2: 1.20, 3: 1.35}[len(label.split())]
    cues = sorted(set(label.split()).intersection(ACTION))
    action_boost = 1.0 + 0.10 * len(cues)
    score = ((repo_count ** 1.45) * math.log1p(doc_count) * idf *
             mean_weight * length_boost * action_boost)
    evidence = sorted(docs.values(),
                      key=lambda d: (-d["strength"], d["repo"], d["path"]))
    genre_counts = collections.Counter(d["genre"] for d in docs.values())
    return {
        "id": _theme_id(label), "label": label, "score": round(score, 4),
        "repo_count": repo_count, "repos": repos, "doc_count": doc_count,
        "genre_counts": dict(sorted(genre_counts.items())),
        "actionability_cues": cues,
        "evidence": [{"repo": d["repo"], "path": d["path"],
                      "title": d["title"], "genre": d["genre"],
                      "mentions": d["mentions"]}
                     for d in evidence[:6]],
        "_doc_keys": set(docs),
    }


def _similar(a, b):
    wa, wb = set(a["label"].split()), set(b["label"].split())
    lexical = len(wa.intersection(wb)) / float(len(wa.union(wb)))
    da, db = a["_doc_keys"], b["_doc_keys"]
    evidence = len(da.intersection(db)) / float(min(len(da), len(db)))
    return lexical >= 0.67 and evidence >= 0.60


def _dedupe(candidates, limit):
    selected = []
    for candidate in sorted(candidates, key=lambda c: (-c["score"], c["label"])):
        if any(_similar(candidate, keep) for keep in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    for rank, candidate in enumerate(selected, 1):
        candidate["rank"] = rank
        candidate.pop("_doc_keys", None)
    return selected


def rank_themes(docs, top=30, min_repos=2, require_actionable=False):
    support = {}
    for doc in docs:
        key = doc["repo"] + "/" + doc["path"]
        for label, mentions in doc["phrases"].items():
            words = label.split()
            # Repetition at a sentence boundary ("negative control. Negative
            # control") can otherwise manufacture labels such as "control
            # negative control" and outscore the natural bigram.
            if len(set(words)) != len(words):
                continue
            # A singleton one-word mention is almost always vocabulary, not a
            # theme. Multiword phrases may earn candidacy from one mention.
            if len(words) == 1 and mentions < 2:
                continue
            row = support.setdefault(label, {"repos": set(), "docs": {}})
            row["repos"].add(doc["repo"])
            row["docs"][key] = {
                "repo": doc["repo"], "path": doc["path"],
                "title": doc["title"], "genre": doc["genre"],
                "weight": doc["weight"], "mentions": mentions,
                "strength": doc["weight"] * (1.0 + math.log1p(mentions)),
            }
    candidates = [_candidate(label, row, len(docs))
                  for label, row in support.items()
                  if len(row["docs"]) >= 2]
    general = [c for c in candidates if c["repo_count"] >= min_repos]
    if require_actionable:
        general = [c for c in general if c["actionability_cues"]]
    local = [c for c in candidates if c["repo_count"] == 1]
    return _dedupe(general, top), _dedupe(local, top)


def _delta(previous, current):
    if not previous:
        return {"baseline": True, "added": [t["id"] for t in current],
                "dropped": [], "rank_changes": []}
    old = dict((t["id"], t) for t in previous)
    new = dict((t["id"], t) for t in current)
    changes = []
    for tid in sorted(set(old).intersection(new)):
        if old[tid]["rank"] != new[tid]["rank"]:
            changes.append({"id": tid, "label": new[tid]["label"],
                            "from": old[tid]["rank"], "to": new[tid]["rank"]})
    changes.sort(key=lambda c: (abs(c["to"] - c["from"]), c["id"]),
                 reverse=True)
    return {"baseline": False, "added": sorted(set(new) - set(old)),
            "dropped": sorted(set(old) - set(new)),
            "rank_changes": changes[:20]}


def make_snapshot(repos, top=30, min_repos=2, previous=None):
    manifest = corpus_manifest(repos)
    docs = documents(repos)
    # A broad thematic view and a narrower knowledge-candidate view answer
    # different questions. Mine a deeper broad list, then retain the phrases
    # carrying explicit actionability cues for the latter.
    wide_general, _unused = rank_themes(docs, top, min_repos)
    general = wide_general[:top]
    actionable, _unused_local = rank_themes(
        docs, top, min_repos, require_actionable=True)
    # A single global local ranking is just a ranking by corpus size and was
    # entirely tsmc65 in the first live run. Give each port ten seats instead.
    local = []
    for repo in sorted(set(d["repo"] for d in docs)):
        _none, per_repo = rank_themes(
            [d for d in docs if d["repo"] == repo], 10, min_repos)
        for theme in per_repo:
            theme["scope_repo"] = repo
            theme["id"] += "-" + hashlib.sha256(
                repo.encode("utf-8")).hexdigest()[:6]
            local.append(theme)
    old_general = (previous or {}).get("general_themes", [])
    old_actionable = (previous or {}).get("actionable_themes", [])
    old_local = (previous or {}).get("local_themes", [])
    return {
        "schema": SCHEMA,
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "parameters": {"top": top, "min_repos": min_repos,
                       "genre_weight": GENRE_WEIGHT},
        "corpus": manifest,
        "document_count": len(docs),
        "warning": "derived candidates from private and public text; review and sanitize before promotion",
        "actionable_themes": actionable,
        "general_themes": general, "local_themes": local,
        "changes": {"general": _delta(old_general, general),
                    "actionable": _delta(old_actionable, actionable),
                    "local": _delta(old_local, local)},
    }


def render(snapshot):
    corpus = snapshot["corpus"]
    lines = [
        "# Periodic Markdown themes",
        "",
        "Generated `{}` from **{} Markdown documents / {:.2f} MiB**, corpus "
        "`{}`.".format(snapshot["generated"], snapshot["document_count"],
                       corpus["bytes"] / float(1 << 20), corpus["digest"][:16]),
        "",
        "> Derived candidates only. The corpus includes private ports. Do not "
        "commit this report or promote a theme without reviewing its evidence.",
        "",
    ]
    for heading, key in (("Actionable cross-repository candidates", "actionable_themes"),
                         ("Broad cross-repository themes", "general_themes")):
        lines.extend(["## " + heading, ""])
        for theme in snapshot[key]:
            evidence = "; ".join("{}/{}".format(e["repo"], e["path"])
                                 for e in theme["evidence"][:3])
            lines.append("{}. **{}** — score {:.2f}; {} repos / {} docs; `{}`."
                         .format(theme["rank"], theme["label"], theme["score"],
                                 theme["repo_count"], theme["doc_count"], evidence))
        lines.append("")
    lines.extend(["## Per-repository themes", ""])
    grouped = collections.defaultdict(list)
    for theme in snapshot["local_themes"]:
        grouped[theme["scope_repo"]].append(theme)
    for repo in sorted(grouped):
        lines.extend(["### " + repo, ""])
        for theme in grouped[repo]:
            evidence = "; ".join("{}/{}".format(e["repo"], e["path"])
                                 for e in theme["evidence"][:3])
            lines.append("{}. **{}** — score {:.2f}; {} docs; `{}`."
                         .format(theme["rank"], theme["label"], theme["score"],
                                 theme["doc_count"], evidence))
        lines.append("")
    change = snapshot["changes"]["general"]
    lines.extend(["## Since the previous refresh", "",
                  "- Added: {}".format(len(change["added"])),
                  "- Dropped from top set: {}".format(len(change["dropped"])),
                  "- Rank changes recorded: {}".format(len(change["rank_changes"])),
                  ""])
    return "\n".join(lines).rstrip() + "\n"


def refresh(top=30, min_repos=2, snapshot_path=SNAPSHOT, report_path=REPORT):
    try:
        previous = _read_json(snapshot_path)
    except (OSError, ValueError):
        previous = None
    repos = repositories()
    current_manifest = corpus_manifest(repos)
    same = (previous and previous.get("schema") == SCHEMA
            and previous.get("corpus", {}).get("digest") == current_manifest["digest"]
            and previous.get("parameters", {}).get("top") == top
            and previous.get("parameters", {}).get("min_repos") == min_repos)
    if same:
        return previous, False
    snapshot = make_snapshot(repos, top, min_repos, previous)
    _write_json(snapshot_path, snapshot)
    _write_text(report_path, render(snapshot))
    return snapshot, True


def check(snapshot_path=SNAPSHOT):
    try:
        snapshot = _read_json(snapshot_path)
    except (OSError, ValueError):
        return False, "MISSING: run themes.py refresh"
    if snapshot.get("schema") != SCHEMA:
        return False, "STALE: theme algorithm/schema changed; run themes.py refresh"
    manifest = corpus_manifest(repositories())
    old = dict((f["key"], f["sha256"])
               for f in snapshot.get("corpus", {}).get("files", []))
    new = dict((f["key"], f["sha256"]) for f in manifest["files"])
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old).intersection(new) if old[k] != new[k])
    ok = snapshot.get("corpus", {}).get("digest") == manifest["digest"]
    if ok:
        return True, "CURRENT: {} Markdown files, corpus {}".format(
            manifest["file_count"], manifest["digest"][:16])
    return False, "STALE: {} added, {} changed, {} removed; run themes.py refresh".format(
        len(added), len(changed), len(removed))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    refresh_p = sub.add_parser("refresh")
    refresh_p.add_argument("--top", type=int, default=30)
    refresh_p.add_argument("--min-repos", type=int, default=2)
    sub.add_parser("check")
    sub.add_parser("show")
    args = parser.parse_args(argv)
    if args.command == "refresh":
        snap, changed = refresh(args.top, args.min_repos)
        print("{}: {} actionable + {} general + {} local themes from {} documents; corpus {}"
              .format("UPDATED" if changed else "UNCHANGED",
                      len(snap["actionable_themes"]),
                      len(snap["general_themes"]), len(snap["local_themes"]),
                      snap["document_count"], snap["corpus"]["digest"][:16]))
        print("report: " + REPORT)
        return 0
    if args.command == "check":
        ok, message = check()
        print(message)
        return 0 if ok else 1
    if args.command == "show":
        try:
            print(render(_read_json(SNAPSHOT)), end="")
            return 0
        except (OSError, ValueError):
            print("MISSING: run themes.py refresh")
            return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
