#!/usr/bin/env python3
"""Offline self-test for procscan -- no cluster, no ssh, no processes killed.

Every fixture here is a transcription of REAL `ps` output measured on this
cluster on 2026-07-31, because the classifier's whole job is to tell four
things apart that look alike from a distance: live work, a session a human
abandoned, a worker whose driver died, and a desktop daemon that must never
be touched.

  python3 test_procscan.py
"""
import json
import os
import sys

import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
import procscan                          # noqa: E402
import license as lic                    # noqa: E402

CDS = "/u/cad/cds/IC231/tools.lnx86/dfII/bin/64bit/"
INN = "/u/cad/cds/DDI251/INNOVUS251/tools.lnx86/"


def _row(pid, ppid, args, stat="Sl", etimes=100, cpusec=1, tty="?", rss=1024):
    return {"pid": pid, "ppid": ppid, "stat": stat, "etimes": etimes,
            "cpusec": cpusec, "rss_kb": rss, "tty": tty, "args": args}


def _data(rows, jobs=None, own=None, host="asic8"):
    return {"host": host, "user": "smandal", "rows": rows,
            "jobs": jobs or {}, "own": own or []}


# ------------------------------------------------------------------ identity
def test_a_desktop_session_is_never_a_candidate():
    """The measured reality: `ps -u` on asic7 returned 87 processes, ~75 of
    them a VNC/GNOME desktop, most ppid=1 and three weeks old. An age-and-
    orphanhood rule would have offered the lot for killing."""
    rows = [
        _row(1131360, 1131339, "/usr/libexec/Xorg :3 -config vncserver.conf",
             etimes=1690194, cpusec=217),
        _row(1131674, 1131516, "/usr/bin/gnome-shell", etimes=1690193,
             cpusec=509),
        _row(1131722, 1, "/usr/libexec/gvfsd", etimes=1690193, cpusec=0),
        _row(1131320, 1, "/usr/lib/systemd/systemd --user", etimes=1690194),
        _row(4151088, 1, CDS + "virtuoso", etimes=1506 * 3600, cpusec=4237),
    ]
    got = procscan.classify(_data(rows))
    assert [p["pid"] for p in got] == [4151088], [p["pid"] for p in got]


def test_the_tool_root_decides_and_the_vendor_is_named():
    assert procscan.is_tool(CDS + "virtuoso")
    assert not procscan.is_tool("/usr/bin/python3 run.py")
    assert procscan.vendor_of(CDS + "virtuoso") == "Cadence"
    assert procscan.vendor_of("/u/cad/cliosoft/latest/bin/sosdisplog") \
        == "Cliosoft"
    assert procscan.vendor_of("/u/cad/mgc/aok_cal/bin/calibre") \
        == "Siemens/Mentor"
    assert procscan.vendor_of("/usr/bin/vim") is None


# ------------------------------------------------------------- strong signals
def test_a_pipe_driven_worker_without_its_driver_is_unreachable():
    """oa_worker.py talks to `virtuoso -restore worker.il` over its stdin and
    stdout PIPES -- there is no other channel. Losing the parent makes it
    unreachable BY CONSTRUCTION, which is a fact about how the tool is driven
    rather than a guess from its age.

    Measured: asic7 pid 1424500, orphaned, 163 h old.
    """
    row = _row(1424500, 1,
               CDS + "virtuoso -nographE -restore worker.il -log worker.cds.log",
               etimes=163 * 3600, cpusec=70)
    got = procscan.classify(_data([row], host="asic7"))[0]
    kinds = [e["kind"] for e in got["evidence"]]
    assert "driver-gone" in kinds, kinds
    assert got["safe_to_kill"] is True


def test_the_same_worker_with_a_live_driver_is_left_alone():
    """asic6 pid 86434: the same command line, parent alive. The flow depends
    on this one -- 'use the warm worker, never batch virtuoso'."""
    rows = [_row(86356, 900, "/usr/bin/python3 oa_worker.py"),
            _row(86434, 86356,
                 CDS + "virtuoso -nographE -restore worker.il", etimes=1800)]
    got = procscan.classify(_data(rows, host="asic6"))
    worker = [p for p in got if p["pid"] == 86434][0]
    assert [e["kind"] for e in worker["evidence"]] == [], worker["evidence"]
    assert worker["safe_to_kill"] is False


