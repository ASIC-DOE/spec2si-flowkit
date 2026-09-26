"""Bounded command recognition and hook adapter; no commands are executed.

Python 3.6+, stdlib. Invoke by absolute path with --config PRIVATE_JSON.
Input/output use the common Codex/Claude command-hook JSON contract.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile

SHELL_TOOLS = ("Bash", "PowerShell", "exec_command", "shell", "shell_command")
MODULES = ("jobs.workflow", "deployment.bnl.jobs.workflow")
GUIDANCE = ("Use the configured jobs.workflow profile for supported compute requests by default. "
            "Retain the returned reference; resume/status reads the existing job, never starts it again. "
            "An unknown submission is reconciled by repeating start with the SAME task-key: it attaches to the "
            "job the tracker holds for that key, or dispatches under that key only if the tracker has none. "
            "Never retry under a new key. Collect before reporting results. "
            "Execution completion and tracker-verified artifacts are not an engineering pass; "
            "Report engineering pass/fail only from collect's validated engineering field; unchecked/invalid is not pass. "
            "Use ordinary transport for read-only diagnostics. Do not bypass a denial using another shell. "
            "In a one-shot or headless session, do not end on a background poll: wait in the foreground with "
            "collect --wait <seconds> (at most 540 per call; repeat while it is still pending), or, if the job "
            "outlasts the session, end with each task key and its collect command. A Stop hook refuses to end a "
            "session holding a job it started and never collected. "
            "A collect that is not a verified pass writes a failure report (failure_report): give the user its "
            "path and failed checks; record what you know with report --task-key K [--cause gate-fail|tool-error|"
            "transport --by agent] [--question ...]; judgement causes are the user's; failures lists open reports.")


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    required = {"schema", "roots", "hosts", "routes", "entrypoint", "receipt_dir"}
    if (not required <= set(cfg) or set(cfg) - required - {"state_dir", "wrappers", "repository"}
            or cfg["schema"] != 1):
        raise ValueError("invalid config schema")
    for key in ("roots", "hosts", "routes"):
        if not isinstance(cfg[key], list) or not cfg[key]:
            raise ValueError("empty config scope")
    if not all(isinstance(x, str) and x for x in cfg["roots"] + cfg["hosts"]):
        raise ValueError("invalid scope")
    if not all(isinstance(cfg[x], str) and cfg[x] for x in ("entrypoint", "receipt_dir")):
        raise ValueError("invalid entrypoint/cache")
    if not os.path.isabs(cfg["receipt_dir"]):
        raise ValueError("receipt_dir must be absolute on the hook host")
    if "repository" in cfg and not (isinstance(cfg["repository"], str) and cfg["repository"]):
        raise ValueError("repository must be a non-empty string")
    if "wrappers" in cfg and (not isinstance(cfg["wrappers"], list)
                              or not all(isinstance(x, str) and x for x in cfg["wrappers"])):
        raise ValueError("wrappers must be a list of executable names")
    if "state_dir" in cfg and (not isinstance(cfg["state_dir"], str) or not os.path.isabs(cfg["state_dir"])):
        raise ValueError("state_dir must be absolute on the hook host")
    for route in cfg["routes"]:
        if (not {"name", "executables", "profile"} <= set(route)
                or set(route) - {"name", "executables", "profile", "argument_prefixes"}
                or not route["executables"]):
            raise ValueError("invalid route")
        if "argument_prefixes" in route and (not isinstance(route["argument_prefixes"], list)
                or not route["argument_prefixes"] or not all(isinstance(p, list) and p
                and all(isinstance(a, str) and a for a in p) for p in route["argument_prefixes"])):
            raise ValueError("invalid route argument prefixes")
        if not all(isinstance(v, str) and v for v in
                   [route["name"], route["profile"]] + route["executables"]):
            raise ValueError("invalid route fields")
    return cfg


def canonical(path):
    # Compare configured Windows and POSIX aliases lexically; no remote FS access.
    path = path.replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:/", path):
        import ntpath
        return ntpath.normpath(path).replace("\\", "/").lower()
    import posixpath
    return posixpath.normpath(path)


def in_scope(event, cfg):
    inp = event.get("tool_input") or {}
    paths = [event.get("cwd", "")]
    if isinstance(inp, dict):
        paths += [inp.get("cwd", ""), inp.get("workdir", "")]
    return any(canonical(p) == canonical(root) or canonical(p).startswith(canonical(root) + "/")
               for p in paths if isinstance(p, str) and p for root in cfg["roots"])


def base(token):
    return re.sub(r"\.exe$", "", token.replace("\\", "/").rsplit("/", 1)[-1].lower())


def tokenize(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.escape = ""  # Preserve native Windows paths; escaped shell forms are unsupported.
    return list(lex)


def inspect_command(command, cfg, depth=0):
    """Return (denied route names, workflow invocation seen, opaque syntax).

    This is a small recognizer, not a shell parser/security boundary. Never use
    the presence of a workflow token to exempt other commands in the same call.
    """
    if depth > 6:
        return set(), False, True
    try:
        tokens = tokenize(command)
    except ValueError:
        return set(), False, True
    denied, workflow, opaque = set(), False, False
    segment = []
    for token in tokens + [";"]:
        if token and all(c in ";&|()\n" for c in token):
            if segment:
                d, w, o = inspect_argv(segment, cfg, depth)
                denied.update(d)
                workflow |= w
                opaque |= o
                segment = []
        else:
            segment.append(token)
    return denied, workflow, opaque


def prefix_after_options(rest, prefix):
    # The prefix may follow leading options: each earlier argument is an option
    # or the value directly after one. A positional word before it is another
    # subcommand, so the search stops there.
    for i in range(len(rest)):
        if rest[i:i + len(prefix)] == prefix:
            return True
        if not (rest[i].startswith("-") or (i > 0 and rest[i - 1].startswith("-"))):
            return False
    return False


def inspect_argv(args, cfg, depth):
    if depth > 6:
        return set(), False, True
    while args and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0]):
        args = args[1:]
    if not args:
        return set(), False, False
    exe = base(args[0])
    rest = args[1:]
    for route in cfg["routes"]:
        if any((base(x) == exe if "/" not in x.replace("\\", "/") else
                (args[0].replace("\\", "/").lstrip("./") == x.replace("\\", "/") or
                 args[0].replace("\\", "/").endswith("/" + x.replace("\\", "/"))))
               for x in route["executables"]):
            prefixes = route.get("argument_prefixes")
            if prefixes is None or any(prefix_after_options(rest, p) for p in prefixes):
                return {route["name"]}, False, False
    if exe in ("nohup", "setsid", "start-process", "start-job"):
        if exe == "start-job":
            return inspect_command(" ".join(rest).strip("{} "), cfg, depth + 1)
        if rest and rest[0].lower() == "-filepath":
            rest = rest[1:]
        denied, workflow, _ = inspect_argv(rest, cfg, depth + 1)
        # Unrelated local background work is outside the compute route policy.
        return denied, workflow, not bool(denied)
    if exe in cfg.get("wrappers", ()):
        # A project's own launch wrapper (e.g. a tool-activation script that
        # runs its argv): the command after `--` if there is one, else after
        # leading options. Without this the wrapped launch is never inspected.
        if "--" in rest:
            rest = rest[rest.index("--") + 1:]
        else:
            while rest and (rest[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0])):
                rest = rest[1:]
        if not rest:
            return set(), False, True
        return inspect_argv(rest, cfg, depth + 1)
    if exe in ("env", "exec", "command", "wsl"):
        # Supported wrappers: env KEY=VALUE cmd; wsl [-e|--exec] cmd.
        while rest and (rest[0] in ("-e", "--exec", "--") or
                        re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0])):
            rest = rest[1:]
        if not rest or rest[0].startswith("-"):
            return set(), False, True
        return inspect_argv(rest, cfg, depth + 1)
    if exe in ("bash", "sh", "tcsh", "csh", "pwsh", "powershell", "cmd"):
        for i, value in enumerate(rest):
            if value.lower() in ("-c", "-lc", "-command", "/c"):
                source = (rest[i + 1] if exe in ("sh", "bash", "tcsh", "csh") and i + 1 < len(rest)
                          else " ".join(rest[i + 1:]))
                return inspect_command(source, cfg, depth + 1)
        if rest and not rest[0].startswith("-"):
            return inspect_argv(rest, cfg, depth + 1)
        return set(), False, True  # stdin/script content is not visible.
    if exe == "ssh":
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in ("-o", "-p", "-i", "-l", "-F", "-J") else 1
        if i >= len(rest):
            return set(), False, True
        host = rest[i].rsplit("@", 1)[-1]
        if host not in cfg["hosts"]:
            return set(), False, False
        return inspect_command(" ".join(rest[i + 1:]), cfg, depth + 1)
    if exe == "py" or re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", exe):
        if rest and rest[0] == "-3":
            rest = rest[1:]
        if len(rest) >= 2 and rest[0] == "-m" and rest[1] in MODULES:
            return set(), True, False
        if rest and not rest[0].startswith("-"):
            return inspect_argv(rest, cfg, depth + 1)
        return set(), False, True
    return set(), False, False


def context(event_name, message):
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": message}}


def cache_dir(event, cfg):
    session = event.get("session_id")
    if not isinstance(session, str) or not session:
        raise ValueError("session_id required")
    return os.path.join(cfg["receipt_dir"], hashlib.sha256(session.encode("utf-8")).hexdigest())


def envelopes(value, depth=0):
    """Bounded extraction from known response wrappers, never from stderr."""
    if depth > 5:
        return
    if isinstance(value, dict):
        if value.get("kind") == "workflow" and value.get("schema") == 1:
            yield value
        else:
            for key in ("stdout", "output", "text", "content"):
                if key in value:
                    yield from envelopes(value[key], depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            yield from envelopes(item, depth + 1)
    elif isinstance(value, str):
        for line in value.splitlines()[:1000]:
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, (dict, list)):
                yield from envelopes(parsed, depth + 1)


#: How many times one session's Stop hook may refuse to end before it lets go.
STOP_BLOCKS_MAX = 3
TERMINAL = ("done", "failed", "killed")
OBSERVATIONS = TERMINAL + ("submitted", "running", "unknown", "submission-unknown")


def settled(envelope):
    """Collected to a terminal state: a finished job whose next action is no longer `collect`."""
    return envelope.get("observation") in TERMINAL and envelope.get("next_action") != "collect"


def save_receipt(event, cfg, envelope):
    ref = envelope.get("reference", {})
    task = envelope.get("task_id", "")
    if (not isinstance(ref, dict) or not isinstance(task, str)
            or not re.fullmatch(r"task-[0-9a-f]{32}", task)
            or ref.get("task_id") != task or ref.get("host") != envelope.get("host")
            or ref.get("job_id") != envelope.get("job_id")):
        raise ValueError("invalid workflow reference")
    # Cache only the WP1 contract, never arbitrary output/log fields.
    keys = ("schema", "task_id", "host", "job_id", "profile_sha256", "repository",
            "workspace", "request_sha256")
    if set(ref) != set(keys) or not all(isinstance(ref[k], str) for k in keys if k not in ("schema", "job_id")):
        raise ValueError("invalid reference fields")
    safe = {"schema": 1, "kind": "workflow", "reference": ref}
    directory = cache_dir(event, cfg)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    # Whether this session collected it, for the Stop hook. Once collected, a later status read
    # (whose next action is "collect" again) does not make it open.
    key = envelope.get("task_key")
    if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", key):
        safe["task_key"] = key
    if envelope.get("observation") in OBSERVATIONS:
        safe["observation"] = envelope["observation"]
    try:
        with open(os.path.join(directory, task + ".json"), encoding="utf-8") as fh:
            before = json.load(fh).get("settled") is True
    except (OSError, ValueError, AttributeError):
        before = False
    safe["settled"] = before or settled(envelope)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".receipt-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(safe, fh, sort_keys=True)
        target = os.path.join(directory, task + ".json")
        os.replace(temporary, target)
        return target
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def last_assistant_text(event):
    """The session's last assistant message: the Stop event's own field, else the transcript's tail."""
    text = event.get("last_assistant_message")
    if isinstance(text, str):
        return text
    path = event.get("transcript_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - 2097152))
        lines = fh.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if isinstance(entry, dict) and entry.get("type") == "assistant" and isinstance(message, dict):
            parts = message.get("content")
            if isinstance(parts, str):
                return parts
            texts = [x.get("text", "") for x in parts or [] if isinstance(x, dict) and x.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return ""


def stop(event, cfg):
    """Refuse to end a session that started a tracked job and never collected it.

    A one-shot session that ends on a background timer leaves its own result unread (B versus C,
    2026-09-26: 4 of 6 B runs on 1-3 minute jobs). The session may end once every job it started
    is collected, or when its last message hands each open job off by task key (a job longer than
    the session), or after STOP_BLOCKS_MAX refusals, so it can never be held forever.
    """
    directory = cache_dir(event, cfg)
    names = sorted(n for n in os.listdir(directory)
                   if re.fullmatch(r"task-[0-9a-f]{32}\.json", n)) if os.path.isdir(directory) else []
    open_jobs = []
    for name in names:
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                receipt = json.load(fh)
        except (OSError, ValueError):
            continue
        if receipt.get("settled") is False:   # receipts from before this rule carry no field: not held
            open_jobs.append(receipt)
    if not open_jobs:
        return {}
    said = last_assistant_text(event)
    names = [r.get("task_key") or r["reference"].get("job_id") or r["reference"]["task_id"] for r in open_jobs]
    if all(any(x and x in said for x in (r.get("task_key"), r["reference"].get("job_id"), r["reference"]["task_id"]))
           for r in open_jobs):
        return {}   # handed off by name
    counter = os.path.join(directory, "stop-blocks")
    try:
        with open(counter, encoding="utf-8") as fh:
            count = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        count = 0
    if count >= STOP_BLOCKS_MAX:
        return {}
    with open(counter, "w", encoding="utf-8") as fh:
        fh.write(str(count + 1))
    return {"decision": "block", "reason": (
        "This session started tracked job(s) it has not collected: " + ", ".join(names) + ". Before ending, "
        "collect each in the foreground: " + cfg["entrypoint"] + " collect --profile <its profile> --state-dir "
        "<its store> --task-key <key> --wait 540, and repeat while it reports still_pending. If a job will run "
        "longer than you can wait, end instead with each task key and its collect command and say the result is "
        "pending. Report engineering pass/fail only from a completed collect.")}


def handle(event, cfg):
    kind = event.get("hook_event_name")
    if not in_scope(event, cfg):
        return {}
    if kind == "Stop":
        return stop(event, cfg)
    if kind == "SessionStart":
        directory = cache_dir(event, cfg)
        paths = sorted(os.path.join(directory, n) for n in os.listdir(directory)
                       if re.fullmatch(r"task-[0-9a-f]{32}\.json", n)) if os.path.isdir(directory) else []
        profiles = [{"name": r["name"], "profile": r["profile"]} for r in cfg["routes"]]
        durable = ""
        if "state_dir" in cfg:
            state_dir = cfg["state_dir"]
            records = sorted(os.path.join(state_dir, n, "task.json")
                             for n in os.listdir(state_dir)
                             if re.fullmatch(r"request-[0-9a-f]{64}", n)) if os.path.isdir(state_dir) else []
            durable = (" Durable task store: " + json.dumps(cfg["state_dir"]) +
                       ". Use tasks --profile <profile> --state-dir <store> to discover references across sessions; "
                       "resume --task-key <key> --state-dir <store> queries the tracker. "
                       "New starts require --state-dir, a stable --task-key, --repo and --manifest. "
                       "Reuse the same key after interruption; never create another key to retry an uncertain launch. "
                       "Cross-session record paths (may include unresolved reservations): " + json.dumps(records[:20]) +
                       ("; use tasks to list all records." if len(records) > 20 else ""))
        return context(kind, GUIDANCE + " Entry point: " + cfg["entrypoint"] +
                       durable +
                       ". Profiles: " + json.dumps(profiles) +
                       ". Cached references (observations may be stale; query resume): " +
                       json.dumps(paths[:20]) + ("; more references in " + directory if len(paths) > 20 else ""))
    if event.get("tool_name") not in SHELL_TOOLS:
        return {}
    inp = event.get("tool_input") or {}
    command = inp.get("command", inp.get("cmd")) if isinstance(inp, dict) else None
    if not isinstance(command, str):
        return context(kind, "Cluster-job guard coverage unavailable for this tool input shape.")
    denied, workflow, opaque = inspect_command(command, cfg)
    if kind == "PreToolUse" and denied:
        routes = [r for r in cfg["routes"] if r["name"] in denied]
        alternatives = [cfg["entrypoint"] + " start --profile " + json.dumps(r["profile"]) +
                        " --parameters '<required JSON>' --state-dir " + json.dumps(cfg.get("state_dir", "<private-state-dir>")) +
                        " --task-key <stable-key> --repo <checkout> --manifest <input-manifest.json>" for r in routes]
        reason = ("Tracked compute required: " + ", ".join(sorted(denied)) + ". " +
                  ("Use " + " OR ".join(alternatives) if alternatives else
                   "Use a configured foreground workflow profile; unsupported detached launches need an adapter.") +
                  ". Preserve the requested host via --host. Correct the invocation; do not ask for a tracker exception.")
        return {"hookSpecificOutput": {"hookEventName": kind, "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    if kind == "PostToolUse" and workflow:
        found = [e for e in envelopes(event.get("tool_response")) if "reference" in e]
        # A session's hooks belong to ONE project. A reference from another
        # repository is that project's to record; saving it here would put a
        # job under the wrong project's receipts and resume context.
        own = cfg.get("repository")
        foreign = sorted({str((e.get("reference") or {}).get("repository")) for e in found
                          if own and (e.get("reference") or {}).get("repository") != own})
        paths = [save_receipt(event, cfg, e) for e in found
                 if not own or (e.get("reference") or {}).get("repository") == own]
        note = (" Not captured here (another repository's job; record it from that project): "
                + ", ".join(foreign) + "." if foreign else "")
        return context(kind, ("Workflow references captured: " + json.dumps(paths) if paths else
                              "No workflow reference captured; retain the original result. If launch acknowledgement is lost, repeat start with the same task-key to reconcile; never use a new key.") +
                       note +
                       " Collect before reporting results. Use the validated engineering field and failed/missing checks; never infer pass from process exit or hashes."
                       " If collect wrote a failure_report, give the user its path.")
    if kind == "PreToolUse" and opaque:
        return context(kind, "Guard cannot inspect this script/stdin/inline-code payload. Use the configured workflow for compute; this path has advisory coverage only.")
    return {}  # No 'allow': never override another hook or permission policy.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    event = {}
    try:
        raw = sys.stdin.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("hook input too large")
        event = json.loads(raw)
        output = handle(event, load_config(args.config))
        print(json.dumps(output))
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # A broken pre-hook must not silently report success. Post hooks cannot
        # undo a launch: provide recovery guidance without masking its result.
        if isinstance(event, dict) and event.get("hook_event_name") in ("SessionStart", "PostToolUse"):
            print(json.dumps(context(event["hook_event_name"], "Cluster-job integration error: reference capture/context unavailable. Retain the original result and repair the private hook config; do not resubmit an uncertain launch.")))
            return 0
        print("Cluster-job guard could not validate its input/configuration; repair the integration before retrying.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
