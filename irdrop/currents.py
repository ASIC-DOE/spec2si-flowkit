#!/usr/bin/env python3
"""Supply currents from a simulated operating point. VENDORED.

⚠️ **THIS FILE IS VENDORED.** The master is `spec2si-flowkit/irdrop/
currents.py`; every consumer holds a byte-identical copy that `sync.py`
hash-checks. Change the master, re-vendor.

The companion to `solver.py`. That one turns a topology into volts; this
one turns a simulator's operating point into the amps the topology needs,
and it is shared for the same reason: a Spectre operating point is a
SIMULATOR format, not a PDK one. `Vdd:p` means the same thing at 65 nm,
28 nm and 110 nm.

What stays local is **how you get the numbers**: AIML reads PSF through
`wave.read_psf`, ONR has a text-oppoint reader in `tech/probes/
parse_oppoint.py`, and each flow names its supplies differently. Those
readers hand this module a plain `{name: value}` dict and it does the
three things that are the same everywhere and easy to get wrong.

THE THREE THINGS
----------------
1. **SIGN.** A voltage source's terminal current is negative when the
   source is delivering -- but which sign a given simulator, source
   orientation and save statement produce is not something to assume.
   `supply_load()` takes the MAGNITUDE and records that it did. What it
   will not do is silently accept a mixture: if some supplies come back
   positive and others negative, that is a bench with a source wired
   backwards, and it says so.

2. **UNITS.** PSF is SI (amps). A text operating point is NOT -- Spectre
   writes `27.8321 pA`, and the prefix is on the UNIT token, so a naive
   `float()` on the value alone mis-scales by twelve orders of magnitude
   and still looks like a number. `scale()` reads the prefix.

3. **COVERAGE.** ⚠️ **A NET WITH NO SIMULATED CURRENT RAISES.** It does
   not default to zero. Zero current is a valid answer -- a decoupling-
   only rail draws none -- but it has to be SAID, because "this net was
   not in the operating point" and "this net draws nothing" produce the
   same 0.000 mV and only one of them is true. `Currents.require()` is
   how a caller asserts the set it expects.

⚠️ WHICH NUMBER, AND WHY IT IS NOT ONE NUMBER
----------------------------------------------
A DC operating point gives one current per supply. A transient gives
three, and they answer different questions:

  * `avg`  -- what an EM rule asks for. Every EM card in this flow states
             a DC/average limit and nothing else.
  * `rms`  -- Joule self-heating, which feeds back into the EM
             temperature derate. No card here states an RMS limit.
  * `peak` -- the worst-case IR drop, which is what a supply-sensitive
             block actually sees.

`Currents` carries a `kind` and refuses to be combined with a different
one, because averaging one block and taking the peak of another produces
a grid nobody simulated.
"""

#: SI prefixes a Spectre text operating point emits. 'a' is atto, not
#: ampere: the prefix is the FIRST character and only when the unit token
#: is longer than one character.
_PREFIX = {"a": 1e-18, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
           "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6,
           "G": 1e9, "T": 1e12}
_BARE = {"Coul", "Ohm", "Ohms", "Sec", "sec", "Hz", "deg"}

KINDS = ("dc", "avg", "rms", "peak")


class CurrentError(Exception):
    """Raised for an operating point that cannot answer the question."""


def scale(value_tok, unit_tok):
    """SI-scaled float for a (value, unit) token pair from a text oppoint."""
    v = float(value_tok)
    if not unit_tok or unit_tok in _BARE or len(unit_tok) == 1:
        return v
    p = _PREFIX.get(unit_tok[0])
    return v * p if p else v


