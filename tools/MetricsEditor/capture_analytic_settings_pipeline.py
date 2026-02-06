from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.MetricsEditor import paths


@dataclass(frozen=True)
class PendingCasSummary:
    total_desired: int
    new_count: int
    changed_count: int
    unchanged_count: int
    prod_total_existing: int
    deleted_count: int
    baseline_source: str
    baseline_last_update_time: int


def _dynamo_app_cwd() -> Path:
    return paths.dynamo_root() / "app"


def _dev_cred_path() -> Path | None:
    # Convention used in this repo (local-only file; should not be committed)
    p = paths.dynamo_root() / "file_system" / "firebase-dev-key.json"
    return p if p.exists() else None


def _run_dynamo_inline(
    code: str,
    *,
    dev_cred: Path | None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """
    Run a small DynamoDeluxe `app.*` snippet in a separate process.

    We do this to avoid any Firebase Admin singleton conflicts inside Streamlit
    and to control which credential/project is used for the operation.
    """
    env = os.environ.copy()
    env.setdefault("APP_ENV", "development")
    # Make `import app.*` work when cwd is DynamoDeluxe/app.
    env["PYTHONPATH"] = str(Path(".."))
    if dev_cred is not None:
        env["AXF_FIREBASE_CRED"] = str(dev_cred)
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_dynamo_app_cwd()),
        env=env,
        text=True,
        capture_output=True,
    )
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return proc.returncode, out.strip()

def _read_last_update_time_for_path_key(*, path_key: str, cred: Path | None) -> int:
    code = rf"""
from app.db import db_hub
fb = db_hub.firebase_hub
ref = fb.database.document(fb.paths[{path_key!r}])
doc = ref.get()
data = doc.to_dict() if doc.exists else {{}}
val = data.get("last_update_time", 0) if isinstance(data, dict) else 0
try:
    val = int(val or 0)
except Exception:
    val = 0
print("AXF_LAST_UPDATE_TIME:", val)
"""
    rc, out = _run_dynamo_inline(code, dev_cred=cred, extra_env={"AXF_FIREBASE_CRED": ""} if cred is None else None)
    if rc != 0:
        return 0
    m = re.search(r"AXF_LAST_UPDATE_TIME:\s*(\d+)", out)
    return int(m.group(1)) if m else 0


def _choose_baseline_source(*, dev_cred: Path, force_source: str | None = None) -> tuple[str, Path | None, int]:
    prod_ts = _read_last_update_time_for_path_key(path_key="captureAnalyticSettings", cred=None)
    dev_ts = _read_last_update_time_for_path_key(path_key="captureAnalyticSettings", cred=dev_cred)
    if force_source == "dev":
        return "dev", dev_cred, dev_ts
    elif force_source == "prod":
        return "prod", None, prod_ts
    elif dev_ts > prod_ts:
        return "dev", dev_cred, dev_ts
    return "prod", None, prod_ts


def _read_json_dir(dir_path: Path) -> dict[str, dict[str, Any]]:
    if not dir_path.exists() or not dir_path.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in dir_path.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("axf_id"), str):
            out[data["axf_id"]] = data
    return out


