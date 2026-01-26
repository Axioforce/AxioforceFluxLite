from __future__ import annotations

from typing import Dict, Optional, List, Tuple

from PySide6 import QtCore

from ...app_services.testing import TestingService
from ...app_services.temperature_test_import_service import import_temperature_raw_tests


class ProcessingWorker(QtCore.QThread):
    """Worker thread for running temperature processing in the background."""

    def __init__(
        self,
        service: TestingService,
        folder: str,
        device_id: str,
        csv_path: str,
        slopes: dict,
        room_temp_f: float,
        mode: str = "scalar",
    ):
        super().__init__()
        self.service = service
        self.folder = folder
        self.device_id = device_id
        self.csv_path = csv_path
        self.slopes = slopes
        self.room_temp_f = float(room_temp_f)
        self.mode = mode

    def run(self):
        self.service.run_temperature_processing(self.folder, self.device_id, self.csv_path, self.slopes, self.room_temp_f, self.mode)


class TemperatureAnalysisWorker(QtCore.QThread):
    """Background worker for processed run analysis."""

    result_ready = QtCore.Signal(dict)
    error = QtCore.Signal(str)

    def __init__(
        self,
        service: TestingService,
        baseline_csv: str,
        selected_csv: str,
        meta: Dict[str, object],
        baseline_data: Optional[Dict[str, object]] = None,
    ):
        super().__init__()
        self.service = service
        self.baseline_csv = baseline_csv
        self.selected_csv = selected_csv
        self.meta = dict(meta or {})
        self.baseline_data = baseline_data

    def run(self) -> None:
        try:
            payload = self.service.analyze_temperature_processed_runs(
                self.baseline_csv,
                self.selected_csv,
                self.meta,
                baseline_data=self.baseline_data,
            )
            self.result_ready.emit(payload)
        except Exception as exc:
            self.error.emit(str(exc))


class BiasComputeWorker(QtCore.QThread):
    """Background worker for per-device room-temp baseline bias computation."""

    result_ready = QtCore.Signal(dict)

    def __init__(self, service: TestingService, device_id: str):
        super().__init__()
        self.service = service
        self.device_id = str(device_id or "").strip()

    def run(self) -> None:
        try:
            res = self.service.compute_temperature_bias_for_device(self.device_id)
            self.result_ready.emit(dict(res or {}))
        except Exception as exc:
            self.result_ready.emit({"ok": False, "message": str(exc), "errors": [str(exc)]})


class PlateTypeRollupWorker(QtCore.QThread):
    """Background worker for batch rollup across a plate type."""

    result_ready = QtCore.Signal(dict)

    def __init__(self, service: TestingService, plate_type: str, coefs: dict, mode: str):
        super().__init__()
        self.service = service
        self.plate_type = str(plate_type or "").strip()
        self.coefs = dict(coefs or {})
        self.mode = str(mode or "legacy")

    def run(self) -> None:
        try:
            res = self.service.run_temperature_coefs_across_plate_type(
                plate_type=self.plate_type,
                coefs=self.coefs,
                mode=self.mode,
            )
            self.result_ready.emit(dict(res or {}))
        except Exception as exc:
            self.result_ready.emit({"ok": False, "message": str(exc), "errors": [str(exc)]})


