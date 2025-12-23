from __future__ import annotations

import datetime
from typing import Optional

from PySide6 import QtCore, QtWidgets


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


