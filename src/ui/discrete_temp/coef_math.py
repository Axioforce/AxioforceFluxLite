from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple, Literal


Point = Tuple[float, float]  # (temperature_f, value)


@dataclass(frozen=True)
class BaselineAnchor:
    t0: float
    y0: float
    method: str  # "weighted_baseline" | "mean_baseline" | "closest_k" | "mean_all" | "first"
    used_baseline_band: bool


@dataclass(frozen=True)
class SummaryStats:
    n: int
    mean: float
    std: float
    median: float
    p25: float
    p75: float


def _percentile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if p <= 0:
        return float(sorted_vals[0])
    if p >= 100:
        return float(sorted_vals[-1])
    # Linear interpolation
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    d0 = float(sorted_vals[f]) * (c - k)
    d1 = float(sorted_vals[c]) * (k - f)
    return d0 + d1


def summarize(values: Iterable[float]) -> SummaryStats:
    vals = [float(v) for v in values]
    if not vals:
        return SummaryStats(n=0, mean=0.0, std=0.0, median=0.0, p25=0.0, p75=0.0)
    n = len(vals)
    mean = sum(vals) / float(n)
    var = sum((v - mean) ** 2 for v in vals) / float(n)
    svals = sorted(vals)
    return SummaryStats(
        n=n,
        mean=float(mean),
        std=float(var**0.5),
        median=_percentile(svals, 50),
        p25=_percentile(svals, 25),
        p75=_percentile(svals, 75),
    )


def compute_baseline_anchor(
    points: Sequence[Point],
    *,
    baseline_low_f: float = 74.0,
    baseline_high_f: float = 78.0,
    target_f: float = 76.0,
    closest_k: int = 5,
) -> BaselineAnchor:
    """
    Compute a stable (T0, Y0) anchor used by coefficient estimation and coef-line plotting.

    Preference order:
    - Weighted baseline mean within [baseline_low_f, baseline_high_f], biased toward target_f.
    - Simple baseline mean within that band (fallback).
    - Weighted mean of closest_k points to target_f (if no baseline band points exist).
    - Mean of all points (fallback).
    - First point (last resort).
    """
    pts = [(float(t), float(y)) for (t, y) in points if points]
    if not pts:
        return BaselineAnchor(t0=float(target_f), y0=0.0, method="first", used_baseline_band=False)

    baseline = [(t, y) for (t, y) in pts if baseline_low_f <= t <= baseline_high_f]
    if baseline:
        # Weighted baseline anchor biased toward target_f
        try:
            weights: List[float] = []
            for t, _y in baseline:
                dt = abs(float(t) - float(target_f))
                weights.append(1.0 / (1.0 + dt))
            w_sum = sum(weights)
            if w_sum <= 0.0:
                raise ValueError("baseline_weight_sum_zero")
            t0 = sum(w * t for w, (t, _y) in zip(weights, baseline)) / w_sum
            y0 = sum(w * y for w, (_t, y) in zip(weights, baseline)) / w_sum
            return BaselineAnchor(t0=float(t0), y0=float(y0), method="weighted_baseline", used_baseline_band=True)
        except Exception:
            try:
                t0 = sum(t for t, _ in baseline) / float(len(baseline))
                y0 = sum(y for _, y in baseline) / float(len(baseline))
                return BaselineAnchor(t0=float(t0), y0=float(y0), method="mean_baseline", used_baseline_band=True)
            except Exception:
                pass

    # No baseline points; use closest_k weighted mean
    try:
        k = max(1, min(int(closest_k), len(pts)))
        closest = sorted(pts, key=lambda p: abs(float(p[0]) - float(target_f)))[:k]
        weights = [1.0 / (1.0 + abs(float(t) - float(target_f))) for (t, _y) in closest]
        w_sum = sum(weights)
        if w_sum > 0.0:
            t0 = sum(w * float(t) for w, (t, _y) in zip(weights, closest)) / w_sum
            y0 = sum(w * float(y) for w, (_t, y) in zip(weights, closest)) / w_sum
            return BaselineAnchor(t0=float(t0), y0=float(y0), method="closest_k", used_baseline_band=False)
    except Exception:
        pass

    # Mean of all points
    try:
        t0 = sum(t for t, _ in pts) / float(len(pts))
        y0 = sum(y for _, y in pts) / float(len(pts))
        return BaselineAnchor(t0=float(t0), y0=float(y0), method="mean_all", used_baseline_band=False)
    except Exception:
        return BaselineAnchor(t0=float(pts[0][0]), y0=float(pts[0][1]), method="first", used_baseline_band=False)