class Currents(object):
    """Supply currents with their provenance, in AMPS.

    `kind` is one of KINDS and travels with the numbers, because an EM
    check and an IR check want different ones and mixing them silently is
    the failure this class exists to prevent.
    """

    def __init__(self, kind, source, analysis=None, corner=None,
                 temp_c=None, note=None):
        if kind not in KINDS:
            raise CurrentError(
                "kind {!r} is not one of {} -- name what these numbers are"
                .format(kind, ", ".join(KINDS)))
        self.kind = kind
        self.source = source
        self.analysis = analysis
        self.corner = corner
        self.temp_c = temp_c
        self.note = note
        self._a = {}
        self._raw = {}

    # ---- construction ------------------------------------------------
    def supply_load(self, name, amps, raw=None):
        """Record `name` as drawing `|amps|` out of the grid.

        ⚠️ THE MAGNITUDE IS TAKEN AND THE RAW VALUE IS KEPT. A supply's
        terminal current sign depends on the source orientation and the
        save statement, so the magnitude is the only part that is
        reliably about the circuit. `signs_consistent()` is how a caller
        checks the part that was thrown away."""
        a = abs(float(amps))
        self._a[name] = a
        self._raw[name] = float(amps if raw is None else raw)
        return self

    @classmethod
    def from_values(cls, values, kind, source, want=None, **kw):
        """Build from a `{name: amps}` dict a local reader produced.

        `want` optionally selects and RENAMES: `{"vdd": "Vdd:p"}` takes
        `Vdd:p` from the dict and records it as `vdd`. A requested name
        that is absent raises -- see the coverage rule."""
        c = cls(kind, source, **kw)
        if want is None:
            for k, v in values.items():
                c.supply_load(k, v)
            return c
        missing = [k for k, src in want.items() if src not in values]
        if missing:
            raise CurrentError(
                "the operating point has no {} -- asked for {} and it "
                "holds {}. A supply that was not saved is not a supply "
                "that draws nothing.".format(
                    ", ".join(repr(want[m]) for m in sorted(missing)),
                    ", ".join(sorted(want)), ", ".join(sorted(values))[:200]))
        for k, src in want.items():
            c.supply_load(k, values[src])
        return c

    # ---- use ---------------------------------------------------------
    def amps(self, name):
        if name not in self._a:
            raise CurrentError(
                "no simulated current for {!r}. It is not zero -- it is "
                "absent, and those give the same 0.000 mV. This operating "
                "point holds: {}".format(name, ", ".join(sorted(self._a))))
        return self._a[name]

    def mA(self, name):
        return self.amps(name) * 1e3

    def names(self):
        return sorted(self._a)

    def require(self, names):
        """Assert every net the topology needs has a simulated current."""
        missing = sorted(set(names) - set(self._a))
        if missing:
            raise CurrentError(
                "{} net(s) in the grid have no simulated current: {}. "
                "Either save them in the bench or state explicitly that "
                "they draw none -- a grid solved with a silently missing "
                "load reports a drop that is too small and looks fine."
                .format(len(missing), ", ".join(missing)))
        extra = sorted(set(self._a) - set(names))
        return {"required": sorted(names), "unused": extra}

    def signs_consistent(self):
        """True when every non-zero raw current has the same sign.

        A mixture means at least one source is wired backwards, or one
        terminal was saved from the other side. Both are bench faults
        that produce plausible magnitudes."""
        s = set()
        for v in self._raw.values():
            if v > 0:
                s.add(1)
            elif v < 0:
                s.add(-1)
        return len(s) <= 1

    def total_A(self):
        return sum(self._a.values())

    def provenance(self):
        """What every derived number must carry with it."""
        return {"kind": self.kind, "source": self.source,
                "analysis": self.analysis, "corner": self.corner,
                "temp_C": self.temp_c, "note": self.note,
                "n_supplies": len(self._a),
                "total_mA": round(self.total_A() * 1e3, 6),
                "signs_consistent": self.signs_consistent()}

    def merged_with(self, other):
        """Combine two operating points of the SAME kind."""
        if other.kind != self.kind:
            raise CurrentError(
                "refusing to merge {!r} currents with {!r} -- averaging one "
                "block and taking the peak of another describes a grid "
                "nobody simulated".format(self.kind, other.kind))
        out = Currents(self.kind, "{} + {}".format(self.source, other.source),
                       self.analysis, self.corner, self.temp_c, self.note)
        for n, v in list(self._a.items()):
            out.supply_load(n, v, self._raw[n])
        for n, v in list(other._a.items()):
            if n in out._a:
                raise CurrentError(
                    "both operating points define {!r}".format(n))
            out.supply_load(n, v, other._raw[n])
        return out

    def __repr__(self):
        return "<Currents {} n={} total={:.6g} mA from {}>".format(
            self.kind, len(self._a), self.total_A() * 1e3, self.source)


def parse_oppoint_text(text, want=None):
    """`{instance: {param: SI float}}` from a Spectre `info what=oppoint`.

    Kept here rather than in each repo because the SI-prefix trap is the
    same everywhere and it is the kind of bug that produces a number
    twelve orders of magnitude wrong that still plots."""
    import re
    out = {}
    inst_re = re.compile(r"Instance:\s*(\S+)(.*?)(?=Instance:|\Z)", re.S)
    line_re = re.compile(
        r"^\s*(\w+)\s*=\s*(-?[\d.]+(?:[eE][+-]?\d+)?)\s*(\S*)\s*$", re.M)
    for m in inst_re.finditer(text):
        name, body = m.group(1), m.group(2)
        d = {}
        for lm in line_re.finditer(body):
            k, v, u = lm.group(1), lm.group(2), lm.group(3)
            if want and k not in want:
                continue
            try:
                d[k] = scale(v, u)
            except ValueError:
                pass
        if d:
            out[name] = d
    return out
