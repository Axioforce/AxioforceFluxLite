from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    # tools/MetricsEditor/paths.py -> repo root
    return Path(__file__).resolve().parents[2]


def dynamo_root() -> Path:
    """
    Path to the DynamoDeluxe repo folder that owns `app/` and `file_system/`.

    This keeps the Metrics Editor behaving the same way it did when it lived
    inside DynamoDeluxe: snapshots/truth live in DynamoDeluxe/file_system and
    `app.*` imports resolve against DynamoDeluxe.
    """
    env = (os.environ.get("METRICS_EDITOR_DYNAMO_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p

    candidate = _repo_root() / "FluxDeluxe" / "DynamoDeluxe"
    if candidate.exists():
        return candidate

    # Fallback: repository root (best-effort)
    return _repo_root()


def repo_root() -> Path:
    # Back-compat name: historically this returned the DynamoDeluxe repo root.
    return dynamo_root()


def file_system_dir() -> Path:
    return repo_root() / "file_system"


def analytics_db_dir() -> Path:
    return file_system_dir() / "analytics_db"


def capture_config_db_dir() -> Path:
    return file_system_dir() / "capture_config_from_db"


def truth_dir() -> Path:
    return file_system_dir() / "metrics_truth"


def uploads_docs_dir() -> Path:
    # Keep editor uploads within the tool package folder.
    return Path(__file__).resolve().parent / "Metrics doc"


def uploads_latex_dir() -> Path:
    return Path(__file__).resolve().parent / "latex"

