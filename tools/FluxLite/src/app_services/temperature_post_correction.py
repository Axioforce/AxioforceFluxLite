from __future__ import annotations

from typing import Optional


def extract_temp_f_from_meta(meta: dict) -> Optional[float]:
    """
    Best-effort temperature extraction from a temp test meta dict.

    Matches current UI conventions.
    """
    meta = meta or {}
    for key in ("temp_f", "room_temperature_f", "room_temp_f", "ambient_temp_f", "avg_temp"):
        try:
            val = meta.get(key)
            if val is None:
                continue
            val_f = float(val)
            if val_f:
                return val_f
        except Exception:
            continue
    return None


def compute_delta_t_f(*, meta: dict, ideal_room_temp_f: float) -> Optional[float]:
    temp_f = extract_temp_f_from_meta(meta or {})
    if temp_f is None:
        return None
    try:
        return float(temp_f) - float(ideal_room_temp_f)
    except Exception:
        return None


def compute_post_correction_scale(*, fz_n: float, delta_t_f: float, k: float, fref_n: float) -> float:
    """
    Fz,c = Fz * scale
    scale = 1 + deltaT * k * ((|Fz| - Fref)/Fref)
    """
    fref = float(fref_n or 0.0)
    if fref <= 0.0:
        return 1.0
    return 1.0 + (float(delta_t_f) * float(k) * ((abs(float(fz_n)) - fref) / fref))


def apply_post_correction_to_run_data(
    run_data: dict,
    *,
    delta_t_f: float,
    k: float,
    fref_n: float,
) -> None:
    """
    In-place update of analyzer run payload (`baseline` or `selected`) applying post-correction
    to each cell `mean_n` and updating `signed_pct` and `abs_ratio` based on stage target/tolerance.
    """
    if not run_data:
        return
    fref = float(fref_n or 0.0)
    if fref <= 0.0:
        return
    stages = (run_data or {}).get("stages") or {}
    for stage in stages.values():
        if not isinstance(stage, dict):
            continue
        target_n = float(stage.get("target_n") or 0.0)
        tol_n = float(stage.get("tolerance_n") or 0.0)
        for cell in stage.get("cells", []) or []:
            try:
                mean_n = float(cell.get("mean_n", 0.0))
            except Exception:
                continue
            scale = compute_post_correction_scale(
                fz_n=mean_n,
                delta_t_f=float(delta_t_f),
                k=float(k),
                fref_n=fref,
            )
            mean_corr = mean_n * scale
            cell["mean_n"] = float(mean_corr)
            if target_n:
                cell["signed_pct"] = float((mean_corr - target_n) / target_n * 100.0)
            if tol_n:
                cell["abs_ratio"] = float(abs(mean_corr - target_n) / tol_n)

