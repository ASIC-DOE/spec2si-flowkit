#!/usr/bin/env python3
"""Spectral responses -- an optical transfer function drawn as a picture.

A photonic flow's result is a SPECTRUM: transmission against wavelength,
with the metrics that were scored on it. The interchange contract the
photonics port speaks (`photonic-filter-interchange/response/1`) carries it
as `frequency_hz` beside `transfer` -- complex `[re, im]` pairs, or power in
dB -- and the browser showed it as 641 lines of raw JSON.

This draws it, and draws only what the file says:

  * |H|^2 in dB against WAVELENGTH (nm, from the file's own frequencies --
    the unit a photonics engineer reads), and, for a complex response, the
    phase AS RECORDED, unwrapped. No group delay is derived: the sign of the
    phase is a convention the file states (`coverage.phase_sign_measured`)
    and the plot quotes rather than applies;
  * the evaluations' metrics and verdicts exactly as scored, beside it.
    Nothing here re-derives a verdict (the browser's standing rule).

Stdlib only and PDK-blind, like the rest of the package: the schema is a
contract between flows, not a fact about any process.
"""
import math

C_M_S = 299792458.0
#: Below this, a power in dB is drawn at the floor rather than stretching the
#: axis to -300 dB: a deep null is real, and so is the plot losing its passband
#: to it. The floor is stated on the axis, never hidden.
FLOOR_DB = 60.0


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and math.isfinite(x)


def detect(d):
    """Is this parsed JSON a spectral response we can draw? -> kind or None"""
    if not isinstance(d, dict):
        return None
    f, t = d.get("frequency_hz"), d.get("transfer")
    if not isinstance(f, list) or not isinstance(t, list) or len(f) < 2 \
            or len(f) != len(t) or not all(_num(x) and x > 0 for x in f):
        return None
    kind = d.get("transfer_kind") or (
        "complex" if isinstance(t[0], list) else "power-db")
    if kind == "complex":
        if all(isinstance(p, list) and len(p) == 2 and _num(p[0]) and _num(p[1])
               for p in t):
            return "complex"
    elif kind == "power-db" and all(_num(x) for x in t):
        return "power-db"
    return None


def _nice(lo, hi, n=5):
    """Round tick values covering [lo, hi]."""
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw),
               default=10 * mag)
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + step * 1e-9:
        out.append(round(t, 12))
        t += step
    return out


def _fmt(v):
    a = abs(v)
    if a and (a >= 1e5 or a < 1e-3):
        return "%.3g" % v
    return ("%.4f" % v).rstrip("0").rstrip(".")


def _panel(x, y, x0, x1, top, h, width, label, color, ylo=None, yhi=None):
    """One axes box with a polyline; -> SVG fragment."""
    L, R = 64, 16
    yl = min(y) if ylo is None else ylo
    yh = max(y) if yhi is None else yhi
    if yh - yl < 1e-12:
        yl, yh = yl - 1, yh + 1
    pad = (yh - yl) * 0.05
    yl, yh = yl - pad, yh + pad
    W = width - L - R

    def sx(v):
        return L + (v - x0) / (x1 - x0) * W

    def sy(v):
        return top + h - (v - yl) / (yh - yl) * h
    parts = ['<rect x="%d" y="%d" width="%d" height="%d" fill="none" '
             'stroke="currentColor" stroke-opacity=".35"/>' % (L, top, W, h)]
    for t in _nice(yl, yh):
        yy = sy(t)
        parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="currentColor" '
                     'stroke-opacity=".12"/>' % (L, L + W, yy, yy))
        parts.append('<text x="%d" y="%.1f" font-size="10" text-anchor="end" '
                     'fill="currentColor">%s</text>' % (L - 4, yy + 3, _fmt(t)))
    pts = " ".join("%.1f,%.1f" % (sx(a), sy(min(max(b, yl), yh)))
                   for a, b in zip(x, y))
    parts.append('<polyline fill="none" stroke="%s" stroke-width="1.3" points="%s"/>'
                 % (color, pts))
    parts.append('<text x="12" y="%.1f" font-size="11" fill="currentColor" '
                 'transform="rotate(-90 12 %.1f)" text-anchor="middle">%s</text>'
                 % (top + h / 2, top + h / 2, label))
    return "".join(parts), sx