class PlateTypeAutoSearchWorker(QtCore.QThread):
    """Background worker that performs coefficient auto-search for a plate type."""

    result_ready = QtCore.Signal(dict)
    status_ready = QtCore.Signal(dict)

    def __init__(self, service: TestingService, plate_type: str, mode: str, search_mode: str):
        super().__init__()
        self.service = service
        self.plate_type = str(plate_type or "").strip()
        self.mode = str(mode or "scalar")
        self.search_mode = str(search_mode or "unified").strip().lower()

    @staticmethod
    def _quantize(v: float, step: float) -> float:
        if step <= 0:
            return float(v)
        return round(float(v) / float(step)) * float(step)

    def _eval(self, coef: float, *, cache: dict) -> tuple[float, Optional[float], Optional[dict]]:
        c = float(coef)
        c = max(0.0, min(0.01, c))
        c = self._quantize(c, 0.0001)  # keep candidates stable/deduped in the search
        key = f"{c:.6f}"
        if key in cache:
            ms = cache[key]
            return c, (float(ms) if ms is not None else None), None

        coefs = {"x": c, "y": c, "z": c}
        self.status_ready.emit({"status": "running", "message": f"Auto search: evaluating coef {c:.4f}…"})

        # Ensure rollup entries exist for this candidate.
        self.service.run_temperature_coefs_across_plate_type(plate_type=self.plate_type, coefs=coefs, mode=self.mode)

        agg = self.service.aggregate_temperature_coefs_for_plate_type(self.plate_type, coefs=coefs, mode=self.mode)
        ms = None
        if isinstance(agg, dict):
            try:
                ms = float(agg.get("mean_signed"))
            except Exception:
                ms = None
        cache[key] = ms
        return c, ms, agg

    @staticmethod
    def _sign(v: float) -> int:
        if v > 0:
            return 1
        if v < 0:
            return -1
        return 0

    def _seed_cache_from_existing(self) -> List[dict]:
        """
        Pull pre-run unified candidates from the existing rollup, and seed cache so we don't
        re-run those coefs unless needed.
        """
        existing = self.service.list_existing_unified_temperature_coef_candidates_for_plate_type(
            self.plate_type, mode=self.mode, min_coef=0.0, max_coef=0.01
        )
        rows: List[dict] = []
        for r in existing or []:
            try:
                c = float(r.get("coef"))
                ms = float(r.get("mean_signed"))
            except Exception:
                continue
            c = max(0.0, min(0.01, self._quantize(c, 0.0001)))
            rows.append({"coef": c, "mean_signed": ms, "coverage": str(r.get("coverage") or "")})
        rows.sort(key=lambda rr: float(rr.get("coef") or 0.0))
        return rows

    @staticmethod
    def _narrowest_sign_flip_bracket(rows: List[dict]) -> Optional[Tuple[float, float, float, float]]:
        """
        Find the narrowest adjacent bracket (by coef) where mean_signed changes sign.
        Returns (low_c, high_c, low_ms, high_ms) or None.
        """
        best = None
        best_w = None
        for i in range(len(rows or []) - 1):
            a = rows[i] or {}
            b = rows[i + 1] or {}
            try:
                ca = float(a.get("coef"))
                cb = float(b.get("coef"))
                ma = float(a.get("mean_signed"))
                mb = float(b.get("mean_signed"))
            except Exception:
                continue
            if ca == cb:
                continue
            sa = 1 if ma > 0 else (-1 if ma < 0 else 0)
            sb = 1 if mb > 0 else (-1 if mb < 0 else 0)
            if sa == 0 or sb == 0:
                # exact zero exists; no need to bracket (caller will handle as best)
                continue
            if sa == sb:
                continue
            low_c, high_c = (ca, cb) if ca < cb else (cb, ca)
            low_ms, high_ms = (ma, mb) if ca < cb else (mb, ma)
            w = high_c - low_c
            if best_w is None or w < best_w:
                best_w = w
                best = (low_c, high_c, low_ms, high_ms)
        return best

    def run(self) -> None:
        if self.search_mode not in ("unified",):
            self.result_ready.emit({"ok": False, "message": f"Unsupported auto search mode: {self.search_mode}", "best": {}})
            return

        # Search parameters (per spec)
        min_c = 0.0
        max_c = 0.01
        coarse_step = 0.001
        refine_step = 0.0001
        start_c = 0.001

        cache: dict = {}
        best = {"coef": None, "mean_signed": None, "abs_mean_signed": None, "coverage": None}

        def _maybe_best(c: float, ms: Optional[float], agg: Optional[dict]) -> None:
            if ms is None:
                return
            abs_ms = abs(float(ms))
            if best["abs_mean_signed"] is None or abs_ms < float(best["abs_mean_signed"]):
                best["coef"] = float(c)
                best["mean_signed"] = float(ms)
                best["abs_mean_signed"] = abs_ms
                if isinstance(agg, dict):
                    best["coverage"] = str(agg.get("coverage") or "")

        # 1) Take stock of pre-run candidates and seed cache/best.
        pre = self._seed_cache_from_existing()
        explored = set()
        for r in pre:
            try:
                c = float(r.get("coef"))
                ms = float(r.get("mean_signed"))
            except Exception:
                continue
            explored.add(c)
            cache[f"{c:.6f}"] = ms
            _maybe_best(c, ms, dict(r))

        # If any pre-run candidate hit exactly 0.0, we're done.
        if best["mean_signed"] is not None and float(best["mean_signed"]) == 0.0:
            self.result_ready.emit({"ok": True, "message": f"Auto search complete: coef {float(best['coef']):.4f} hit 0.00% mean signed.", "best": best})
            return

        # 2) If we already have a sign-flip bracket, pick the narrowest one and fill missing 0.0001-grid points inside it.
        bracket = self._narrowest_sign_flip_bracket(pre)
        if bracket is not None:
            low_c, high_c, low_ms, high_ms = bracket
            self.status_ready.emit({"status": "running", "message": f"Auto search: using pre-run bracket [{low_c:.4f}, {high_c:.4f}] and filling missing 0.0001 steps…"})

            # Determine all 0.0001-grid points inside the bracket.
            lo = self._quantize(low_c, refine_step)
            hi = self._quantize(high_c, refine_step)
            if hi < lo:
                lo, hi = hi, lo

            # Build missing points list.
            pts = []
            n_steps = int(round((hi - lo) / refine_step))
            for k in range(n_steps + 1):
                c = self._quantize(lo + k * refine_step, refine_step)
                if c not in explored:
                    pts.append(c)

            # Evaluate missing points in "closest-to-mid" order, shrinking bracket when possible.
            def _mid() -> float:
                return self._quantize((low_c + high_c) / 2.0, refine_step)

            pts.sort(key=lambda c: abs(c - _mid()))
            for c in pts:
                # If already adjacent, stop.
                if (high_c - low_c) <= refine_step:
                    break
                cm, msm, aggm = self._eval(c, cache=cache)
                explored.add(cm)
                _maybe_best(cm, msm, aggm)
                if msm is None:
                    continue
                msmf = float(msm)
                if msmf == 0.0:
                    break
                # Update bracket if this point helps narrow it (keep sign flip).
                if (low_ms > 0 and msmf > 0) or (low_ms < 0 and msmf < 0):
                    low_c, low_ms = cm, msmf
                else:
                    high_c, high_ms = cm, msmf

            self.result_ready.emit(
                {
                    "ok": True,
                    "message": f"Auto search complete (pre-run bracket): best coef {float(best['coef']):.4f} (mean signed {float(best['mean_signed']):+.2f}%).",
                    "best": best,
                }
            )
            return

        # 3) No pre-run bracket found → fall back to original bracket-then-bisection search.
        c0, ms0, agg0 = self._eval(start_c, cache=cache)
        explored.add(c0)
        _maybe_best(c0, ms0, agg0)
        if ms0 is None:
            self.result_ready.emit({"ok": False, "message": "Auto search failed: no eligible runs to score at start coef.", "best": best})
            return
        if float(ms0) == 0.0:
            self.result_ready.emit({"ok": True, "message": f"Auto search complete: coef {c0:.4f} hit 0.00% mean signed.", "best": best})
            return

        direction = 1.0 if float(ms0) > 0.0 else -1.0
        prev_c, prev_ms = c0, float(ms0)
        bracket = None  # (low_c, high_c, low_ms, high_ms)

        # Bracket by stepping until sign flip or bounds.
        while True:
            next_c = prev_c + direction * coarse_step
            if next_c < min_c or next_c > max_c:
                break
            c1, ms1, agg1 = self._eval(next_c, cache=cache)
            explored.add(c1)
            _maybe_best(c1, ms1, agg1)
            if ms1 is None:
                prev_c, prev_ms = c1, prev_ms
                continue
            ms1f = float(ms1)
            if ms1f == 0.0:
                self.result_ready.emit({"ok": True, "message": f"Auto search complete: coef {c1:.4f} hit 0.00% mean signed.", "best": best})
                return
            if (prev_ms > 0 and ms1f < 0) or (prev_ms < 0 and ms1f > 0):
                low_c, high_c = (prev_c, c1) if prev_c < c1 else (c1, prev_c)
                low_ms, high_ms = (prev_ms, ms1f) if prev_c < c1 else (ms1f, prev_ms)
                bracket = (low_c, high_c, low_ms, high_ms)
                break
            prev_c, prev_ms = c1, ms1f

        # If no bracket, return best seen within bounds.
        if bracket is None:
            if best["coef"] is None:
                self.result_ready.emit({"ok": False, "message": "Auto search complete: no scored candidates.", "best": best})
            else:
                self.result_ready.emit(
                    {
                        "ok": True,
                        "message": f"Auto search complete (no sign flip in range): best coef {float(best['coef']):.4f} (mean signed {float(best['mean_signed']):+.2f}%).",
                        "best": best,
                    }
                )
            return

        low_c, high_c, low_ms, high_ms = bracket
        self.status_ready.emit({"status": "running", "message": f"Auto search: bracket found [{low_c:.4f}, {high_c:.4f}] (sign flip). Refining…"})

        # Bisection refinement
        for _ in range(60):
            if (high_c - low_c) <= refine_step:
                break
            mid = self._quantize((low_c + high_c) / 2.0, refine_step)
            if mid == low_c or mid == high_c:
                break
            # If midpoint was already explored, choose nearest unexplored grid point to mid inside the bracket.
            if mid in explored:
                # Scan outward by 0.0001 for an unexplored point.
                found = None
                step_n = int(round((high_c - low_c) / refine_step))
                for j in range(1, step_n + 1):
                    lo_try = self._quantize(mid - j * refine_step, refine_step)
                    hi_try = self._quantize(mid + j * refine_step, refine_step)
                    if lo_try > low_c and lo_try < high_c and lo_try not in explored:
                        found = lo_try
                        break
                    if hi_try > low_c and hi_try < high_c and hi_try not in explored:
                        found = hi_try
                        break
                if found is None:
                    break
                mid = found

            cm, msm, aggm = self._eval(mid, cache=cache)
            explored.add(cm)
            _maybe_best(cm, msm, aggm)
            if msm is None:
                break
            msmf = float(msm)
            if msmf == 0.0:
                break
            # Choose the half-interval that contains the sign flip.
            if (low_ms > 0 and msmf > 0) or (low_ms < 0 and msmf < 0):
                low_c, low_ms = cm, msmf
            else:
                high_c, high_ms = cm, msmf

        self.result_ready.emit(
            {
                "ok": True,
                "message": f"Auto search complete: best coef {float(best['coef']):.4f} (mean signed {float(best['mean_signed']):+.2f}%).",
                "best": best,
            }
        )


