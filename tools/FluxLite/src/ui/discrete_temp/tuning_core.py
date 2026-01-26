from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from ...app_services.backend_csv_processor import process_csv_via_backend

Point = Tuple[float, float]  # (temperature_f, value)


@dataclass(frozen=True)
class TuneScore:
    total: float
    per_phase_axis_mse: Dict[str, Dict[str, float]]  # phase -> axis -> mse


class TuningCancelled(Exception):
    pass


def _mean(vals: Iterable[float]) -> float:
    vals = list(vals)
    if not vals:
        return 0.0
    return float(sum(vals) / float(len(vals)))


def _read_sum_points(csv_path: str, phase_name: str, axis: str) -> List[Point]:
    """
    Read (sum-t, sum-{axis}) points for a given phase from a discrete-temp-style CSV.
    """
    if not csv_path or (not os.path.isfile(csv_path)) or os.path.getsize(csv_path) <= 0:
        return []

    axis = str(axis or "").strip().lower()
    if axis not in ("x", "y", "z"):
        return []

    out: List[Point] = []
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            # Normalize headers (strip whitespace/BOM) for safety
            header = next(csv.reader([f.readline()]), [])
            headers = [(h or "").lstrip("\ufeff").strip() for h in header]
            reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)
            y_key = f"sum-{axis}"
            for row in reader:
                try:
                    if str(row.get("phase") or "").strip().lower() != str(phase_name).strip().lower():
                        continue
                    t = float(row.get("sum-t") or 0.0)
                    y = float(row.get(y_key) or 0.0)
                except Exception:
                    continue
                out.append((float(t), float(y)))
    except Exception:
        return []

    out.sort(key=lambda p: p[0])
    return out


def compute_baseline_targets_from_off(
    off_csv_path: str,
    *,
    baseline_low_f: float = 74.0,
    baseline_high_f: float = 78.0,
) -> Dict[str, Dict[str, float]]:
    """
    Compute baseline target values Y0^{off}_{phase,axis} from the temp-correction-off CSV.
    Uses mean of all points in the baseline band.
    """
    targets: Dict[str, Dict[str, float]] = {"45lb": {}, "bodyweight": {}}
    for phase in ("45lb", "bodyweight"):
        for axis in ("x", "y", "z"):
            pts = _read_sum_points(off_csv_path, phase, axis)
            baseline_ys = [y for (t, y) in pts if baseline_low_f <= t <= baseline_high_f]
            if not baseline_ys:
                # Fallback: if no baseline-band points exist, fall back to mean of all points
                baseline_ys = [y for (_t, y) in pts]
            targets[phase][axis] = float(_mean(baseline_ys)) if baseline_ys else 0.0
    return targets


def score_candidate_against_targets(
    candidate_csv_path: str,
    baseline_targets: Dict[str, Dict[str, float]],
    *,
    baseline_low_f: float = 74.0,
    baseline_high_f: float = 78.0,
    axes: Tuple[str, ...] = ("x", "y", "z"),
    weights: Tuple[float, float, float] = (1.0, 1.0, 5.0),  # (x, y, z)
) -> TuneScore:
    """
    Score candidate processed output vs baseline targets, excluding baseline-band points.
    Only scores sum axes and both phases.
    """
    wx, wy, wz = float(weights[0]), float(weights[1]), float(weights[2])
    w_by_axis = {"x": wx, "y": wy, "z": wz}

    per: Dict[str, Dict[str, float]] = {"45lb": {}, "bodyweight": {}}
    total = 0.0
    for phase in ("45lb", "bodyweight"):
        for axis in ("x", "y", "z"):
            if axis not in set([str(a).strip().lower() for a in (axes or ())]):
                continue
            target = float((baseline_targets or {}).get(phase, {}).get(axis, 0.0))
            pts = _read_sum_points(candidate_csv_path, phase, axis)
            eval_pts = [(t, y) for (t, y) in pts if (t < baseline_low_f) or (t > baseline_high_f)]
            if not eval_pts:
                mse = float("inf")
            else:
                mse = float(_mean([(y - target) ** 2 for (_t, y) in eval_pts]))
            per[phase][axis] = mse
            total += w_by_axis[axis] * mse

    return TuneScore(total=float(total), per_phase_axis_mse=per)


def run_backend_off_baseline(
    *,
    input_csv_path: str,
    device_id: str,
    output_folder: str,
    room_temp_f: float = 76.0,
    timeout_s: int = 300,
    sanitize_header: bool = True,
    hardware: object | None = None,
) -> str:
    """
    Generate a temp-correction-off reference CSV used to compute baseline targets.
    """
    os.makedirs(output_folder, exist_ok=True)
    return process_csv_via_backend(
        input_csv_path=input_csv_path,
        device_id=device_id,
        output_folder=output_folder,
        output_filename="baseline__nn_off.csv",
        use_temperature_correction=False,
        room_temp_f=float(room_temp_f),
        mode="scalar",
        temperature_coefficients=None,
        sanitize_header=bool(sanitize_header),
        hardware=hardware,
        timeout_s=int(timeout_s),
    )


def run_backend_candidate(
    *,
    input_csv_path: str,
    device_id: str,
    output_folder: str,
    output_filename: str,
    coeffs: Dict[str, float],
    room_temp_f: float = 76.0,
    timeout_s: int = 300,
    sanitize_header: bool = True,
    hardware: object | None = None,
) -> str:
    os.makedirs(output_folder, exist_ok=True)
    return process_csv_via_backend(
        input_csv_path=input_csv_path,
        device_id=device_id,
        output_folder=output_folder,
        output_filename=output_filename,
        use_temperature_correction=True,
        room_temp_f=float(room_temp_f),
        mode="scalar",
        temperature_coefficients=dict(coeffs),
        sanitize_header=bool(sanitize_header),
        hardware=hardware,
        timeout_s=int(timeout_s),
    )


def write_run_meta(path: str, payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
    except Exception:
        pass


def fmt_coef_tag(v: float) -> str:
    try:
        # Short + stable for filenames
        return f"{float(v):.6f}".rstrip("0").rstrip(".")
    except Exception:
        return "0"


def now_ms() -> int:
    return int(time.time() * 1000)


