from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtWidgets, QtGui

from .tools.launcher_page import ToolLauncherPage
from .tools.tool_registry import ToolSpec, default_tools
from .tools.web_tool_page import WebToolPage


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
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

        # Always start at Home (tool grid)
        self.show_home()

    def show_home(self) -> None:
        try:
            self.tool_stack.setCurrentWidget(self.launcher_page)
            self.tool_title.setText("Home")
            self.status_label.setText("")
        except Exception:
            pass

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

    def open_tool(self, tool_id: str) -> None:
        tool_id = str(tool_id or "").strip()
        spec = self._tool_by_id.get(tool_id)
        if spec is None:
            return

        if spec.kind == "qt" and spec.tool_id == "fluxlite":
            page = self._ensure_fluxlite_page()
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

    def closeEvent(self, event) -> None:
        # Give the current tool a chance to shut down.
        try:
            page = self._fluxlite_page
            if page is not None and hasattr(page, "shutdown"):
                page.shutdown()  # type: ignore[misc]
        except Exception:
            pass
        super().closeEvent(event)

