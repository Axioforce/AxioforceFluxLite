from __future__ import annotations

import csv
import math
import os
import statistics
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .io_discrete import DiscreteRow, SENSOR_PREFIXES


def l1_z_raw(row: DiscreteRow) -> float:
    return float(sum(abs(float(row.z_by_sensor.get(sp, 0.0))) for sp in SENSOR_PREFIXES))


def l1_z_scaled(row: DiscreteRow, coef_z: float, room_temp_f: float = 76.0) -> float:
    total = 0.0
    for sp in SENSOR_PREFIXES:
        z = float(row.z_by_sensor.get(sp, 0.0))
        t = float(row.t_by_sensor_f.get(sp, float(row.sum_t_f)))
        dt = float(room_temp_f) - float(t)
        sf = 1.0 - (dt * float(coef_z))
        total += abs(z * sf)
    return float(total)


def pct_change(new: float, old: float) -> Optional[float]:
    if old is None:
        return None
    try:
        old_f = float(old)
        if abs(old_f) < 1e-9:
            return None
        return (float(new) - old_f) / old_f
    except Exception:
        return None


def _norm_phase(v: object) -> str:
    """
    Normalize phase strings so processed CSV rows align with DiscreteRow.phase.
    Expected discrete phases: '45lb' and 'bodyweight'.
    """
    s = str(v or "").strip().lower()
    if "45" in s:
        return "45lb"
    if "body" in s:
        return "bodyweight"
    return s


def parse_processed_sumz(processed_csv_path: str) -> List[Tuple[int, str, float]]:
    """
    Parse processed CSV and return [(time_ms, phase, sum_z)] in file order.
    """
    out: List[Tuple[int, str, float]] = []
    with open(processed_csv_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, skipinitialspace=True)
        if reader.fieldnames:
            reader.fieldnames = [str(h or "").strip() for h in reader.fieldnames]
        for row in reader:
            if not row:
                continue
            r = {str(k or "").strip(): v for k, v in row.items() if k}
            try:
                t_ms = int(float(r.get("time") or r.get("time_ms") or 0))
            except Exception:
                t_ms = 0
            ph = _norm_phase(r.get("phase_name") or r.get("phase") or "")
            try:
                sz = float(r.get("sum-z") or r.get("sum_z") or 0.0)
            except Exception:
                sz = 0.0
            out.append((t_ms, str(ph), float(sz)))
    return out


def align_sumz_by_time(
    raw_rows: List[DiscreteRow],
    processed_pairs: List[Tuple[int, str, float]],
) -> List[Optional[float]]:
    """
    Align processed sum-z values to raw rows by (time_ms, phase) when possible.
    Falls back to (time_ms) queue, then to index order as a last resort.
    """
    # Primary key: (time_ms, phase)
    by_time_phase: Dict[Tuple[int, str], float] = {}
    # Secondary fallback: keep a queue per time for duplicate timestamps
    by_time_q: Dict[int, List[float]] = {}

    for t_ms, ph, sz in processed_pairs:
        key = (int(t_ms), _norm_phase(ph))
        by_time_phase[key] = float(sz)  # last wins for same (time,phase)
        by_time_q.setdefault(int(t_ms), []).append(float(sz))

    out: List[Optional[float]] = []
    miss = 0
    for i, rr in enumerate(raw_rows):
        key = (int(rr.time_ms), _norm_phase(rr.phase))
        if key in by_time_phase:
            out.append(by_time_phase[key])
            continue

        # fallback 1: time-only queue (pop in file order)
        t = int(rr.time_ms)
        q = by_time_q.get(t)
        if q:
            out.append(q.pop(0))
            continue

        # fallback 2: index mapping
        miss += 1
        if i < len(processed_pairs):
            out.append(float(processed_pairs[i][2]))
        else:
            out.append(None)
    return out


@dataclass(frozen=True)
class GainRow:
    source_file: str
    device_id: str
    plate_type: str
    date_str: str
    tester: str
    phase: str
    time_ms: int
    sum_t_f: float
    coef_z: float
    l1z_raw: float
    l1z_scaled: float
    din: Optional[float]
    f0: Optional[float]
    f1: Optional[float]
    dout: Optional[float]
    gain: Optional[float]


