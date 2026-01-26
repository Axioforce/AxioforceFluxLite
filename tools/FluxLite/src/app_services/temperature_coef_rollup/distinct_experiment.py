from __future__ import annotations

import csv
import os
import time
from typing import Dict, List, Optional, Tuple

from ...project_paths import data_dir


def _quantize(v: float, step: float) -> float:
    if step <= 0:
        return float(v)
    return round(float(v) / float(step)) * float(step)


def _pct(v: Optional[float]) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _mean(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / float(len(xs))


def _std(xs: List[float]) -> Optional[float]:
    if xs is None or len(xs) < 2:
        return 0.0 if xs else None
    m = _mean(xs)
    if m is None:
        return None
    var = sum((x - m) ** 2 for x in xs) / float(len(xs) - 1)
    return var**0.5


def _percentile(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    if p <= 0:
        return float(ys[0])
    if p >= 100:
        return float(ys[-1])
    k = (len(ys) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ys) - 1)
    if f == c:
        return float(ys[f])
    return float(ys[f] + (ys[c] - ys[f]) * (k - f))


def _eligible_runs_for_coef_key(*, runs: List[dict], coef_key: str) -> Tuple[int, List[dict]]:
    """
    Eligibility matches top-3 logic:
      - group by device_id
      - require >=2 distinct temps per device
    """
    ck = str(coef_key or "")
    if not ck:
        return 0, []

    by_dev: Dict[str, List[dict]] = {}
    for r in runs or []:
        try:
            if str(r.get("coef_key") or "") != ck:
                continue
            dev = str(r.get("device_id") or "")
        except Exception:
            continue
        if not dev:
            continue
        by_dev.setdefault(dev, []).append(r)

    eligible_runs: List[dict] = []
    eligible_devices = 0
    for _dev, dev_runs in by_dev.items():
        temps = set()
        for rr in dev_runs:
            tf = rr.get("temp_f")
            if tf is None:
                continue
            try:
                temps.add(float(tf))
            except Exception:
                continue
        if len(temps) < 2:
            continue
        eligible_devices += 1
        eligible_runs.extend(dev_runs)
    return eligible_devices, eligible_runs


def export_distinct_experiment_report(
    *,
    plate_type: str,
    rollup_runs: List[dict],
    seed: dict,
    candidates: List[dict],
) -> Dict[str, object]:
    """
    Export two CSVs:
      1) summary: per candidate aggregate stats + per-stage breakdown + deltas
      2) per_test: per candidate, per test, per stage stats including std_signed (cell variability)
    """
    pt = str(plate_type or "").strip() or "unknown"
    ts = int(time.time() * 1000)
    out_dir = os.path.join(data_dir("analysis"), "temp_coef_distinct_reports")
    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, f"type{pt}-distinct-{ts}-summary.csv")
    per_test_path = os.path.join(out_dir, f"type{pt}-distinct-{ts}-per_test.csv")

    seed_x = float(seed.get("x") or 0.0)
    seed_y = float(seed.get("y") or 0.0)
    seed_z = float(seed.get("z") or 0.0)

    # Candidate list includes seed row (step=0) for reference.
    cand_rows = [{"step": 0.0, "axis": "seed", "direction": "", "x": seed_x, "y": seed_y, "z": seed_z, "coef_key": str(seed.get("coef_key") or "")}] + list(candidates or [])

    stages = ("all", "db", "bw")

    # Per-test rows
    per_test_fields = [
        "plate_type",
        "seed_x",
        "seed_y",
        "seed_z",
        "step",
        "axis",
        "direction",
        "x",
        "y",
        "z",
        "coef_key",
        "device_id",
        "raw_csv",
        "temp_f",
        "stage",
        "mean_abs",
        "mean_signed",
        "std_signed",  # std across cells of signed error (per test)
        "pass_rate",
    ]

    summary_fields = [
        "plate_type",
        "seed_x",
        "seed_y",
        "seed_z",
        "step",
        "axis",
        "direction",
        "x",
        "y",
        "z",
        "coef_key",
        "eligible_devices",
        "eligible_tests",
    ]
    # Add per-stage aggregate fields
    for st in stages:
        summary_fields += [
            f"{st}_mean_abs_mean",
            f"{st}_mean_abs_std_tests",
            f"{st}_mean_abs_p50",
            f"{st}_mean_signed_mean",
            f"{st}_mean_signed_std_tests",
            f"{st}_mean_signed_p50",
            f"{st}_abs_mean_signed_mean",
            f"{st}_std_signed_mean",  # mean of per-test std across cells
            f"{st}_std_signed_std_tests",
            f"{st}_std_signed_p50",
        ]
    # Stage deltas (db - bw)
    summary_fields += [
        "db_minus_bw_mean_abs_mean",
        "db_minus_bw_mean_signed_mean",
        "db_minus_bw_std_signed_mean",
    ]

    summary_rows_out: List[dict] = []
    per_test_rows_out: List[dict] = []

    for c in cand_rows:
        ck = str(c.get("coef_key") or "")
        if not ck:
            continue
        eligible_devices, eligible_runs = _eligible_runs_for_coef_key(runs=rollup_runs, coef_key=ck)

        # Collect per-stage arrays across tests
        by_stage_vals: Dict[str, Dict[str, List[float]]] = {st: {"mean_abs": [], "mean_signed": [], "std_signed": []} for st in stages}
        for rr in eligible_runs:
            for st in stages:
                sel = (rr.get("selected") or {}).get(st) or {}
                ma = _pct(sel.get("mean_abs"))
                ms = _pct(sel.get("mean_signed"))
                ss = _pct(sel.get("std_signed"))
                if ma is not None:
                    by_stage_vals[st]["mean_abs"].append(ma)
                if ms is not None:
                    by_stage_vals[st]["mean_signed"].append(ms)
                if ss is not None:
                    by_stage_vals[st]["std_signed"].append(ss)

                # Per-test output row (one per stage)
                per_test_rows_out.append(
                    {
                        "plate_type": pt,
                        "seed_x": seed_x,
                        "seed_y": seed_y,
                        "seed_z": seed_z,
                        "step": float(c.get("step") or 0.0),
                        "axis": str(c.get("axis") or ""),
                        "direction": str(c.get("direction") or ""),
                        "x": float(c.get("x") or 0.0),
                        "y": float(c.get("y") or 0.0),
                        "z": float(c.get("z") or 0.0),
                        "coef_key": ck,
                        "device_id": str(rr.get("device_id") or ""),
                        "raw_csv": os.path.basename(str(rr.get("raw_csv") or "")),
                        "temp_f": rr.get("temp_f"),
                        "stage": st,
                        "mean_abs": ma,
                        "mean_signed": ms,
                        "std_signed": ss,
                        "pass_rate": sel.get("pass_rate"),
                    }
                )

        row = {
            "plate_type": pt,
            "seed_x": seed_x,
            "seed_y": seed_y,
            "seed_z": seed_z,
            "step": float(c.get("step") or 0.0),
            "axis": str(c.get("axis") or ""),
            "direction": str(c.get("direction") or ""),
            "x": float(c.get("x") or 0.0),
            "y": float(c.get("y") or 0.0),
            "z": float(c.get("z") or 0.0),
            "coef_key": ck,
            "eligible_devices": eligible_devices,
            "eligible_tests": len(eligible_runs),
        }

        for st in stages:
            mas = by_stage_vals[st]["mean_abs"]
            mss = by_stage_vals[st]["mean_signed"]
            sss = by_stage_vals[st]["std_signed"]
            row[f"{st}_mean_abs_mean"] = _mean(mas)
            row[f"{st}_mean_abs_std_tests"] = _std(mas)
            row[f"{st}_mean_abs_p50"] = _percentile(mas, 50)
            row[f"{st}_mean_signed_mean"] = _mean(mss)
            row[f"{st}_mean_signed_std_tests"] = _std(mss)
            row[f"{st}_mean_signed_p50"] = _percentile(mss, 50)
            row[f"{st}_abs_mean_signed_mean"] = abs(float(row[f"{st}_mean_signed_mean"])) if row[f"{st}_mean_signed_mean"] is not None else None
            row[f"{st}_std_signed_mean"] = _mean(sss)
            row[f"{st}_std_signed_std_tests"] = _std(sss)
            row[f"{st}_std_signed_p50"] = _percentile(sss, 50)

        # Deltas (db - bw)
        db_ma = row.get("db_mean_abs_mean")
        bw_ma = row.get("bw_mean_abs_mean")
        db_ms = row.get("db_mean_signed_mean")
        bw_ms = row.get("bw_mean_signed_mean")
        db_ss = row.get("db_std_signed_mean")
        bw_ss = row.get("bw_std_signed_mean")
        row["db_minus_bw_mean_abs_mean"] = (float(db_ma) - float(bw_ma)) if db_ma is not None and bw_ma is not None else None
        row["db_minus_bw_mean_signed_mean"] = (float(db_ms) - float(bw_ms)) if db_ms is not None and bw_ms is not None else None
        row["db_minus_bw_std_signed_mean"] = (float(db_ss) - float(bw_ss)) if db_ss is not None and bw_ss is not None else None

        summary_rows_out.append(row)

    # Write CSVs
    with open(summary_path, "w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=summary_fields)
        w.writeheader()
        for r in summary_rows_out:
            w.writerow({k: r.get(k) for k in summary_fields})

    with open(per_test_path, "w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=per_test_fields)
        w.writeheader()
        for r in per_test_rows_out:
            w.writerow({k: r.get(k) for k in per_test_fields})

    return {
        "ok": True,
        "summary_path": summary_path,
        "per_test_path": per_test_path,
        "candidate_count": len(cand_rows),
    }

