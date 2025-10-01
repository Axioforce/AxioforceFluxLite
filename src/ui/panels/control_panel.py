from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ... import config
from ..state import ViewState
from ..delegates import DeviceListDelegate


class ControlPanel(QtWidgets.QWidget):
    connect_requested = QtCore.Signal(str, int)
    disconnect_requested = QtCore.Signal()
    start_capture_requested = QtCore.Signal(dict)
    stop_capture_requested = QtCore.Signal(dict)
    tare_requested = QtCore.Signal(str)
    scale_changed = QtCore.Signal(float)
    flags_changed = QtCore.Signal()
    config_changed = QtCore.Signal()
    refresh_devices_requested = QtCore.Signal()

    def __init__(self, state: ViewState, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state

        def _fixh(w: QtWidgets.QWidget, h: int = 22) -> None:
            w.setFixedHeight(h)

        def _fix_btn(b: QtWidgets.QPushButton, wmin: int = 110, h: int = 26) -> None:
            b.setFixedHeight(h)
            b.setMinimumWidth(wmin)
            b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(6, 6, 6, 6)

        tabs = QtWidgets.QTabWidget()
        self.tabs = tabs

        connection_tab = QtWidgets.QWidget()
        conn_layout = QtWidgets.QGridLayout(connection_tab)
        conn_layout.setVerticalSpacing(12)
        conn_row = 0

        conn_host_port_row = QtWidgets.QWidget()
        conn_host_port_layout = QtWidgets.QHBoxLayout(conn_host_port_row)
        conn_host_port_layout.setContentsMargins(0, 0, 0, 0)
        conn_host_port_layout.setSpacing(10)
        conn_host_port_layout.addWidget(QtWidgets.QLabel("Host:"))
        self.host_edit = QtWidgets.QLineEdit(config.SOCKET_HOST)
        _fixh(self.host_edit)
        self.host_edit.setMaximumWidth(220)
        conn_host_port_layout.addWidget(self.host_edit)
        conn_host_port_layout.addWidget(QtWidgets.QLabel("Port:"))
        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(config.SOCKET_PORT)
        _fixh(self.port_spin)
        self.port_spin.setMaximumWidth(80)
        conn_host_port_layout.addWidget(self.port_spin)
        conn_host_port_layout.addStretch(1)
        conn_layout.addWidget(conn_host_port_row, conn_row, 0, 1, 4)
        conn_row += 1

        conn_buttons_row = QtWidgets.QWidget()
        conn_buttons_layout = QtWidgets.QHBoxLayout(conn_buttons_row)
        conn_buttons_layout.setContentsMargins(0, 0, 0, 0)
        conn_buttons_layout.setSpacing(10)
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
        _fix_btn(self.btn_connect, 120)
        _fix_btn(self.btn_disconnect, 120)
        conn_buttons_layout.addWidget(self.btn_connect)
        conn_buttons_layout.addWidget(self.btn_disconnect)
        conn_buttons_layout.addStretch(1)
        conn_layout.addWidget(conn_buttons_row, conn_row, 0, 1, 4)
        conn_row += 1

        conn_layout.setRowStretch(conn_row, 1)

        demo_tab = QtWidgets.QWidget()
        demo_layout = QtWidgets.QGridLayout(demo_tab)
        demo_layout.setVerticalSpacing(12)
        demo_row = 0

        ids_row = QtWidgets.QWidget()
        ids_layout = QtWidgets.QHBoxLayout(ids_row)
        ids_layout.setContentsMargins(0, 0, 0, 0)
        ids_layout.setSpacing(10)
        ids_layout.addWidget(QtWidgets.QLabel("Group ID:"))
        self.group_edit = QtWidgets.QLineEdit()
        _fixh(self.group_edit)
        self.group_edit.setMaximumWidth(260)
        ids_layout.addWidget(self.group_edit)
        ids_layout.addWidget(QtWidgets.QLabel("Athlete ID:"))
        self.athlete_edit = QtWidgets.QLineEdit()
        _fixh(self.athlete_edit)
        self.athlete_edit.setMaximumWidth(260)
        ids_layout.addWidget(self.athlete_edit)
        ids_layout.addStretch(1)
        demo_layout.addWidget(ids_row, demo_row, 0, 1, 3)
        demo_row += 1

        capture_type_row = QtWidgets.QWidget()
        capture_type_layout = QtWidgets.QHBoxLayout(capture_type_row)
        capture_type_layout.setContentsMargins(0, 0, 0, 0)
        capture_type_layout.setSpacing(10)
        capture_type_layout.addWidget(QtWidgets.QLabel("Capture Type:"))
        self.capture_type = QtWidgets.QComboBox()
        self.capture_type.addItems(["pitch", "other"])
        _fixh(self.capture_type)
        self.capture_type.setMaximumWidth(140)
        capture_type_layout.addWidget(self.capture_type)
        capture_type_layout.addStretch(1)
        demo_layout.addWidget(capture_type_row, demo_row, 0, 1, 3)
        demo_row += 1

        btn_row_widget = QtWidgets.QWidget()
        btn_row = QtWidgets.QHBoxLayout(btn_row_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)
        self.btn_start = QtWidgets.QPushButton("Start Capture")
        self.btn_stop = QtWidgets.QPushButton("Stop Capture")
        _fix_btn(self.btn_start, 130)
        _fix_btn(self.btn_stop, 130)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch(1)
        demo_layout.addWidget(btn_row_widget, demo_row, 0, 1, 3)
        demo_row += 1

        demo_layout.setRowStretch(demo_row, 1)

        interface_tab = QtWidgets.QWidget()
        iface_layout = QtWidgets.QGridLayout(interface_tab)
        iface_layout.setVerticalSpacing(12)
        iface_row = 0

        cop_scale_row = QtWidgets.QWidget()
        cop_scale_layout = QtWidgets.QHBoxLayout(cop_scale_row)
        cop_scale_layout.setContentsMargins(0, 0, 0, 0)
        cop_scale_layout.setSpacing(10)
        cop_scale_layout.addWidget(QtWidgets.QLabel("COP Scale (px/N):"))
        self.scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scale_slider.setRange(1, 50)
        self.scale_slider.setValue(int(self.state.cop_scale_k * 100))
        self.scale_slider.setFixedHeight(8)
        self.scale_slider.setStyleSheet(
            "QSlider::groove:horizontal{height:6px;background:#444;border-radius:3px;}"
            "QSlider::handle:horizontal{background:#AAA;width:10px;height:10px;margin:-4px 0;border-radius:5px;}"
        )
        cop_scale_layout.addWidget(self.scale_slider)
        self.scale_label = QtWidgets.QLabel(f"{self.state.cop_scale_k:.2f}")
        _fixh(self.scale_label, 20)
        cop_scale_layout.addWidget(self.scale_label)
        cop_scale_layout.addStretch(1)
        iface_layout.addWidget(cop_scale_row, iface_row, 0, 1, 3)
        iface_row += 1

        checkboxes_row = QtWidgets.QWidget()
        checkboxes_layout = QtWidgets.QHBoxLayout(checkboxes_row)
        checkboxes_layout.setContentsMargins(0, 0, 0, 0)
        checkboxes_layout.setSpacing(10)
        self.chk_plates = QtWidgets.QCheckBox("Show Plates")
        self.chk_plates.setChecked(self.state.flags.show_plates)
        self.chk_labels = QtWidgets.QCheckBox("Show Labels")
        self.chk_labels.setChecked(self.state.flags.show_labels)
        checkboxes_layout.addWidget(self.chk_plates)
        checkboxes_layout.addWidget(self.chk_labels)
        checkboxes_layout.addStretch(1)
        iface_layout.addWidget(checkboxes_row, iface_row, 0, 1, 3)
        iface_row += 1

        iface_layout.setRowStretch(iface_row, 1)

        config_tab = QtWidgets.QWidget()
        cfg_layout = QtWidgets.QGridLayout(config_tab)
        cfg_row = 0

        layout_row = QtWidgets.QWidget()
        layout_row_layout = QtWidgets.QHBoxLayout(layout_row)
        layout_row_layout.setContentsMargins(0, 0, 0, 0)
        layout_row_layout.setSpacing(6)
        self.rb_layout_single = QtWidgets.QRadioButton("Single Device")
        self.rb_layout_mound = QtWidgets.QRadioButton("Pitching Mound")
        self.rb_layout_single.setChecked(True)
        layout_row_layout.addWidget(self.rb_layout_single)
        layout_row_layout.addWidget(self.rb_layout_mound)
        layout_row_layout.addStretch(1)
        cfg_layout.addWidget(layout_row, cfg_row, 0, 1, 3)
        self.state.display_mode = "single"
        cfg_row += 1

        filter_row = QtWidgets.QWidget()
        filter_row_layout = QtWidgets.QHBoxLayout(filter_row)
        filter_row_layout.setContentsMargins(0, 0, 0, 0)
        filter_row_layout.setSpacing(6)
        self.chk_filter_06 = QtWidgets.QCheckBox("Show 06 (Lite)")
        self.chk_filter_06.setChecked(True)
        self.chk_filter_07 = QtWidgets.QCheckBox("Show 07 (Launchpad)")
        self.chk_filter_07.setChecked(True)
        self.chk_filter_08 = QtWidgets.QCheckBox("Show 08 (XL)")
        self.chk_filter_08.setChecked(True)
        filter_row_layout.addWidget(self.chk_filter_06)
        filter_row_layout.addWidget(self.chk_filter_07)
        filter_row_layout.addWidget(self.chk_filter_08)
        filter_row_layout.addStretch(1)
        cfg_layout.addWidget(filter_row, cfg_row, 0, 1, 3)
        cfg_row += 1

        refresh_row = QtWidgets.QHBoxLayout()
        self.btn_refresh_devices = QtWidgets.QPushButton("Refresh Devices")
        refresh_row.addStretch(1)
        refresh_row.addWidget(self.btn_refresh_devices)
        cfg_layout.addLayout(refresh_row, cfg_row, 0, 1, 3)
        cfg_row += 1

        self.device_list = QtWidgets.QListWidget()
        self.device_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.device_list.setItemDelegate(DeviceListDelegate())
        cfg_layout.addWidget(self.device_list, cfg_row, 0, 1, 3)
        cfg_row += 1

        self._config_tab_index = tabs.addTab(config_tab, "Config")
        tabs.addTab(connection_tab, "Connection")
        tabs.addTab(interface_tab, "Interface")
        tabs.addTab(demo_tab, "Demo")

        tare_row = QtWidgets.QHBoxLayout()
        tare_row.addStretch(1)
        self.btn_tare = QtWidgets.QPushButton("Tare")
        _fix_btn(self.btn_tare, 110)
        tare_row.addWidget(self.btn_tare)
        root.addLayout(tare_row)

        self._all_devices: List[Tuple[str, str, str]] = []

        root.addWidget(tabs)

        self.btn_connect.clicked.connect(self._emit_connect)
        self.btn_disconnect.clicked.connect(self.disconnect_requested.emit)
        self.btn_start.clicked.connect(self._emit_start)
        self.btn_stop.clicked.connect(self._emit_stop)
        self.btn_tare.clicked.connect(self._emit_tare)
        self.scale_slider.valueChanged.connect(self._on_scale)
        self.chk_plates.stateChanged.connect(self._on_flags)
        self.chk_labels.stateChanged.connect(self._on_flags)
        self.chk_filter_06.stateChanged.connect(self._on_filter_changed)
        self.chk_filter_07.stateChanged.connect(self._on_filter_changed)
        self.chk_filter_08.stateChanged.connect(self._on_filter_changed)
        self.rb_layout_mound.toggled.connect(self._on_layout_changed)
        self.rb_layout_single.toggled.connect(self._on_layout_changed)
        self.device_list.currentItemChanged.connect(self._on_device_selected)
        self.btn_refresh_devices.clicked.connect(lambda: self.refresh_devices_requested.emit())
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _emit_connect(self) -> None:
        host = self.host_edit.text().strip() or config.SOCKET_HOST
        port = int(self.port_spin.value())
        self.connect_requested.emit(host, port)

    def _on_scale(self, value: int) -> None:
        self.state.cop_scale_k = max(0.01, value / 100.0)
        self.scale_label.setText(f"{self.state.cop_scale_k:.2f}")
        self.scale_changed.emit(self.state.cop_scale_k)

    def _on_flags(self) -> None:
        self.state.flags.show_plates = self.chk_plates.isChecked()
        self.state.flags.show_labels = self.chk_labels.isChecked()
        self.flags_changed.emit()

    def set_available_devices(self, devices: List[Tuple[str, str, str]]) -> None:
        self._all_devices = devices or []
        self._populate_device_list()

    def _populate_device_list(self) -> None:
        show06 = self.chk_filter_06.isChecked()
        show07 = self.chk_filter_07.isChecked()
        show08 = self.chk_filter_08.isChecked()
        selected_id = self.state.selected_device_id or ""
        self.device_list.blockSignals(True)
        self.device_list.clear()
        for name, axf_id, dev_type in self._all_devices:
            if dev_type == "06" and not show06:
                continue
            if dev_type == "07" and not show07:
                continue
            if dev_type == "08" and not show08:
                continue
            display = f"{name} ({axf_id})"
            item = QtWidgets.QListWidgetItem(display)
            item.setData(QtCore.Qt.UserRole, (name, axf_id, dev_type))
            item.setData(QtCore.Qt.UserRole + 1, False)
            self.device_list.addItem(item)
            if selected_id and axf_id == selected_id:
                self.device_list.setCurrentItem(item)
        self.device_list.blockSignals(False)
        self.device_list.setEnabled(self.rb_layout_single.isChecked())

    def update_active_devices(self, active_device_ids: set) -> None:
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item is None:
                continue
            try:
                name, axf_id, dev_type = item.data(QtCore.Qt.UserRole)
                is_active = any(axf_id in active_id or active_id in axf_id for active_id in active_device_ids)
                item.setData(QtCore.Qt.UserRole + 1, is_active)
                display = f"{name} ({axf_id})"
                item.setText(display)
                item.setForeground(QtGui.QColor(255, 255, 255))
            except Exception:
                continue
        self.device_list.viewport().update()

    def _on_filter_changed(self) -> None:
        self._populate_device_list()

    def _on_layout_changed(self, _checked: bool) -> None:
        if self.rb_layout_mound.isChecked():
            self.state.display_mode = "mound"
            self.state.selected_device_id = None
            self.state.selected_device_type = None
        else:
            self.state.display_mode = "single"
        self._populate_device_list()
        self.config_changed.emit()

    def _on_device_selected(self, current: Optional[QtWidgets.QListWidgetItem], _previous: Optional[QtWidgets.QListWidgetItem]) -> None:
        if current is None:
            return
        try:
            name, axf_id, dev_type = current.data(QtCore.Qt.UserRole)
        except Exception:
            return
        self.state.selected_device_id = str(axf_id)
        self.state.selected_device_type = str(dev_type)
        self.state.selected_device_name = str(name)
        self.state.display_mode = "single"
        self.config_changed.emit()

    def _on_tab_changed(self, idx: int) -> None:
        try:
            if idx == getattr(self, "_config_tab_index", -1):
                self.refresh_devices_requested.emit()
        except Exception:
            pass

    def _emit_start(self) -> None:
        payload = {
            "capture_name": "",
            "capture_configuration": self.capture_type.currentText() or "pitch",
            "group_id": self.group_edit.text().strip(),
            "athlete_id": self.athlete_edit.text().strip(),
        }
        self.start_capture_requested.emit(payload)

    def _emit_stop(self) -> None:
        payload = {"group_id": self.group_edit.text().strip()}
        self.stop_capture_requested.emit(payload)

    def _emit_tare(self) -> None:
        gid = self.group_edit.text().strip()
        self.tare_requested.emit(gid)


