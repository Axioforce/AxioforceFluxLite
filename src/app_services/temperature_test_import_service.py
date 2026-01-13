from __future__ import annotations

import os
import re
import shutil
from typing import Dict, List

from ..project_paths import data_dir


_RAW_RE = re.compile(r"^temp-raw-(?P<device>.+?)-(?P<date>\d{8})-(?P<time>\d{6})\.csv$", re.IGNORECASE)


def _device_id_from_raw_filename(filename: str) -> str:
    name = os.path.basename(str(filename or "").strip())
    m = _RAW_RE.match(name)
    if not m:
        return ""
    return str(m.group("device") or "").strip()


def import_temperature_raw_tests(file_paths: List[str]) -> Dict[str, object]:
    """
    Import raw temperature tests into the canonical folder layout:
      temp_testing/<device_id>/temp-raw-<device_id>-<date>-<time>.csv
      temp_testing/<device_id>/temp-raw-<device_id>-<date>-<time>.meta.json

    Rules:
      - Accepts selecting CSVs and/or `.meta.json` files. Pairs are formed by filename.
      - Each imported test must have both CSV and meta present.
      - Copies files (does not move).
      - If destination CSV already exists, skips that pair.
    """
    paths = [os.path.abspath(str(p or "").strip()) for p in (file_paths or []) if str(p or "").strip()]
    if not paths:
        return {"ok": False, "imported": 0, "skipped": 0, "errors": ["No files selected"], "affected_devices": [], "affected_plate_types": [], "imported_by_device": {}}

    csvs = {p for p in paths if p.lower().endswith(".csv")}
    metas = {p for p in paths if p.lower().endswith(".meta.json")}

    # Derive missing counterparts.
    for m in list(metas):
        csvs.add(m[: -len(".meta.json")] + ".csv")
    for c in list(csvs):
        metas.add(os.path.splitext(c)[0] + ".meta.json")

    if not csvs:
        return {"ok": False, "imported": 0, "skipped": 0, "errors": ["No CSV/meta files selected"], "affected_devices": [], "affected_plate_types": [], "imported_by_device": {}}

    base_dir = data_dir("temp_testing")
    os.makedirs(base_dir, exist_ok=True)

    imported = 0
    skipped = 0
    errors: List[str] = []
    imported_by_device: Dict[str, List[str]] = {}
    affected_devices = set()
    affected_plate_types = set()

    for csv_path in sorted(csvs):
        csv_path = os.path.abspath(csv_path)
        if not os.path.isfile(csv_path):
            errors.append(f"Missing CSV: {csv_path}")
            continue
        fname = os.path.basename(csv_path)
        dev = _device_id_from_raw_filename(fname)
        if not dev:
            errors.append(f"Invalid raw CSV name (expected temp-raw-<device>-YYYYMMDD-HHMMSS.csv): {fname}")
            continue

        meta_path = os.path.splitext(csv_path)[0] + ".meta.json"
        if not os.path.isfile(meta_path):
            errors.append(f"Missing meta JSON for {fname}: {os.path.basename(meta_path)}")
            continue

        dest_dir = os.path.join(base_dir, dev)
        os.makedirs(dest_dir, exist_ok=True)
        dest_csv = os.path.join(dest_dir, fname)
        dest_meta = os.path.join(dest_dir, os.path.basename(meta_path))

        if os.path.isfile(dest_csv):
            skipped += 1
            continue

        try:
            shutil.copy2(csv_path, dest_csv)
            shutil.copy2(meta_path, dest_meta)
            imported += 1
            imported_by_device.setdefault(dev, []).append(dest_csv)
            affected_devices.add(dev)
            pt = dev.split(".", 1)[0].strip() if "." in dev else dev[:2]
            if pt:
                affected_plate_types.add(pt)
        except Exception as exc:
            errors.append(f"Failed to import {fname}: {exc}")
            continue

    return {
        "ok": imported > 0 and not errors,
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "affected_devices": sorted(affected_devices),
        "affected_plate_types": sorted(affected_plate_types),
        "imported_by_device": imported_by_device,
    }


