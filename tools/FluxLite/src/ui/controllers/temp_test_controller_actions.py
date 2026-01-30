from __future__ import annotations

import os
from PySide6 import QtCore

from .temp_test_workers import TemperatureAnalysisWorker


class TempTestControllerActionsMixin:
    """
    Mixin for TempTestController methods that don't need to live in the main controller file.
    Keeps `temp_test_controller.py` under the preferred size limit.
    """

    def _on_analysis_result(self, payload: dict) -> None:
        # Update cache if needed
        self._last_analysis_payload = payload
        if payload and payload.get("baseline"):
            worker = self.sender()
            if isinstance(worker, TemperatureAnalysisWorker) and worker.baseline_csv:
                if self._cached_baseline_path != worker.baseline_csv:
                    self._cached_baseline_path = worker.baseline_csv
                    self._cached_baseline_result = payload.get("baseline")

        self.analysis_status.emit({"status": "completed", "message": "Analysis ready"})
        self.analysis_ready.emit(payload)

    def _on_analysis_error(self, message: str) -> None:
        self.analysis_status.emit({"status": "error", "message": message})
        self.processing_status.emit({"status": "error", "message": message})

    def delete_processed_run(self, file_path: str) -> None:
        """Delete a processed run file."""
        if not file_path:
            return

        if not os.path.exists(file_path):
            self.processing_status.emit({"status": "error", "message": "File not found"})
            return

        try:
            os.remove(file_path)
            # We do NOT delete the meta file as per instructions "NOTHING ELSE"

            # Refresh details
            if self._current_test_csv:
                self.load_test_details(self._current_test_csv)

            self.processing_status.emit({"status": "completed", "message": "File deleted"})
        except Exception as e:
            self.processing_status.emit({"status": "error", "message": f"Failed to delete file: {str(e)}"})

    def configure_correction(self, payload: dict):
        self.hardware.configure_temperature_correction(
            payload.get("slopes", {}),
            payload.get("use_temperature_correction", False),
            payload.get("room_temperature_f", 72.0),
        )

    def prepare_grid_display(self, payload: dict, stage_key: str) -> None:
        """
        Prepare grid cell display data from analysis payload and emit grid_display_ready.
        Uses GridPresenter for logic.
        """
        if not payload:
            return

        grid_info = payload.get("grid", {})
        meta = payload.get("meta", {})
        body_weight_n = float(meta.get("body_weight_n") or 0.0)
        device_type = str(grid_info.get("device_type", "06"))

        baseline = payload.get("baseline", {})
        selected = payload.get("selected", {})

        bias = self.bias_map() if self._grading_mode == "bias" else None

        # Compute view models
        baseline_vms = self.presenter.compute_analysis_cells(baseline, stage_key, device_type, body_weight_n, bias_map=bias)
        selected_vms = self.presenter.compute_analysis_cells(selected, stage_key, device_type, body_weight_n, bias_map=bias)

        # Convert to dicts for view compatibility (for now)
        # Passing 'color' (QColor) instead of 'color_bin'
        def _to_dict(vms):
            return [
                {
                    "row": vm.row,
                    "col": vm.col,
                    "text": vm.text,
                    "color": vm.color,
                    "tooltip": vm.tooltip,
                }
                for vm in vms
            ]

        display_data = {
            "grid_info": grid_info,
            "device_id": meta.get("device_id"),
            "baseline_cells": _to_dict(baseline_vms),
            "selected_cells": _to_dict(selected_vms),
        }

        self.grid_display_ready.emit(display_data)

    def plot_stage_detection(self) -> None:
        """
        Emit signal to launch matplotlib visualization showing stage detection windows.
        """
        if not self._current_meta:
            self.processing_status.emit({"status": "error", "message": "No test loaded"})
            return

        baseline_path = self._current_baseline_path or ""
        selected_path = self._current_selected_path or ""

        # Fallback: find paths from processed runs if not set
        if not baseline_path:
            for run in self._current_processed_runs:
                if run.get("is_baseline"):
                    baseline_path = str(run.get("path") or "").strip()
                    break

        if not selected_path:
            for run in reversed(self._current_processed_runs):
                if not run.get("is_baseline"):
                    selected_path = str(run.get("path") or "").strip()
                    break

        if not baseline_path:
            self.processing_status.emit({"status": "error", "message": "No baseline CSV found"})
            return

        if not selected_path:
            selected_path = baseline_path

        body_weight_n = float(self._current_meta.get("body_weight_n") or 800.0)

        baseline_windows = {}
        baseline_segments = []
        selected_windows = {}
        selected_segments = []

        if self._last_analysis_payload:
            base_data = self._last_analysis_payload.get("baseline") or {}
            sel_data = self._last_analysis_payload.get("selected") or {}
            baseline_windows = base_data.get("_windows") or {}
            baseline_segments = base_data.get("_segments") or []
            selected_windows = sel_data.get("_windows") or {}
            selected_segments = sel_data.get("_segments") or []

        self.plot_ready.emit(
            {
                "baseline_path": baseline_path,
                "selected_path": selected_path,
                "body_weight_n": body_weight_n,
                "baseline_windows": baseline_windows,
                "baseline_segments": baseline_segments,
                "selected_windows": selected_windows,
                "selected_segments": selected_segments,
            }
        )


