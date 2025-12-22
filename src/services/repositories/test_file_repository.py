from __future__ import annotations
import os
import csv
import json
import random
import datetime
import time
from typing import List, Dict, Optional, Tuple, Any

from ... import config

class TestFileRepository:
    """
    Handles file system operations for test data (listing, reading, metadata).
    """

    def list_temperature_tests(self, device_id: str) -> List[str]:
        """List available temperature test CSV files for a device."""
        if not device_id:
            return []
            
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
            # Treat each test folder as a single picker entry.
            # The canonical file for a test is discrete_temp_session.csv; other CSVs in the folder
            # (e.g. discrete_temp_measurements.csv) are considered plot-only overlays and must not
            # create additional picker entries.
            for root, _dirs, files in os.walk(base_dir):
                files_lc = {str(f or "").lower(): f for f in (files or [])}

                # Only list tests that have a session file (canonical).
                if "discrete_temp_session.csv" not in files_lc:
                    continue

                try:
                    rel = os.path.relpath(root, base_dir)
                except Exception:
                    rel = root
                parts = str(rel).split(os.sep)
                device_id = parts[0] if len(parts) > 0 else ""
                date_part = parts[1] if len(parts) > 1 else ""
                tester = parts[2] if len(parts) > 2 else ""

                # Build a concise label like "caleb • 06.0000000c"
                label_bits = [p for p in (tester, device_id) if p]
                label = " • ".join(label_bits) if label_bits else (device_id or tester or os.path.basename(root))

                # Prefer the folder date (e.g. 11-20-2025), fallback to mtime.
                date_str = ""
                if date_part:
                    date_str = date_part.replace("-", ".")

                mtimes: List[float] = []
                for fn in ("discrete_temp_session.csv", "discrete_temp_measurements.csv", "test_meta.json"):
                    try:
                        p = os.path.join(root, fn)
                        if os.path.isfile(p):
                            mtimes.append(float(os.path.getmtime(p)))
                    except Exception:
                        pass
                mtime = max(mtimes) if mtimes else 0.0

                if not date_str:
                    try:
                        dt = datetime.datetime.fromtimestamp(mtime)
                        date_str = dt.strftime("%m.%d.%Y")
                    except Exception:
                        date_str = ""

                # Key for selection should be the test folder, not an individual CSV file.
                tests.append((label, date_str, root, float(mtime)))
        except Exception:
            pass

        # Sort newest-first by modification time
        tests.sort(key=lambda x: x[3], reverse=True)
        return [(label, date_str, path) for (label, date_str, path, _mtime) in tests]

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
            mode = variant.get("mode", "legacy")
            processed_runs.append({
                "label": self.format_slopes_label(slopes, mode=mode),
                "path": path,
                "is_baseline": False,
                "slopes": slopes,
                "mode": mode,
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

    def analyze_discrete_temp_csv(self, csv_path: str) -> Tuple[bool, List[float]]:
        """
        Analyze a discrete_temp_session.csv-style file and return:
          - includes_baseline: whether any session temp is within the 74–78°F window
          - temps_f: list of non-baseline session temps (°F), sorted high → low
        """
        includes_baseline = False
        temps_f: List[float] = []

        if not csv_path:
            return includes_baseline, temps_f

        # Accept either a folder path (preferred for the picker) or a direct CSV path.
        p = str(csv_path).strip()
        if os.path.isdir(p):
            p = os.path.join(p, "discrete_temp_session.csv")
        else:
            # If a measurements CSV is ever passed here, redirect to the canonical session CSV.
            try:
                if os.path.basename(p).lower() == "discrete_temp_measurements.csv":
                    p = os.path.join(os.path.dirname(p), "discrete_temp_session.csv")
            except Exception:
                pass

        if not os.path.isfile(p):
            return includes_baseline, temps_f

        try:
            with open(p, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, skipinitialspace=True)
                sessions: Dict[str, List[float]] = {}
                for row in reader:
                    if not row:
                        continue
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

    def downsample_csv_to_50hz(self, source_csv: str, dest_csv: str) -> str:
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

    def derive_temperature_paths(self, raw_csv: str, device_id: str, mode: str = "legacy") -> Dict[str, str]:
        filename = os.path.basename(raw_csv)
        folder = os.path.dirname(raw_csv)
        if not filename.startswith("temp-raw-"):
            raise ValueError("Unexpected filename format for temperature test")
        base_without_prefix = filename[len("temp-raw-") :]
        stem, ext = os.path.splitext(base_without_prefix)

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
        return {
            axis: float(slopes.get(axis, 0.0))
            for axis in ("x", "y", "z")
        }

    # Internal helpers

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
                runs.append({
                    "label": "Temp Off (Baseline)",
                    "path": full_path,
                    "is_baseline": True,
                })
                baseline_present = True
                known_paths.add(full_path)
                continue
            slopes, mode = self._slopes_from_filename(fname, base_without_prefix)
            if not slopes:
                continue
            runs.append({
                "label": self.format_slopes_label(slopes, mode=mode),
                "path": full_path,
                "is_baseline": False,
                "slopes": slopes,
                "mode": mode,
            })
            known_paths.add(full_path)
        return runs

    def _slopes_from_filename(self, filename: str, base_without_prefix: str) -> Tuple[Dict[str, float], str]:
        suffix = f"-{base_without_prefix}"
        if not filename.endswith(suffix):
            return {}, "legacy"
        body = filename[:-len(suffix)]
        if not body.startswith("temp-"):
            return {}, "legacy"
        core = body[len("temp-") :]
        if core.startswith("processed-"):
            return {}, "legacy"
            
        mode = "legacy"
        if core.startswith("scalar-"):
            mode = "scalar"
            core = core[len("scalar-"):]
            
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

