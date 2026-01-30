from __future__ import annotations

from pathlib import Path

from PySide6 import QtGui, QtWidgets

from .fluxlite_page import FluxLitePage


class MainWindow(QtWidgets.QMainWindow):
    """Standalone FluxLite window (no FluxDeluxe launcher/tool switching)."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FluxLite")

        # Window icon (matches app icon set in tools/FluxLite/src/main.py)
        try:
            icon_path = Path(__file__).resolve().parent / "assets" / "icons" / "fluxliteicon.svg"
            icon = QtGui.QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)
        except Exception:
            pass

        self.page = FluxLitePage(self)
        self.setCentralWidget(self.page)

        # Compatibility: some code expects `win.controller`.
        self.controller = self.page.controller

        # Basic status bar
        self.status_label = QtWidgets.QLabel("Disconnected")
        self.statusBar().addPermanentWidget(self.status_label)
        try:
            self.page.connection_status_changed.connect(self.status_label.setText)
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        try:
            self.page.shutdown()
        except Exception:
            pass
        super().closeEvent(event)
