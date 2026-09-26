"""Deterministic controller for one bounded worker task (agentic workflow study §9.2).

    python -m worker.controller run --contract task.json --state-dir C:/dev/.spec2si-job-state/worker
    python -m worker.controller show --run <run dir>

The task contract is the decision that exploration (chat) hands to
implementation (study §4.11). The controller, not the model, owns everything
that must be trusted:

  1. Preflight. A git worktree on a new branch `worker/<run id>` from the
     contract's base commit, inside the run directory (never the engineer's
     checkout). The gates run once on the base: the acceptance gates must
     FAIL (else there is nothing to do) and the others must PASS (else a
     result could not be attributed to the change).
  2. Rounds. A headless worker (worker/harness.py) gets the contract, the
     ledger so far and the last gate results, edits the worktree, and returns
     a schema-checked proposal: patch or stop, with observations kept apart
     from hypotheses.
  3. Checks, without model turns. The diff must stay inside `editable`, touch
     nothing `protected` (the acceptance tests always are) and not repeat an
     earlier round's diff. Then the controller runs every gate itself.
  4. Decision. All gates pass: commit to the branch and write a review bundle
     (patch, summary, gate results, cost). Otherwise the next round gets the
     failures, until the budget (rounds, dollars, minutes) runs out. A stop,
     a scope violation, a repeated diff or an exhausted budget ends with a
     FAILURE REPORT in jobs/failure.py's format: the handoff back to
     exploration. Nothing is merged or pushed; the engineer reviews.

Gates are local commands, or TRACKED cluster jobs: the controller packages and
deploys the worktree's adapter to a run-specific snapshot (no licence), starts
the job through jobs.workflow under a stable request key, and waits for
`collect` itself. A tracked gate passes only on a verified engineering pass;
on a fail its failure report is what the next round reads. Tracked runs count
against `budget.licensed_jobs`.

A `diagnosis` contract starts from a failure: the contract's `failure_report`
and the baseline failure reports of its tracked gates are put in front of the
worker, which reports observations apart from hypotheses and may ask for ONE
discriminating experiment per round (a tracked gate re-run with other
parameters) instead of a patch.

Every event is appended to `ledger.jsonl` in the run directory.
Standard library only.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobs import failure  # noqa: E402  (flowkit's report format and renderer)
from worker import harness as harness_module  # noqa: E402

GATE_OUTPUT_MAX = 6000
#: Byproducts of running gates, never the worker's change.
BYPRODUCTS = ("*.pyc", "*/__pycache__/*", "__pycache__/*", ".pytest_cache/*", "*/.pytest_cache/*")
DIFF_LINES_MAX = 600
TRACKED_FIELDS = {"adapter", "snapshot_root", "host", "work_root", "parameters", "state_dir", "poll_seconds", "fetch"}
#: A fetched result file: JSON in the job's workspace, never a log. Logs stay on
#: the cluster (bin/runjob: they may carry model paths); a scorer's result holds
#: the measured numbers a diagnosis needs and the report deliberately leaves out.
FETCH_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*\.json")
FETCH_BYTES_MAX = 200000
FETCH_PROMPT_MAX = 6000
TERMINAL = ("done", "failed", "killed")


class Refusal(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def load_contract(path):
    with open(path, encoding="utf-8") as fh:
        c = json.load(fh)
    require(c.get("schema") == 1, "contract schema must be 1")
    for key in ("id", "kind", "goal", "repo", "editable", "gates", "budget"):
        require(key in c, "contract needs %r" % key)
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", c["id"]) is not None, "invalid contract id")
    require(c["kind"] in ("maintenance", "diagnosis"), "kind is maintenance or diagnosis")
    require(os.path.isdir(os.path.join(c["repo"], ".git")) or os.path.isfile(os.path.join(c["repo"], ".git")),
            "repo must be a git checkout")
    require(isinstance(c["editable"], list) and c["editable"], "editable globs required")
    c.setdefault("protected", [])
    c.setdefault("context", [])
    c.setdefault("base", "HEAD")
    c.setdefault("failure_report", None)
    require(c["failure_report"] is None or os.path.isfile(c["failure_report"]),
            "failure_report must be an existing file")
    gates = c["gates"]
    require(isinstance(gates, list) and gates, "at least one gate")
    for g in gates:
        require(set(g) <= {"name", "run", "tracked", "timeout", "acceptance", "protects"}
                and "name" in g and (("run" in g) != ("tracked" in g)),
                "gate fields: name, run or tracked, timeout, acceptance, protects")
        if "run" in g:
            require(isinstance(g["run"], list) and g["run"] and all(isinstance(a, str) for a in g["run"]),
                    "gate run is an argv list")
        else:
            t = g["tracked"]
            require(set(t) <= TRACKED_FIELDS and {"adapter", "snapshot_root", "host", "work_root", "parameters",
                                                   "state_dir"} <= set(t),
                    "tracked gate fields: " + ", ".join(sorted(TRACKED_FIELDS)))
            require(isinstance(t["parameters"], dict), "tracked parameters is an object")
            t.setdefault("poll_seconds", 20)
            t.setdefault("fetch", [])
            require(isinstance(t["fetch"], list) and all(isinstance(p, str) and FETCH_PATH.fullmatch(p)
                                                         and ".." not in p.split("/") for p in t["fetch"]),
                    "tracked fetch lists result .json files relative to the job workspace")
        g.setdefault("timeout", 600)
        g.setdefault("acceptance", False)
        g.setdefault("protects", [])
        c["protected"] = c["protected"] + g["protects"]
    require(any(g["acceptance"] for g in gates), "at least one acceptance gate (it must fail before the change)")
    c.setdefault("replay", None)
    if c["replay"] is not None:
        r = c["replay"]
        require(isinstance(r, dict), "replay is an object")
        r.setdefault("oracle_files", [])
        r.setdefault("shallow", False)
        require(set(r) <= {"fix", "oracle_files", "shallow"} and r.get("fix")
                and isinstance(r["oracle_files"], list) and isinstance(r["shallow"], bool),
                "replay fields: fix (a commit), oracle_files (its tests, laid over the parent as the oracle) "
                "and shallow (true hides the parent's history too)")
        c["protected"] = c["protected"] + [p for p in r["oracle_files"] if p not in c["protected"]]
    b = c["budget"]
    require(set(b) <= {"rounds", "usd", "minutes", "per_round_usd", "licensed_jobs"},
            "budget fields: rounds, usd, minutes, per_round_usd, licensed_jobs")
    b.setdefault("licensed_jobs", 0)
    tracked = sum(1 for g in gates if "tracked" in g)
    require(b["licensed_jobs"] >= tracked, "licensed_jobs must cover at least one run of each tracked gate")
    b.setdefault("rounds", 3)
    b.setdefault("usd", 6.0)
    b.setdefault("minutes", 60)
    b.setdefault("per_round_usd", 2.0)
    require(1 <= b["rounds"] <= 10, "rounds 1..10")
    h = c.setdefault("harness", {})
    h.setdefault("name", "claude")
    require(h["name"] in ("claude", "codex"), "harness is claude or codex")
    h.setdefault("model", None)
    h.setdefault("round_timeout", 1800)
    return c


def git(repo, *args, check=True):
    p = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise Refusal("git %s failed: %s" % (" ".join(args[:2]), p.stderr.strip()[-300:]))
    return p.stdout


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, p) for p in patterns)


class Run:
    def __init__(self, contract, state_dir, harness=None, clock=time.time, fetcher=None):
        self.c = contract
        self.fetcher = fetcher or fetch_results
        self.clock = clock
        self.started = clock()
        self.id = "%s-%s-%s" % (contract["id"], time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.started)),
                                os.urandom(2).hex())
        self.dir = os.path.join(os.path.abspath(state_dir), self.id)
        os.makedirs(self.dir)
        self.wt = os.path.join(self.dir, "worktree")
        self.branch = "worker/" + self.id
        self.harness = harness or harness_module.run
        self.cost = 0.0
        self.rounds = []
        self.diffs = set()
        self.licensed = 0
        self.experiments = []
        self.baseline_reports = []

    # -- ledger ------------------------------------------------------------
    def log(self, event, **fields):
        fields.update(event=event, at=round(self.clock(), 3))
        with open(os.path.join(self.dir, "ledger.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields, sort_keys=True) + "\n")

    # -- steps -------------------------------------------------------------
    def preflight(self):
        repo = self.c["repo"]
        dirty = [l for l in git(repo, "status", "--porcelain").splitlines() if l.strip()]
        if self.c["replay"]:
            base = self.replay_workspace(repo)
        else:
            base = git(repo, "rev-parse", self.c["base"]).strip()
            git(repo, "worktree", "add", "-b", self.branch, self.wt, base)
        self.base = base
        self.log("preflight", repo=repo, base=base, branch=self.branch, worktree=self.wt,
                 checkout_dirty_files=len(dirty), contract=self.c)
        gates = self.gates("baseline")
        self.baseline_reports = [g["failure_report"] for g in gates if g.get("failure_report")]
        broken = [g["name"] for g in gates if not g["acceptance"] and not g["passed"]]
        done = all(g["passed"] for g in gates if g["acceptance"])
        return gates, broken, done

    def replay_workspace(self, repo):
        """A blind replay: a fresh repository holding the fix's parent and nothing later.

        A worktree shares the source repository, so `git log --all` or `git show
        <fix>` would hand the worker the answer. Here only the parent's history is
        fetched (no refs, no tags, no remote), the fix's tests are laid over it as
        the oracle commit, and that commit is the base. The fix itself stays out of
        reach of git; reads of the checkout on disk are what `audit` looks for.
        """
        r = self.c["replay"]
        self.fix = git(repo, "rev-parse", r["fix"] + "^{commit}").strip()
        parent = git(repo, "rev-parse", self.fix + "^").strip()
        os.makedirs(self.wt)
        git(self.wt, "init", "-q")
        who = ["-c", "user.name=spec2si worker", "-c", "user.email=worker@localhost", "-c", "commit.gpgsign=false"]
        if r["shallow"]:
            # No history at all: the parent's tree becomes a new root commit. An
            # injected fault's own commit would show the answer in `git log -p`, and
            # even a depth-1 fetch keeps its message (pilot 7 read "ota6: tail sink").
            proc = subprocess.Popen(["git", "-C", repo, "archive", "--format=tar", parent],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                tar.extractall(self.wt, filter="data")
            if proc.wait():
                raise Refusal("git archive of %s failed: %s" % (parent[:12], proc.stderr.read()[-300:]))
            git(self.wt, "checkout", "-q", "-b", self.branch)
            git(self.wt, "add", "-A")
            git(self.wt, *who, "commit", "-q", "-m", "replay base")
        else:
            git(self.wt, "fetch", "-q", "--no-tags", os.path.abspath(repo), parent)
            git(self.wt, "checkout", "-q", "-b", self.branch, "FETCH_HEAD")
        for path in r["oracle_files"]:
            p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (self.fix, path)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
            if p.returncode:
                raise Refusal("replay oracle %s is not in %s" % (path, self.fix[:12]))
            dest = os.path.join(self.wt, *path.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(p.stdout)
        if r["oracle_files"]:
            git(self.wt, "add", "--", *r["oracle_files"])
        git(self.wt, *who, "commit", "-q", "--allow-empty", "-m", "replay oracle: the acceptance tests")
        return git(self.wt, "rev-parse", "HEAD").strip()

    def audit(self, raw):
        """Paths a replay's worker named outside its own run directory (the answer is on disk).

        Reads the harness transcript: any absolute path under the source
        repository's parent directory that is not inside this run, or a relative
        climb of three levels or more, is recorded. An empty list is not proof of
        blindness, only that the transcript shows no look outside.
        """
        try:
            text = open(raw, encoding="utf-8", errors="replace").read()
        except (OSError, TypeError):
            return ["transcript unreadable"]
        norm = text.replace("\\\\", "/").replace("\\", "/").lower()
        root = os.path.dirname(os.path.abspath(self.c["repo"])).replace("\\", "/").lower().rstrip("/")
        own = os.path.abspath(self.dir).replace("\\", "/").lower()

        def forms(path):
            if path[1:2] != ":":
                return {path}
            return {path, "/%s%s" % (path[0], path[2:]), "/mnt/%s%s" % (path[0], path[2:])}
        own_forms = forms(own)
        found = set()
        for prefix in forms(root):
            for m in re.finditer(re.escape(prefix + "/") + r"[^\s\"'<>|,;)]*", norm):
                path = m.group(0)
                if not any(path.startswith(o) for o in own_forms):
                    found.add(path[:160])
        found |= {m.group(0)[:160] for m in re.finditer(r"(?:\.\./){3,}[^\s\"'<>|,;)]*", norm)}
        return sorted(found)

    def gates(self, label):
        results = []
        for g in self.c["gates"]:
            t0 = self.clock()
            if "tracked" in g:
                results.append(self.tracked_gate(g, label))
                continue
            try:
                p = subprocess.run(g["run"], cwd=self.wt, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=g["timeout"], env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                rc, out = p.returncode, p.stdout.decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                rc, out = None, "timed out after %ds" % g["timeout"]
            except OSError as exc:
                rc, out = None, "could not run: %s" % exc
            path = os.path.join(self.dir, "%s-%s.log" % (label, re.sub(r"[^A-Za-z0-9._-]", "_", g["name"])))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(out)
            results.append(dict(name=g["name"], acceptance=g["acceptance"], rc=rc, passed=rc == 0,
                                seconds=round(self.clock() - t0, 1), log=path, tail=out[-GATE_OUTPUT_MAX:]))
        self.log("gates", label=label, licensed_jobs=self.licensed,
                 results=[{k: r.get(k) for k in ("name", "acceptance", "rc", "passed", "seconds", "log", "task_key",
                                                  "job_id", "engineering", "failure_report")} for r in results])
        return results

    def py(self):
        return ["py", "-3"] if os.name == "nt" else ["python3"]

    def step(self, argv, timeout, label):
        """Run one controller step in the worktree -> (rc, stdout text)."""
        try:
            p = subprocess.run(argv, cwd=self.wt, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            return p.returncode, p.stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            return None, "%s timed out after %ds" % (label, timeout)

    def tracked_gate(self, g, label, parameters=None):
        """Package and deploy the worktree, run the job tracked, and collect it."""
        t = g["tracked"]
        tag = "%s-%s" % (label, re.sub(r"[^A-Za-z0-9._-]", "_", g["name"]))
        name = "worker-%s-%s" % (self.id, tag)
        out = dict(name=g["name"], acceptance=g["acceptance"], rc=None, passed=False, tracked=True,
                   task_key=name, log=None, tail="")
        t0 = self.clock()
        if self.licensed >= self.c["budget"]["licensed_jobs"]:
            out["tail"] = "not run: licensed-job budget exhausted (%d)" % self.licensed
            out["seconds"] = 0.0
            return out
        pkg, prof = os.path.join(self.dir, "pkg-" + tag), os.path.join(self.dir, "profile-" + tag)
        params = os.path.join(self.dir, "params-%s.json" % tag)
        with open(params, "w", encoding="utf-8") as fh:
            json.dump(parameters if parameters is not None else t["parameters"], fh)
        log = []
        steps = [
            ("package", self.py() + [t["adapter"], "package", "--repo", ".", "--output", pkg], 600),
            ("deploy", self.py() + [t["adapter"], "deploy", "--repo", ".", "--package", pkg, "--snapshot",
                                    t["snapshot_root"].rstrip("/") + "/" + name, "--host", t["host"],
                                    "--work-root", t["work_root"], "--output", prof], 900),
            ("start", self.py() + ["-m", "deployment.bnl.jobs.workflow", "start", "--profile",
                                   os.path.join(prof, "profile.json"), "--state-dir", t["state_dir"],
                                   "--task-key", name, "--repo", ".", "--manifest",
                                   os.path.join(prof, "manifest.json"), "--parameters-file", params,
                                   "--host", t["host"]], 600)]
        for step_name, argv, timeout in steps:
            rc, text = self.step(argv, timeout, step_name)
            log.append("== %s rc=%s\n%s" % (step_name, rc, text[-3000:]))
            if rc not in (0, 4) or (step_name != "start" and rc != 0):
                out["tail"] = "\n".join(log)[-GATE_OUTPUT_MAX:]
                out["seconds"] = round(self.clock() - t0, 1)
                return self.gate_log(out, tag, log)
            if step_name == "start":
                self.licensed += 1
        deadline = self.clock() + g["timeout"]
        collected = {}
        while True:
            rc, text = self.step(self.py() + ["-m", "deployment.bnl.jobs.workflow", "collect", "--profile",
                                              os.path.join(prof, "profile.json"), "--state-dir", t["state_dir"],
                                              "--task-key", name], 600, "collect")
            try:
                collected = json.loads(text[text.index("{"):])
            except ValueError:
                collected = {}
            if collected.get("observation") in TERMINAL or self.clock() > deadline:
                break
            time.sleep(t["poll_seconds"])
        log.append("== collect rc=%s\n%s" % (rc, json.dumps({k: collected.get(k) for k in (
            "observation", "execution_rc", "evidence", "engineering", "checks_passed", "checks_failed",
            "failed_checks", "missing_checks", "issues", "failure_cause")}, indent=1)))
        report = collected.get("failure_report")
        if report and os.path.isfile(report):
            log.append("== failure report\n" + open(report, encoding="utf-8").read())
            out["failure_report"] = report
        workspace = (collected.get("reference") or {}).get("workspace")
        results = self.fetch(t, os.path.join(prof, "profile.json"), workspace, tag) if t["fetch"] and workspace else ""
        out.update(rc=0 if collected.get("engineering") == "pass" else 1,
                   passed=collected.get("engineering") == "pass" and collected.get("evidence") == "tracker-verified",
                   job_id=collected.get("job_id"), engineering=collected.get("engineering"),
                   seconds=round(self.clock() - t0, 1), tail="\n".join(log)[-GATE_OUTPUT_MAX:], results=results)
        if results:
            log.append(results)
        return self.gate_log(out, tag, log)

    def fetch(self, t, profile, workspace, tag):
        """Copy the gate's declared result files back; -> a bounded view for the next prompt."""
        try:
            with open(profile, encoding="utf-8") as fh:
                mode = json.load(fh).get("transport_mode")
            files = self.fetcher(t["host"], mode, workspace, t["fetch"])
        except Exception as exc:   # evidence is a help to the worker, never a gate verdict
            return "== results not fetched: %s" % exc
        keep = os.path.join(self.dir, "results-" + tag)
        lines = ["== results (from %s)" % workspace]
        for rel in t["fetch"]:
            text = files.get(rel)
            if text is None:
                lines.append("-- %s: absent" % rel)
                continue
            dest = os.path.join(keep, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(text)
            try:   # compact JSON says more per character than the indented original
                text = json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
            except ValueError:
                pass
            lines.append("-- %s (%d chars%s):\n%s" % (rel, len(text), ", clipped" if len(text) > FETCH_PROMPT_MAX
                                                        else "", text[:FETCH_PROMPT_MAX]))
        return "\n".join(lines)

    def gate_log(self, out, tag, log):
        path = os.path.join(self.dir, "%s.log" % tag)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(log))
        out["log"] = path
        return out

    def scope(self):
        """-> (changed paths, diff text, violation or None). Untracked files count."""
        git(self.wt, "add", "-A")
        names = [n for n in git(self.wt, "diff", "--cached", "--name-only", self.base).splitlines()
                 if n and not matches(n, BYPRODUCTS)]
        diff = git(self.wt, "diff", "--cached", self.base, "--", *names) if names else ""
        git(self.wt, "reset", "-q")
        if not names:
            return names, diff, "no change"
        outside = [n for n in names if not matches(n, self.c["editable"])]
        guarded = [n for n in names if matches(n, self.c["protected"])]
        if guarded:
            return names, diff, "protected path changed: " + ", ".join(guarded)
        if outside:
            return names, diff, "outside the editable paths: " + ", ".join(outside)
        changed = sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
        if changed > DIFF_LINES_MAX:
            return names, diff, "diff too large: %d changed lines (limit %d)" % (changed, DIFF_LINES_MAX)
        return names, diff, None

    def prompt(self, last_gates):
        c = self.c
        lines = [
            "You are a bounded implementation worker. The decision is made; implement it, do not reopen it.",
            "", "GOAL: " + c["goal"], "",
            ("You are in a fresh clone of the repository at base %s. Edit files there only. Work from this clone "
             "alone: do not read any other checkout or directory on this machine." % self.base[:12]
             if c["replay"] else
             "You are in a git worktree of %s at base %s. Edit files there only." % (c["repo"], self.base[:12])),
            "Editable paths (glob): " + ", ".join(c["editable"]),
            "Protected paths (never edit; they include the acceptance tests): " + (", ".join(c["protected"]) or "none"),
        ]
        if c["context"]:
            lines += ["Read these first: " + ", ".join(c["context"])]
        if c["kind"] == "diagnosis":
            lines += ["", "DIAGNOSIS: the failure below is your starting point. Find its cause from evidence; keep "
                          "what you observed apart from what you infer. Fix it within the editable paths, or, if one "
                          "run would settle between hypotheses, ask for ONE experiment (action=experiment: a tracked "
                          "gate name and a JSON object of its parameters). If the cause lies outside the editable "
                          "paths or needs a design decision, stop and say what the engineer must decide."]
            reports = ([c["failure_report"]] if c["failure_report"] else []) + self.baseline_reports
            for path in reports[:3]:
                lines += ["", "FAILURE REPORT (%s):" % path, open(path, encoding="utf-8").read()[-5000:]]
        tracked = [g["name"] for g in c["gates"] if "tracked" in g]
        if tracked:
            lines += ["Tracked cluster gates (%s) run licensed jobs; the controller runs them, never you. "
                      "Licensed runs left: %d." % (", ".join(tracked), c["budget"]["licensed_jobs"] - self.licensed)]
        lines += ["", "The controller, not you, runs these gates after your turn and decides:"]
        lines += ["- %s%s: %s" % (g["name"], " (acceptance)" if g["acceptance"] else "",
                                  " ".join(g["run"]) if "run" in g else
                                  "tracked job via %s on %s, parameters %s" % (
                                      g["tracked"]["adapter"], g["tracked"]["host"],
                                      json.dumps(g["tracked"]["parameters"])))
                  for g in c["gates"]]
        if c["harness"]["name"] == "codex":
            # Codex reads and edits through the shell; its sandbox (workspace writes, no
            # network) is the boundary. Pilot 2's first worker obeyed a pytest-only line
            # meant for Claude and could not read a file when its reader tool crashed.
            lines += ["Use shell commands freely inside the worktree to read, edit and test (the sandbox "
                      "blocks the network). Do not commit, push, or touch anything outside the worktree."]
        else:
            lines += ["You may run pytest yourself to iterate. You cannot run other commands, commit, or push."]
        lines += ["Round %d of %d. Budget left about $%.2f." % (len(self.rounds) + 1, c["budget"]["rounds"],
                                                            c["budget"]["usd"] - self.cost)]
        if self.rounds:
            lines += ["", "EARLIER ROUNDS:"]
            for i, r in enumerate(self.rounds, 1):
                p = r.get("proposal") or {}
                lines += ["- Round %d: %s -> %s" % (i, p.get("summary", r.get("error", "")), r.get("outcome", ""))]
        if last_gates:
            lines += ["", "GATE RESULTS AFTER THE LAST ROUND (output tails):"]
            for g in last_gates:
                lines += ["--- %s: %s" % (g["name"], "PASS" if g["passed"] else "FAIL rc=%s" % g["rc"]),
                          g["tail"][-2500:] if not g["passed"] else ""]
                if g.get("results") and not g["passed"]:
                    lines += [g["results"]]
        if self.experiments:
            lines += ["", "EXPERIMENT RESULTS:"] + self.experiments[-2:]
        lines += ["", "End with the structured proposal. action=patch when the worktree holds your change; "
                      "action=stop if you cannot meet the goal within the editable paths, with stop_reason and "
                      "the question the engineer must answer. Keep observations (seen) apart from hypotheses "
                      "(inferred)."]
        return "\n".join(lines)

    def experiment(self, p, n):
        """Run ONE tracked gate with the worker's parameters; the result goes to the next round."""
        gate = next((g for g in self.c["gates"] if g["name"] == p.get("experiment_gate") and "tracked" in g), None)
        try:
            params = json.loads(p.get("experiment_parameters") or "")
            ok = isinstance(params, dict)
        except ValueError:
            ok = False
        if gate is None or not ok:
            note = "experiment refused: name a tracked gate and give its parameters as a JSON object"
        else:
            r = self.tracked_gate(gate, "round-%d-experiment" % n, params)
            note = "experiment on %s with %s: engineering %s\n%s" % (
                gate["name"], json.dumps(params), r.get("engineering"), r["tail"][-3000:]) + (
                "\n" + r["results"] if r.get("results") else "")
        self.experiments.append(note)
        self.log("experiment", round=n, note=note[:500])
        return note.splitlines()[0]

    def over_budget(self):
        b = self.c["budget"]
        if len(self.rounds) >= b["rounds"]:
            return "rounds exhausted (%d)" % b["rounds"]
        if self.cost >= b["usd"]:
            return "dollar budget exhausted ($%.2f of $%.2f)" % (self.cost, b["usd"])
        if any("tracked" in g for g in self.c["gates"]) and self.licensed >= b["licensed_jobs"]:
            return "licensed-job budget exhausted (%d runs)" % b["licensed_jobs"]
        if (self.clock() - self.started) / 60.0 >= b["minutes"]:
            return "time budget exhausted (%d min)" % b["minutes"]
        return None

    # -- the loop ------------------------------------------------------------
    def execute(self):
        gates, broken, done = self.preflight()
        if broken:
            return self.fail("baseline", "gates fail before any change: " + ", ".join(broken), gates,
                             question="The base is broken; fix or choose another base before delegating.")
        if done:
            return self.finish("nothing-to-do", gates, None)
        last = gates
        while True:
            why = self.over_budget()
            if why:
                return self.fail("budget", why, last)
            n = len(self.rounds) + 1
            record_dir = os.path.join(self.dir, "round-%d" % n)
            os.makedirs(record_dir)
            per_round = min(self.c["budget"]["per_round_usd"], max(0.5, self.c["budget"]["usd"] - self.cost))
            h = self.c["harness"]
            args = (h["name"], self.prompt(last), self.wt, per_round, h["round_timeout"], record_dir, h.get("model"))
            # A replay records Claude's tool calls too, so the audit can read them.
            out = self.harness(*args, transcript=True) if self.c["replay"] else self.harness(*args)
            self.cost += out.get("cost_usd") or 0.0
            entry = dict(round=n, proposal=out.get("proposal"), error=out.get("error"),
                         cost_usd=out.get("cost_usd"), tokens=out.get("tokens"), seconds=out.get("seconds"))
            if self.c["replay"]:
                entry["outside_paths"] = self.audit(out.get("raw"))
            self.rounds.append(entry)
            self.log("round", **entry)
            if not out.get("ok"):
                entry["outcome"] = "harness error"
                continue
            p = out["proposal"]
            if p["action"] == "stop":
                entry["outcome"] = "worker stopped"
                return self.fail("worker-stop", p.get("stop_reason") or "the worker stopped", last,
                                 question=p.get("question") or None)
            if p["action"] == "experiment":
                entry["outcome"] = self.experiment(p, n)
                continue
            names, diff, violation = self.scope()
            self.log("scope", files=names, violation=violation)
            if violation == "no change":
                entry["outcome"] = "no change"
                continue
            if violation:
                entry["outcome"] = "scope violation"
                return self.fail("scope", violation, last)
            digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
            if digest in self.diffs:
                entry["outcome"] = "repeated diff"
                return self.fail("no-progress", "round %d repeated an earlier diff" % n, last)
            self.diffs.add(digest)
            last = self.gates("round-%d" % n)
            if all(g["passed"] for g in last):
                entry["outcome"] = "all gates pass"
                return self.finish("ready-for-review", last, (names, diff))
            entry["outcome"] = "gates fail: " + ", ".join(g["name"] for g in last if not g["passed"])

    # -- outcomes ----------------------------------------------------------
    def summary_facts(self):
        final = next((r["proposal"] for r in reversed(self.rounds) if r.get("proposal")), None) or {}
        counted = [r["tokens"] for r in self.rounds if r.get("tokens") is not None]
        facts = dict(run=self.id, branch=self.branch, worktree=self.wt, base=self.base,
                     rounds=len(self.rounds), cost_usd=round(self.cost, 2), licensed_jobs=self.licensed,
                     tokens=sum(counted) if counted else None,
                     priced=any(r.get("cost_usd") is not None for r in self.rounds),
                     minutes=round((self.clock() - self.started) / 60.0, 1),
                     observations=final.get("observations", []), hypotheses=final.get("hypotheses", []),
                     summary=final.get("summary", ""))
        if self.c["replay"]:
            # Written only now, after the last round: the worker must never find it.
            oracle = self.c["replay"]["oracle_files"]
            real = git(self.c["repo"], "diff", self.fix + "^", self.fix, "--", ".",
                       *[":(exclude)%s" % p for p in oracle])
            with open(os.path.join(self.dir, "fix.patch"), "w", encoding="utf-8") as fh:
                fh.write(real)
            facts["replay"] = dict(fix=self.fix, fix_patch=os.path.join(self.dir, "fix.patch"),
                                   outside_paths=sorted({p for r in self.rounds for p in r.get("outside_paths", [])}))
        return facts

    def finish(self, status, gates, change):
        facts = self.summary_facts()
        facts.update(status=status, gates=[{k: g[k] for k in ("name", "acceptance", "passed", "rc")} for g in gates])
        if change:
            names, diff = change
            git(self.wt, "add", "--", *names)
            git(self.wt, "-c", "user.name=spec2si worker", "-c", "user.email=worker@localhost",
                "commit", "-q", "-m", "worker %s: %s\n\n%s" % (self.c["id"], self.c["goal"][:60], facts["summary"]))
            with open(os.path.join(self.dir, "change.patch"), "w", encoding="utf-8") as fh:
                fh.write(diff)
            facts.update(files=names, patch=os.path.join(self.dir, "change.patch"))
        with open(os.path.join(self.dir, "review.json"), "w", encoding="utf-8") as fh:
            json.dump(facts, fh, indent=1, sort_keys=True)
        with open(os.path.join(self.dir, "review.md"), "w", encoding="utf-8") as fh:
            fh.write(review_markdown(self.c, facts))
        self.log("finish", status=status, cost_usd=facts["cost_usd"], rounds=facts["rounds"])
        facts["review"] = os.path.join(self.dir, "review.md")
        return facts

    def fail(self, stage, reason, gates, question=None):
        facts = self.summary_facts()
        failing = [g["name"] for g in gates if not g["passed"]]
        acceptance_failing = any(not g["passed"] and g["acceptance"] for g in gates)
        derived, basis = (("gate-fail", "the acceptance gates still fail after the worker's rounds: "
                           + ", ".join(failing)) if stage in ("budget", "no-progress") and acceptance_failing
                          else ("unclassified", "%s: %s" % (stage, reason)))
        now = self.clock()
        report = dict(
            schema=1, kind="failure-report", task_key=self.id, created_at=now, updated_at=now,
            contract=dict(profile_id=self.c["id"], profile_version=self.c["kind"], repository=self.c["repo"],
                          host="local worker (%s)" % self.c["harness"]["name"],
                          request_sha256=hashlib.sha256(json.dumps(self.c, sort_keys=True).encode()).hexdigest(),
                          manifest_sha256=None, source_head=self.base,
                          required_checks=[g["name"] for g in self.c["gates"]], required_corners=None,
                          expected_artifacts=None),
            outcome=dict(observation="stopped: " + stage, execution_rc=None, evidence="worker ledger",
                         engineering="fail" if acceptance_failing else "unchecked",
                         checks_passed=sum(1 for g in gates if g["passed"]), checks_failed=len(failing),
                         failed_checks=failing, missing_checks=[], issues=[reason]),
            evidence=dict(job_id=None, task_id=self.id, workspace=self.wt, tracker_records=self.dir + "/ledger.jsonl",
                          collection="review.json", log_signatures=None),
            attempts=dict(count=len(self.rounds), earlier=[dict(task_key="round %d" % r["round"], job_id=None,
                                                                observation=r.get("outcome"), engineering=None)
                                                           for r in self.rounds],
                          started_at=self.started, collected_at=now),
            cause=dict(derived=dict(cause=derived, basis=basis), declared=[], current=derived),
            exploration=dict(contradicts=None, question=question, status="open", history=[]),
            worker=facts,
        )
        with open(os.path.join(self.dir, "failure.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
        md = failure.markdown(report)
        md += "\n## Worker\n\n- Branch `%s` (worktree `%s`), %d round(s), %s, %.1f min\n" % (
            self.branch, self.wt, facts["rounds"], spend(facts), facts["minutes"])
        md += "".join("- Observed: %s\n" % o for o in facts["observations"])
        md += "".join("- Hypothesis: %s\n" % h for h in facts["hypotheses"])
        if facts.get("replay"):
            md += "- Replay of `%s` (real fix: fix.patch); paths named outside the run: %s\n" % (
                facts["replay"]["fix"][:12], ", ".join(facts["replay"]["outside_paths"]) or "none")
        with open(os.path.join(self.dir, "failure.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        self.log("stop", stage=stage, reason=reason, cause=derived)
        facts.update(status="stopped", stage=stage, reason=reason, failure_report=os.path.join(self.dir, "failure.md"))
        return facts


def fetch_results(host, mode, workspace, paths):
    """Read result files from a job workspace over the tracker's transport -> {path: text or None}.

    Read-only, bounded, and the request travels base64-encoded on stdin like
    every other script (jobs.remote): no interpolated remote command.
    """
    from jobs.remote import Transport
    import base64
    request = base64.b64encode(json.dumps(dict(workspace=workspace, paths=paths,
                                               limit=FETCH_BYTES_MAX)).encode("utf-8")).decode("ascii")
    script = ("python3 - <<'PY'\n"
              "import base64, json, os\n"
              "r = json.loads(base64.b64decode('%s'))\n"
              "out = {}\n"
              "for rel in r['paths']:\n"
              "    p = os.path.join(r['workspace'], rel)\n"
              "    ok = os.path.isfile(p) and not os.path.islink(p) and os.path.getsize(p) <= r['limit']\n"
              "    out[rel] = open(p, encoding='utf-8', errors='replace').read() if ok else None\n"
              "print(json.dumps(dict(schema=1, files=out)))\n"
              "PY\n") % request
    result = Transport(host=host, mode=mode, timeout=60).run_sh(script, retry_enoent=False)
    if not result.ok:
        raise RuntimeError("fetch not confirmed: %s" % result.reason)
    return result.data["files"]


def spend(f):
    """What the run spent: dollars when a round reported a cost, else tokens (Codex prices nothing)."""
    if not f["priced"] and f["tokens"] is not None:
        return "%d tokens" % f["tokens"]
    return "$%.2f" % f["cost_usd"]


def review_markdown(c, f):
    lines = ["# Worker result: `%s`" % f["run"], "", "Status: **%s**." % f["status"], "",
             "- Goal: %s" % c["goal"],
             "- Branch `%s` from `%s`; worktree `%s`" % (f["branch"], f["base"][:12], f["worktree"]),
             "- %d round(s), %s, %.1f min" % (f["rounds"], spend(f), f["minutes"])]
    if f.get("files"):
        lines += ["- Files: " + ", ".join("`%s`" % n for n in f["files"]),
                  "- Patch: [change.patch](change.patch)"]
    if f.get("replay"):
        r = f["replay"]
        lines += ["- Replay of `%s`: compare the patch with [fix.patch](fix.patch)" % r["fix"][:12],
                  "- Paths named outside the run: %s" % (", ".join("`%s`" % p for p in r["outside_paths"])
                                                         or "none in the transcript")]
    lines += ["", "## Gates", ""] + ["- %s%s: **%s**" % (g["name"], " (acceptance)" if g["acceptance"] else "",
                                                          "pass" if g["passed"] else "fail rc=%s" % g["rc"])
                                      for g in f["gates"]]
    lines += ["", "## Summary", "", f["summary"] or "-", "", "## Observed", ""]
    lines += ["- %s" % o for o in f["observations"]] or ["- none recorded"]
    lines += ["", "## Hypotheses (not verified)", ""]
    lines += ["- %s" % h for h in f["hypotheses"]] or ["- none recorded"]
    lines += ["", "Review the branch, then merge it yourself. The worker never merges or pushes.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="op", required=True)
    r = sub.add_parser("run")
    r.add_argument("--contract", required=True)
    r.add_argument("--state-dir", required=True, help="private directory outside every checkout")
    s = sub.add_parser("show")
    s.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.op == "show":
        for name in ("review.md", "failure.md"):
            path = os.path.join(a.run, name)
            if os.path.exists(path):
                print(open(path, encoding="utf-8").read())
        return 0
    try:
        contract = load_contract(a.contract)
        result = Run(contract, a.state_dir).execute()
    except Refusal as exc:
        print(json.dumps(dict(status="refused", reason=str(exc))))
        return 2
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["status"] in ("ready-for-review", "nothing-to-do") else 1


if __name__ == "__main__":
    sys.exit(main())