def test_a_process_of_a_finished_job_is_a_leftover():
    row = _row(555, 1, INN + "innovus -stylus", etimes=7200, cpusec=10)
    jobs = {"asic7/555": {"jobid": "dec2s-pnr-2026", "state": "done"}}
    got = procscan.classify(_data([row], jobs, host="asic7"))[0]
    assert "job-finished" in [e["kind"] for e in got["evidence"]]
    assert got["safe_to_kill"] is True


def test_a_process_of_a_running_job_is_protected_at_any_age():
    """The single most expensive mistake this tool could make."""
    row = _row(555, 1, INN + "innovus -stylus",
               etimes=400 * 3600, cpusec=1)          # ancient AND idle
    jobs = {"asic7/555": {"jobid": "dec2s-pnr-2026", "state": "running"}}
    got = procscan.classify(_data([row], jobs, host="asic7"))[0]
    assert got["protected"], got
    assert got["safe_to_kill"] is False
    assert got["suspect"] is False, "a running job must not even look suspect"


def test_the_scan_never_proposes_its_own_session():
    row = _row(777, 1, CDS + "virtuoso", etimes=99 * 3600, cpusec=0)
    got = procscan.classify(_data([row], own=[777, 3]))[0]
    assert got["protected"] and got["safe_to_kill"] is False


# --------------------------------------------------------------- weak signals
def test_idle_is_judged_independently_of_orphanhood():
    """THE bug this test exists for. The 62-day Virtuoso on asic8 was started
    from a VNC desktop that is still up, so it has a controlling tty and a
    live session leader -- not an orphan. A rule requiring "orphaned AND idle"
    reported it as healthy while it held a Cadence seat for two months.
    """
    row = _row(4151088, 1, CDS + "virtuoso", tty="pts/5",
               etimes=1506 * 3600, cpusec=4237)
    got = procscan.classify(_data([row]))[0]
    kinds = [e["kind"] for e in got["evidence"]]
    assert "idle" in kinds, kinds
    assert got["suspect"] is True
    # ... and weak evidence alone is never sufficient to call it killable
    assert got["safe_to_kill"] is False


def test_a_busy_tool_is_not_idle_however_old():
    row = _row(1, 1, CDS + "virtuoso", etimes=100 * 3600, cpusec=90 * 3600)
    got = procscan.classify(_data([row]))[0]
    assert "idle" not in [e["kind"] for e in got["evidence"]]
    assert got["suspect"] is False


def test_a_young_quiet_tool_is_not_yet_idle():
    """A tool may legitimately sit still while a human thinks."""
    row = _row(1, 1, CDS + "virtuoso", etimes=600, cpusec=0)
    assert "idle" not in [e["kind"] for e in
                          procscan.classify(_data([row]))[0]["evidence"]]


# -------------------------------------------------------------------- session
def test_detached_cadence_helpers_rejoin_their_session():
    """They reparent to init but name their session in their own arguments.
    Grouping on ppid alone split one Virtuoso into a tree plus six false
    orphans -- and killing the root would have left every one behind.
    """
    rows = [
        _row(4151088, 1, CDS + "virtuoso", etimes=1506 * 3600, cpusec=4237),
        _row(4151161, 1, CDS + "cdsMsgServer -mpssession virtuoso4151088"),
        _row(4151769, 4151768,
             CDS + "libManager -mpssession virtuoso4151088 -mpshost asic8"),
        _row(4151768, 4151088, CDS + "cdsServIpc -c 45937 -n 8"),
        _row(4152296, 4152295,
             CDS + "perfUtilExtCtrl 4151088 /dev/shm/perf"),
        _row(4152295, 4151088, CDS + "cdsServIpc -c 45937 -n 12"),
    ]
    groups = procscan.sessions(procscan.classify(_data(rows)))
    assert len(groups) == 1, [g["root_pid"] for g in groups]
    g = groups[0]
    assert g["root_pid"] == 4151088 and g["n"] == 6, g["n"]
    assert g["root"]["name"] == "virtuoso"


