from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional, Tuple

from ... import config
from ...project_paths import data_dir
from ..analysis.temperature_analyzer import TemperatureAnalyzer
from ..repositories.test_file_repository import TestFileRepository
from ..temperature_processing_service import TemperatureProcessingService
from ..temperature_post_correction import apply_post_correction_to_run_data
from .eligibility import eligible_runs_by_device_and_temp
from .scoring import score_run_against_bias


def cache_path_for_plate_type(plate_type: str) -> str:
    pt = str(plate_type or "").strip() or "unknown"
    out_dir = os.path.join(data_dir("analysis"), "temp_coef_stage_split_reports")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"type{pt}-stage-split.cache.json")


def load_cached_summary(plate_type: str) -> Optional[dict]:
    path = cache_path_for_plate_type(plate_type)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as h:
            cached = json.load(h) or {}
        if not isinstance(cached, dict):
            return None
        summary = cached.get("summary")
        return dict(summary or {}) if isinstance(summary, dict) else None
    except Exception:
        return None


def save_cache(plate_type: str, *, signature: list, rows: int, errors: list, summary: Optional[dict]) -> None:
    path = cache_path_for_plate_type(plate_type)
    try:
        with open(path, "w", encoding="utf-8") as h:
            json.dump({"signature": signature, "rows": int(rows or 0), "errors": list(errors or []), "summary": summary}, h)
    except Exception:
        pass


def compute_c_and_k_from_stage_split_rows(rows: List[dict], *, fref_n: float) -> Optional[Tuple[float, float]]:
    observations: List[Tuple[float, float]] = []
    for r in rows or []:
        try:
            bw_f = float(r.get("body_weight_n") or 0.0)
            bw_c = r.get("best_bw_coef")
            if bw_f and bw_c is not None:
                observations.append((bw_f, float(bw_c)))
        except Exception:
            pass
        try:
            db_f = float(r.get("dumbbell_weight_n") or 0.0)
            db_c = r.get("best_db_coef")
            if db_f and db_c is not None:
                observations.append((db_f, float(db_c)))
        except Exception:
            pass

    if not observations:
        return None

    fref = float(fref_n or 0.0)
    if fref <= 0:
        return None

    coeffs = [c for (_f, c) in observations]
    c_mean = round((sum(coeffs) / float(len(coeffs))) / 0.0001) * 0.0001

    xs: List[float] = []
    deltas: List[float] = []
    for f_i, c_i in observations:
        x_i = (float(f_i) - fref) / fref
        xs.append(float(x_i))
        deltas.append(float(c_i) - float(c_mean))
    denom = sum(x * x for x in xs)
    k = (sum(x * dc for x, dc in zip(xs, deltas)) / denom) if denom else 0.0
    return float(c_mean), float(k)


