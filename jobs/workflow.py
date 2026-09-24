"""Profile-driven tracker entry point. Python 3.6+, standard library only.

Profiles are trusted executable configuration; parameter values are argv data.
Durable CLI submissions use jobs.state; legacy references remain readable.
"""
import argparse
import hashlib
import json
import os
import posixpath
import re
import sys
import uuid

from .remote import Transport, KNOWN, _SAFE_PATH
from . import hosts


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def fields(value, required, optional=()):
    require(isinstance(value, dict), "expected an object")
    require(set(required) <= set(value), "missing required fields")
    require(set(value) <= set(required) | set(optional), "unknown fields")


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)


def relative(value):
    return (isinstance(value, str) and _SAFE_PATH(value)
            and all(p not in ("", ".", "..") for p in value.split("/")))


def absolute(value):
    return (isinstance(value, str) and value.startswith("/")
            and not any(c in value for c in "\x00\r\n")
            and ".." not in value.split("/") and posixpath.normpath(value) == value)


def parse_parameters(text):
    """--parameters as a JSON object. A shell that mangles the quoting (PowerShell
    turns '{"case":"tt"}' into {case:tt}) raised a bare ValueError, which the CLI
    reported as "cannot read/write workflow state"; say what is actually wrong."""
    try:
        value = json.loads(text)
    except ValueError:
        raise ContractError("--parameters is not valid JSON (check the shell's quoting): %r" % text[:120])
    require(isinstance(value, dict), "--parameters must be a JSON object")
    return value

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def evidence_summary(result):
    """Private, concise review artifact. No log/report content is embedded."""
    lines = ["# Job evidence", "", "- Task: `" + str(result.get("task_id")) + "`",
             "- Job: `" + str(result.get("job_id")) + "`",
             "- Host: `" + str(result.get("host")) + "`",
             "- Execution: " + result["observation"] + " (rc " + str(result.get("execution_rc", "unknown")) + ")",
             "- Artifacts: " + result["evidence"], "- Engineering: " + result["engineering"],
             "- Checks: " + str(result.get("checks_passed", 0)) + " passed, " +
             str(result.get("checks_failed", 0)) + " failed",
             "- Next action: " + result["next_action"], "", "## Incomplete checks / issues", ""]
    issues = result.get("issues") or ([] if result["engineering"] in ("pass", "fail") else
                                     ["Engineering validation not established; inspect execution and artifact evidence."])
    lines += ["- " + issue for issue in issues] or ["None detected within the declared report contract."]
    lines += ["- Missing check: " + c for c in result.get("missing_checks", [])]
    lines += ["- Failed check: " + c for c in result.get("failed_checks", [])]
    lines += ["", "Tracker records on the selected host: `~/.asicjobs/" + str(result.get("job_id")) +
              "/{meta,status,result}.json` (or the configured ASICJOBS_DIR).", "",
              "A report verdict covers the declared checks only; it is not a general signoff claim.", ""]
    return "\n".join(lines)


