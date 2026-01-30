from __future__ import annotations

import os
from typing import Callable, Dict, Optional

from PySide6 import QtCore

from .backend_csv_processor import process_csv_via_backend


class _DiscreteTempProcessWorker(QtCore.QThread):
    def __init__(
        self,
        *,
        parent: QtCore.QObject,
        csv_path: str,
        device_id: str,
        output_dir: str,
        off_filename: str,
        on_filename: str,
        coefficients: dict,
        hardware: object | None,
        room_temp_f: float,
        timeout_s: int,
    ) -> None:
        super().__init__(parent)
        self.csv_path = str(csv_path)
        self.device_id = str(device_id)
        self.output_dir = str(output_dir)
        self.off_filename = str(off_filename)
        self.on_filename = str(on_filename)
        self.coefficients = dict(coefficients or {})
        self.hardware = hardware
        self.room_temp_f = float(room_temp_f)
        self.timeout_s = int(timeout_s)

        self.err: Optional[str] = None
        self.off_path: str = ""
        self.on_path: str = ""

    def run(self) -> None:  # noqa: D401
        try:
            os.makedirs(self.output_dir, exist_ok=True)

            off_full = os.path.join(self.output_dir, self.off_filename)
            if os.path.isfile(off_full):
                self.off_path = off_full
            else:
                self.off_path = process_csv_via_backend(
                    input_csv_path=self.csv_path,
                    device_id=self.device_id,
                    output_folder=self.output_dir,
                    output_filename=self.off_filename,
                    use_temperature_correction=False,
                    room_temp_f=self.room_temp_f,
                    mode="scalar",
                    temperature_coefficients=None,
                    sanitize_header=True,
                    hardware=self.hardware,
                    timeout_s=self.timeout_s,
                )

            self.on_path = process_csv_via_backend(
                input_csv_path=self.csv_path,
                device_id=self.device_id,
                output_folder=self.output_dir,
                output_filename=self.on_filename,
                use_temperature_correction=True,
                room_temp_f=self.room_temp_f,
                mode="scalar",
                temperature_coefficients=self.coefficients,
                sanitize_header=True,
                hardware=self.hardware,
                timeout_s=self.timeout_s,
            )
        except Exception as e:
            self.err = str(e)


class _DiscreteTempTuneWorker(QtCore.QThread):
    def __init__(
        self,
        *,
        parent: QtCore.QObject,
        test_folder: str,
        csv_path: str,
        device_id: str,
        hardware: object | None,
        room_temp_f: float,
        add_runs: int,
        timeout_s: int,
        sanitize_header: bool,
        baseline_low_f: float,
        baseline_high_f: float,
        x_max: float,
        y_max: float,
        z_max: float,
        step: float,
        stop_after_worse: int,
        precise_origin_coeffs: dict | None,
        precise_offset_max: float,
        precise_offset_step: float,
        score_axes: tuple[str, ...],
        score_weights: tuple[float, float, float],
        emit_progress: Callable[[dict], None],
    ) -> None:
        super().__init__(parent)
        self.test_folder = str(test_folder)
        self.csv_path = str(csv_path)
        self.device_id = str(device_id)
        self.hardware = hardware
        self.room_temp_f = float(room_temp_f)
        self.add_runs = int(add_runs)
        self.timeout_s = int(timeout_s)
        self.sanitize_header = bool(sanitize_header)
        self.baseline_low_f = float(baseline_low_f)
        self.baseline_high_f = float(baseline_high_f)
        self.x_max = float(x_max)
        self.y_max = float(y_max)
        self.z_max = float(z_max)
        self.step = float(step)
        self.stop_after_worse = int(stop_after_worse)
        self.precise_origin_coeffs = dict(precise_origin_coeffs) if isinstance(precise_origin_coeffs, dict) else None
        self.precise_offset_max = float(precise_offset_max)
        self.precise_offset_step = float(precise_offset_step)
        self.score_axes = tuple(score_axes)
        self.score_weights = tuple(score_weights)
        self._emit_progress = emit_progress

        self.err: Optional[str] = None
        self.best: dict | None = None

    def run(self) -> None:
        try:
            # Import here to avoid loading tuning code unless used.
            from ..ui.discrete_temp.tuning_pair_sweep import run_pair_sweep_tuning  # local import
            from ..ui.discrete_temp.tuning_local_refine import run_local_refine_tuning  # local import

            def _emit(p: dict) -> None:
                try:
                    self._emit_progress(dict(p or {}))
                except Exception:
                    pass

            # If precise_origin_coeffs is provided, treat this as "Precise Tune" and use local refinement.
            if isinstance(self.precise_origin_coeffs, dict) and self.precise_origin_coeffs:
                self.best = run_local_refine_tuning(
                    test_folder=self.test_folder,
                    input_csv_path=self.csv_path,
                    device_id=self.device_id,
                    add_runs=int(self.add_runs),
                    hardware=self.hardware,
                    room_temp_f=self.room_temp_f,
                    timeout_s=int(self.timeout_s),
                    sanitize_header=bool(self.sanitize_header),
                    baseline_low_f=float(self.baseline_low_f),
                    baseline_high_f=float(self.baseline_high_f),
                    x_max=float(self.x_max),
                    y_max=float(self.y_max),
                    z_max=float(self.z_max),
                    refine_step=float(self.precise_offset_step),
                    stop_after_worse=int(self.stop_after_worse),
                    score_axes=tuple(self.score_axes),
                    score_weights=tuple(self.score_weights),
                    progress_cb=_emit,
                    cancel_cb=self.isInterruptionRequested,
                    start_coeffs=self.precise_origin_coeffs,
                )
            else:
                self.best = run_pair_sweep_tuning(
                    test_folder=self.test_folder,
                    input_csv_path=self.csv_path,
                    device_id=self.device_id,
                    add_runs=int(self.add_runs),
                    hardware=self.hardware,
                    room_temp_f=self.room_temp_f,
                    timeout_s=int(self.timeout_s),
                    sanitize_header=bool(self.sanitize_header),
                    baseline_low_f=float(self.baseline_low_f),
                    baseline_high_f=float(self.baseline_high_f),
                    x_max=float(self.x_max),
                    y_max=float(self.y_max),
                    z_max=float(self.z_max),
                    step=float(self.step),
                    stop_after_worse=int(self.stop_after_worse),
                    score_axes=tuple(self.score_axes),
                    score_weights=tuple(self.score_weights),
                    progress_cb=_emit,
                    cancel_cb=self.isInterruptionRequested,
                )
        except Exception as e:
            self.err = str(e)


