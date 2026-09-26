"""Structured failure reports: the handoff from implementation back to exploration.

The agentic workflow study's §4.11: each design stage is an exploration round
(chat) followed by an implementation round (an agentic or tracked flow). When
implementation cannot meet its contract, the failure is the input to the next
exploration round, so it must come back as a structured report, not as prose
or a silent retry.

`collect` writes `failure.json` and `failure.md` beside a task whenever a
finished job is not a verified engineering pass. A report holds:

  contract    what was asked: profile, host, required checks and corners, and
              the request/source/manifest identities (never parameter values)
  outcome     execution, artifact evidence, engineering verdict, failed and
              missing checks
  evidence    where to look: job id, tracker records, workspace, collection,
              and counts of fixed failure signatures in the job's logs
  attempts    earlier tasks for the same request and how they ended
  cause       `derived` by the tracker where that is mechanical, and a
              `declared` history (who, when, which class)
  exploration the decision the failure contradicts and the question it poses,
              and whether exploration has closed it

Causes come from the session-log harvest's closed enumeration
(browse/runlog.py CAUSES). The tracker derives only the mechanical classes:
`gate-fail` (the checks ran and said no) and `tool-error` (licence, crash,
environment). An agent may declare only those and `transport`; the judgement
classes (`engine-defect`, `stale-artifact`, `silent-pass`, `abandoned`) are
declared by a human, because an actor grading its own work is the one most
likely to be wrong about it (the harvest's rule).

Standard library only, Python 3.6+. Reports live in the private task store,
outside every checkout.
"""
import time

from .workflow import ContractError

#: browse/runlog.py CAUSES, minus `closed` (a pass writes no report).
CAUSES = {
    "gate-fail": "the checks ran and said no: a real engineering failure, found where it should be",
    "tool-error": "the EDA tool failed: licence, crash, environment",
    "transport": "ssh, sync or cluster failure, nothing to do with the design",
    "engine-defect": "our own code was wrong",
    "stale-artifact": "an input the checks protect was lost, stale or hand-edited",
    "silent-pass": "a check reported success against nothing",
    "abandoned": "stopped without a verdict: superseded, out of budget, or dropped",
    "unclassified": "not yet judged",
}
MECHANICAL = ("gate-fail", "tool-error", "transport")
TEXT_MAX = 400
TERMINAL = ("done", "failed", "killed")


def needs_report(result):
    """A finished job that is not a verified engineering pass."""
    return (result.get("observation") in TERMINAL
            and not (result.get("engineering") == "pass" and result.get("evidence") == "tracker-verified"))


def derive_cause(result, signatures):
    """-> (class, basis). Only mechanical classes; anything else stays unclassified."""
    sig = signatures or {}
    if result.get("engineering") == "fail" and result.get("evidence") == "tracker-verified":
        return "gate-fail", "the declared checks ran on verified artifacts and failed: %s" % (
            ", ".join(result.get("failed_checks") or []) or "see failed checks")
    for key, what in (("license", "licence"), ("crash", "tool crash"), ("environment", "tool environment")):
        if sig.get(key):
            return "tool-error", "%d %s signature line(s) in the job's logs" % (sig[key], what)
    hints = [k for k in ("traceback", "timeout", "memory", "disk") if sig.get(k)]
    if hints:
        return "unclassified", "log signatures: %s; judge the cause" % ", ".join(
            "%s %d" % (k, sig[k]) for k in hints)
    return "unclassified", "no mechanical signal; judge the cause"


def build(record, result, related, report_spec=None, existing=None, now=None):
    """-> the report for a collected, non-passing task. Declared parts of an
    existing report (cause history, exploration) are kept."""
    now = now or time.time()
    ref = record["reference"]
    required = report_spec or {}
    derived, basis = derive_cause(result, result.get("log_signatures"))
    old = existing or {}
    report = dict(
        schema=1, kind="failure-report", task_key=record["task_key"],
        created_at=old.get("created_at", now), updated_at=now,
        contract=dict(profile_id=record.get("profile_id"), profile_version=record.get("profile_version"),
                      repository=ref.get("repository"), host=ref.get("host"),
                      request_sha256=ref.get("request_sha256"), manifest_sha256=record.get("manifest_sha256"),
                      source_head=(record.get("source") or {}).get("head"),
                      required_checks=required.get("checks"), required_corners=required.get("corners"),
                      expected_artifacts=record.get("required_artifacts")),
        outcome=dict(observation=result.get("observation"), execution_rc=result.get("execution_rc"),
                     evidence=result.get("evidence"), engineering=result.get("engineering"),
                     checks_passed=result.get("checks_passed", 0), checks_failed=result.get("checks_failed", 0),
                     failed_checks=result.get("failed_checks") or [],
                     missing_checks=result.get("missing_checks") or [], issues=result.get("issues") or []),
        evidence=dict(job_id=ref.get("job_id"), task_id=ref.get("task_id"), workspace=ref.get("workspace"),
                      tracker_records="~/.asicjobs/%s/{meta,status,result}.json" % ref.get("job_id"),
                      collection="collection.json", log_signatures=result.get("log_signatures")),
        attempts=dict(count=1 + len(related), earlier=related,
                      started_at=record.get("created_at"), collected_at=now),
        cause=dict(derived=dict(cause=derived, basis=basis),
                   declared=old.get("cause", {}).get("declared", [])),
        exploration=old.get("exploration") or dict(contradicts=None, question=None, status="open", history=[]),
    )
    report["cause"]["current"] = (report["cause"]["declared"][-1]["cause"]
                                  if report["cause"]["declared"] else derived)
    return report


