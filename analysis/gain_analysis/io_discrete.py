from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple


SENSOR_PREFIXES: List[str] = [
    "rear-right-outer",
    "rear-right-inner",
    "rear-left-outer",
    "rear-left-inner",
    "front-left-outer",
    "front-left-inner",
    "front-right-outer",
    "front-right-inner",
]


@dataclass(frozen=True)
class DiscreteRow:
    """A normalized representation of one raw row from a discrete temp CSV."""

    source_file: str
    device_id: str
    plate_type: str  # e.g. "06","07","08","11"
    date_str: str  # folder-derived, best-effort
    tester: str  # folder-derived, best-effort
    phase: str  # "45lb" or "bodyweight" (best-effort normalized)
    time_ms: int
    sum_t_f: float
    sum_z: float
    # Per-sensor z and temp (F)
    z_by_sensor: Dict[str, float]
    t_by_sensor_f: Dict[str, float]


def _norm_phase(v: str) -> str:
    s = str(v or "").strip().lower()
    if s.startswith("45"):
        return "45lb"
    if "body" in s:
        return "bodyweight"
    # fallback: if stage uses "db" etc.
    if "db" in s:
        return "45lb"
    return s or "unknown"


def _safe_float(v: object, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_int(v: object, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return int(default)


def _infer_meta_from_path(path: str) -> Tuple[str, str, str]:
    """
    Infer (plate_type, date_str, tester) from a path like:
      discrete_temp_testing/<device_id>/<date>/<tester>/<file>
    Best-effort; returns empty strings when unknown.
    """
    p = os.path.normpath(path)
    parts = p.split(os.sep)
    plate_type = ""
    date_str = ""
    tester = ""
    try:
        # Find the index of discrete_temp_testing, then pull subsequent components
        idx = next(i for i, part in enumerate(parts) if part.lower() == "discrete_temp_testing")
        # idx+1 is typically device_id folder (e.g. 07.00000051)
        dev_folder = parts[idx + 1] if idx + 1 < len(parts) else ""
        plate_type = str(dev_folder).split(".", 1)[0].strip()
        date_str = parts[idx + 2] if idx + 2 < len(parts) else ""
        tester = parts[idx + 3] if idx + 3 < len(parts) else ""
    except Exception:
        pass
    return plate_type, date_str, tester


def iter_discrete_csv_paths(root_dir: str) -> Iterator[str]:
    """
    Yield discrete CSV file paths under `root_dir`:
      - discrete_temp_session.csv
      - discrete_temp_measurements.csv
    """
    target_names = {"discrete_temp_session.csv", "discrete_temp_measurements.csv"}
    for base, _dirs, files in os.walk(root_dir):
        for fn in files:
            if fn.lower() in target_names:
                yield os.path.join(base, fn)


def load_discrete_rows(csv_path: str) -> List[DiscreteRow]:
    """
    Load raw discrete rows from a discrete temp CSV.
    Returns a list of normalized DiscreteRow objects.
    """
    plate_type, date_str, tester = _infer_meta_from_path(csv_path)
    out: List[DiscreteRow] = []
    if not csv_path or not os.path.isfile(csv_path):
        return out

    with open(csv_path, "r", encoding="utf-8", newline="") as handle:
        # Some files contain padded headers; strip whitespace aggressively.
        reader = csv.DictReader(handle, skipinitialspace=True)
        if reader.fieldnames:
            reader.fieldnames = [str(h or "").strip() for h in reader.fieldnames]

        for row in reader:
            if not row:
                continue
            r = {str(k or "").strip(): v for k, v in row.items() if k}

            device_id = str(r.get("device_id") or r.get("deviceId") or "").strip()
            if not device_id:
                # Fallback to folder-inferred plate_type only
                device_id = plate_type or ""

            if not plate_type:
                plate_type = str(device_id).split(".", 1)[0].strip()

            phase = _norm_phase(r.get("phase_name") or r.get("phase") or "")
            time_ms = _safe_int(r.get("time") or r.get("time_ms") or 0)

            sum_t_f = _safe_float(r.get("sum-t") or r.get("sum_t") or r.get("avgTemperatureF") or 0.0)
            sum_z = _safe_float(r.get("sum-z") or r.get("sum_z") or 0.0)

            z_by_sensor: Dict[str, float] = {}
            t_by_sensor: Dict[str, float] = {}

            for sp in SENSOR_PREFIXES:
                z_by_sensor[sp] = _safe_float(r.get(f"{sp}-z") or 0.0)
                # Some discrete CSVs store per-sensor temps, but the current discrete temp pipeline
                # uses avgTemperatureF for all sensors as a proxy.
                t_val = r.get(f"{sp}-t")
                if t_val is None or str(t_val).strip() == "":
                    t_by_sensor[sp] = float(sum_t_f)
                else:
                    t_by_sensor[sp] = _safe_float(t_val, default=float(sum_t_f))

            out.append(
                DiscreteRow(
                    source_file=os.path.abspath(csv_path),
                    device_id=device_id,
                    plate_type=plate_type,
                    date_str=str(date_str or ""),
                    tester=str(tester or ""),
                    phase=phase,
                    time_ms=time_ms,
                    sum_t_f=float(sum_t_f),
                    sum_z=float(sum_z),
                    z_by_sensor=z_by_sensor,
                    t_by_sensor_f=t_by_sensor,
                )
            )

    return out


def load_all_discrete_rows(root_dir: str) -> List[DiscreteRow]:
    """Load all discrete rows under a root directory."""
    all_rows: List[DiscreteRow] = []
    for p in iter_discrete_csv_paths(root_dir):
        all_rows.extend(load_discrete_rows(p))
    return all_rows


