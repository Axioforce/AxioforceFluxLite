from __future__ import annotations

import csv
import os
from typing import List

from . import config


HEADERS: List[str] = ["DeviceID", "Pass/Fail", "DateTime", "Tester", "ModelID"]


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def append_summary_row_csv(device_id: str, pass_fail: str, date_text: str, tester: str, model_id: str, path: str | None = None) -> str:
    out_path = (path or config.CSV_EXPORT_PATH or "").strip()
    if not out_path:
        # Default to current working directory if config is empty
        out_path = os.path.join(os.getcwd(), "LiveTesting_Summary.csv")

    ensure_parent_dir(out_path)

    file_exists = os.path.exists(out_path)
    with open(out_path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(HEADERS)
        writer.writerow([device_id, pass_fail, date_text, tester, model_id])

    return out_path