def evaluate_unified_k_bias_metrics(
    *,
    repo: TestFileRepository,
    analyzer: TemperatureAnalyzer,
    processing: TemperatureProcessingService,
    plate_type: str,
    eval_entries: List[dict],
    c: float,
    k: float,
    status_cb: Callable[[dict], None] | None = None,
) -> Optional[dict]:
    """
    Evaluate bias-controlled metrics for the unified `c` plus post-correction `k`.
    Matches Top-3 aggregation semantics: score each test (stage=all), then average across tests,
    with eligibility requiring >=2 temps per device and >=2 eligible devices.
    """
    pt = str(plate_type or "").strip()
    if not pt:
        return None

    # Build a run-like list for eligibility filtering (device_id + temp_f).
    run_like = []
    for e in eval_entries or []:
        run_like.append({"device_id": e.get("device_id"), "temp_f": (e.get("meta") or {}).get("temp_f")})
    eligible_devices, _eligible, _temps = eligible_runs_by_device_and_temp(runs=run_like, min_distinct_temps_per_device=2)
    if eligible_devices < 2:
        return None

    fref = float(getattr(config, "TEMP_POST_CORRECTION_FREF_N", 550.0))
    ideal = float(getattr(config, "TEMP_IDEAL_ROOM_TEMP_F", 76.0))

    mean_abs_vals: List[float] = []
    mean_signed_vals: List[float] = []
    std_signed_vals: List[float] = []

    for entry in eval_entries or []:
        raw_csv = str(entry.get("raw_csv") or "")
        meta = dict(entry.get("meta") or {})
        bias_map = entry.get("bias_map") if isinstance(entry.get("bias_map"), list) else None
        device_id = str(entry.get("device_id") or "")
        if not raw_csv or not bias_map:
            continue

        temp_f = None
        try:
            temp_f = repo.extract_temperature_f(meta)
        except Exception:
            temp_f = None
        if temp_f is None:
            continue

        details = repo.get_temperature_test_details(raw_csv)
        baseline_path = ""
        selected_path = ""
        proc_runs = list((details or {}).get("processed_runs") or [])
        for r in proc_runs:
            if r.get("is_baseline") and not baseline_path:
                baseline_path = str(r.get("path") or "")
                continue
        for r in proc_runs:
            if r.get("is_baseline"):
                continue
            slopes = dict((r.get("slopes") or {}) if isinstance(r, dict) else {})
            try:
                rx = float(slopes.get("x", 0.0))
                ry = float(slopes.get("y", 0.0))
                rz = float(slopes.get("z", 0.0))
            except Exception:
                continue
            if f"{rx:.6f}" == f"{c:.6f}" and f"{ry:.6f}" == f"{c:.6f}" and f"{rz:.6f}" == f"{c:.6f}":
                selected_path = str(r.get("path") or "")
                break

        if not (baseline_path and selected_path and os.path.isfile(baseline_path) and os.path.isfile(selected_path)):
            processing.run_temperature_processing(
                folder=os.path.dirname(raw_csv),
                device_id=device_id,
                csv_path=raw_csv,
                slopes={"x": float(c), "y": float(c), "z": float(c)},
                room_temp_f=ideal,
                mode="scalar",
                status_cb=status_cb,
            )
            details = repo.get_temperature_test_details(raw_csv)
            proc_runs = list((details or {}).get("processed_runs") or [])
            baseline_path = ""
            selected_path = ""
            for r in proc_runs:
                if r.get("is_baseline") and not baseline_path:
                    baseline_path = str(r.get("path") or "")
                    continue
            for r in proc_runs:
                if r.get("is_baseline"):
                    continue
                slopes = dict((r.get("slopes") or {}) if isinstance(r, dict) else {})
                try:
                    rx = float(slopes.get("x", 0.0))
                    ry = float(slopes.get("y", 0.0))
                    rz = float(slopes.get("z", 0.0))
                except Exception:
                    continue
                if f"{rx:.6f}" == f"{c:.6f}" and f"{ry:.6f}" == f"{c:.6f}" and f"{rz:.6f}" == f"{c:.6f}":
                    selected_path = str(r.get("path") or "")
                    break

        if not (baseline_path and selected_path and os.path.isfile(baseline_path) and os.path.isfile(selected_path)):
            continue

        payload = analyzer.analyze_temperature_processed_runs(baseline_path, selected_path, meta)
        selected = payload.get("selected") or {}
        delta_t = float(temp_f) - ideal
        apply_post_correction_to_run_data(selected, delta_t_f=delta_t, k=float(k), fref_n=fref)

        grid = dict(payload.get("grid") or {})
        device_type = str(grid.get("device_type") or pt)
        body_weight_n = float((payload.get("meta") or {}).get("body_weight_n") or 0.0)
        s = score_run_against_bias(
            run_data=selected,
            stage_key="all",
            device_type=device_type,
            body_weight_n=body_weight_n,
            bias_map=bias_map,
        )
        if isinstance(s, dict) and s.get("n"):
            try:
                mean_abs_vals.append(float(s.get("mean_abs")))
            except Exception:
                pass
            try:
                mean_signed_vals.append(float(s.get("mean_signed")))
            except Exception:
                pass
            try:
                std_signed_vals.append(float(s.get("std_signed")))
            except Exception:
                pass

    def _avg(xs: List[float]) -> Optional[float]:
        return (sum(xs) / float(len(xs))) if xs else None

    mean_abs = _avg(mean_abs_vals)
    mean_signed = _avg(mean_signed_vals)
    std_signed = _avg(std_signed_vals)
    n = len(mean_abs_vals)
    return {"mean_abs": mean_abs, "mean_signed": mean_signed, "std_signed": std_signed, "n": n}