def test_a_session_root_is_not_confused_by_a_self_reference():
    """`perfUtilExtCtrl 4152296 ...` naming its OWN pid must not make it its
    own parent -- that is an infinite walk, not a session."""
    rows = [_row(4152296, 1, CDS + "perfUtilExtCtrl 4152296 /dev/shm/perf")]
    groups = procscan.sessions(procscan.classify(_data(rows)))
    assert len(groups) == 1 and groups[0]["n"] == 1


def test_session_totals_are_the_thing_worth_reclaiming():
    rows = [_row(100, 1, CDS + "virtuoso", etimes=9999, rss=1024 * 1024),
            _row(101, 100, CDS + "libManager", rss=512 * 1024)]
    g = procscan.sessions(procscan.classify(_data(rows)))[0]
    assert g["rss_mb"] == 1536, g["rss_mb"]
    assert g["vendor"] == "Cadence"


# ----------------------------------------------------------------- transport
def test_an_unreachable_host_is_an_error_not_an_empty_list():
    """Reporting a busy host as clean is the exact failure this tool exists
    to prevent."""
    class _Bad(object):
        @staticmethod
        def Transport(host=None, timeout=None):
            class T(object):
                def run_sh(self, script, **kw):
                    class R(object):
                        ok, status, data = False, "UNKNOWN", None
                        reason = "transport timeout"
                    return R()
            return T()
    keep = procscan._remote
    procscan._remote = _Bad
    try:
        got = procscan.scan(["asic8"])
        assert got["asic8"]["error"], got
        assert got["asic8"]["procs"] == []
        assert "timeout" in got["asic8"]["error"]
    finally:
        procscan._remote = keep


def test_the_remote_script_is_shell_not_bare_python():
    """remote.py pipes what it is given to /bin/sh -s (its invariant 2), so a
    bare python program is read as sh and exits 2 -- which is exactly what the
    first version of this scanner did against every host."""
    w = procscan._wrap("print('hi')")
    assert w.startswith("#!/bin/sh"), w[:40]
    assert "python3 - <<" in w
    assert "print('hi')" in w


def test_the_kill_script_verifies_before_it_signals():
    """A pid seen in a scan may be a different process by the time anyone
    clicks. The check is what separates killing a stale tool from killing
    whatever inherited its pid."""
    src = procscan._PY_KILL
    assert "expect not in cur" in src
    assert "st.st_uid != os.getuid()" in src, "must refuse another user's pid"
    assert "SIGTERM" in src and "SIGKILL" in src
    # TERM first, and KILL only when explicitly forced
    assert src.index("SIGTERM") < src.index("SIGKILL")
    assert "if not hard" in src


def test_kill_quoting_survives_an_awkward_expectation():
    assert procscan._sh_quote("it's") == "'it'\\''s'"


# ------------------------------------------------- licence seat attribution
_LMSTAT_A = """lmstat - Copyright (c) 1989-2018 Flexera.
Flexible License Manager status on Fri 8/7/2026 17:45

License server status: 7188@iolicense2.inst.bnl.gov
    License file(s) on iolicense2: /opt/flex/clio.dat:

iolicense2.inst.bnl.gov: license server UP (MASTER) v11.19.8

Vendor daemon status (on iolicense2):
   cliolmd: UP v11.18.1

Feature usage info:

Users of clio_sos_ent_ul:  (Total of 20 licenses issued;  Total of 2 licenses in use)

  "clio_sos_ent_ul" v2024.0610, vendor: cliolmd
  floating license

    smandal asic8.inst.bnl.gov /dev/tty (v2024.0610) (iolicense2.inst.bnl.gov/7188 901), start Fri 5/29 12:46  (linger: 0 / 86400)
    otheruser asic3.inst.bnl.gov :1 (v2024.0610) (iolicense2.inst.bnl.gov/7188 902), start Fri 8/7 09:00

Users of clio_sos_viadfII_ul:  (Total of 20 licenses issued;  Total of 1 license in use)

    smandal asic8.inst.bnl.gov /dev/tty (v2024.0610) (iolicense2.inst.bnl.gov/7188 1001), start Fri 5/29 12:47  (linger: 0 / 86400)
"""


