from __future__ import annotations

from typing import Dict, List

from ... import config


def score_run_against_bias(
    *,
    run_data: dict,
    stage_key: str,
    device_type: str,
    body_weight_n: float,
    bias_map: list,
) -> dict:
    """
    Compute bias-controlled scoring stats for a single analysis payload run ('baseline' or 'selected').

    Returns:
      { n, mean_abs, mean_signed, std_signed, pass_rate }
    """
    stages = (run_data or {}).get("stages") or {}
    keys = list(stages.keys()) if stage_key == "all" else [stage_key]
    abs_pcts: List[float] = []
    signed_pcts: List[float] = []
    pass_count = 0
    total = 0
    for sk in keys:
        stage = stages.get(sk) or {}
        base_target = float(stage.get("target_n") or 0.0)
        threshold = float(config.get_passing_threshold(sk, device_type, body_weight_n))
        for cell in stage.get("cells", []) or []:
            try:
                rr = int(cell.get("row", 0))
                cc = int(cell.get("col", 0))
                mean_n = float(cell.get("mean_n", 0.0))
            except Exception:
                continue
            target = base_target
            try:
                target = base_target * (1.0 + float(bias_map[rr][cc]))
            except Exception:
                target = base_target
            if not target:
                continue
            signed = (mean_n - target) / target * 100.0
            abs_pcts.append(abs(signed))
            signed_pcts.append(signed)
            total += 1
            err_ratio = abs(mean_n - target) / threshold if threshold > 0 else 999.0
            if err_ratio <= float(config.COLOR_BIN_MULTIPLIERS.get("light_green", 1.0)):
                pass_count += 1
    if not abs_pcts:
        return {"n": 0}
    mean_abs = sum(abs_pcts) / float(len(abs_pcts))
    mean_signed = sum(signed_pcts) / float(len(signed_pcts))
    var = sum((x - mean_signed) ** 2 for x in signed_pcts) / float(max(1, len(signed_pcts) - 1))
    std_signed = float(var) ** 0.5
    return {
        "n": len(abs_pcts),
        "mean_abs": mean_abs,
        "mean_signed": mean_signed,
        "std_signed": std_signed,
        "pass_rate": (100.0 * pass_count / total) if total else None,
    }


