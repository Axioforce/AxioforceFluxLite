from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

import requests
from PySide6 import QtCore, QtWidgets

from ... import config


@dataclass(frozen=True)
class StreamlitEndpoint:
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


class MetricsEditorPage(QtWidgets.QWidget):
    """
    A small "tool page" that can embed a Streamlit app.

    - Tries to embed using QtWebEngine if available.
    - Launches Streamlit as a subprocess (optional; requires streamlit installed).
    - Falls back to an instructional placeholder if embedding isn't possible.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._proc: Optional[subprocess.Popen] = None
        self._endpoint = StreamlitEndpoint(
            host=getattr(config, "METRICS_EDITOR_STREAMLIT_HOST", "127.0.0.1"),
            port=int(getattr(config, "METRICS_EDITOR_STREAMLIT_PORT", 8503)),
        )
        self._entrypoint = str(getattr(config, "METRICS_EDITOR_STREAMLIT_ENTRYPOINT", "") or "").strip()

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self._poll_ready)

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        title = QtWidgets.QLabel("Metrics Editor")
        title.setStyleSheet("font-weight: 700;")
        header.addWidget(title)

        header.addStretch(1)

        self.btn_open_browser = QtWidgets.QPushButton("Open in Browser")
        self.btn_open_browser.clicked.connect(self._open_in_browser)
        header.addWidget(self.btn_open_browser)

        self.btn_restart = QtWidgets.QPushButton("Restart")
        self.btn_restart.clicked.connect(self.restart)
        header.addWidget(self.btn_restart)

        layout.addLayout(header)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._content = QtWidgets.QStackedWidget()
        layout.addWidget(self._content, 1)

        # Placeholder content (always available)
        self._placeholder = QtWidgets.QWidget()
        ph = QtWidgets.QVBoxLayout(self._placeholder)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(8)

        self._placeholder_text = QtWidgets.QLabel("")
        self._placeholder_text.setWordWrap(True)
        ph.addWidget(self._placeholder_text)
        ph.addStretch(1)

        self._content.addWidget(self._placeholder)

        # WebEngine content (optional)
        self._webview = None
        try:
            from PySide6 import QtWebEngineWidgets  # type: ignore

            self._webview = QtWebEngineWidgets.QWebEngineView()
            self._content.addWidget(self._webview)
        except Exception:
            self._webview = None

        self._show_placeholder(
            "Select this tool to embed a Streamlit app.\n\n"
            "To configure it, set environment variable:\n"
            "  METRICS_EDITOR_STREAMLIT_ENTRYPOINT=<path to your streamlit app.py>\n\n"
            f"Default URL target: {self._endpoint.url}\n"
        )

    def ensure_started(self) -> None:
        """
        Start Streamlit (if configured) and load the page if embedding is possible.
        Safe to call multiple times.
        """
        if not self._entrypoint:
            self.status.setText("Metrics Editor is not configured (no entrypoint set).")
            self._show_placeholder(
                "No Streamlit entrypoint configured.\n\n"
                "Set environment variable:\n"
                "  METRICS_EDITOR_STREAMLIT_ENTRYPOINT=<path to your streamlit app.py>\n\n"
                "Then restart the app (or click Restart here)."
            )
            return

        if self._proc is None or self._proc.poll() is not None:
            ok = self._start_streamlit()
            if not ok:
                return

        # Try to load once it's ready (poll timer handles readiness)
        self.status.setText(f"Starting Streamlit… {self._endpoint.url}")
        self._poll_timer.start()

    def restart(self) -> None:
        self.shutdown()
        # Re-read env in case user changed it without restarting python
        self._entrypoint = os.environ.get("METRICS_EDITOR_STREAMLIT_ENTRYPOINT", self._entrypoint).strip()
        self.ensure_started()

    def shutdown(self) -> None:
        self._poll_timer.stop()
        if self._proc is None:
            return
        try:
            self._proc.terminate()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=2.0)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def _start_streamlit(self) -> bool:
        # Start: python -m streamlit run <entrypoint> --server.address ... --server.port ...
        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            self._entrypoint,
            "--server.headless",
            "true",
            "--server.address",
            self._endpoint.host,
            "--server.port",
            str(self._endpoint.port),
            "--browser.gatherUsageStats",
            "false",
        ]

        try:
            self._proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=os.path.dirname(self._entrypoint) or None,
                text=True,
            )
        except Exception as exc:
            self.status.setText(f"Failed to launch Streamlit: {exc}")
            self._show_placeholder(
                "Could not launch Streamlit.\n\n"
                "Make sure Streamlit is installed in this environment:\n"
                "  pip install streamlit\n"
            )
            return False

        self.status.setText(f"Launching Streamlit… {self._endpoint.url}")
        return True

    def _poll_ready(self) -> None:
        url = self._endpoint.url

        # If process died, show output hint.
        if self._proc is not None and self._proc.poll() is not None:
            self._poll_timer.stop()
            self.status.setText("Streamlit exited unexpectedly.")
            self._show_placeholder(self._read_recent_output() or "Streamlit exited unexpectedly.")
            return

        # Probe readiness quickly (no UI blocking)
        try:
            r = requests.get(url, timeout=0.2)
            if 200 <= r.status_code < 500:
                self._poll_timer.stop()
                self._load(url)
                return
        except Exception:
            return

        # If it takes a while, keep the user informed.
        self.status.setText(f"Waiting for Streamlit… {url}")

    def _load(self, url: str) -> None:
        if self._webview is None:
            self.status.setText("Qt WebEngine not available; opening in browser instead.")
            self._show_placeholder(
                "Qt WebEngine (PySide6.QtWebEngineWidgets) is not available in this environment.\n\n"
                f"Open Streamlit in your browser: {url}\n"
            )
            self._open_in_browser()
            return

        self.status.setText(f"Loaded: {url}")
        self._content.setCurrentWidget(self._webview)
        self._webview.setUrl(QtCore.QUrl(url))

    def _open_in_browser(self) -> None:
        QtCore.QDesktopServices.openUrl(QtCore.QUrl(self._endpoint.url))

    def _show_placeholder(self, text: str) -> None:
        self._content.setCurrentWidget(self._placeholder)
        self._placeholder_text.setText(text)

    def _read_recent_output(self) -> str:
        if self._proc is None or self._proc.stdout is None:
            return ""
        try:
            # Non-blocking-ish: read whatever is available quickly.
            start = time.time()
            chunks: list[str] = []
            while time.time() - start < 0.05:
                line = self._proc.stdout.readline()
                if not line:
                    break
                chunks.append(line.rstrip("\n"))
            return "\n".join(chunks[-30:])
        except Exception:
            return ""

