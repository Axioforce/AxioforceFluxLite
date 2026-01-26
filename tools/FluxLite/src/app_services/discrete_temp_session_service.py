from __future__ import annotations

import os
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class DiscreteTempSessionService:
    """
    Discrete temperature session buffer/aggregation helpers.

    Operates on the `TestSession` object (duck-typed) so the UI-facing `TestingService`
    can remain a thin coordinator.
    """

    def buffer_live_payload(self, session: Any, payload: dict) -> None:
        """Buffer raw live payloads for discrete temperature analysis."""
        if not session or not getattr(session, "is_discrete_temp", False):
            return

        if not isinstance(payload, dict):
            return

        dev_id = str(payload.get("deviceId") or payload.get("device_id") or "").strip()
        if not dev_id or dev_id != getattr(session, "device_id", ""):
            return

        t_ms = int(payload.get("time") or 0)
        if t_ms <= 0:
            return

        try:
            session.discrete_buffer.append(payload)
        except Exception:
            return

        # Trim buffer to last 10 seconds to keep memory usage low
        cutoff = t_ms - 10_000
        try:
            buf = session.discrete_buffer
            if len(buf) > 100 and int(buf[0].get("time") or 0) < cutoff:
                session.discrete_buffer = [p for p in buf if int(p.get("time") or 0) >= cutoff]
        except Exception:
            pass

    def accumulate_discrete_measurement(self, session: Any, stage_name: str, window_start_ms: int, window_end_ms: int) -> bool:
        """
        Aggregate detailed sensor data over a stability window for discrete temp sessions.
        Returns True if successful.
        """
        if not session or not getattr(session, "is_discrete_temp", False):
            return False

        phase_kind = "45lb" if "db" in str(stage_name or "").lower() else "bodyweight"

        # Filter samples
        samples = []
        try:
            for p in list(getattr(session, "discrete_buffer", []) or []):
                t = int(p.get("time") or 0)
                if window_start_ms <= t <= window_end_ms:
                    samples.append(p)
        except Exception:
            samples = []

        if not samples:
            logger.warning(f"No samples found in window [{window_start_ms}, {window_end_ms}] for {phase_kind}")
            return False

        name_map = {
            "Rear Right Outer": "rear-right-outer",
            "Rear Right Inner": "rear-right-inner",
            "Rear Left Outer": "rear-left-outer",
            "Rear Left Inner": "rear-left-inner",
            "Front Left Outer": "front-left-outer",
            "Front Left Inner": "front-left-inner",
            "Front Right Outer": "front-right-outer",
            "Front Right Inner": "front-right-inner",
            "Sum": "sum",
        }
        cols = [
            "time",
            "phase",
            "device_id",
            "phase_name",
            "phase_id",
            "record_id",
            "rear-right-outer-x",
            "rear-right-outer-y",
            "rear-right-outer-z",
            "rear-right-outer-t",
            "rear-right-inner-x",
            "rear-right-inner-y",
            "rear-right-inner-z",
            "rear-right-inner-t",
            "rear-left-outer-x",
            "rear-left-outer-y",
            "rear-left-outer-z",
            "rear-left-outer-t",
            "rear-left-inner-x",
            "rear-left-inner-y",
            "rear-left-inner-z",
            "rear-left-inner-t",
            "front-left-outer-x",
            "front-left-outer-y",
            "front-left-outer-z",
            "front-left-outer-t",
            "front-left-inner-x",
            "front-left-inner-y",
            "front-left-inner-z",
            "front-left-inner-t",
            "front-right-outer-x",
            "front-right-outer-y",
            "front-right-outer-z",
            "front-right-outer-t",
            "front-right-inner-x",
            "front-right-inner-y",
            "front-right-inner-z",
            "front-right-inner-t",
            "sum-x",
            "sum-y",
            "sum-z",
            "sum-t",
            "moments-x",
            "moments-y",
            "moments-z",
            "COPx",
            "COPy",
            "bx",
            "by",
            "bz",
            "mx",
            "my",
            "mz",
        ]

        sums = {c: 0.0 for c in cols if c not in ("time", "phase", "device_id", "phase_name", "phase_id", "record_id")}
        count = len(samples)
        last_record_id = 0

        for p in samples:
            last_record_id = int(p.get("recordId") or p.get("record_id") or last_record_id)
            avg_temp = float(p.get("avgTemperatureF") or 0.0)

            sensors = p.get("sensors") or []
            by_name = {str((s or {}).get("name") or "").strip(): s for s in sensors}

            for nm, prefix in name_map.items():
                s = by_name.get(nm)
                if not s:
                    continue
                sums[f"{prefix}-x"] += float(s.get("x") or 0.0)
                sums[f"{prefix}-y"] += float(s.get("y") or 0.0)
                sums[f"{prefix}-z"] += float(s.get("z") or 0.0)
                sums[f"{prefix}-t"] += avg_temp

            m = p.get("moments") or {}
            sums["moments-x"] += float(m.get("x") or 0.0)
            sums["moments-y"] += float(m.get("y") or 0.0)
            sums["moments-z"] += float(m.get("z") or 0.0)

            cop = p.get("cop") or {}
            sums["COPx"] += float(cop.get("x") or 0.0)
            sums["COPy"] += float(cop.get("y") or 0.0)

        # Build Row
        row: Dict[str, Any] = {}
        row["time"] = int(getattr(session, "started_at_ms", 0) or 0) or int(window_start_ms)
        row["phase"] = phase_kind
        row["phase_name"] = phase_kind
        row["phase_id"] = phase_kind
        row["device_id"] = getattr(session, "device_id", "")
        row["record_id"] = last_record_id

        for k, v in sums.items():
            row[k] = v / count

        # Running average per phase kind
        try:
            if phase_kind not in session.discrete_stats:
                session.discrete_stats[phase_kind] = {"count": 0, "row": {}}
            bucket = session.discrete_stats[phase_kind]
            prev_cnt = int(bucket.get("count") or 0)
            prev_row = dict(bucket.get("row") or {})
        except Exception:
            return False

        new_row: Dict[str, Any] = {}
        for k in cols:
            if k in ("time", "phase", "phase_name", "phase_id", "device_id"):
                new_row[k] = row.get(k)
                continue
            v_new = float(row.get(k, 0.0))
            if prev_cnt > 0:
                v_prev = float(prev_row.get(k, 0.0))
                new_row[k] = (v_prev * prev_cnt + v_new) / (prev_cnt + 1)
            else:
                new_row[k] = v_new

        bucket["row"] = new_row
        bucket["count"] = prev_cnt + 1
        return True

    def write_discrete_session_csv(self, session: Any) -> int:
        """Write the accumulated stats to the session CSV."""
        if not session or not getattr(session, "is_discrete_temp", False) or not getattr(session, "discrete_test_path", ""):
            return 0

        csv_path = os.path.join(str(session.discrete_test_path), "discrete_temp_session.csv")

        cols = [
            "time",
            "phase",
            "device_id",
            "phase_name",
            "phase_id",
            "record_id",
            "rear-right-outer-x",
            "rear-right-outer-y",
            "rear-right-outer-z",
            "rear-right-outer-t",
            "rear-right-inner-x",
            "rear-right-inner-y",
            "rear-right-inner-z",
            "rear-right-inner-t",
            "rear-left-outer-x",
            "rear-left-outer-y",
            "rear-left-outer-z",
            "rear-left-outer-t",
            "rear-left-inner-x",
            "rear-left-inner-y",
            "rear-left-inner-z",
            "rear-left-inner-t",
            "front-left-outer-x",
            "front-left-outer-y",
            "front-left-outer-z",
            "front-left-outer-t",
            "front-left-inner-x",
            "front-left-inner-y",
            "front-left-inner-z",
            "front-left-inner-t",
            "front-right-outer-x",
            "front-right-outer-y",
            "front-right-outer-z",
            "front-right-outer-t",
            "front-right-inner-x",
            "front-right-inner-y",
            "front-right-inner-z",
            "front-right-inner-t",
            "sum-x",
            "sum-y",
            "sum-z",
            "sum-t",
            "moments-x",
            "moments-y",
            "moments-z",
            "COPx",
            "COPy",
            "bx",
            "by",
            "bz",
            "mx",
            "my",
            "mz",
        ]

        rows_to_write: List[dict] = []
        for kind in ("45lb", "bodyweight"):
            try:
                bucket = session.discrete_stats.get(kind)
            except Exception:
                bucket = None
            if bucket and bucket.get("count", 0) > 0:
                rows_to_write.append(bucket.get("row") or {})

        if not rows_to_write:
            return 0

        file_exists = os.path.isfile(csv_path) and os.path.getsize(csv_path) > 0

        try:
            import csv as _csv

            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = _csv.writer(f)
                if not file_exists:
                    writer.writerow(cols)
                for row_dict in rows_to_write:
                    row_data = [row_dict.get(c, 0.0) for c in cols]
                    writer.writerow(row_data)
            return len(rows_to_write)
        except Exception as e:
            logger.error(f"Failed to write discrete session CSV: {e}")
            return 0


