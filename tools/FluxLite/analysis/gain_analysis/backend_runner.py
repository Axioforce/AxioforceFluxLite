from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Dict, Optional

import requests


@dataclass(frozen=True)
class BackendConfig:
    host: str  # e.g. "http://localhost"
    port: int  # e.g. 3000
    room_temperature_f: float = 76.0

    def base_url(self) -> str:
        h = (self.host or "").strip()
        if not h.startswith("http"):
            h = f"http://{h}"
        return h.rstrip("/")

    def process_csv_url(self) -> str:
        return f"{self.base_url()}:{int(self.port)}/api/device/process-csv"


def _coef_key(coef: Optional[float]) -> str:
    if coef is None:
        return "off"
    return f"c{coef:.6f}"


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _hash_key(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()[:12]


def _sanitize_csv_headers(input_csv_path: str, cache_dir: str) -> str:
    """
    Backend `process-csv` expects exact header names (e.g. `device_id`) and does not
    always trim whitespace. Some discrete CSVs contain padded headers like `device_id   `.
    This helper writes a sanitized copy with stripped headers and returns its path.
    """
    abs_in = os.path.abspath(input_csv_path)
    st = os.stat(abs_in)
    # Bump the version if sanitize behavior changes so cached inputs are regenerated.
    cache_id = _hash_key(abs_in, str(st.st_mtime_ns), str(st.st_size), "sanitized_v2")
    in_base = os.path.splitext(os.path.basename(abs_in))[0]
    out_dir = os.path.join(cache_dir, "_inputs")
    _safe_mkdir(out_dir)
    out_path = os.path.join(out_dir, f"{in_base}__{cache_id}.csv")
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    import csv

    with open(abs_in, "r", encoding="utf-8", newline="") as src:
        reader = csv.reader(src)
        header = next(reader, None)
        if not header:
            raise ValueError(f"empty csv: {abs_in}")
        header_clean = [str(h or "").strip() for h in header]
        # Identify columns where trimming values is helpful for backend filtering.
        device_id_idx = -1
        try:
            device_id_idx = header_clean.index("device_id")
        except Exception:
            device_id_idx = -1
        with open(out_path, "w", encoding="utf-8", newline="") as dst:
            writer = csv.writer(dst)
            writer.writerow(header_clean)
            for row in reader:
                if device_id_idx >= 0 and device_id_idx < len(row):
                    try:
                        row[device_id_idx] = str(row[device_id_idx] or "").strip()
                    except Exception:
                        pass
                writer.writerow(row)
    return out_path


def process_csv_with_cache(
    cfg: BackendConfig,
    input_csv_path: str,
    device_id: str,
    cache_dir: str,
    coef_z: Optional[float],
    timeout_s: int = 300,
) -> str:
    """
    Call backend `/api/device/process-csv` and cache the resulting processed CSV on disk.

    Returns the cached processed CSV path.
    """
    abs_in = os.path.abspath(input_csv_path)
    if not os.path.isfile(abs_in):
        raise FileNotFoundError(abs_in)

    _safe_mkdir(cache_dir)

    # Sanitize headers to ensure required columns like `device_id` are recognized.
    abs_in_sanitized = _sanitize_csv_headers(abs_in, cache_dir=cache_dir)

    # Build deterministic cache file name
    st = os.stat(abs_in_sanitized)
    cache_id = _hash_key(
        abs_in_sanitized,
        str(st.st_mtime_ns),
        str(st.st_size),
        _coef_key(coef_z),
        f"rt{cfg.room_temperature_f:.1f}",
    )
    base_name = os.path.splitext(os.path.basename(abs_in_sanitized))[0]
    out_name = f"{base_name}__{_coef_key(coef_z)}__{cache_id}.csv"
    cached_path = os.path.join(cache_dir, out_name)

    if os.path.isfile(cached_path) and os.path.getsize(cached_path) > 0:
        return cached_path

    url = cfg.process_csv_url()
    body: Dict[str, object] = {
        "csvPath": abs_in_sanitized,
        "deviceId": str(device_id or "").strip(),
        "outputDir": os.path.abspath(cache_dir),
        "use_temperature_correction": bool(coef_z is not None),
        "room_temperature_f": float(cfg.room_temperature_f),
        "mode": "scalar",
    }
    if coef_z is not None:
        body["temperature_correction_coefficients"] = {"x": 0.0, "y": 0.0, "z": float(coef_z)}

    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, data=json.dumps(body), headers=headers, timeout=timeout_s)
    if resp.status_code >= 400:
        # Include response body to make schema/validation errors debuggable.
        raise RuntimeError(
            f"backend process-csv failed: status={resp.status_code} url={url} resp={resp.text}"
        )
    data = resp.json() or {}
    out_csv_path = data.get("outputPath") or data.get("path") or data.get("processed_csv") or ""
    out_csv_path = str(out_csv_path)
    if not out_csv_path:
        raise RuntimeError(f"backend returned no output path for {abs_in}")

    # Backend may write directly to cache_dir or elsewhere. If it exists, rename into our cache key.
    out_csv_path_abs = os.path.abspath(out_csv_path)
    if os.path.isfile(out_csv_path_abs) and os.path.getsize(out_csv_path_abs) > 0:
        if os.path.abspath(out_csv_path_abs) != os.path.abspath(cached_path):
            # Replace if exists
            try:
                if os.path.exists(cached_path):
                    os.remove(cached_path)
            except Exception:
                pass
            try:
                os.rename(out_csv_path_abs, cached_path)
            except Exception:
                # Cross-device rename fallback: copy then delete
                with open(out_csv_path_abs, "rb") as src, open(cached_path, "wb") as dst:
                    dst.write(src.read())
                try:
                    os.remove(out_csv_path_abs)
                except Exception:
                    pass
        return cached_path

    # If backend wrote remotely and only returned a path, fail loudly (we need local files to parse).
    raise FileNotFoundError(f"processed output not found locally: {out_csv_path_abs}")


