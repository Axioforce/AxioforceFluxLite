from __future__ import annotations

from typing import Dict

import os
import requests
import json
from .. import config


def _http_base() -> str:
    base = str(getattr(config, "SOCKET_HOST", "http://localhost") or "http://localhost").rstrip("/")
    port = int(getattr(config, "HTTP_PORT", 3001))
    if not base.startswith("http://") and not base.startswith("https://"):
        base = f"http://{base}"
    # Replace or add port
    try:
        head, tail = base.split("://", 1)
        host_only = tail.split(":")[0]
        base = f"{head}://{host_only}:{port}"
    except Exception:
        base = f"{base}:{port}"
    return base


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
        resp = requests.post(url, data=json.dumps(body), headers={"Content-Type": "application/json"}, timeout=20)
        try:
            print(f"[calib]  -> status={resp.status_code}")
        except Exception:
            pass
        if resp.status_code // 100 != 2:
            try:
                err = resp.json()
                msg = err.get("message") or resp.text
            except Exception:
                msg = resp.text
            try:
                print(f"[calib]  error: {msg}")
            except Exception:
                pass
            return {"error": f"http_{resp.status_code}: {msg}"}
        data = resp.json() or {}
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


