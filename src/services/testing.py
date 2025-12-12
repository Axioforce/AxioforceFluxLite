from __future__ import annotations
import csv
import datetime
import json
import math
import os
import random
import statistics
import time
from typing import Optional, Tuple, List, Dict, TYPE_CHECKING, Any

import logging

import requests
from PySide6 import QtCore

from .. import config

if TYPE_CHECKING:
    from .hardware import HardwareService
from ..domain.models import TestSession, TestStage, TestResult, TestThresholds, GRID_BY_MODEL

logger = logging.getLogger(__name__)


class TestingService(QtCore.QObject):
    """
    Manages the state and lifecycle of test sessions (Live and Discrete).
    """
    session_started = QtCore.Signal(object) # TestSession
    session_ended = QtCore.Signal(object)   # TestSession
    stage_changed = QtCore.Signal(int)      # new stage index
    cell_updated = QtCore.Signal(int, int, object) # row, col, TestResult
    processing_status = QtCore.Signal(dict) # {status, message, progress}

    def __init__(self, hardware_service: Optional["HardwareService"] = None):
        super().__init__()
        self._current_session: Optional[TestSession] = None
        self._active_cell: Optional[Tuple[int, int]] = None
        self._current_stage_index: int = 0
        self._hardware = hardware_service

    @property
    def current_session(self) -> Optional[TestSession]:
        return self._current_session

    @property
    def active_cell(self) -> Optional[Tuple[int, int]]:
        return self._active_cell

    @property
    def current_stage_index(self) -> int:
        """Return the index of the currently active stage in the session."""
        return int(self._current_stage_index)

    def start_session(self, tester_name: str, device_id: str, model_id: str, body_weight_n: float, thresholds: TestThresholds, is_temp_test: bool = False, is_discrete_temp: bool = False) -> TestSession:
        rows, cols = GRID_BY_MODEL.get(model_id, (3, 3))
        
        session = TestSession(
            tester_name=tester_name,
            device_id=device_id,
            model_id=model_id,
            body_weight_n=body_weight_n,
            thresholds=thresholds,
            grid_rows=rows,
            grid_cols=cols,
            is_temp_test=is_temp_test,
            is_discrete_temp=is_discrete_temp
        )
        
        # Initialize stages (default logic, can be customized)
        # For now, replicate the default stages from the original code
        stages = []
        # Stage 0: 45 lb DB
        stages.append(TestStage(0, "45 lb DB", "A", 200.0, rows * cols)) # Target approx 200N (45lbs)
        # Stage 1: Body Weight
        stages.append(TestStage(1, "Body Weight", "A", body_weight_n, rows * cols))
        
        session.stages = stages
        session.start()
        
        self._current_session = session
        self._current_stage_index = 0
        self.session_started.emit(session)
        return session

    def end_session(self) -> None:
        if self._current_session:
            self._current_session.end()
            self.session_ended.emit(self._current_session)
            self._current_session = None
            self._active_cell = None

    def set_active_cell(self, row: int, col: int) -> None:
        self._active_cell = (row, col)

    def record_result(self, stage_idx: int, row: int, col: int, result: TestResult) -> None:
        if not self._current_session:
            return
            
        if 0 <= stage_idx < len(self._current_session.stages):
            stage = self._current_session.stages[stage_idx]
            stage.results[(row, col)] = result
            self.cell_updated.emit(row, col, result)

    def next_stage(self) -> Optional[int]:
        if not self._current_session or not self._current_session.stages:
            return None
        
        if self._current_stage_index < len(self._current_session.stages) - 1:
            self._current_stage_index += 1
            self.stage_changed.emit(self._current_stage_index)
            return self._current_stage_index
        return None

    def prev_stage(self) -> Optional[int]:
        if not self._current_session or not self._current_session.stages:
            return None
            
        if self._current_stage_index > 0:
            self._current_stage_index -= 1
            self.stage_changed.emit(self._current_stage_index)
            return self._current_stage_index
        return None

    def run_temperature_processing(self, folder: str, device_id: str, csv_path: str, slopes: dict, room_temp_f: float = 72.0) -> None:
        """
        Process a raw CSV file from a temperature test run.
        Generates a processed CSV with temperature-corrected values.
        """
        if not os.path.isfile(csv_path):
            self.processing_status.emit({"status": "error", "message": f"File not found: {csv_path}"})
            return

        if not self._hardware:
            self.processing_status.emit({"status": "error", "message": "Hardware service unavailable for temperature correction"})
            return

        try:
            paths = self._derive_temperature_paths(csv_path, device_id)

            slopes_name = self._formatted_slope_name(slopes)
            processed_on_name = paths["processed_on_template"].format(slopes=slopes_name)

            if os.path.isfile(paths["trimmed"]):
                trimmed_path = paths["trimmed"]
                self.processing_status.emit({"status": "running", "message": "Using existing 50 Hz CSV...", "progress": 5})
            else:
                self.processing_status.emit({"status": "running", "message": "Slimming CSV to 50 Hz...", "progress": 5})
                trimmed_path = self._downsample_csv_to_50hz(csv_path, paths["trimmed"])

            self.processing_status.emit({"status": "running", "message": "Checking baseline...", "progress": 25})
            
            processed_off_path = os.path.join(folder, paths["processed_off_name"])
            if os.path.isfile(processed_off_path):
                self.processing_status.emit({"status": "running", "message": "Using existing baseline...", "progress": 25})
            else:
                self.processing_status.emit({"status": "running", "message": "Processing (temp correction off)...", "progress": 25})
                self._call_backend_process_csv(
                    trimmed_path,
                    device_id,
                    folder,
                    paths["processed_off_name"],
                    use_temperature_correction=False,
                    room_temp_f=room_temp_f,
                    slopes=None, # Ensure no slopes are applied for baseline
                )

            self.processing_status.emit({"status": "running", "message": "Processing (temp correction on)...", "progress": 65})
            # self._configure_temperature_correction(slopes, True, room_temp_f)
            
            self._call_backend_process_csv(
                trimmed_path,
                device_id,
                folder,
                processed_on_name,
                use_temperature_correction=True,
                room_temp_f=room_temp_f,
                slopes=slopes,
            )

            # Restore backend state (off by default)
            # self._configure_temperature_correction(slopes, False, room_temp_f)

            self._update_meta_with_processed(
                paths["meta"],
                trimmed_path,
                os.path.join(folder, paths["processed_off_name"]),
                os.path.join(folder, processed_on_name),
                slopes,
            )

            self.processing_status.emit({"status": "completed", "message": "Temperature processing complete", "progress": 100})
        except Exception as e:
            self.processing_status.emit({"status": "error", "message": str(e)})

    def list_temperature_tests(self, device_id: str) -> List[str]:
        """List available temperature test CSV files for a device."""
        if not device_id:
            return []
            
        # Assuming 'temp_testing' directory in CWD
        base_dir = "temp_testing"
        device_dir = os.path.join(base_dir, device_id)
        
        if not os.path.isdir(device_dir):
            return []
            
        files = []
        try:
            for f in os.listdir(device_dir):
                lower = f.lower()
                if not lower.endswith(".csv"):
                    continue
                if not lower.startswith("temp-raw-"):
                    continue
                files.append(os.path.join(device_dir, f))
        except Exception:
            pass
        
        files = sorted(files)
        for path in files:
            self._ensure_meta_avg_temperature(path)
        return files

    def list_temperature_devices(self) -> List[str]:
        """List available devices (subdirectories) in temp_testing folder."""
        base_dir = "temp_testing"
        if not os.path.isdir(base_dir):
            return []
            
        devices = []
        try:
            for d in os.listdir(base_dir):
                if os.path.isdir(os.path.join(base_dir, d)):
                    devices.append(d)
        except Exception:
            pass
        return sorted(devices)

    def list_discrete_tests(self) -> List[Tuple[str, str, str]]:
        """
        List available discrete temperature tests from the on-disk folder.
        """
        base_dir = "discrete_temp_testing"
        if not os.path.isdir(base_dir):
            return []

        tests: List[Tuple[str, str, str, float]] = []
        try:
            for root, _dirs, files in os.walk(base_dir):
                for fname in files:
                    if not fname.lower().endswith(".csv"):
                        continue
                    path = os.path.join(root, fname)
                    try:
                        rel = os.path.relpath(path, base_dir)
                    except Exception:
                        rel = fname
                    parts = rel.split(os.sep)
                    device_id = parts[0] if len(parts) > 0 else ""
                    date_part = parts[1] if len(parts) > 1 else ""
                    tester = parts[2] if len(parts) > 2 else ""

                    # Build a concise label like "mike • 06.0000000c" (no filename).
                    label_bits = [p for p in (tester, device_id) if p]
                    label = " • ".join(label_bits) if label_bits else (device_id or tester or fname)

                    # Prefer the folder date (e.g. 11-20-2025), fallback to mtime.
                    date_str = ""
                    if date_part:
                        # 11-20-2025 -> 11.20.2025 for display
                        date_str = date_part.replace("-", ".")
                    try:
                        mtime = os.path.getmtime(path)
                    except Exception:
                        mtime = 0.0
                    if not date_str:
                        try:
                            dt = datetime.datetime.fromtimestamp(mtime)
                            date_str = dt.strftime("%m.%d.%Y")
                        except Exception:
                            date_str = ""

                    tests.append((label, date_str, path, float(mtime)))
        except Exception:
            pass

        # Sort newest-first by modification time
        tests.sort(key=lambda x: x[3], reverse=True)
        return [(label, date_str, path) for (label, date_str, path, _mtime) in tests]

    # --- Temperature processing helpers ---------------------------------------

    def _downsample_csv_to_50hz(self, source_csv: str, dest_csv: str) -> str:
        os.makedirs(os.path.dirname(dest_csv), exist_ok=True)
        with open(source_csv, "r", newline="", encoding="utf-8") as fin, open(dest_csv, "w", newline="", encoding="utf-8") as fout:
            reader = csv.reader(fin)
            writer = csv.writer(fout)
            header = next(reader, None)
            if not header:
                raise ValueError("CSV header missing")
            writer.writerow(header)

            headers_map = {h.strip().lower(): i for i, h in enumerate(header)}
            time_idx = -1
            for k in ("time", "time_ms"):
                if k in headers_map:
                    time_idx = headers_map[k]
                    break
            
            if time_idx < 0:
                raise ValueError("CSV missing required 'time' column")

            last_t: Optional[float] = None
            target_interval = 20.0  # 50Hz = 20ms

            for row in reader:
                if len(row) <= time_idx:
                    continue
                try:
                    t_val = float(row[time_idx])
                except Exception:
                    continue
                
                if last_t is None:
                    writer.writerow(row)
                    last_t = t_val
                    continue

                if (t_val - last_t) >= target_interval:
                    writer.writerow(row)
                    last_t = t_val

        return dest_csv

    def _derive_temperature_paths(self, raw_csv: str, device_id: str) -> Dict[str, str]:
        filename = os.path.basename(raw_csv)
        folder = os.path.dirname(raw_csv)
        if not filename.startswith("temp-raw-"):
            raise ValueError("Unexpected filename format for temperature test")
        base_without_prefix = filename[len("temp-raw-") :]
        stem, ext = os.path.splitext(base_without_prefix)

        trimmed = os.path.join(folder, f"temp-trimmed-{base_without_prefix}")
        processed_off = f"temp-processed-{base_without_prefix}"
        # processed_on uses placeholder; final name decided later
        return {
            "trimmed": trimmed,
            "processed_off_name": processed_off,
            "processed_on_template": f"temp-{{slopes}}-{base_without_prefix}",
            "meta": os.path.join(folder, f"temp-raw-{stem}.meta.json"),
        }

    def _meta_path_for_csv(self, csv_path: str) -> str:
        folder = os.path.dirname(csv_path)
        name, _ext = os.path.splitext(os.path.basename(csv_path))
        prefix_mappings = {
            "temp-trimmed-": "temp-raw-",
            "temp-processed-": "temp-raw-",
        }
        for prefix, replacement in prefix_mappings.items():
            if name.startswith(prefix):
                name = replacement + name[len(prefix):]
                break
        if not name.startswith("temp-raw-"):
            name = f"temp-raw-{name.split('-', 1)[-1]}"
        return os.path.join(folder, f"{name}.meta.json")

    def _ensure_meta_avg_temperature(self, csv_path: str) -> None:
        meta_path = self._meta_path_for_csv(csv_path)
        if not os.path.isfile(meta_path):
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                meta = json.load(mf) or {}
        except Exception:
            return
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("avg_temp") is not None:
            return
        avg_temp = self._estimate_avg_temperature_from_csv(csv_path)
        if avg_temp is None:
            return
        meta["avg_temp"] = float(avg_temp)
        try:
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(meta, mf, indent=2, sort_keys=True)
        except Exception:
            pass

    def _estimate_avg_temperature_from_csv(self, csv_path: str, sample_size: int = 100) -> Optional[float]:
        if not os.path.isfile(csv_path):
            return None
        try:
            with open(csv_path, "r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                if not header:
                    return None
                target_names = {"sum-t", "sum_t", "sumt"}
                col_idx = None
                for idx, name in enumerate(header):
                    if name and name.strip().lower() in target_names:
                        col_idx = idx
                        break
                if col_idx is None:
                    return None

                reservoir: List[float] = []
                seen = 0
                for row in reader:
                    if len(row) <= col_idx:
                        continue
                    try:
                        val = float(row[col_idx])
                    except Exception:
                        continue
                    seen += 1
                    if len(reservoir) < sample_size:
                        reservoir.append(val)
                    else:
                        j = random.randint(0, seen - 1)
                        if j < sample_size:
                            reservoir[j] = val
                if not reservoir:
                    return None
                return sum(reservoir) / float(len(reservoir))
        except Exception:
            return None
        return None

    def _format_slopes_label(self, slopes: dict) -> str:
        x = float(slopes.get("x", 0.0))
        y = float(slopes.get("y", 0.0))
        z = float(slopes.get("z", 0.0))
        
        # Check if values are effectively equal (using small epsilon for float comparison)
        if abs(x - y) < 1e-9 and abs(y - z) < 1e-9:
            return f"All: {x:.3f}"

        return ", ".join(
            f"{axis.upper()}={float(slopes.get(axis, 0.0)):.2f}"
            for axis in ("x", "y", "z")
        )

    def _formatted_slope_name(self, slopes: dict) -> str:
        def _fmt(val: object) -> str:
            try:
                as_str = f"{float(val):.3f}".rstrip("0").rstrip(".")
                if not as_str:
                    as_str = "0"
                if "." not in as_str:
                    as_str = f"{as_str}.0"
                return as_str
            except Exception:
                return "0.0"

        return "_".join([_fmt(slopes.get(axis, 0.0)) for axis in ("x", "y", "z")])

    def _normalize_slopes(self, slopes: dict) -> Dict[str, float]:
        return {
            axis: float(slopes.get(axis, 0.0))
            for axis in ("x", "y", "z")
        }

    def _slopes_key(self, slopes: dict) -> tuple:
        normalized = self._normalize_slopes(slopes)
        return tuple(round(normalized.get(axis, 0.0), 6) for axis in ("x", "y", "z"))

    def _append_processed_from_disk(
        self,
        runs: List[Dict[str, object]],
        folder: str,
        base_without_prefix: str,
    ) -> List[Dict[str, object]]:
        if not base_without_prefix or not os.path.isdir(folder):
            return runs
        known_paths = {str(run.get("path") or "") for run in runs}
        baseline_present = any(run.get("is_baseline") for run in runs)
        try:
            files = os.listdir(folder)
        except Exception:
            return runs
        suffix = f"-{base_without_prefix}"
        for fname in files:
            lower = fname.lower()
            if not lower.endswith(".csv"):
                continue
            if base_without_prefix not in fname:
                continue
            if lower.startswith("temp-raw-") or lower.startswith("temp-trimmed-"):
                continue
            full_path = os.path.join(folder, fname)
            if full_path in known_paths:
                continue
            if fname.startswith("temp-processed-"):
                if baseline_present:
                    continue
                runs.append({
                    "label": "Temp Off (Baseline)",
                    "path": full_path,
                    "is_baseline": True,
                })
                baseline_present = True
                known_paths.add(full_path)
                continue
            slopes = self._slopes_from_filename(fname, base_without_prefix)
            if not slopes:
                continue
            runs.append({
                "label": self._format_slopes_label(slopes),
                "path": full_path,
                "is_baseline": False,
                "slopes": slopes,
            })
            known_paths.add(full_path)
        return runs

    def _slopes_from_filename(self, filename: str, base_without_prefix: str) -> Dict[str, float]:
        suffix = f"-{base_without_prefix}"
        if not filename.endswith(suffix):
            return {}
        body = filename[:-len(suffix)]
        if not body.startswith("temp-"):
            return {}
        core = body[len("temp-") :]
        if core.startswith("processed-"):
            return {}
        parts = core.split("_")
        axes = ("x", "y", "z")
        slopes: Dict[str, float] = {}
        for axis, part in zip(axes, parts):
            try:
                slopes[axis] = float(part)
            except Exception:
                slopes[axis] = 0.0
        for axis in axes:
            slopes.setdefault(axis, 0.0)
        return slopes

    def _http_base(self) -> str:
        base = str(getattr(config, "SOCKET_HOST", "http://localhost") or "http://localhost").rstrip("/")
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"http://{base}"
        port = int(getattr(config, "HTTP_PORT", 3001))
        try:
            scheme, rest = base.split("://", 1)
            host_only = rest.split(":")[0]
            return f"{scheme}://{host_only}:{port}"
        except Exception:
            return f"{base}:{port}"

    def _call_backend_process_csv(
        self,
        csv_path: str,
        device_id: str,
        output_dir: str,
        output_filename: str,
        use_temperature_correction: bool = False,
        room_temp_f: float = 72.0,
        slopes: Optional[Dict[str, float]] = None,
    ) -> str:
        url = f"{self._http_base()}/api/device/process-csv"
        os.makedirs(output_dir, exist_ok=True)
        payload = {
            "csvPath": os.path.abspath(csv_path),
            "deviceId": str(device_id).strip(),
            "outputDir": os.path.abspath(output_dir),
            "outputFilename": output_filename,
            "use_temperature_correction": use_temperature_correction,
            "room_temperature_f": room_temp_f,
        }
        
        if slopes:
            payload["temperature_correction"] = {
                "x": float(slopes.get("x", 0.0)),
                "y": float(slopes.get("y", 0.0)),
                "z": float(slopes.get("z", 0.0)),
            }
            
        response = requests.post(url, json=payload, timeout=300)
        if response.status_code // 100 != 2:
            try:
                data = response.json()
                message = data.get("message") or response.text
            except Exception:
                message = response.text
            raise RuntimeError(f"Backend processing failed: {message}")
        data = response.json() or {}
        output_path = data.get("outputPath") or os.path.join(output_dir, output_filename)
        return str(output_path)

    def _configure_temperature_correction(self, slopes: dict, enabled: bool, room_temp_f: float) -> None:
        try:
            self._hardware.configure_temperature_correction(
                {
                    "x": float(slopes.get("x", 0.0)),
                    "y": float(slopes.get("y", 0.0)),
                    "z": float(slopes.get("z", 0.0)),
                },
                bool(enabled),
                float(room_temp_f),
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to configure backend temperature correction: {exc}") from exc

    def _update_meta_with_processed(
        self,
        meta_path: str,
        trimmed_csv: str,
        processed_off: str,
        processed_on: str,
        slopes: dict,
    ) -> None:
        meta: Dict[str, object] = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf) or {}
            except Exception:
                meta = {}

        now_ms = int(time.time() * 1000)
        slopes_clean = self._normalize_slopes(slopes)

        baseline_payload = {
            "trimmed_csv": os.path.basename(trimmed_csv),
            "processed_off": os.path.basename(processed_off),
            "updated_at_ms": now_ms,
        }
        meta["processed_baseline"] = baseline_payload

        variant = {
            "processed_on": os.path.basename(processed_on),
            "slopes": slopes_clean,
            "processed_at_ms": now_ms,
        }
        variants = meta.get("processed_variants")
        if not isinstance(variants, list):
            variants = []
        key = self._slopes_key(slopes_clean)
        replaced = False
        for entry in variants:
            if self._slopes_key(entry.get("slopes") or {}) == key:
                entry.update(variant)
                replaced = True
                break
        if not replaced:
            variants.append(variant)
        meta["processed_variants"] = variants

        # Maintain legacy field for backward compatibility
        legacy = dict(variant)
        legacy.update(
            {
                "trimmed_csv": baseline_payload["trimmed_csv"],
                "processed_off": baseline_payload["processed_off"],
            }
        )
        meta["processed"] = legacy

        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, indent=2, sort_keys=True)

    def get_temperature_test_details(self, csv_path: str) -> Dict[str, object]:
        meta_path = self._meta_path_for_csv(csv_path)
        meta: Dict[str, object] = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf) or {}
            except Exception:
                meta = {}

        stage_names = ["All"]
        seen = set()
        for evt in meta.get("stage_events", []):
            name = (evt or {}).get("stage_name")
            if name and name not in seen:
                seen.add(name)
                stage_names.append(name)

        folder = os.path.dirname(csv_path)
        processed_runs: List[Dict[str, object]] = []
        base_without_prefix = ""
        filename = os.path.basename(csv_path)
        if filename.startswith("temp-raw-"):
            base_without_prefix = filename[len("temp-raw-") :]

        # Keep track of known paths to avoid duplicates
        known_paths = set()

        baseline_added = False
        baseline_info = meta.get("processed_baseline")
        if isinstance(baseline_info, dict):
            processed_off = baseline_info.get("processed_off")
            if processed_off:
                path = os.path.join(folder, processed_off)
                if os.path.isfile(path):
                    processed_runs.append({
                        "label": "Temp Off (Baseline)",
                        "path": path,
                        "is_baseline": True,
                    })
                    baseline_added = True
                    known_paths.add(path)

        legacy_processed = meta.get("processed") if isinstance(meta, dict) else None
        if not baseline_added and isinstance(legacy_processed, dict):
            processed_off = legacy_processed.get("processed_off")
            if processed_off:
                path = os.path.join(folder, processed_off)
                if os.path.isfile(path):
                    processed_runs.append({
                        "label": "Temp Off (Baseline)",
                        "path": path,
                        "is_baseline": True,
                    })
                    baseline_added = True
                    known_paths.add(path)

        variant_entries: List[Dict[str, object]] = []
        stored_variants = meta.get("processed_variants")
        if isinstance(stored_variants, list):
            variant_entries.extend(stored_variants)
        if not variant_entries and isinstance(legacy_processed, dict):
            variant_entries.append(legacy_processed)

        seen_variant_paths: set = set()
        for variant in variant_entries:
            if not isinstance(variant, dict):
                continue
            processed_on = variant.get("processed_on")
            if not processed_on:
                continue
            path = os.path.join(folder, processed_on)
            if path in seen_variant_paths:
                continue
            if not os.path.isfile(path):
                continue
            seen_variant_paths.add(path)
            slopes = variant.get("slopes", {})
            processed_runs.append({
                "label": self._format_slopes_label(slopes),
                "path": path,
                "is_baseline": False,
                "slopes": slopes,
            })
            known_paths.add(path)

        final_runs = self._append_processed_from_disk(
            processed_runs,
            folder,
            base_without_prefix,
        )
        
        # Sort runs: Baseline first, then others sorted by Z slope (high to low)
        baseline_runs = [r for r in final_runs if r.get("is_baseline")]
        other_runs = [r for r in final_runs if not r.get("is_baseline")]
        
        def _get_sort_key(r):
            slopes = r.get("slopes", {})
            # Sort by Z, then Y, then X (all descending)
            return (
                float(slopes.get("z", 0.0)), 
                float(slopes.get("y", 0.0)), 
                float(slopes.get("x", 0.0))
            )
            
        other_runs.sort(key=_get_sort_key, reverse=True)
        
        return {
            "meta": meta,
            "stage_names": stage_names,
            "processed_runs": baseline_runs + other_runs,
        }

    def analyze_temperature_processed_runs(
        self,
        baseline_csv: str,
        selected_csv: str,
        meta: Optional[Dict[str, object]] = None,
        baseline_data: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        """
        Evaluate processed CSVs (temp correction off/on) and derive per-cell accuracy data.
        
        Windows are synced between baseline and selected: if a valid window is found
        in one file, that same time range is used for both, ensuring fair comparison.
        """
        logger.info(
            "temperature.analyze.start baseline=%s selected=%s",
            os.path.basename(baseline_csv),
            os.path.basename(selected_csv),
        )
        meta = dict(meta or {})
        device_type = self._infer_device_type(meta)
        rows, cols = getattr(config, "GRID_DIMS_BY_MODEL", {}).get(device_type, (3, 3))
        stage_configs = self._stage_configs_for_meta(meta)

        # First pass: analyze baseline to find valid windows
        if baseline_data:
            baseline = baseline_data
            baseline_windows = baseline.get("_windows", {})
        else:
            baseline = self._analyze_single_processed_csv(
                baseline_csv,
                stage_configs,
                rows,
                cols,
                device_type,
            )
            baseline_windows = baseline.get("_windows", {})

        # Force selected run to use exactly the same windows as the baseline
        # This ensures fair comparison on the same time segments and keeps baseline constant
        if baseline_windows:
            selected = self._analyze_with_forced_windows(
                selected_csv, stage_configs, rows, cols, device_type, baseline_windows
            )
        else:
            # Fallback: if baseline found nothing, analyze selected independently
            selected = self._analyze_single_processed_csv(
                selected_csv,
                stage_configs,
                rows,
                cols,
                device_type,
            )

        return {
            "grid": {
                "rows": rows,
                "cols": cols,
                "device_type": device_type,
            },
            "meta": {
                "device_id": meta.get("device_id"),
                "model_id": meta.get("model_id"),
                "body_weight_n": meta.get("body_weight_n"),
            },
            "stage_order": [cfg["key"] for cfg in stage_configs],
            "baseline": baseline,
            "selected": selected,
        }

    def _merge_windows(
        self,
        windows_a: Dict[str, Dict[Tuple[int, int], Dict]],
        windows_b: Dict[str, Dict[Tuple[int, int], Dict]],
    ) -> Dict[str, Dict[Tuple[int, int], Dict]]:
        """Merge windows from two analyses, keeping all unique cell windows."""
        merged: Dict[str, Dict[Tuple[int, int], Dict]] = {}
        all_stages = set(windows_a.keys()) | set(windows_b.keys())
        
        for stage_key in all_stages:
            merged[stage_key] = {}
            cells_a = windows_a.get(stage_key, {})
            cells_b = windows_b.get(stage_key, {})
            all_cells = set(cells_a.keys()) | set(cells_b.keys())
            
            for cell in all_cells:
                # Prefer the window with lower std (more stable)
                win_a = cells_a.get(cell)
                win_b = cells_b.get(cell)
                
                if win_a and win_b:
                    # Both have this cell - pick the one with lower score (more stable)
                    if win_a.get("score", (999,999)) <= win_b.get("score", (999,999)):
                        merged[stage_key][cell] = win_a
                    else:
                        merged[stage_key][cell] = win_b
                elif win_a:
                    merged[stage_key][cell] = win_a
                elif win_b:
                    merged[stage_key][cell] = win_b
        
        return merged

    def _analyze_with_forced_windows(
        self,
        csv_path: str,
        stage_configs: List[Dict[str, object]],
        rows: int,
        cols: int,
        device_type: str,
        forced_windows: Dict[str, Dict[Tuple[int, int], Dict]],
    ) -> Dict[str, object]:
        """
        Analyze a CSV using forced time windows instead of detecting them.
        This ensures both baseline and selected use the exact same time ranges.
        """
        stage_map = {
            cfg["key"]: {
                "key": cfg["key"],
                "name": cfg["name"],
                "target_n": cfg["target_n"],
                "tolerance_n": cfg["tolerance_n"],
                "cells": [],
            }
            for cfg in stage_configs
        }
        
        if not csv_path or not os.path.isfile(csv_path):
            return {"stages": stage_map}
        
        # Load all data from CSV
        times, fz_vals, copx_vals, copy_vals = self._load_csv_for_analysis(csv_path)
        if not times:
            return {"stages": stage_map}
        
        cfg_by_key = {cfg["key"]: cfg for cfg in stage_configs}
        
        # For each forced window, extract data from that exact time range
        for stage_key, cells in forced_windows.items():
            cfg = cfg_by_key.get(stage_key)
            if not cfg:
                continue
            
            target_n = float(cfg.get("target_n", 0.0))
            tolerance_n = float(cfg.get("tolerance_n", 0.0))
            
            for (row, col), win_info in cells.items():
                t_start = win_info.get("t_start", 0)
                t_end = win_info.get("t_end", 0)
                
                # Find samples in this time range
                window_fz = []
                window_x = []
                window_y = []
                for i, t in enumerate(times):
                    if t_start <= t <= t_end:
                        window_fz.append(fz_vals[i])
                        if i < len(copx_vals):
                            window_x.append(copx_vals[i])
                        if i < len(copy_vals):
                            window_y.append(copy_vals[i])
                
                if not window_fz:
                    continue
                
                mean_fz = sum(window_fz) / len(window_fz)
                mean_x = sum(window_x) / len(window_x) if window_x else 0.0
                mean_y = sum(window_y) / len(window_y) if window_y else 0.0
                
                signed_pct = ((mean_fz - target_n) / target_n * 100.0) if target_n else 0.0
                abs_ratio = abs(mean_fz - target_n) / tolerance_n if tolerance_n else 0.0
                
                stage_map[stage_key]["cells"].append({
                    "row": row,
                    "col": col,
                    "mean_n": float(mean_fz),
                    "signed_pct": float(signed_pct),
                    "abs_ratio": float(abs_ratio),
                    "cop": {"x": float(mean_x), "y": float(mean_y)},
                })
        
        return {"stages": stage_map, "_windows": forced_windows, "_segments": []}

    def _load_csv_for_analysis(self, csv_path: str) -> Tuple[List[float], List[float], List[float], List[float]]:
        """Load time, Fz, COPx, COPy from a processed CSV for analysis."""
        times: List[float] = []
        fz_vals: List[float] = []
        copx_vals: List[float] = []
        copy_vals: List[float] = []
        
        try:
            with open(csv_path, "r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                if not header:
                    return times, fz_vals, copx_vals, copy_vals
                
                headers_map = {h.strip().lower(): i for i, h in enumerate(header)}
                
                time_idx = -1
                for k in ("time", "time_ms", "elapsed_time"):
                    if k in headers_map:
                        time_idx = headers_map[k]
                        break
                
                fz_idx = -1
                for k in ("sum-z", "sum_z", "fz"):
                    if k in headers_map:
                        fz_idx = headers_map[k]
                        break
                
                copx_idx = -1
                for k in ("copx", "cop_x"):
                    if k in headers_map:
                        copx_idx = headers_map[k]
                        break
                
                copy_idx = -1
                for k in ("copy", "cop_y"):
                    if k in headers_map:
                        copy_idx = headers_map[k]
                        break
                
                if time_idx < 0 or fz_idx < 0:
                    return times, fz_vals, copx_vals, copy_vals
                
                for row in reader:
                    if len(row) <= max(time_idx, fz_idx):
                        continue
                    try:
                        t = float(row[time_idx])
                        fz = float(row[fz_idx])
                        # COP is in meters from backend, convert to mm
                        copx = float(row[copx_idx]) * 1000.0 if copx_idx >= 0 and copx_idx < len(row) else 0.0
                        copy = float(row[copy_idx]) * 1000.0 if copy_idx >= 0 and copy_idx < len(row) else 0.0
                    except (ValueError, IndexError):
                        continue
                    times.append(t)
                    fz_vals.append(fz)
                    copx_vals.append(copx)
                    copy_vals.append(copy)
        except Exception:
            pass
        
        return times, fz_vals, copx_vals, copy_vals

    # --- Discrete temperature test analysis ---------------------------------

    def analyze_discrete_temp_csv(self, csv_path: str) -> Tuple[bool, List[float]]:
        """
        Analyze a discrete_temp_session.csv-style file and return:
          - includes_baseline: whether any session temp is within the 74–78°F window
          - temps_f: list of non-baseline session temps (°F), sorted high → low

        This mirrors the legacy _on_discrete_test_selected behavior from the
        pre-refactor MainWindow, but is UI-agnostic so controllers/views can
        consume it cleanly.
        """
        includes_baseline = False
        temps_f: List[float] = []

        if not csv_path or not os.path.isfile(csv_path):
            return includes_baseline, temps_f

        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, skipinitialspace=True)
                sessions: Dict[str, List[float]] = {}
                for row in reader:
                    if not row:
                        continue
                    # Normalize keys defensively
                    clean_row = { (k.strip() if k else k): v for k, v in row.items() if k }
                    key = str(clean_row.get("time") or "").strip()
                    if not key:
                        continue
                    try:
                        temp_val = float(clean_row.get("sum-t") or 0.0)
                    except Exception:
                        continue
                    sessions.setdefault(key, []).append(temp_val)

            if not sessions:
                return includes_baseline, temps_f

            # Average per-session temperature
            session_temps: List[float] = []
            for vals in sessions.values():
                if not vals:
                    continue
                avg = sum(vals) / float(len(vals))
                session_temps.append(avg)

            if not session_temps:
                return includes_baseline, temps_f

            baseline_low = 74.0
            baseline_high = 78.0
            non_baseline: List[float] = []
            for t in session_temps:
                if baseline_low <= t <= baseline_high:
                    includes_baseline = True
                else:
                    non_baseline.append(t)

            temps_f = sorted(non_baseline, reverse=True)
        except Exception:
            includes_baseline = False
            temps_f = []

        return includes_baseline, temps_f

    # --- Temperature heatmap helpers -----------------------------------------

    def _stage_configs_for_meta(self, meta: Dict[str, object]) -> List[Dict[str, object]]:
        configs: List[Dict[str, object]] = []
        min_duration = int(getattr(config, "TEMP_STAGE_MIN_DURATION_MS", 2000))
        window_ms = int(getattr(config, "TEMP_ANALYSIS_WINDOW_MS", 1000))
        window_tol = int(getattr(config, "TEMP_ANALYSIS_WINDOW_TOL_MS", 200))
        min_force = float(getattr(config, "TEMP_MIN_FORCE_N", 100.0))

        db_target = float(getattr(config, "TEMP_DB_TARGET_N", 45.0 * 4.44822))
        db_tol = float(getattr(config, "TEMP_DB_TOL_N", 100.0))
        if db_target > 0.0 and db_tol > 0.0:
            configs.append(
                {
                    "key": "db",
                    "name": "45 lb DB",
                    "target_n": db_target,
                    "tolerance_n": db_tol,
                    "min_duration_ms": min_duration,
                    "window_ms": window_ms,
                    "window_tol_ms": window_tol,
                    "min_force_n": min_force,
                }
            )

        bw_target = float(meta.get("body_weight_n") or 0.0)
        bw_tol = float(getattr(config, "TEMP_BW_TOL_N", 200.0))
        if bw_target > 0.0 and bw_tol > 0.0:
            configs.append(
                {
                    "key": "bw",
                    "name": "Body Weight",
                    "target_n": bw_target,
                    "tolerance_n": bw_tol,
                    "min_duration_ms": min_duration,
                    "window_ms": window_ms,
                    "window_tol_ms": window_tol,
                    "min_force_n": min_force,
                }
            )

        return configs

    def _analyze_single_processed_csv(
        self,
        csv_path: Optional[str],
        stage_configs: List[Dict[str, object]],
        rows: int,
        cols: int,
        device_type: str,
    ) -> Dict[str, object]:
        stage_map = {
            cfg["key"]: {
                "key": cfg["key"],
                "name": cfg["name"],
                "target_n": cfg["target_n"],
                "tolerance_n": cfg["tolerance_n"],
                "cells": [],
            }
            for cfg in stage_configs
        }
        if not csv_path or not os.path.isfile(csv_path) or not stage_configs:
            return {"stages": stage_map}

        logger.info(
            "temperature.analyze.csv start path=%s device=%s rows=%s cols=%s",
            os.path.basename(csv_path),
            device_type,
            rows,
            cols,
        )
        segments = self._collect_stage_segments(csv_path, stage_configs, rows, cols, device_type)
        best_per_stage: Dict[str, Dict[Tuple[int, int], Dict[str, float]]] = {cfg["key"]: {} for cfg in stage_configs}

        cfg_by_key = {cfg["key"]: cfg for cfg in stage_configs}
        for segment in segments:
            cfg = cfg_by_key.get(segment["stage_key"])
            if not cfg:
                continue
            metrics = self._evaluate_segment(segment, cfg)
            if not metrics:
                continue
            cell_key = (int(metrics["row"]), int(metrics["col"]))
            current = best_per_stage[segment["stage_key"]].get(cell_key)
            if current is None or metrics["score"] < current["score"]:
                best_per_stage[segment["stage_key"]][cell_key] = metrics

        # Build windows dict for syncing with other files
        windows: Dict[str, Dict[Tuple[int, int], Dict]] = {cfg["key"]: {} for cfg in stage_configs}
        
        for stage_key, cells_dict in best_per_stage.items():
            logger.info(
                "temperature.analyze.stage summary stage=%s cells=%s",
                stage_key,
                len(cells_dict),
            )
            for cell_key, metrics in cells_dict.items():
                payload = dict(metrics)
                score = payload.pop("score", None)
                stage_map[stage_key]["cells"].append(payload)
                
                # Store window info for syncing
                windows[stage_key][cell_key] = {
                    "t_start": metrics.get("t_start", 0),
                    "t_end": metrics.get("t_end", 0),
                    "score": score,
                }

        # Extract simplified segment info (candidates) for visualization
        candidate_segments = []
        for seg in segments:
            samples = seg.get("samples") or []
            if samples:
                candidate_segments.append({
                    "stage_key": seg.get("stage_key"),
                    "cell": seg.get("cell"),
                    "t_start": samples[0][0],
                    "t_end": samples[-1][0],
                })
        
        logger.info(
            "temperature.analyze.csv done path=%s",
            os.path.basename(csv_path),
        )

        return {"stages": stage_map, "_windows": windows, "_segments": candidate_segments}

    def _collect_stage_segments(
        self,
        csv_path: str,
        stage_configs: List[Dict[str, object]],
        rows: int,
        cols: int,
        device_type: str,
    ) -> List[Dict[str, object]]:
        segments: List[Dict[str, object]] = []
        cfg_by_key = {cfg["key"]: cfg for cfg in stage_configs}
        current: Optional[Dict[str, object]] = None

        def close_current() -> None:
            nonlocal current
            if not current:
                return
            cfg = cfg_by_key.get(current["stage_key"])
            samples: List[Tuple[int, float, float, float]] = current.get("samples") or []
            if cfg and samples:
                duration = samples[-1][0] - samples[0][0]
                if duration >= int(cfg.get("min_duration_ms", 2000)):
                    segments.append(current)
            current = None

        try:
            with open(csv_path, "r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                if not header:
                    return segments
                
                # Map headers to indices
                headers_map = {h.strip().lower(): i for i, h in enumerate(header)}
                time_idx = -1
                for k in ("time", "time_ms", "elapsed_time"):
                     if k in headers_map:
                         time_idx = headers_map[k]
                         break
                
                fz_idx = -1
                for k in ("sum-z", "sum_z", "fz"):
                    if k in headers_map:
                        fz_idx = headers_map[k]
                        break
                
                copx_idx = -1
                for k in ("copx", "cop_x"):
                    if k in headers_map:
                        copx_idx = headers_map[k]
                        break
                        
                copy_idx = -1
                for k in ("copy", "cop_y"):
                    if k in headers_map:
                        copy_idx = headers_map[k]
                        break
                        
                if time_idx < 0 or fz_idx < 0 or copx_idx < 0 or copy_idx < 0:
                    return segments

                warmup_skip_ms = int(getattr(config, "TEMP_WARMUP_SKIP_MS", 20000))
                first_t_ms: Optional[int] = None
                
                for row in reader:
                    if len(row) <= max(time_idx, fz_idx, copx_idx, copy_idx):
                        continue
                    try:
                        t_ms = int(float(row[time_idx]))
                        fz = float(row[fz_idx])
                        # TODO: Remove ×1000 once backend outputs COP in mm instead of m
                        copx = float(row[copx_idx]) * 1000.0
                        copy = float(row[copy_idx]) * 1000.0
                    except (ValueError, IndexError):
                        continue
                    
                    # Track first timestamp to compute relative time
                    if first_t_ms is None:
                        first_t_ms = t_ms
                    
                    # Skip warmup period (first 20 seconds)
                    if (t_ms - first_t_ms) < warmup_skip_ms:
                        continue

                    cell = self._map_cop_to_cell(device_type, rows, cols, copx, copy)
                    stage_cfg = self._match_stage(fz, stage_configs)
                    if cell is None or stage_cfg is None:
                        close_current()
                        continue

                    stage_key = stage_cfg["key"]
                    # Check if we need to break segment: different stage, different cell, or large jump in COP
                    if current:
                        should_close = False
                        if current["stage_key"] != stage_key:
                            should_close = True
                        elif current["cell"] != cell:
                            # IMPORTANT: This is where we detect if we've moved to a new cell
                            should_close = True
                        else:
                            # Check COP stability
                            # 1. Instantaneous jump (detect fast movements/lifts)
                            last_sample = current["samples"][-1]
                            last_x, last_y = last_sample[2], last_sample[3]
                            dist_jump = math.sqrt((copx - last_x)**2 + (copy - last_y)**2)
                            
                            # 2. Cumulative drift (detect slow sliding across the plate)
                            start_sample = current["samples"][0]
                            start_x, start_y = start_sample[2], start_sample[3]
                            dist_drift = math.sqrt((copx - start_x)**2 + (copy - start_y)**2)
                            
                            max_drift = float(getattr(config, "TEMP_COP_MAX_DISPLACEMENT_MM", 100.0))
                            
                            # 10mm jump between 20ms samples is 0.5m/s - fairly fast
                            if dist_jump > 20.0: 
                                should_close = True
                            elif dist_drift > max_drift:
                                should_close = True
                        
                        if should_close:
                            close_current()

                    if current is None:
                        current = {
                            "stage_key": stage_key,
                            "cell": cell,
                            "samples": [],
                        }
                    current["samples"].append((t_ms, fz, copx, copy))
            
            logger.info(
                "temperature.analyze.csv segments path=%s count=%s",
                os.path.basename(csv_path),
                len(segments),
            )
        except Exception:
            close_current()
            return segments

        close_current()
        return segments

    def _match_stage(self, fz: float, stage_configs: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
        for cfg in stage_configs:
            target = float(cfg.get("target_n") or 0.0)
            tol = float(cfg.get("tolerance_n") or 0.0)
            min_force = float(cfg.get("min_force_n") or 0.0)
            if target <= 0.0 or tol <= 0.0:
                continue
            if fz < min_force:
                continue
            if abs(fz - target) <= tol:
                return cfg
        return None

    def _evaluate_segment(self, segment: Dict[str, object], stage_cfg: Dict[str, object]) -> Optional[Dict[str, object]]:
        samples: List[Tuple[int, float, float, float]] = segment.get("samples") or []
        if not samples:
            return None
        
        desired_ms = int(stage_cfg.get("window_ms", 1000))
        tolerance_ms = int(stage_cfg.get("window_tol_ms", 200))
        
        best = self._select_best_window_optimized(
            samples,
            desired_ms,
            tolerance_ms,
        )
        if not best:
            return None

        target = float(stage_cfg.get("target_n") or 0.0)
        tolerance = float(stage_cfg.get("tolerance_n") or 0.0)
        mean_fz = best["mean_fz"]
        signed_pct = ((mean_fz - target) / target * 100.0) if target else 0.0
        abs_ratio = abs(mean_fz - target) / tolerance if tolerance else 0.0
        row_idx, col_idx = segment["cell"]

        return {
            "row": int(row_idx),
            "col": int(col_idx),
            "mean_n": float(mean_fz),
            "signed_pct": float(signed_pct),
            "abs_ratio": float(abs_ratio),
            "cop": {"x": float(best["mean_x"]), "y": float(best["mean_y"])},
            "score": (best["std"], abs(best["slope"])),
            "t_start": float(best.get("t_start", 0)),
            "t_end": float(best.get("t_end", 0)),
        }

    def _select_best_window_optimized(
        self,
        samples: List[Tuple[int, float, float, float]],
        desired_ms: int,
        tolerance_ms: int,
    ) -> Optional[Dict[str, float]]:
        """
        Optimized sliding window search (O(N)) using running sums.
        samples: list of (t, fz, x, y)
        """
        if not samples:
            return None
        
        n = len(samples)
        min_duration = max(200, desired_ms - tolerance_ms)
        max_duration = desired_ms + tolerance_ms
        
        best_stats: Optional[Dict[str, float]] = None
        best_std = float("inf")
        best_slope = float("inf")

        # Running sums variables
        sum_t = 0.0
        sum_fz = 0.0
        sum_x = 0.0
        sum_y = 0.0
        sum_fz2 = 0.0 # sum(fz^2) for std dev
        sum_t2 = 0.0 # sum(t^2) for slope
        sum_tfz = 0.0 # sum(t*fz) for slope
        
        left = 0
        # Initialize with first sample? No, window starts empty
        
        for right in range(n):
            # Add sample at right
            t, fz, x, y = samples[right]
            # To avoid large numbers with t^2 (timestamp squared), center t around first sample?
            # Or just use relative time. Let's use relative time to keep precision.
            # BUT, the samples tuple has absolute t.
            # For running sums, we can shift t by samples[0][0] for numerical stability.
            t_rel = (t - samples[0][0]) / 1000.0 # seconds
            
            sum_t += t_rel
            sum_fz += fz
            sum_x += x
            sum_y += y
            sum_fz2 += fz * fz
            sum_t2 += t_rel * t_rel
            sum_tfz += t_rel * fz
            
            # Shrink from left if too long
            while left < right:
                duration = samples[right][0] - samples[left][0]
                if duration <= max_duration:
                    break
                
                # Remove sample at left
                tl, fzl, xl, yl = samples[left]
                tl_rel = (tl - samples[0][0]) / 1000.0
                
                sum_t -= tl_rel
                sum_fz -= fzl
                sum_x -= xl
                sum_y -= yl
                sum_fz2 -= fzl * fzl
                sum_t2 -= tl_rel * tl_rel
                sum_tfz -= tl_rel * fzl
                left += 1
            
            # Check validity
            count = right - left + 1
            if count < 2:
                continue
                
            duration = samples[right][0] - samples[left][0]
            if duration < min_duration:
                continue
                
            # Calculate stats from sums
            # Mean
            mean_fz = sum_fz / count
            
            # Std Dev (population or sample? fast formula usually pop, but can adjust)
            # Var = E[X^2] - (E[X])^2
            # Sample Var = (Sum(X^2) - (Sum(X)^2)/N) / (N-1)
            variance_num = sum_fz2 - (sum_fz * sum_fz / count)
            if variance_num < 0: variance_num = 0 # floating point noise
            std = math.sqrt(variance_num / (count - 1)) if count > 1 else 0.0
            
            # Slope
            # m = (N*Sum(xy) - Sum(x)Sum(y)) / (N*Sum(x^2) - (Sum(x))^2)
            slope_num = count * sum_tfz - sum_t * sum_fz
            slope_den = count * sum_t2 - sum_t * sum_t
            if abs(slope_den) < 1e-9:
                slope = 0.0
            else:
                slope = slope_num / slope_den
                
            slope_abs = abs(slope)
            
            # Update best
            if std < best_std - 1e-6 or (abs(std - best_std) <= 1e-6 and slope_abs < best_slope):
                best_std = std
                best_slope = slope_abs
                best_stats = {
                    "std": std,
                    "slope": slope_abs,  # Storing abs slope to match logic
                    "mean_fz": mean_fz,
                    "mean_x": sum_x / count,
                    "mean_y": sum_y / count,
                    "t_start": float(samples[left][0]),
                    "t_end": float(samples[right][0]),
                }

        return best_stats

    # Legacy method kept if needed, but _evaluate_segment calls _select_best_window_optimized now
    def _select_best_window(
        self,
        samples: List[Tuple[int, float, float, float]],
        desired_ms: int,
        tolerance_ms: int,
    ) -> Optional[Dict[str, float]]:
        return self._select_best_window_optimized(samples, desired_ms, tolerance_ms)

    def _compute_window_stats(
        self,
        window_samples: List[Tuple[int, float, float, float]],
    ) -> Optional[Dict[str, float]]:
        # Keeping for backward compat or testing if needed
        if len(window_samples) < 2:
            return None
        times = [s[0] for s in window_samples]
        fz_vals = [s[1] for s in window_samples]
        x_vals = [s[2] for s in window_samples]
        y_vals = [s[3] for s in window_samples]
        try:
            std = statistics.pstdev(fz_vals)
        except statistics.StatisticsError:
            std = 0.0
        slope = self._compute_slope(times, fz_vals)
        mean_fz = sum(fz_vals) / len(fz_vals)
        mean_x = sum(x_vals) / len(x_vals)
        mean_y = sum(y_vals) / len(y_vals)
        return {
            "std": float(std),
            "slope": float(slope),
            "mean_fz": float(mean_fz),
            "mean_x": float(mean_x),
            "mean_y": float(mean_y),
        }

    def _compute_slope(self, times_ms: List[int], values: List[float]) -> float:
        if len(times_ms) < 2:
            return 0.0
        # Note: Optimized window doesn't smooth, for parity we might skip smoothing or implement it differently.
        # The original code smoothed before slope.
        # Fast sliding window slope works on raw data. Smoothing is O(N) pre-pass if needed.
        # For performance, we skip smoothing in the optimized path as raw slope is usually fine for this metric
        # or effectively similar over 1000ms window.
        
        smoothed = self._smooth_series(
            values,
            max(1, int(getattr(config, "TEMP_SLOPE_SMOOTHING_WINDOW", 5))),
        )
        x = [(t - times_ms[0]) / 1000.0 for t in times_ms]
        mean_x = sum(x) / len(x)
        mean_y = sum(smoothed) / len(smoothed)
        denom = sum((xi - mean_x) ** 2 for xi in x)
        if denom == 0.0:
            return 0.0
        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, smoothed))
        return num / denom

    def _smooth_series(self, values: List[float], window: int) -> List[float]:
        if window <= 1 or len(values) <= 2:
            return list(values)
        half = window // 2
        smoothed: List[float] = []
        for idx in range(len(values)):
            start = max(0, idx - half)
            end = min(len(values), idx + half + 1)
            segment = values[start:end]
            smoothed.append(sum(segment) / float(len(segment)))
        return smoothed

    def _map_cop_to_cell(
        self,
        device_type: str,
        rows: int,
        cols: int,
        x_mm: Optional[float],
        y_mm: Optional[float],
    ) -> Optional[Tuple[int, int]]:
        if x_mm is None or y_mm is None:
            return None
        dev = (device_type or "").strip()
        if dev == "07" or dev == "11":
            w_mm = config.TYPE07_W_MM if dev == "07" else config.TYPE11_W_MM
            h_mm = config.TYPE07_H_MM if dev == "07" else config.TYPE11_H_MM
            half_w = w_mm / 2.0
            half_h = h_mm / 2.0
            rx, ry = x_mm, y_mm
        elif dev == "08":
            w_mm = config.TYPE08_W_MM
            h_mm = config.TYPE08_H_MM
            half_w = w_mm / 2.0
            half_h = h_mm / 2.0
            rx, ry = y_mm, x_mm
        else:  # default to 06 layout
            w_mm = config.TYPE06_W_MM
            h_mm = config.TYPE06_H_MM
            half_w = w_mm / 2.0
            half_h = h_mm / 2.0
            rx, ry = y_mm, x_mm

        if dev in ("07", "11"):
            if abs(rx) > half_w or abs(ry) > half_h:
                return None
            col_f = (rx + half_w) / w_mm * cols
            row_f = ((half_h - ry) / h_mm) * rows
        else:
            if abs(ry) > half_w or abs(rx) > half_h:
                return None
            col_f = (ry + half_w) / w_mm * cols
            row_f = ((half_h - rx) / h_mm) * rows

        row = min(max(int(row_f), 0), rows - 1)
        col = min(max(int(col_f), 0), cols - 1)
        return (row, col)

    def _infer_device_type(self, meta: Dict[str, object]) -> str:
        model = str(meta.get("model_id") or "").strip()
        if model:
            return model[:2]
        device_id = str(meta.get("device_id") or "").strip()
        if device_id:
            prefix = device_id.split(".", 1)[0]
            prefix = prefix.split("-", 1)[0]
            if prefix:
                return prefix[:2]
        return "06"

    def _read_time_ms(self, row: Dict[str, object]) -> Optional[int]:
        for key in ("time", "time_ms", "elapsed_time"):
            raw = row.get(key)
            if raw is None:
                continue
            try:
                return int(float(raw))
            except Exception:
                continue
        return None

    def _read_float(self, row: Dict[str, object], keys: Tuple[str, ...]) -> Optional[float]:
        for key in keys:
            if key in row and row[key] not in (None, ""):
                try:
                    return float(row[key])
                except Exception:
                    continue
        return None
