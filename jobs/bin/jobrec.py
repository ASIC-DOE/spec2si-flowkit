#!/usr/bin/env python3
"""jobrec.py -- in-process job recorder for the Python flow engines.

docs/job_status_plan.md, Phase 1/2 wiring. The sh sidecar (runjob) makes a
DETACHED command self-report. But the digital and mixed-signal flow engines
are long-lived PYTHON processes that spawn tools directly (dig_flows/run.py,
mixed_signal/flow/ams_flow.py) -- they never went through runjob, so they
ran blind. This module lets them publish the SAME on-disk schema
(meta/status/result/events under $JOBS) that `jobs ls/watch/why` already
read, so a flow started in a cluster shell is observable from the laptop.

Two modes, decided automatically:

  * ATTACH -- when launched under runjob (env ASICJOBS_JOBDIR is set), the
    engine only ENRICHES progress: it drops a progress.json into the job
    dir that the sh sidecar picks up (semantic "stage 3/7", not just log
    bytes). The sidecar still owns the status/result/kill-trap lifecycle.
  * SELF-REGISTER -- otherwise the engine mints its own job and owns the
    whole lifecycle inline (meta -> running status -> result + event).

BEST EFFORT ALWAYS. Every method is wrapped so a $JOBS write failure (full
disk, NFS stall, permissions) is silently skipped -- observability must
never change a flow's behavior or its verdict. On by default; set
ASICJOBS=0 to disable entirely.

Pure stdlib, 3.6+ (runs in the tool env AND under the cluster's system
python3). NDA: records ids/states/counts/epochs and expected-artifact
paths only -- never log text.
"""
import hashlib
import json
import os
import random
import re
import socket
import time

try:
    import progress as _progress          # sibling in the shipped bundle
except Exception:                          # pragma: no cover
    _progress = None

SCHEMA = 1


def _license_seats(feature):
    """Best-effort FlexLM seat query via the sibling license module."""
    try:
        import license as _lic
        return _lic.seats(feature)
    except Exception:
        return None


def _enabled():
    return os.environ.get("ASICJOBS", "1").strip().lower() \
        not in ("0", "false", "no", "off", "")


def _jobs_root():
    return (os.environ.get("ASICJOBS_DIR")
            or os.path.join(os.path.expanduser("~"), ".asicjobs"))


def _dump(obj):
    """Compact JSON with NO space after ':' or ',' -- byte-compatible with
    the `printf` format runjob writes and the `sed` field-extractor in
    report.sh (which keys on `"key":"val"` with no whitespace). Using the
    json.dumps default (`": "`) makes report.sh's `list` read empty
    flow/state, so this is load-bearing, not cosmetic."""
    return json.dumps(obj, separators=(",", ":"))


def _atomic_write(path, text):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)                  # atomic on POSIX + NFS


def _san(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))[:40] or "x"


def _mint(flow, target):
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return "%s-%s-%s-%04x" % (_san(flow), _san(target), stamp,
                              random.randrange(1 << 16))


def _host():
    try:
        return socket.gethostname().split(".")[0]
    except Exception:
        return "unknown"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifacts_json(paths, jobid, manifest_path=None):
    """Stamp each artifact with its size/mtime/sha256/jobid (Class-D: an
    artifact carries the id of the job that made it, so a later reader can
    prove it is CURRENT). Also writes a sha256sum-checkable manifest with
    ABSOLUTE paths for `jobs verify`. sha skipped for dirs / very large
    files."""
    out = []
    lines = []
    for p in paths or []:
        try:
            if os.path.exists(p):
                st = os.stat(p)
                rec = {"path": p, "exists": True, "bytes": st.st_size,
                       "mtime": int(st.st_mtime), "jobid": jobid}
                if os.path.isfile(p) and st.st_size < (100 << 20):
                    sha = _sha256(p)
                    rec["sha256"] = sha
                    lines.append("%s  %s\n" % (sha, os.path.abspath(p)))
                out.append(rec)
            else:
                out.append({"path": p, "exists": False, "jobid": jobid})
        except Exception:
            out.append({"path": p, "exists": False, "jobid": jobid})
    if manifest_path is not None:
        try:
            _atomic_write(manifest_path, "".join(lines))
        except Exception:
            pass
    return out


