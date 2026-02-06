from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from tools.MetricsEditor import analytics_index, paths
from tools.MetricsEditor.normalization import normalize_optimization_mode


def list_truth_metric_ids() -> list[str]:
    """
    Metrics that exist only in the truth store (not necessarily in analytics snapshots).
    """
    out: list[str] = []
    d = paths.truth_dir()
    if not d.exists():
        return []
    for p in d.glob("*.json"):
        if p.name == "_file_ingest_state.json":
            continue
        out.append(p.stem)
    return sorted({x for x in out if x})


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+", re.IGNORECASE)


def suggest_axf_id(name: str, existing_ids: Iterable[str]) -> str:
    """
    Create a stable-ish camelCase axf_id suggestion from a display name.
    Ensures uniqueness by appending a numeric suffix if needed.
    """
    existing = {str(x) for x in existing_ids if str(x)}
    norm = analytics_index.normalize_name(name)
    if not norm:
        base = "newMetric"
    else:
        # normalize_name returns a space-separated canonical string (via tokenization rules)
        parts = [p for p in _NON_ALNUM_RE.split(norm) if p]
        if not parts:
            base = "newMetric"
        else:
            base = parts[0].lower() + "".join([p[:1].upper() + p[1:] for p in parts[1:]])
            if not base[0].isalpha():
                base = "m" + base

    cand = base
    i = 2
    while cand in existing:
        cand = f"{base}{i}"
        i += 1
    return cand


def draft_metric_from_doc_item(
    *,
    kind: str,
    doc_name: str,
    capture_type_id: str | None,
    doc_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build a new metric dict seeded from a docx/latex extracted metric.
    """
    doc_fields = doc_fields if isinstance(doc_fields, dict) else {}
    m: dict[str, Any] = {
        "name": (doc_name or "").strip(),
        "description": (doc_fields.get("description") or None),
        "units": (doc_fields.get("units") or None),
        "equation": None,
        "script": None,
        "required_components": [],
        "required_metrics": [],
        "required_devices": [],
        "latex_formula": None,
        "optimization_mode": {},
        "equation_explanation": {},
        "capture_type_info": {},
    }

    cap = (capture_type_id or "").strip()

    if kind == "latex":
        eq = (doc_fields.get("equation") or "").strip()
        if eq:
            m["latex_formula"] = eq
        # latex has no capture-type-specific overrides in the source.
        how = (doc_fields.get("how_to_use") or "").strip()
        if how:
            m["description"] = m.get("description") or how
        return m

    # docx
    how2 = (doc_fields.get("how_to_use") or "").strip()
    ee2 = (doc_fields.get("equation_explanation") or "").strip()
    om_raw = doc_fields.get("optimization_mode")
    om_norm = normalize_optimization_mode(str(om_raw)) if om_raw is not None else None

    if cap:
        if how2:
            m["capture_type_info"][cap] = how2
        if ee2:
            m["equation_explanation"][cap] = ee2
        if om_norm:
            m["optimization_mode"][cap] = om_norm

    return m

