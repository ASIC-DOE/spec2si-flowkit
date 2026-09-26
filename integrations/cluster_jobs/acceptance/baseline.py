"""Condition A of the agentic workflow report's §8: how chat sessions use the cluster.

    python integrations/cluster_jobs/acceptance/baseline.py --repo tsmc65 --since 2026-08-30 \
        --until 2026-09-24 [--json out.json] [--sample eda-detached:20]

Reads Claude Code transcripts (~/.claude/projects/C--dev-spec2si-<repo>*/*.jsonl)
LOCALLY and prints only counts. Commands are classified in memory and never
written out; `--sample` prints examples to the terminal for checking the
classifier by hand and must not be pasted into a committed document (NDA:
commands can carry PDK paths). The committed session-log harvest
(`browse/runlog.py`) cannot answer these questions because it keeps no
commands, by the same rule.

Transcripts expire (cleanupPeriodDays, default 30), so a window is only as old
as the oldest surviving file. Tool calls are de-duplicated by id: a resumed
session copies its history into a new file. Headless trial sessions (`claude -p`,
entrypoint sdk-cli) are excluded; subagent calls count.

Shell calls that touch the cluster (ssh, remote_task.sh, the tool wrappers,
scp/rsync) are classified, first match wins:
  tracked   jobs.workflow / runjob (the tracker)
  eda       a licensed EDA tool or a flow runner that calls one (EDA,
            LAUNCH_SCRIPT), split into -detached (nohup, setsid, screen, tmux,
            a trailing &) and -foreground
  compute   another command run through the tool wrapper (python analysis,
            generators): cluster compute, usually unlicensed
  transfer  scp / rsync / push scripts
  kill      kill / pkill
  read      everything else: log tails, greps, ps, ls -- monitoring and diagnosis
The classes are heuristics. Check them with --sample before quoting a number.

The "after" measurement (condition B in ordinary use) adds two things:
  --exclude-session ID   leave out a session (the tracker's own development
                         session is not ordinary use); repeatable
  --hook PATH            the repo's deployment/bnl/tracker_hook.py: every shell
                         call naming a migrated flow is put to that hook as a
                         synthetic PreToolUse event, so "a launch of a migrated
                         flow in its untracked form" means exactly what the
                         deployed guard means. Reported: tracked starts, such
                         launches the hook refused in the session, and such
                         launches that ran (escapes).
"""
import subprocess
import argparse
import collections
import glob
import json
import os
import random
import re
import sys

CLUSTER = re.compile(r"\bssh\b|remote_task\.sh|asic_tools\w*\.csh|\bscp\b|\brsync\b|push(_flow)?\.sh")
TRACKED = re.compile(r"jobs\.workflow\s+(start|resume|status|collect|tasks)\b|\brunjob\b")
TRANSFER = re.compile(r"\bscp\b|\brsync\b|push(_flow)?\.sh")
KILL = re.compile(r"\b(kill|pkill|killall)\b")
DETACHED = re.compile(r"\bnohup\b|\bsetsid\b|\bscreen\s+-d|\btmux\b|\bdisown\b|[^&>|]&\s*(?:[\"';)]|$)", re.M)
EDA = {"spectre", "virtuoso", "genus", "innovus", "calibre", "pegasus", "pvs", "quantus", "qrc",
       "xrun", "ocean", "xcelium", "spectremdl", "joules", "tempus", "voltus", "conformal", "lec"}
#: A command run through a wrapper whose head is not one of these is compute.
READ_HEADS = {"cat", "tail", "head", "grep", "egrep", "zgrep", "ls", "ps", "pgrep", "find", "du", "df",
              "wc", "echo", "printf", "stat", "md5sum", "sha256sum", "diff", "cmp", "readlink", "test", "[",
              "which", "type", "command", "hostname", "date", "awk", "sed", "sort", "uniq", "less", "more",
              "file", "cut", "tr", "true", "false", "env", "printenv", "id", "whoami", "uptime", "free",
              "nproc", "lmstat", "lmutil", "mkdir", "cp", "mv", "rm", "ln", "chmod", "touch", "tar", "gzip",
              "gunzip", "zcat", "cd", "pwd", "sleep", "kill", "pkill", "readelf", "strings", "xxd", "od",
              "basename", "dirname", "realpath", "tee", "xargs", "jq", "column", "numfmt", "comm", "join",
              "git", "svn", "sos", "soscmd", "for", "while", "until", "if", "do", "done", "then", "else",
              "elif", "fi", "set", "export", "source", "unset", "exit", "return", "local", "read", "wait",
              "trap", "shift", "case", "esac", "PATTERN"}