class JobRecorder:
    """Records one flow run. Construct with `begin(...)`; call `progress()`
    / `heartbeat()` as it runs; `finalize()` (or use as a context manager)
    at the end. A DISABLED recorder (opt-out, or any init failure) is a
    silent no-op for every method."""

    def __init__(self):
        self.enabled = False
        self.attached = False
        self.dir = None
        self.jobid = None
        self.started = None
        self.tool = None
        self.total = None
        self._license = None
        self._hb_stop = None
        self._hb_thread = None

    # -- construction ------------------------------------------------------

    @classmethod
    def begin(cls, flow="job", target="run", cmd=None, cwd=None, expect=None,
              tool=None, total=None, enabled=None):
        self = cls()
        try:
            if enabled is None:
                enabled = _enabled()
            if not enabled:
                return self
            self.enabled = True
            self.tool = tool
            self.total = total
            self.started = int(time.time())
            jd = os.environ.get("ASICJOBS_JOBDIR")
            if jd and os.path.isdir(jd):
                # ATTACH: runjob owns the lifecycle; we only enrich progress.
                self.attached = True
                self.dir = jd
                self.jobid = (os.environ.get("ASICJOBS_ID")
                              or os.path.basename(jd.rstrip("/")))
                self._read_started()
                return self
            # SELF-REGISTER: mint and own the whole lifecycle.
            self.jobid = _mint(flow, target)
            self.dir = os.path.join(_jobs_root(), self.jobid)
            os.makedirs(self.dir, exist_ok=True)
            self._write_meta(flow, target, cmd, cwd, expect)
            self._publish("running", None)
        except Exception:
            self.enabled = False           # any failure -> disable, silently
        return self

    def _read_started(self):
        try:
            with open(os.path.join(self.dir, "meta.json"), encoding="utf-8") as fh:
                self.started = json.load(fh).get("started", self.started)
        except Exception:
            pass

    def _write_meta(self, flow, target, cmd, cwd, expect):
        meta = {
            "schema": SCHEMA, "kind": "meta", "jobid": self.jobid,
            "flow": _san(flow), "target": _san(target), "host": _host(),
            "cwd": cwd or os.getcwd(), "tool": self.tool or "",
            "started": self.started, "interval": 0,
            "ptool": self.tool or "", "ptotal": self.total or 0, "plog": "",
            "cmd": list(cmd) if cmd else [], "expect": list(expect or []),
            "engine": "jobrec",
        }
        _atomic_write(os.path.join(self.dir, "meta.json"),
                      _dump(meta) + "\n")

    # -- progress ----------------------------------------------------------

    def _prog_obj(self, done, total=None, label=None):
        tot = total if total is not None else self.total
        el = int(time.time()) - (self.started or int(time.time()))
        if _progress is not None:
            return _progress.assemble(self.tool or "flow", done, total=tot,
                                      label=label, elapsed_s=el)
        out = {"tool": self.tool or "flow", "done": done}
        if tot:
            out["total"] = tot
            out["frac"] = round(min(done, tot) / float(tot), 4)
        return out

    def progress(self, done, total=None, label=None):
        """Report SEMANTIC progress the engine already knows (e.g. stage
        k of N). Attach mode drops progress.json; standalone republishes
        status.json."""
        if not self.enabled:
            return
        try:
            p = self._prog_obj(done, total, label)
            if self.attached:
                _atomic_write(os.path.join(self.dir, "progress.json"),
                              _dump(p))
            else:
                self._publish("running", p)
        except Exception:
            pass

    def heartbeat(self, log_path=None, state="running"):
        """Recompute progress from a tool LOG via the shared extractor and
        publish. Use when the engine cannot count stages itself (spectre /
        xrun): point it at the growing log."""
        if not self.enabled:
            return
        try:
            p = None
            if log_path and self.tool and _progress is not None:
                el = int(time.time()) - (self.started or int(time.time()))
                p = _progress.from_file(self.tool, log_path, elapsed_s=el,
                                        total=self.total)
            if self.attached:
                if p is not None:
                    _atomic_write(os.path.join(self.dir, "progress.json"),
                                  _dump(p))
            else:
                self._publish(state, p)
        except Exception:
            pass

    def license(self, feature=None, info=None):
        """Publish current license-seat availability for this job (Phase 4).
        `info` is a seats dict from license.seats(); if omitted and `feature`
        is given, query it here. Written to license.json (the sidecar folds
        it into status in attach mode) and into status.json directly when
        standalone -- so a job stalled on 0 free seats reads as
        WAITING_LICENSE, not a hang."""
        if not self.enabled:
            return
        try:
            if info is None and feature is not None:
                info = _license_seats(feature)
            if info is None:
                return
            info = dict(info)
            info["ts"] = int(time.time())
            self._license = info
            _atomic_write(os.path.join(self.dir, "license.json"), _dump(info))
            if not self.attached:
                self._publish("running", None)
        except Exception:
            pass

    def start_heartbeat(self, log_path, interval=10):
        """Spawn a daemon thread that heartbeats off `log_path` every
        `interval` s -- for a blocking tool call (subprocess.run) we cannot
        instrument line-by-line. Stopped by finalize()/stop_heartbeat()."""
        if not self.enabled:
            return
        try:
            import threading
            self._hb_stop = threading.Event()

            def _loop():
                while not self._hb_stop.wait(interval):
                    self.heartbeat(log_path)
            self._hb_thread = threading.Thread(target=_loop, daemon=True)
            self._hb_thread.start()
        except Exception:
            pass

    def stop_heartbeat(self):
        try:
            if self._hb_stop is not None:
                self._hb_stop.set()
        except Exception:
            pass

    # -- lifecycle ---------------------------------------------------------

    def _publish(self, state, prog):
        hb = int(time.time())
        el = hb - (self.started or hb)
        status = {
            "schema": SCHEMA, "kind": "status", "jobid": self.jobid,
            "state": state, "pid": os.getpid(), "host": _host(),
            "started": self.started, "heartbeat": hb, "elapsed_s": el,
            "log_bytes": 0, "pstat": "R",
            "progress": prog if prog is not None else None,
            "license": self._license,
        }
        _atomic_write(os.path.join(self.dir, "status.json"),
                      _dump(status) + "\n")

    def finalize(self, rc, state=None, artifacts=None):
        """Record the terminal verdict. Attach mode defers to the sidecar
        (which writes result/event/terminal status); standalone writes them
        itself, publishing the terminal status LAST as the commit point."""
        self.stop_heartbeat()
        if not self.enabled:
            return
        try:
            if state is None:
                state = "done" if rc == 0 else "failed"
            if self.attached:
                # let the app mark 100%/verdict for the sidecar to surface
                self.progress(self.total, self.total) if self.total else None
                return
            fin = int(time.time())
            result = {
                "schema": SCHEMA, "kind": "result", "jobid": self.jobid,
                "state": state, "rc": rc, "host": _host(),
                "started": self.started, "finished": fin,
                "elapsed_s": fin - (self.started or fin),
                "artifacts": _artifacts_json(
                    artifacts, self.jobid,
                    os.path.join(self.dir, "artifacts.sha256")),
            }
            _atomic_write(os.path.join(self.dir, "result.json"),
                          _dump(result) + "\n")
            try:
                with open(os.path.join(_jobs_root(), "events.jsonl"), "a", encoding="utf-8") as fh:
                    fh.write(_dump({
                        "schema": SCHEMA, "jobid": self.jobid, "event": "end",
                        "state": state, "rc": rc, "epoch": fin}) + "\n")
            except Exception:
                pass
            self._publish(state, None)     # commit point, written last
        except Exception:
            pass

    # context manager: finalize on exit, mapping an exception to failed.
    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.finalize(0 if et is None else 1,
                      state=None if et is None else "failed")
        return False