def _now():
    import time
    return time.mktime((2026, 8, 7, 17, 45, 0, 0, 1, -1))


def test_usage_lines_parse_including_the_linger_suffix():
    """The Cliosoft daemon appends `(linger: 0 / 86400)`. Left unconsumed it
    corrupts the start field, which is the whole join key."""
    got = lic.parse_usage(_LMSTAT_A, user="smandal")
    assert [g["feature"] for g in got] ==         ["clio_sos_ent_ul", "clio_sos_viadfII_ul"], got
    assert got[0]["start"] == "Fri 5/29 12:46", got[0]
    assert got[0]["host"] == "asic8.inst.bnl.gov"
    assert got[0]["handle"] == 901 and got[0]["port"] == 7188


def test_other_peoples_usernames_never_leave_the_parser():
    """These lines carry real people. The filter is the privacy boundary."""
    got = lic.parse_usage(_LMSTAT_A, user="smandal")
    assert all(g["user"] == "smandal" for g in got), got
    assert "otheruser" not in json.dumps(got)


def test_the_handle_is_not_mistaken_for_a_pid():
    """FlexLM reports NO pid -- measured against the live server, zero usage
    lines carry one. The number after the port is FlexLM's handle, and calling
    it a pid would attribute seats to unrelated processes."""
    got = lic.parse_usage(_LMSTAT_A, user="smandal")
    assert "pid" not in got[0], got[0]
    src = open(os.path.join(os.path.dirname(os.path.abspath(procscan.__file__)),
                            "bin", "license.py"), encoding="utf-8").read()
    assert "NOT REPORT A PID" in src.upper()


def test_a_session_is_matched_to_the_seats_it_started_with():
    """The real case: virtuoso started 12:45:53 on asic8, seats stamped 12:46
    and 12:47 -- 7 s and 67 s later."""
    now = _now()
    age = now - time.mktime((2026, 5, 29, 12, 45, 53, 0, 1, -1))
    sess = [{"host": "asic8", "root_pid": 4151088, "age_s": age}]
    out = procscan.attribute(sess, lic.parse_usage(_LMSTAT_A, user="smandal"),
                             now)
    feats = [s["feature"] for s in out[0]["seats"]]
    assert feats == ["clio_sos_ent_ul", "clio_sos_viadfII_ul"], out[0]["seats"]
    assert 0 <= out[0]["seats"][0]["lag_s"] <= 120, out[0]["seats"][0]


def test_a_session_on_another_host_gets_nothing():
    now = _now()
    age = now - time.mktime((2026, 5, 29, 12, 45, 53, 0, 1, -1))
    sess = [{"host": "asic6", "root_pid": 1, "age_s": age}]
    out = procscan.attribute(sess, lic.parse_usage(_LMSTAT_A, user="smandal"),
                             now)
    assert out[0]["seats"] == [], out[0]["seats"]


def test_an_unrelated_start_time_does_not_match():
    """A session started days after the checkout holds none of it."""
    now = _now()
    sess = [{"host": "asic8", "root_pid": 9, "age_s": 3600}]
    out = procscan.attribute(sess, lic.parse_usage(_LMSTAT_A, user="smandal"),
                             now)
    assert out[0]["seats"] == [], out[0]["seats"]


def test_no_seat_found_is_an_answer_not_a_gap():
    """The point of the whole feature: a stale process that holds nothing is
    one whose kill frees memory, not a licence. It must be reportable as such
    and distinguishable from 'nobody asked'."""
    now = _now()
    sess = [{"host": "asic8", "root_pid": 9, "age_s": 3600}]
    out = procscan.attribute(sess, [], now)
    assert out[0]["seats"] == []
    assert "seats" in out[0], "the field must exist even when empty"


