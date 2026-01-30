from __future__ import annotations

from typing import Optional
import csv
import os


def _safe_float(v: object, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _detect_units_and_to_mm(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    max_mag = 0.0
    for a, b in zip(xs, ys):
        if abs(a) > max_mag:
            max_mag = abs(a)
        if abs(b) > max_mag:
            max_mag = abs(b)
    if max_mag < 2.0:
        return [v * 1000.0 for v in xs], [v * 1000.0 for v in ys]
    return xs, ys


def run_45v_model(input_csv: str, model_id: str, plate_type: str, output_csv: Optional[str] = None) -> str:
    """
    Model runner entrypoint for 45V calibration.

    For now, this pass-through maps truth columns to the expected model output schema.
    Replace internals with your actual model inference.

    Returns the path to the generated processed CSV with columns:
      time (ms), fz (N), copx_mm (mm), copy_mm (mm)
    """
    src = str(input_csv or "").strip()
    if not src or not os.path.isfile(src):
        raise FileNotFoundError(f"input_csv not found: {src}")
    # Choose default output path near the source
    if not output_csv:
        out_dir = os.path.join(os.path.dirname(src), "calibration_output")
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(src))[0]
        output_csv = os.path.join(out_dir, f"{base}_modelout_45V.csv")

    times: list[int] = []
    fz: list[float] = []
    cx: list[float] = []
    cy: list[float] = []
    with open(src, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            times.append(int(_safe_float(row.get("time", 0.0))))
            fz.append(_safe_float(row.get("sum-z", 0.0)))
            cx.append(_safe_float(row.get("COPx", 0.0)))
            cy.append(_safe_float(row.get("COPy", 0.0)))

    # Convert COP to mm if needed
    cx, cy = _detect_units_and_to_mm(cx, cy)

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "fz", "copx_mm", "copy_mm"])
        for t, z, x, y in zip(times, fz, cx, cy):
            w.writerow([int(t), float(z), float(x), float(y)])

    return output_csv


