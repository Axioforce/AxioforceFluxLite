from __future__ import annotations

import json
import os
from typing import List


def load_top_runs(test_folder: str, limit: int = 10) -> List[dict]:
    """
    Load the top-N scored runs from tuning/runs/run_*.json for a test folder.
    """
    base = str(test_folder or "")
    runs_dir = os.path.join(base, "tuning", "runs")
    out: List[dict] = []
    if not os.path.isdir(runs_dir):
        return []
    try:
        for fn in os.listdir(runs_dir):
            if not fn.lower().endswith(".json") or not fn.lower().startswith("run_"):
                continue
            p = os.path.join(runs_dir, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
                coeffs = meta.get("coeffs") or {}
                if not isinstance(coeffs, dict):
                    continue
                score_total = float(meta.get("score_total") or float("inf"))
                out.append(
                    {
                        "run_index": int(meta.get("run_index") or 0),
                        "score_total": float(score_total),
                        "coeffs": {
                            "x": float(coeffs.get("x") or 0.0),
                            "y": float(coeffs.get("y") or 0.0),
                            "z": float(coeffs.get("z") or 0.0),
                        },
                        "output_csv": str(meta.get("output_csv") or ""),
                        "tuning_mode": str(meta.get("tuning_mode") or ""),
                        "created_at_ms": int(meta.get("created_at_ms") or 0),
                    }
                )
            except Exception:
                continue
    except Exception:
        return []
    out.sort(key=lambda e: float(e.get("score_total") or float("inf")))
    lim = max(0, int(limit))
    return out[:lim] if lim else out


def load_leaderboard_and_exploration(
    test_folder: str,
    *,
    limit: int = 10,
    x_max: float = 0.005,
    y_max: float = 0.005,
    z_max: float = 0.008,
    step: float = 0.001,
) -> tuple[list[dict], dict]:
    """
    Load top runs AND exploration stats for a test folder.
    """
    base = str(test_folder or "")
    runs_dir = os.path.join(base, "tuning", "runs")
    out: List[dict] = []
    if not os.path.isdir(runs_dir):
        return ([], {"runs_total_files": 0, "unique_triples": 0, "legacy_runs_missing_pair_id": 0, "pairs_total": 0})

    def _grid(max_v: float, step_v: float) -> list[float]:
        max_v = float(max_v)
        step_v = float(step_v)
        if step_v <= 0:
            step_v = 0.001
        n = int(round(max_v / step_v))
        return [round(step_v * i, 7) for i in range(0, n + 1)]

    x_vals = _grid(x_max, step)
    y_vals = _grid(y_max, step)
    z_vals = _grid(z_max, step)
    nx, ny, nz = len(x_vals), len(y_vals), len(z_vals)
    pairs_total = (nx * ny) + (nx * nz) + (ny * nz)
    triples_total = nx * ny * nz

    unique_triples: set[tuple[float, float, float]] = set()
    pairs_xy: set[tuple[float, float]] = set()
    pairs_xz: set[tuple[float, float]] = set()
    pairs_yz: set[tuple[float, float]] = set()
    legacy_missing_pair = 0
    run_files = 0

    try:
        for fn in os.listdir(runs_dir):
            if not fn.lower().endswith(".json") or not fn.lower().startswith("run_"):
                continue
            p = os.path.join(runs_dir, fn)
            run_files += 1
            try:
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
                coeffs = meta.get("coeffs") or {}
                if not isinstance(coeffs, dict):
                    continue
                cx = round(float(coeffs.get("x") or 0.0), 7)
                cy = round(float(coeffs.get("y") or 0.0), 7)
                cz = round(float(coeffs.get("z") or 0.0), 7)
                unique_triples.add((cx, cy, cz))

                score_total = float(meta.get("score_total") or float("inf"))
                out.append(
                    {
                        "run_index": int(meta.get("run_index") or 0),
                        "score_total": float(score_total),
                        "coeffs": {"x": float(cx), "y": float(cy), "z": float(cz)},
                        "output_csv": str(meta.get("output_csv") or ""),
                        "tuning_mode": str(meta.get("tuning_mode") or ""),
                        "created_at_ms": int(meta.get("created_at_ms") or 0),
                    }
                )

                # Pair coverage (only available for newer runs that include pair_id)
                pid = meta.get("pair_id")
                if isinstance(pid, str) and pid:
                    if pid.startswith("xy:"):
                        pairs_xy.add((cx, cy))
                    elif pid.startswith("xz:"):
                        pairs_xz.add((cx, cz))
                    elif pid.startswith("yz:"):
                        pairs_yz.add((cy, cz))
                else:
                    legacy_missing_pair += 1
            except Exception:
                continue
    except Exception:
        pass

    out.sort(key=lambda e: float(e.get("score_total") or float("inf")))
    lim = max(0, int(limit))
    rows = out[:lim] if lim else out

    if triples_total > 0 and len(unique_triples) >= triples_total:
        stats = {
            "runs_total_files": int(run_files),
            "unique_triples": int(len(unique_triples)),
            "legacy_runs_missing_pair_id": int(legacy_missing_pair),
            "pairs_total": int(pairs_total),
            "pairs_explored_xy": int(nx * ny),
            "pairs_explored_xz": int(nx * nz),
            "pairs_explored_yz": int(ny * nz),
            "pairs_explored_total": int(pairs_total),
            "triples_total": int(triples_total),
        }
        return (rows, stats)

    stats = {
        "runs_total_files": int(run_files),
        "unique_triples": int(len(unique_triples)),
        "legacy_runs_missing_pair_id": int(legacy_missing_pair),
        "pairs_total": int(pairs_total),
        "pairs_explored_xy": int(len(pairs_xy)),
        "pairs_explored_xz": int(len(pairs_xz)),
        "pairs_explored_yz": int(len(pairs_yz)),
        "pairs_explored_total": int(len(pairs_xy) + len(pairs_xz) + len(pairs_yz)),
        "triples_total": int(triples_total),
    }
    return (rows, stats)


