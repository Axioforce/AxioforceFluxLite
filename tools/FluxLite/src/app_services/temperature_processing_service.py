from __future__ import annotations

import os
from typing import Callable, Dict, Optional

from .backend_csv_processor import process_csv_via_backend


class TemperatureProcessingService:
    """
    Orchestrates temperature test processing:
    - derive paths
    - downsample to 50Hz
    - run backend processing off/on
    - update meta
    """

    def __init__(self, *, repo: object, hardware: object | None) -> None:
        self._repo = repo
        self._hardware = hardware

    def run_temperature_processing(
        self,
        *,
        folder: str,
        device_id: str,
        csv_path: str,
        slopes: dict,
        room_temp_f: float = 72.0,
        mode: str = "legacy",
        status_cb: Callable[[dict], None] | None = None,
    ) -> None:
        def emit(payload: dict) -> None:
            if status_cb is None:
                return
            try:
                status_cb(dict(payload or {}))
            except Exception:
                pass

        if not os.path.isfile(csv_path):
            emit({"status": "error", "message": f"File not found: {csv_path}"})
            return

        if not self._hardware:
            emit({"status": "error", "message": "Hardware service unavailable for temperature correction"})
            return

        try:
            paths = self._repo.derive_temperature_paths(csv_path, device_id, mode)

            slopes_name = self._repo.formatted_slope_name(slopes)
            processed_on_name = paths["processed_on_template"].format(slopes=slopes_name)

            if os.path.isfile(paths["trimmed"]):
                trimmed_path = paths["trimmed"]
                emit({"status": "running", "message": "Using existing 50Hz CSV...", "progress": 5})
            else:
                emit({"status": "running", "message": "Slimming CSV to 50Hz...", "progress": 5})
                trimmed_path = self._repo.downsample_csv_to_50hz(csv_path, paths["trimmed"])

            emit({"status": "running", "message": "Checking baseline...", "progress": 25})

            processed_off_path = os.path.join(folder, paths["processed_off_name"])
            if os.path.isfile(processed_off_path):
                emit({"status": "running", "message": "Using existing baseline...", "progress": 25})
            else:
                emit({"status": "running", "message": "Processing (temp correction off)...", "progress": 25})
                self._call_backend_process_csv(
                    input_csv_path=trimmed_path,
                    device_id=device_id,
                    output_folder=folder,
                    output_filename=paths["processed_off_name"],
                    use_temperature_correction=False,
                    room_temp_f=room_temp_f,
                    slopes=None,
                    mode="legacy",
                )

            emit({"status": "running", "message": "Processing (temp correction on)...", "progress": 65})

            self._call_backend_process_csv(
                input_csv_path=trimmed_path,
                device_id=device_id,
                output_folder=folder,
                output_filename=processed_on_name,
                use_temperature_correction=True,
                room_temp_f=room_temp_f,
                slopes=slopes,
                mode=mode,
            )

            self._repo.update_meta_with_processed(
                paths["meta"],
                trimmed_path,
                os.path.join(folder, paths["processed_off_name"]),
                os.path.join(folder, processed_on_name),
                slopes,
                mode,
            )

            emit({"status": "completed", "message": "Temperature processing complete", "progress": 100})
        except Exception as e:
            emit({"status": "error", "message": str(e)})

    def ensure_temp_off_processed(
        self,
        *,
        folder: str,
        device_id: str,
        csv_path: str,
        room_temp_f: float = 72.0,
        status_cb: Callable[[dict], None] | None = None,
    ) -> str:
        """
        Ensure the "temp correction OFF" processed CSV exists for a given raw temp test.

        Unlike `run_temperature_processing`, this does NOT produce a "temp correction ON"
        variant. It is intended for building per-device room-temp baseline bias.

        Returns the full path to the processed-off CSV.
        """
        def emit(payload: dict) -> None:
            if status_cb is None:
                return
            try:
                status_cb(dict(payload or {}))
            except Exception:
                pass

        if not os.path.isfile(csv_path):
            raise FileNotFoundError(csv_path)
        if not self._hardware:
            raise RuntimeError("Hardware service unavailable for temperature correction")

        paths = self._repo.derive_temperature_paths(csv_path, device_id, mode="legacy")

        if os.path.isfile(paths["trimmed"]):
            trimmed_path = paths["trimmed"]
        else:
            emit({"status": "running", "message": "Slimming baseline CSV to 50Hz...", "progress": 5})
            trimmed_path = self._repo.downsample_csv_to_50hz(csv_path, paths["trimmed"])

        processed_off_path = os.path.join(folder, paths["processed_off_name"])
        if not os.path.isfile(processed_off_path):
            emit({"status": "running", "message": "Processing baseline (temp correction off)...", "progress": 25})
            self._call_backend_process_csv(
                input_csv_path=trimmed_path,
                device_id=device_id,
                output_folder=folder,
                output_filename=paths["processed_off_name"],
                use_temperature_correction=False,
                room_temp_f=room_temp_f,
                slopes=None,
                mode="legacy",
            )

        # Record baseline output in meta for discoverability in the UI and for future reuse.
        try:
            self._repo.update_meta_with_baseline_only(
                paths["meta"],
                trimmed_csv=trimmed_path,
                processed_off=processed_off_path,
            )
        except Exception:
            # Meta updates are best-effort; baseline processing result is still valid.
            pass

        return processed_off_path

    def _call_backend_process_csv(
        self,
        *,
        input_csv_path: str,
        device_id: str,
        output_folder: str,
        output_filename: str,
        use_temperature_correction: bool,
        room_temp_f: float,
        slopes: Optional[dict] = None,
        mode: str = "legacy",
    ) -> None:
        temperature_coefficients = None
        if slopes:
            temperature_coefficients = {
                "x": float(slopes.get("x", 0.0)),
                "y": float(slopes.get("y", 0.0)),
                "z": float(slopes.get("z", 0.0)),
            }

        process_csv_via_backend(
            input_csv_path=input_csv_path,
            device_id=device_id,
            output_folder=output_folder,
            output_filename=output_filename,
            use_temperature_correction=use_temperature_correction,
            room_temp_f=room_temp_f,
            mode=mode,
            temperature_coefficients=temperature_coefficients,
            sanitize_header=True,
            hardware=self._hardware,
            timeout_s=300,
        )