def validate_profile(p):
    fields(p, ("schema", "id", "version", "repository", "work_root", "argv",
               "parameters", "host_policy", "expected_artifacts", "workspace",
               "lifecycle", "progress", "engineering_report"), ("isolated_bundle", "transport_mode"))
    require(type(p.get("isolated_bundle", False)) is bool, "isolated_bundle must be boolean")
    require(p.get("transport_mode") in (None, "winssh", "wsl", "ssh"), "invalid transport_mode")
    require(type(p["schema"]) is int and p["schema"] == 1, "unsupported profile schema")
    require(identifier(p["id"]) and identifier(p["version"]), "invalid profile identity")
    require(isinstance(p["repository"], str) and bool(p["repository"]), "repository required")
    require(absolute(p["work_root"]), "work_root must be an absolute POSIX directory")
    require(p["workspace"] == "unique-child", "only unique-child allocation is supported")
    require(p["lifecycle"] == "foreground", "payload must own/wait for its work")
    policy = p["host_policy"]
    fields(policy, ("allowed", "default", "allow_auto"))
    require(isinstance(policy["allowed"], list) and bool(policy["allowed"])
            and all(identifier(h) and h != "auto" for h in policy["allowed"]), "invalid hosts")
    require(type(policy["allow_auto"]) is bool, "allow_auto must be boolean")
    require(policy["default"] in policy["allowed"] or
            (policy["default"] == "auto" and policy["allow_auto"]), "invalid default host")
    require(isinstance(p["parameters"], dict), "parameters must be an object")
    for name, spec in p["parameters"].items():
        require(identifier(name), "invalid parameter name")
        fields(spec, ("type",), ("choices",))
        require(spec["type"] in ("string", "integer"), "unsupported parameter type")
        if "choices" in spec:
            require(isinstance(spec["choices"], list) and bool(spec["choices"]), "empty choices")
            typ = str if spec["type"] == "string" else int
            require(all(type(v) is typ for v in spec["choices"]), "invalid choice type")
    require(isinstance(p["argv"], list) and bool(p["argv"]), "argv required")
    for token in p["argv"]:
        if isinstance(token, dict):
            fields(token, ("parameter",))
            require(isinstance(token["parameter"], str) and
                    token["parameter"] in p["parameters"], "unknown argv parameter")
        else:
            require(isinstance(token, str) and bool(token) and "\x00" not in token,
                    "argv literals must be nonempty strings")
    require(isinstance(p["argv"][0], str) and absolute(p["argv"][0]),
            "executable must be an absolute path")
    artifacts = p["expected_artifacts"]
    require(isinstance(artifacts, list) and all(relative(a) for a in artifacts), "invalid artifacts")
    require(len(set(artifacts)) == len(artifacts), "duplicate artifacts")
    progress = p["progress"]
    if progress is not None:
        fields(progress, ("tool", "log"), ("total",))
        require(progress["tool"] in ("spectre", "innovus", "calibre", "cocotb"), "unknown progress tool")
        require(relative(progress["log"]), "invalid progress log")
        require(type(progress.get("total", 0)) is int and progress.get("total", 0) >= 0,
                "invalid progress total")
    report = p["engineering_report"]
    if report is not None:
        fields(report, ("parser", "path", "design", "top", "checks", "corners"))
        require(report["parser"] == "json-v1" and report["path"] in artifacts,
                "report must use json-v1 and name a required artifact")
        require(identifier(report["design"]) and identifier(report["top"]), "invalid report design/top")
        for key in ("checks", "corners"):
            require(isinstance(report[key], list) and bool(report[key])
                    and all(identifier(v) for v in report[key])
                    and len(set(report[key])) == len(report[key]), "invalid required checks/corners")
    return p


