from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtWidgets, QtGui

from ...delegates import DiscreteTestDelegate
from ....project_paths import data_dir


def _icon_path(name: str) -> str:
    """Return absolute path to an icon in the assets/icons folder."""
    # __file__ is in panels/live_testing/, need to go up to ui/ then into assets/icons/
    panels_dir = os.path.dirname(os.path.dirname(__file__))  # panels/
    ui_dir = os.path.dirname(panels_dir)  # ui/
    return os.path.join(ui_dir, "assets", "icons", name)


class SessionControlsBox(QtWidgets.QGroupBox):
    """
    Session Controls group box for `LiveTestingPanel`.

    Contains session info fields (tester, body weight, device/model IDs) and
    navigation controls with icon buttons.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Session Controls", parent)
        controls_layout = QtWidgets.QVBoxLayout(self)

        # Backing store for discrete tests (for filtering)
        self._all_discrete_tests: list[tuple[str, str, str]] = []

        # --- Session Type Selector ---
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Session Type:"))
        self.session_mode_combo = QtWidgets.QComboBox()
        try:
            self.session_mode_combo.addItems(["Normal", "Temperature Test", "Discrete Temp. Testing"])
        except Exception:
            pass
        mode_row.addWidget(self.session_mode_combo)
        mode_row.addStretch(1)
        controls_layout.addLayout(mode_row)

        # --- Session Info Fields (moved from SessionInfoBox) ---
        info_form = QtWidgets.QFormLayout()
        info_form.setContentsMargins(0, 8, 0, 8)

        # Tester input
        self.edit_tester = QtWidgets.QLineEdit()
        self.edit_tester.setPlaceholderText("Enter tester name")
        info_form.addRow("Tester:", self.edit_tester)

        # Body weight input
        self.spin_bw = QtWidgets.QDoubleSpinBox()
        self.spin_bw.setRange(0.0, 5000.0)
        self.spin_bw.setDecimals(1)
        self.spin_bw.setSuffix(" N")
        self.spin_bw.setSingleStep(1.0)
        self.spin_bw.setSpecialValueText("—")
        info_form.addRow("Body Weight:", self.spin_bw)

        # Device ID (read-only label)
        self.lbl_device = QtWidgets.QLabel("—")
        info_form.addRow("Device ID:", self.lbl_device)

        # Model ID (read-only label)
        self.lbl_model = QtWidgets.QLabel("—")
        info_form.addRow("Model ID:", self.lbl_model)

        # CSV Capture checkbox
        self.chk_capture = QtWidgets.QCheckBox("Capture (CSV/logs)")
        self.chk_capture.setChecked(False)  # Default off for Normal mode
        info_form.addRow(self.chk_capture)

        # Save location row (only visible when capture is enabled)
        self._save_row_widget = QtWidgets.QWidget()
        save_row_layout = QtWidgets.QHBoxLayout(self._save_row_widget)
        save_row_layout.setContentsMargins(0, 0, 0, 0)
        save_row_layout.setSpacing(6)
        self.edit_save_dir = QtWidgets.QLineEdit()
        self.edit_save_dir.setPlaceholderText("Choose save location...")
        self.btn_browse = QtWidgets.QPushButton("Browse…")
        save_row_layout.addWidget(self.edit_save_dir, 1)
        save_row_layout.addWidget(self.btn_browse, 0)
        info_form.addRow("CSV Save Location:", self._save_row_widget)

        # Initially hide save location row
        self._save_row_widget.setVisible(False)

        # Wire capture checkbox to show/hide save location
        self.chk_capture.toggled.connect(self._on_capture_toggled)
        self.btn_browse.clicked.connect(self._choose_save_directory)

        controls_layout.addLayout(info_form)

        # Backward-compat aliases
        self.lbl_tester = self.edit_tester
        self.lbl_bw = self.spin_bw

        # Backward-compat stub labels (hidden container keeps them from appearing)
        self._hidden_container = QtWidgets.QWidget(self)
        self._hidden_container.setVisible(False)
        self.lbl_test_date_title = QtWidgets.QLabel("Test Date:", self._hidden_container)
        self.lbl_test_date = QtWidgets.QLabel("—", self._hidden_container)
        self.lbl_short_label_title = QtWidgets.QLabel("Short Label:", self._hidden_container)
        self.lbl_short_label = QtWidgets.QLabel("—", self._hidden_container)
        self.lbl_thresh_db = QtWidgets.QLabel("—", self._hidden_container)
        self.lbl_thresh_bw = QtWidgets.QLabel("—", self._hidden_container)

        # --- Navigation Row: Previous | Start | End | Next ---
        nav_row = QtWidgets.QHBoxLayout()
        nav_row.setSpacing(4)

        icon_size = QtCore.QSize(20, 20)
        btn_size = QtCore.QSize(36, 28)

        self.btn_prev = QtWidgets.QPushButton()
        self.btn_prev.setIcon(QtGui.QIcon(_icon_path("chevron_left.svg")))
        self.btn_prev.setIconSize(icon_size)
        self.btn_prev.setFixedSize(btn_size)
        self.btn_prev.setToolTip("Previous Stage")

        self.btn_start = QtWidgets.QPushButton()
        self.btn_start.setIcon(QtGui.QIcon(_icon_path("play.svg")))
        self.btn_start.setIconSize(icon_size)
        self.btn_start.setFixedSize(btn_size)
        self.btn_start.setToolTip("Start Session")

        self.btn_end = QtWidgets.QPushButton()
        self.btn_end.setIcon(QtGui.QIcon(_icon_path("stop.svg")))
        self.btn_end.setIconSize(icon_size)
        self.btn_end.setFixedSize(btn_size)
        self.btn_end.setToolTip("End Session")
        self.btn_end.setEnabled(False)

        self.btn_next = QtWidgets.QPushButton()
        self.btn_next.setIcon(QtGui.QIcon(_icon_path("chevron_right.svg")))
        self.btn_next.setIconSize(icon_size)
        self.btn_next.setFixedSize(btn_size)
        self.btn_next.setToolTip("Next Stage")
        self.btn_next.setEnabled(False)

        nav_row.addStretch(1)
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.btn_start)
        nav_row.addWidget(self.btn_end)
        nav_row.addWidget(self.btn_next)
        nav_row.addStretch(1)

        controls_layout.addLayout(nav_row)

        # Stage/progress text (backward compat - inside hidden container)
        self.lbl_stage_title = QtWidgets.QLabel("Stage:", self._hidden_container)
        self.stage_label = QtWidgets.QLabel("—", self._hidden_container)
        self.lbl_progress_title = QtWidgets.QLabel("Progress:", self._hidden_container)
        self.progress_label = QtWidgets.QLabel("0 / 0 cells", self._hidden_container)

        # --- Discrete Temp Testing Controls ---
        discrete_picker_box = QtWidgets.QVBoxLayout()
        self.lbl_discrete_tests = QtWidgets.QLabel("Tests:", self)
        self.lbl_discrete_tests.setVisible(False)

        filters_row = QtWidgets.QHBoxLayout()
        filters_row.setContentsMargins(0, 0, 0, 0)
        filters_row.setSpacing(6)
        self.discrete_type_filter = QtWidgets.QComboBox(self)
        self.discrete_type_filter.addItems(["All types", "06", "07", "08", "11"])
        self.discrete_type_filter.setVisible(False)
        self.discrete_plate_filter = QtWidgets.QComboBox(self)
        self.discrete_plate_filter.addItem("All plates")
        self.discrete_plate_filter.setVisible(False)
        self.discrete_type_label = QtWidgets.QLabel("Type:", self)
        self.discrete_type_label.setVisible(False)
        self.discrete_plate_label = QtWidgets.QLabel("Plate:", self)
        self.discrete_plate_label.setVisible(False)
        filters_row.addWidget(self.discrete_type_label)
        filters_row.addWidget(self.discrete_type_filter)
        filters_row.addWidget(self.discrete_plate_label)
        filters_row.addWidget(self.discrete_plate_filter, 1)
        discrete_picker_box.addLayout(filters_row)

        self.discrete_test_list = QtWidgets.QListWidget(self)
        self.discrete_test_list.setVisible(False)
        try:
            self.discrete_test_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.discrete_test_list.setUniformItemSizes(True)
            self.discrete_test_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.discrete_test_list.setItemDelegate(DiscreteTestDelegate(self.discrete_test_list))
        except Exception:
            pass
        discrete_picker_box.addWidget(self.discrete_test_list, 1)
        controls_layout.addLayout(discrete_picker_box)

        # Discrete temp actions
        discrete_row = QtWidgets.QHBoxLayout()
        self.btn_discrete_new = QtWidgets.QPushButton("Start New Test", self)
        self.btn_discrete_new.setVisible(False)
        self.btn_discrete_add = QtWidgets.QPushButton("Add to Existing Test", self)
        self.btn_discrete_add.setVisible(False)
        self.btn_discrete_add.setEnabled(False)
        try:
            self.btn_discrete_new.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.btn_discrete_add.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        discrete_row.addWidget(self.btn_discrete_new, 1)
        discrete_row.addWidget(self.btn_discrete_add, 1)
        controls_layout.addLayout(discrete_row)

    # --- Session Info Accessors ---
    def get_tester_name(self) -> str:
        return self.edit_tester.text().strip()

    def get_body_weight_n(self) -> float:
        return float(self.spin_bw.value())

    def set_tester_name(self, name: str) -> None:
        self.edit_tester.setText(name or "")

    def set_body_weight_n(self, weight_n: float) -> None:
        try:
            self.spin_bw.setValue(float(weight_n) if weight_n else 0.0)
        except Exception:
            self.spin_bw.setValue(0.0)

    def apply_discrete_test_meta(self, key: str) -> None:
        """Load test_meta.json for discrete temp tests and populate fields."""
        import json

        # Clear defaults
        try:
            self.edit_tester.setText("")
            self.lbl_device.setText("—")
            self.lbl_model.setText("—")
            self.spin_bw.setValue(0.0)
            self.lbl_test_date.setText("—")
            self.lbl_short_label.setText("—")
        except Exception:
            pass

        if not key:
            return

        base = str(key)
        try:
            if os.path.isfile(base):
                base = os.path.dirname(base)
        except Exception:
            pass

        meta_path = os.path.join(base, "test_meta.json")
        if not os.path.isfile(meta_path):
            return

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception:
            meta = {}

        if not isinstance(meta, dict):
            return

        try:
            self.edit_tester.setText(str(meta.get("tester_name") or meta.get("tester") or "").strip())
        except Exception:
            pass
        try:
            self.lbl_device.setText(str(meta.get("device_id") or meta.get("deviceId") or "").strip() or "—")
        except Exception:
            pass
        try:
            self.lbl_model.setText(str(meta.get("model_id") or meta.get("modelId") or "").strip() or "—")
        except Exception:
            pass
        try:
            bw = meta.get("body_weight_n")
            self.spin_bw.setValue(float(bw) if bw is not None else 0.0)
        except Exception:
            pass
        try:
            self.lbl_test_date.setText(str(meta.get("date") or "").strip() or "—")
        except Exception:
            pass
        try:
            self.lbl_short_label.setText(str(meta.get("short_label") or "").strip() or "—")
        except Exception:
            pass

    # --- Discrete Test Management ---
    def set_discrete_tests(self, tests: list[tuple[str, str, str]]) -> None:
        """Populate discrete test picker with (label, date, key) triples."""
        self._all_discrete_tests = list(tests or [])

        # Refresh plate filter options based on available device ids
        try:
            device_ids: set[str] = set()
            base_dir = data_dir("discrete_temp_testing")
            for _label, _date_str, key in self._all_discrete_tests:
                path = str(key)
                try:
                    rel = os.path.relpath(path, base_dir)
                except Exception:
                    rel = path
                parts = rel.split(os.sep)
                if parts and parts[0]:
                    device_ids.add(parts[0])
            self.discrete_plate_filter.blockSignals(True)
            self.discrete_plate_filter.clear()
            self.discrete_plate_filter.addItem("All plates")
            for did in sorted(device_ids):
                self.discrete_plate_filter.addItem(did)
        except Exception:
            pass
        finally:
            try:
                self.discrete_plate_filter.blockSignals(False)
            except Exception:
                pass

        self.apply_discrete_filters()

    def apply_discrete_filters(self) -> None:
        """Re-populate discrete_test_list based on current filter selections."""
        try:
            self.discrete_test_list.blockSignals(True)
        except Exception:
            pass
        try:
            self.discrete_test_list.clear()
            base_dir = data_dir("discrete_temp_testing")
            try:
                type_sel = str(self.discrete_type_filter.currentText() or "All types")
            except Exception:
                type_sel = "All types"
            try:
                plate_sel = str(self.discrete_plate_filter.currentText() or "All plates")
            except Exception:
                plate_sel = "All plates"

            for label, date_str, key in self._all_discrete_tests:
                path = str(key)
                try:
                    rel = os.path.relpath(path, base_dir)
                except Exception:
                    rel = path
                parts = rel.split(os.sep)
                device_id = parts[0] if parts else ""
                dev_type = ""
                if device_id:
                    if "." in device_id:
                        dev_type = device_id.split(".", 1)[0]
                    else:
                        dev_type = device_id[:2]

                if type_sel != "All types" and dev_type != type_sel:
                    continue
                if plate_sel != "All plates" and device_id != plate_sel:
                    continue

                item = QtWidgets.QListWidgetItem()
                try:
                    item.setData(QtCore.Qt.UserRole, path)
                    item.setData(QtCore.Qt.UserRole + 1, str(label))
                    item.setData(QtCore.Qt.UserRole + 2, str(date_str))
                    item.setData(QtCore.Qt.UserRole + 3, device_id)
                except Exception:
                    pass
                self.discrete_test_list.addItem(item)
        except Exception:
            pass
        finally:
            try:
                self.discrete_test_list.blockSignals(False)
            except Exception:
                pass

    def current_discrete_test_key(self) -> str:
        try:
            item = self.discrete_test_list.currentItem()
            if item is None:
                return ""
            key = item.data(QtCore.Qt.UserRole)
            return str(key or "")
        except Exception:
            return ""

    # --- CSV Capture Controls ---
    def _project_root(self) -> str:
        """Project root: three levels up from this file (panels/live_testing -> panels -> ui -> src -> root)."""
        try:
            return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        except Exception:
            return os.getcwd()

    def _get_default_save_dir(self, is_temp: bool) -> str:
        """Get default save directory based on session type."""
        base = self._project_root()
        folder = "temp_testing" if is_temp else "live_test_logs"
        path = os.path.join(base, folder)
        # Add device ID subfolder if available
        device_id = self.lbl_device.text().strip()
        if device_id and device_id != "—":
            path = os.path.join(path, device_id)
        return path

    def _on_capture_toggled(self, enabled: bool) -> None:
        """Show/hide save location row when capture is toggled."""
        self._save_row_widget.setVisible(bool(enabled))
        if enabled and not self.edit_save_dir.text().strip():
            # Set default path based on current session type
            is_temp = self.session_mode_combo.currentIndex() == 1  # Index 1 = Temperature Test
            self.edit_save_dir.setText(self._get_default_save_dir(is_temp))

    def _choose_save_directory(self) -> None:
        """Open file dialog to choose CSV save directory."""
        is_temp = self.session_mode_combo.currentIndex() == 1
        start_dir = self.edit_save_dir.text().strip() or self._get_default_save_dir(is_temp)
        options = QtWidgets.QFileDialog.Options()
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose CSV Save Folder", start_dir, options=options
        )
        if directory:
            self.edit_save_dir.setText(directory)

    def set_capture_default_for_mode(self, is_temp_test: bool) -> None:
        """Set the capture checkbox default based on session mode (called when mode changes)."""
        # Normal mode: OFF, Temperature mode: ON
        self.chk_capture.setChecked(is_temp_test)
        if is_temp_test and not self.edit_save_dir.text().strip():
            self.edit_save_dir.setText(self._get_default_save_dir(True))

    def is_capture_enabled(self) -> bool:
        """Return whether CSV capture is enabled."""
        return bool(self.chk_capture.isChecked())

    def get_save_directory(self) -> str:
        """Return the configured save directory."""
        return self.edit_save_dir.text().strip()

    def set_session_active(self, active: bool) -> None:
        """Lock/unlock session info controls when a live test is active."""
        locked = bool(active)
        # Lock editable session info fields
        self.edit_tester.setEnabled(not locked)
        self.spin_bw.setEnabled(not locked)
        self.session_mode_combo.setEnabled(not locked)
        # Lock capture settings
        self.chk_capture.setEnabled(not locked)
        self.edit_save_dir.setEnabled(not locked)
        self.btn_browse.setEnabled(not locked)
