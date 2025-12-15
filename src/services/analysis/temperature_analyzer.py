from __future__ import annotations
import os
import csv
import math
import statistics
import logging
from typing import Dict, List, Optional, Tuple, Any

from ... import config
from ..device_geometry_service import DeviceGeometryService

logger = logging.getLogger(__name__)

class TemperatureAnalyzer:
    """
    Analyzes processed temperature test CSVs to find stable windows and evaluate accuracy.
    """

    def __init__(self, geometry_service: DeviceGeometryService):
        self.geometry = geometry_service

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
        device_type = self.geometry.infer_device_type(meta)
        rows, cols = self.geometry.get_grid_dimensions(device_type)
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
                        copx = float(row[copx_idx]) * 1000.0
                        copy = float(row[copy_idx]) * 1000.0
                    except (ValueError, IndexError):
                        continue
                    
                    if first_t_ms is None:
                        first_t_ms = t_ms
                    
                    if (t_ms - first_t_ms) < warmup_skip_ms:
                        continue

                    cell = self.geometry.map_cop_to_cell(device_type, rows, cols, copx, copy)
                    stage_cfg = self._match_stage(fz, stage_configs)
                    if cell is None or stage_cfg is None:
                        close_current()
                        continue

                    stage_key = stage_cfg["key"]
                    if current:
                        should_close = False
                        if current["stage_key"] != stage_key:
                            should_close = True
                        elif current["cell"] != cell:
                            should_close = True
                        else:
                            last_sample = current["samples"][-1]
                            last_x, last_y = last_sample[2], last_sample[3]
                            dist_jump = math.sqrt((copx - last_x)**2 + (copy - last_y)**2)
                            
                            start_sample = current["samples"][0]
                            start_x, start_y = start_sample[2], start_sample[3]
                            dist_drift = math.sqrt((copx - start_x)**2 + (copy - start_y)**2)
                            
                            max_drift = float(getattr(config, "TEMP_COP_MAX_DISPLACEMENT_MM", 100.0))
                            
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
        if not samples:
            return None
        
        n = len(samples)
        min_duration = max(200, desired_ms - tolerance_ms)
        max_duration = desired_ms + tolerance_ms
        
        best_stats: Optional[Dict[str, float]] = None
        best_std = float("inf")
        best_slope = float("inf")

        sum_t = 0.0
        sum_fz = 0.0
        sum_x = 0.0
        sum_y = 0.0
        sum_fz2 = 0.0 
        sum_t2 = 0.0 
        sum_tfz = 0.0 
        
        left = 0
        
        for right in range(n):
            t, fz, x, y = samples[right]
            t_rel = (t - samples[0][0]) / 1000.0
            
            sum_t += t_rel
            sum_fz += fz
            sum_x += x
            sum_y += y
            sum_fz2 += fz * fz
            sum_t2 += t_rel * t_rel
            sum_tfz += t_rel * fz
            
            while left < right:
                duration = samples[right][0] - samples[left][0]
                if duration <= max_duration:
                    break
                
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
            
            count = right - left + 1
            if count < 2:
                continue
                
            duration = samples[right][0] - samples[left][0]
            if duration < min_duration:
                continue
                
            mean_fz = sum_fz / count
            
            variance_num = sum_fz2 - (sum_fz * sum_fz / count)
            if variance_num < 0: variance_num = 0
            std = math.sqrt(variance_num / (count - 1)) if count > 1 else 0.0
            
            slope_num = count * sum_tfz - sum_t * sum_fz
            slope_den = count * sum_t2 - sum_t * sum_t
            if abs(slope_den) < 1e-9:
                slope = 0.0
            else:
                slope = slope_num / slope_den
                
            slope_abs = abs(slope)
            
            if std < best_std - 1e-6 or (abs(std - best_std) <= 1e-6 and slope_abs < best_slope):
                best_std = std
                best_slope = slope_abs
                best_stats = {
                    "std": std,
                    "slope": slope_abs,
                    "mean_fz": mean_fz,
                    "mean_x": sum_x / count,
                    "mean_y": sum_y / count,
                    "t_start": float(samples[left][0]),
                    "t_end": float(samples[right][0]),
                }

        return best_stats

    def _analyze_with_forced_windows(
        self,
        csv_path: str,
        stage_configs: List[Dict[str, object]],
        rows: int,
        cols: int,
        device_type: str,
        forced_windows: Dict[str, Dict[Tuple[int, int], Dict]],
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
        
        if not csv_path or not os.path.isfile(csv_path):
            return {"stages": stage_map}
        
        times, fz_vals, copx_vals, copy_vals = self._load_csv_for_analysis(csv_path)
        if not times:
            return {"stages": stage_map}
        
        cfg_by_key = {cfg["key"]: cfg for cfg in stage_configs}
        
        for stage_key, cells in forced_windows.items():
            cfg = cfg_by_key.get(stage_key)
            if not cfg:
                continue
            
            target_n = float(cfg.get("target_n", 0.0))
            tolerance_n = float(cfg.get("tolerance_n", 0.0))
            
            for (row, col), win_info in cells.items():
                t_start = win_info.get("t_start", 0)
                t_end = win_info.get("t_end", 0)
                
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

