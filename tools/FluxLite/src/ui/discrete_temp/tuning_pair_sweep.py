from __future__ import annotations

import json
import os
from typing import Callable, List, Optional, Tuple

from .tuning_core import (
    TuningCancelled,
    compute_baseline_targets_from_off,
    fmt_coef_tag,
    now_ms,
    run_backend_candidate,
    run_backend_off_baseline,
    score_candidate_against_targets,
    write_run_meta,
)


def _grid_from_max(max_v: float, step: float) -> List[float]:
    max_v = float(max_v)
    step_v = float(step)
    if step_v <= 0:
        step_v = 0.001
    n = int(round(max_v / step_v))
    return [round(step_v * i, 7) for i in range(0, n + 1)]


def _grid_from_origin(origin: float, offset_max: float, offset_step: float) -> List[float]:
    """
    Create a precise grid around an origin using symmetric offsets:
      origin ± [0 .. offset_max] in steps of offset_step

    Values are clamped to be >= 0.
    """
    origin = float(origin)
    offset_max = max(0.0, float(offset_max))
    offset_step = float(offset_step)
    if offset_step <= 0:
        offset_step = 0.0001
    n = int(round(offset_max / offset_step))

    vals: List[float] = []
    for i in range(-n, n + 1):
        vals.append(round(max(0.0, origin + (offset_step * float(i))), 7))
    return sorted(set(vals))


