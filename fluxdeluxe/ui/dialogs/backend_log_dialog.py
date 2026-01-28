from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QCheckBox,
)
from PySide6.QtGui import QFont, QTextCursor

if TYPE_CHECKING:
    import subprocess


class LogSignals(QObject):
    """Signals for thread-safe log updates."""
    log_received = Signal(str)


class BackendLogDialog(QDialog):
    """Dialog that displays DynamoDeluxe backend logs in real-time."""

    _instance: "BackendLogDialog | None" = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DynamoDeluxe Backend Logs")
        self.setMinimumSize(800, 500)
        self.resize(900, 600)

        # Allow dialog to stay open while interacting with main window
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)

        self._setup_ui()
        self._signals = LogSignals()
        self._signals.log_received.connect(self._append_log)
        self._reader_threads: list[threading.Thread] = []
        self._stop_event = threading.Event()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Log display area
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._log_view.setFont(QFont("Consolas", 9))
        self._log_view.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4; }"
        )
        layout.addWidget(self._log_view)

        # Bottom controls
        controls = QHBoxLayout()

        self._auto_scroll_cb = QCheckBox("Auto-scroll")
        self._auto_scroll_cb.setChecked(True)
        controls.addWidget(self._auto_scroll_cb)

        controls.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._log_view.clear)
        controls.addWidget(clear_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.hide)
        controls.addWidget(close_btn)

        layout.addLayout(controls)

    def _append_log(self, text: str):
        """Append log text to the view (called from signal, thread-safe)."""
        self._log_view.appendPlainText(text.rstrip())
        if self._auto_scroll_cb.isChecked():
            self._log_view.moveCursor(QTextCursor.MoveOperation.End)

    def start_reading(self, process: "subprocess.Popen"):
        """Start background threads to read stdout/stderr from the process."""
        self._stop_event.clear()

        if process.stdout:
            t = threading.Thread(
                target=self._read_stream,
                args=(process.stdout, "[stdout]"),
                daemon=True,
            )
            t.start()
            self._reader_threads.append(t)

        if process.stderr:
            t = threading.Thread(
                target=self._read_stream,
                args=(process.stderr, "[stderr]"),
                daemon=True,
            )
            t.start()
            self._reader_threads.append(t)

    def _read_stream(self, stream, prefix: str):
        """Read lines from a stream and emit signals."""
        try:
            for line in iter(stream.readline, b""):
                if self._stop_event.is_set():
                    break
                try:
                    text = line.decode("utf-8", errors="replace")
                    self._signals.log_received.emit(text)
                except Exception:
                    pass
        except Exception:
            pass

    def stop_reading(self):
        """Stop the reader threads."""
        self._stop_event.set()
        self._reader_threads.clear()

    def closeEvent(self, event):
        """Hide instead of close so we can reopen."""
        self.hide()
        event.ignore()

    @classmethod
    def get_instance(cls, parent=None) -> "BackendLogDialog":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(parent)
        return cls._instance
