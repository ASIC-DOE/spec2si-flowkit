#!/usr/bin/env python3
"""Tests for the in-process job recorder (jobrec.py).

No cluster: drives the recorder against a temp $JOBS root and asserts the
on-disk schema matches what `jobs ls/watch` reads. Run:
  python3 test_jobrec.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "bin"))
import jobrec  # noqa: E402

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


class _Env:
    """Set $JOBS root to a temp dir and control the attach/disable env,
    restoring os.environ on exit."""

    def __init__(self, **over):
        self.over = over
        self.saved = {}
        self.root = None

    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix="jobrec-test-")
        env = dict(self.over)
        env.setdefault("ASICJOBS_DIR", self.root)
        for k in ("ASICJOBS_DIR", "ASICJOBS_JOBDIR", "ASICJOBS_ID",
                  "ASICJOBS"):
            self.saved[k] = os.environ.get(k)
            if k in env:
                os.environ[k] = env[k]
            elif k in os.environ:
                del os.environ[k]
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --- standalone (self-register) -------------------------------------------

def test_standalone_lifecycle():
    with _Env() as e:
        rec = jobrec.begin(flow="digital", target="aiml65p2", tool="innovus",
                           total=7, cmd=["dig_flows/run.py", "x"])
        check(rec.enabled and not rec.attached, "standalone: enabled, own job")
        jd = rec.dir
        check(os.path.isfile(os.path.join(jd, "meta.json")),
              "meta.json written")
        meta = _read(os.path.join(jd, "meta.json"))
        check(meta["flow"] == "digital" and meta["ptotal"] == 7,
              "meta records flow + total")
        check(meta["engine"] == "jobrec", "meta marks the recorder engine")
        # running status before any progress
        st = _read(os.path.join(jd, "status.json"))
        check(st["state"] == "running" and st["kind"] == "status",
              "initial running status")

        rec.progress(3, 7, label="place")
        st = _read(os.path.join(jd, "status.json"))
        p = st.get("progress") or {}
        check(p.get("done") == 3 and p.get("total") == 7
              and p.get("label") == "place", "progress 3/7 [place] (%r)" % p)
        check(p.get("frac") == round(3 / 7.0, 4), "frac computed")

        rec.finalize(0, artifacts=[os.path.join(jd, "meta.json")])
        res = _read(os.path.join(jd, "result.json"))
        check(res["state"] == "done" and res["rc"] == 0, "result done/rc0")
        arts = res.get("artifacts", [])
        check(arts and arts[0]["exists"] is True, "artifact recorded exists")
        # terminal status + one event line
        st = _read(os.path.join(jd, "status.json"))
        check(st["state"] == "done", "terminal status = done")
        ev = os.path.join(e.root, "events.jsonl")
        check(os.path.isfile(ev), "events.jsonl appended")
        evs = [json.loads(x) for x in open(ev, encoding="utf-8", errors="replace") if x.strip()]
        check(len(evs) == 1 and evs[0]["state"] == "done", "one done event")


def test_standalone_failed_via_context_manager():
    with _Env():
        try:
            with jobrec.begin(flow="d", target="t") as rec:
                jd = rec.dir
                raise ValueError("boom")
        except ValueError:
            pass
        res = _read(os.path.join(jd, "result.json"))
        check(res["state"] == "failed" and res["rc"] == 1,
              "exception -> failed/rc1 via context manager")


# --- attach ---------------------------------------------------------------

def test_attach_enriches_progress_only():
    with _Env() as e:
        # simulate a runjob-created job dir
        jid = "enob-schem-20260721T000000Z-abcd"
        jd = os.path.join(e.root, jid)
        os.makedirs(jd)
        with open(os.path.join(jd, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "kind": "meta", "jobid": jid,
                       "started": 1000}, fh)
        os.environ["ASICJOBS_JOBDIR"] = jd
        os.environ["ASICJOBS_ID"] = jid

        rec = jobrec.begin(flow="x", target="y", tool="spectre", total=256)
        check(rec.enabled and rec.attached, "attach mode detected")
        check(rec.dir == jd, "attached to the runjob job dir")
        check(rec.started == 1000, "reads started from runjob meta")

        rec.progress(64, 256, label="conv")
        pj = os.path.join(jd, "progress.json")
        check(os.path.isfile(pj), "attach writes progress.json")
        p = _read(pj)
        check(p["done"] == 64 and p["total"] == 256 and p["frac"] == 0.25,
              "progress.json carries semantic progress (%r)" % p)

        # attach mode must NOT write result.json (the sidecar owns it)
        rec.finalize(0)
        check(not os.path.isfile(os.path.join(jd, "result.json")),
              "attach defers result.json to the sidecar")


def test_bump_is_attach_only():
    # not under runjob -> bump does nothing, creates no job
    with _Env() as e:
        jobrec._AUTO["rec"] = None
        jobrec.bump(tool="spectre")
        jobrec.bump(tool="spectre")
        check(not os.listdir(e.root), "bump is a no-op outside runjob")
    # under runjob -> bump advances a counter in progress.json
    with _Env() as e:
        jid = "j-t-20260721T000000Z-0001"
        jd = os.path.join(e.root, jid)
        os.makedirs(jd)
        with open(os.path.join(jd, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"started": 1000}, fh)
        os.environ["ASICJOBS_JOBDIR"] = jd
        jobrec._AUTO["rec"] = None
        jobrec.bump(tool="spectre", label="spectre")
        jobrec.bump(tool="spectre", label="spectre")
        jobrec.bump(tool="spectre", label="spectre")
        p = _read(os.path.join(jd, "progress.json"))
        check(p["done"] == 3 and p["label"] == "spectre",
              "bump counts sims on the parent job (%r)" % p)


# --- disabled -------------------------------------------------------------

def test_license_standalone_and_attach():
    """Phase 4: license() publishes seat state -- into status.json when
    standalone, and to license.json (for the sidecar) when attached."""
    seats = {"feature": "Virtuoso_Multi_mode_Simulation", "server": "7183@x",
             "issued": 54, "in_use": 54, "free": 0}
    # standalone: license folds into status.json
    with _Env():
        rec = jobrec.begin(flow="sim", target="sweep", tool="spectre")
        rec.license(info=seats)
        st = _read(os.path.join(rec.dir, "status.json"))
        check((st.get("license") or {}).get("free") == 0,
              "standalone status carries license free=0 (%r)" % st.get("license"))
        check(os.path.isfile(os.path.join(rec.dir, "license.json")),
              "license.json written")
    # attach: only license.json is written (sidecar owns status)
    with _Env() as e:
        jid = "sim-x-20260721T000000Z-0001"
        jd = os.path.join(e.root, jid)
        os.makedirs(jd)
        with open(os.path.join(jd, "meta.json"), "w", encoding="utf-8") as fh:
            fh.write('{"started":1000}')
        os.environ["ASICJOBS_JOBDIR"] = jd
        jobrec._AUTO["rec"] = None
        jobrec.publish_license(info=seats)
        lj = os.path.join(jd, "license.json")
        check(os.path.isfile(lj), "publish_license writes license.json (attach)")
        check(_read(lj)["free"] == 0, "attach license.json free=0")
        check(not os.path.isfile(os.path.join(jd, "status.json")),
              "attach mode does not write status.json (sidecar owns it)")


def test_disabled_is_total_noop():
    with _Env(ASICJOBS="0") as e:
        rec = jobrec.begin(flow="d", target="t", tool="innovus", total=7)
        check(not rec.enabled, "ASICJOBS=0 disables")
        rec.progress(1, 7)
        rec.finalize(0)
        check(not os.listdir(e.root), "disabled writes nothing at all")


def test_artifacts_stamped_sha_jobid():
    """Class-D: finalize must stamp each artifact with sha256 + jobid and
    write a sha256sum-checkable manifest, so a later reader can prove the
    file is the one THIS job produced."""
    with _Env() as e:
        art = os.path.join(e.root, "out.dat")
        with open(art, "w", encoding="utf-8") as fh:
            fh.write("hello world\n")
        rec = jobrec.begin(flow="d", target="t")
        rec.finalize(0, artifacts=[art])
        res = _read(os.path.join(rec.dir, "result.json"))
        a = res["artifacts"][0]
        check(a["jobid"] == rec.jobid, "artifact stamped with jobid")
        check(len(a.get("sha256", "")) == 64, "artifact carries sha256")
        man = os.path.join(rec.dir, "artifacts.sha256")
        check(os.path.isfile(man), "sha256sum manifest written")
        line = open(man, encoding="utf-8").read().strip()
        check(a["sha256"] in line and os.path.abspath(art) in line,
              "manifest has <sha>  <abspath> (%r)" % line)


def test_report_sh_reads_jobrec_jobs():
    """Cross-check: a job written by jobrec (Python) must be readable by
    report.sh (sh `sed`). Guards the compact-JSON contract -- json.dumps'
    default `": "` made report.sh's `list` read empty flow/state."""
    import shutil as _sh
    import subprocess
    sh = "/bin/sh" if os.path.exists("/bin/sh") else _sh.which("sh")
    if not sh:
        print("  SKIP: no /bin/sh")
        return
    with _Env() as e:
        rec = jobrec.begin(flow="digital", target="aiml65", tool="innovus",
                           total=7)
        rec.progress(4, 7, label="route")
        rec.finalize(0)
        report = os.path.join(os.path.dirname(__file__), "bin", "report.sh")
        env = dict(os.environ)  # ASICJOBS_DIR already points at e.root
        out = subprocess.check_output([sh, report, "list"], env=env)
        obj = json.loads(out.decode())
        jobs = {j["jobid"]: j for j in obj.get("jobs", [])}
        check(rec.jobid in jobs, "report.sh list sees the jobrec job")
        row = jobs.get(rec.jobid, {})
        check(row.get("flow") == "digital" and row.get("target") == "aiml65",
              "flow/target extracted (not empty) (%r)" % row)
        check(row.get("state") == "done",
              "state extracted from result.json (%r)" % row.get("state"))


def test_recorder_is_lf_stdlib():
    path = os.path.join(os.path.dirname(__file__), "bin", "jobrec.py")
    with open(path, "rb") as fh:
        data = fh.read()
    check(b"\r" not in data, "jobrec.py is LF-only")
    check(b"import dataclasses" not in data, "no dataclasses (cluster 3.6.8)")


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