class Workflow:
    def __init__(self, profile, transport_factory=Transport, host_picker=hosts.pick):
        self.profile = validate_profile(profile)
        self.transport_factory = transport_factory
        self.host_picker = host_picker

    def envelope(self, ref, observation, next_action, evidence="unchecked", **extra):
        result = dict(schema=1, kind="workflow", task_id=ref["task_id"],
                      host=ref["host"], job_id=ref["job_id"],
                      observation=observation, evidence=evidence,
                      engineering="unchecked", next_action=next_action, reference=ref)
        result.update(extra)
        return result

    def transport(self, host):
        options = {"isolated_bundle": True} if self.profile.get("isolated_bundle") else {}
        if self.profile.get("transport_mode"):
            options["mode"] = self.profile["transport_mode"]
        return self.transport_factory(host=host, **options)

    def start(self, parameters, host=None):
        """Low-level, non-durable API retained for compatibility/test adapters.

        Harness callers must use TaskStore.start or the durable CLI instead.
        """
        ref, argv = self.prepare(parameters, host)
        return self.dispatch(ref, argv)

    def prepare(self, parameters, host=None):
        p = self.profile
        require(isinstance(parameters, dict) and set(parameters) == set(p["parameters"]),
                "parameters must match profile exactly")
        for name, spec in p["parameters"].items():
            value = parameters[name]
            typ = str if spec["type"] == "string" else int
            require(type(value) is typ, "incorrect parameter type")
            require("choices" not in spec or value in spec["choices"], "parameter outside choices")
            require(str(value) != "" and "\x00" not in str(value), "empty/NUL argv unsupported")
        policy = p["host_policy"]
        chosen = host if host is not None else policy["default"]
        if chosen == "auto":
            require(policy["allow_auto"], "automatic host selection is disabled")
            chosen, _ = self.host_picker(hosts=policy["allowed"])
        require(chosen in policy["allowed"], "host unavailable or outside profile policy")
        task = "task-" + uuid.uuid4().hex
        ref = dict(schema=1, task_id=task, host=chosen, job_id=None,
                   profile_sha256=digest(p), repository=p["repository"],
                   workspace=posixpath.join(p["work_root"], task),
                   request_sha256=digest(parameters))
        argv = [str(parameters[t["parameter"]]) if isinstance(t, dict) else t for t in p["argv"]]
        return ref, argv

    def dispatch(self, ref, argv):
        p = self.profile
        progress = p["progress"] or {}
        res = self.transport(ref["host"]).run(
            argv, flow=p["id"], target="run", interval=1,
            expect=p["expected_artifacts"], workspace=ref["workspace"],
            progress=progress.get("tool"), progress_log=progress.get("log"),
            total=progress.get("total"))
        data = res.data or {}
        if res.status != KNOWN or res.rc != 0 or data.get("kind") != "launched" or not identifier(data.get("jobid")):
            return self.envelope(ref, "submission-unknown", "reconcile")
        ref["job_id"] = data["jobid"]
        return self.envelope(ref, "submitted", "status")

    def observe(self, reference, collect=False, identity=None):
        result = self._observe(reference, collect, identity)
        if collect:
            result["evidence_summary"] = evidence_summary(result)
        return result

    def _observe(self, reference, collect=False, identity=None):
        ref = reference
        fields(ref, ("schema", "task_id", "host", "job_id", "profile_sha256",
                     "repository", "workspace", "request_sha256"))
        require(ref["schema"] == 1 and identifier(ref["task_id"]), "invalid reference")
        require(ref["profile_sha256"] == digest(self.profile), "profile changed; reconcile explicitly")
        require(ref["repository"] == self.profile["repository"] and
                ref["workspace"] == posixpath.join(self.profile["work_root"], ref["task_id"]),
                "reference workspace/repository mismatch")
        require(ref["host"] in self.profile["host_policy"]["allowed"], "invalid reference host")
        if ref["job_id"] is None:
            return self.envelope(ref, "submission-unknown", "reconcile")
        require(identifier(ref["job_id"]), "invalid job id")
        transport = self.transport(ref["host"])
        res = transport.status(ref["job_id"])
        data = res.data or {}
        if res.status != KNOWN or res.rc != 0 or data.get("jobid") != ref["job_id"] or data.get("kind") != "status":
            return self.envelope(ref, "unknown", "status")
        state = data.get("state")
        if state not in ("running", "done", "failed", "killed"):
            return self.envelope(ref, "unknown", "status")
        terminal = state in ("done", "failed", "killed")
        if not collect or not terminal:
            return self.envelope(ref, state, "collect" if terminal else "status")
        why = transport.why(ref["job_id"])
        body = why.data or {}
        result = body.get("result") or {}
        if not isinstance(result, dict):
            return self.envelope(ref, "unknown", "collect")
        if (why.status != KNOWN or why.rc != 0 or body.get("jobid") != ref["job_id"]
                or result.get("jobid") != ref["job_id"] or result.get("state") != state):
            return self.envelope(ref, "unknown", "collect")
        artifacts = result.get("artifacts", [])
        expected = self.profile["expected_artifacts"]
        complete = (isinstance(artifacts, list) and bool(expected)
                    and len(artifacts) == len(expected)
                    and all(isinstance(a, dict) and isinstance(a.get("path"), str) for a in artifacts)
                    and {a.get("path") for a in artifacts} == set(expected)
                    and all(a.get("exists") is True and a.get("jobid") == ref["job_id"]
                            and re.fullmatch(r"[0-9a-f]{64}", str(a.get("sha256", ""))) for a in artifacts))
        if not complete:
            return self.envelope(ref, state, "inspect-evidence", "incomplete", execution_rc=result.get("rc"),
                                 issues=["required-artifacts-missing-unstamped-or-incomplete"])
        verify = transport.verify(ref["job_id"])
        v = verify.data or {}
        verified = (verify.status == KNOWN and verify.rc == 0
                    and v.get("jobid") == ref["job_id"] and v.get("verdict") == "OK"
                    and v.get("checked") == len(expected) and v.get("ok") == len(expected))
        evidence = "tracker-verified" if verified else "unverified"
        if not verified:
            return self.envelope(ref, state, "inspect-evidence", evidence,
                                 execution_rc=result.get("rc"), issues=["tracker-verification-failed"])
        checked = transport.evidence(dict(job_id=ref["job_id"], workspace=ref["workspace"],
                                          repository=ref["repository"], expected_artifacts=expected,
                                          report=self.profile["engineering_report"], identity=identity))
        detail = checked.data or {}
        if (checked.status != KNOWN or checked.rc != 0 or detail.get("kind") != "evidence"
                or detail.get("jobid") != ref["job_id"]):
            return self.envelope(ref, state, "inspect-evidence", "unverified",
                                 execution_rc=result.get("rc"), issues=["evidence-reader-unavailable"])
        engineering = detail.get("engineering", "unchecked")
        evidence = detail.get("evidence", "unverified")
        action = ("report-result" if engineering in ("pass", "fail") and evidence == "tracker-verified"
                  else "inspect-evidence")
        return self.envelope(ref, state, action, evidence, execution_rc=result.get("rc"),
                             engineering=engineering, issues=detail.get("issues", []),
                             checks_passed=detail.get("checks_passed", 0),
                             checks_failed=detail.get("checks_failed", 0),
                             missing_checks=detail.get("missing_checks", []),
                             failed_checks=detail.get("failed_checks", []))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("start", "status", "resume", "collect", "tasks"))
    parser.add_argument("--profile", required=True, help="trusted private profile JSON path")
    parser.add_argument("--parameters", default="{}", help="JSON object; start only")
    parser.add_argument("--host", help="explicit allowed host; start only")
    parser.add_argument("--reference", help="saved start envelope JSON path; reads only")
    parser.add_argument("--state-dir", default=os.environ.get("ASICJOBS_STATE_DIR"),
                        help="private durable directory outside the source checkout")
    parser.add_argument("--task-key", help="stable logical request key; reuse after interruption")
    parser.add_argument("--repo", help="local Git checkout whose identity is recorded at start")
    parser.add_argument("--manifest", help="input manifest JSON; its digest is recorded at start")
    args = parser.parse_args(argv)
    try:
        with open(args.profile, encoding="utf-8") as fh:
            workflow = Workflow(json.load(fh))
        if args.operation == "tasks":
            from .state import TaskStore
            require(args.state_dir is not None, "state-dir required")
            result = TaskStore(args.state_dir).listing(workflow)
        elif (args.state_dir or args.task_key) and args.reference is None:
            from .state import TaskStore, bind_source
            require(args.state_dir is not None and args.task_key is not None, "state-dir and task-key required")
            require(args.reference is None, "use task-key or reference, not both")
            store = TaskStore(args.state_dir)
            if args.operation == "start":
                require(args.repo is not None and args.manifest is not None, "repo and manifest required for durable start")
                store.require_external(args.repo)
                with open(args.manifest, encoding="utf-8") as fh:
                    manifest = json.load(fh)
                result = store.start(workflow, args.task_key, parse_parameters(args.parameters),
                                     bind_source(args.repo, manifest), digest(manifest), args.host,
                                     os.path.abspath(args.profile))
            else:
                require(args.host is None and args.parameters == "{}" and args.repo is None
                        and args.manifest is None, "resume cannot change the request")
                result = store.observe(workflow, args.task_key, args.operation == "collect")
        elif args.operation == "start":
            raise ContractError("durable start requires state-dir, task-key, repo and manifest")
        else:
            require(args.task_key is None, "use task-key or reference, not both")
            require(args.repo is None and args.manifest is None, "source options are start-only")
            require(args.reference is not None, "reference required")
            require(args.host is None and args.parameters == "{}", "resume cannot change the request")
            with open(args.reference, encoding="utf-8") as fh:
                reference = json.load(fh)["reference"]
            result = workflow.observe(reference, collect=args.operation == "collect")
        print(json.dumps(result, sort_keys=True))
        return 4 if result.get("observation") in ("unknown", "submission-unknown") else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Avoid echoing process-local paths, parameter values or raw transport logs.
        print(json.dumps(dict(schema=1, kind="workflow", observation="refused",
                              evidence="unchecked", engineering="unchecked",
                              next_action="correct-contract",
                              reason=str(exc) if isinstance(exc, ContractError)
                              else "cannot read/write workflow state; reuse the original task-key after repair, never create a retry key")))
        return 2


if __name__ == "__main__":
    # Run the PACKAGE's copy of this module, not this `__main__` copy. state.py
    # raises the package's ContractError; this copy's class is a different
    # object, so `isinstance` failed and every task-store refusal reached the
    # caller as "cannot read/write workflow state".
    import importlib
    sys.exit(importlib.import_module(__spec__.name).main())
