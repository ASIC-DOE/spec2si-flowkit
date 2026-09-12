#!/usr/bin/env python3
"""What a click is about to cost -- measured on this installation, not guessed.

Plan section 10.9. Some artifacts here are enormous: a 38 MB PSF transient on
asic7, a 1.65 MB GDS that takes 70 s to draw, a chip-level stream that would
never come back. Every one of them is one click away from a pane that simply
stops responding, and an unexplained pause is indistinguishable from a hang --
which is how a user learns to distrust a tool.

So each expensive read says, BEFORE it runs, roughly how long it will take.

THE NUMBERS ARE MEASURED, AND THEY ARE THIS MACHINE'S. A rate compiled into
the source would be a claim about someone else's disk: a remote transient
costs 0.055 s/MB end to end and a local one 0.037, and a render on a busy box
is not a render on an idle one. Every operation that finishes records
`(bytes, seconds)`, and the next estimate is the median rate over what
actually happened. The seeds below are real measurements too -- each is
cited, and the first live sample of the same kind starts displacing it.

WHAT THIS IS NOT. It is not a progress bar (nothing here can report its own
progress) and it is not a promise. It is the difference between "this pane is
broken" and "this takes about a minute", which is the whole of its value; the
UI phrases it as "about", and a wrong estimate is corrected by the sample it
generates. Measured once end to end: the pane predicted 70 s for the ctrl2
pilot from a single recorded sample and the render took 72.

THE HONEST OUTCOME OF MEASURING FIRST. Most reads here turned out to be cheap,
and exactly one thing is genuinely slow -- the GDS render, 45.4 s/MB measured.
A budget-capped read is cheap BY CONSTRUCTION: the cluster's 7.2 GB transient
costs what 64 MB costs, because 64 MB is all that is ever read, and the
estimate says so rather than quoting the file size back. Estimating the size
of a file you are not going to read is the easiest way to produce a scary
number that means nothing.

`python3 estimate.py` prints what this installation believes, and why.

Usage:
  python3 estimate.py
"""
import json
import os
import threading
import time

#: How long a click may take before the user is ASKED first rather than told
#: afterwards. Under this, the estimate is shown and the work just happens;
#: over it, the pane offers the read instead of starting it.
#:
#: Five seconds because that is about where a quiet pane stops reading as slow
#: and starts reading as broken. It is not a measured constant and does not
#: pretend to be one -- which is exactly why it is settable: someone on a slow
#: link wants to be asked sooner, and someone who does not want to be asked at
#: all should not have to edit the source to say so.
CONFIRM_SECONDS = float(os.environ.get("BROWSE_CONFIRM_SECONDS") or 5.0)

#: Below this, saying anything at all is noise.
MENTION_SECONDS = 1.5

#: Samples kept per (op, place). Enough to out-vote one cold cache or one
#: busy moment; short enough that a faster disk shows up within a session.
KEEP = 16

#: Seeds: real measurements, each cited. They are stored as ordinary samples
#: and carry `seed`, so a pane can say where a number came from -- and so the
#: first live sample of the same kind starts out-weighing them immediately.
#:
#: (op, place) -> [(bytes, seconds, provenance)]
SEEDS = {
    ("render", "local"): [
        (1653318, 70.3, "ctrl2 pilot, 259k polygons -- plan 10.2"),
    ],
    ("wave", "local"): [
        (1124934, 0.039, "lif_neuron tran1, 6131 points x 5 traces"),
        (27814000, 0.88, "the same body x24, 147k points"),
    ],
    ("wave", "remote"): [
        (39910000, 3.66, "asic7 sar_kernel PEX tran, 73 traces, wall clock"),
    ],
}

#: A floor per (op, place) in seconds, for the fixed cost no fit can see from
#: two large samples: an ssh round trip is ~0.4 s whatever the file weighs.
FLOOR = {("wave", "remote"): 0.5, ("render", "remote"): 0.6,
         ("fetch", "remote"): 0.4, ("list", "remote"): 0.4}

