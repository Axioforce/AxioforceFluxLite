from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ... import config
from ...core import sync_logic as data_sync
from ..state import ViewState
from .live_testing_panel import LiveTestingPanel
from .temperature_testing_panel import TemperatureTestingPanel
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
    sampling_rate_changed = QtCore.Signal(int)
    emission_rate_changed = QtCore.Signal(int)
    ui_tick_hz_changed = QtCore.Signal(int)
    autoscale_damp_toggled = QtCore.Signal(bool)
    autoscale_damp_n_changed = QtCore.Signal(int)
    live_testing_tab_selected = QtCore.Signal()
    # Backend configuration quick actions (Config tab, right-hand pane)
    backend_model_bypass_changed = QtCore.Signal(bool)
    backend_capture_detail_changed = QtCore.Signal(str)
    backend_temperature_apply_requested = QtCore.Signal(object)  # dict: { use_temperature_correction, slopes, room_temperature_f }
    # Data sync (OneDrive) actions
    data_sync_requested = QtCore.Signal(str)

    def __init__(self, state: ViewState, controller: object = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.controller = controller

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

        # Data rate controls
        rate_row = QtWidgets.QWidget()
        rate_layout = QtWidgets.QHBoxLayout(rate_row)
        rate_layout.setContentsMargins(0, 0, 0, 0)
        rate_layout.setSpacing(10)
        rate_layout.addWidget(QtWidgets.QLabel("Sampling Hz:"))
        self.sampling_spin = QtWidgets.QSpinBox()
        self.sampling_spin.setRange(-1, 1200)
        self.sampling_spin.setValue(1000)
        _fixh(self.sampling_spin)
        self.sampling_spin.setMaximumWidth(80)
        rate_layout.addWidget(self.sampling_spin)
        self.apply_sampling_btn = QtWidgets.QPushButton("Apply")
        _fix_btn(self.apply_sampling_btn, 70)
        rate_layout.addWidget(self.apply_sampling_btn)
        rate_layout.addWidget(QtWidgets.QLabel("Emission Hz:"))
        self.emission_spin = QtWidgets.QSpinBox()
        self.emission_spin.setRange(-1, 500)
        self.emission_spin.setValue(250)
        _fixh(self.emission_spin)
        self.emission_spin.setMaximumWidth(80)
        rate_layout.addWidget(self.emission_spin)
        self.apply_emission_btn = QtWidgets.QPushButton("Apply")
        _fix_btn(self.apply_emission_btn, 70)
        rate_layout.addWidget(self.apply_emission_btn)
        rate_layout.addStretch(1)
        conn_layout.addWidget(rate_row, conn_row, 0, 1, 4)
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

        # UI throttling controls
        ui_row = QtWidgets.QWidget()
        ui_layout = QtWidgets.QHBoxLayout(ui_row)
        ui_layout.setContentsMargins(0, 0, 0, 0)
        ui_layout.setSpacing(10)
        ui_layout.addWidget(QtWidgets.QLabel("UI Tick Hz:"))
        self.ui_tick_spin = QtWidgets.QSpinBox()
        self.ui_tick_spin.setRange(10, 240)
        self.ui_tick_spin.setValue(int(getattr(config, "UI_TICK_HZ", 60)))
        _fixh(self.ui_tick_spin)
        self.ui_tick_spin.setMaximumWidth(80)
        ui_layout.addWidget(self.ui_tick_spin)
        self.apply_ui_tick_btn = QtWidgets.QPushButton("Apply")
        _fix_btn(self.apply_ui_tick_btn, 70)
        ui_layout.addWidget(self.apply_ui_tick_btn)
        ui_layout.addStretch(1)
        iface_layout.addWidget(ui_row, iface_row, 0, 1, 3)
        iface_row += 1

        # Autoscale damping controls
        damp_row = QtWidgets.QWidget()
        damp_layout = QtWidgets.QHBoxLayout(damp_row)
        damp_layout.setContentsMargins(0, 0, 0, 0)
        damp_layout.setSpacing(10)
        self.chk_autoscale_damp = QtWidgets.QCheckBox("Autoscale Damping")
        self.chk_autoscale_damp.setChecked(bool(getattr(config, "PLOT_AUTOSCALE_DAMP_ENABLED", True)))
        damp_layout.addWidget(self.chk_autoscale_damp)
        damp_layout.addWidget(QtWidgets.QLabel("Every N frames:"))
        self.autoscale_every_spin = QtWidgets.QSpinBox()
        self.autoscale_every_spin.setRange(1, 20)
        self.autoscale_every_spin.setValue(int(getattr(config, "PLOT_AUTOSCALE_DAMP_EVERY_N", 2)))
        _fixh(self.autoscale_every_spin)
        self.autoscale_every_spin.setMaximumWidth(80)
        damp_layout.addWidget(self.autoscale_every_spin)
        damp_layout.addStretch(1)
        iface_layout.addWidget(damp_row, iface_row, 0, 1, 3)
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
        cfg_layout.setColumnStretch(0, 2)
        cfg_layout.setColumnStretch(1, 1)

        # Left column: layout mode, device filters, and device list
        cfg_left = QtWidgets.QWidget()
        cfg_left_layout = QtWidgets.QVBoxLayout(cfg_left)
        cfg_left_layout.setContentsMargins(0, 0, 0, 0)
        cfg_left_layout.setSpacing(6)

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
        cfg_left_layout.addWidget(layout_row)
        self.state.display_mode = "single"

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
        cfg_left_layout.addWidget(filter_row)

        # (moved) Refresh Devices button now lives next to the always-visible Tare button
        self.device_list = QtWidgets.QListWidget()
        self.device_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.device_list.setItemDelegate(DeviceListDelegate())
        cfg_left_layout.addWidget(self.device_list, 1)

        cfg_layout.addWidget(cfg_left, 0, 0, 1, 1)

        # Right column: Backend configuration quick settings
        backend_box = QtWidgets.QGroupBox("Backend Config")
        backend_layout = QtWidgets.QGridLayout(backend_box)
        backend_layout.setVerticalSpacing(6)
        row = 0

        # Bypass models
        self.chk_bypass_models = QtWidgets.QCheckBox("Bypass ML Models")
        backend_layout.addWidget(self.chk_bypass_models, row, 0, 1, 2)
        row += 1

        # Capture detail
        backend_layout.addWidget(QtWidgets.QLabel("Capture Detail:"), row, 0)
        self.capture_detail_combo = QtWidgets.QComboBox()
        self.capture_detail_combo.addItems(["basic", "all", "allSums", "allTemp"])
        self.capture_detail_combo.setCurrentText("allTemp")
        backend_layout.addWidget(self.capture_detail_combo, row, 1)
        row += 1

        # Temperature correction
        temp_group = QtWidgets.QGroupBox("Temperature Correction")
        temp_layout = QtWidgets.QGridLayout(temp_group)
        temp_layout.setVerticalSpacing(4)
        tr = 0
        self.chk_use_temp_corr = QtWidgets.QCheckBox("Enable Temperature Correction")
        temp_layout.addWidget(self.chk_use_temp_corr, tr, 0, 1, 2)
        tr += 1
        temp_layout.addWidget(QtWidgets.QLabel("Slope X:"), tr, 0)
        self.spin_temp_x = QtWidgets.QDoubleSpinBox()
        self.spin_temp_x.setRange(-1000.0, 1000.0)
        self.spin_temp_x.setDecimals(3)
        self.spin_temp_x.setSingleStep(0.1)
        self.spin_temp_x.setValue(3.0)
        temp_layout.addWidget(self.spin_temp_x, tr, 1)
        tr += 1
        temp_layout.addWidget(QtWidgets.QLabel("Slope Y:"), tr, 0)
        self.spin_temp_y = QtWidgets.QDoubleSpinBox()
        self.spin_temp_y.setRange(-1000.0, 1000.0)
        self.spin_temp_y.setDecimals(3)
        self.spin_temp_y.setSingleStep(0.1)
        self.spin_temp_y.setValue(3.0)
        temp_layout.addWidget(self.spin_temp_y, tr, 1)
        tr += 1
        temp_layout.addWidget(QtWidgets.QLabel("Slope Z:"), tr, 0)
        self.spin_temp_z = QtWidgets.QDoubleSpinBox()
        self.spin_temp_z.setRange(-1000.0, 1000.0)
        self.spin_temp_z.setDecimals(3)
        self.spin_temp_z.setSingleStep(0.1)
        self.spin_temp_z.setValue(3.0)
        temp_layout.addWidget(self.spin_temp_z, tr, 1)
        tr += 1
        temp_layout.addWidget(QtWidgets.QLabel("Room Temp (°F):"), tr, 0)
        self.spin_room_temp = QtWidgets.QDoubleSpinBox()
        self.spin_room_temp.setRange(-100.0, 300.0)
        self.spin_room_temp.setDecimals(1)
        self.spin_room_temp.setSingleStep(0.5)
        self.spin_room_temp.setValue(76.0)
        temp_layout.addWidget(self.spin_room_temp, tr, 1)
        tr += 1
        self.btn_apply_temp_corr = QtWidgets.QPushButton("Apply Temperature Correction")
        _fix_btn(self.btn_apply_temp_corr, 200)
        temp_layout.addWidget(self.btn_apply_temp_corr, tr, 0, 1, 2)

        backend_layout.addWidget(temp_group, row, 0, 1, 2)
        row += 1

        # Sampling rate quick control
        sampling_row = QtWidgets.QWidget()
        sampling_layout = QtWidgets.QHBoxLayout(sampling_row)
        sampling_layout.setContentsMargins(0, 0, 0, 0)
        sampling_layout.setSpacing(6)
        sampling_layout.addWidget(QtWidgets.QLabel("Sampling Hz:"))
        self.backend_sampling_spin = QtWidgets.QSpinBox()
        self.backend_sampling_spin.setRange(-1, 1200)
        self.backend_sampling_spin.setValue(1000)
        _fixh(self.backend_sampling_spin)
        self.backend_sampling_spin.setMaximumWidth(80)
        sampling_layout.addWidget(self.backend_sampling_spin)
        self.backend_sampling_apply_btn = QtWidgets.QPushButton("Apply")
        _fix_btn(self.backend_sampling_apply_btn, 70)
        sampling_layout.addWidget(self.backend_sampling_apply_btn)
        sampling_layout.addStretch(1)
        backend_layout.addWidget(sampling_row, row, 0, 1, 2)
        row += 1

        # OneDrive data sync controls
        onedrive_group = QtWidgets.QGroupBox("OneDrive Data Sync")
        od_layout = QtWidgets.QGridLayout(onedrive_group)
        od_layout.setVerticalSpacing(4)
        r = 0
        od_layout.addWidget(QtWidgets.QLabel("OneDrive data root:"), r, 0)
        self.onedrive_path_edit = QtWidgets.QLineEdit()
        try:
            self.onedrive_path_edit.setText(data_sync.get_onedrive_data_root())
        except Exception:
            pass
        self.onedrive_path_edit.setPlaceholderText("e.g. C:/Users/You/OneDrive/AxioforceData")
        od_layout.addWidget(self.onedrive_path_edit, r, 1)
        r += 1
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_onedrive_browse = QtWidgets.QPushButton("Browse...")
        self.btn_onedrive_sync = QtWidgets.QPushButton("Sync Data")
        _fix_btn(self.btn_onedrive_browse, 90)
        _fix_btn(self.btn_onedrive_sync, 100)
        btn_row.addWidget(self.btn_onedrive_browse)
        btn_row.addWidget(self.btn_onedrive_sync)
        btn_row.addStretch(1)
        od_layout.addLayout(btn_row, r, 0, 1, 2)

        backend_layout.addWidget(onedrive_group, row, 0, 1, 2)
        row += 1

        backend_layout.setRowStretch(row, 1)
        cfg_layout.addWidget(backend_box, 0, 1, 1, 1)

        self._config_tab_index = tabs.addTab(config_tab, "Config")
        tabs.addTab(connection_tab, "Connection")
        tabs.addTab(interface_tab, "Interface")
        # Ensure Demo tab (Group/Athlete inputs) remains parented to avoid widget deletion
        tabs.addTab(demo_tab, "Demo")

        # Live Testing tab
        # Pass live test controller if available
        live_ctrl = getattr(self.controller, "live_test", None) if self.controller else None
        self.live_testing_panel = LiveTestingPanel(self.state, live_ctrl)
        
        live_scroll = QtWidgets.QScrollArea()
        live_scroll.setWidget(self.live_testing_panel)
        live_scroll.setWidgetResizable(True)
        live_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        
        self._live_tab_index = tabs.addTab(live_scroll, "Live Testing")
        
        # Temperature Testing tab (to the right of Live Testing)
        temp_ctrl = getattr(self.controller, "temp_test", None) if self.controller else None
        self.temperature_testing_panel = TemperatureTestingPanel(temp_ctrl)
        self.temperature_testing_panel.setObjectName("temperature_testing_panel")
        
        temp_scroll = QtWidgets.QScrollArea()
        temp_scroll.setWidget(self.temperature_testing_panel)
        temp_scroll.setWidgetResizable(True)
        temp_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        
        self._temp_tab_index = tabs.addTab(temp_scroll, "Temperature Testing")
        
        # Ensure tabs consume available vertical space (MainWindow controls overall 3:2 split)
        try:
            tabs.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            # Size policies for panels are now handled by scroll areas' widgetResizable=True
        except Exception:
            pass

        # NOTE: Tare/Refresh quick-actions moved to Plate View overlay buttons (WorldCanvas).

        self._all_devices: List[Tuple[str, str, str]] = []

        root.addWidget(tabs)

        self.btn_connect.clicked.connect(self._emit_connect)
        self.btn_disconnect.clicked.connect(self.disconnect_requested.emit)
        self.btn_start.clicked.connect(self._emit_start)
        self.btn_stop.clicked.connect(self._emit_stop)
        self.scale_slider.valueChanged.connect(self._on_scale)
        self.chk_plates.stateChanged.connect(self._on_flags)
        self.chk_labels.stateChanged.connect(self._on_flags)
        self.chk_filter_06.stateChanged.connect(self._on_filter_changed)
        self.chk_filter_07.stateChanged.connect(self._on_filter_changed)
        self.chk_filter_08.stateChanged.connect(self._on_filter_changed)
        self.rb_layout_mound.toggled.connect(self._on_layout_changed)
        self.rb_layout_single.toggled.connect(self._on_layout_changed)
        self.device_list.currentItemChanged.connect(self._on_device_selected)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.btn_onedrive_browse.clicked.connect(self._on_browse_onedrive)
        self.btn_onedrive_sync.clicked.connect(self._emit_data_sync)
        # Gate backend updates behind Apply or Enter
        def _emit_sampling():
            try:
                self.sampling_rate_changed.emit(int(self.sampling_spin.value()))
            except Exception:
                pass
        def _emit_emission():
            try:
                self.emission_rate_changed.emit(int(self.emission_spin.value()))
            except Exception:
                pass
        self.apply_sampling_btn.clicked.connect(_emit_sampling)
        # Mirror backend sampling Apply in Config tab quick controls
        self.backend_sampling_apply_btn.clicked.connect(
            lambda: self.sampling_rate_changed.emit(int(self.backend_sampling_spin.value()))
        )
        self.apply_emission_btn.clicked.connect(_emit_emission)
        self.sampling_spin.lineEdit().returnPressed.connect(_emit_sampling)
        self.emission_spin.lineEdit().returnPressed.connect(_emit_emission)
        self.backend_sampling_spin.lineEdit().returnPressed.connect(
            lambda: self.sampling_rate_changed.emit(int(self.backend_sampling_spin.value()))
        )
        # Interface: ui tick & autoscale damping
        self.apply_ui_tick_btn.clicked.connect(lambda: self.ui_tick_hz_changed.emit(int(self.ui_tick_spin.value())))
        self.ui_tick_spin.lineEdit().returnPressed.connect(lambda: self.ui_tick_hz_changed.emit(int(self.ui_tick_spin.value())))
        self.chk_autoscale_damp.toggled.connect(lambda v: self.autoscale_damp_toggled.emit(bool(v)))
        self.autoscale_every_spin.valueChanged.connect(lambda v: self.autoscale_damp_n_changed.emit(int(v)))
        # Backend Config quick actions
        self.chk_bypass_models.toggled.connect(lambda v: self.backend_model_bypass_changed.emit(bool(v)))
        self.capture_detail_combo.currentTextChanged.connect(
            lambda text: self.backend_capture_detail_changed.emit(str(text))
        )
        self.btn_apply_temp_corr.clicked.connect(self._emit_backend_temp_corr)

        # NOTE: We intentionally do NOT poll the backend for device lists.
        # Device connect/disconnect is pushed via websocket events, and we also
        # refresh on explicit user action (Refresh Devices button) or when the
        # Config tab is selected.

    def _emit_backend_temp_corr(self) -> None:
        """Emit a single payload for backend temperature correction settings."""
        try:
            payload = {
                "use_temperature_correction": bool(self.chk_use_temp_corr.isChecked()),
                "slopes": {
                    "x": float(self.spin_temp_x.value()),
                    "y": float(self.spin_temp_y.value()),
                    "z": float(self.spin_temp_z.value()),
                },
                "room_temperature_f": float(self.spin_room_temp.value()),
            }
            self.backend_temperature_apply_requested.emit(payload)
        except Exception:
            # Never crash on UI-side conversion issues
            pass

    def set_backend_rates(self, sampling_hz: int, emission_hz: int) -> None:
        try:
            self.sampling_spin.blockSignals(True)
            self.emission_spin.blockSignals(True)
            if sampling_hz:
                self.sampling_spin.setValue(int(sampling_hz))
            if emission_hz:
                self.emission_spin.setValue(int(emission_hz))
        except Exception:
            pass
        finally:
            try:
                self.sampling_spin.blockSignals(False)
                self.emission_spin.blockSignals(False)
            except Exception:
                pass

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
            if dev_type == "11" and not show07:  # 11 uses same filter as 07 (identical dimensions)
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
            if idx == getattr(self, "_live_tab_index", -1):
                self.live_testing_tab_selected.emit()
            # No-op for Temperature Testing tab select for now
        except Exception:
            pass

    def _on_browse_onedrive(self) -> None:
        """Open a folder picker to select the OneDrive data root."""
        try:
            dlg = QtWidgets.QFileDialog(self)
            dlg.setFileMode(QtWidgets.QFileDialog.Directory)
            dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
            if dlg.exec():
                dirs = dlg.selectedFiles()
                if dirs:
                    path = dirs[0]
                    self.onedrive_path_edit.setText(path)
        except Exception:
            pass

    def _emit_data_sync(self) -> None:
        """Emit a data sync request with the configured OneDrive path."""
        try:
            path = self.onedrive_path_edit.text().strip()
        except Exception:
            path = ""
        if not path:
            return
        self.data_sync_requested.emit(path)

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
        try:
            gid = self.group_edit.text().strip()
        except Exception:
            gid = ""
        self.tare_requested.emit(gid)


