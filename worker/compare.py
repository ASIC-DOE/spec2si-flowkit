"""The B-versus-C comparison on frozen tasks (agentic workflow study §8; docs/agentic_baseline.md).

    python -m worker.compare plan  --contracts worker/contracts/frozen --repeats 3 --seed 20260926 --out <dir>/plan.json
    python -m worker.compare run   --plan <dir>/plan.json --state-dir <dir> [--parallel 3]
    python -m worker.compare score --state-dir <dir> [--out <dir>/score.md]

`plan` fixes the randomized order of every (task, condition, repeat) once; `run`
executes what is not done yet (resumable: each item is recorded in
progress.jsonl with its run directory) through `worker.controller run
--condition B|C`; `score` reads every run's outcome.json. B is one ordinary
session judged by the same gates; C is the worker. Standard library only.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import random
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plan(contracts, repeats, seed):
    paths = sorted(glob.glob(os.path.join(contracts, "*.json")))
    items = [dict(contract=os.path.abspath(p).replace("\\", "/"), task=os.path.splitext(os.path.basename(p))[0],
                  condition=cond, repeat=r) for p in paths for cond in ("B", "C") for r in range(1, repeats + 1)]
    random.Random(seed).shuffle(items)
    for i, item in enumerate(items, 1):
        item["item"] = i
    return dict(schema=1, seed=seed, repeats=repeats, contracts=contracts, created=time.strftime("%Y-%m-%d %H:%M"),
                items=items)


def invalid(outcome):
    """A run that never reached its condition: the base's gates failed before any session or round
    (on 2026-09-26 a two-process race test flaked under the campaign's parallel load). It says
    nothing about B or C, so it is excluded from the score and its item runs again."""
    return outcome.get("stage") == "baseline"


def done_items(state_dir):
    """item -> its latest valid record."""
    path = os.path.join(state_dir, "progress.jsonl")
    done = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            rec = json.loads(line)
            if not rec.get("outcome"):
                continue
            with open(rec["outcome"], encoding="utf-8") as fh:
                if invalid(json.load(fh)):
                    continue
            done[rec["item"]] = rec
    return done


def invalid_runs(state_dir):
    path = os.path.join(state_dir, "progress.jsonl")
    runs = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            rec = json.loads(line)
            if rec.get("outcome"):
                with open(rec["outcome"], encoding="utf-8") as fh:
                    o = json.load(fh)
                if invalid(o):
                    runs.append(o["run"])
    return runs


_lock = threading.Lock()


def run_item(item, state_dir):
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "worker.controller", "run", "--contract", item["contract"],
                        "--state-dir", state_dir, "--condition", item["condition"]],
                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=4 * 3600)
    text = p.stdout.decode("utf-8", "replace")
    try:
        result = json.loads(text[text.index("{"):])
    except ValueError:
        result = {}
    run_dir = os.path.join(state_dir, result["run"]) if result.get("run") else None
    outcome = os.path.join(run_dir, "outcome.json") if run_dir else None
    rec = dict(item=item["item"], task=item["task"], condition=item["condition"], repeat=item["repeat"], rc=p.returncode,
               run=result.get("run"), outcome=outcome if outcome and os.path.exists(outcome) else None,
               seconds=round(time.time() - t0, 1), tail=None if result else text[-800:])
    with _lock:
        with open(os.path.join(state_dir, "progress.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    print("item %3d %-24s %s r%d -> %s (%s s)" % (item["item"], item["task"], item["condition"], item["repeat"],
                                                   result.get("status", "no result"), rec["seconds"]), flush=True)
    return rec


def run(plan_path, state_dir, parallel):
    with open(plan_path, encoding="utf-8") as fh:
        p = json.load(fh)
    os.makedirs(state_dir, exist_ok=True)
    done = done_items(state_dir)
    todo = [i for i in p["items"] if i["item"] not in done]
    print("%d of %d items to run" % (len(todo), len(p["items"])), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        list(pool.map(lambda i: run_item(i, state_dir), todo))


def outcomes(state_dir):
    rows = []
    for rec in done_items(state_dir).values():
        with open(rec["outcome"], encoding="utf-8") as fh:
            rows.append(json.load(fh))
    return rows


def score(state_dir):
    rows = outcomes(state_dir)
    tasks = sorted({r["task"] for r in rows})
    lines = ["| Task | Harness | Cond. | Accepted | False acceptance | Scope excursions | Claimed done / blocked "
             "| Rounds | Minutes (mean) | Cost $ (mean) | Tokens (mean) |",
             "|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|"]
    totals = {}
    for t in tasks:
        for cond in ("B", "C"):
            rs = [r for r in rows if r["task"] == t and r["condition"] == cond]
            if not rs:
                continue
            n = len(rs)
            acc = sum(1 for r in rs if r["accepted"])
            false = sum(1 for r in rs if r["claimed"] in ("done", "ready-for-review") and not r["accepted"])
            scope = sum(1 for r in rs if r["protected_touched"] or r["outside_editable"])
            done = sum(1 for r in rs if r["claimed"] in ("done", "ready-for-review"))
            blocked = sum(1 for r in rs if r["claimed"] == "blocked" or r["status"] == "stopped")
            mean = lambda k: sum(r[k] or 0 for r in rs) / n
            tok = [r["tokens"] for r in rs if r["tokens"] is not None]
            lines.append("| %s | %s | %s | %d/%d | %d | %d | %d / %d | %.1f | %.1f | %.2f | %s |" % (
                t, rs[0]["harness"], cond, acc, n, false, scope, done, blocked, mean("rounds"), mean("minutes"),
                mean("cost_usd"), "%dk" % (sum(tok) / len(tok) / 1000) if tok else "-"))
            s = totals.setdefault(cond, dict(n=0, acc=0, false=0, scope=0, minutes=0.0, cost=0.0))
            s["n"] += n
            s["acc"] += acc
            s["false"] += false
            s["scope"] += scope
            s["minutes"] += sum(r["minutes"] or 0 for r in rs)
            s["cost"] += sum(r["cost_usd"] or 0 for r in rs)
    lines += ["", "| Condition | Accepted | False acceptances | Scope excursions | Minutes (total) | Claude $ (total) |",
              "|---|---:|---:|---:|---:|---:|"]
    for cond, s in sorted(totals.items()):
        lines.append("| %s | %d/%d | %d | %d | %.0f | %.2f |" % (cond, s["acc"], s["n"], s["false"], s["scope"],
                                                                s["minutes"], s["cost"]))
    versions = sorted({"%s %s" % (r["harness"], r["harness_version"]) for r in rows})
    lines += ["", "Harness versions: " + "; ".join(versions) + "."]
    bad = invalid_runs(state_dir)
    lines += ["Invalid runs (the base's gates failed before the condition ran; excluded and re-run): %s." % (
        ", ".join(bad) if bad else "none"), ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="op", required=True)
    a = sub.add_parser("plan")
    a.add_argument("--contracts", required=True)
    a.add_argument("--repeats", type=int, default=3)
    a.add_argument("--seed", type=int, required=True)
    a.add_argument("--out", required=True)
    b = sub.add_parser("run")
    b.add_argument("--plan", required=True)
    b.add_argument("--state-dir", required=True)
    b.add_argument("--parallel", type=int, default=3)
    c = sub.add_parser("score")
    c.add_argument("--state-dir", required=True)
    c.add_argument("--out")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.op == "plan":
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        if os.path.exists(args.out):
            print("refusing to overwrite a plan: %s" % args.out)
            return 2
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(plan(args.contracts, args.repeats, args.seed), fh, indent=1)
        print(args.out)
    elif args.op == "run":
        run(args.plan, args.state_dir, args.parallel)
    else:
        text = score(args.state_dir)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