class TemperatureImportWorker(QtCore.QThread):
    """Background worker to import raw temperature tests (copy CSV+meta into temp_testing/<device_id>/)."""

    result_ready = QtCore.Signal(dict)

    def __init__(self, file_paths: List[str]):
        super().__init__()
        self.file_paths = list(file_paths or [])

    def run(self) -> None:
        try:
            res = import_temperature_raw_tests(self.file_paths)
        except Exception as exc:
            res = {"ok": False, "imported": 0, "skipped": 0, "errors": [str(exc)], "affected_devices": [], "affected_plate_types": [], "imported_by_device": {}}
        self.result_ready.emit(dict(res or {}))


class TemperatureAutoUpdateWorker(QtCore.QThread):
    """
    Background worker that performs the post-import auto-update flow:
      - Reset rollups for each affected plate type
      - Run unified auto-search for each affected plate type (which recomputes bias caches internally)
    """

    status_ready = QtCore.Signal(dict)
    result_ready = QtCore.Signal(dict)

    def __init__(self, service: TestingService, plate_types: List[str]):
        super().__init__()
        self.service = service
        self.plate_types = [str(p or "").strip() for p in (plate_types or []) if str(p or "").strip()]

    def run(self) -> None:
        pts = sorted(set(self.plate_types))
        if not pts:
            self.result_ready.emit({"ok": False, "message": "No affected plate types to update.", "results": {}, "errors": ["No plate types"]})
            return

        errors: List[str] = []
        results: Dict[str, dict] = {}

        for pt in pts:
            try:
                self.status_ready.emit({"status": "running", "message": f"Auto-update: resetting rollup for plate {pt}…"})
                self.service.reset_temperature_coef_rollup(pt, backup=True)
            except Exception as exc:
                errors.append(f"{pt}: reset rollup failed: {exc}")

            # Run unified auto-search; this will recompute bias caches for each device in the plate type
            # (TemperatureCoefRollupService always calls compute_and_store_bias_for_device).
            self.status_ready.emit({"status": "running", "message": f"Auto-update: running unified auto search for plate {pt}…"})
            try:
                w = PlateTypeAutoSearchWorker(self.service, pt, "scalar", "unified")
                w.status_ready.connect(self.status_ready.emit)
                holder: List[dict] = []
                w.result_ready.connect(lambda p: holder.append(dict(p or {})))
                # Run synchronously within this worker thread.
                w.run()
                results[pt] = holder[-1] if holder else {"ok": False, "message": "Auto search produced no result"}
            except Exception as exc:
                errors.append(f"{pt}: auto search failed: {exc}")
                results[pt] = {"ok": False, "message": str(exc)}

        ok = all(bool((results.get(pt) or {}).get("ok")) for pt in pts) and not errors
        msg = "Auto-update complete" if ok else "Auto-update complete (with errors)"
        self.result_ready.emit({"ok": ok, "message": msg, "results": results, "errors": errors})