def compute_gain_rows(
    raw_rows: List[DiscreteRow],
    f0_list: List[Optional[float]],
    f1_list: List[Optional[float]],
    coef_z: float,
    room_temp_f: float = 76.0,
    min_abs_din: float = 0.002,
) -> List[GainRow]:
    out: List[GainRow] = []
    for rr, f0, f1 in zip(raw_rows, f0_list, f1_list):
        l1_raw = l1_z_raw(rr)
        l1_sc = l1_z_scaled(rr, coef_z=coef_z, room_temp_f=room_temp_f)
        din = pct_change(l1_sc, l1_raw)
        dout = pct_change(f1, f0) if (f0 is not None and f1 is not None) else None
        gain: Optional[float] = None
        if din is not None and dout is not None:
            if abs(din) >= float(min_abs_din):
                try:
                    gain = float(dout) / float(din)
                except Exception:
                    gain = None

        out.append(
            GainRow(
                source_file=rr.source_file,
                device_id=rr.device_id,
                plate_type=rr.plate_type,
                date_str=rr.date_str,
                tester=rr.tester,
                phase=rr.phase,
                time_ms=int(rr.time_ms),
                sum_t_f=float(rr.sum_t_f),
                coef_z=float(coef_z),
                l1z_raw=float(l1_raw),
                l1z_scaled=float(l1_sc),
                din=din,
                f0=f0,
                f1=f1,
                dout=dout,
                gain=gain,
            )
        )
    return out


def _bucket_temp(t_f: float, bucket_f: float = 2.0) -> str:
    try:
        b = float(bucket_f)
        if b <= 0:
            return "na"
        v = float(t_f)
        lo = math.floor(v / b) * b
        hi = lo + b
        return f"{lo:.0f}-{hi:.0f}"
    except Exception:
        return "na"


def summarize_gain(rows: List[GainRow], temp_bucket_f: float = 2.0) -> List[Dict[str, object]]:
    """
    Aggregate gain stats by plate_type/device/phase/coef/temp_bucket.
    Returns list of dict rows for CSV writing.
    """
    groups: Dict[Tuple[str, str, str, float, str], List[float]] = {}
    for r in rows:
        if r.gain is None:
            continue
        key = (
            str(r.plate_type),
            str(r.device_id),
            str(r.phase),
            float(r.coef_z),
            _bucket_temp(r.sum_t_f, bucket_f=temp_bucket_f),
        )
        groups.setdefault(key, []).append(float(r.gain))

    out: List[Dict[str, object]] = []
    for (plate_type, device_id, phase, coef_z, temp_bucket), vals in sorted(groups.items()):
        if not vals:
            continue
        mu = sum(vals) / len(vals)
        sd = statistics.pstdev(vals) if len(vals) >= 2 else 0.0
        med = statistics.median(vals)
        out.append(
            {
                "plate_type": plate_type,
                "device_id": device_id,
                "phase": phase,
                "coef_z": coef_z,
                "temp_bucket_f": temp_bucket,
                "n": len(vals),
                "gain_mean": mu,
                "gain_std": sd,
                "gain_median": med,
                "gain_min": min(vals),
                "gain_max": max(vals),
            }
        )
    return out


def write_gain_rows_csv(path: str, rows: List[GainRow]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cols = [
        "source_file",
        "device_id",
        "plate_type",
        "date_str",
        "tester",
        "phase",
        "time_ms",
        "sum_t_f",
        "coef_z",
        "l1z_raw",
        "l1z_scaled",
        "din",
        "f0",
        "f1",
        "dout",
        "gain",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(cols)
        for r in rows:
            w.writerow([getattr(r, c) for c in cols])


def write_summary_csv(path: str, summary_rows: List[Dict[str, object]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not summary_rows:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            handle.write("plate_type,device_id,phase,coef_z,temp_bucket_f,n,gain_mean,gain_std,gain_median,gain_min,gain_max\n")
        return
    cols = list(summary_rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as handle:
        w = csv.DictWriter(handle, fieldnames=cols)
        w.writeheader()
        for row in summary_rows:
            w.writerow(row)


