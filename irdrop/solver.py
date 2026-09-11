#!/usr/bin/env python3
"""A DC resistive solver for supply grids. NODE-AGNOSTIC, VENDORED.

⚠️ **THIS FILE IS VENDORED.** The master is `spec2si-flowkit/irdrop/
solver.py`; every consumer holds a byte-identical copy that `sync.py`
hash-checks. Never edit a vendored copy -- change the master, re-vendor,
and let each consumer decide whether its adapter still holds.

WHY THIS ONE FILE IS SHARED WHEN THE ENGINE IS NOT
--------------------------------------------------
The flowkit's contract is deliberately narrow: the rule set is shared and
the engine is not, because the engines genuinely diverge (Calibre vs
PVS/Pegasus, PyCell vs SKILL, one substrate vs per-tub). This file is the
exception that proves the rule -- it touches no PDK, no deck, no PCell,
no card. It takes resistances in ohms and currents in amps and returns
volts. There is nothing in it that could differ between 65 nm, 28 nm and
110 nm, so three copies of it would be three chances to fix a bug twice.

What stays local is everything that knows about a process:

  * where the resistances come from (each node's own RC card, and the
    per-cut/per-square lookups that go with it);
  * where the currents come from (that node's own Spectre reader);
  * how the rail plan becomes a netlist (each repo's own topology).

Those three are the adapter each consumer writes. This is the solver.

WHAT IT SOLVES
--------------
Modified nodal analysis with no sources but current injections and ideal
voltage pins:

    G v = i,   G[a][a] += 1/R,  G[a][b] -= 1/R,  ...

pinned nodes are removed from the system and their contribution folded
into the right-hand side. Dense LU with partial pivoting, pure Python --
a few hundred nodes solve in milliseconds and it adds no dependency,
which matters because these repos run raw `python3` through a cluster
activation wrapper and a numpy import is not guaranteed there.

⚠️ **A FLOATING NODE IS AN ERROR, NOT A ZERO.** A node with no resistive
path to any pin makes the matrix singular. Solvers habitually paper over
this with a tiny conductance to ground, which turns an unconnected rail
-- a real and common topology bug -- into a plausible small voltage. This
one finds the unreachable nodes by traversal BEFORE factoring and raises
naming them, because "0.3 mV of drop" on a net that is not connected is
exactly the kind of clean-looking wrong answer these flows exist to
avoid.

⚠️ **UNITS ARE SI AND CHECKED.** Ohms, amps, volts. The EM cards on every
node speak milliamps, and the one thing guaranteed to produce a
believable wrong answer is a 1000x current. `Grid.inject_mA` exists so a
caller never has to write the conversion itself.
"""


class SolverError(Exception):
    """Raised for a topology the solver refuses to answer for."""


