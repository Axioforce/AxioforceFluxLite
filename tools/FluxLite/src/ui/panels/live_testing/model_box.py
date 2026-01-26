from __future__ import annotations

import datetime
from typing import Optional

from PySide6 import QtCore, QtWidgets, QtGui


class ModelBox(QtWidgets.QGroupBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Model", parent)
        layout = QtWidgets.QVBoxLayout(self)

        current_row = QtWidgets.QHBoxLayout()
        current_row.addWidget(QtWidgets.QLabel("Current Model:"))
        self.lbl_current_model = QtWidgets.QLabel("—")
        current_row.addWidget(self.lbl_current_model)
        current_row.addStretch(1)
        layout.addLayout(current_row)

        layout.addWidget(QtWidgets.QLabel("Available Models:"))
        self.model_list = QtWidgets.QListWidget()
        self.model_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        try:
            self.model_list.setUniformItemSizes(True)
        except Exception:
            pass
        try:
            self.model_list.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass
        layout.addWidget(self.model_list, 1)

        status_row = QtWidgets.QHBoxLayout()
        self.lbl_model_status = QtWidgets.QLabel("")
        self.lbl_model_status.setStyleSheet("color:#ccc;")
        status_row.addWidget(self.lbl_model_status)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        # Reconnect hint label (shows after activate/deactivate, fades out)
        self.lbl_reconnect_hint = QtWidgets.QLabel("Reconnect cable to take effect")
        self.lbl_reconnect_hint.setStyleSheet("color: #FFA500; font-style: italic;")  # Orange color
        self.lbl_reconnect_hint.setVisible(False)
        layout.addWidget(self.lbl_reconnect_hint)

        # Setup fade animation for the hint
        self._hint_opacity_effect = QtWidgets.QGraphicsOpacityEffect(self.lbl_reconnect_hint)
        self.lbl_reconnect_hint.setGraphicsEffect(self._hint_opacity_effect)
        self._hint_opacity_effect.setOpacity(1.0)

        self._hint_fade_animation = QtCore.QPropertyAnimation(self._hint_opacity_effect, b"opacity")
        self._hint_fade_animation.setDuration(1000)  # 1 second fade
        self._hint_fade_animation.setStartValue(1.0)
        self._hint_fade_animation.setEndValue(0.0)
        self._hint_fade_animation.finished.connect(self._on_hint_fade_finished)

        self._hint_timer = QtCore.QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._start_hint_fade)

        act_row = QtWidgets.QHBoxLayout()
        self.btn_activate = QtWidgets.QPushButton("Activate")
        self.btn_deactivate = QtWidgets.QPushButton("Deactivate")
        act_row.addWidget(self.btn_activate)
        act_row.addWidget(self.btn_deactivate)
        act_row.addStretch(1)
        layout.addLayout(act_row)

        self.btn_package_model = QtWidgets.QPushButton("Package Model…")
        layout.addWidget(self.btn_package_model)
        layout.addStretch(1)

    def set_current_model(self, model_text: Optional[str]) -> None:
        self.lbl_current_model.setText((model_text or "").strip() or "—")
        self.set_model_status("")

    def set_model_list(self, models: list[dict]) -> None:
        try:
            self.model_list.clear()
            for m in (models or []):
                try:
                    mid = str((m or {}).get("modelId") or (m or {}).get("model_id") or "").strip()
                except Exception:
                    mid = ""
                if not mid:
                    continue
                loc = str((m or {}).get("location") or "").strip()
                date_text = ""
                try:
                    raw_ts = (m or {}).get("packageDate") or (m or {}).get("package_date")
                    if raw_ts is not None:
                        ts = float(raw_ts)
                        if ts > 1e12:
                            ts = ts / 1000.0
                        dt = datetime.datetime.fromtimestamp(ts)
                        date_text = dt.strftime("%m.%d.%Y")
                except Exception:
                    date_text = ""
                if loc and date_text:
                    text = f"{mid}  ({loc}) • {date_text}"
                elif loc:
                    text = f"{mid}  ({loc})"
                elif date_text:
                    text = f"{mid}  • {date_text}"
                else:
                    text = mid
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, mid)
                self.model_list.addItem(item)
        except Exception:
            pass

    def set_model_status(self, text: Optional[str]) -> None:
        self.lbl_model_status.setText((text or "").strip())

    def set_model_controls_enabled(self, enabled: bool) -> None:
        try:
            self.btn_activate.setEnabled(bool(enabled))
            self.btn_deactivate.setEnabled(bool(enabled))
            self.btn_package_model.setEnabled(bool(enabled))
        except Exception:
            pass

    def show_reconnect_hint(self) -> None:
        """Show the reconnect hint message, then fade it out after 5 seconds."""
        try:
            # Stop any ongoing animation/timer
            self._hint_timer.stop()
            self._hint_fade_animation.stop()

            # Reset and show
            self._hint_opacity_effect.setOpacity(1.0)
            self.lbl_reconnect_hint.setVisible(True)

            # Start timer to begin fade after 5 seconds
            self._hint_timer.start(5000)
        except Exception:
            pass

    def _start_hint_fade(self) -> None:
        """Start the fade-out animation."""
        try:
            self._hint_fade_animation.start()
        except Exception:
            pass

    def _on_hint_fade_finished(self) -> None:
        """Hide the label after fade completes."""
        try:
            self.lbl_reconnect_hint.setVisible(False)
            self._hint_opacity_effect.setOpacity(1.0)  # Reset for next time
        except Exception:
            pass


