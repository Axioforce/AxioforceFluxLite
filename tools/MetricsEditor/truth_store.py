from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.MetricsEditor import paths


NEW_FIELDS_DEFAULTS: dict[str, Any] = {
    "optimization_mode": {},  # capture_type_id -> raw string (e.g. "increase"/"decrease")
    "equation_explanation": {},  # capture_type_id -> string
    "capture_type_info": {},  # capture_type_id -> string
    "latex_formula": None,  # LaTeX math content (no surrounding \\[ \\])
}


def _ensure_new_fields(metric: dict[str, Any]) -> dict[str, Any]:
    for k, v in NEW_FIELDS_DEFAULTS.items():
        if k not in metric:
            metric[k] = v if not isinstance(v, (dict, list)) else json.loads(json.dumps(v))
        # Normalize None -> {} for the 3 kvp maps
        if k in ("optimization_mode", "equation_explanation", "capture_type_info") and metric.get(k) is None:
            metric[k] = {}
    return metric


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def base_metric_path(axf_id: str) -> Path:
    return paths.analytics_db_dir() / f"{axf_id}.json"


def truth_metric_path(axf_id: str) -> Path:
    return paths.truth_dir() / f"{axf_id}.json"


def load_base_metric(axf_id: str) -> dict[str, Any]:
    metric = _read_json(base_metric_path(axf_id))
    return _ensure_new_fields(metric)


def load_truth_or_base(axf_id: str) -> dict[str, Any]:
    truth_path = truth_metric_path(axf_id)
    if truth_path.exists():
        metric = _read_json(truth_path)
        return _ensure_new_fields(metric)
    return load_base_metric(axf_id)


def save_truth(axf_id: str, metric: dict[str, Any]) -> None:
    metric = _ensure_new_fields(metric)
    metric["axf_id"] = axf_id  # enforce consistency
    _write_json(truth_metric_path(axf_id), metric)


@dataclass(frozen=True)
class MergeResult:
    updated_metrics: set[str]
    skipped_metrics: dict[str, str]  # name -> reason

