"""Private report validation beside tracker records. No report/log bytes emitted.

Input is a base64 JSON contract supplied by the local workflow. Python 3.6+.
"""
import base64
import hashlib
import json
import os
import re
import sys


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def read_json(path):
    with open(path, "rb") as fh:
        raw = fh.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("metadata too large")
    return decode(raw.decode("utf-8"))


def validate(contract):
    job = contract["job_id"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job):
        raise ValueError("invalid job")
    directory = os.path.join(os.environ.get("ASICJOBS_DIR", os.path.expanduser("~/.asicjobs")), job)
    result = dict(schema=1, kind="evidence", jobid=job, evidence="unverified",
                  engineering="unchecked", issues=[], checked=0, checks_passed=0, checks_failed=0,
                  missing_checks=[], failed_checks=[])
    meta = read_json(os.path.join(directory, "meta.json"))
    terminal = read_json(os.path.join(directory, "result.json"))
    status = read_json(os.path.join(directory, "status.json"))
    root = os.path.realpath(contract["workspace"])
    expected = contract["expected_artifacts"]
    if (any(v.get("jobid") != job or v.get("schema") != 1 for v in (meta, terminal, status))
            or terminal.get("state") not in ("done", "failed", "killed")
            or status.get("state") != terminal.get("state")
            or type(terminal.get("rc")) is not int
            or (terminal["state"] == "done") != (terminal["rc"] == 0)
            or os.path.realpath(meta["cwd"]) != root or meta.get("expect") != expected):
        result["issues"] = ["tracker-identity-mismatch"]
        return result
    artifacts = terminal["artifacts"]
    if (not expected or len(artifacts) != len(expected) or
            {a["path"] for a in artifacts} != set(expected)):
        result["issues"] = ["artifact-coverage-incomplete"]
        return result
    report_spec = contract.get("report")
    report_bytes = None
    for artifact in artifacts:
        name = artifact["path"]
        if (not isinstance(name, str) or name.startswith("/") or
                any(p in ("", ".", "..") for p in name.split("/"))):
            raise ValueError("invalid path")
        path = os.path.join(root, name)
        if (os.path.realpath(path) != os.path.abspath(path) or not os.path.isfile(path)
                or artifact.get("exists") is not True or artifact.get("jobid") != job
                or not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("sha256", "")))):
            result["issues"] = ["artifact-missing-unstamped-or-symlink"]
            return result
        sha = hashlib.sha256()
        size = 0
        chunks = []
        is_report = report_spec is not None and name == report_spec["path"]
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                size += len(block)
                if size > (1048576 if is_report else 104857600):
                    result["issues"] = ["artifact-size-limit"]
                    return result
                sha.update(block)
                if is_report:
                    chunks.append(block)
        if sha.hexdigest() != artifact["sha256"] or size != artifact.get("bytes"):
            result["issues"] = ["artifact-hash-or-size-mismatch"]
            return result
        result["checked"] += 1
        if is_report:
            report_bytes = b"".join(chunks)
    result["evidence"] = "tracker-verified"
    if report_spec is None:
        result["issues"] = ["no-engineering-parser"]
        return result
    identity = contract.get("identity")
    if identity is None:
        result["issues"] = ["durable-input-identity-required"]
        return result
    try:
        report = decode(report_bytes.decode("utf-8"))
        fields = {"schema", "job_id", "repository", "design", "top", "manifest_sha256",
                  "request_sha256", "source_sha256", "checks"}
        wanted = dict(identity, job_id=job, repository=contract["repository"],
                      design=report_spec["design"], top=report_spec["top"])
        mismatches = [k for k, v in wanted.items() if report.get(k) != v]
        if mismatches:
            result["issues"] = ["report-identity-mismatch:" + k for k in mismatches]
        if (set(report) != fields or type(report["schema"]) is not int or report["schema"] != 1
                or mismatches):
            raise ValueError("identity mismatch")
        checks = report["checks"]
        required = {(name, corner) for name in report_spec["checks"] for corner in report_spec["corners"]}
        if isinstance(checks, list) and all(isinstance(c, dict) and
                isinstance(c.get("name"), str) and isinstance(c.get("corner"), str) for c in checks):
            present = {(c["name"], c["corner"]) for c in checks}
            result["missing_checks"] = [name + "/" + corner for name, corner in sorted(required - present)]
        if (not isinstance(checks, list) or len(checks) != len(required)
                or any(set(c) != {"name", "corner", "status"} for c in checks)
                or {(c["name"], c["corner"]) for c in checks} != required
                or any(c["status"] not in ("pass", "fail") for c in checks)):
            raise ValueError("check coverage invalid")
        result["checks_passed"] = sum(c["status"] == "pass" for c in checks)
        result["checks_failed"] = sum(c["status"] == "fail" for c in checks)
        result["failed_checks"] = [c["name"] + "/" + c["corner"] for c in checks if c["status"] == "fail"]
        result["engineering"] = "fail" if result["checks_failed"] else "pass"
        if result["engineering"] == "pass" and terminal["rc"] != 0:
            result["engineering"] = "invalid"
            result["issues"] = ["execution-not-successful"]
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        result["engineering"] = "invalid"
        if not result["issues"]:
            result["issues"] = ["report-schema-or-check-coverage-invalid"]
    return result


def main():
    try:
        contract = decode(base64.b64decode(sys.argv[1], validate=True).decode("utf-8"))
        result = validate(contract)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, IndexError):
        result = dict(schema=1, kind="evidence", evidence="unverified", engineering="unchecked",
                      issues=["evidence-read-or-contract-error"])
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
