from __future__ import annotations

import json
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

from .. import config
from ..project_paths import data_dir
from .analysis.temperature_analyzer import TemperatureAnalyzer
from .repositories.test_file_repository import TestFileRepository
from .temperature_baseline_bias_service import TemperatureBaselineBiasService
from .temperature_processing_service import TemperatureProcessingService


def _plate_type_from_device_id(device_id: str) -> str:
    d = str(device_id or "").strip()
    if not d:
        return ""
    # device id format looks like "06.00000025"
    return d.split(".", 1)[0].strip()


def _coef_key(mode: str, coefs: dict) -> str:
    m = str(mode or "legacy").strip().lower()
    x = float((coefs or {}).get("x", 0.0))
    y = float((coefs or {}).get("y", 0.0))
    z = float((coefs or {}).get("z", 0.0))
    return f"{m}:x={x:.6f},y={y:.6f},z={z:.6f}"


class TemperatureCoefRollupService:
    """
    Batch runner + rollup generator for temperature coefficients.

    Goal: find coefficient sets that generalize across devices of the same plate type
    and across temperatures. Uses bias-controlled grading only.
    """

    def __init__(
        self,
        *,
        repo: TestFileRepository,
        analyzer: TemperatureAnalyzer,
        processing: TemperatureProcessingService,
        bias: TemperatureBaselineBiasService,
    ) -> None:
        self._repo = repo
        self._analyzer = analyzer
        self._processing = processing
        self._bias = bias

    def rollup_path(self, plate_type: str) -> str:
        base = os.path.join(data_dir("analysis"), "temp_coef_rollup")
        os.makedirs(base, exist_ok=True)
        pt = str(plate_type or "").strip() or "unknown"
        return os.path.join(base, f"type{pt}.json")

    def load_rollup(self, plate_type: str) -> Dict[str, object]:
        path = self.rollup_path(plate_type)
        if not os.path.isfile(path):
            return {"version": 1, "plate_type": plate_type, "updated_at_ms": 0, "runs": []}
        try:
            with open(path, "r", encoding="utf-8") as h:
                data = json.load(h)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"version": 1, "plate_type": plate_type, "updated_at_ms": 0, "runs": []}

    def save_rollup(self, plate_type: str, payload: Dict[str, object]) -> str:
        path = self.rollup_path(plate_type)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as h:
            json.dump(payload, h, indent=2, sort_keys=True)
        return path

    def run_coefs_across_plate_type(
        self,
        *,
        plate_type: str,
        coefs: dict,
        mode: str,
        status_cb: Callable[[dict], None] | None = None,
    ) -> Dict[str, object]:
        """
        For each device of plate_type, for each test CSV that has meta, ensure processing exists
        for the given coef set, then analyze and append to rollup.

        Returns { ok, message, rollup_path, errors }
        """

        def emit(p: dict) -> None:
            if status_cb is None:
                return
            try:
                status_cb(dict(p or {}))
            except Exception:
                pass

        pt = str(plate_type or "").strip()
        if not pt:
            return {"ok": False, "message": "Missing plate type", "rollup_path": None, "errors": ["Missing plate type"]}

        # Find devices with this prefix
        devices = [d for d in (self._repo.list_temperature_devices() or []) if _plate_type_from_device_id(d) == pt]
        if not devices:
            return {"ok": False, "message": f"No devices found for plate type {pt}", "rollup_path": None, "errors": []}

        coef_key = _coef_key(mode, coefs)
        rollup = self.load_rollup(pt)
        runs: List[Dict[str, object]] = list(rollup.get("runs") or [])
        errors: List[str] = []

        emit({"status": "running", "message": f"Batch run {coef_key} across type {pt} ({len(devices)} devices)...", "progress": 1})

        # For each device, compute bias cache if missing/invalid (required for bias-controlled scoring).
        for di, device_id in enumerate(devices):
            emit({"status": "running", "message": f"Device {di+1}/{len(devices)}: {device_id}", "progress": 5})

            bias_res = self._bias.compute_and_store_bias_for_device(device_id=device_id, status_cb=status_cb)
            if not bool((bias_res or {}).get("ok")):
                errs = list((bias_res or {}).get("errors") or [])
                msg = str((bias_res or {}).get("message") or "bias failed")
                errors.append(f"{device_id}: bias baseline invalid: {msg}")
                for e in errs:
                    errors.append(f"{device_id}: {e}")
                continue

            bias_cache = self._repo.load_temperature_bias_cache(device_id) or {}
            bias_map = (bias_cache.get("bias_all") or bias_cache.get("bias")) if isinstance(bias_cache, dict) else None
            if not isinstance(bias_map, list):
                errors.append(f"{device_id}: bias cache missing bias map")
                continue

            tests = self._repo.list_temperature_tests(device_id)
            for ti, raw_csv in enumerate(tests):
                meta = self._repo.load_temperature_meta_for_csv(raw_csv)
                if not meta:
                    continue  # only tests with meta
                # Need a temperature for "2 temps per plate" eligibility later; use meta's temp if present.
                temp_f = None
                try:
                    temp_f = self._repo.extract_temperature_f(meta)
                except Exception:
                    temp_f = None

                folder = os.path.dirname(raw_csv)
                room_temp_f = float(temp_f) if temp_f is not None else float(meta.get("room_temperature_f") or 72.0)

                emit(
                    {
                        "status": "running",
                        "message": f"{device_id}: processing {ti+1}/{len(tests)}",
                        "progress": 5,
                    }
                )

                try:
                    # Ensure baseline off exists; run full processing to create the on-variant for this coef set.
                    self._processing.run_temperature_processing(
                        folder=folder,
                        device_id=device_id,
                        csv_path=raw_csv,
                        slopes=coefs,
                        room_temp_f=room_temp_f,
                        mode=str(mode or "legacy"),
                        status_cb=status_cb,
                    )
                except Exception as exc:
                    errors.append(f"{device_id}: failed processing {os.path.basename(raw_csv)}: {exc}")
                    continue

                # Resolve processed paths from meta (authoritative).
                details = self._repo.get_temperature_test_details(raw_csv)
                proc_runs = list(details.get("processed_runs") or [])
                baseline_path = ""
                for r in proc_runs:
                    if r.get("is_baseline"):
                        baseline_path = str(r.get("path") or "")
                        break
                selected_path = ""
                for r in proc_runs:
                    if r.get("is_baseline"):
                        continue
                    if _coef_key(str(r.get("mode") or "legacy"), dict(r.get("slopes") or {})) == coef_key:
                        selected_path = str(r.get("path") or "")
                        break
                if not baseline_path or not selected_path:
                    errors.append(f"{device_id}: missing processed paths after processing: {os.path.basename(raw_csv)}")
                    continue

                # Analyze baseline(off) vs selected(on).
                try:
                    payload = self._analyzer.analyze_temperature_processed_runs(baseline_path, selected_path, meta)
                except Exception as exc:
                    errors.append(f"{device_id}: analyze failed {os.path.basename(raw_csv)}: {exc}")
                    continue

                grid = dict(payload.get("grid") or {})
                device_type = str(grid.get("device_type") or pt)
                body_weight_n = float((payload.get("meta") or {}).get("body_weight_n") or 0.0)

                def _score(run_data: dict, stage_key: str) -> dict:
                    stages = (run_data or {}).get("stages") or {}
                    keys = list(stages.keys()) if stage_key == "all" else [stage_key]
                    abs_pcts: List[float] = []
                    signed_pcts: List[float] = []
                    pass_count = 0
                    total = 0
                    for sk in keys:
                        stage = stages.get(sk) or {}
                        base_target = float(stage.get("target_n") or 0.0)
                        threshold = float(config.get_passing_threshold(sk, device_type, body_weight_n))
                        for cell in stage.get("cells", []) or []:
                            try:
                                rr = int(cell.get("row", 0))
                                cc = int(cell.get("col", 0))
                                mean_n = float(cell.get("mean_n", 0.0))
                            except Exception:
                                continue
                            target = base_target
                            try:
                                target = base_target * (1.0 + float(bias_map[rr][cc]))
                            except Exception:
                                target = base_target
                            if not target:
                                continue
                            signed = (mean_n - target) / target * 100.0
                            abs_pcts.append(abs(signed))
                            signed_pcts.append(signed)
                            total += 1
                            err_ratio = abs(mean_n - target) / threshold if threshold > 0 else 999.0
                            if err_ratio <= float(config.COLOR_BIN_MULTIPLIERS.get("light_green", 1.0)):
                                pass_count += 1
                    if not abs_pcts:
                        return {"n": 0}
                    mean_abs = sum(abs_pcts) / float(len(abs_pcts))
                    mean_signed = sum(signed_pcts) / float(len(signed_pcts))
                    var = sum((x - mean_signed) ** 2 for x in signed_pcts) / float(max(1, len(signed_pcts) - 1))
                    std_signed = float(var) ** 0.5
                    return {
                        "n": len(abs_pcts),
                        "mean_abs": mean_abs,
                        "mean_signed": mean_signed,
                        "std_signed": std_signed,
                        "pass_rate": (100.0 * pass_count / total) if total else None,
                    }

                baseline_scores = {k: _score(payload.get("baseline") or {}, k) for k in ("all", "db", "bw")}
                selected_scores = {k: _score(payload.get("selected") or {}, k) for k in ("all", "db", "bw")}

                runs.append(
                    {
                        "plate_type": pt,
                        "device_id": device_id,
                        "device_type": device_type,
                        "coef_key": coef_key,
                        "mode": str(mode or "legacy"),
                        "coefs": {"x": float(coefs.get("x", 0.0)), "y": float(coefs.get("y", 0.0)), "z": float(coefs.get("z", 0.0))},
                        "raw_csv": raw_csv,
                        "temp_f": temp_f,
                        "baseline_csv": baseline_path,
                        "selected_csv": selected_path,
                        "baseline": baseline_scores,
                        "selected": selected_scores,
                        "recorded_at_ms": int(time.time() * 1000),
                    }
                )

        rollup["version"] = 1
        rollup["plate_type"] = pt
        rollup["updated_at_ms"] = int(time.time() * 1000)
        rollup["runs"] = runs
        path = self.save_rollup(pt, rollup)

        msg = f"Batch run complete for type {pt} ({coef_key})"
        if errors:
            msg = f"{msg} (with errors)"
        return {"ok": True, "message": msg, "rollup_path": path, "errors": errors}

    def top3_for_plate_type(self, plate_type: str) -> List[Dict[str, object]]:
        """
        Compute top-3 coefficient combos for a plate type using bias-controlled scoring only.

        Eligibility:
          - at least 2 distinct temps per device (temp_f) for each included device
          - at least 2 devices contributing for the coef combo
        Score:
          - mean of selected/all mean_abs across included runs
        """
        pt = str(plate_type or "").strip()
        rollup = self.load_rollup(pt)
        runs = list(rollup.get("runs") or [])

        # Group by coef_key -> device -> list of runs
        by_coef: Dict[str, Dict[str, List[dict]]] = {}
        for r in runs:
            try:
                ck = str(r.get("coef_key") or "")
                dev = str(r.get("device_id") or "")
            except Exception:
                continue
            if not ck or not dev:
                continue
            by_coef.setdefault(ck, {}).setdefault(dev, []).append(r)

        rows: List[Dict[str, object]] = []
        for ck, by_dev in by_coef.items():
            eligible_runs: List[dict] = []
            eligible_devices = 0
            all_temps: List[float] = []
            for dev, dev_runs in by_dev.items():
                temps = set()
                for rr in dev_runs:
                    tf = rr.get("temp_f")
                    if tf is None:
                        continue
                    try:
                        temps.add(float(tf))
                    except Exception:
                        continue
                if len(temps) < 2:
                    continue
                eligible_devices += 1
                all_temps.extend(list(temps))
                eligible_runs.extend(dev_runs)

            if eligible_devices < 2:
                continue

            mean_abs_vals: List[float] = []
            mean_signed_vals: List[float] = []
            std_signed_vals: List[float] = []
            for rr in eligible_runs:
                sel = (rr.get("selected") or {}).get("all") or {}
                try:
                    mean_abs_vals.append(float(sel.get("mean_abs")))
                except Exception:
                    pass
                try:
                    mean_signed_vals.append(float(sel.get("mean_signed")))
                except Exception:
                    pass
                try:
                    std_signed_vals.append(float(sel.get("std_signed")))
                except Exception:
                    pass

            if not mean_abs_vals:
                continue

            score_mean_abs = sum(mean_abs_vals) / float(len(mean_abs_vals))
            mean_signed = sum(mean_signed_vals) / float(len(mean_signed_vals)) if mean_signed_vals else 0.0
            std_signed = sum(std_signed_vals) / float(len(std_signed_vals)) if std_signed_vals else 0.0
            coverage = f"{eligible_devices} devices, {len(eligible_runs)} tests"
            if all_temps:
                try:
                    coverage = f"{coverage}, temps {min(all_temps):.1f}–{max(all_temps):.1f}°F"
                except Exception:
                    pass

            rows.append(
                {
                    "coef_key": ck,
                    "coef_label": ck,
                    "score_mean_abs": score_mean_abs,
                    "mean_signed": mean_signed,
                    "std_signed": std_signed,
                    "coverage": coverage,
                }
            )

        rows.sort(key=lambda r: float(r.get("score_mean_abs") or 1e9))
        return rows[:3]


