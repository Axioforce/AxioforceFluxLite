from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional, Tuple

from .. import config
from .analysis.temperature_analyzer import TemperatureAnalyzer
from .repositories.test_file_repository import TestFileRepository
from .temperature_processing_service import TemperatureProcessingService


class TemperatureBaselineBiasService:
    """
    Computes per-device, per-cell baseline bias from room-temperature tests.

    Bias is learned from *processed temp-correction OFF* outputs of baseline raw tests
    in a configurable temperature range (default 71–77F). The bias is then used by the
    Temperature Testing UI to score runs against "room-temp behavior" rather than absolute truth.
    """

    def __init__(
        self,
        *,
        repo: TestFileRepository,
        analyzer: TemperatureAnalyzer,
        processing: TemperatureProcessingService,
    ) -> None:
        self._repo = repo
        self._analyzer = analyzer
        self._processing = processing

    def compute_and_store_bias_for_device(
        self,
        *,
        device_id: str,
        min_temp_f: Optional[float] = None,
        max_temp_f: Optional[float] = None,
        status_cb: Callable[[dict], None] | None = None,
    ) -> Dict[str, object]:
        """
        Compute bias and write it to `temp_testing/<device_id>/temp-baseline-bias.json`.

        Returns:
          { ok: bool, message: str, cache_path: str | None, payload: dict | None, errors: list[str] }
        """

        def emit(payload: dict) -> None:
            if status_cb is None:
                return
            try:
                status_cb(dict(payload or {}))
            except Exception:
                pass

        dev = str(device_id or "").strip()
        if not dev:
            return {"ok": False, "message": "Missing device_id", "cache_path": None, "payload": None, "errors": ["Missing device_id"]}

        tmin = float(config.TEMP_BASELINE_ROOM_TEMP_MIN_F if min_temp_f is None else min_temp_f)
        tmax = float(config.TEMP_BASELINE_ROOM_TEMP_MAX_F if max_temp_f is None else max_temp_f)
        if tmin > tmax:
            tmin, tmax = tmax, tmin

        baselines = self._repo.list_temperature_room_baseline_tests(dev, min_temp_f=tmin, max_temp_f=tmax)
        if not baselines:
            msg = f"No room-temp baseline tests found in {tmin:.1f}–{tmax:.1f}°F for device {dev}"
            return {"ok": False, "message": msg, "cache_path": None, "payload": None, "errors": [msg]}

        emit({"status": "running", "message": f"Computing bias from {len(baselines)} room-temp baseline(s)...", "progress": 5})

        # Collect per-baseline per-cell bias matrices.
        # Each is [baseline][row][col] of fractional bias (e.g. 0.10 = +10%).
        per_baseline_bias_all: List[List[List[float]]] = []
        per_baseline_bias_db: List[List[List[float]]] = []
        per_baseline_bias_bw: List[List[List[float]]] = []
        baseline_summaries: List[Dict[str, object]] = []
        errors: List[str] = []

        rows: Optional[int] = None
        cols: Optional[int] = None
        device_type: Optional[str] = None

        db_truth = float(getattr(config, "TEMP_DB_TARGET_N", 45.0 * 4.44822))

        for idx, entry in enumerate(baselines):
            raw_csv = str(entry.get("csv_path") or "")
            meta = dict(entry.get("meta") or {})
            temp_f = entry.get("temp_f")
            temp_label = f"{float(temp_f):.1f}°F" if temp_f is not None else "—°F"

            if not raw_csv or not os.path.isfile(raw_csv):
                errors.append(f"Baseline missing CSV: {raw_csv}")
                continue

            # Process OFF for this baseline (only if missing).
            folder = os.path.dirname(raw_csv)
            room_temp_f = float(temp_f) if temp_f is not None else float(meta.get("room_temperature_f") or 72.0)
            emit(
                {
                    "status": "running",
                    "message": f"Baseline {idx+1}/{len(baselines)}: ensuring temp-off processed ({temp_label})...",
                    "progress": 10 + int(30 * (idx / max(1, len(baselines)))),
                }
            )
            try:
                processed_off = self._processing.ensure_temp_off_processed(
                    folder=folder,
                    device_id=dev,
                    csv_path=raw_csv,
                    room_temp_f=room_temp_f,
                    status_cb=status_cb,
                )
            except Exception as exc:
                errors.append(f"Baseline {temp_label}: failed to process temp-off for {os.path.basename(raw_csv)}: {exc}")
                continue

            # Analyze processed-off.
            try:
                analysis = self._analyzer.analyze_single_processed_csv(processed_off, meta)
            except Exception as exc:
                errors.append(f"Baseline {temp_label}: failed to analyze {os.path.basename(processed_off)}: {exc}")
                continue

            grid = dict(analysis.get("grid") or {})
            data = dict(analysis.get("data") or {})
            stage_map = dict((data.get("stages") or {}))

            r = int(grid.get("rows") or 0)
            c = int(grid.get("cols") or 0)
            dt = str(grid.get("device_type") or "")
            if r <= 0 or c <= 0:
                errors.append(f"Baseline {temp_label}: invalid grid dimensions for {os.path.basename(raw_csv)}")
                continue

            if rows is None:
                rows, cols, device_type = r, c, dt
            else:
                if rows != r or cols != c:
                    errors.append(
                        f"Baseline {temp_label}: grid mismatch (expected {rows}x{cols}, got {r}x{c}) for {os.path.basename(raw_csv)}"
                    )
                    continue

            db_stage = dict(stage_map.get("db") or {})
            bw_stage = dict(stage_map.get("bw") or {})
            db_cells = list(db_stage.get("cells") or [])
            bw_cells = list(bw_stage.get("cells") or [])

            # Rule: if ANY baseline has zero detected windows for a stage, bias mode is invalid.
            if len(db_cells) == 0 or len(bw_cells) == 0:
                errors.append(
                    f"Baseline {temp_label} ({os.path.basename(raw_csv)}): missing stage windows "
                    f"(45lb cells={len(db_cells)}, bodyweight cells={len(bw_cells)})"
                )
                continue

            # Per-stage target truths used to compute baseline-stage pct.
            db_target = float(db_stage.get("target_n") or db_truth)
            bw_target = float(bw_stage.get("target_n") or 0.0)
            if db_target <= 0.0 or bw_target <= 0.0:
                errors.append(
                    f"Baseline {temp_label} ({os.path.basename(raw_csv)}): invalid targets "
                    f"(db_target={db_target:.1f}, bw_target={bw_target:.1f})"
                )
                continue

            def _pct_map(cells: List[dict], target: float) -> Dict[Tuple[int, int], float]:
                out: Dict[Tuple[int, int], float] = {}
                for cell in cells:
                    try:
                        rr = int(cell.get("row", 0))
                        cc = int(cell.get("col", 0))
                        mean_n = float(cell.get("mean_n", 0.0))
                    except Exception:
                        continue
                    if target <= 0:
                        continue
                    out[(rr, cc)] = (mean_n - target) / target
                return out

            db_pcts = _pct_map(db_cells, db_target)
            bw_pcts = _pct_map(bw_cells, bw_target)

            # Stage averages across all cells that do have the stage.
            avg_db = sum(db_pcts.values()) / float(len(db_pcts)) if db_pcts else 0.0
            avg_bw = sum(bw_pcts.values()) / float(len(bw_pcts)) if bw_pcts else 0.0

            # Per-cell pct for this baseline (fill missing stage with stage average).
            baseline_db_bias: List[List[float]] = []
            baseline_bw_bias: List[List[float]] = []
            baseline_all_bias: List[List[float]] = []
            for rr in range(r):
                row_db: List[float] = []
                row_bw: List[float] = []
                row_all: List[float] = []
                for cc in range(c):
                    pct45 = float(db_pcts.get((rr, cc), avg_db))
                    pctbw = float(bw_pcts.get((rr, cc), avg_bw))
                    row_db.append(pct45)
                    row_bw.append(pctbw)
                    row_all.append(0.5 * pct45 + 0.5 * pctbw)
                baseline_db_bias.append(row_db)
                baseline_bw_bias.append(row_bw)
                baseline_all_bias.append(row_all)

            per_baseline_bias_db.append(baseline_db_bias)
            per_baseline_bias_bw.append(baseline_bw_bias)
            per_baseline_bias_all.append(baseline_all_bias)
            baseline_summaries.append(
                {
                    "csv": os.path.basename(raw_csv),
                    "temp_f": float(temp_f) if temp_f is not None else None,
                    "processed_off": os.path.basename(processed_off),
                    "db_target_n": db_target,
                    "bw_target_n": bw_target,
                    "db_cells_measured": len(db_pcts),
                    "bw_cells_measured": len(bw_pcts),
                }
            )

        if errors:
            msg = "Bias-controlled grading disabled: one or more room-temp baseline files are not usable."
            return {"ok": False, "message": msg, "cache_path": None, "payload": None, "errors": errors}

        if rows is None or cols is None or device_type is None or not per_baseline_bias_all:
            msg = "Bias-controlled grading disabled: no usable room-temp baselines were found."
            return {"ok": False, "message": msg, "cache_path": None, "payload": None, "errors": [msg]}

        emit({"status": "running", "message": "Averaging baseline biases per cell...", "progress": 85})

        # Average across baselines per cell.
        bias_all: List[List[float]] = [[0.0 for _ in range(cols)] for _ in range(rows)]
        bias_db: List[List[float]] = [[0.0 for _ in range(cols)] for _ in range(rows)]
        bias_bw: List[List[float]] = [[0.0 for _ in range(cols)] for _ in range(rows)]

        n = float(len(per_baseline_bias_all))
        for rr in range(rows):
            for cc in range(cols):
                bias_all[rr][cc] = sum(b[rr][cc] for b in per_baseline_bias_all) / n
                bias_db[rr][cc] = sum(b[rr][cc] for b in per_baseline_bias_db) / n
                bias_bw[rr][cc] = sum(b[rr][cc] for b in per_baseline_bias_bw) / n

        def _summarize_counts(key: str) -> Dict[str, float]:
            vals: List[float] = []
            for b in baseline_summaries:
                try:
                    vals.append(float(b.get(key) or 0.0))
                except Exception:
                    continue
            if not vals:
                return {"mean": 0.0, "min": 0.0, "max": 0.0}
            return {"mean": sum(vals) / float(len(vals)), "min": float(min(vals)), "max": float(max(vals))}

        payload: Dict[str, object] = {
            "version": 1,
            "device_id": dev,
            "device_type": device_type,
            "rows": rows,
            "cols": cols,
            "room_temp_min_f": tmin,
            "room_temp_max_f": tmax,
            "computed_at_ms": int(time.time() * 1000),
            "baselines": baseline_summaries,
            # Backward compatibility: keep 'bias' as the "all" map used for grading.
            "bias": bias_all,
            # New: stage-resolved bias maps for metrics/diagnostics.
            "bias_all": bias_all,
            "bias_db": bias_db,
            "bias_bw": bias_bw,
            "measured_cells": {
                "db": _summarize_counts("db_cells_measured"),
                "bw": _summarize_counts("bw_cells_measured"),
            },
        }

        cache_path = self._repo.save_temperature_bias_cache(dev, payload)
        emit({"status": "completed", "message": "Bias baseline computed", "progress": 100})

        return {"ok": True, "message": "Bias baseline computed", "cache_path": cache_path, "payload": payload, "errors": []}


