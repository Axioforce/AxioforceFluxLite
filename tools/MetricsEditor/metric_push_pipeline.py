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
class PendingMetricSummary:
    total_local: int
    new_count: int
    changed_count: int
    unchanged_count: int
    baseline_total_existing: int
    baseline_source: str
    baseline_last_update_time: int


_IGNORE_DIFF_KEYS: set[str] = {
    "last_update_time",
    "last_update_timestamp",
    "synced_from",
}

# These fields exist in `metrics_truth` for CAS building / editor UX, but we do not
# want them to affect metric diffs or be pushed as part of metric documents.
# (We can re-enable `latex_formula` later when we're ready.)
_IGNORE_METRIC_TRUTH_ONLY_KEYS: set[str] = {
    "equation_explanation",
    "capture_type_info",
    "optimization_mode",
    "latex_formula",
}


def _dynamo_app_cwd() -> Path:
    return paths.dynamo_root() / "app"


def _dev_cred_path() -> Path | None:
    p = paths.dynamo_root() / "file_system" / "firebase-dev-key.json"
    return p if p.exists() else None


def _run_dynamo_inline(
    code: str,
    *,
    cred: Path | None,
    extra_env: dict[str, str] | None = None,
    timeout_s: int = 180,
) -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("APP_ENV", "development")
    env["PYTHONPATH"] = str(Path(".."))
    if cred is not None:
        env["AXF_FIREBASE_CRED"] = str(cred)
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(_dynamo_app_cwd()),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"Timed out after {timeout_s}s running DynamoDeluxe subprocess."


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
    rc, out = _run_dynamo_inline(
        code,
        cred=cred,
        extra_env={"AXF_FIREBASE_CRED": ""} if cred is None else None,
        timeout_s=45,
    )
    if rc != 0:
        return 0
    m = re.search(r"AXF_LAST_UPDATE_TIME:\s*(\d+)", out)
    return int(m.group(1)) if m else 0


def _choose_baseline_source(*, dev_cred: Path, force_source: str | None = None) -> tuple[str, Path | None, int]:
    prod_ts = _read_last_update_time_for_path_key(path_key="analytics", cred=None)
    dev_ts = _read_last_update_time_for_path_key(path_key="analytics", cred=dev_cred)
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


def _pull_single_metric_to_dir(*, axf_id: str, target_dir: Path, cred: Path | None) -> tuple[bool, str]:
    code = rf"""
import json
from pathlib import Path
from app.db import db_hub
from app.db.firebase_utils import convert_firebase_admin_response

axf_id = {json.dumps(axf_id)}
out_dir = Path({json.dumps(str(target_dir))})
out_dir.mkdir(parents=True, exist_ok=True)

fb = db_hub.firebase_hub
path = f'{{fb.paths["analytics"]}}/analytic'
doc = fb.database.collection(path).document(axf_id).get()
if not doc.exists:
    raise RuntimeError(f"Analytic not found in Firebase: {{axf_id}}")

data = convert_firebase_admin_response(doc.to_dict() or {{}})
data["axf_id"] = axf_id
(out_dir / f"{{axf_id}}.json").write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {{axf_id}} to {{out_dir}}")
"""
    rc, out = _run_dynamo_inline(code, cred=cred, extra_env={"AXF_FIREBASE_CRED": ""} if cred is None else None, timeout_s=120)
    if rc != 0:
        return False, out or "Failed pulling metric."
    return True, out


