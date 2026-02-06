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
class PendingCaptureConfigSummary:
    total_local: int
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
    p = paths.dynamo_root() / "file_system" / "firebase-dev-key.json"
    return p if p.exists() else None


def _run_dynamo_inline(code: str, *, cred: Path | None, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("APP_ENV", "development")
    env["PYTHONPATH"] = str(Path(".."))
    if cred is not None:
        env["AXF_FIREBASE_CRED"] = str(cred)
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
    """
    Read the marker doc `{paths[path_key]}` field `last_update_time` in the selected Firebase project.
    """
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
    rc, out = _run_dynamo_inline(code, cred=cred, extra_env={"AXF_FIREBASE_CRED": ""} if cred is None else None)
    if rc != 0:
        return 0
    m = re.search(r"AXF_LAST_UPDATE_TIME:\s*(\d+)", out)
    return int(m.group(1)) if m else 0


def _choose_baseline_source(*, dev_cred: Path, force_source: str | None = None) -> tuple[str, Path | None, int]:
    """
    Choose whether to baseline diffs against prod or dev.
    If force_source is provided ("prod" or "dev"), use that.
    Otherwise, auto-choose based on whichever has the newest marker timestamp.
    """
    prod_ts = _read_last_update_time_for_path_key(path_key="captureConfigurations", cred=None)
    dev_ts = _read_last_update_time_for_path_key(path_key="captureConfigurations", cred=dev_cred)
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


def _canon_capture_config(cfg: dict[str, Any]) -> dict[str, Any]:
    # Ignore timestamps; otherwise compare the full document structure.
    d = dict(cfg)
    d.pop("last_update_time", None)
    d.pop("last_update_timestamp", None)
    d.pop("synced_from", None)
    return d


_METRIC_WIRING_TOPLEVEL_KEYS: set[str] = {
    "analytics_keys",
    "device_analytics_keys",
    "multi_phase_analytics_keys",
    "metric_priority",
}


def _canon_phases_without_metric_keys(phases: Any) -> Any:
    """
    Canonicalize phases while ignoring metric-wiring-only differences.

    Specifically remove `phase_analytics_keys` so that changes that are only
    metric adds/removes don't show up as phase diffs.
    """
    if not isinstance(phases, list):
        return phases
    out: list[Any] = []
    for ph in phases:
        if not isinstance(ph, dict):
            out.append(ph)
            continue
        d = dict(ph)
        d.pop("phase_analytics_keys", None)
        out.append(d)
    return out


def _canon_capture_config_without_metric_wiring(cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Canonicalize capture config while removing metric-wiring keys so the “other diffs”
    section stays focused (metrics changes are handled separately).
    """
    d = _canon_capture_config(cfg)
    for k in _METRIC_WIRING_TOPLEVEL_KEYS:
        d.pop(k, None)
    if "phases" in d:
        d["phases"] = _canon_phases_without_metric_keys(d.get("phases"))
    return d


def _extract_metric_signatures(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Build a stable per-metric signature capturing where/how a metric is referenced
    inside a capture configuration.
    """
    sig: dict[str, dict[str, Any]] = {}

    def _ensure(mid: str) -> dict[str, Any]:
        ent = sig.get(mid)
        if ent is None:
            ent = {
                "in_priority": False,
                "priority_indices": [],
                "wiring": {"capture": False, "device": False, "phase": [], "multi_phase": []},
            }
            sig[mid] = ent
        return ent

    # metric_priority (ordered list of cards)
    mp = cfg.get("metric_priority")
    if isinstance(mp, list):
        for i, card in enumerate(mp):
            if not isinstance(card, dict):
                continue
            mid = card.get("axf_id")
            if isinstance(mid, str) and mid.strip():
                ent = _ensure(mid.strip())
                ent["in_priority"] = True
                ent["priority_indices"].append(i)

    # capture/device analytics keys
    for k, wiring_key in (("analytics_keys", "capture"), ("device_analytics_keys", "device")):
        keys = cfg.get(k)
        if isinstance(keys, list):
            for mid in keys:
                if isinstance(mid, str) and mid.strip():
                    ent = _ensure(mid.strip())
                    ent["wiring"][wiring_key] = True

    # phases with phase_analytics_keys
    phases = cfg.get("phases")
    if isinstance(phases, list):
        for ph in phases:
            if not isinstance(ph, dict):
                continue
            pn = ph.get("name")
            pn = pn.strip() if isinstance(pn, str) else ""
            pkeys = ph.get("phase_analytics_keys")
            if not isinstance(pkeys, list):
                continue
            for mid in pkeys:
                if isinstance(mid, str) and mid.strip():
                    ent = _ensure(mid.strip())
                    if pn:
                        ent["wiring"]["phase"].append(pn)

    # multi_phase_analytics_keys entries
    mpk = cfg.get("multi_phase_analytics_keys")
    if isinstance(mpk, list):
        for ent0 in mpk:
            if not isinstance(ent0, dict):
                continue
            mid = ent0.get("key")
            if not isinstance(mid, str) or not mid.strip():
                continue
            mid = mid.strip()
            phase_names = ent0.get("phase_names") if isinstance(ent0.get("phase_names"), list) else []
            data_set_devices = (
                ent0.get("data_set_devices") if isinstance(ent0.get("data_set_devices"), list) else []
            )
            entry = {
                "phase_names": [str(x) for x in phase_names if str(x).strip()],
                "data_set_devices": [str(x) for x in data_set_devices if str(x).strip()],
            }
            entry["phase_names"].sort()
            entry["data_set_devices"].sort()
            ent = _ensure(mid)
            ent["wiring"]["multi_phase"].append(entry)

    # Normalize signature lists
    for ent in sig.values():
        ent["priority_indices"] = sorted([int(x) for x in ent.get("priority_indices") or []])
        ent["wiring"]["phase"] = sorted({str(x) for x in ent["wiring"].get("phase") or [] if str(x).strip()})
        # multi_phase entries: stable sort by repr
        mp_entries = ent["wiring"].get("multi_phase") or []
        ent["wiring"]["multi_phase"] = sorted(mp_entries, key=lambda x: json.dumps(x, sort_keys=True))
    return sig


def _metric_change_summary(prod_cfg: dict[str, Any] | None, local_cfg: dict[str, Any]) -> dict[str, Any]:
    prod_sig = _extract_metric_signatures(prod_cfg or {})
    local_sig = _extract_metric_signatures(local_cfg)

    prod_ids = set(prod_sig.keys())
    local_ids = set(local_sig.keys())

    added = sorted(local_ids - prod_ids)
    removed = sorted(prod_ids - local_ids)

    modified: list[str] = []
    diffs: dict[str, dict[str, Any]] = {}
    for mid in sorted(prod_ids & local_ids):
        if prod_sig.get(mid) != local_sig.get(mid):
            modified.append(mid)
            diffs[mid] = {"before": prod_sig.get(mid), "after": local_sig.get(mid)}

    common = prod_ids & local_ids
    unaffected = sorted([mid for mid in common if mid not in set(modified)])

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unaffected": unaffected,
        "unaffected_count": len(unaffected),
        "common_count": len(common),
        "prod_total_count": len(prod_ids),
        "local_total_count": len(local_ids),
        "metric_diffs": diffs,
    }


def _top_level_changed_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """
    Return top-level keys whose values differ (after canonicalization).
    Treat large nested keys as single fields (phases/metric_priority/etc).
    """
    keys = sorted({*a.keys(), *b.keys()} - {"last_update_time", "last_update_timestamp", "synced_from"})
    changed: list[str] = []
    for k in keys:
        if a.get(k) != b.get(k):
            changed.append(k)
    return changed


def compute_pending_capture_config_changes(*, baseline_choice: str = "auto") -> tuple[
    PendingCaptureConfigSummary | None,
    list[dict[str, Any]],
    str | None,
    list[str],
    dict[str, list[str]],
]:
    """
    Pull current prod capture configurations into a separate snapshot dir,
    then diff against local editable `capture_config_from_db`.

    Returns:
      (summary, rows_for_ui, logs_or_error, axf_ids_to_push, meta_lists)
    """
    dev_cred = _dev_cred_path()
    if dev_cred is None:
        return None, [], (
            "Missing dev Firebase credential. Expected "
            f"`{paths.dynamo_root() / 'file_system' / 'firebase-dev-key.json'}`."
        ), []

    fs = paths.dynamo_root() / "file_system"
    baseline_dir = fs / "capture_config_baseline_from_db"
    local_dir = fs / "capture_config_from_db"

    force_source = None if baseline_choice == "auto" else baseline_choice
    baseline_source, baseline_cred, baseline_ts = _choose_baseline_source(dev_cred=dev_cred, force_source=force_source)

    # Step A: pull baseline (prod or dev) into baseline_dir
    pull_code = r"""
import json
from pathlib import Path
from app.db import db_hub
from app.logger import logger

out_dir = Path("../file_system/capture_config_baseline_from_db")
out_dir.mkdir(parents=True, exist_ok=True)

res = db_hub.firebase_hub.capture_config_hub.get_capture_configurations()
if res.get("status") != "success":
    raise RuntimeError(f"Failed pulling capture configs from baseline: {res.get('message')}")

count = 0
for cfg in (res.get("data") or []):
    axf_id = cfg.get("axf_id")
    if not isinstance(axf_id, str) or not axf_id.strip():
        continue
    p = out_dir / f"{axf_id}.json"
    p.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
    count += 1
logger.info(f"Wrote {count} baseline capture configs to {out_dir}")
"""
    rc, out = _run_dynamo_inline(pull_code, cred=baseline_cred, extra_env={"AXF_FIREBASE_CRED": ""} if baseline_cred is None else None)
    if rc != 0:
        return None, [], f"Failed pulling {baseline_source} capture configs.\n\n{out}", [], {}

    local = _read_json_dir(local_dir)
    base = _read_json_dir(baseline_dir)

    new_count = 0
    changed_count = 0
    unchanged_count = 0
    deleted_count = 0
    rows: list[dict[str, Any]] = []
    to_push: list[str] = []

    new_ids = sorted([k for k in local.keys() if k not in base])
    deleted_ids = sorted([k for k in base.keys() if k not in local])
    common_ids = sorted([k for k in local.keys() if k in base])

    new_count = len(new_ids)
    deleted_count = len(deleted_ids)

    modified_ids: list[str] = []
    for axf_id in common_ids:
        want = local[axf_id]
        have = base[axf_id]
        # Split diffs into:
        # - metric changes (adds/removes/wiring changes)
        # - non-metric changes (description, transitions, etc.)
        metric_changes = _metric_change_summary(have, want)
        cw_other = _canon_capture_config_without_metric_wiring(want)
        ch_other = _canon_capture_config_without_metric_wiring(have)

        other_changed = cw_other != ch_other
        metrics_changed = bool(metric_changes["added"] or metric_changes["removed"] or metric_changes["modified"])

        if other_changed or metrics_changed:
            modified_ids.append(axf_id)
            changed_fields: list[str] = []
            if other_changed:
                changed_fields.extend(_top_level_changed_fields(cw_other, ch_other))
            if metrics_changed:
                changed_fields.append(
                    f"metrics(+{len(metric_changes['added'])} -{len(metric_changes['removed'])} *{len(metric_changes['modified'])})"
                )
            rows.append({"axf_id": axf_id, "changed_fields": ", ".join(changed_fields)})
        else:
            unchanged_count += 1

    changed_count = len(modified_ids)
    to_push = [*new_ids, *modified_ids]

    summary = PendingCaptureConfigSummary(
        total_local=len(local),
        new_count=new_count,
        changed_count=changed_count,
        unchanged_count=unchanged_count,
        prod_total_existing=len(base),
        deleted_count=deleted_count,
        baseline_source=baseline_source,
        baseline_last_update_time=baseline_ts,
    )
    meta = {"new": new_ids, "deleted": deleted_ids, "modified": modified_ids}
    return summary, rows, out or None, to_push, meta


def push_capture_configs_to_dev(axf_ids: list[str]) -> tuple[bool, str]:
    """
    Push the specified capture configurations (by axf_id) from local `capture_config_from_db`
    into Firebase dev.
    """
    dev_cred = _dev_cred_path()
    if dev_cred is None:
        return False, (
            "Missing dev Firebase credential. Expected "
            f"`{paths.dynamo_root() / 'file_system' / 'firebase-dev-key.json'}`."
        )
    if not axf_ids:
        return True, "Nothing to push."

    # We read the local JSONs and call the Firebase hub directly (avoids writing to capture_config_to_db).
    push_code = r"""
import json
from pathlib import Path
from app.db import db_hub

local_dir = Path("../file_system/capture_config_from_db")
axf_ids = %s
cfgs = []
for axf_id in axf_ids:
    p = local_dir / f"{axf_id}.json"
    if not p.exists():
        continue
    cfg = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    if isinstance(cfg, dict):
        cfgs.append(cfg)

res = db_hub.firebase_hub.capture_config_hub.write_capture_configurations(cfgs)
if res.get("status") != "success":
    raise RuntimeError(f"Failed pushing capture configs: {res.get('message')}")
print(f"pushed={len(cfgs)}")
""" % (json.dumps(axf_ids))

    rc, out = _run_dynamo_inline(push_code, cred=dev_cred)
    return (rc == 0), (out or ("ok" if rc == 0 else "unknown error"))


def describe_capture_config_diff(axf_id: str) -> dict[str, Any] | None:
    """
    Return a structured diff for one capture config between local and prod baseline.
    This is used by the MetricsEditor UI for a quick popover detail view.
    """
    fs = paths.dynamo_root() / "file_system"
    local_path = fs / "capture_config_from_db" / f"{axf_id}.json"
    base_path = fs / "capture_config_baseline_from_db" / f"{axf_id}.json"

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

    # Metric-aware diff:
    metric_changes = _metric_change_summary(base, local)

    canon_local_other = _canon_capture_config_without_metric_wiring(local)
    canon_base_other = _canon_capture_config_without_metric_wiring(base or {})
    other_changed_fields = (
        _top_level_changed_fields(canon_local_other, canon_base_other) if base is not None else list(canon_local_other.keys())
    )

    out: dict[str, Any] = {
        "axf_id": axf_id,
        "other_changed_fields": other_changed_fields,
        "before_other": {},
        "after_other": {},
        "metric_changes": metric_changes,
    }
    for k in other_changed_fields:
        out["before_other"][k] = (canon_base_other.get(k) if base is not None else None)
        out["after_other"][k] = canon_local_other.get(k)
    if base is None:
        out["note"] = "Not present in baseline (new)."
    return out

