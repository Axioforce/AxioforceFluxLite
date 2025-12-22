from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

from .. import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackendAddress:
    host: str
    port: int

    def base_url(self) -> str:
        host = (self.host or "").strip() or "http://localhost"
        if not host.startswith("http"):
            host = f"http://{host}"
        host = host.rstrip("/")
        # Normalize: strip any existing port if present
        try:
            head, tail = host.split("://", 1)
            if ":" in tail:
                host = f"{head}://{tail.split(':')[0]}"
        except Exception:
            pass
        return f"{host}:{int(self.port)}"

    def process_csv_url(self) -> str:
        return f"{self.base_url()}/api/device/process-csv"


def _resolve_backend_address(hardware: object | None = None) -> BackendAddress:
    host = getattr(config, "SOCKET_HOST", "http://localhost")
    port = int(getattr(config, "HTTP_PORT", 3001))

    # If hardware service discovered a port, prefer it (same behavior as legacy callers)
    if hardware is not None:
        try:
            hw_port = getattr(hardware, "_http_port", None)
            hw_host = getattr(hardware, "_http_host", None)
            if hw_port:
                port = int(hw_port)
            if hw_host:
                host = str(hw_host)
        except Exception:
            pass

    return BackendAddress(host=str(host), port=int(port))


def process_csv_via_backend(
    *,
    input_csv_path: str,
    device_id: str,
    output_folder: str,
    output_filename: str,
    use_temperature_correction: bool,
    room_temp_f: float,
    mode: str = "scalar",
    temperature_coefficients: Optional[dict] = None,
    sanitize_header: bool = False,
    hardware: object | None = None,
    timeout_s: int = 300,
) -> str:
    """
    Process a CSV through the backend NN via HTTP.

    This is a shared utility used by both the Temperature Testing flow and
    discrete temp plotting features.
    """
    if not os.path.isfile(input_csv_path):
        raise FileNotFoundError(f"Input CSV not found: {input_csv_path}")

    output_folder = os.path.abspath(output_folder)
    os.makedirs(output_folder, exist_ok=True)

    # Some exported CSVs include padded header names like "device_id   " (or a UTF-8 BOM),
    # but the backend expects exact column names (e.g. "device_id") and may also do exact
    # value matching on device_id. Optionally sanitize into a temporary sibling file so we
    # never mutate the original CSV.
    csv_path_for_backend = input_csv_path
    if sanitize_header:
        try:
            base = os.path.basename(input_csv_path)
            sanitized_path = os.path.join(output_folder, f"__sanitized__{base}")

            with open(input_csv_path, "r", encoding="utf-8", newline="") as src:
                reader = csv.reader(src)
                raw_headers = next(reader, [])
                norm_headers = [(h or "").lstrip("\ufeff").strip() for h in raw_headers]

                try:
                    device_id_idx = norm_headers.index("device_id")
                except Exception:
                    device_id_idx = -1

                first_row = next(reader, None)

                needs_rewrite = raw_headers != norm_headers
                if (not needs_rewrite) and first_row is not None and device_id_idx >= 0 and device_id_idx < len(first_row):
                    if first_row[device_id_idx] != (first_row[device_id_idx] or "").strip():
                        needs_rewrite = True

                if needs_rewrite:
                    with open(sanitized_path, "w", encoding="utf-8", newline="") as dst:
                        writer = csv.writer(dst, lineterminator="\n")
                        writer.writerow(norm_headers)

                        def _write_row(r: list[str]) -> None:
                            if device_id_idx >= 0 and device_id_idx < len(r):
                                r[device_id_idx] = (r[device_id_idx] or "").strip()
                            writer.writerow(r)

                        if first_row is not None:
                            _write_row(list(first_row))
                        for row in reader:
                            if row is None:
                                continue
                            _write_row(list(row))

                    csv_path_for_backend = sanitized_path
        except Exception:
            # If sanitization fails for any reason, fall back to original path.
            csv_path_for_backend = input_csv_path

    addr = _resolve_backend_address(hardware)
    url = addr.process_csv_url()

    body: dict = {
        "csvPath": os.path.abspath(csv_path_for_backend),
        "deviceId": str(device_id),
        "outputDir": output_folder,
        "use_temperature_correction": bool(use_temperature_correction),
        "room_temperature_f": float(room_temp_f),
        "mode": str(mode or "scalar"),
    }

    if temperature_coefficients:
        vals = {
            "x": float(temperature_coefficients.get("x", 0.0)),
            "y": float(temperature_coefficients.get("y", 0.0)),
            "z": float(temperature_coefficients.get("z", 0.0)),
        }
        # Backend supports "temperature_correction_coefficients" in scalar mode.
        body["temperature_correction_coefficients"] = vals

    logger.info(f"POST {url} with body keys: {list(body.keys())}")
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, data=json.dumps(body), headers=headers, timeout=int(timeout_s))
    resp.raise_for_status()

    data = resp.json() or {}
    out_csv_path = data.get("outputPath") or data.get("path") or data.get("processed_csv")

    expected_path = os.path.join(output_folder, output_filename)

    if out_csv_path and os.path.isfile(str(out_csv_path)):
        if os.path.abspath(str(out_csv_path)) != os.path.abspath(expected_path):
            try:
                if os.path.exists(expected_path):
                    os.remove(expected_path)
                os.rename(str(out_csv_path), expected_path)
            except Exception as move_err:
                logger.error(f"Failed to move processed file to expected name: {move_err}")
                # Fall back to returning the backend's path
                return str(out_csv_path)
        return expected_path

    # If backend didn't report a usable path, still return expected path if it exists.
    if os.path.isfile(expected_path):
        return expected_path

    raise RuntimeError(f"Backend processed successfully but output file was not found (expected={expected_path}, returned={out_csv_path})")