#: Script names that are licensed-flow runners even when run directly.
LAUNCH_SCRIPT = re.compile(r"(^|/)(run\.py|run_[\w.-]*\.(sh|py)|launch_[\w.-]*\.sh|[\w.-]*_flow\.py|"
                           r"pex\.py|drc[\w.-]*\.(sh|py)|lvs[\w.-]*\.(sh|py)|[\w.-]*sweep[\w.-]*\.(sh|py))$")
PREFIX = {"nohup", "setsid", "exec", "time", "nice", "stdbuf", "env", "command", "builtin", "sudo"}
SHELLS = {"bash", "sh", "tcsh", "csh", "zsh"}
#: Quoted arguments of these are data (a grep pattern's `|` or tool name is not a command).
PATTERN_ARG = re.compile(r"\b(grep|egrep|zgrep|pgrep|pkill|awk|sed|jq|echo|printf|Select-String|findstr)\b"
                         r"((?:\s+-[\w-]+(?:\s+[$\w][\w.$]*)?)*)\s+(\"[^\"]*\"|'[^']*')")
HEREDOC = re.compile(r"<<-?\s*[\"']?(\w+)[\"']?")
WAIT_TOOLS = ("Monitor", "ScheduleWakeup", "BashOutput", "TaskOutput")
#: The migrated flows' executables, by repository: the prefilter before a command is put to the hook.
MIGRATED = {"tsmc65": ("dig_flows/run.py", "run.py", "tracked_job.py"),
            "tsmc28": ("tracked_adc.py", "adc_cal_bench", "bandgap_dc.py", "tracked_job.py"),
            "xt011": ("run_buf_bench.sh", "launch_buf_bench.sh", "tracked_job.py"),
            "sky130": ("run_schematic.sh", "tracked_job.py")}
TRACKED_START = re.compile(r"jobs\.workflow\s+start\b")
TRACKED_COLLECT = re.compile(r"jobs\.workflow\s+collect\b")
HOOK_DENIAL = "Tracked compute required"


REMOTE_LINE = "\x01"   # marks a line of a heredoc fed to ssh
VERSION_PROBE = {"-version", "--version", "-V", "-help", "--help", "-h"}


def shell_text(cmd):
    """The command without non-command text: heredoc bodies that are file or
    Python content, quoted-text continuation lines, and quoted grep/pgrep/echo
    patterns. Lines of a heredoc fed to ssh are kept and marked remote."""
    lines, out, i = re.sub(r"\\\r?\n", " ", cmd).split("\n"), [], 0   # join `\` continuations
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.lstrip()[:1] in ("'", '"'):
            continue   # the rest of a multi-line quoted argument (a message, a pattern)
        out.append(line)
        m = HEREDOC.search(line)
        if m:
            body = []
            while i < len(lines) and lines[i].strip() != m.group(1):
                body.append(lines[i])
                i += 1
            before = line[:m.start()]
            if not re.search(r"\b(python3?|py|cat|tee|perl|awk)\b|>\s*\S", before):
                mark = REMOTE_LINE if re.search(r"\bssh\b", before) else ""
                out.extend(mark + b for b in body)
            i += 1
    return PATTERN_ARG.sub(lambda m: m.group(1) + m.group(2) + " PATTERN", "\n".join(out))


