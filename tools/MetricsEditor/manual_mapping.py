from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.MetricsEditor import analytics_index, paths


def mapping_path() -> Path:
    # Keep it alongside truth outputs (user-facing and persisted)
    return paths.truth_dir() / "_manual_metric_name_map.json"


def load_manual_map() -> dict[str, str]:
    """
    Returns mapping: token_signature(doc_metric_name_tokens) -> axf_id
    """
    p = mapping_path()
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        mappings = data.get("mappings", {})
        if isinstance(mappings, dict):
            # ensure str->str
            out: dict[str, str] = {}
            for k, v in mappings.items():
                if isinstance(k, str) and isinstance(v, str):
                    out[k] = v
            return out
    except Exception:
        return {}
    return {}


def save_manual_map(m: dict[str, str], labels: dict[str, str] | None = None) -> None:
    p = mapping_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"version": 1, "mappings": dict(sorted(m.items()))}
    if labels:
        payload["labels"] = dict(sorted(labels.items()))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def key_for_name(name: str) -> str:
    return analytics_index.token_signature(analytics_index.tokenize(name))