class Grid(object):
    """A resistive network: named nodes, resistors, injections, pins.

    Nodes are created implicitly by being referred to. Names are opaque
    strings -- the adapter chooses them, and choosing them well is what
    makes a report readable ("vco.VDD.tap", "trunk@x=120.5").
    """

    def __init__(self):
        self.edges = []          # (a, b, ohms, label)
        self.inj = {}            # node -> amps INTO the node
        self.pins = {}           # node -> volts
        self._nodes = set()

    # ---- construction ------------------------------------------------
    def resistor(self, a, b, ohms, label=None):
        """A resistive branch. `ohms` must be > 0."""
        if a == b:
            raise SolverError(
                "resistor {!r} shorts node {!r} to itself".format(label, a))
        try:
            r = float(ohms)
        except (TypeError, ValueError):
            raise SolverError(
                "resistor {!r} has non-numeric resistance {!r}"
                .format(label, ohms))
        if not (r > 0.0):
            # A zero-ohm branch is a merge, not a resistor, and silently
            # accepting it produces an infinite conductance.
            raise SolverError(
                "resistor {!r} between {!r} and {!r} has resistance {!r}; "
                "a zero or negative resistance is a merge or a bug, not a "
                "branch -- merge the nodes instead".format(label, a, b, r))
        self.edges.append((a, b, r, label))
        self._nodes.add(a)
        self._nodes.add(b)
        return self

    def inject(self, node, amps):
        """Current drawn OUT of `node` by a load, in amps.

        Sign convention: a positive number is a LOAD -- current leaving
        the grid into a block. That is what every caller means, and the
        sign flip lives here rather than in each adapter."""
        self.inj[node] = self.inj.get(node, 0.0) + float(amps)
        self._nodes.add(node)
        return self

    def inject_mA(self, node, milliamps):
        """As `inject`, in milliamps -- the unit every EM card speaks."""
        return self.inject(node, float(milliamps) * 1e-3)

    def pin(self, node, volts=0.0):
        """Hold `node` at a fixed voltage: the pad, or the supply entry."""
        self.pins[node] = float(volts)
        self._nodes.add(node)
        return self

    # ---- solve -------------------------------------------------------
    def _reachable(self):
        """Nodes with a resistive path to some pinned node."""
        adj = {}
        for a, b, _r, _l in self.edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        seen, stack = set(self.pins), list(self.pins)
        while stack:
            n = stack.pop()
            for m in adj.get(n, ()):
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        return seen

    def solve(self):
        """{node: volts}. Raises rather than guessing at a bad topology."""
        if not self.pins:
            raise SolverError(
                "no pinned node -- a resistive network with no voltage "
                "reference has no solution. Pin the pad.")
        reach = self._reachable()
        orphans = sorted(n for n in self._nodes if n not in reach)
        if orphans:
            raise SolverError(
                "{} node(s) have no resistive path to any pin: {}{}. "
                "This is an open in the grid, not a small voltage -- fix "
                "the topology rather than grounding them."
                .format(len(orphans), ", ".join(orphans[:8]),
                        " ..." if len(orphans) > 8 else ""))
        loaded = sorted(n for n in self.inj if n not in self._nodes)
        if loaded:
            raise SolverError(
                "current injected into unknown node(s): {}"
                .format(", ".join(loaded)))

        free = sorted(n for n in self._nodes if n not in self.pins)
        idx = dict((n, k) for k, n in enumerate(free))
        m = len(free)
        G = [[0.0] * m for _ in range(m)]
        rhs = [0.0] * m
        for n, a in self.inj.items():
            if n in idx:
                rhs[idx[n]] -= a          # a load draws current out
        for a, b, r, _l in self.edges:
            g = 1.0 / r
            ia, ib = idx.get(a), idx.get(b)
            if ia is not None:
                G[ia][ia] += g
            if ib is not None:
                G[ib][ib] += g
            if ia is not None and ib is not None:
                G[ia][ib] -= g
                G[ib][ia] -= g
            elif ia is not None:
                rhs[ia] += g * self.pins[b]
            elif ib is not None:
                rhs[ib] += g * self.pins[a]
        v = _lu_solve(G, rhs)
        out = dict(self.pins)
        for n, k in idx.items():
            out[n] = v[k]
        return out

    # ---- reporting ---------------------------------------------------
    def report(self, volts=None):
        """Per-load drop and the branch that carries the most of it.

        `drop_V` is measured from the node's own reference pin -- the
        HIGHEST pinned voltage, because a supply grid's reference is the
        pad it is fed from. On a ground grid, pin at 0 and the "drop" is
        the rise, which is the same number with the sign that matters."""
        v = self.solve() if volts is None else volts
        ref = max(self.pins.values())
        loads = []
        for n, a in sorted(self.inj.items()):
            loads.append({"node": n, "A": a, "mA": a * 1e3,
                          "V": v[n], "drop_V": ref - v[n],
                          "drop_mV": (ref - v[n]) * 1e3})
        branches = []
        for a, b, r, lab in self.edges:
            i = (v[a] - v[b]) / r
            branches.append({"label": lab, "from": a, "to": b, "ohm": r,
                             "A": i, "mA": i * 1e3,
                             "drop_V": abs(v[a] - v[b]),
                             "drop_mV": abs(v[a] - v[b]) * 1e3,
                             "P_W": i * i * r})
        loads.sort(key=lambda d: -d["drop_V"])
        branches.sort(key=lambda d: -d["drop_V"])
        return {"reference_V": ref, "loads": loads, "branches": branches,
                "worst_load": loads[0] if loads else None,
                "worst_branch": branches[0] if branches else None,
                "total_A": sum(d["A"] for d in loads)}


def _lu_solve(A, b):
    """Dense LU with partial pivoting. A is consumed."""
    n = len(b)
    if n == 0:
        return []
    M = [row[:] + [b[k]] for k, row in enumerate(A)]
    for col in range(n):
        p = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[p][col]) < 1e-18:
            raise SolverError(
                "the conductance matrix is singular at row {} -- the grid "
                "has a disconnected or degenerate section that the "
                "reachability check did not catch. Do not interpret a "
                "result from this network.".format(col))
        if p != col:
            M[col], M[p] = M[p], M[col]
        piv = M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / piv
            if f == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / M[r][r]
    return x


def series(grid, nodes, ohms_each, label=None):
    """Chain `nodes` with one resistor between each adjacent pair.

    The common shape: a trunk sampled at its taps. `ohms_each` is either
    one value or a list of len(nodes)-1."""
    if len(nodes) < 2:
        raise SolverError("a series chain needs at least two nodes")
    if not isinstance(ohms_each, (list, tuple)):
        ohms_each = [ohms_each] * (len(nodes) - 1)
    if len(ohms_each) != len(nodes) - 1:
        raise SolverError(
            "series: {} nodes need {} resistances, got {}"
            .format(len(nodes), len(nodes) - 1, len(ohms_each)))
    for k in range(len(nodes) - 1):
        grid.resistor(nodes[k], nodes[k + 1], ohms_each[k],
                      "{}[{}]".format(label or "series", k))
    return grid
