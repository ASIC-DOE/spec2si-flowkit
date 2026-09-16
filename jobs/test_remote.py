#!/usr/bin/env python3
"""Tests for the Phase-0 transport (remote.py).

No cluster is needed: the SSH-dependent parts run through an injected
`runner` that executes the piped script on the LOCAL /bin/sh, so the
normalize -> pipe -> reader -> parse -> classify path is exercised
end-to-end for real (including report.sh's own POSIX-sh logic and the
atomic ship-once installer with its quoted heredoc). The pure functions
(normalization, envelope parse, tri-state mapping, argv construction) are
unit-tested directly.

Run:  python3 test_remote.py       # from deployment/bnl/jobs/
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote  # noqa: E402


# --- a local /bin/sh "transport" -----------------------------------------

def _find_sh():
    for cand in ("/bin/sh", shutil.which("sh"), shutil.which("bash")):
        if cand and os.path.exists(cand):
            return cand
    return None


SH = _find_sh()


def local_runner(home, cwd=None):
    """A runner that ignores the ssh prefix and runs the piped script on the
    local /bin/sh with HOME pinned to `home` -- so $HOME/.asicjobs resolves
    into a throwaway dir. Mirrors the real contract: script on stdin to a
    posix sh, stdout+stderr+rc back. `cwd` sets the job's working directory
    (where a launched command runs and where its relative artifacts land)."""
    env = dict(os.environ)
    env["HOME"] = home
    env.pop("ASICJOBS_DIR", None)

    def run(argv, input_bytes, timeout):
        # the real argv ends in ['/bin/sh', '-s']; locally we just run sh -s.
        proc = subprocess.Popen([SH, "-s"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, cwd=cwd)
        out, err = proc.communicate(input_bytes, timeout=timeout)
        return proc.returncode, out, err
    return run


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _poll_until(t, jobid, states, tries=60, delay=0.25):
    """Poll t.status(jobid) until its state is in `states` (or give up).
    Returns the final status dict, or None."""
    for _ in range(tries):
        r = t.status(jobid)
        if r.ok and (r.data or {}).get("state") in states:
            return r.data
        time.sleep(delay)
    return r.data if r.ok else None


# --- test registry --------------------------------------------------------

_PASS = 0
_FAIL = 0


def check(cond, msg):
    """Assert, loudly, in BOTH runners.

    This used to count a failure and print it, then RETURN. Under
    `python3 <file>.py` that produced an honest tally -- but the project runs
    pytest, where a test function returning None has PASSED. Every check in
    this file was therefore green by construction, and a deliberately reverted
    bug in cli.py was caught by neither the suite nor its own negative control
    (2026-08-01). Raising costs the script runner nothing: `main` below now
    catches per test and still prints the tally.
    """
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        return
    _FAIL += 1
    raise AssertionError(msg)


# --- pure-unit tests ------------------------------------------------------

def test_normalize():
    # BOM stripped, CRLF + lone CR -> LF, single trailing newline added
    got = remote.normalize_script(b"\xef\xbb\xbfecho hi\r\ndone\rx")
    check(got == b"echo hi\ndone\nx\n", "normalize BOM/CRLF/CR: %r" % got)
    check(remote.normalize_script("a\n") == b"a\n", "already-clean str")
    check(remote.normalize_script("no newline") == b"no newline\n",
          "trailing newline added")


def test_envelope_and_classify():
    good = '{"schema":1,"kind":"probe","host":"asic6","njobs":2}'
    # KNOWN
    r = remote._classify("asic6", 0, good + "\n", "banner on stderr\n")
    check(r.status == remote.KNOWN and r.ok, "valid envelope -> KNOWN")
    check(r.data["njobs"] == 2, "envelope data parsed")
    # stderr banner must NOT be parsed as an answer
    r = remote._classify("asic6", 0, "NOTICE TO USERS\n", good + "\n")
    check(r.status == remote.UNKNOWN,
          "envelope only on stderr -> UNKNOWN (stdout-only parse)")
    # wrong schema is not our envelope
    r = remote._classify("asic6", 0, '{"schema":9,"x":1}\n', "")
    check(r.status == remote.UNKNOWN, "wrong schema -> UNKNOWN")
    # empty / silence is never success
    r = remote._classify("asic6", 0, "", "")
    check(r.status == remote.UNKNOWN, "silence -> UNKNOWN, never PASS")
    # transport failure (rc None) is UNKNOWN, never 'gone'
    r = remote._classify("asic6", None, "", "wall-clock timeout")
    check(r.status == remote.UNKNOWN and not r.ok, "timeout -> UNKNOWN")
    # STALE surfaced from the envelope
    r = remote._classify("asic6", 0, '{"schema":1,"stale":true}\n', "")
    check(r.status == remote.STALE, "stale flag -> STALE")
    r = remote._classify("asic6", 0, '{"schema":1,"state":"STALE"}\n', "")
    check(r.status == remote.STALE, "state=STALE -> STALE")


def test_enoent_heuristic():
    check(remote._looks_like_enoent(1, "", "cd: No such file or directory"),
          "ENOENT on stderr with rc!=0 -> retry")
    check(not remote._looks_like_enoent(0, "", "No such file or directory"),
          "rc=0 is trustworthy -> no retry")
    check(not remote._looks_like_enoent(None, "", "timeout"),
          "timeout -> no ENOENT retry (would just double)")
    check(not remote._looks_like_enoent(
        1, '{"schema":1,"kind":"probe"}', "No such file or directory"),
        "valid envelope present -> no retry despite stderr noise")


def test_argv_modes():
    t = remote.Transport(host="asic7", mode="wsl")
    argv = t._remote_sh_argv()
    check(argv[:2] == ["wsl", "ssh"], "wsl mode -> `wsl ssh`")
    check("ControlMaster=auto" in argv, "wsl mode multiplexes")
    check(argv[-3:] == ["asic7", "/bin/sh", "-s"], "runs /bin/sh -s on host")

    t = remote.Transport(host="asic7", mode="winssh")
    argv = t._remote_sh_argv()
    check(argv[0] == "ssh.exe", "winssh mode -> ssh.exe")
    check("ControlMaster=auto" not in argv,
          "winssh does NOT multiplex (Windows OpenSSH cannot)")

    t = remote.Transport(host="asic7", mode="ssh")
    argv = t._remote_sh_argv()
    check(argv[0] == "ssh" and "ControlMaster=auto" in argv,
          "native ssh multiplexes")


def test_safe_arg():
    check(remote._SAFE_ARG("spectre-adc-20260720-ab12"), "jobid accepted")
    check(remote._SAFE_ARG("probe"), "subcommand accepted")
    check(not remote._SAFE_ARG("a b"), "space rejected")
    check(not remote._SAFE_ARG("$(rm -rf /)"), "injection rejected")
    check(not remote._SAFE_ARG(""), "empty rejected")


def test_installer_heredoc_safe():
    man = remote.bundle_manifest()
    inst = remote._bundle_installer(remote.bin_files(), man)
    check("<<'EOF_0_" in inst, "files embedded in QUOTED heredocs")
    check(man in inst, "manifest hash baked into installer")
    check("mv -f" in inst, "atomic install via temp + mv -f")
    check('$DIR/runjob"' in inst and '$DIR/report.sh"' in inst,
          "both bundle files installed")


def test_safe_path():
    check(remote._SAFE_PATH("enob.log"), "relative artifact path ok")
    check(remote._SAFE_PATH("psf_enob/tranAns.tran"), "subdir path ok")
    check(not remote._SAFE_PATH("/abs/path"), "absolute path rejected")
    check(not remote._SAFE_PATH("a b.log"), "space rejected")
    check(not remote._SAFE_PATH("$(x)"), "injection rejected")


# --- end-to-end through a real local /bin/sh ------------------------------

def test_run_sh_end_to_end():
    if not SH:
        print("  SKIP: no local /bin/sh available")
        return
    home = tempfile.mkdtemp(prefix="asicjobs-test-")
    try:
        t = remote.Transport(host="local", mode="ssh",
                             runner=local_runner(home))
        # arbitrary script that emits a valid envelope -> KNOWN
        r = t.run_sh('printf \'{"schema":1,"kind":"x","v":7}\\n\'')
        check(r.ok and r.data.get("v") == 7,
              "run_sh envelope -> KNOWN (%r)" % r)
        # arbitrary junk -> UNKNOWN, never a false pass
        r = t.run_sh('echo just some noise')
        check(r.status == remote.UNKNOWN, "run_sh junk -> UNKNOWN")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_ship_and_probe_end_to_end():
    if not SH:
        print("  SKIP: no local /bin/sh available")
        return
    home = tempfile.mkdtemp(prefix="asicjobs-test-")
    try:
        t = remote.Transport(host="local", mode="ssh",
                             runner=local_runner(home))
        # first ship installs the reader (SHIPPED), fresh process cache
        r = t.ensure_reader()
        check(r.ok, "ensure_reader first call -> KNOWN (%r: %s)"
              % (r, r.stdout.strip()))
        installed = os.path.join(home, "..", "")  # noqa: F841
        reader_path = os.path.join(home, ".asicjobs", "bin", "report.sh")
        check(os.path.isfile(reader_path), "reader written to $HOME/.asicjobs")
        check(r.data["action"] == "shipped", "first call reports shipped")
        # second call (bypassing the per-process cache) must find it current
        t2 = remote.Transport(host="local", mode="ssh",
                             runner=local_runner(home))
        r2 = t2.ensure_reader()
        check(r2.ok and r2.data["action"] == "present",
              "re-ship no-ops on matching hash (%r)" % r2)

        # now drive the installed reader's `probe` subcommand for real,
        # with a couple of fake job dirs so njobs is exercised.
        jobs = os.path.join(home, ".asicjobs")
        for jid in ("job-a", "job-b"):
            d = os.path.join(jobs, jid)
            os.makedirs(d)
            with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
        os.makedirs(os.path.join(jobs, "not-a-job"))  # no meta.json
        r = t.probe()
        check(r.ok, "probe -> KNOWN (%r: %s)" % (r, r.stderr.strip()))
        if r.ok:
            check(r.data["kind"] == "probe", "probe envelope kind")
            check(r.data["jobs_dir_ok"] is True, "jobs_dir_ok true")
            check(r.data["njobs"] == 2,
                  "njobs counts meta.json dirs only (got %s)"
                  % r.data.get("njobs"))
            check("host" in r.data and "epoch" in r.data,
                  "probe carries host+epoch")

        # status of an absent job = a KNOWN 'NOTFOUND', NOT a transport miss
        r = t.status("no-such-job")
        check(r.ok and r.data.get("state") == "NOTFOUND",
              "absent job -> KNOWN NOTFOUND (%r)" % r)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_reader_is_lf_no_bom():
    for name, data in remote.bin_files().items():
        check(not data.startswith(b"\xef\xbb\xbf"),
              "shipped %s has no BOM" % name)
        check(b"\r" not in data, "shipped %s is LF-only" % name)


# --- Phase 1: runjob lifecycle through a real local /bin/sh ---------------

def test_runjob_done_lifecycle():
    if not SH:
        print("  SKIP: no local /bin/sh available")
        return
    home = tempfile.mkdtemp(prefix="asicjobs-test-")
    work = tempfile.mkdtemp(prefix="asicjobs-work-")
    try:
        t = remote.Transport(host="local", mode="ssh",
                             runner=local_runner(home, cwd=work))
        # a short job that writes an expected artifact, then succeeds.
        r = t.run(["sh", "-c", "echo hello > out.txt; sleep 1"],
                  flow="test", target="done", interval=1, expect=["out.txt"])
        check(r.ok and r.data.get("kind") == "launched",
              "run -> launched envelope (%r: %s)" % (r, r.stderr.strip()))
        jobid = (r.data or {}).get("jobid")
        check(bool(jobid), "launch returned a jobid")
        if not jobid:
            return
        check(jobid.startswith("test-done-"), "jobid encodes flow-target")

        # meta.json exists immediately (before first heartbeat)
        meta_p = os.path.join(home, ".asicjobs", jobid, "meta.json")
        check(os.path.isfile(meta_p), "meta.json written at launch")
        meta = _read_json(meta_p)
        check(meta["cmd"] == ["sh", "-c", "echo hello > out.txt; sleep 1"],
              "meta.cmd preserves arbitrary argv exactly (%r)" % meta["cmd"])
        check(meta["expect"] == ["out.txt"], "meta records expected artifact")

        # the sidecar drives it to a terminal 'done'
        final = _poll_until(t, jobid, {"done", "failed", "killed"})
        check(final is not None, "job reached a terminal state")
        check(final and final.get("state") == "done",
              "clean exit -> state=done (%r)" % (final or {}).get("state"))

        # result.json: rc 0 and the artifact recorded as existing
        res_p = os.path.join(home, ".asicjobs", jobid, "result.json")
        check(os.path.isfile(res_p), "result.json written on exit")
        res = _read_json(res_p)
        check(res["rc"] == 0 and res["state"] == "done", "result rc/state")
        arts = {a["path"]: a for a in res.get("artifacts", [])}
        check(arts.get("out.txt", {}).get("exists") is True,
              "expected artifact detected as present")

        # unique stdout.log captured the command output
        log_p = os.path.join(home, ".asicjobs", jobid, "stdout.log")
        check(os.path.isfile(log_p), "per-job stdout.log exists")

        # events.jsonl got exactly one terminal line
        ev_p = os.path.join(home, ".asicjobs", "events.jsonl")
        check(os.path.isfile(ev_p), "events.jsonl appended")
        if os.path.isfile(ev_p):
            evs = [json.loads(x) for x in open(ev_p, encoding="utf-8", errors="replace") if x.strip()]
            mine = [e for e in evs if e.get("jobid") == jobid]
            check(len(mine) == 1 and mine[0]["state"] == "done",
                  "one terminal event, state=done")

        # `jobs ls` (report.sh list) sees the finished job authoritatively
        lst = t.list()
        check(lst.ok, "list -> KNOWN")
        rows = {j["jobid"]: j for j in (lst.data or {}).get("jobs", [])}
        check(jobid in rows, "listed job present")
        check(rows.get(jobid, {}).get("state") == "done",
              "list reports terminal state from result.json")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def test_runjob_live_progress():
    """The sidecar must publish live progress via the shared progress.py
    (Phase 2). A job appends `VA NORMAL conv=N` lines to a log; status.json
    should carry a progress object with done/total/frac and a measured
    rate."""
    if not SH:
        print("  SKIP: no local /bin/sh available")
        return
    if not shutil.which("python3") and not shutil.which("python"):
        print("  SKIP: no python3 for the sidecar to call")
        return
    home = tempfile.mkdtemp(prefix="asicjobs-test-")
    work = tempfile.mkdtemp(prefix="asicjobs-work-")
    try:
        t = remote.Transport(host="local", mode="ssh",
                             runner=local_runner(home, cwd=work))
        # emit 5 conversions over ~2.5s into prog.log
        gen = ('for i in 1 2 3 4 5; do '
               'echo "VA NORMAL conv=$i code_q=1 t=1" >> prog.log; '
               'sleep 0.5; done')
        r = t.run(["sh", "-c", gen], flow="test", target="prog",
                  interval=1, progress="spectre", total=5,
                  progress_log="prog.log")
        jobid = (r.data or {}).get("jobid")
        check(bool(jobid), "progress job launched (%r)" % r)
        if not jobid:
            return
        # meta should record the progress config
        meta = _read_json(os.path.join(home, ".asicjobs", jobid, "meta.json"))
        check(meta.get("ptool") == "spectre" and meta.get("ptotal") == 5
              and meta.get("plog") == "prog.log", "meta records progress cfg")

        # collect progress objects across the run
        seen_prog = []
        for _ in range(60):
            s = t.status(jobid)
            if s.ok:
                p = (s.data or {}).get("progress")
                if isinstance(p, dict):
                    seen_prog.append(p)
                if (s.data or {}).get("state") in {"done", "failed", "killed"}:
                    break
            time.sleep(0.25)
        check(any(p.get("done", 0) >= 1 and p.get("total") == 5
                  for p in seen_prog),
              "live progress published with done>=1, total=5")
        check(any("rate_per_s" in p for p in seen_prog),
              "measured rate present in live progress")
        # final result is done, and the final status frac reached 1.0
        res = _read_json(os.path.join(home, ".asicjobs", jobid, "result.json"))
        check(res["state"] == "done", "progress job finished done")
        fs = _read_json(os.path.join(home, ".asicjobs", jobid, "status.json"))
        fp = fs.get("progress") or {}
        check(fp.get("done") == 5 and fp.get("frac") == 1.0,
              "final progress = 5/5 (100%%) (%r)" % fp)
        check("pstat" in fs, "status carries child pstat for hung-vs-slow")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def test_phase3_events_why_verify():
    """Phase 3: events lists terminal finishes; why bundles status+result+ps;
    verify catches a STALE artifact (Class-D)."""
    if not SH:
        print("  SKIP: no local /bin/sh available")
        return
    home = tempfile.mkdtemp(prefix="asicjobs-test-")
    work = tempfile.mkdtemp(prefix="asicjobs-work-")
    try:
        t = remote.Transport(host="local", mode="ssh",
                             runner=local_runner(home, cwd=work))
        r = t.run(["sh", "-c", "echo v1 > art.txt"], flow="test",
                  target="p3", interval=1, expect=["art.txt"])
        jobid = (r.data or {}).get("jobid")
        check(bool(jobid), "phase3 job launched")
        if not jobid:
            return
        _poll_until(t, jobid, {"done", "failed", "killed"})

        # events: the terminal finish shows up in the shared bus
        ev = t.events(50)
        check(ev.ok, "events -> KNOWN")
        mine = [e for e in (ev.data or {}).get("events", [])
                if e.get("jobid") == jobid]
        check(len(mine) == 1 and mine[0]["state"] == "done",
              "events lists the terminal 'done' for the job")

        # why: bundle carries result + not-alive ps
        why = t.why(jobid)
        check(why.ok and (why.data or {}).get("result", {}).get("state")
              == "done", "why bundles result=done")
        check((why.data or {}).get("ps", {}).get("alive") is False,
              "why reports the finished pid as not alive")

        # verify: fresh artifact matches its stamp
        v = t.verify(jobid)
        check(v.ok and v.data.get("verdict") == "OK",
              "verify OK on the unmodified artifact (%r)" % (v.data,))
        check(v.data.get("checked") == 1, "one artifact checked")

        # tamper with the artifact -> STALE (a later run co-wrote it)
        with open(os.path.join(work, "art.txt"), "w", encoding="utf-8") as fh:
            fh.write("TAMPERED\n")
        v = t.verify(jobid)
        check(v.data.get("verdict") == "STALE" and v.data.get("stale") == 1,
              "verify STALE after the artifact changed (%r)" % (v.data,))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def test_runjob_killed_publishes_state():
    """A killed job must publish state=killed, not go silent -- the core
    Class-A fix. We launch a long sleep, kill the child (via sh, in the same
    MSYS pid space), and require the sidecar to notice and finalize."""
    if not SH:
        print("  SKIP: no local /bin/sh available")
        return
    home = tempfile.mkdtemp(prefix="asicjobs-test-")
    work = tempfile.mkdtemp(prefix="asicjobs-work-")
    try:
        t = remote.Transport(host="local", mode="ssh",
                             runner=local_runner(home, cwd=work))
        # exec so CHILD is the sleep itself; killing it is unambiguous.
        r = t.run(["sh", "-c", "exec sleep 20"],
                  flow="test", target="kill", interval=1)
        jobid = (r.data or {}).get("jobid")
        check(bool(jobid), "kill-test launched (%r)" % r)
        if not jobid:
            return
        running = _poll_until(t, jobid, {"running"}, tries=40, delay=0.25)
        check(running and running.get("state") == "running",
              "job is running before kill")
        pid = (running or {}).get("pid")
        if pid:
            # kill the child from within the same sh runtime (MSYS pids)
            t.run_sh("kill %d 2>/dev/null; true" % int(pid))
        final = _poll_until(t, jobid, {"killed", "failed", "done"},
                            tries=40, delay=0.25)
        check(final and final.get("state") == "killed",
              "killed child -> state=killed, never silent (%r)"
              % (final or {}).get("state"))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


# --- runner ---------------------------------------------------------------

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in tests:
        print("*", fn.__name__)
        try:
            fn()
        except AssertionError as exc:      # keep going: a tally is the point
            bad += 1
            print("  FAIL:", exc)
        except Exception as exc:           # noqa: BLE001
            bad += 1
            print("  ERROR: %s: %s" % (type(exc).__name__, exc))
    print("\n%d check(s) passed, %d test(s) failed" % (_PASS, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
