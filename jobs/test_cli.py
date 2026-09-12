#!/usr/bin/env python3
"""Tests for cli.py display/health helpers (no cluster)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli  # noqa: E402
import hosts  # noqa: E402
import remote  # noqa: E402

_PASS = 0
_FAIL = 0


def check(cond, msg):
    """Assert, loudly, in BOTH runners.

    This used to count a failure and print it, and return. Under `python3
    test_cli.py` that produced an honest tally -- but the project runs
    pytest, where a test function that returns None has PASSED. So every
    check in this file was green by construction: a deliberately reverted bug
    (the parent-parser default clobbering `--host`) was caught by neither the
    negative control nor the suite.

    Raising fixes pytest and costs the script runner nothing, because `main`
    below catches per test and still prints the tally.
    """
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        return
    _FAIL += 1
    raise AssertionError(msg)


def test_license_note():
    d = {"license": {"feature": "Virtuoso_Multi_mode_Simulation",
                     "issued": 54, "free": 0}}
    note = cli._license_note(d)
    check("WAITING_LICENSE" in note and "0/54" in note,
          "0 free seats -> WAITING_LICENSE note (%r)" % note)
    check(cli._license_note({"license": {"free": 5}}) == "",
          "free seats -> no note")
    check(cli._license_note({}) == "", "no license -> no note")


def test_health_waiting_license_beats_hang():
    prev = {"heartbeat": 100, "progress": {"done": 7}}
    # heartbeat advanced, progress did NOT -> would be 'no progress', but a
    # 0-seat license makes it WAITING_LICENSE, not a suspected hang.
    cur = {"heartbeat": 105, "progress": {"done": 7}, "pstat": "S",
           "license": {"feature": "Virtuoso_x", "issued": 54, "free": 0}}
    h = cli._health(prev, cur)
    check("WAITING_LICENSE" in h, "stalled + 0 seats -> WAITING_LICENSE (%r)" % h)
    # with free seats, the same stall is just 'no progress'
    cur2 = dict(cur, license={"free": 10})
    h2 = cli._health(prev, cur2)
    check("no progress" in h2 and "WAITING_LICENSE" not in h2,
          "stalled + free seats -> plain no-progress (%r)" % h2)


class _FakeResult:
    def __init__(self, ok, data):
        self.ok = ok
        self.data = data
        self.status = "KNOWN" if ok else "UNKNOWN"
        self.reason = None


class _FakeT:
    host = "asic6"

    def __init__(self, data, ok=True):
        self._r = _FakeResult(ok, data)

    def list(self):
        return self._r


def test_ls_lines_and_wait_lic():
    data = {"now": 1000, "jobs": [
        {"jobid": "enob-schem-x", "state": "running", "flow": "enob",
         "target": "schem", "started": 900, "heartbeat": 995,
         "elapsed_s": 100, "frac": 0.25, "lic_free": 5},
        {"jobid": "sim-sweep-y", "state": "running", "flow": "sim",
         "target": "sweep", "started": 950, "heartbeat": 998,
         "elapsed_s": 50, "frac": None, "lic_free": 0}]}
    ok, lines = cli._ls_lines(_FakeT(data))
    check(ok, "ls_lines ok")
    body = "\n".join(lines)
    check("JOBID" in lines[0] and "PROG" in lines[0], "header present")
    check("25%" in body, "progress %% shown")
    check("wait-lic" in body,
          "running job with 0 free seats renders wait-lic (%r)" % body)
    # newest-first WITHIN a group: sim (started 950) before enob (900)
    check(body.index("sim-sweep-y") < body.index("enob-schem-x"),
          "sorted newest-first")
    check("-- running (2)" in body, "running group header + count (%r)" % body)


def test_ls_lines_groups():
    """running / done / failed buckets, in that order, newest-first inside."""
    def job(jid, state, started, **kw):
        d = {"jobid": jid, "state": state, "flow": "f", "target": "t",
             "started": started, "heartbeat": 999, "elapsed_s": 1}
        d.update(kw)
        return d

    data = {"now": 1000, "jobs": [
        job("d-old", "done", 100), job("r-old", "running", 200),
        job("k-1", "killed", 300), job("d-new", "done", 400),
        job("f-1", "failed", 500), job("r-new", "running", 600),
        job("w-1", "running", 700, lic_free=0),
        job("weird", "banana", 800)]}
    ok, lines = cli._ls_lines(_FakeT(data))
    body = "\n".join(lines)
    check(ok, "groups ok")
    # group order: running, done, failed, other
    for a, b in (("-- running", "-- done"), ("-- done", "-- failed"),
                 ("-- failed", "-- other")):
        check(body.index(a) < body.index(b), "%s before %s" % (a, b))
    check("-- running (3)" in body, "wait-lic counts as running (%r)" % body)
    check("-- failed (2)" in body, "killed+failed are one bucket")
    check("-- done (2)" in body, "done bucket")
    # newest-first inside a group, and no job is dropped
    check(body.index("r-new") < body.index("r-old"), "newest-first in group")
    check(body.index("w-1") < body.index("r-new"), "wait-lic sorts by time")
    for jid in ("d-old", "r-old", "k-1", "d-new", "f-1", "r-new", "w-1",
                "weird"):
        check(jid in body, "job %s present (nothing dropped)" % jid)
    check("-- other (1)" in body, "unknown state -> other, never dropped")


def test_ls_lines_host_column():
    """$JOBS is shared NFS, so the table is cluster-wide -- the row has to
    say WHERE. A record without a host (older bundle) shows '-', not blank."""
    data = {"now": 1000, "jobs": [
        {"jobid": "enob-a-1", "state": "running", "flow": "enob",
         "target": "a", "host": "asic10", "started": 200, "heartbeat": 999,
         "elapsed_s": 5},
        {"jobid": "enob-b-2", "state": "running", "flow": "enob",
         "target": "b", "started": 100, "heartbeat": 999, "elapsed_s": 5}]}
    ok, lines = cli._ls_lines(_FakeT(data))
    check(ok and "HOST" in lines[0], "HOST header (%r)" % lines[0])
    rows = [ln for ln in lines[1:] if not ln.startswith("--")]
    check("asic10" in rows[0], "host rendered (%r)" % rows[0])
    col = lines[0].index("HOST")
    check(rows[1][col] == "-", "missing host -> '-' placeholder (%r)" % rows[1])
    for r in rows:                     # still aligned with the extra column
        check(r[lines[0].index("STATE"):][:7] == "running",
              "STATE still aligned (%r)" % r)


def test_ls_lines_columns_align():
    """Every row lines up on the same columns, whatever the id length, and
    the table is no wider than the longest id needs."""
    data = {"now": 1000, "jobs": [
        {"jobid": "control_oa-draw_srcgnd-20260721T181527Z-9a69",
         "state": "done", "flow": "control_oa", "target": "draw_srcgnd",
         "started": 200, "heartbeat": 999, "elapsed_s": 50},
        {"jobid": "enob-pexw-20260721T174201Z-87ad", "state": "done",
         "flow": "enob", "target": "pexw", "started": 100,
         "heartbeat": 999, "elapsed_s": 1322, "frac": 1.0}]}
    ok, lines = cli._ls_lines(_FakeT(data))
    check(ok, "align ok")
    rows = [ln for ln in lines[1:] if not ln.startswith("--")]
    check(len(rows) == 2, "two rows")
    # STATE starts at the same offset on every row and on the header
    col = lines[0].index("STATE")
    for r in rows:
        check(r[col:col + 4] == "done", "STATE aligned at %d in %r" % (col, r))
    # ...and the id is never truncated, since it is what you paste into `why`
    for j in data["jobs"]:
        check(j["jobid"] in "\n".join(rows), "id %s intact" % j["jobid"])
    # flow/target is NOT repeated when the id already carries it
    check("control_oa/draw_srcgnd" not in "\n".join(rows),
          "redundant FLOW/TARGET dropped (%r)" % rows)
    # ...but IS shown when the id does not decompose to it
    odd = {"now": 1000, "jobs": [
        {"jobid": "hand-minted-id", "state": "done", "flow": "a-b",
         "target": "c", "started": 1, "heartbeat": 999, "elapsed_s": 1}]}
    _, l2 = cli._ls_lines(_FakeT(odd))
    check("a-b/c" in "\n".join(l2),
          "id/meta mismatch -> flow/target still shown (%r)" % l2)


def test_ls_lines_empty_and_error():
    ok, lines = cli._ls_lines(_FakeT({"jobs": []}))
    check(ok and "no jobs" in lines[0], "empty -> friendly note")
    ok, lines = cli._ls_lines(_FakeT(None, ok=False))
    check(not ok, "transport failure -> ok False (top keeps polling)")


def test_health_heartbeat_stall():
    prev = {"heartbeat": 100, "progress": {"done": 1}}
    cur = {"heartbeat": 100, "progress": {"done": 1}}
    check("suspect" in cli._health(prev, cur),
          "frozen heartbeat -> sidecar/host suspect")


def test_a_host_named_before_the_subcommand_survives():
    """The bug the shared-read work uncovered: a parent-parser option also
    lives on every subparser, and the subparser runs SECOND, so its default
    overwrote the value -- `jobs --host asic7 ls` silently read asic6. It
    failed the quiet way: the operator names a host and the tool goes
    somewhere else. Both orders must now agree."""
    for argv in (["--host", "asic7", "ls"], ["ls", "--host", "asic7"]):
        ns = cli.build_parser().parse_args(argv)
        named = cli._fill_globals(ns)
        check(ns.host == "asic7", "%s -> host=%s" % (argv, ns.host))
        check(named is True, "%s -> named=%s" % (argv, named))


def test_an_unnamed_host_is_distinguishable_from_the_default():
    """`ns.host == 'asic6'` cannot tell "because I said so" from "because
    nobody said anything", and that decides whether a READ may go to another
    host. With suppressed defaults it is a fact, not a guess."""
    ns = cli.build_parser().parse_args(["ls"])
    named = cli._fill_globals(ns)
    check(named is False, "an unspecified host read as named")
    check(ns.host == remote.DEFAULT_HOST, "default not applied: %s" % ns.host)
    check(ns.timeout == remote.DEFAULT_TIMEOUT, "timeout default not applied")
    ns = cli.build_parser().parse_args(["--timeout", "9", "ls"])
    cli._fill_globals(ns)
    check(ns.timeout == 9.0, "an explicit timeout was lost: %s" % ns.timeout)


def test_only_reads_may_wander_between_hosts():
    """`run` is a decision about where work LANDS; a read is a question about
    the shared filesystem. Measured: asic6/asic7/asic8 return the identical
    389-job set, so a read has no reason to care -- and `--host auto` remains
    how you ask for a launch host to be chosen."""
    check("run" not in cli._READ_ACTIONS, "run must never fail over")
    check("hosts" not in cli._READ_ACTIONS, "hosts probes each box itself")
    for a in ("ls", "top", "why", "verify", "wait", "watch"):
        check(a in cli._READ_ACTIONS, "%s should be a shared read" % a)
    check("run" not in hosts.READ_CALLS, "run is not a shared-store read")


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
            print("  ERROR:", exc)
    print("\n%d check(s) passed, %d test(s) failed" % (_PASS, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