def test_a_december_checkout_does_not_land_in_the_future():
    """FlexLM omits the year. Taking the current one puts a 12/31 checkout
    eleven months ahead when read on 1 January."""
    now = time.mktime((2026, 1, 2, 10, 0, 0, 0, 1, -1))
    t = procscan._seat_start_epoch("Wed 12/31 23:50", now)
    assert t is not None and t < now, (t, now)
    assert now - t < 3 * 86400, "rolled back too far"


def test_the_session_start_uses_the_scans_own_clock():
    """The licence query is a separate round trip ~90 s after the scan. Anchor
    a session's start to the LICENCE clock and every lag is skewed by that gap
    -- observed live as -26 s and -86 s where the truth is +7 s and +67 s, one
    of them 4 s from silently dropping out of the window."""
    scan_now = _now()
    age = scan_now - time.mktime((2026, 5, 29, 12, 45, 53, 0, 1, -1))
    sess = [{"host": "asic8", "root_pid": 4151088, "age_s": age}]
    seats = lic.parse_usage(_LMSTAT_A, user="smandal")
    out = procscan.attribute(sess, seats, scan_now)
    lags = [x["lag_s"] for x in out[0]["seats"]]
    assert all(0 <= l <= 120 for l in lags), lags


def test_scan_records_the_clock_its_ages_were_read_against():
    assert '"now"' in procscan._PY_SCAN, "the scan does not report its clock"
    src = open(procscan.__file__, encoding="utf-8").read()
    assert 'result[h].get("now")' in src,         "attribute() is being handed the licence clock again"


def test_binary_discovery_sweeps_candidates_outside_servers():
    """THE performance bug, as a shape rather than a stopwatch.

    Sweeping servers on the OUTSIDE meant one dead licence server re-probed
    every candidate lmstat under it. There are 144 of them on this cluster and
    that measured 70.6 s of a 92.9 s query -- for a server that was never going
    to answer. Candidates must be the outer loop, so discovery happens once.
    """
    src = procscan._seats_script()
    disc = src[src.index("for c in candidates:"):]
    inner = disc[:disc.index("if lmstat or")]
    assert "for srv in servers:" in inner,         "servers are not the inner loop -- discovery will repeat per server"
    # and after discovery, a known-good binary's answer is FINAL
    assert "ask(lmstat, srv) if lmstat else None" in src, src[:200]


def test_a_dead_server_does_not_retrigger_discovery():
    """The invariant that makes one sweep enough: once a good binary gets
    nothing from a server, the SERVER is down and no other binary helps.
    Re-probing there is not a retry, it is a category error."""
    src = procscan._seats_script()
    after = src[src.index("def query(srv):"):]
    assert "for c in candidates" not in after,         "the per-server path still falls back to sweeping binaries"


def test_the_seat_query_is_not_parallelised():
    """Measured: the daemon serialises and charges ~5 s per concurrent
    connection (7184 alone 0.5 s, paired 10.5 s). A thread pool measured
    slower than the sequential loop it replaced."""
    src = procscan._seats_script()
    assert "ThreadPoolExecutor" not in src,         "parallel lmstat is measurably slower here -- see the comment"
    assert "PACE_S" in src, "the rate-limit pacing is gone"


def test_the_advertised_cost_matches_the_measured_one():
    """A cost the user is quoted before paying it has to be the real one --
    the previous number outlived its measurement by one commit."""
    src = open(procscan.__file__, encoding="utf-8").read()
    assert "~10 s" in src, "the announced cost still claims the old timing"
    assert procscan.SEATS_TIMEOUT_S >= 60, procscan.SEATS_TIMEOUT_S


def main():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ok   %s" % name)
        except Exception as exc:                       # noqa: BLE001
            bad += 1
            print("  FAIL %s: %s" % (name, exc))
    print("%d/%d passed" % (len(fns) - bad, len(fns)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
