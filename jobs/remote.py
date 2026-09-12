#!/usr/bin/env python3
"""remote.py -- a cluster transport that cannot be corrupted (Phase 0).

The job-status plan (`docs/job_status_plan.md` L2) diagnoses that every
status check today is an ad-hoc shell string that must survive
PowerShell quoting -> ssh -> tcsh login shell -> /bin/sh -> CRLF/BOM ->
autofs first-access, and that the channel lies often enough that the
observer believes false answers. This module is the single local choke
point that removes every one of those failure modes. It is useful on its
own, before any launcher or sidecar exists: hand it a POSIX sh script and
it returns a trustworthy tri-state answer.

The seven invariants (plan L2), each realized below:

  1. Ship ONE versioned, LF/BOM-normalized reader once (content-hashed;
     re-shipped only when it changes) -- `ensure_reader`.
  2. Never compose a remote shell string. Scripts go on STDIN to
     `/bin/sh -s`; nothing is interpolated into argv -- `run_sh`.
  3. Hard WALL-CLOCK timeout on the ssh (not just ConnectTimeout, which
     covers only TCP connect) -- `_invoke(timeout=...)`.
  4. Parse STDOUT ONLY (the tcsh banner is on stderr); require a
     {"schema":1,...} envelope. Anything else is a transport error.
  5. Tri-state ALWAYS: KNOWN / UNKNOWN / STALE. "I could not reach the
     cluster" never collapses into "the job is gone" -- `Result`.
  6. Retry a first-access ENOENT once before believing absence (autofs
     automount race is real and reproducible) -- `run_sh(retry_enoent)`.
  7. Silence is never success: empty/unparsed/missing => UNKNOWN, never
     PASS -- the default mapping in `_classify`.

Transport selection (plan L2 performance note): on Windows the poll path
runs through WSL ssh with ControlMaster/ControlPersist multiplexing
(Windows OpenSSH cannot multiplex), taking a poll from ~841 ms to tens of
ms; the `ssh.exe` path is kept as the documented fallback for when WSL
egress dies. On Linux/WSL we just use `ssh`.

Pure stdlib, Python 3.6+ (the plan's floor for code running outside the
tool env). No third-party deps.

CLI (smoke test):
  python3 remote.py probe [--host asic6]        # cluster reachability
  python3 remote.py status <jobid> [--host ...]  # one job (Phase 1 fills in)
  python3 remote.py sh --host asic6 < script.sh  # run an arbitrary reader

Usage:
  python3 remote.py <cmd> [arg] [--host] [--mode] [--timeout]
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time

# --- constants ------------------------------------------------------------

SCHEMA = 1
#: remote-home-relative metadata root (plan L3: host-agnostic, outside any
#: flow tree; a deliberate documented exception to the work-location rule).
JOBS_DIRNAME = ".asicjobs"
#: where shipped scripts land, relative to $HOME on the cluster.
BIN_REL = JOBS_DIRNAME + "/bin"
READER_REL = BIN_REL + "/report.sh"
RUNJOB_REL = BIN_REL + "/runjob"
#: local source dir; every file here is shipped verbatim (normalized) as a
#: single content-hashed bundle.
_BIN_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
#: the scripts that make up the shipped bundle.
_BIN_FILES = ("report.sh", "runjob", "progress.py", "jobrec.py", "license.py")
_READER_SRC = os.path.join(_BIN_SRC, "report.sh")

#: tri-state (plan invariant 5). KNOWN carries data; the others carry a
#: reason and MUST NOT be read as a verdict about the job.
KNOWN = "KNOWN"       # reached the cluster, got a valid {"schema":1} envelope
UNKNOWN = "UNKNOWN"   # could not get a trustworthy answer -- NEVER "job gone"
STALE = "STALE"       # got an envelope, but it is flagged stale/mismatched

DEFAULT_HOST = os.environ.get("ASIC_HOST", "asic6")
#: wall-clock ceiling for a single ssh (seconds). Covers auth + NFS stall,
#: not just TCP connect -- CLUSTER.md warns ConnectTimeout does only connect.
DEFAULT_TIMEOUT = 20.0
#: ssh's own connect timeout, kept well under the wall-clock guard.
CONNECT_TIMEOUT = 10


# --- result type ----------------------------------------------------------

class Result:
    """The tri-state answer. `status` is KNOWN/UNKNOWN/STALE; only KNOWN
    (and STALE, which is KNOWN-but-old) carry `data`. `reason` explains a
    non-KNOWN result in a loggable token. Raw streams and rc are kept for
    diagnosis (`jobs why`)."""

    __slots__ = ("status", "data", "reason", "host", "stdout", "stderr",
                 "rc", "elapsed_s", "attempts")

    def __init__(self, status, host, data=None, reason=None, stdout="",
                 stderr="", rc=None, elapsed_s=0.0, attempts=1):
        self.status = status
        self.data = data
        self.reason = reason
        self.host = host
        self.stdout = stdout
        self.stderr = stderr
        self.rc = rc
        self.elapsed_s = elapsed_s
        self.attempts = attempts

    @property
    def ok(self):
        """True only for a reached, valid, fresh answer. Silence, transport
        failure, and staleness are all falsey -- invariant 7."""
        return self.status == KNOWN

    def __repr__(self):
        tail = "" if self.ok else " reason=%r" % self.reason
        return "<Result %s host=%s%s>" % (self.status, self.host, tail)

    def to_dict(self):
        return {
            "status": self.status, "host": self.host, "data": self.data,
            "reason": self.reason, "rc": self.rc,
            "elapsed_s": round(self.elapsed_s, 3), "attempts": self.attempts,
        }


# --- normalization (invariants 1 & the BOM/CRLF class) --------------------

_BOM = b"\xef\xbb\xbf"


def normalize_script(text):
    """Return bytes safe to pipe to a remote /bin/sh: UTF-8, no BOM, LF
    line endings, exactly one trailing newline. This single function
    defuses the entire CRLF/BOM failure class the plan tabulates (``$'\\r':
    command not found``, ``<U+FEFF>echo: command not found``) -- every byte
    that crosses the wire passes through here, so it is enforced, not left
    to discipline (GETTING_STARTED.md / CLUSTER.md:45)."""
    if isinstance(text, str):
        data = text.encode("utf-8")
    else:
        data = bytes(text)
    if data.startswith(_BOM):
        data = data[len(_BOM):]
    # normalize CRLF and lone CR to LF
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not data.endswith(b"\n"):
        data += b"\n"
    return data


def reader_bytes():
    """Normalized bytes of the local report.sh -- the exact payload shipped
    to the cluster."""
    with open(_READER_SRC, "rb") as fh:
        return normalize_script(fh.read())


def reader_hash():
    """Short content hash of report.sh alone."""
    return hashlib.sha256(reader_bytes()).hexdigest()[:12]


def bin_files():
    """name -> normalized bytes for every script in the shipped bundle."""
    out = {}
    for name in _BIN_FILES:
        with open(os.path.join(_BIN_SRC, name), "rb") as fh:
            out[name] = normalize_script(fh.read())
    return out


def bundle_manifest():
    """Short content hash identifying the WHOLE bundle version. Drives
    ship-once: the cluster stores this in `bin/.manifest` and we re-ship
    only on a mismatch."""
    files = bin_files()
    lines = "".join("%s:%s\n" % (n, hashlib.sha256(files[n]).hexdigest())
                    for n in sorted(files))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()[:12]


# --- transport ------------------------------------------------------------

class Transport:
    """Runs POSIX sh on a cluster host with every plan invariant enforced.

    `runner` is injectable purely for testing: it takes (argv, input_bytes,
    timeout) and returns (rc, stdout_bytes, stderr_bytes) or raises
    TimeoutError. The default runs a real subprocess.
    """

    def __init__(self, host=DEFAULT_HOST, mode=None, timeout=DEFAULT_TIMEOUT,
                 runner=None):
        self.host = host
        self.mode = mode or self._default_mode()
        self.timeout = timeout
        self._runner = runner or self._subprocess_runner
        #: (host -> reader hash) we have already ensured this process, so a
        #: tight poll loop does not re-check the reader every call.
        self._ensured = {}

    # -- ssh argv construction (mode-specific) -----------------------------

    @staticmethod
    def _default_mode():
        """`ASICJOBS_RSH` overrides; otherwise WSL-multiplexed ssh on
        Windows (Windows OpenSSH cannot ControlMaster), plain ssh elsewhere.
        Values: 'wsl' | 'winssh' | 'ssh'."""
        env = os.environ.get("ASICJOBS_RSH")
        if env:
            return env
        return "wsl" if os.name == "nt" else "ssh"

    def _mux_opts(self):
        """ControlMaster multiplexing opts -- only for ssh binaries that
        support it (WSL/native OpenSSH, NOT Windows ssh.exe). Turns a
        ~841 ms handshake into tens of ms, which is what makes a 5 s refresh
        affordable (plan L2)."""
        return [
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=60s",
            # %C = hash of (host,port,user) -- one socket per target, under
            # the WSL/native ~/.ssh which is guaranteed writable.
            "-o", "ControlPath=~/.ssh/asicjobs-cm-%C",
        ]

    def _base_ssh(self):
        """The ssh argv up to (but not including) the host and remote
        command. Always BatchMode (never block on a prompt) with a real
        ConnectTimeout under the wall-clock guard."""
        common = ["-o", "BatchMode=yes",
                  "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
                  "-o", "StrictHostKeyChecking=accept-new"]
        if self.mode == "wsl":
            return ["wsl", "ssh"] + self._mux_opts() + common
        if self.mode == "winssh":
            # Windows OpenSSH: no multiplexing available. Documented
            # fallback for when WSL egress dies (sync.local's SYNC_RSH=ssh.exe).
            return ["ssh.exe"] + common
        # native ssh (Linux / inside WSL) -- multiplex too.
        return ["ssh"] + self._mux_opts() + common

    def _remote_sh_argv(self):
        """Full argv to run `/bin/sh -s` on the host, reading its script
        from OUR stdin. This is the ONLY way a script reaches the cluster --
        never interpolated into a command string (invariant 2), so tcsh
        quoting, `Ambiguous output redirect`, and BOM classes never arise."""
        return self._base_ssh() + [self.host, "/bin/sh", "-s"]

    # -- the low-level invoke ---------------------------------------------

    def _subprocess_runner(self, argv, input_bytes, timeout):
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()  # reap; drain pipes
            raise TimeoutError("wall-clock timeout after %.1fs" % timeout)
        return proc.returncode, out, err

    def _invoke(self, script, timeout):
        """Pipe `script` to `/bin/sh -s` on the host under a hard wall-clock
        timeout. This is the SINGLE choke point every byte crosses, so
        normalization (invariant 1: LF, no BOM, bytes) happens here and
        nowhere else -- no caller can forget it. Returns (rc, stdout_text,
        stderr_text); rc is None on timeout."""
        script_bytes = normalize_script(script)
        argv = self._remote_sh_argv()
        try:
            rc, out, err = self._runner(argv, script_bytes, timeout)
        except TimeoutError as exc:
            return None, "", str(exc)
        except FileNotFoundError as exc:
            # ssh / wsl binary missing -- a transport error, not "job gone".
            return None, "", "transport binary missing: %s" % exc
        return rc, _decode(out), _decode(err)

    # -- bundle shipping (invariant 1) ------------------------------------

    def ensure_bin(self, timeout=None):
        """Ship the script bundle (report.sh + runjob) to the host iff its
        manifest hash differs from what is installed. Idempotent and atomic
        on the cluster side (temp + `mv -f` per file). Cached per process.
        Returns a Result (KNOWN on success)."""
        want = bundle_manifest()
        if self._ensured.get(self.host) == want:
            return Result(KNOWN, self.host, data={"bundle": want,
                                                  "action": "cached"})
        installer = _bundle_installer(bin_files(), want)
        rc, out, err = self._invoke(installer, timeout or self.timeout)
        if rc == 0 and ("OK " + want in out or "SHIPPED " + want in out):
            self._ensured[self.host] = want
            action = "shipped" if "SHIPPED" in out else "present"
            return Result(KNOWN, self.host, data={"bundle": want,
                                                  "action": action},
                          stdout=out, stderr=err, rc=rc)
        return Result(UNKNOWN, self.host,
                      reason="bundle install failed (rc=%s)" % rc,
                      stdout=out, stderr=err, rc=rc)

    #: back-compat alias -- the reader is one file of the bundle now.
    ensure_reader = ensure_bin

    # -- the public query paths -------------------------------------------

    def run_sh(self, script, timeout=None, retry_enoent=True):
        """Run an arbitrary POSIX sh `script` on the host and classify the
        result. This is Phase 0's whole value: it makes ANY existing ad-hoc
        query reliable without touching a launcher -- normalized on the way
        out, parsed stdout-only, mapped to tri-state on the way back.

        `script` may emit a {"schema":1,...} envelope on stdout (=> KNOWN
        with .data) or arbitrary text (=> UNKNOWN: we refuse to guess).
        """
        timeout = timeout or self.timeout
        start = time.time()
        rc, out, err = self._invoke(script, timeout)
        attempts = 1
        # invariant 6: the autofs first-access race yields a transient ENOENT
        # on a path that exists on the very next touch. Retry once before
        # trusting an absence signal.
        if retry_enoent and _looks_like_enoent(rc, out, err):
            attempts = 2
            rc, out, err = self._invoke(script, timeout)
        res = _classify(self.host, rc, out, err)
        res.elapsed_s = time.time() - start
        res.attempts = attempts
        return res

    def read(self, subcommand="probe", *args, **kw):
        """Run the shipped reader with a subcommand. Ships it first if
        needed (invariant 1), then invokes it by piping a one-line launcher
        on stdin -- so even the reader call carries no interpolated argv
        string. Args are validated against the reader's closed charset."""
        timeout = kw.pop("timeout", None)
        ens = self.ensure_reader(timeout=timeout)
        if not ens.ok:
            return ens  # could not ship the reader -> UNKNOWN, not a verdict
        for a in args:
            if not _SAFE_ARG(a):
                return Result(UNKNOWN, self.host,
                              reason="unsafe reader arg: %r" % a)
        launcher = _reader_launcher(subcommand, args)
        return self.run_sh(launcher, timeout=timeout, **kw)

    # convenience wrappers -------------------------------------------------

    def probe(self, **kw):
        """Cluster reachability + $JOBS health. The Phase-0 smoke test."""
        return self.read("probe", **kw)

    def status(self, jobid, **kw):
        """One job's published status.json envelope."""
        return self.read("status", jobid, **kw)

    def list(self, **kw):
        """One-line summary of every job under $JOBS."""
        return self.read("list", **kw)

    def events(self, n=50, **kw):
        """Last n terminal events -- the ONE read that answers 'did anything
        finish?' across all jobs/hosts."""
        return self.read("events", str(int(n)), **kw)

    def why(self, jobid, **kw):
        """Diagnosis bundle: status + result + live ps state."""
        return self.read("why", jobid, **kw)

    def verify(self, jobid, **kw):
        """Re-check the jobid-stamped artifact hashes (Class-D STALE guard)."""
        return self.read("verify", jobid, **kw)

    def run(self, cmd, flow="job", target="run", interval=5, expect=None,
            progress=None, total=None, progress_log=None, timeout=None):
        """Launch `cmd` (a list of argv tokens) as a detached, self-reporting
        job via `runjob`, and return its launch envelope (KNOWN with
        .data['jobid']). The command crosses the wire base64-encoded per
        argv element -- the base64 alphabet is shell-safe unquoted, so an
        ARBITRARY command (spaces, `+aps`, redirections-as-args) survives
        with zero quoting, upholding invariant 2 even for launches.

        `progress` (spectre|innovus|calibre|cocotb) + optional `total` and
        `progress_log` turn on live rate/ETA in status.json (Phase 2)."""
        if not cmd:
            return Result(UNKNOWN, self.host, reason="empty command")
        if not (_SAFE_ARG(flow) and _SAFE_ARG(target)):
            return Result(UNKNOWN, self.host,
                          reason="unsafe flow/target: %r/%r" % (flow, target))
        expect = expect or []
        for p in expect:
            if not _SAFE_PATH(p):
                return Result(UNKNOWN, self.host,
                              reason="unsafe expect path: %r" % p)
        if progress is not None and not _SAFE_ARG(progress):
            return Result(UNKNOWN, self.host,
                          reason="unsafe progress tool: %r" % progress)
        if progress_log is not None and not _SAFE_PATH(progress_log):
            return Result(UNKNOWN, self.host,
                          reason="unsafe progress-log: %r" % progress_log)
        ens = self.ensure_bin(timeout=timeout)
        if not ens.ok:
            return ens
        toks = [base64.b64encode(str(a).encode("utf-8")).decode("ascii")
                for a in cmd]
        for t in toks:
            if not all(c.isalnum() or c in "+/=" for c in t):
                return Result(UNKNOWN, self.host, reason="bad base64 token")
        opts = ["--flow", flow, "--target", target,
                "--interval", str(int(interval))]
        if progress:
            opts += ["--progress", progress]
        if total is not None:
            opts += ["--total", str(int(total))]
        if progress_log:
            opts += ["--progress-log", progress_log]
        for p in expect:
            opts += ["--expect", p]
        launcher = ('exec /bin/sh "$HOME/%s" %s --cmd64 %s\n') % (
            RUNJOB_REL, " ".join(opts), " ".join(toks))
        return self.run_sh(launcher, timeout=timeout, retry_enoent=False)