_MB = 1048576.0
_SAMPLES = {}
_GUARD = threading.Lock()
_LOADED = [False]


# ------------------------------------------------------------------ the store

def store_path():
    """Beside the render cache, and for the same reason: never in a flow tree.

    The browser is read-only with respect to everything it browses (plan
    principle 6), and a timings file dropped into a signed stamp directory
    would break the byte-for-byte comparison those directories exist for.
    """
    d = os.environ.get("BROWSE_CACHE_DIR")
    if not d:
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(os.path.expanduser("~"), ".cache"))
        d = os.path.join(base, "browse-gds-cache")
    return os.path.join(d, "timings.json")


def _key(op, place):
    return "%s|%s" % (op, place or "local")


def _load():
    """Seeds first, then whatever this installation has since measured."""
    if _LOADED[0]:
        return
    _LOADED[0] = True
    for (op, place), rows in SEEDS.items():
        _SAMPLES[_key(op, place)] = [
            {"bytes": b, "seconds": s, "seed": True, "why": why}
            for b, s, why in rows]
    try:
        with open(store_path(), encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(saved, dict):
        return
    for k, rows in saved.items():
        if not isinstance(rows, list):
            continue
        keep = [r for r in rows
                if isinstance(r, dict) and _num(r.get("bytes")) is not None
                and _num(r.get("seconds")) is not None]
        if keep:
            # Live samples go AFTER the seeds and the fit weights recent ones,
            # so a seed is a starting point rather than a permanent anchor.
            _SAMPLES.setdefault(k, [])
            _SAMPLES[k] = (_SAMPLES[k] + keep)[-KEEP:]


def _save():
    """Best effort. A browser that cannot write its cache still browses."""
    live = {k: [r for r in v if not r.get("seed")] for k, v in _SAMPLES.items()}
    live = {k: v for k, v in live.items() if v}
    p = store_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".%d.part" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(live, fh)
        os.replace(tmp, p)
    except OSError:
        pass


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def record(op, place, nbytes, seconds):
    """One finished operation. This is the only way a rate ever changes."""
    if not nbytes or seconds is None or seconds < 0:
        return
    with _GUARD:
        _load()
        k = _key(op, place)
        _SAMPLES.setdefault(k, [])
        _SAMPLES[k].append({"bytes": int(nbytes), "seconds": round(
            float(seconds), 4), "at": int(time.time())})
        _SAMPLES[k] = _SAMPLES[k][-KEEP:]
        _save()


def samples(op, place):
    with _GUARD:
        _load()
        return list(_SAMPLES.get(_key(op, place)) or [])


def reset():
    """Tests only -- forget everything, including whether seeds were loaded."""
    with _GUARD:
        _SAMPLES.clear()
        _LOADED[0] = False


# ------------------------------------------------------------------- the model

def predict(op, place, nbytes):
    """-> {seconds, rate, n, basis, floor}

    `seconds` is None when nothing has ever been measured for this operation.
    None is the answer, not a guess: a made-up estimate on the very first
    click is worse than no estimate, because it is indistinguishable from a
    measured one.

    The fit is a plain rate (s/MB) rather than a line through the origin plus
    an intercept, with a per-op FLOOR standing in for the fixed cost. Two
    reasons, both practical: a two-point least-squares fit on noisy timings
    happily produces a negative intercept, and the fixed cost here is a
    network round trip -- a thing we know independently and can state.
    """
    rows = samples(op, place)
    floor = FLOOR.get((op, place or "local"), 0.0)
    if not rows:
        return {"seconds": None, "rate": None, "n": 0, "basis": "unmeasured",
                "floor": floor}
    live = [r for r in rows if not r.get("seed")]
    use = live or rows
    rates = sorted(max(0.0, (r["seconds"] - floor)) / (r["bytes"] / _MB)
                   for r in use if r["bytes"] > 0)
    if not rates:
        return {"seconds": None, "rate": None, "n": 0, "basis": "unmeasured",
                "floor": floor}
    # MEDIAN, not mean: one render that queued behind another must not double
    # every estimate afterwards, and outliers here are one-sided (things get
    # slow, never faster than the work).
    mid = len(rates) // 2
    rate = rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2.0
    return {"seconds": floor + rate * (nbytes / _MB), "rate": rate,
            "n": len(use), "basis": "measured" if live else "seeded",
            "floor": floor}


def cost(op, place, nbytes, will_read=None):
    """The whole answer a pane needs about one prospective read.

    `will_read` is how many bytes will ACTUALLY be touched, when that differs
    from the file size -- every budget-capped read here. Quoting a 500 MB file
    size for a read that stops at 64 MB is a scary number that means nothing;
    the size is still reported, because the user wants to know the file is
    500 MB, but the TIME is for the work that will really happen.
    """
    n = nbytes if will_read is None else min(nbytes, will_read)
    p = predict(op, place, n)
    return {"op": op, "place": place or "local", "size": nbytes,
            "reads": n, "capped": will_read is not None and will_read < nbytes,
            "seconds": p["seconds"], "rate": p["rate"], "n": p["n"],
            "basis": p["basis"],
            "confirm": (p["seconds"] or 0) >= CONFIRM_SECONDS,
            "mention": (p["seconds"] or 0) >= MENTION_SECONDS,
            "text": phrase(op, p, nbytes, n)}


def confirm_bytes(op, place, seconds=CONFIRM_SECONDS):
    """The file size at which this operation crosses `seconds`.

    The remote reader needs a NUMBER, not a verdict: it stats the file on the
    cluster and must decide there whether to go on, because coming back to ask
    and then going again is two round trips for one question.
    """
    p = predict(op, place, _MB)
    if not p["rate"]:
        return None
    if p["rate"] <= 0:
        return None
    return int(max(0.0, seconds - p["floor"]) / p["rate"] * _MB)


def human(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 1:
        return "under a second"
    if seconds < 90:
        return "%.0f s" % seconds
    return "%.0f min" % round(seconds / 60.0)


def phrase(op, p, nbytes, reads):
    """One sentence a pane can print without composing anything itself."""
    if p["seconds"] is None:
        return "never measured on this machine -- the first read sets the rate"
    what = "%.1f MB" % (reads / _MB)
    if reads < nbytes:
        what += " of %.1f MB" % (nbytes / _MB)
    basis = ("measured, %d run%s" % (p["n"], "" if p["n"] == 1 else "s")
             if p["basis"] == "measured" else "from a recorded measurement")
    return "about %s for %s (%s, %.3g s/MB)" % (
        human(p["seconds"]), what, basis, p["rate"])


class Timer(object):
    """`with Timer("render", "local", size):` -- records on the way out.

    A context manager rather than a call after the fact, because the one thing
    this module must not do is stop learning when a code path grows an early
    return. It records on an exception too: a render that died after 70 s took
    70 s, and the next estimate should know that.
    """

    def __init__(self, op, place, nbytes, enabled=True):
        self.op, self.place, self.nbytes = op, place, nbytes
        self.enabled = enabled and bool(nbytes)
        self.t0 = None
        self.elapsed = None

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.time() - self.t0
        if self.enabled:
            record(self.op, self.place, self.nbytes, self.elapsed)
        return False


def _main(argv=None):
    """What this installation believes, and why. `python3 estimate.py`"""
    import sys
    with _GUARD:
        _load()
        keys = sorted(_SAMPLES)
    print("store: %s" % store_path())
    for k in keys:
        op, _, place = k.partition("|")
        rows = _SAMPLES[k]
        n_live = len([r for r in rows if not r.get("seed")])
        p = predict(op, place, _MB)
        print("%-18s %2d sample(s) (%d measured here)  %.4g s/MB + %.2g s"
              % (k, len(rows), n_live, p["rate"] or 0, p["floor"]))
        for mb in (1, 10, 64):
            print("      %5d MB -> %s" % (mb, human(
                predict(op, place, mb * _MB)["seconds"])))
    return 0 if keys else int(bool(argv or sys.argv))


if __name__ == "__main__":
    raise SystemExit(_main())
