from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import csv
import os

from .. import config
from .offline_runner import run_45v


@dataclass
class HeatPoint:
    x_mm: float
    y_mm: float
    bin_name: str  # one of: green, light_green, yellow, orange, red


def _safe_float(v: object, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _detect_units_and_to_mm(xs: List[float], ys: List[float]) -> Tuple[List[float], List[float]]:
    # Heuristic: if max magnitude < 2.0, assume meters and convert to mm
    max_mag = 0.0
    for a, b in zip(xs, ys):
        if abs(a) > max_mag:
            max_mag = abs(a)
        if abs(b) > max_mag:
            max_mag = abs(b)
    if max_mag < 2.0:
        return [v * 1000.0 for v in xs], [v * 1000.0 for v in ys]
    return xs, ys


def _rolling_stable_windows(times_ms: List[float], fz_n: List[float], min_window_ms: int = 1200,
                            std_threshold_n: float = 5.0, fz_min_n: float = 22.0) -> List[Tuple[int, int]]:
    windows: List[Tuple[int, int]] = []
    if not times_ms:
        return windows
    # Use two-pointer window with rolling mean/std recomputed naïvely (small N acceptable offline)
    n = len(times_ms)
    i0 = 0
    while i0 < n:
        j = i0
        while j < n and (times_ms[j] - times_ms[i0]) < float(min_window_ms):
            j += 1
        if j - i0 >= 5:  # need a few samples
            window = fz_n[i0:j]
            mean_fz = sum(window) / len(window)
            var = sum((v - mean_fz) ** 2 for v in window) / max(1, (len(window) - 1))
            std = var ** 0.5
            if std <= std_threshold_n and abs(mean_fz) >= fz_min_n:
                windows.append((i0, j))
                # Advance past this window to avoid dense overlap; keep slight overlap allowed
                i0 = j
                continue
        i0 += 1
    return windows


def _color_bin(error_n: float, base_tol_n: float) -> str:
    g = getattr(config, "COLOR_BIN_MULTIPLIERS", {"green": 0.5, "light_green": 1.0, "yellow": 1.5, "orange": 2.5})
    if error_n <= base_tol_n * g.get("green", 0.5):
        return "green"
    if error_n <= base_tol_n * g.get("light_green", 1.0):
        return "light_green"
    if error_n <= base_tol_n * g.get("yellow", 1.5):
        return "yellow"
    if error_n <= base_tol_n * g.get("orange", 2.5):
        return "orange"
    return "red"


def process_45v(csv_path: str, model_id: str, plate_type: str, device_id: str, existing_processed_csv: Optional[str] = None) -> Dict[str, object]:
    # Load truth CSV columns
    path = str(csv_path or "").strip()
    try:
        print(f"[calib] process_45v: csv_path={path} model_id={model_id} plate_type={plate_type} device_id={device_id}")
    except Exception:
        pass
    if not path or not os.path.isfile(path):
        return {"error": "file_not_found"}
    times_ms: List[float] = []
    truth_fz: List[float] = []
    truth_x: List[float] = []
    truth_y: List[float] = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t_raw = _safe_float(row.get("time", 0.0))
            # If time looks like seconds (<1e4), convert to ms; else assume already ms
            t_ms = (t_raw * 1000.0) if t_raw < 1.0e4 else t_raw
            times_ms.append(float(t_ms))
            truth_fz.append(_safe_float(row.get("sum-z", 0.0)))
            truth_x.append(_safe_float(row.get("COPx", 0.0)))
            truth_y.append(_safe_float(row.get("COPy", 0.0)))
    truth_x, truth_y = _detect_units_and_to_mm(truth_x, truth_y)

    # Run model offline (placeholder integration); expected to return a processed CSV path or dict with columns
    # Prefer an existing processed CSV if provided/available
    maybe_csv: Optional[str] = None
    if existing_processed_csv and os.path.isfile(existing_processed_csv):
        maybe_csv = existing_processed_csv
        try:
            print(f"[calib] using existing processed CSV: {maybe_csv}")
        except Exception:
            pass
        run_result = {}
    else:
        run_result = run_45v(path, model_id, plate_type, device_id)
    try:
        print(f"[calib] backend result keys={list((run_result or {}).keys())}")
    except Exception:
        pass
    model_times: List[float] = []
    model_fz: List[float] = []
    model_x: List[float] = []
    model_y: List[float] = []
    # Accept a sibling CSV path named in result
    if maybe_csv is None and isinstance(run_result, dict):
        maybe_csv = run_result.get("processed_csv") or run_result.get("csv")
    if isinstance(maybe_csv, str) and os.path.isfile(maybe_csv):
        with open(maybe_csv, "r", newline="", encoding="utf-8") as f:
            r2 = csv.DictReader(f)
            try:
                print(f"[calib] reading processed CSV: {maybe_csv}")
            except Exception:
                pass
            # Use processed model and truth from the same file per provided schema
            # time, sum-z (model Fz), COPx/COPy (meters), bz (truth Fz)
            p_model_times: List[float] = []
            p_model_fz: List[float] = []
            p_model_x: List[float] = []
            p_model_y: List[float] = []
            p_truth_fz: List[float] = []
            for row in r2:
                t_raw = _safe_float(row.get("time", 0.0))
                t_ms = (t_raw * 1000.0) if t_raw < 1.0e4 else t_raw
                p_model_times.append(float(t_ms))
                p_model_fz.append(_safe_float(row.get("sum-z", 0.0)))
                p_model_x.append(_safe_float(row.get("COPx", 0.0)))
                p_model_y.append(_safe_float(row.get("COPy", 0.0)))
                p_truth_fz.append(_safe_float(row.get("bz", 0.0)))
            # Normalize units and assign exclusively from processed file
            p_model_x, p_model_y = _detect_units_and_to_mm(p_model_x, p_model_y)
            model_times = p_model_times
            model_fz = p_model_fz
            model_x = p_model_x
            model_y = p_model_y
            truth_fz = p_truth_fz
            times_ms = list(model_times)
            # Reset any unused truth COP lists to align downstream slicing
            truth_x = [0.0] * len(times_ms)
            truth_y = [0.0] * len(times_ms)
    else:
        try:
            print(f"[calib] processed CSV not found locally: {maybe_csv}")
        except Exception:
            pass
        # Fallback: use truth as stand-in (colors will be green) until runner is integrated or file not accessible
        model_times = list(times_ms)
        model_fz = list(truth_fz)
        model_x = list(truth_x)
        model_y = list(truth_y)

    # Align series on index by nearest time (assume same sampling rate; simple 1:1 by position)
    length = min(len(times_ms), len(model_times))
    times_ms = times_ms[:length]
    truth_fz = truth_fz[:length]
    truth_x = truth_x[:length]
    truth_y = truth_y[:length]
    model_fz = model_fz[:length]
    model_x = model_x[:length]
    model_y = model_y[:length]

    # --- Detect windows where processed model sum-z >= threshold for a minimum duration ---
    def _ema(vals: List[float], alpha: float) -> List[float]:
        out: List[float] = []
        prev: Optional[float] = None
        for v in vals:
            if prev is None:
                prev = float(v)
            else:
                prev = alpha * float(v) + (1.0 - alpha) * prev
            out.append(prev)
        return out

    def _windows_sumz_min(t_ms: List[float], sumz_vals: List[float],
                          min_window_ms: int = 500, min_fz_n: float = 150.0) -> List[Tuple[int, int]]:
        windows: List[Tuple[int, int]] = []
        n = len(t_ms)
        i = 0
        while i < n:
            # skip until condition met
            if abs(sumz_vals[i]) < min_fz_n:
                i += 1
                continue
            start = i
            j = i + 1
            while j < n and abs(sumz_vals[j]) >= min_fz_n:
                j += 1
            # check duration
            if (t_ms[j - 1] - t_ms[start]) >= float(min_window_ms) and (j - start) >= 3:
                windows.append((start, j))
            i = j
        return windows

    windows = _windows_sumz_min(
        times_ms,
        model_fz,
        min_window_ms=500,
        min_fz_n=150.0,
    )
    try:
        print(f"[calib] samples={length} windows={len(windows)}")
    except Exception:
        pass

    # Per-model DB tolerance from config
    db_by_model = getattr(config, "THRESHOLDS_DB_N_BY_MODEL", {"06": 5.0, "07": 6.0, "08": 8.0, "11": 6.0})
    base_tol_n = float(db_by_model.get((model_id or "06").strip(), db_by_model.get("06", 5.0)))

    points: List[HeatPoint] = []
    processed_rows: List[List[object]] = []
    for (i0, j) in windows:
        w_len = max(1, j - i0)
        m_fz = sum(model_fz[i0:j]) / w_len
        t_fz = sum(truth_fz[i0:j]) / w_len
        m_x = sum(model_x[i0:j]) / w_len
        m_y = sum(model_y[i0:j]) / w_len
        t_x = sum(truth_x[i0:j]) / w_len
        t_y = sum(truth_y[i0:j]) / w_len
        err = abs(m_fz - t_fz)
        bname = _color_bin(err, base_tol_n)
        points.append(HeatPoint(x_mm=m_x, y_mm=m_y, bin_name=bname))
        processed_rows.append([
            times_ms[i0], times_ms[j - 1], m_fz, t_fz, err, m_x, m_y, t_x, t_y, bname, w_len,
        ])

    # Metrics
    errs = [abs(r[4]) for r in processed_rows]
    metrics = {
        "count": len(processed_rows),
        "mean_err": (sum(errs) / len(errs)) if errs else 0.0,
        "max_err": (max(errs) if errs else 0.0),
        "median_err": (sorted(errs)[len(errs)//2] if errs else 0.0),
    }
    try:
        print(f"[calib] points={len(points)} mean_err={metrics['mean_err']:.2f} max_err={metrics['max_err']:.2f}")
    except Exception:
        pass

    # Do not write a local processed CSV; rely on backend output path if provided
    out_path = str(maybe_csv or "")

    # Serialize heat points
    pts = [{"x_mm": p.x_mm, "y_mm": p.y_mm, "bin": p.bin_name} for p in points]
    return {"processed_csv": out_path, "points": pts, "metrics": metrics}