def estimate_slope(points: Sequence[Point], anchor: BaselineAnchor) -> tuple[float, int] | None:
    """
    Estimate anchored least-squares slope m in raw units:

      m = sum((ti - T0) * (yi - Y0)) / sum((ti - T0)^2)

    Returns (m, n_points_used) or None if denominator==0.
    """
    t0 = float(anchor.t0)
    y0 = float(anchor.y0)

    num = 0.0
    den = 0.0
    n = 0
    for (t, y) in points:
        try:
            tf = float(t)
            yf = float(y)
        except Exception:
            continue
        dt = tf - t0
        dy = yf - y0
        num += dt * dy
        den += dt * dt
        n += 1

    if n <= 0 or den == 0.0:
        return None

    m = num / den
    if m == m:  # not NaN
        return float(m), int(n)
    return None


def _rms(vals: Sequence[float]) -> float:
    try:
        if not vals:
            return 0.0
        s2 = 0.0
        n = 0
        for v in vals:
            try:
                fv = float(v)
            except Exception:
                continue
            s2 += fv * fv
            n += 1
        if n <= 0:
            return 0.0
        return float((s2 / float(n)) ** 0.5)
    except Exception:
        return 0.0


def estimate_coef(
    points: Sequence[Point],
    anchor: BaselineAnchor,
    *,
    normalization: Literal["y0", "rms_baseline"] = "y0",
    baseline_low_f: float = 74.0,
    baseline_high_f: float = 78.0,
) -> tuple[float, int] | None:
    """
    Estimate coefficient C (1/°F) from the anchored slope m:

      m = sum((ti - T0)(yi - Y0)) / sum((ti - T0)^2)

    Normalization options:
      - "y0":           C = -(m / Y0)        (signed baseline normalization; good for Z)
      - "rms_baseline": C = -(m / S)         (S is RMS magnitude of baseline-band y's; robust for X/Y)

    Returns (C, n_points_used) or None when denominator is unusable.
    """
    est = estimate_slope(points, anchor)
    if not est:
        return None
    m, n = est

    # Choose normalization scale
    scale = 0.0
    if normalization == "rms_baseline":
        baseline_ys = [float(y) for (t, y) in points if baseline_low_f <= float(t) <= baseline_high_f]
        scale = _rms(baseline_ys)
        if scale == 0.0:
            # Fallback: magnitude of Y0, then RMS of all y
            scale = abs(float(anchor.y0)) or _rms([float(y) for (_t, y) in points])
    else:
        scale = float(anchor.y0)

    if scale == 0.0:
        return None

    # Sign convention: backend expects the opposite sign for the scalar coefficients.
    c = -(float(m) / float(scale))
    if c == c:  # not NaN
        return float(c), int(n)
    return None


def estimate_coefs(
    points: Sequence[Point],
    anchor: BaselineAnchor,
    *,
    normalization: Literal["y0", "rms_baseline"] = "y0",
) -> List[float]:
    """
    Back-compat helper returning a single anchored-LS coefficient as a list.

    Callers that used to summarize(mean) of per-point coefs can keep working,
    while the underlying math matches the anchored least-squares definition.
    """
    est = estimate_coef(points, anchor, normalization=normalization)
    if not est:
        return []
    c, _n = est
    return [float(c)]


def coef_line_points(
    *,
    anchor: BaselineAnchor,
    coef: float,
    t_values: Sequence[float],
) -> List[Point]:
    """
    Generate points for plotting the coef model line:
      y(t) = Y0 * (1 - (T0 - t) * C)
    """
    t0 = float(anchor.t0)
    y0 = float(anchor.y0)
    c = float(coef)
    pts: List[Point] = []
    for t in t_values:
        try:
            tf = float(t)
        except Exception:
            continue
        dt = t0 - tf
        y = y0 * (1.0 - (dt * c))
        pts.append((tf, float(y)))
    return sorted(pts, key=lambda p: p[0])