def segments(text):
    """-> [(segment, remote)] split at ; && || | newline, inside quotes too (the
    remote shell splits them). A segment is remote when it lies in a quote that
    was opened after `ssh`, or on a line of an ssh-fed heredoc."""
    out, buf, quote, quote_remote, seen_ssh = [], [], None, False, False
    line_remote = text.startswith(REMOTE_LINE)
    i = 0

    def emit():
        seg = "".join(buf).strip()
        if seg:
            out.append((seg, line_remote or (quote is not None and quote_remote)))
        del buf[:]
    while i < len(text):
        ch = text[i]
        two = text[i:i + 2]
        if quote is None and ch in "'\"":
            quote, quote_remote = ch, seen_ssh or re.search(r"\bssh\b", "".join(buf)) is not None
            i += 1
            continue
        if quote is not None and ch == quote:
            quote = None
            i += 1
            continue
        if quote is not None and not quote_remote and ch != "\n":
            buf.append(ch)   # quoted data (a message, a pattern), not commands
            i += 1
            continue
        if two in ("&&", "||", "$(") or ch in ";|`\n":
            emit()
            if quote is None:
                seen_ssh = False   # a new outer command; inside a quote the ssh still applies
            if ch == "\n":
                line_remote = text[i + 1:i + 2] == REMOTE_LINE
            i += 2 if two in ("&&", "||", "$(") else 1
            continue
        if quote is None and re.match(r"ssh\b", text[i:i + 4]) and (i == 0 or not text[i - 1].isalnum()):
            seen_ssh = True
        buf.append(ch)
        i += 1
    emit()
    return out


def heads(text):
    """-> [(head, wrapped, raw, remote, next)] per simple command, seen through
    ssh, shells and wrappers."""
    out = []
    for seg, remote in segments(text):
        toks = [t.strip("\"'(){}" + REMOTE_LINE) for t in seg.split()]
        toks = [t for t in toks if t]
        if toks and (toks[0].startswith("#") or toks[0].startswith("<<")):
            continue   # a comment, or a heredoc marker left on its own
        wrapped = False
        i = 0
        while i < len(toks):
            t = toks[i]
            base = t.rsplit("/", 1)[-1]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t) or base in PREFIX:
                i += 1
            elif base == "timeout":
                i += 2
            elif base == "ssh":
                remote = True
                i += 1
                while i < len(toks) and toks[i].startswith("-"):
                    i += 2 if toks[i] in ("-o", "-i", "-p", "-l", "-J", "-F") else 1
                i += 1  # the host
            elif base in SHELLS:
                i += 1
                while i < len(toks) and toks[i].startswith("-"):
                    i += 1
            elif re.match(r"asic_tools\w*\.csh$", base):
                wrapped = True
                i += 1
            elif base == "remote_task.sh":
                wrapped = True
                i = toks.index("--", i) + 1 if "--" in toks[i:] else len(toks)
            elif base in ("python", "python3", "py") or re.match(r"python3\.\d+$", base):
                i += 1
                while i < len(toks) and toks[i].startswith("-"):
                    i += 2 if toks[i] in ("-m", "-c") else 1
            else:
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                out.append((base, wrapped, toks[i], remote or wrapped, nxt))
                break
    return out


#: `ssh host bash -s < script` or `ssh host < script`: the remote commands are
#: in a file. It is resolved from an earlier write in the same session (the
#: Write tool, or `cat > script <<EOF`); otherwise the call stays "opaque".
OPAQUE = re.compile(r"\bssh\b[^\n;|&]*(?<![<0-9])<(?!<)\s*[\"']?([^\s\"';|&<>]+)")
SCRIPT_WRITE = re.compile(r"\bcat\s*>\s*[\"']?([^\s\"'<>]+)[\"']?\s*<<-?\s*[\"']?(\w+)[\"']?")
SCRIPT_SUFFIX = (".sh", ".bash", ".csh", ".tcsh")


def written_scripts(cmd):
    """-> {basename: body} for scripts a shell command writes with a heredoc."""
    found, lines = {}, cmd.split("\n")
    for n, line in enumerate(lines):
        m = SCRIPT_WRITE.search(line)
        if m and m.group(1).endswith(SCRIPT_SUFFIX):
            body = []
            for rest in lines[n + 1:]:
                if rest.strip() == m.group(2):
                    break
                body.append(rest)
            found[m.group(1).rsplit("/", 1)[-1]] = "\n".join(body)
    return found


#: Why the last call was classified as it was (for --sample review only).
REASON = []