def _canon_metric(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    for k in _IGNORE_DIFF_KEYS:
        out.pop(k, None)
    for k in _IGNORE_METRIC_TRUTH_ONLY_KEYS:
        out.pop(k, None)
    return out


def _top_level_changed_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    keys = sorted({*a.keys(), *b.keys()} - _IGNORE_DIFF_KEYS - _IGNORE_METRIC_TRUTH_ONLY_KEYS)
    return [k for k in keys if a.get(k) != b.get(k)]


def compute_pending_metric_changes(*, baseline_choice: str = "auto") -> tuple[
    PendingMetricSummary | None,
    list[dict[str, Any]],
    str | None,
    list[str],
    dict[str, list[str]],
]:
    """
    Diff local `file_system/metrics_truth` metrics against the newest baseline (prod vs dev),
    and prepare a list of metrics to push to dev.
    """
    dev_cred = _dev_cred_path()
    if dev_cred is None:
        return None, [], (
            "Missing dev Firebase credential. Expected "
            f"`{paths.dynamo_root() / 'file_system' / 'firebase-dev-key.json'}`."
        ), [], {}

    force_source = None if baseline_choice == "auto" else baseline_choice
    baseline_source, baseline_cred, baseline_ts = _choose_baseline_source(dev_cred=dev_cred, force_source=force_source)

    fs = paths.dynamo_root() / "file_system"
    baseline_dir = fs / "analytics_baseline_from_db"
    local_dir = fs / "metrics_truth"

    # Pull baseline analytics into baseline_dir
    pull_code = r"""
import json
from pathlib import Path
from app.db import db_hub
from app.logger import logger

out_dir = Path("../file_system/analytics_baseline_from_db")
out_dir.mkdir(parents=True, exist_ok=True)

res = db_hub.firebase_hub.analytic_hub.get_analytics()
if res.get("status") != "success":
    raise RuntimeError(f"Failed pulling analytics from baseline: {res.get('message')}")

count = 0
for a in (res.get("data") or []):
    axf_id = a.get("axf_id")
    if not isinstance(axf_id, str) or not axf_id.strip():
        continue
    p = out_dir / f"{axf_id}.json"
    p.write_text(json.dumps(a, indent=4, ensure_ascii=False), encoding="utf-8")
    count += 1
logger.info(f"Wrote {count} baseline analytics to {out_dir}")
"""
    rc, out = _run_dynamo_inline(
        pull_code,
        cred=baseline_cred,
        extra_env={"AXF_FIREBASE_CRED": ""} if baseline_cred is None else None,
        timeout_s=240,
    )
    if rc != 0:
        return None, [], f"Failed pulling {baseline_source} analytics.\n\n{out}", [], {}

    local = _read_json_dir(local_dir)
    base = _read_json_dir(baseline_dir)

    new_ids = sorted([k for k in local.keys() if k not in base])
    common_ids = sorted([k for k in local.keys() if k in base])

    modified_ids: list[str] = []
    rows: list[dict[str, Any]] = []

    for axf_id in common_ids:
        cw = _canon_metric(local[axf_id])
        ch = _canon_metric(base[axf_id])
        if cw != ch:
            modified_ids.append(axf_id)
            rows.append({"axf_id": axf_id, "changed_fields": ", ".join(_top_level_changed_fields(cw, ch))})

    unchanged_count = len(common_ids) - len(modified_ids)
    to_push = [*new_ids, *modified_ids]

    summary = PendingMetricSummary(
        total_local=len(local),
        new_count=len(new_ids),
        changed_count=len(modified_ids),
        unchanged_count=unchanged_count,
        baseline_total_existing=len(base),
        baseline_source=baseline_source,
        baseline_last_update_time=baseline_ts,
    )

    meta = {"new": new_ids, "modified": modified_ids}
    # UI only needs modified rows; new metrics are handled by the summary line.
    return summary, rows, out or None, to_push, meta


def describe_metric_diff(axf_id: str) -> dict[str, Any] | None:
    """
    Diff local metrics_truth metric against baseline snapshot.
    """
    fs = paths.dynamo_root() / "file_system"
    local_path = fs / "metrics_truth" / f"{axf_id}.json"
    base_path = fs / "analytics_baseline_from_db" / f"{axf_id}.json"
    if not local_path.exists():
        return None
    try:
        local = json.loads(local_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(local, dict):
        return None

    base: dict[str, Any] | None = None
    if base_path.exists():
        try:
            tmp = json.loads(base_path.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(tmp, dict):
                base = tmp
        except Exception:
            base = None

    canon_local = _canon_metric(local)
    canon_base = _canon_metric(base or {})
    changed = _top_level_changed_fields(canon_local, canon_base) if base is not None else sorted(canon_local.keys())

    out: dict[str, Any] = {"axf_id": axf_id, "changed_fields": changed, "before": {}, "after": {}}
    for k in changed:
        out["before"][k] = (canon_base.get(k) if base is not None else None)
        out["after"][k] = canon_local.get(k)
    if base is None:
        out["note"] = "Not present in baseline (new)."
    return out


def compute_single_metric_diff(
    *,
    axf_id: str,
    baseline_choice: str = "auto",
) -> tuple[dict[str, Any] | None, str | None, int | None, str | None]:
    dev_cred = _dev_cred_path()
    if dev_cred is None:
        return None, None, None, (
            "Missing dev Firebase credential. Expected "
            f"`{paths.dynamo_root() / 'file_system' / 'firebase-dev-key.json'}`."
        )

    force_source = None if baseline_choice == "auto" else baseline_choice
    baseline_source, baseline_cred, baseline_ts = _choose_baseline_source(dev_cred=dev_cred, force_source=force_source)
    baseline_dir = paths.dynamo_root() / "file_system" / "analytics_baseline_from_db"

    ok, out = _pull_single_metric_to_dir(axf_id=axf_id, target_dir=baseline_dir, cred=baseline_cred)
    if not ok:
        return None, baseline_source, baseline_ts, f"Failed pulling {baseline_source} metric.\n\n{out}"

    diff = describe_metric_diff(axf_id)
    return diff, baseline_source, baseline_ts, None


def push_metrics_to_dev(axf_ids: list[str]) -> tuple[bool, str]:
    """
    Push selected metrics (analytics documents) to Firebase dev.
    Source is local `file_system/metrics_truth/*.json`.
    """
    dev_cred = _dev_cred_path()
    if dev_cred is None:
        return False, (
            "Missing dev Firebase credential. Expected "
            f"`{paths.dynamo_root() / 'file_system' / 'firebase-dev-key.json'}`."
        )
    if not axf_ids:
        return True, "Nothing to push."

    push_code = r"""
import json
from pathlib import Path
from app.db import db_hub

local_dir = Path("../file_system/metrics_truth")
axf_ids = %s
strip_keys = %s

metrics = []
for axf_id in axf_ids:
    p = local_dir / f"{axf_id}.json"
    if not p.exists():
        continue
    m = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    if isinstance(m, dict):
        # Ensure required key is present and consistent
        m["axf_id"] = axf_id
        for k in strip_keys:
            m.pop(k, None)
        metrics.append(m)

res = db_hub.firebase_hub.analytic_hub.write_analytics(metrics)
if res.get("status") != "success":
    raise RuntimeError(f"Failed pushing analytics: {res.get('message')}")
print(f"pushed={len(metrics)}")
""" % (json.dumps(axf_ids), json.dumps(sorted(_IGNORE_METRIC_TRUTH_ONLY_KEYS)))

    rc, out = _run_dynamo_inline(push_code, cred=dev_cred)
    return (rc == 0), (out or ("ok" if rc == 0 else "unknown error"))