# --- classification (invariants 4, 5, 7) ----------------------------------

def _classify(host, rc, out, err):
    """Map (rc, stdout, stderr) to a tri-state Result. STDOUT ONLY is parsed
    for the envelope; stderr is diagnostic (the tcsh banner lives there).
    The default for anything unrecognized is UNKNOWN -- silence is never
    success (invariant 7)."""
    base = dict(host=host, stdout=out, stderr=err, rc=rc)
    if rc is None:
        return Result(UNKNOWN, reason="transport timeout/failure: %s"
                      % (err.strip() or "no output"), **base)
    env = _find_envelope(out)
    if env is None:
        if rc != 0:
            return Result(UNKNOWN, reason="ssh rc=%s, no envelope" % rc,
                          **base)
        return Result(UNKNOWN, reason="no {\"schema\":%d} envelope on stdout"
                      % SCHEMA, **base)
    # a valid envelope, but the transport still refuses to call a broken
    # answer good: staleness is surfaced as its own state.
    if env.get("stale") is True or env.get("state") == STALE:
        return Result(STALE, data=env,
                      reason="envelope reports stale artifact", **base)
    return Result(KNOWN, data=env, **base)


def _find_envelope(stdout):
    """First line of stdout that parses as a JSON object with our schema.
    Tolerant of a stray leading line (defensive -- the banner is on stderr,
    but a misconfigured host could leak one). Returns the dict or None."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and obj.get("schema") == SCHEMA:
            return obj
    return None


def _looks_like_enoent(rc, out, err):
    """Heuristic for the autofs first-access miss: a nonzero rc with a
    'No such file or directory' on stderr and no valid envelope yet. We do
    NOT retry a clean rc=0 (that answer is trustworthy) nor a timeout (that
    is a different failure that a retry would just double)."""
    if rc in (0, None):
        return False
    if _find_envelope(out) is not None:
        return False
    blob = err.lower()
    return ("no such file or directory" in blob
            or "not a directory" in blob)


# --- helpers --------------------------------------------------------------

def _decode(b):
    if isinstance(b, str):
        return b
    return (b or b"").decode("utf-8", "replace")


def _SAFE_ARG(a):
    """The reader's closed argument charset (jobids, subcommands, small
    ints). Anything else is rejected before it can reach a remote sh."""
    a = str(a)
    return bool(a) and all(
        c.isalnum() or c in "._-" for c in a)


def _SAFE_PATH(p):
    """A relative artifact path safe to drop into a runjob launcher
    unquoted: the arg charset plus '/'. No spaces, no shell metacharacters,
    no leading slash (artifacts are relative to the job cwd)."""
    p = str(p)
    return (bool(p) and not p.startswith("/")
            and all(c.isalnum() or c in "._-/" for c in p))


def _reader_launcher(subcommand, args):
    """A one-line sh script that execs the installed reader with the given
    args. All tokens are pre-validated by `_SAFE_ARG`, so no quoting is
    needed and none is done -- the launcher itself is what we pipe on stdin,
    keeping invariant 2 intact even for reader calls."""
    parts = [subcommand] + [str(a) for a in args]
    # $HOME expands in the remote /bin/sh, not in any login shell.
    return 'exec /bin/sh "$HOME/%s" %s\n' % (READER_REL, " ".join(parts))


def _bundle_installer(files, manifest_hash):
    """Build the sh installer we pipe on stdin: it checks the stored
    manifest hash and no-ops if current, else writes every bundle file
    atomically (temp + `mv -f`, atomic on NFS -- plan supporting-fact 2)
    and records the manifest. Each file is embedded in its OWN quoted
    heredoc, so content is preserved literally with zero interpolation and
    the delimiter (indexed + hashed) cannot occur in POSIX sh source."""
    parts = [
        "#!/bin/sh",
        "set -eu",
        'DIR="$HOME/%s"' % BIN_REL,
        'MAN="$DIR/.manifest"',
        'want="%s"' % manifest_hash,
        'if [ -f "$MAN" ] && [ "$(cat "$MAN" 2>/dev/null)" = "$want" ]; '
        'then echo "OK $want"; exit 0; fi',
        'mkdir -p "$DIR"',
    ]
    for i, name in enumerate(sorted(files)):
        body = files[name].decode("utf-8")
        if not body.endswith("\n"):
            body += "\n"
        delim = "EOF_%d_%s" % (i, manifest_hash)
        assert delim not in body
        parts += [
            'tmp="$DIR/%s.tmp.$$"' % name,
            "cat > \"$tmp\" <<'%s'" % delim,
            body.rstrip("\n"),
            delim,
            'mv -f "$tmp" "$DIR/%s"' % name,
        ]
    parts += [
        'printf %s "$want" > "$MAN.tmp.$$" && mv -f "$MAN.tmp.$$" "$MAN"',
        'echo "SHIPPED $want"',
    ]
    return "\n".join(parts) + "\n"


# --- CLI ------------------------------------------------------------------

def _main(argv=None):
    p = argparse.ArgumentParser(description="cluster job-status transport "
                                            "(Phase 0 smoke test)")
    p.add_argument("cmd", choices=["probe", "list", "status", "sh", "ship"],
                   help="probe: cluster health; list: all jobs; "
                        "status <id>: one job; sh: run script from stdin; "
                        "ship: install the script bundle")
    p.add_argument("arg", nargs="?", help="jobid for `status`")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--mode", default=None,
                   help="wsl | winssh | ssh (default: auto)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ns = p.parse_args(argv)

    t = Transport(host=ns.host, mode=ns.mode, timeout=ns.timeout)
    if ns.cmd == "probe":
        res = t.probe()
    elif ns.cmd == "list":
        res = t.list()
    elif ns.cmd == "status":
        if not ns.arg:
            p.error("status needs a jobid")
        res = t.status(ns.arg)
    elif ns.cmd == "ship":
        res = t.ensure_bin()
    else:  # sh
        res = t.run_sh(sys.stdin.read())

    print(json.dumps(res.to_dict(), indent=2))
    # exit code mirrors the tri-state so shell callers can branch: 0 KNOWN,
    # 3 STALE, 4 UNKNOWN -- and crucially never 0 on a non-answer.
    return {KNOWN: 0, STALE: 3, UNKNOWN: 4}[res.status]


if __name__ == "__main__":
    sys.exit(_main())
