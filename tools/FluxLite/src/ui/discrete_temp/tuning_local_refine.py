from __future__ import annotations

import json
import os
from typing import Callable, Optional, Tuple

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


def run_local_refine_tuning(
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
    refine_step: float = 0.0001,
    stop_after_worse: int = 1,
    score_axes: Tuple[str, ...] = ("z",),
    score_weights: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    progress_cb: Callable[[dict], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    start_coeffs: dict | None = None,
) -> dict:
    def _check_cancel() -> None:
        if cancel_cb is not None:
            try:
                if bool(cancel_cb()):
                    raise TuningCancelled()
            except TuningCancelled:
                raise
            except Exception:
                return

    tuning_dir = os.path.join(str(test_folder), "tuning")
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

    axis_max = {"x": float(x_max), "y": float(y_max), "z": float(z_max)}
    step_v = float(refine_step)
    if step_v <= 0:
        step_v = 0.0001
    worse_limit = max(1, int(stop_after_worse))

    ref = {"x": 0.0, "y": 0.0, "z": 0.0}
    if isinstance(best_coeffs, dict) and best_coeffs:
        ref.update(
            {
                "x": float(best_coeffs.get("x") or 0.0),
                "y": float(best_coeffs.get("y") or 0.0),
                "z": float(best_coeffs.get("z") or 0.0),
            }
        )
    if isinstance(start_coeffs, dict) and start_coeffs:
        ref.update(
            {
                "x": float(start_coeffs.get("x") or ref["x"]),
                "y": float(start_coeffs.get("y") or ref["y"]),
                "z": float(start_coeffs.get("z") or ref["z"]),
            }
        )

    new_runs = 0
    target_new = max(1, int(add_runs))
    cancelled = False

    def _emit_status(msg: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(
                {
                    "event": "refine_status",
                    "message": str(msg),
                    "best_score": float(best_score),
                    "best_coeffs": dict(best_coeffs or {}),
                    "best_output_csv": str(best_csv_path),
                    "tuning_mode": "local_refine",
                }
            )
        except Exception:
            pass

    def _eval(coeffs: dict, *, tag: str) -> Optional[float]:
        nonlocal run_count, best_score, best_coeffs, best_csv_path, new_runs
        _check_cancel()
        k = _key(coeffs["x"], coeffs["y"], coeffs["z"])
        if k in score_cache:
            return float(score_cache[k])
        if new_runs >= target_new:
            return None
        new_runs += 1
        run_count += 1
        out_tag = f"{fmt_coef_tag(coeffs['x'])}_{fmt_coef_tag(coeffs['y'])}_{fmt_coef_tag(coeffs['z'])}"
        out_csv = f"run_{run_count:03d}__nn_scalar_{out_tag}.csv"
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
            "pair_id": f"refine:{tag}",
            "score_total": s,
            "score_per_phase_axis_mse": score.per_phase_axis_mse,
            "baseline_off_csv": str(off_path),
            "baseline_targets": targets,
            "baseline_score_total": float(baseline_score),
            "output_csv": str(out_path),
            "tuning_mode": "local_refine",
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
                            "tuning_mode": "local_refine",
                            "created_at_ms": int(meta.get("created_at_ms") or 0),
                        },
                        "best_score": float(best_score),
                        "best_coeffs": dict(best_coeffs or {}),
                        "best_output_csv": str(best_csv_path),
                        "tuning_mode": "local_refine",
                        "runs_new": int(new_runs),
                        "budget": int(target_new),
                    }
                )
            except Exception:
                pass
        return s

    start_key = _key(ref["x"], ref["y"], ref["z"])
    if start_key in score_cache:
        ref_score = float(score_cache[start_key])
    else:
        _emit_status("Refine: evaluating start point…")
        s0 = _eval(dict(ref), tag="start")
        ref_score = float("inf") if s0 is None else float(s0)

    ref_best = dict(ref)
    ref_best_score = float(ref_score)

    axis_order = ("x", "y", "z")
    nudge_axis_i = 0

    try:
        while new_runs < target_new:
            _check_cancel()
            prev_new_runs = int(new_runs)
            improved = False

            for ax in axis_order:
                _check_cancel()
                origin = float(ref_best.get(ax, 0.0))
                origin = max(0.0, min(axis_max[ax], origin))

                def _score_at(v: float, tag: str) -> Optional[float]:
                    cand = dict(ref_best)
                    cand[ax] = float(v)
                    return _eval(cand, tag=tag)

                s_origin = _score_at(origin, f"{ax}:origin")
                if s_origin is None:
                    break
                best_dir = float(s_origin)
                best_dir_val = float(origin)

                worse_streak = 0
                v = round(origin + step_v, 7)
                while v <= axis_max[ax] + 1e-12:
                    s = _score_at(v, f"{ax}:+")
                    if s is None:
                        break
                    s = float(s)
                    if s < best_dir:
                        best_dir = s
                        best_dir_val = float(v)
                        worse_streak = 0
                    else:
                        worse_streak += 1
                        if worse_streak >= worse_limit:
                            break
                    v = round(v + step_v, 7)

                worse_streak = 0
                v = round(origin - step_v, 7)
                best_dir2 = float(s_origin)
                best_dir_val2 = float(origin)
                while v >= -1e-12:
                    vc = round(max(0.0, v), 7)
                    s = _score_at(vc, f"{ax}:-")
                    if s is None:
                        break
                    s = float(s)
                    if s < best_dir2:
                        best_dir2 = s
                        best_dir_val2 = float(vc)
                        worse_streak = 0
                    else:
                        worse_streak += 1
                        if worse_streak >= worse_limit:
                            break
                    v = round(v - step_v, 7)

                cand_val = best_dir_val if best_dir < best_dir2 else best_dir_val2
                cand_score = best_dir if best_dir < best_dir2 else best_dir2

                if cand_score < ref_best_score:
                    ref_best_score = float(cand_score)
                    ref_best[ax] = float(cand_val)
                    improved = True

            if improved:
                continue

            ax = axis_order[int(nudge_axis_i) % 3]
            nudge_axis_i += 1
            cur = float(ref_best.get(ax, 0.0))

            moved = False
            evaluated_new = False
            max_k = int(max(1.0, round(axis_max[ax] / step_v))) if step_v > 0 else 1
            max_k = max(1, min(500, max_k))

            for k_i in range(1, max_k + 1):
                for sign in (1.0, -1.0):
                    nv = float(cur + (sign * float(k_i) * step_v))
                    nv = float(max(0.0, min(axis_max[ax], nv)))
                    if abs(nv - cur) < 1e-12:
                        continue
                    cand = dict(ref_best)
                    cand[ax] = nv
                    k = _key(cand["x"], cand["y"], cand["z"])
                    if k in score_cache:
                        if not moved:
                            ref_best = cand
                            try:
                                ref_best_score = float(score_cache[k])
                            except Exception:
                                ref_best_score = float("inf")
                            moved = True
                        continue
                    s = _eval(cand, tag=f"nudge:{ax}")
                    if s is None:
                        continue
                    ref_best = cand
                    ref_best_score = float(s)
                    moved = True
                    evaluated_new = True
                    break
                if evaluated_new:
                    break

            if not moved:
                _emit_status("Refine: no further moves available (at bounds).")
                break

            if not evaluated_new and int(new_runs) == prev_new_runs:
                _emit_status("Refine: no new (x,y,z) combos left to evaluate near current best; stopping.")
                break
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
        "tuning_mode": "local_refine",
        "cancelled": bool(cancelled),
        "created_at_ms": now_ms(),
    }
    write_run_meta(os.path.join(tuning_dir, "best.json"), best_payload)
    return best_payload