def declare(report, cause=None, by=None, note=None, contradicts=None, question=None, close=None, now=None):
    """Append a declaration. Raises ValueError for an unknown cause, or a
    judgement cause declared by an agent."""
    now = now or time.time()
    if cause is not None:
        if cause not in CAUSES or cause == "unclassified":
            raise ContractError("unknown cause %r; one of: %s" % (cause, ", ".join(sorted(set(CAUSES) - {"unclassified"}))))
        if by not in ("human", "agent"):
            raise ContractError("--by human or --by agent is required with --cause")
        if by == "agent" and cause not in MECHANICAL:
            raise ContractError("an agent may declare only %s; %r is a judgement for a human"
                             % ("/".join(MECHANICAL), cause))
        report["cause"]["declared"].append(dict(cause=cause, by=by, when=now, note=clip(note)))
        report["cause"]["current"] = cause
    ex = report["exploration"]
    if note is not None and cause is None:
        ex["history"].append(dict(field="note", value=clip(note), by=by or "unspecified", when=now))
    for key, value in (("contradicts", contradicts), ("question", question)):
        if value is not None:
            ex[key] = clip(value)
            ex["history"].append(dict(field=key, value=ex[key], by=by or "unspecified", when=now))
    if close is not None:
        ex["status"] = "closed"
        ex["history"].append(dict(field="closed", value=clip(close), by=by or "unspecified", when=now))
    report["updated_at"] = now
    return report


def clip(text):
    if text is None:
        return None
    text = " ".join(str(text).split())
    if len(text) > TEXT_MAX:
        raise ContractError("keep it to %d characters: the report points at evidence, it does not copy it" % TEXT_MAX)
    return text


def when(t):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t)) if t else "unknown"


def markdown(report):
    c, o, e, a, cause, ex = (report[k] for k in ("contract", "outcome", "evidence", "attempts", "cause", "exploration"))
    lines = ["# Failure report: `%s`" % report["task_key"], "",
             "Status: **%s**. Cause: **%s**%s." % (
                 ex["status"], cause["current"],
                 "" if cause["declared"] else " (derived by the tracker, not yet declared)"),
             "", "## Contract", "",
             "- Profile `%s` version `%s` (%s), host `%s`" % (c["profile_id"], c["profile_version"],
                                                            c["repository"], c["host"]),
             "- Required checks: %s; corners: %s" % (", ".join(c["required_checks"] or []) or "none declared",
                                                    ", ".join(c["required_corners"] or []) or "none declared"),
             "- Identities: request `%s`, manifest `%s`, source `%s`" % (
                 str(c["request_sha256"])[:12], str(c["manifest_sha256"])[:12], str(c["source_head"])[:12]),
             "", "## Outcome", "",
             "- Execution: %s (rc %s); artifacts: %s; engineering: **%s**" % (
                 o["observation"], o["execution_rc"], o["evidence"], o["engineering"]),
             "- Checks: %s passed, %s failed" % (o["checks_passed"], o["checks_failed"])]
    lines += ["- Failed check: `%s`" % x for x in o["failed_checks"]]
    lines += ["- Missing check: `%s`" % x for x in o["missing_checks"]]
    lines += ["- Issue: %s" % x for x in o["issues"]]
    sig = e.get("log_signatures")
    lines += ["", "## Evidence", "",
              "- Job `%s` (task `%s`); tracker records `%s`" % (e["job_id"], e["task_id"], e["tracker_records"]),
              "- Workspace `%s`; collection [collection.json](collection.json)" % e["workspace"],
              "- Log signatures: %s" % (", ".join("%s %d" % (k, v) for k, v in sorted(sig.items())
                                                    if k not in ("files",) and v) or "none"
                                         if isinstance(sig, dict) else "not read"),
              "", "## Attempts", "",
              "- %d attempt(s) at this request; started %s, collected %s" % (
                  a["count"], when(a["started_at"]), when(a["collected_at"]))]
    lines += ["- Earlier: `%s` job `%s`: %s / %s" % (r["task_key"], r["job_id"], r["observation"], r["engineering"])
              for r in a["earlier"]]
    lines += ["", "## Cause", "", "- Derived: **%s**: %s" % (cause["derived"]["cause"], cause["derived"]["basis"])]
    lines += ["- Declared %s by %s: **%s**%s" % (when(d["when"]), d["by"], d["cause"],
                                                  (": " + d["note"]) if d.get("note") else "")
              for d in cause["declared"]]
    lines += ["", "## For exploration", "",
              "- Contradicts: %s" % (ex["contradicts"] or "*not yet stated*"),
              "- Question: %s" % (ex["question"] or "*not yet stated*")]
    lines += ["- Note %s by %s: %s" % (when(h["when"]), h["by"], h["value"])
              for h in ex["history"] if h["field"] == "note"]
    lines += [""]
    lines += ["Record judgement with `jobs.workflow report --task-key %s --cause <class> --by human|agent "
              "[--contradicts ...] [--question ...] [--close ...]`. An agent may declare only %s." % (
                  report["task_key"], ", ".join(MECHANICAL)), ""]
    return "\n".join(lines)
