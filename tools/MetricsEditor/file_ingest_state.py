from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.MetricsEditor import paths


def state_path() -> Path:
    return paths.truth_dir() / "_file_ingest_state.json"


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.exists():
        return {"version": 1, "docx": {}, "latex": {}}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "docx": {}, "latex": {}}
        data.setdefault("version", 1)
        data.setdefault("docx", {})
        data.setdefault("latex", {})
        return data
    except Exception:
        return {"version": 1, "docx": {}, "latex": {}}


def save_state(state: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_file_record(state: dict[str, Any], kind: str, filename: str) -> dict[str, Any] | None:
    bucket = state.get(kind, {})
    if isinstance(bucket, dict):
        rec = bucket.get(filename)
        return rec if isinstance(rec, dict) else None
    return None


def upsert_file_record(state: dict[str, Any], kind: str, filename: str, record: dict[str, Any]) -> None:
    state.setdefault(kind, {})
    if not isinstance(state[kind], dict):
        state[kind] = {}
    state[kind][filename] = record


def delete_file_record(state: dict[str, Any], kind: str, filename: str) -> None:
    bucket = state.get(kind, {})
    if isinstance(bucket, dict):
        bucket.pop(filename, None)


def mapped_count(record: dict[str, Any]) -> tuple[int, int]:
    total = int(record.get("total_items") or 0)
    unresolved = record.get("unresolved", [])
    if isinstance(unresolved, list):
        remaining = len(unresolved)
    else:
        remaining = 0
    mapped = max(0, total - remaining)
    return mapped, total