def run_pair_sweep_tuning(
    *,
    input_csv_path: str,
    device_id: str,
    test_folder: str,
    add_runs: int,
    hardware: object | None = None,
    room_temp_f: float = 76.0,
    timeout_s: int = 300,
    sanitize_header: bool = True,
    baseline_low_f: float = 74.0,
    baseline_high_f: float = 78.0,
    x_max: float = 0.005,
    y_max: float = 0.005,
    z_max: float = 0.008,
    step: float = 0.001,
    stop_after_worse: int = 2,
    score_axes: Tuple[str, ...] = ("z",),
    score_weights: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    progress_cb: Callable[[dict], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    precise_origin_coeffs: dict | None = None,
    precise_offset_max: float = 0.001,
    precise_offset_step: float = 0.0001,
) -> dict:
    from .tuning import tuning_folder_for_test  # avoid circular import at module load

    def _check_cancel() -> None:
        if cancel_cb is not None:
            try:
                if bool(cancel_cb()):
                    raise TuningCancelled()
            except TuningCancelled:
                raise
            except Exception:
                return

    tuning_dir = tuning_folder_for_test(test_folder)
    runs_dir = os.path.join(tuning_dir, "runs")
    os.makedirs(runs_dir, exist_ok=True)

    off_path = os.path.join(tuning_dir, "baseline__nn_off.csv")
    if not os.path.isfile(off_path):
        _check_cancel()
        off_path = run_backend_off_baseline(
            input_csv_path=input_csv_path,
            device_id=device_id,
            output_folder=tuning_dir,
            room_temp_f=float(room_temp_f),
            timeout_s=int(timeout_s),
            sanitize_header=bool(sanitize_header),
            hardware=hardware,
        )

    targets = compute_baseline_targets_from_off(
        off_path, baseline_low_f=float(baseline_low_f), baseline_high_f=float(baseline_high_f)
    )
    baseline_score = score_candidate_against_targets(
        off_path,
        targets,
        baseline_low_f=float(baseline_low_f),
        baseline_high_f=float(baseline_high_f),
        axes=tuple(score_axes),
        weights=tuple(score_weights),
    ).total

    def _key(cx: float, cy: float, cz: float) -> tuple[float, float, float]:
        return (round(float(cx), 7), round(float(cy), 7), round(float(cz), 7))

    if isinstance(precise_origin_coeffs, dict) and precise_origin_coeffs:
        ox = float(precise_origin_coeffs.get("x") or 0.0)
        oy = float(precise_origin_coeffs.get("y") or 0.0)
        oz = float(precise_origin_coeffs.get("z") or 0.0)
        x_vals = _grid_from_origin(ox, precise_offset_max, precise_offset_step)
        y_vals = _grid_from_origin(oy, precise_offset_max, precise_offset_step)
        z_vals = _grid_from_origin(oz, precise_offset_max, precise_offset_step)
        tuning_mode = "precise"
        origin_by_axis = {"x": round(max(0.0, ox), 7), "y": round(max(0.0, oy), 7), "z": round(max(0.0, oz), 7)}
    else:
        x_vals = _grid_from_max(x_max, step)
        y_vals = _grid_from_max(y_max, step)
        z_vals = _grid_from_max(z_max, step)
        tuning_mode = "coarse"
        origin_by_axis = {}
    axis_max_by_axis = {"x": float(x_max), "y": float(y_max), "z": float(z_max)}

    best_score = float("inf")
    best_coeffs: Optional[dict] = None
    best_csv_path = ""
    run_count = 0
    score_cache: dict[tuple[float, float, float], float] = {}

    def _maybe_update_best_from_meta(meta: dict) -> None:
        nonlocal best_score, best_coeffs, best_csv_path
        try:
            s = float(meta.get("score_total") or float("inf"))
            c = meta.get("coeffs") or {}
            if s < best_score and isinstance(c, dict):
                best_score = s
                best_coeffs = dict(c)
                best_csv_path = str(meta.get("output_csv") or "")
        except Exception:
            return

    try:
        for fn in os.listdir(runs_dir):
            if not fn.lower().endswith(".json") or (not fn.lower().startswith("run_")):
                continue
            try:
                with open(os.path.join(runs_dir, fn), "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
                idx = int(meta.get("run_index") or 0)
                if idx > run_count:
                    run_count = idx
                coeffs = meta.get("coeffs") or {}
                if isinstance(coeffs, dict):
                    k = _key(coeffs.get("x", 0.0), coeffs.get("y", 0.0), coeffs.get("z", 0.0))
                    score_cache[k] = float(meta.get("score_total") or float("inf"))
                _maybe_update_best_from_meta(meta)
            except Exception:
                pass
    except Exception:
        pass

    try:
        best_json_path = os.path.join(tuning_dir, "best.json")
        if os.path.isfile(best_json_path):
            with open(best_json_path, "r", encoding="utf-8") as f:
                b = json.load(f) or {}
            s = float(b.get("best_score_total") or float("inf"))
            c = b.get("best_coeffs") or {}
            if s < best_score and isinstance(c, dict):
                best_score = s
                best_coeffs = dict(c)
                best_csv_path = str(b.get("best_output_csv") or "")
    except Exception:
        pass

    new_runs = 0
    target_new = max(1, int(add_runs))
    cancelled = False

    def _eval(coeffs: dict, *, pair_id: str | None = None) -> Optional[float]:
        nonlocal run_count, best_score, best_coeffs, best_csv_path, new_runs
        _check_cancel()
        k = _key(coeffs["x"], coeffs["y"], coeffs["z"])
        if k in score_cache:
            return float(score_cache[k])
        if new_runs >= target_new:
            return None

        new_runs += 1
        run_count += 1
        tag = f"{fmt_coef_tag(coeffs['x'])}_{fmt_coef_tag(coeffs['y'])}_{fmt_coef_tag(coeffs['z'])}"
        out_csv = f"run_{run_count:03d}__nn_scalar_{tag}.csv"
        out_path = run_backend_candidate(
            input_csv_path=input_csv_path,
            device_id=device_id,
            output_folder=runs_dir,
            output_filename=out_csv,
            coeffs=dict(coeffs),
            room_temp_f=float(room_temp_f),
            timeout_s=int(timeout_s),
            sanitize_header=bool(sanitize_header),
            hardware=hardware,
        )
        _check_cancel()
        score = score_candidate_against_targets(
            out_path,
            targets,
            baseline_low_f=float(baseline_low_f),
            baseline_high_f=float(baseline_high_f),
            axes=tuple(score_axes),
            weights=tuple(score_weights),
        )
        s = float(score.total)
        score_cache[k] = s
        if s < best_score:
            best_score = s
            best_coeffs = dict(coeffs)
            best_csv_path = str(out_path)

        meta = {
            "run_index": run_count,
            "device_id": device_id,
            "coeffs": dict(coeffs),
            "pair_id": str(pair_id or ""),
            "score_total": s,
            "score_per_phase_axis_mse": score.per_phase_axis_mse,
            "baseline_off_csv": str(off_path),
            "baseline_targets": targets,
            "baseline_score_total": float(baseline_score),
            "output_csv": str(out_path),
            "tuning_mode": tuning_mode,
            "created_at_ms": now_ms(),
        }
        write_run_meta(os.path.join(runs_dir, f"run_{run_count:03d}.json"), meta)
        if progress_cb is not None:
            try:
                progress_cb(
                    {
                        "event": "run_complete",
                        "run": {
                            "run_index": int(run_count),
                            "score_total": float(s),
                            "coeffs": dict(coeffs),
                            "output_csv": str(out_path),
                            "tuning_mode": tuning_mode,
                            "created_at_ms": int(meta.get("created_at_ms") or 0),
                        },
                        "best_score": float(best_score),
                        "best_coeffs": dict(best_coeffs or {}),
                        "best_output_csv": str(best_csv_path),
                        "tuning_mode": tuning_mode,
                    }
                )
            except Exception:
                pass
        return s

    pairs_total = (len(x_vals) * len(y_vals)) + (len(x_vals) * len(z_vals)) + (len(y_vals) * len(z_vals))
    pairs_done = 0

    def _emit(current_score: float) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(
                {
                    "pairs_done": int(pairs_done),
                    "pairs_total": int(pairs_total),
                    "current_score": float(current_score),
                    "best_score": float(best_score),
                    "best_coeffs": dict(best_coeffs or {}),
                    "best_output_csv": str(best_csv_path),
                    "tuning_mode": tuning_mode,
                }
            )
        except Exception:
            return

    def _sweep(third: str, fixed_a: str, fixed_b: str, a_vals: List[float], b_vals: List[float], third_vals: List[float]) -> None:
        nonlocal pairs_done, best_score, best_coeffs
        for va in a_vals:
            _check_cancel()
            if new_runs >= target_new:
                return
            for vb in b_vals:
                _check_cancel()
                if new_runs >= target_new:
                    return
                best_local = float("inf")
                best_local_coeffs: Optional[dict] = None
                worse_limit = max(1, int(stop_after_worse))

                def _try_v3(v3: float, *, worse_streak: int) -> tuple[bool, int]:
                    _check_cancel()
                    if new_runs >= target_new:
                        return (False, worse_streak)
                    cand = {"x": 0.0, "y": 0.0, "z": 0.0}
                    cand[fixed_a] = float(va)
                    cand[fixed_b] = float(vb)
                    cand[third] = float(v3)
                    pid = None
                    if fixed_a == "x" and fixed_b == "y":
                        pid = f"xy:{round(float(va),7):.7f},{round(float(vb),7):.7f}"
                    elif fixed_a == "x" and fixed_b == "z":
                        pid = f"xz:{round(float(va),7):.7f},{round(float(vb),7):.7f}"
                    elif fixed_a == "y" and fixed_b == "z":
                        pid = f"yz:{round(float(va),7):.7f},{round(float(vb),7):.7f}"
                    s = _eval(cand, pair_id=pid)
                    if s is None:
                        return (False, worse_streak)
                    s = float(s)
                    nonlocal best_local, best_local_coeffs
                    if s < best_local:
                        best_local = s
                        best_local_coeffs = dict(cand)
                        return (True, 0)
                    worse_streak += 1
                    if worse_streak >= worse_limit:
                        return (False, worse_streak)
                    return (True, worse_streak)

                if tuning_mode == "precise":
                    origin_v = float(origin_by_axis.get(third, 0.0))
                    origin_v = round(max(0.0, origin_v), 7)
                    step_p = round(float(precise_offset_step if precise_offset_step else 0.0001), 7)
                    if step_p <= 0:
                        step_p = 0.0001
                    axis_max = float(axis_max_by_axis.get(third, 0.0))
                    axis_max = max(0.0, axis_max)

                    def _score_at(v3: float) -> Optional[float]:
                        _check_cancel()
                        if new_runs >= target_new:
                            return None
                        cand = {"x": 0.0, "y": 0.0, "z": 0.0}
                        cand[fixed_a] = float(va)
                        cand[fixed_b] = float(vb)
                        cand[third] = float(v3)
                        s = _eval(cand)
                        if s is None:
                            return None
                        s = float(s)
                        nonlocal best_local, best_local_coeffs
                        if s < best_local:
                            best_local = s
                            best_local_coeffs = dict(cand)
                        return s

                    def _run_direction(start_v: float, step_delta: float, *, clamp_min0: bool, clamp_max: bool) -> None:
                        worse_streak = 0
                        s0 = _score_at(start_v)
                        if s0 is None:
                            return
                        best_dir = float(s0)
                        v3 = round(start_v + step_delta, 7)
                        while True:
                            if clamp_min0 and v3 < -1e-12:
                                break
                            v_eval = v3
                            if clamp_min0:
                                v_eval = round(max(0.0, v_eval), 7)
                            if clamp_max and v_eval > axis_max + 1e-12:
                                break
                            s = _score_at(v_eval)
                            if s is None:
                                return
                            s = float(s)
                            if s < best_dir:
                                best_dir = s
                                worse_streak = 0
                            else:
                                worse_streak += 1
                                if worse_streak >= worse_limit:
                                    break
                            v3 = round(v3 + step_delta, 7)

                    _run_direction(origin_v, step_p, clamp_min0=False, clamp_max=True)
                    _run_direction(origin_v, -step_p, clamp_min0=True, clamp_max=False)
                else:
                    worse_streak = 0
                    for v3 in third_vals:
                        cont, worse_streak = _try_v3(v3, worse_streak=worse_streak)
                        if not cont:
                            break

                pairs_done += 1
                if best_local_coeffs is not None and best_local < best_score:
                    best_score = float(best_local)
                    best_coeffs = dict(best_local_coeffs)
                _emit(best_local)

    try:
        _sweep("z", "x", "y", x_vals, y_vals, z_vals)
        _sweep("y", "x", "z", x_vals, z_vals, y_vals)
        _sweep("x", "y", "z", y_vals, z_vals, x_vals)
    except TuningCancelled:
        cancelled = True

    best_payload = {
        "device_id": device_id,
        "best_coeffs": dict(best_coeffs or {}),
        "best_score_total": float(best_score),
        "best_output_csv": str(best_csv_path),
        "baseline_off_csv": str(off_path),
        "baseline_targets": targets,
        "baseline_score_total": float(baseline_score),
        "budget": int(target_new),
        "completed_runs": int(run_count),
        "pairs_total": int(pairs_total),
        "pairs_done": int(pairs_done),
        "tuning_mode": tuning_mode,
        "cancelled": bool(cancelled),
        "created_at_ms": now_ms(),
    }
    write_run_meta(os.path.join(tuning_dir, "best.json"), best_payload)
    return best_payload


