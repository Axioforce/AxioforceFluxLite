from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def _eligible_runs_for_coef_key(
    *,
    runs: List[dict],
    coef_key: str,
) -> Tuple[int, List[dict], List[float]]:
    """
    Apply the same eligibility logic used by top-3 selection:
      - group by device_id
      - require >=2 distinct temps per device

    Returns:
      (eligible_devices, eligible_runs, all_temps)
    """
    ck = str(coef_key or "")
    if not ck:
        return 0, [], []

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
    all_temps: List[float] = []
    for dev, dev_runs in by_dev.items():
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
        all_temps.extend(list(temps))
        eligible_runs.extend(dev_runs)

    return eligible_devices, eligible_runs, all_temps


def aggregate_mean_signed_for_coef_key(
    *,
    runs: List[dict],
    coef_key: str,
) -> Optional[dict]:
    """
    Aggregate selected/all mean_signed across eligible runs for a given coef_key.

    Returns None if no eligible runs.
    """
    eligible_devices, eligible_runs, all_temps = _eligible_runs_for_coef_key(runs=runs, coef_key=coef_key)
    if eligible_devices < 1:
        return None

    mean_abs_vals: List[float] = []
    mean_signed_vals: List[float] = []
    std_signed_vals: List[float] = []
    for rr in eligible_runs:
        sel = (rr.get("selected") or {}).get("all") or {}
        try:
            mean_abs_vals.append(float(sel.get("mean_abs")))
        except Exception:
            pass
        try:
            mean_signed_vals.append(float(sel.get("mean_signed")))
        except Exception:
            pass
        try:
            std_signed_vals.append(float(sel.get("std_signed")))
        except Exception:
            pass

    if not mean_signed_vals:
        return None

    mean_signed = sum(mean_signed_vals) / float(len(mean_signed_vals))
    score_mean_abs = sum(mean_abs_vals) / float(len(mean_abs_vals)) if mean_abs_vals else None
    std_signed = sum(std_signed_vals) / float(len(std_signed_vals)) if std_signed_vals else None
    coverage = f"{eligible_devices} devices, {len(eligible_runs)} tests"
    if all_temps:
        try:
            coverage = f"{coverage}, temps {min(all_temps):.1f}–{max(all_temps):.1f}°F"
        except Exception:
            pass

    return {
        "coef_key": str(coef_key or ""),
        "mean_signed": mean_signed,
        "score_mean_abs": score_mean_abs,
        "std_signed": std_signed,
        "coverage": coverage,
        "eligible_devices": eligible_devices,
        "eligible_runs": len(eligible_runs),
    }


def top3_rows_for_plate_type(*, runs: List[dict], sort_by: str = "mean_abs") -> List[Dict[str, object]]:
    """
    Compute top-3 coefficient combos for a plate type using bias-controlled scoring only.

    This mirrors the existing behavior: eligibility is applied per coef_key, then rows are sorted
    by score_mean_abs ascending.
    """
    # Group by coef_key -> device -> list of runs
    by_coef: Dict[str, Dict[str, List[dict]]] = {}
    for r in runs or []:
        try:
            ck = str(r.get("coef_key") or "")
            dev = str(r.get("device_id") or "")
        except Exception:
            continue
        if not ck or not dev:
            continue
        by_coef.setdefault(ck, {}).setdefault(dev, []).append(r)

    rows: List[Dict[str, object]] = []
    for ck, by_dev in by_coef.items():
        eligible_runs: List[dict] = []
        eligible_devices = 0
        all_temps: List[float] = []
        for dev, dev_runs in by_dev.items():
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
            all_temps.extend(list(temps))
            eligible_runs.extend(dev_runs)

        if eligible_devices < 1:
            continue

        mean_abs_vals: List[float] = []
        mean_signed_vals: List[float] = []
        std_signed_vals: List[float] = []
        for rr in eligible_runs:
            sel = (rr.get("selected") or {}).get("all") or {}
            try:
                mean_abs_vals.append(float(sel.get("mean_abs")))
            except Exception:
                pass
            try:
                mean_signed_vals.append(float(sel.get("mean_signed")))
            except Exception:
                pass
            try:
                std_signed_vals.append(float(sel.get("std_signed")))
            except Exception:
                pass

        if not mean_abs_vals:
            continue

        score_mean_abs = sum(mean_abs_vals) / float(len(mean_abs_vals))
        mean_signed = sum(mean_signed_vals) / float(len(mean_signed_vals)) if mean_signed_vals else 0.0
        std_signed = sum(std_signed_vals) / float(len(std_signed_vals)) if std_signed_vals else 0.0
        coverage = f"{eligible_devices} devices, {len(eligible_runs)} tests"
        if all_temps:
            try:
                coverage = f"{coverage}, temps {min(all_temps):.1f}–{max(all_temps):.1f}°F"
            except Exception:
                pass

        rows.append(
            {
                "coef_key": ck,
                "coef_label": ck,
                "score_mean_abs": score_mean_abs,
                "mean_signed": mean_signed,
                "std_signed": std_signed,
                "coverage": coverage,
            }
        )

    sb = str(sort_by or "mean_abs").strip().lower()
    if sb in ("signed", "signed_abs", "abs_signed", "mean_signed_abs"):
        rows.sort(key=lambda r: abs(float(r.get("mean_signed") or 1e9)))
    else:
        rows.sort(key=lambda r: float(r.get("score_mean_abs") or 1e9))
    return rows[:3]


