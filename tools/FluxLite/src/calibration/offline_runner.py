from __future__ import annotations

from typing import Dict

import os
from .. import config
from ..infra.backend_address import backend_address_from_config
from ..infra.http_client import post_json


def _http_base() -> str:
    # Keep this helper for backwards-compat logs, but delegate to canonical infra logic.
    return backend_address_from_config().base_url()


def run_45v(csv_path: str, model_id: str, plate_type: str, device_id: str) -> Dict[str, object]:
    """
    Offline runner adapter for 45V calibration.

    Calls the Python model runner entrypoint and returns the path to the model output CSV.
    Expected output CSV columns: time, fz, copx_mm, copy_mm
    """
    path = str(csv_path or "").strip()
    if not path or not os.path.isfile(path):
        return {"error": "file_not_found"}
    # POST to backend API
    base = _http_base()
    url = f"{base}/api/device/process-csv"
    out_dir = os.path.join(os.path.dirname(path), "calibration_output")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass
    try:
        print(f"[calib] POST {url}")
        print(f"[calib]  csvPath={path}")
        print(f"[calib]  deviceId={device_id}")
        print(f"[calib]  outputDir={out_dir}")
    except Exception:
        pass
    body = {
        "csvPath": path,
        "deviceId": str(device_id or "").strip(),
        "outputDir": out_dir,
    }
    try:
        data = post_json(url, body, timeout_s=20)
        try:
            print("[calib]  -> status=200")
        except Exception:
            pass
        out_csv = data.get("outputPath") or data.get("path")
        try:
            print(f"[calib]  outputPath={out_csv}")
        except Exception:
            pass
        if not out_csv or not os.path.isfile(out_csv):
            # Backend may write remotely; still return the path for downstream use
            return {"processed_csv": str(out_csv or "")}
        return {"processed_csv": str(out_csv)}
    except Exception as e:
        try:
            print(f"[calib] http error: {e}")
        except Exception:
            pass
        return {"error": f"http_error: {e}"}


