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
from .temperature_coef_rollup.aggregation import aggregate_mean_signed_for_coef_key, top3_rows_for_plate_type
from .temperature_coef_rollup.coef_key import parse_coef_key
from .temperature_coef_rollup.distinct_experiment import export_distinct_experiment_report
from .temperature_coef_rollup.scoring import score_run_against_bias
from .temperature_coef_rollup.eligibility import baseline_csvs_for_devices


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

    def coef_key(self, mode: str, coefs: dict) -> str:
        return _coef_key(mode, coefs)

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

    def reset_rollup(self, plate_type: str, *, backup: bool = True) -> Dict[str, object]:
        """
        Clear the stored rollup for a plate type (this indirectly resets the UI "Top 3").

        By default we create a timestamped backup next to the original file to avoid
        accidental loss of compute.
        """
        pt = str(plate_type or "").strip()
        if not pt:
            return {"ok": False, "message": "Missing plate type", "rollup_path": None, "backup_path": None}

        path = self.rollup_path(pt)
        if not os.path.isfile(path):
            return {"ok": True, "message": f"No rollup found for type {pt} (already reset)", "rollup_path": path, "backup_path": None}

        backup_path = None
        if backup:
            backup_path = f"{path}.bak.{int(time.time() * 1000)}"
            try:
                os.replace(path, backup_path)
            except Exception:
                # Fallback: best-effort copy + delete
                try:
                    with open(path, "rb") as src, open(backup_path, "wb") as dst:
                        dst.write(src.read())
                    os.remove(path)
                except Exception as exc:
                    return {"ok": False, "message": f"Failed to backup/remove rollup: {exc}", "rollup_path": path, "backup_path": backup_path}
        else:
            try:
                os.remove(path)
            except Exception as exc:
                return {"ok": False, "message": f"Failed to delete rollup: {exc}", "rollup_path": path, "backup_path": None}

        return {"ok": True, "message": f"Cleared rollup cache for type {pt}", "rollup_path": path, "backup_path": backup_path}

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
        # Keyed by (coef_key, device_id, raw_csv) for dedupe/overwrite.
        existing_idx: Dict[tuple, int] = {}
        for i, r in enumerate(runs):
            try:
                key = (str(r.get("coef_key") or ""), str(r.get("device_id") or ""), str(r.get("raw_csv") or ""))
            except Exception:
                continue
            if key[0] and key[1] and key[2]:
                existing_idx[key] = i

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

            # IMPORTANT: exclude room-temp baseline raw tests from rollup scoring.
            # Those tests are used to *learn* the bias baseline and will make top-3 look artificially good.
            tmin = float(getattr(config, "TEMP_BASELINE_ROOM_TEMP_MIN_F", 71.0))
            tmax = float(getattr(config, "TEMP_BASELINE_ROOM_TEMP_MAX_F", 77.0))
            try:
                baseline_csvs = baseline_csvs_for_devices(repo=self._repo, device_ids=[device_id], min_temp_f=tmin, max_temp_f=tmax)
            except Exception:
                baseline_csvs = set()

            tests = self._repo.list_temperature_tests(device_id)
            for ti, raw_csv in enumerate(tests):
                if raw_csv in baseline_csvs:
                    continue
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
                # IMPORTANT: room_temp_f is the *ideal reference temp*, not the test's measured temp.
                room_temp_f = float(getattr(config, "TEMP_IDEAL_ROOM_TEMP_F", 76.0))

                emit(
                    {
                        "status": "running",
                        "message": f"{device_id}: processing {ti+1}/{len(tests)}",
                        "progress": 5,
                    }
                )

                # If this coef set already exists for this test, skip processing and just analyze it.
                try:
                    details_existing = self._repo.get_temperature_test_details(raw_csv)
                    proc_runs_existing = list(details_existing.get("processed_runs") or [])
                except Exception:
                    proc_runs_existing = []

                baseline_path = ""
                selected_path = ""
                for r in proc_runs_existing:
                    if r.get("is_baseline") and not baseline_path:
                        baseline_path = str(r.get("path") or "")
                        continue
                    if r.get("is_baseline"):
                        continue
                    if _coef_key(str(r.get("mode") or "legacy"), dict(r.get("slopes") or {})) == coef_key:
                        selected_path = str(r.get("path") or "")
                        break

                if baseline_path and selected_path and os.path.isfile(baseline_path) and os.path.isfile(selected_path):
                    try:
                        payload = self._analyzer.analyze_temperature_processed_runs(baseline_path, selected_path, meta)
                    except Exception as exc:
                        errors.append(f"{device_id}: analyze failed {os.path.basename(raw_csv)}: {exc}")
                        continue
                else:
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
                    for r in proc_runs:
                        if r.get("is_baseline"):
                            baseline_path = str(r.get("path") or "")
                            break
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
                    # end else (processing path)

                grid = dict(payload.get("grid") or {})
                device_type = str(grid.get("device_type") or pt)
                body_weight_n = float((payload.get("meta") or {}).get("body_weight_n") or 0.0)
                baseline_scores = {
                    k: score_run_against_bias(
                        run_data=payload.get("baseline") or {},
                        stage_key=k,
                        device_type=device_type,
                        body_weight_n=body_weight_n,
                        bias_map=bias_map,
                    )
                    for k in ("all", "db", "bw")
                }
                selected_scores = {
                    k: score_run_against_bias(
                        run_data=payload.get("selected") or {},
                        stage_key=k,
                        device_type=device_type,
                        body_weight_n=body_weight_n,
                        bias_map=bias_map,
                    )
                    for k in ("all", "db", "bw")
                }

                row = {
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
                k_existing = (coef_key, device_id, raw_csv)
                if k_existing in existing_idx:
                    runs[existing_idx[k_existing]] = row
                else:
                    existing_idx[k_existing] = len(runs)
                    runs.append(row)

        rollup["version"] = 1
        rollup["plate_type"] = pt
        rollup["updated_at_ms"] = int(time.time() * 1000)
        rollup["runs"] = runs
        path = self.save_rollup(pt, rollup)

        msg = f"Batch run complete for type {pt} ({coef_key})"
        if errors:
            msg = f"{msg} (with errors)"
        return {"ok": True, "message": msg, "rollup_path": path, "errors": errors}

    def top3_for_plate_type(self, plate_type: str, *, sort_by: str = "mean_abs") -> List[Dict[str, object]]:
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

        # Filter out room-temp baseline raw tests (even if they exist in an older rollup file).
        try:
            tmin = float(getattr(config, "TEMP_BASELINE_ROOM_TEMP_MIN_F", 71.0))
            tmax = float(getattr(config, "TEMP_BASELINE_ROOM_TEMP_MAX_F", 77.0))
            devs = {str(r.get("device_id") or "") for r in runs if str(r.get("device_id") or "")}
            baseline_csvs = baseline_csvs_for_devices(repo=self._repo, device_ids=devs, min_temp_f=tmin, max_temp_f=tmax)
            if baseline_csvs:
                runs = [r for r in runs if str(r.get("raw_csv") or "") not in baseline_csvs]
        except Exception:
            pass
        return top3_rows_for_plate_type(runs=runs, sort_by=str(sort_by or "mean_abs"))

    def aggregate_selected_all_mean_signed(self, plate_type: str, *, coef_key: str) -> Optional[dict]:
        """
        Aggregate the rollup's selected/all mean_signed for a specific coef_key.
        Uses the same eligibility logic as top-3 selection.
        """
        pt = str(plate_type or "").strip()
        if not pt:
            return None
        rollup = self.load_rollup(pt)
        runs = list(rollup.get("runs") or [])
        return aggregate_mean_signed_for_coef_key(runs=runs, coef_key=str(coef_key or ""))

    def list_existing_unified_candidates(
        self,
        plate_type: str,
        *,
        mode: str = "scalar",
        min_coef: float = 0.0,
        max_coef: float = 0.01,
        tol: float = 1e-9,
    ) -> List[Dict[str, object]]:
        """
        Return pre-run unified candidates (x=y=z) that already exist in the rollup, along with
        their aggregate selected/all mean_signed (eligibility rules applied).
        """
        pt = str(plate_type or "").strip()
        if not pt:
            return []
        m = str(mode or "scalar").strip().lower()
        rollup = self.load_rollup(pt)
        runs = list(rollup.get("runs") or [])

        # Collect unique coef_keys for this mode that appear in the rollup.
        keys = set()
        for r in runs:
            try:
                ck = str(r.get("coef_key") or "")
            except Exception:
                continue
            if not ck:
                continue
            parsed = parse_coef_key(ck)
            if not isinstance(parsed, dict):
                continue
            if str(parsed.get("mode") or "").strip().lower() != m:
                continue
            x = float(parsed.get("x") or 0.0)
            y = float(parsed.get("y") or 0.0)
            z = float(parsed.get("z") or 0.0)
            if abs(x - y) > tol or abs(x - z) > tol:
                continue
            if x < float(min_coef) - tol or x > float(max_coef) + tol:
                continue
            keys.add(ck)

        out: List[Dict[str, object]] = []
        for ck in sorted(keys):
            parsed = parse_coef_key(ck) or {}
            coef = float(parsed.get("x") or 0.0)
            agg = self.aggregate_selected_all_mean_signed(pt, coef_key=ck)
            if not isinstance(agg, dict):
                continue
            try:
                ms = float(agg.get("mean_signed"))
            except Exception:
                continue
            out.append(
                {
                    "coef": coef,
                    "coef_key": ck,
                    "mean_signed": ms,
                    "score_mean_abs": agg.get("score_mean_abs"),
                    "std_signed": agg.get("std_signed"),
                    "coverage": str(agg.get("coverage") or ""),
                }
            )

        # Sort by coef ascending for bracket scanning.
        out.sort(key=lambda r: float(r.get("coef") or 0.0))
        return out

    def export_distinct_experiment_report(
        self,
        plate_type: str,
        *,
        seed: dict,
        candidates: List[dict],
    ) -> Dict[str, object]:
        """
        Export distinct-coefs experiment CSVs for a plate type based on existing rollup data.
        """
        pt = str(plate_type or "").strip()
        rollup = self.load_rollup(pt)
        runs = list(rollup.get("runs") or [])
        return export_distinct_experiment_report(plate_type=pt, rollup_runs=runs, seed=dict(seed or {}), candidates=list(candidates or []))