def compute_pending_cas_changes(*, baseline_choice: str = "auto") -> tuple[
    PendingCasSummary | None,
    list[dict[str, Any]],
    str | None,
    list[str],
    dict[str, list[str]],
]:
    """
    Build desired CAS docs from `metrics_truth`, refresh current dev CAS snapshots,
    then compute a diff.

    Returns:
      (summary, rows_for_ui, error_or_logs)
    """
    dev_cred = _dev_cred_path()
    if dev_cred is None:
        return None, [], (
            "Missing dev Firebase credential. Expected "
            f"`{paths.dynamo_root() / 'file_system' / 'firebase-dev-key.json'}`."
        )

    force_source = None if baseline_choice == "auto" else baseline_choice
    baseline_source, baseline_cred, baseline_ts = _choose_baseline_source(dev_cred=dev_cred, force_source=force_source)

    # 1) Refresh current baseline CAS snapshots into a separate folder.
    rc1, out1 = _run_dynamo_inline(
        r"""
import json
from pathlib import Path
from app.db import db_hub
from app.logger import logger

out_dir = Path("../file_system/capture_analytic_setting_baseline_from_db")
out_dir.mkdir(parents=True, exist_ok=True)

res = db_hub.firebase_hub.capture_analytic_setting_hub.get_capture_analytic_settings()
if res.get("status") != "success":
    raise RuntimeError(f"Failed pulling capture analytic settings from baseline: {res.get('message')}")

count = 0
for s in (res.get("data") or []):
    axf_id = s.get("axf_id")
    if not isinstance(axf_id, str) or not axf_id.strip():
        continue
    p = out_dir / f"{axf_id}.json"
    p.write_text(json.dumps(s, indent=4, ensure_ascii=False), encoding="utf-8")
    count += 1
logger.info(f"Wrote {count} baseline capture analytic settings to {out_dir}")
""",
        dev_cred=baseline_cred,
        extra_env={"AXF_FIREBASE_CRED": ""} if baseline_cred is None else None,
    )
    if rc1 != 0:
        return None, [], f"Failed pulling {baseline_source} CAS snapshots.\n\n{out1}", [], {}

    # 2) Build desired CAS docs from metrics_truth into *_to_db
    rc2, out2 = _run_dynamo_inline(
        "from app.data_maintenance.data_maintenance import build_capture_analytic_settings_to_db_from_metrics_truth as f; f();",
        dev_cred=dev_cred,
    )
    if rc2 != 0:
        return None, [], f"Failed building CAS docs from metrics_truth.\n\n{out2}"

    fs = paths.dynamo_root() / "file_system"
    desired_dir = fs / "capture_analytic_setting_to_db"
    existing_dir = fs / "capture_analytic_setting_baseline_from_db"

    desired = _read_json_dir(desired_dir)
    existing = _read_json_dir(existing_dir)

    def _canon(d: dict[str, Any]) -> dict[str, Any]:
        # Only compare the fields we currently generate from metrics_truth.
        return {
            "equation_explanation": d.get("equation_explanation") or "",
            "context_description": d.get("context_description") or "",
            "optimization_mode": d.get("optimization_mode", None),
            "target_value": d.get("target_value", None),
        }

    rows: list[dict[str, Any]] = []
    new_count = 0
    changed_count = 0
    unchanged_count = 0
    deleted_count = 0
    to_push: list[str] = []

    new_ids = sorted([k for k in desired.keys() if k not in existing])
    deleted_ids = sorted([k for k in existing.keys() if k not in desired])
    common_ids = sorted([k for k in desired.keys() if k in existing])

    new_count = len(new_ids)
    deleted_count = len(deleted_ids)

    modified_ids: list[str] = []
    for axf_id in common_ids:
        want = desired[axf_id]
        have = existing[axf_id]
        changed_fields: list[str] = []
        cw = _canon(want)
        ch = _canon(have)
        for k in cw.keys():
            if cw.get(k) != ch.get(k):
                changed_fields.append(k)
        if changed_fields:
            modified_ids.append(axf_id)
            rows.append(
                {
                    "axf_id": axf_id,
                    "analytic_id": want.get("analytic_id"),
                    "capture_configuration_id": want.get("capture_configuration_id"),
                    "changed_fields": ", ".join(changed_fields),
                }
            )
        else:
            unchanged_count += 1

    changed_count = len(modified_ids)
    to_push = [*new_ids, *modified_ids]

    summary = PendingCasSummary(
        total_desired=len(desired),
        new_count=new_count,
        changed_count=changed_count,
        unchanged_count=unchanged_count,
        prod_total_existing=len(existing),
        deleted_count=deleted_count,
        baseline_source=baseline_source,
        baseline_last_update_time=baseline_ts,
    )
    logs = "\n\n".join([x for x in [out1, out2] if x])
    meta = {"new": new_ids, "deleted": deleted_ids, "modified": modified_ids}
    return summary, rows, logs or None, to_push, meta


def push_cas_to_dev() -> tuple[bool, str]:
    """
    Push `file_system/capture_analytic_setting_to_db/*.json` to Firebase.
    Write operations are guarded to only allow project_id == axioforce-dev.
    """
    dev_cred = _dev_cred_path()
    if dev_cred is None:
        return False, (
            "Missing dev Firebase credential. Expected "
            f"`{paths.dynamo_root() / 'file_system' / 'firebase-dev-key.json'}`."
        )
    rc, out = _run_dynamo_inline(
        "from app.data_maintenance.data_maintenance import write_capture_analytic_settings_to_db as f; f();",
        dev_cred=dev_cred,
    )
    return (rc == 0), (out or ("ok" if rc == 0 else "unknown error"))


def describe_cas_diff(axf_id: str) -> dict[str, Any] | None:
    """
    Return a structured diff for one CAS doc between desired and prod baseline.
    """
    fs = paths.dynamo_root() / "file_system"
    desired_path = fs / "capture_analytic_setting_to_db" / f"{axf_id}.json"
    base_path = fs / "capture_analytic_setting_baseline_from_db" / f"{axf_id}.json"

    if not desired_path.exists():
        return None
    try:
        desired = json.loads(desired_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(desired, dict):
        return None

    base: dict[str, Any] | None = None
    if base_path.exists():
        try:
            tmp = json.loads(base_path.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(tmp, dict):
                base = tmp
        except Exception:
            base = None

    fields = ["equation_explanation", "context_description", "optimization_mode", "target_value"]
    out: dict[str, Any] = {"axf_id": axf_id, "changed_fields": [], "before": {}, "after": {}}
    for k in fields:
        before = (base.get(k) if base is not None else None)
        after = (desired.get(k) if desired is not None else None)
        # Normalize empties for display
        if (before or "") != (after or ""):
            out["changed_fields"].append(k)
            out["before"][k] = before
            out["after"][k] = after
    if base is None:
        out["note"] = "Not present in baseline (new)."
        out["changed_fields"] = ["(new)"]
        out["before"] = {}
        out["after"] = {k: desired.get(k) for k in fields}
    return out