def render(d, width=760):
    """-> {svg, kind, n, span_nm, peak_db, verdict, port, evaluations, notes}"""
    kind = detect(d)
    if kind is None:
        raise ValueError("not a spectral response this view can draw")
    f, t = d["frequency_hz"], d["transfer"]
    if kind == "complex":
        p_db = [10 * math.log10(max(re * re + im * im, 1e-300)) for re, im in t]
        ph = [math.atan2(im, re) for re, im in t]
    else:
        p_db, ph = [float(v) for v in t], None
    # by wavelength, ascending -- a frequency grid is descending in wavelength
    order = sorted(range(len(f)), key=lambda i: C_M_S / f[i])
    lam = [C_M_S / f[i] * 1e9 for i in order]
    p_db = [p_db[i] for i in order]
    if ph is not None:
        ph = [ph[i] for i in order]
        # unwrap along the wavelength axis
        out, off = [ph[0]], 0.0
        for a, b in zip(ph, ph[1:]):
            dlt = b - a
            if dlt > math.pi:
                off -= 2 * math.pi
            elif dlt < -math.pi:
                off += 2 * math.pi
            out.append(b + off)
        ph = out
    x0, x1 = lam[0], lam[-1]
    peak = max(p_db)
    floor = peak - FLOOR_DB
    clipped = sum(1 for v in p_db if v < floor)
    h1, h2, gap, top = 210, 120, 34, 10
    height = top + h1 + gap + (h2 + gap if ph is not None else 0) + 4
    body, sx = _panel(lam, p_db, x0, x1, top, h1, width,
                      "|H|² (dB)", "#2563eb", ylo=max(min(p_db), floor),
                      yhi=peak)
    parts = [body]
    if ph is not None:
        b2, _ = _panel(lam, ph, x0, x1, top + h1 + gap, h2, width,
                       "phase (rad)", "#16a34a")
        parts.append(b2)
    axis_y = top + h1 + gap + (h2 + gap if ph is not None else 0) - gap + 14
    for tx in _nice(x0, x1, 8):
        if x0 <= tx <= x1:
            parts.append('<text x="%.1f" y="%d" font-size="10" text-anchor="middle" '
                         'fill="currentColor">%s</text>' % (sx(tx), axis_y, _fmt(tx)))
    parts.append('<text x="%d" y="%d" font-size="11" text-anchor="end" '
                 'fill="currentColor">wavelength (nm)</text>' % (width - 16, axis_y + 14))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
           'width="100%%" style="max-width:%dpx;font-family:sans-serif">%s</svg>'
           % (width, height + 16, width, "".join(parts)))

    evals = []
    for e in d.get("evaluations") or []:
        if not isinstance(e, dict):
            continue
        ms = {}
        for k, m in (e.get("metrics") or {}).items():
            if isinstance(m, dict):
                ms[k] = {"value": m.get("value"), "pass": m.get("pass")}
        evals.append({"id": e.get("id"), "verdict": e.get("verdict"),
                      "metrics": ms,
                      "controls": [{"name": c.get("name"), "value": c.get("value"),
                                    "unit": c.get("unit")}
                                   for c in (e.get("controls") or [])
                                   if isinstance(c, dict)]})
    notes = []
    if clipped:
        notes.append("%d point(s) below %.0f dB under the peak are drawn at "
                     "that floor" % (clipped, FLOOR_DB))
    cov = d.get("coverage") if isinstance(d.get("coverage"), dict) else {}
    if ph is not None and "phase_sign_measured" in cov:
        notes.append("phase as recorded; the file states phase_sign_measured "
                     "= %s, which this plot does not apply"
                     % cov.get("phase_sign_measured"))
    return {"svg": svg, "kind": kind, "n": len(f),
            "span_nm": [round(x0, 4), round(x1, 4)],
            "peak_db": round(peak, 3), "verdict": d.get("verdict"),
            "schema": d.get("schema"), "port": d.get("port"),
            "evaluation": d.get("transfer_evaluation"),
            "evaluations": evals, "notes": notes}
