from __future__ import annotations

import csv
import json
import os
import random
import time
from typing import Dict, List, Optional, Tuple, Any

from ...project_paths import data_dir


class TemperatureTestRepository:
    def list_temperature_tests(self, device_id: str) -> List[str]:
        """List available temperature test CSV files for a device."""
        if not device_id:
            return []

        base_dir = data_dir("temp_testing")
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

    def list_temperature_room_baseline_tests(
        self,
        device_id: str,
        *,
        min_temp_f: float,
        max_temp_f: float,
    ) -> List[Dict[str, object]]:
        """
        List "room temp" baseline raw tests for a device.

        Returns entries:
          { "csv_path": str, "meta_path": str, "temp_f": float | None, "meta": dict }
        """
        out: List[Dict[str, object]] = []
        for csv_path in self.list_temperature_tests(device_id):
            meta = self.load_meta_for_csv(csv_path) or {}
            temp_f = self.extract_temperature_f(meta)
            if temp_f is None:
                continue
            if float(min_temp_f) <= float(temp_f) <= float(max_temp_f):
                out.append(
                    {
                        "csv_path": csv_path,
                        "meta_path": self._meta_path_for_csv(csv_path),
                        "temp_f": float(temp_f),
                        "meta": dict(meta),
                    }
                )
        return out

    def load_meta_for_csv(self, csv_path: str) -> Optional[Dict[str, object]]:
        meta_path = self._meta_path_for_csv(csv_path)
        if not os.path.isfile(meta_path):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                data = json.load(mf)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def extract_temperature_f(self, meta: Dict[str, object]) -> Optional[float]:
        """
        Best-effort temperature extraction from a temp test meta dict.
        Matches UI conventions: room_temperature_f / room_temp_f / ambient_temp_f / avg_temp.
        """
        for key in ("room_temperature_f", "room_temp_f", "ambient_temp_f", "avg_temp"):
            value = (meta or {}).get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def bias_cache_path(self, device_id: str) -> str:
        base_dir = data_dir("temp_testing")
        device_dir = os.path.join(base_dir, str(device_id))
        return os.path.join(device_dir, "temp-baseline-bias.json")

    def load_bias_cache(self, device_id: str) -> Optional[Dict[str, object]]:
        path = self.bias_cache_path(device_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def save_bias_cache(self, device_id: str, payload: Dict[str, object]) -> str:
        path = self.bias_cache_path(device_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        return path

    def list_temperature_devices(self) -> List[str]:
        """List available devices (subdirectories) in temp_testing folder."""
        base_dir = data_dir("temp_testing")
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
                    processed_runs.append(
                        {
                            "label": "Temp Off (Baseline)",
                            "path": path,
                            "is_baseline": True,
                        }
                    )
                    baseline_added = True
                    known_paths.add(path)

        legacy_processed = meta.get("processed") if isinstance(meta, dict) else None
        if not baseline_added and isinstance(legacy_processed, dict):
            processed_off = legacy_processed.get("processed_off")
            if processed_off:
                path = os.path.join(folder, processed_off)
                if os.path.isfile(path):
                    processed_runs.append(
                        {
                            "label": "Temp Off (Baseline)",
                            "path": path,
                            "is_baseline": True,
                        }
                    )
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
            mode = variant.get("mode", "legacy")
            processed_runs.append(
                {
                    "label": self.format_slopes_label(slopes, mode=mode),
                    "path": path,
                    "is_baseline": False,
                    "slopes": slopes,
                    "mode": mode,
                }
            )
            known_paths.add(path)

        final_runs = self._append_processed_from_disk(processed_runs, folder, base_without_prefix)

        # Sort runs: Baseline first, then others sorted by Z slope (high to low)
        baseline_runs = [r for r in final_runs if r.get("is_baseline")]
        other_runs = [r for r in final_runs if not r.get("is_baseline")]

        def _get_sort_key(r):
            slopes = r.get("slopes", {})
            return (
                float(slopes.get("z", 0.0)),
                float(slopes.get("y", 0.0)),
                float(slopes.get("x", 0.0)),
            )

        other_runs.sort(key=_get_sort_key, reverse=True)

        return {
            "meta": meta,
            "stage_names": stage_names,
            "processed_runs": baseline_runs + other_runs,
        }

    def derive_temperature_paths(self, raw_csv: str, device_id: str, mode: str = "legacy") -> Dict[str, str]:
        filename = os.path.basename(raw_csv)
        folder = os.path.dirname(raw_csv)
        if not filename.startswith("temp-raw-"):
            raise ValueError("Unexpected filename format for temperature test")
        base_without_prefix = filename[len("temp-raw-") :]
        stem, _ext = os.path.splitext(base_without_prefix)

        trimmed = os.path.join(folder, f"temp-trimmed-{base_without_prefix}")
        processed_off = f"temp-processed-{base_without_prefix}"

        if mode == "scalar":
            processed_on_template = f"temp-scalar-{{slopes}}-{base_without_prefix}"
        else:
            processed_on_template = f"temp-{{slopes}}-{base_without_prefix}"

        return {
            "trimmed": trimmed,
            "processed_off_name": processed_off,
            "processed_on_template": processed_on_template,
            "meta": os.path.join(folder, f"temp-raw-{stem}.meta.json"),
        }

    def update_meta_with_processed(
        self,
        meta_path: str,
        trimmed_csv: str,
        processed_off: str,
        processed_on: str,
        slopes: dict,
        mode: str = "legacy",
    ) -> None:
        meta: Dict[str, object] = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf) or {}
            except Exception:
                meta = {}

        now_ms = int(time.time() * 1000)
        slopes_clean = self.normalize_slopes(slopes)

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
            "mode": mode,
        }
        variants = meta.get("processed_variants")
        if not isinstance(variants, list):
            variants = []

        key = (self._slopes_key(slopes_clean), mode)
        replaced = False
        for entry in variants:
            entry_mode = entry.get("mode", "legacy")
            entry_key = (self._slopes_key(entry.get("slopes") or {}), entry_mode)
            if entry_key == key:
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

    def update_meta_with_baseline_only(
        self,
        meta_path: str,
        *,
        trimmed_csv: str,
        processed_off: str,
    ) -> None:
        """
        Persist only the 'temp correction off' baseline processing result in the meta file.
        This is used by bias-controlled grading baseline generation, where we don't want to
        create a 'temp correction on' variant.
        """
        meta: Dict[str, object] = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf) or {}
            except Exception:
                meta = {}

        now_ms = int(time.time() * 1000)
        meta["processed_baseline"] = {
            "trimmed_csv": os.path.basename(str(trimmed_csv)),
            "processed_off": os.path.basename(str(processed_off)),
            "updated_at_ms": now_ms,
        }

        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, indent=2, sort_keys=True)

    def format_slopes_label(self, slopes: dict, mode: str = "legacy") -> str:
        """
        Human-friendly label for a processed run.

        Notes:
        - Scalar mode coefficients are typically small (e.g. 0.004), so we show
          3 decimals to avoid the UI appearing like "X=0.00".
        - Legacy mode slopes are usually larger; we default to 2 decimals unless
          values are small.
        """
        mode_lc = str(mode or "legacy").strip().lower()

        def _fmt(val: float) -> str:
            # Keep scalar mode readable; also preserve precision for small legacy values.
            decimals = 3 if (mode_lc == "scalar" or abs(val) < 0.1) else 2
            return f"{val:.{decimals}f}"

        x = float((slopes or {}).get("x", 0.0))
        y = float((slopes or {}).get("y", 0.0))
        z = float((slopes or {}).get("z", 0.0))

        if abs(x - y) < 1e-9 and abs(y - z) < 1e-9:
            return f"All: {_fmt(x)}"

        return f"X={_fmt(x)}, Y={_fmt(y)}, Z={_fmt(z)}"

    def formatted_slope_name(self, slopes: dict) -> str:
        def _fmt(val: object) -> str:
            try:
                # Use 4 decimals so scalar coefficients like 0.0042 are preserved in filenames.
                as_str = f"{float(val):.4f}".rstrip("0").rstrip(".")
                if not as_str:
                    as_str = "0"
                if "." not in as_str:
                    as_str = f"{as_str}.0"
                return as_str
            except Exception:
                return "0.0"

        return "_".join([_fmt(slopes.get(axis, 0.0)) for axis in ("x", "y", "z")])

    def normalize_slopes(self, slopes: dict) -> Dict[str, float]:
        return {axis: float(slopes.get(axis, 0.0)) for axis in ("x", "y", "z")}

    # --- Internal helpers -------------------------------------------------

    def _meta_path_for_csv(self, csv_path: str) -> str:
        folder = os.path.dirname(csv_path)
        name, _ext = os.path.splitext(os.path.basename(csv_path))
        prefix_mappings = {
            "temp-trimmed-": "temp-raw-",
            "temp-processed-": "temp-raw-",
        }
        for prefix, replacement in prefix_mappings.items():
            if name.startswith(prefix):
                name = replacement + name[len(prefix) :]
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

    def _slopes_key(self, slopes: dict) -> tuple:
        normalized = self.normalize_slopes(slopes)
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
                runs.append(
                    {
                        "label": "Temp Off (Baseline)",
                        "path": full_path,
                        "is_baseline": True,
                    }
                )
                baseline_present = True
                known_paths.add(full_path)
                continue
            slopes, mode = self._slopes_from_filename(fname, base_without_prefix)
            if not slopes:
                continue
            runs.append(
                {
                    "label": self.format_slopes_label(slopes, mode=mode),
                    "path": full_path,
                    "is_baseline": False,
                    "slopes": slopes,
                    "mode": mode,
                }
            )
            known_paths.add(full_path)
        return runs

    def _slopes_from_filename(self, filename: str, base_without_prefix: str) -> Tuple[Dict[str, float], str]:
        suffix = f"-{base_without_prefix}"
        if not filename.endswith(suffix):
            return {}, "legacy"
        body = filename[: -len(suffix)]
        if not body.startswith("temp-"):
            return {}, "legacy"
        core = body[len("temp-") :]
        if core.startswith("processed-"):
            return {}, "legacy"

        mode = "legacy"
        if core.startswith("scalar-"):
            mode = "scalar"
            core = core[len("scalar-") :]

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
        return slopes, mode


