from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from ..state import ViewState


class LiveTestingPanel(QtWidgets.QWidget):
    start_session_requested = QtCore.Signal()
    end_session_requested = QtCore.Signal()
    next_stage_requested = QtCore.Signal()

    def __init__(self, state: ViewState, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        # Session Controls
        controls_box = QtWidgets.QGroupBox("Session Controls")
        controls_layout = QtWidgets.QVBoxLayout(controls_box)

        self.btn_start = QtWidgets.QPushButton("Start Session")
        self.btn_end = QtWidgets.QPushButton("End Session")
        self.btn_end.setEnabled(False)
        self.btn_next = QtWidgets.QPushButton("Next Stage")
        self.btn_next.setEnabled(False)

        stage_row = QtWidgets.QHBoxLayout()
        stage_row.addWidget(QtWidgets.QLabel("Stage:"))
        self.stage_label = QtWidgets.QLabel("—")
        stage_row.addWidget(self.stage_label)
        stage_row.addStretch(1)

        progress_row = QtWidgets.QHBoxLayout()
        progress_row.addWidget(QtWidgets.QLabel("Progress:"))
        self.progress_label = QtWidgets.QLabel("0 / 0 cells")
        progress_row.addWidget(self.progress_label)
        progress_row.addStretch(1)

        controls_layout.addWidget(self.btn_start)
        controls_layout.addWidget(self.btn_end)
        controls_layout.addWidget(self.btn_next)
        controls_layout.addLayout(stage_row)
        controls_layout.addLayout(progress_row)

        # Testing Guide
        guide_box = QtWidgets.QGroupBox("Testing Guide")
        guide_layout = QtWidgets.QVBoxLayout(guide_box)
        self.guide_label = QtWidgets.QLabel("Use Start Session to begin. Follow prompts here.")
        self.guide_label.setWordWrap(True)
        guide_layout.addWidget(self.guide_label)
        guide_layout.addStretch(1)

        # Session Info & Thresholds
        meta_box = QtWidgets.QGroupBox("Session Info & Thresholds")
        meta_layout = QtWidgets.QFormLayout(meta_box)
        self.lbl_tester = QtWidgets.QLabel("—")
        self.lbl_device = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.lbl_bw = QtWidgets.QLabel("—")
        meta_layout.addRow("Tester:", self.lbl_tester)
        meta_layout.addRow("Device ID:", self.lbl_device)
        meta_layout.addRow("Model ID:", self.lbl_model)
        meta_layout.addRow("Body Weight (N):", self.lbl_bw)
        self.lbl_thresh_db = QtWidgets.QLabel("—")
        self.lbl_thresh_bw = QtWidgets.QLabel("—")
        meta_layout.addRow("45 lb DB (±N):", self.lbl_thresh_db)
        meta_layout.addRow("Body Weight (±N):", self.lbl_thresh_bw)

        # Live Telemetry
        tele_box = QtWidgets.QGroupBox("Live Telemetry")
        tele_layout = QtWidgets.QFormLayout(tele_box)
        self.lbl_fz = QtWidgets.QLabel("—")
        self.lbl_cop = QtWidgets.QLabel("—")
        self.lbl_stability = QtWidgets.QLabel("—")
        tele_layout.addRow("Fz (N):", self.lbl_fz)
        tele_layout.addRow("COP (mm):", self.lbl_cop)
        tele_layout.addRow("Stability:", self.lbl_stability)

        # Debug Status (large box)
        debug_box = QtWidgets.QGroupBox("Debug Status")
        debug_layout = QtWidgets.QVBoxLayout(debug_box)
        self.debug_label = QtWidgets.QLabel("—")
        self.debug_label.setWordWrap(True)
        debug_layout.addWidget(self.debug_label)
        debug_layout.addStretch(1)

        # Evenly distribute boxes side-by-side
        for w in (controls_box, guide_box, meta_box, tele_box, debug_box):
            w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            root.addWidget(w, 1)

        self.btn_start.clicked.connect(lambda: self.start_session_requested.emit())
        self.btn_end.clicked.connect(lambda: self.end_session_requested.emit())
        self.btn_next.clicked.connect(lambda: self.next_stage_requested.emit())

    # Overlay is now managed by the canvas; this panel keeps controls only
    def configure_grid(self, rows: int, cols: int) -> None:
        pass

    def set_active_cell(self, row: int | None, col: int | None) -> None:
        pass

    def set_cell_error_color(self, row: int, col: int, color: QtGui.QColor) -> None:
        pass

    # UI helpers for future wiring
    def set_metadata(self, tester: str, device_id: str, model_id: str, body_weight_n: float) -> None:
        self.lbl_tester.setText(tester or "—")
        self.lbl_device.setText(device_id or "—")
        self.lbl_model.setText(model_id or "—")
        try:
            self.lbl_bw.setText(f"{body_weight_n:.1f}")
        except Exception:
            self.lbl_bw.setText("—")

    def set_thresholds(self, db_tol_n: float, bw_tol_n: float) -> None:
        try:
            self.lbl_thresh_db.setText(f"±{db_tol_n:.1f}")
        except Exception:
            self.lbl_thresh_db.setText("—")
        try:
            self.lbl_thresh_bw.setText(f"±{bw_tol_n:.1f}")
        except Exception:
            self.lbl_thresh_bw.setText("—")

    def set_stage_progress(self, stage_text: str, completed_cells: int, total_cells: int) -> None:
        self.stage_label.setText(stage_text)
        self.progress_label.setText(f"{completed_cells} / {total_cells} cells")
        # Update guide text
        try:
            self.guide_label.setText(
                f"{stage_text}\n\n"
                "Instructions:\n"
                "- Place the specified load in any cell.\n"
                "- Keep COP inside the cell until stable (≈2s, Fz steady).\n"
                "- When captured, the cell will colorize. Move to next cell.\n"
                "- After all cells, follow prompts for the next stage/location."
            )
        except Exception:
            pass

    def set_next_stage_enabled(self, enabled: bool) -> None:
        try:
            self.btn_next.setEnabled(bool(enabled))
        except Exception:
            pass

    def set_next_stage_label(self, text: str) -> None:
        try:
            self.btn_next.setText(text or "Next Stage")
        except Exception:
            pass

    def set_telemetry(self, fz_n: Optional[float], cop_x_mm: Optional[float], cop_y_mm: Optional[float], stability_text: str) -> None:
        try:
            self.lbl_fz.setText(f"{fz_n:.1f}" if fz_n is not None else "—")
        except Exception:
            self.lbl_fz.setText("—")
        try:
            if cop_x_mm is None or cop_y_mm is None:
                self.lbl_cop.setText("—")
            else:
                self.lbl_cop.setText(f"{cop_x_mm:.1f}, {cop_y_mm:.1f}")
        except Exception:
            self.lbl_cop.setText("—")
        self.lbl_stability.setText(stability_text or "—")

    def set_debug_status(self, text: str | None) -> None:
        self.debug_label.setText((text or "").strip() or "—")