def kind_of(text, scripts, wrapped_ctx=False, depth=0):
    """-> "eda", "compute" or None for the remote commands in `text`."""
    kind = None
    for base, wrapped, raw, remote, nxt in heads(text):
        wrapped = wrapped or wrapped_ctx
        if not (remote or wrapped_ctx) or nxt in VERSION_PROBE:
            continue
        if base in EDA or LAUNCH_SCRIPT.search(raw):
            REASON.append("eda head %r (depth %d)" % (raw[-60:], depth))
            return "eda"
        if base in scripts and depth < 2:
            sub = kind_of(remote_body(scripts[base]), scripts, wrapped, depth + 1)
            if sub == "eda":
                REASON.append("via script %r" % base)
                return sub
            kind = kind or sub
            continue
        if wrapped and base not in READ_HEADS:
            REASON.append("wrapped head %r (depth %d)" % (raw[-60:], depth))
            kind = kind or "compute"
    return kind


def remote_body(body):
    return "\n".join(REMOTE_LINE + line for line in shell_text(body).split("\n"))


def classify(cmd, scripts=None):
    scripts = scripts or {}
    text = shell_text(cmd)
    if TRACKED.search(text):
        return "tracked"   # the tracker runs locally and reaches the cluster itself
    if not CLUSTER.search(text):
        return None
    kind = kind_of(text, scripts)
    opaque = OPAQUE.search(text)
    if kind is None and opaque:
        name = opaque.group(1).rsplit("/", 1)[-1]
        if name not in scripts:
            return "opaque"
        kind = kind_of(remote_body(scripts[name]), scripts, False, 1)
        text = text + "\n" + scripts[name]
    if kind:
        return kind + ("-detached" if DETACHED.search(shell_text(text)) else "-foreground")
    if TRANSFER.search(cmd):
        return "transfer"
    if KILL.search(text):
        return "kill"
    return "read"


def normalize(cmd):
    return re.sub(r"\d+", "N", re.sub(r"\s+", " ", cmd)).strip()


def sleep_seconds(cmd):
    s = sum(int(n) for n in re.findall(r"\bsleep\s+(\d+)\b", cmd))
    return s + sum(int(n) for n in re.findall(r"Start-Sleep\s+(?:-Seconds\s+)?(\d+)", cmd))


def seconds_between(start, end):
    import datetime
    try:
        a, b = (datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) for t in (start, end))
    except (TypeError, ValueError, AttributeError):
        return None
    d = (b - a).total_seconds()
    return d if 0 <= d < 86400 else None


