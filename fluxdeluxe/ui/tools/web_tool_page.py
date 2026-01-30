from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtWidgets


class WebToolPage(QtWidgets.QWidget):
    """Host a remote web tool (embedded if QtWebEngine available)."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._url: str = ""
        self._webview = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        self.btn_home = QtWidgets.QPushButton("Back to Tools")
        header.addWidget(self.btn_home)

        header.addStretch(1)

        self.lbl_title = QtWidgets.QLabel("")
        self.lbl_title.setStyleSheet("font-weight: 700;")
        header.addWidget(self.lbl_title)

        header.addStretch(1)

        self.btn_open_browser = QtWidgets.QPushButton("Open in Browser")
        self.btn_open_browser.clicked.connect(self._open_in_browser)
        header.addWidget(self.btn_open_browser)

        outer.addLayout(header)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self._content = QtWidgets.QStackedWidget()
        outer.addWidget(self._content, 1)

        self._placeholder = QtWidgets.QLabel("")
        self._placeholder.setWordWrap(True)
        self._content.addWidget(self._placeholder)

        self._show_placeholder("No web tool selected.")

    def set_tool(self, *, title: str, url: str) -> None:
        self.lbl_title.setText(title or "")
        self._url = (url or "").strip()

        if not self._url:
            self._show_placeholder("No URL provided for this tool.")
            return

        # IMPORTANT:
        # Initializing Qt WebEngine can hard-crash the process on some machines/configurations.
        # Default to opening in the external browser unless explicitly enabled.
        enable_embed = os.environ.get("FLUXDELUXE_ENABLE_QTWEBENGINE", "").strip().lower() in {"1", "true", "yes"}
        if not enable_embed:
            self.status.setText("Opening in browser (embedded web view disabled).")
            self._show_placeholder(
                "This tool opens in your browser.\n\n"
                "To try embedding (may crash on some machines), set:\n"
                "  FLUXDELUXE_ENABLE_QTWEBENGINE=1\n\n"
                f"URL:\n{self._url}\n"
            )
            self._open_in_browser()
            return

        # Lazy-init embedded web view only when needed (opt-in).
        if self._webview is None:
            try:
                from PySide6 import QtWebEngineWidgets  # type: ignore

                self._webview = QtWebEngineWidgets.QWebEngineView()
                self._content.addWidget(self._webview)
            except Exception:
                self._webview = None

        if self._webview is None:
            self.status.setText("Qt WebEngine not available; opening in browser.")
            self._show_placeholder(
                "Qt WebEngine (PySide6.QtWebEngineWidgets) is not available.\n\n"
                f"Opening in your browser instead:\n{self._url}\n"
            )
            self._open_in_browser()
            return

        self.status.setText(self._url)
        self._content.setCurrentWidget(self._webview)
        self._webview.setUrl(QtCore.QUrl(self._url))

    def _open_in_browser(self) -> None:
        if self._url:
            QtCore.QDesktopServices.openUrl(QtCore.QUrl(self._url))

    def _show_placeholder(self, text: str) -> None:
        self._content.setCurrentWidget(self._placeholder)
        self._placeholder.setText(text)

