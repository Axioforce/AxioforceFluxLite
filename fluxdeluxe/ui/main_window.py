from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6 import QtWidgets, QtGui, QtCore

from .dialogs.backend_log_dialog import BackendLogDialog
from .tools.launcher_page import ToolLauncherPage
from .tools.tool_registry import ToolSpec, default_tools
from .tools.metrics_editor_page import MetricsEditorPage
from .tools.web_tool_page import WebToolPage


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self._backend_ready = False
        self._backend_probe_attempts = 0
        self.setWindowTitle("FluxDeluxe")

        # Window icon (matches app icon set in fluxdeluxe/main.py)
        try:
            icon_path = Path(__file__).resolve().parent / "assets" / "icons" / "fluxliteicon.svg"
            icon = QtGui.QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)
        except Exception:
            pass

        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Tool pages (high-level app switcher)
        self.tool_stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.tool_stack)

        # Tool registry (static for now)
        self._tools: list[ToolSpec] = list(default_tools())
        self._tool_by_id: dict[str, ToolSpec] = {t.tool_id: t for t in self._tools}

        # --- Home launcher (tool grid) ---
        self.launcher_page = ToolLauncherPage(self._tools)
        self.launcher_page.tool_selected.connect(self.open_tool)
        self.tool_stack.addWidget(self.launcher_page)

        # --- FluxLite tool page (lazy loaded) ---
        self._fluxlite_page: Optional[QtWidgets.QWidget] = None
        # --- Metrics Editor tool page (lazy loaded; does NOT require backend) ---
        self._metrics_editor_page: Optional[MetricsEditorPage] = None

        # --- Web tool host page (hosted Streamlit, etc) ---
        self.web_tool_page = WebToolPage()
        self.web_tool_page.btn_home.clicked.connect(self.show_home)
        self.tool_stack.addWidget(self.web_tool_page)

        self.setCentralWidget(central)

        # Bottom status bar
        self.btn_tools = QtWidgets.QPushButton("Tools")
        self.btn_tools.clicked.connect(self.show_home)
        self.statusBar().addWidget(self.btn_tools)

        self.tool_title = QtWidgets.QLabel("")
        self.tool_title.setStyleSheet("color: #BDBDBD;")
        self.statusBar().addWidget(self.tool_title)

        self.status_label = QtWidgets.QLabel("")
        self.statusBar().addPermanentWidget(self.status_label)

        # Backend logs button
        self.btn_backend_logs = QtWidgets.QPushButton("Backend Logs")
        self.btn_backend_logs.clicked.connect(self._show_backend_logs)
        self.statusBar().addPermanentWidget(self.btn_backend_logs)

        # Start reading backend logs (and mark backend ready when detected)
        self._setup_backend_log_reader()

        # Always start at Home (tool grid)
        self.show_home()

    def show_home(self) -> None:
        try:
            self.tool_stack.setCurrentWidget(self.launcher_page)
            self.tool_title.setText("Home")
            self.status_label.setText("")
        except Exception:
            pass

    def is_backend_ready(self) -> bool:
        return bool(self._backend_ready)

    def _ensure_fluxlite_page(self) -> QtWidgets.QWidget:
        if self._fluxlite_page is not None:
            return self._fluxlite_page

        try:
            from tools.FluxLite.src.ui.fluxlite_page import FluxLitePage  # type: ignore

            page = FluxLitePage()
            self._fluxlite_page = page
            self.tool_stack.addWidget(page)

            # Mirror FluxLite connection status into the host status bar.
            try:
                page.controller.hardware.connection_status_changed.connect(self.status_label.setText)
            except Exception:
                pass

            # Ensure the tool can clean up on close.
            try:
                self.destroyed.connect(page.shutdown)  # type: ignore[attr-defined]
            except Exception:
                pass

            return page
        except Exception as exc:
            # Missing or broken FluxLite tool: show a placeholder instead of crashing the host.
            ph = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(ph)
            layout.setContentsMargins(18, 18, 18, 18)
            msg = QtWidgets.QLabel(
                "FluxLite tool failed to load.\n\n"
                f"{exc}\n\n"
                "Check that `tools/FluxLite` is present and importable."
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            layout.addStretch(1)
            self._fluxlite_page = ph
            self.tool_stack.addWidget(ph)
            return ph

    def _ensure_metrics_editor_page(self) -> MetricsEditorPage:
        if self._metrics_editor_page is not None:
            return self._metrics_editor_page

        page = MetricsEditorPage()
        self._metrics_editor_page = page
        self.tool_stack.addWidget(page)

        # Ensure the tool can clean up on close.
        try:
            self.destroyed.connect(page.shutdown)  # type: ignore[attr-defined]
        except Exception:
            pass

        return page

    def open_tool(self, tool_id: str) -> None:
        # Avoid UI-triggered races: do not open tools until backend is detected.
        if not self._backend_ready and str(tool_id or "").strip() != "metrics_editor":
            try:
                self.status_label.setText("Starting backend…")
            except Exception:
                pass
            return

        tool_id = str(tool_id or "").strip()
        spec = self._tool_by_id.get(tool_id)
        if spec is None:
            return

        if spec.kind == "qt" and spec.tool_id == "fluxlite":
            page = self._ensure_fluxlite_page()
            self.tool_stack.setCurrentWidget(page)
            self.tool_title.setText(spec.name)
            return

        if spec.kind == "qt" and spec.tool_id == "metrics_editor":
            page = self._ensure_metrics_editor_page()
            try:
                page.ensure_started()
            except Exception:
                pass
            self.tool_stack.setCurrentWidget(page)
            self.tool_title.setText(spec.name)
            return

        if spec.kind == "web":
            self.tool_stack.setCurrentWidget(self.web_tool_page)
            self.tool_title.setText(spec.name)
            try:
                self.web_tool_page.set_tool(title=spec.name, url=str(spec.url or ""))
            except Exception:
                pass
            return

    def _setup_backend_log_reader(self) -> None:
        """Set up the backend log dialog to read from the DynamoDeluxe process."""
        try:
            main_module = self._resolve_main_module()
            process = getattr(main_module, "get_dynamo_process", lambda: None)()

            if process is not None:
                dialog = BackendLogDialog.get_instance(self)
                dialog.start_reading(process)
                self._backend_ready = True
                try:
                    self.status_label.setText("Backend ready")
                except Exception:
                    pass
                return

            # Backend handle not available yet: retry a few times.
            self._backend_probe_attempts += 1
            if self._backend_probe_attempts <= 25:
                try:
                    self.status_label.setText("Starting backend…")
                except Exception:
                    pass
                QtCore.QTimer.singleShot(200, self._setup_backend_log_reader)
            else:
                self._logger.warning("Backend process handle not found after retries.")
                try:
                    self.status_label.setText("Backend not detected")
                except Exception:
                    pass
        except Exception:
            self._logger.exception("Error setting up backend log reader")

    def _resolve_main_module(self):
        """Resolve the running main module (works with `-m` and with normal imports)."""
        # If launched via `python -m FluxDeluxe.main`, the process is stored on __main__.
        mod = sys.modules.get("__main__")
        if mod is not None and hasattr(mod, "get_dynamo_process"):
            return mod

        # Otherwise (or after our aliasing), this should work.
        import FluxDeluxe.main as main_module  # type: ignore

        return main_module

    def _show_backend_logs(self) -> None:
        """Show the backend log dialog."""
        try:
            dialog = BackendLogDialog.get_instance(self)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        # Stop backend log reader
        try:
            dialog = BackendLogDialog.get_instance(self)
            dialog.stop_reading()
        except Exception:
            pass

        # Give the current tool a chance to shut down.
        try:
            page = self._fluxlite_page
            if page is not None and hasattr(page, "shutdown"):
                page.shutdown()  # type: ignore[misc]
        except Exception:
            pass
        try:
            if self._metrics_editor_page is not None:
                self._metrics_editor_page.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

