from __future__ import annotations

import csv
import datetime
import os
from typing import Dict, List, Tuple

from ...project_paths import data_dir

class DiscreteTempRepository:
    def list_discrete_tests(self) -> List[Tuple[str, str, str]]:
        """
        List available discrete temperature tests from the on-disk folder.
        """
        base_dir = data_dir("discrete_temp_testing")
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

    def analyze_discrete_temp_csv(self, csv_path: str) -> tuple[bool, List[float]]:
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
                    clean_row = {(k.strip() if k else k): v for k, v in row.items() if k}
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