class PlateTypeDistinctCoefsWorker(QtCore.QThread):
    """Runs a small distinct-coefs neighborhood experiment for a plate type.

    Seed: best-known unified coef (x=y=z) for the plate type (by mean abs if available).
    Candidates: for step sizes [0.001, 0.0005, 0.0001], evaluate +/- step on each axis (18 candidates).
    Exports: summary + per-test CSVs for offline analysis.
    """

    result_ready = QtCore.Signal(dict)
    status_ready = QtCore.Signal(dict)

    def __init__(self, service: TestingService, plate_type: str, mode: str = "scalar"):
        super().__init__()
        self.service = service
        self.plate_type = str(plate_type or "").strip()
        self.mode = str(mode or "scalar").strip().lower()

    @staticmethod
    def _quantize(v: float, step: float = 0.0001) -> float:
        if step <= 0:
            return float(v)
        return round(float(v) / float(step)) * float(step)

    @staticmethod
    def _coef_key(mode: str, x: float, y: float, z: float) -> str:
        m = str(mode or "scalar").strip().lower()
        return f"{m}:x={float(x):.6f},y={float(y):.6f},z={float(z):.6f}"

    def _pick_seed_unified(self) -> Optional[dict]:
        """Pick best unified coef candidate (x=y=z) from existing rollup data.

        Prefer lowest score_mean_abs; fallback to smallest abs(mean_signed).
        """
        rows = self.service.list_existing_unified_temperature_coef_candidates_for_plate_type(
            self.plate_type, mode=self.mode, min_coef=0.0, max_coef=0.01
        )
        best = None
        best_score = None
        best_signed = None
        for r in rows or []:
            try:
                c = float(r.get("coef"))
            except Exception:
                continue
            try:
                score_abs = float(r.get("score_mean_abs")) if r.get("score_mean_abs") is not None else None
            except Exception:
                score_abs = None
            try:
                ms = float(r.get("mean_signed"))
            except Exception:
                ms = None

            if score_abs is not None:
                if best_score is None or score_abs < best_score:
                    best_score = score_abs
                    best = {"coef": c, "score_mean_abs": score_abs, "mean_signed": ms}
                continue

            if ms is not None and best_score is None:
                ab = abs(ms)
                if best_signed is None or ab < best_signed:
                    best_signed = ab
                    best = {"coef": c, "score_mean_abs": None, "mean_signed": ms}
        return best

    def run(self) -> None:
        pt = self.plate_type
        if not pt:
            self.result_ready.emit({"ok": False, "message": "Missing plate type", "errors": ["Missing plate type"]})
            return

        seed_info = self._pick_seed_unified()
        if seed_info is None:
            self.status_ready.emit({"status": "running", "message": "Distinct coefs: no unified seed found; running unified search to create one..."})
            try:
                w = PlateTypeAutoSearchWorker(self.service, pt, self.mode, "unified")
                w.status_ready.connect(self.status_ready.emit)
                w.run()
            except Exception:
                pass
            seed_info = self._pick_seed_unified()

        if seed_info is None:
            self.result_ready.emit({"ok": False, "message": "Distinct coefs: no unified seed available.", "errors": ["No unified seed"]})
            return

        seed_c = float(seed_info.get("coef") or 0.0)
        seed_c = max(0.0, min(0.01, self._quantize(seed_c)))
        seed = {"x": seed_c, "y": seed_c, "z": seed_c, "coef_key": self._coef_key(self.mode, seed_c, seed_c, seed_c)}

        steps = [0.001, 0.0005, 0.0001]
        axes = ["x", "y", "z"]
        candidates: List[dict] = []
        seen = set()

        for s in steps:
            for ax in axes:
                for direction in (+1.0, -1.0):
                    x, y, z = seed_c, seed_c, seed_c
                    if ax == "x":
                        x = self._quantize(max(0.0, min(0.01, x + direction * s)))
                    elif ax == "y":
                        y = self._quantize(max(0.0, min(0.01, y + direction * s)))
                    else:
                        z = self._quantize(max(0.0, min(0.01, z + direction * s)))
                    key = (x, y, z)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(
                        {
                            "step": float(s),
                            "axis": ax,
                            "direction": "+" if direction > 0 else "-",
                            "x": float(x),
                            "y": float(y),
                            "z": float(z),
                            "coef_key": self._coef_key(self.mode, x, y, z),
                        }
                    )

        for i, c in enumerate(candidates):
            msg = (
                f"Distinct coefs: {i+1}/{len(candidates)} (step {float(c.get('step') or 0.0):.4f}, "
                f"{c.get('axis')}{c.get('direction')}): x={float(c.get('x') or 0.0):.4f},"
                f"y={float(c.get('y') or 0.0):.4f},z={float(c.get('z') or 0.0):.4f}"
            )
            self.status_ready.emit({"status": "running", "message": msg})
            try:
                self.service.run_temperature_coefs_across_plate_type(
                    plate_type=pt,
                    coefs={"x": float(c["x"]), "y": float(c["y"]), "z": float(c["z"])} ,
                    mode=self.mode,
                )
            except Exception as exc:
                self.result_ready.emit({"ok": False, "message": f"Distinct coefs failed: {exc}", "errors": [str(exc)]})
                return

        self.status_ready.emit({"status": "running", "message": "Distinct coefs: exporting CSV report..."})
        try:
            exporter = getattr(self.service, "export_distinct_temperature_experiment_report", None)
            if callable(exporter):
                rep = exporter(pt, seed=seed, candidates=candidates)
            else:
                # Fallback for already-running app instances where TestingService wasn't reloaded yet.
                rep = self.service._temp_rollup.export_distinct_experiment_report(pt, seed=seed, candidates=candidates)  # type: ignore[attr-defined]
        except Exception as exc:
            self.result_ready.emit({"ok": False, "message": f"Distinct coefs export failed: {exc}", "errors": [str(exc)]})
            return

        self.result_ready.emit(
            {
                "ok": True,
                "message": f"Distinct coefs complete: exported {int(rep.get('candidate_count') or 0)} candidates.",
                "report": rep,
                "seed": seed,
            }
        )