class DiscreteTempProcessingService(QtCore.QObject):
    """
    App-service that owns background processing/tuning for discrete temp plots.

    Keeps backend/tuning orchestration out of widgets.
    """

    processed_ready = QtCore.Signal(dict)  # {error, off, on}
    tune_progress = QtCore.Signal(dict)  # arbitrary progress payload
    tune_ready = QtCore.Signal(dict)  # {error, best}

    def __init__(self, hardware: object | None = None, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._hardware = hardware
        self._worker: Optional[QtCore.QThread] = None
        self._tune_worker: Optional[QtCore.QThread] = None

    def cancel_processing(self) -> None:
        try:
            if self._worker is not None and self._worker.isRunning():
                self._worker.requestInterruption()
        except Exception:
            pass

    def cancel_tuning(self) -> None:
        try:
            if self._tune_worker is not None and self._tune_worker.isRunning():
                self._tune_worker.requestInterruption()
        except Exception:
            pass

    def process_generated(
        self,
        *,
        csv_path: str,
        device_id: str,
        output_dir: str,
        coeffs: dict,
        off_filename: str = "discrete_temp_session__nn_off.csv",
        on_filename: str = "discrete_temp_session__nn_on.csv",
        room_temp_f: float = 76.0,
        timeout_s: int = 300,
    ) -> None:
        self.cancel_processing()
        w = _DiscreteTempProcessWorker(
            parent=self,
            csv_path=csv_path,
            device_id=device_id,
            output_dir=output_dir,
            off_filename=off_filename,
            on_filename=on_filename,
            coefficients=coeffs,
            hardware=self._hardware,
            room_temp_f=room_temp_f,
            timeout_s=timeout_s,
        )
        self._worker = w

        def _done() -> None:
            self.processed_ready.emit(
                {"error": getattr(w, "err", None), "off": getattr(w, "off_path", ""), "on": getattr(w, "on_path", "")}
            )

        w.finished.connect(_done)
        w.start()

    def tune_best(
        self,
        *,
        test_folder: str,
        csv_path: str,
        device_id: str,
        room_temp_f: float = 76.0,
        add_runs: int = 50,
        timeout_s: int = 300,
        sanitize_header: bool = True,
        baseline_low_f: float = 74.0,
        baseline_high_f: float = 78.0,
        x_max: float = 0.005,
        y_max: float = 0.005,
        z_max: float = 0.008,
        step: float = 0.001,
        stop_after_worse: int = 2,
        precise_origin_coeffs: dict | None = None,
        precise_offset_max: float = 0.001,
        precise_offset_step: float = 0.0001,
        score_axes: tuple[str, ...] = ("z",),
        score_weights: tuple[float, float, float] = (0.0, 0.0, 1.0),
    ) -> None:
        self.cancel_tuning()

        w = _DiscreteTempTuneWorker(
            parent=self,
            test_folder=test_folder,
            csv_path=csv_path,
            device_id=device_id,
            hardware=self._hardware,
            room_temp_f=room_temp_f,
            add_runs=int(add_runs),
            timeout_s=int(timeout_s),
            sanitize_header=bool(sanitize_header),
            baseline_low_f=float(baseline_low_f),
            baseline_high_f=float(baseline_high_f),
            x_max=float(x_max),
            y_max=float(y_max),
            z_max=float(z_max),
            step=float(step),
            stop_after_worse=int(stop_after_worse),
            precise_origin_coeffs=precise_origin_coeffs,
            precise_offset_max=float(precise_offset_max),
            precise_offset_step=float(precise_offset_step),
            score_axes=tuple(score_axes),
            score_weights=tuple(score_weights),
            emit_progress=lambda p: self.tune_progress.emit(dict(p or {})),
        )
        self._tune_worker = w

        def _done() -> None:
            self.tune_ready.emit({"error": getattr(w, "err", None), "best": getattr(w, "best", None)})

        w.finished.connect(_done)
        w.start()