def begin(*a, **k):
    """Module-level convenience: `rec = jobrec.begin(flow=..., target=...)`."""
    return JobRecorder.begin(*a, **k)


# --- attach-only throughput counter for tools called in a loop ------------

_AUTO = {"rec": None, "n": 0}


def bump(tool=None, label="sims", total=None):
    """Increment a per-process counter and republish it into the runjob
    job's progress (ATTACH mode). For low-level engines (spectre_flow.run,
    ams_flow.run) called many times inside a sweep: instead of minting a
    job per call (noise), they just advance a "N sims done" counter on the
    parent job. A strict NO-OP when not launched under runjob (no
    ASICJOBS_JOBDIR) or when disabled -- so it is safe to call from a hot
    loop and never creates a job on its own."""
    try:
        if not _enabled() or not os.environ.get("ASICJOBS_JOBDIR"):
            return
        if _AUTO["rec"] is None or not _AUTO["rec"].attached:
            _AUTO["rec"] = JobRecorder.begin(tool=tool or "sim", total=total)
            _AUTO["n"] = 0
        _AUTO["n"] += 1
        _AUTO["rec"].progress(_AUTO["n"], total, label=label)
    except Exception:
        pass


def publish_license(feature=None, info=None):
    """Attach-only: publish the runjob job's current license-seat state.
    No-op outside runjob or when disabled -- safe to call from a hot
    admission path (e.g. spectre_flow.sim_workers)."""
    try:
        if not _enabled() or not os.environ.get("ASICJOBS_JOBDIR"):
            return
        if _AUTO["rec"] is None or not _AUTO["rec"].attached:
            _AUTO["rec"] = JobRecorder.begin()
        _AUTO["rec"].license(feature=feature, info=info)
    except Exception:
        pass