class PlateTypeStageSplitMAEWorker(QtCore.QThread):
    """Export per-test best unified coef by MAE for BW and DB stages (non-baseline tests only)."""

    result_ready = QtCore.Signal(dict)
    status_ready = QtCore.Signal(dict)

    def __init__(self, service: TestingService, plate_type: str, mode: str = "scalar"):
        super().__init__()
        self.service = service
        self.plate_type = str(plate_type or "").strip()
        self.mode = str(mode or "scalar").strip().lower()

    def run(self) -> None:
        try:
            self.status_ready.emit({"status": "running", "message": f"Unified + k: exporting per-test report for plate {self.plate_type}…"})
        except Exception:
            pass

        try:
            res = dict(self.service.export_stage_split_mae_per_test_report(self.plate_type, mode=self.mode, status_cb=self.status_ready.emit) or {})
            ok = bool(res.get("ok"))
            csv_path = str(res.get("csv_path") or "")
            errs = list(res.get("errors") or [])
            msg = str(res.get("message") or ("Report exported" if ok else "Report failed"))
            payload = {"ok": ok, "message": msg, "errors": errs, "best": None}
            if csv_path:
                payload["report"] = {"kind": "stage_split", "csv_path": csv_path}
            if isinstance(res.get("summary"), dict):
                payload["summary"] = dict(res.get("summary") or {})
            self.result_ready.emit(payload)
        except Exception as exc:
            self.result_ready.emit({"ok": False, "message": str(exc), "errors": [str(exc)], "best": None})