def scan(repo, since, until, root, exclude=()):
    calls, results = {}, {}
    denials = set()
    ended = {}
    sessions = collections.defaultdict(lambda: dict(prompts=0, interrupts=0, first=None, last=None))
    prompt_ids = set()
    for f in glob.glob(os.path.join(root, "C--dev-spec2si-" + repo + "*", "*.jsonl")):
        for line in open(f, encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = d.get("timestamp") or ""
            if d.get("entrypoint") == "sdk-cli":
                continue
            sid = d.get("sessionId") or "?"
            if sid in exclude:
                continue
            inside = since <= ts[:10] < until
            if not inside:
                # Outside the window a session's calls are not counted, but the scripts it wrote are
                # remembered: a window that starts mid-session otherwise sees `ssh host bash -s < f`
                # for an f written the day before as opaque (19 % of the 2026-09-25/26 interim).
                content = (d.get("message") or {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("id") not in calls:
                        inp = c.get("input") or {}
                        cmd = inp.get("command") if c.get("name") in ("Bash", "PowerShell") else None
                        entry = dict(session=sid, name=c.get("name"), cmd=cmd or "", ts=ts, outside=True)
                        path = str(inp.get("file_path") or "")
                        if c.get("name") == "Write" and path.endswith(SCRIPT_SUFFIX):
                            entry["script"] = (path.replace("\\", "/").rsplit("/", 1)[-1], str(inp.get("content") or ""))
                        calls[c["id"]] = entry
                continue
            s = sessions[sid]
            s["first"] = min(filter(None, (s["first"], ts)))
            s["last"] = max(filter(None, (s["last"], ts)))
            content = (d.get("message") or {}).get("content")
            if d.get("type") == "user" and not d.get("isSidechain") and not d.get("isMeta"):
                texts = [content] if isinstance(content, str) else [
                    c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
                for text in texts:
                    if "[Request interrupted" in text:
                        s["interrupts"] += 1
                    elif text.strip() and not text.lstrip().startswith("<") and d.get("uuid") not in prompt_ids:
                        prompt_ids.add(d.get("uuid"))
                        s["prompts"] += 1
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and c.get("id") not in calls:
                    inp = c.get("input") or {}
                    cmd = inp.get("command") if c.get("name") in ("Bash", "PowerShell") else None
                    calls[c["id"]] = dict(session=sid, name=c.get("name"), cmd=cmd or "", ts=ts)
                    path = str(inp.get("file_path") or "")
                    if c.get("name") == "Write" and path.endswith(SCRIPT_SUFFIX):
                        calls[c["id"]]["script"] = (path.replace("\\", "/").rsplit("/", 1)[-1],
                                                    str(inp.get("content") or ""))
                elif c.get("type") == "tool_result":
                    results[c.get("tool_use_id")] = bool(c.get("is_error"))
                    ended[c.get("tool_use_id")] = ts
                    body = c.get("content")
                    body = body if isinstance(body, str) else json.dumps(body)
                    if c.get("is_error") and HOOK_DENIAL in body:
                        denials.add(c.get("tool_use_id"))   # kept as a flag only, never the text
    calls["__denials__"] = denials
    calls["__ended__"] = ended
    return calls, results, sessions


def hook_denies(hook, repo_root, cmd):
    """Would the repo's deployed guard deny this command? (a synthetic PreToolUse event)"""
    event = dict(hook_event_name="PreToolUse", session_id="baseline-probe", cwd=repo_root, tool_name="Bash",
                 tool_input=dict(command=cmd))
    p = subprocess.run([sys.executable, hook], input=json.dumps(event), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", timeout=60)
    try:
        out = json.loads(p.stdout or "{}")
    except ValueError:
        return False
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


def migrated(calls, repo, hook):
    """Tracked starts/collects and untracked launches of the migrated flows, by the repo's own guard."""
    denials = calls.get("__denials__", set())
    names = MIGRATED.get(repo, ())
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(hook))))
    m = collections.Counter()
    for cid, c in calls.items():
        if cid in ("__denials__", "__ended__") or c.get("outside") or c["name"] not in ("Bash", "PowerShell"):
            continue
        text = c["cmd"]
        if TRACKED_START.search(text):
            m["tracked_starts"] += 1
        if TRACKED_COLLECT.search(text):
            m["tracked_collects"] += 1
        if not any(n in text for n in names) or TRACKED.search(text):
            continue
        if hook_denies(hook, repo_root, text):
            m["untracked_launch_attempts"] += 1
            m["refused_by_hook" if cid in denials else "untracked_launches_ran"] += 1
    out = dict(tracked_starts=m["tracked_starts"], tracked_collects=m["tracked_collects"],
               untracked_launch_attempts=m["untracked_launch_attempts"], refused_by_hook=m["refused_by_hook"],
               untracked_launches_ran=m["untracked_launches_ran"])
    if m["tracked_starts"] + m["untracked_launches_ran"]:
        out["tracked_share"] = round(m["tracked_starts"] / (m["tracked_starts"] + m["untracked_launches_ran"]), 3)
    return out


def measure(calls, results, sessions):
    m = collections.Counter()
    ended = calls.get("__ended__", {})
    calls = {k: v for k, v in calls.items() if k not in ("__denials__", "__ended__")}
    by_session = collections.defaultdict(list)
    for cid, c in calls.items():
        by_session[c["session"]].append((c["ts"], cid, c))
    samples = collections.defaultdict(list)
    launch_sessions = set()
    for sid, items in by_session.items():
        seen = collections.Counter()
        scripts = {}
        for _ts, cid, c in sorted(items, key=lambda x: (x[0], x[1])):
            if c.get("outside"):   # before the window: learn its scripts, count nothing
                if "script" in c:
                    scripts[c["script"][0]] = c["script"][1]
                if c["name"] in ("Bash", "PowerShell"):
                    scripts.update(written_scripts(c["cmd"]))
                continue
            m["tool_calls"] += 1
            if c["name"] in WAIT_TOOLS:
                m["wait_tool_calls"] += 1
            if "script" in c:
                scripts[c["script"][0]] = c["script"][1]
            if c["name"] not in ("Bash", "PowerShell"):
                continue
            m["shell_calls"] += 1
            m["sleep_seconds"] += sleep_seconds(c["cmd"])
            scripts.update(written_scripts(c["cmd"]))
            del REASON[:]
            kind = classify(c["cmd"], scripts)
            if kind is None:
                continue
            m["cluster_calls"] += 1
            m[kind] += 1
            # How long the session sat in this cluster call: `sleep` counts only literal sleeps, but a
            # foreground `timeout 8200 ssh ... bash -s < watcher.sh` blocks the conversation as surely.
            took = seconds_between(c["ts"], ended.get(cid))
            if took is not None:
                m["cluster_call_seconds"] += took
                if took >= 600:
                    m["cluster_calls_over_10min"] += 1
            samples[kind].append((c["cmd"], " <- ".join(REASON)))
            if kind.startswith(("eda", "compute")):
                launch_sessions.add(sid)
                family = kind.split("-")[0]
                if results.get(cid):
                    m[family + "_errors"] += 1
                key = normalize(c["cmd"])
                if seen[key]:
                    m[family + "_repeats"] += 1
                seen[key] += 1
    eda = m["eda-detached"] + m["eda-foreground"]
    compute = m["compute-detached"] + m["compute-foreground"]
    days = sorted({(s["first"] or "")[:10] for s in sessions.values() if s["first"]})
    out = dict(
        sessions=len(sessions), days_active=len(days), first_day=days[0] if days else None,
        last_day=days[-1] if days else None,
        user_prompts=sum(s["prompts"] for s in sessions.values()),
        interrupts=sum(s["interrupts"] for s in sessions.values()),
        tool_calls=m["tool_calls"], shell_calls=m["shell_calls"], cluster_calls=m["cluster_calls"],
        eda_launches=eda, eda_detached=m["eda-detached"], eda_foreground=m["eda-foreground"],
        eda_errors=m["eda_errors"], eda_repeats=m["eda_repeats"],
        compute_runs=compute, compute_detached=m["compute-detached"], compute_errors=m["compute_errors"],
        tracked_calls=m["tracked"], transfers=m["transfer"], kills=m["kill"], cluster_reads=m["read"],
        opaque_scripts=m["opaque"],
        sessions_with_launch=len(launch_sessions),
        sleep_hours=round(m["sleep_seconds"] / 3600.0, 1), wait_tool_calls=m["wait_tool_calls"],
        cluster_call_hours=round(m["cluster_call_seconds"] / 3600.0, 1),
        cluster_calls_over_10min=m["cluster_calls_over_10min"])
    if eda + compute:
        out["reads_per_launch"] = round(m["read"] / (eda + compute), 1)
    if eda:
        out.update(eda_detached_share=round(m["eda-detached"] / eda, 3),
                   eda_repeat_share=round(m["eda_repeats"] / eda, 3),
                   eda_error_share=round(m["eda_errors"] / eda, 3),
                   tracked_share=round(m["tracked"] / (m["tracked"] + eda), 3))
    return out, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="tsmc65, tsmc28, xt011, sky130, ...")
    ap.add_argument("--since", default="0000-00-00")
    ap.add_argument("--until", default="9999-99-99", help="exclusive")
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--json", help="write the counts here")
    ap.add_argument("--sample", help="KIND:N -- print N example commands of KIND to stderr (local only)")
    ap.add_argument("--exclude-session", action="append", default=[], help="a session id to leave out; repeatable")
    ap.add_argument("--hook", help="the repo's deployment/bnl/tracker_hook.py: count the migrated flows by it")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    calls, results, sessions = scan(a.repo, a.since, a.until, a.root, set(a.exclude_session))
    out, samples = measure(calls, results, sessions)
    out.update(repo=a.repo, since=a.since, until=a.until, excluded_sessions=len(a.exclude_session))
    if a.hook:
        out["migrated"] = migrated(calls, a.repo, a.hook)
    print(json.dumps(out, indent=1, sort_keys=True))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
    if a.sample:
        kind, n = a.sample.split(":")
        random.seed(0)
        pool = samples.get(kind, [])
        for cmd, why in random.sample(pool, min(int(n), len(pool))):
            print("---- " + why, file=sys.stderr)
            print(cmd[:500], file=sys.stderr)


if __name__ == "__main__":
    main()
