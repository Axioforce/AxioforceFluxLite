from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

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
    A small "tool page" that launches a Streamlit app and opens it in the default browser.

    - Launches Streamlit as a subprocess (optional; requires streamlit installed).
    - Shows a lightweight status page inside FluxDeluxe.
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

        self._busy = QtWidgets.QProgressBar()
        self._busy.setRange(0, 0)  # indeterminate
        self._busy.setTextVisible(False)
        self._busy.setMaximumWidth(420)

        self._placeholder_text = QtWidgets.QLabel("")
        self._placeholder_text.setWordWrap(True)
        ph.addWidget(self._busy, 0, QtCore.Qt.AlignLeft)
        ph.addWidget(self._placeholder_text)
        ph.addStretch(1)

        self._content.addWidget(self._placeholder)

        self._show_placeholder(
            "This tool runs the Metrics Editor (Streamlit) and opens it in your browser.\n\n"
            "To configure it, set:\n"
            "  METRICS_EDITOR_STREAMLIT_ENTRYPOINT=<path to your streamlit app.py>\n\n"
            f"Target URL: {self._endpoint.url}\n"
        )

    def _set_loading(self, text: str) -> None:
        self._busy.setVisible(True)
        self._show_placeholder(text)

    def _set_idle_placeholder(self, text: str) -> None:
        self._busy.setVisible(False)
        self._show_placeholder(text)

    def _repo_root(self) -> Path:
        # FluxDeluxe/ui/tools/metrics_editor_page.py -> repo root
        return Path(__file__).resolve().parents[3]

    def _default_entrypoint(self) -> str:
        # Standard location in this monorepo
        return str((self._repo_root() / "tools" / "MetricsEditor" / "metrics_editor_app.py").resolve())

    def _resolve_dynamo_root(self) -> Path:
        # Prefer explicit env override
        env = (os.environ.get("METRICS_EDITOR_DYNAMO_ROOT") or "").strip()
        if env:
            p = Path(env).expanduser().resolve()
            if p.exists():
                return p
        # Repo-local default
        return (self._repo_root() / "FluxDeluxe" / "DynamoDeluxe").resolve()

    def _resolve_entrypoint(self) -> str:
        ep = (os.environ.get("METRICS_EDITOR_STREAMLIT_ENTRYPOINT") or self._entrypoint or "").strip()
        if not ep:
            ep = self._default_entrypoint()

        p = Path(ep)
        if not p.is_absolute():
            p = (self._repo_root() / p).resolve()
        return str(p)

    def ensure_started(self) -> None:
        """
        Start Streamlit (if configured) and load the page if embedding is possible.
        Safe to call multiple times.
        """
        # Re-evaluate entrypoint each time (allows env override without restart).
        self._entrypoint = self._resolve_entrypoint()

        if not self._entrypoint or not Path(self._entrypoint).exists():
            self.status.setText("Metrics Editor is not configured (no entrypoint set).")
            self._set_idle_placeholder(
                "No Streamlit entrypoint configured.\n\n"
                "Set environment variable:\n"
                "  METRICS_EDITOR_STREAMLIT_ENTRYPOINT=<path to your streamlit app.py>\n\n"
                f"Default expected location:\n  {self._default_entrypoint()}\n\n"
                "Then restart the app (or click Restart here)."
            )
            return

        if self._proc is None or self._proc.poll() is not None:
            ok = self._start_streamlit()
            if not ok:
                return

        # Try to load once it's ready (poll timer handles readiness)
        self.status.setText(f"Starting Streamlit… {self._endpoint.url}")
        self._set_loading(
            "Starting Streamlit…\n\n"
            "If you see a browser error briefly, it usually means Streamlit is still booting.\n"
            "This page should switch to the editor automatically once it’s ready."
        )
        self._poll_timer.start()

    def restart(self) -> None:
        self.shutdown()
        # Re-read env in case user changed it without restarting python
        self._entrypoint = self._resolve_entrypoint()
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
        try:
            self._set_idle_placeholder("Streamlit stopped.")
        except Exception:
            pass

    def _start_streamlit(self) -> bool:
        entrypoint = self._resolve_entrypoint()
        dynamo_root = self._resolve_dynamo_root()
        repo_root = self._repo_root()

        # Ensure both packages are importable:
        # - repo root => `tools.*`
        # - dynamo root => `app.*` (DynamoDeluxe)
        env = os.environ.copy()
        # Ensure DynamoDeluxe runs in "dev" layout (uses ../file_system relative to cwd)
        # so importing app.config.dynamo_config can find file_system/paths.cfg.
        env.setdefault("APP_ENV", "development")
        env.setdefault("PYTHONUNBUFFERED", "1")
        pythonpath_parts = [str(repo_root)]
        if dynamo_root.exists():
            pythonpath_parts.append(str(dynamo_root))
            env.setdefault("METRICS_EDITOR_DYNAMO_ROOT", str(dynamo_root))
        if env.get("PYTHONPATH"):
            pythonpath_parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

        # data_maintenance.py uses ../file_system relative to DynamoDeluxe/app
        cwd = (dynamo_root / "app") if (dynamo_root / "app").exists() else Path(entrypoint).resolve().parent

        # Start: python -m streamlit run <entrypoint> --server.address ... --server.port ...
        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            entrypoint,
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
                cwd=str(cwd),
                env=env,
                text=True,
            )
        except Exception as exc:
            self.status.setText(f"Failed to launch Streamlit: {exc}")
            self._set_idle_placeholder(
                "Could not launch Streamlit.\n\n"
                "Make sure Streamlit is installed in this environment:\n"
                "  pip install streamlit\n"
            )
            return False

        self.status.setText(f"Launching Streamlit… {self._endpoint.url}")
        return True

    def _is_streamlit_ready(self) -> bool:
        """
        Prefer Streamlit's internal health endpoint when available.
        Fall back to probing the base URL.
        """
        base = self._endpoint.url
        try:
            r = requests.get(f"{base}/_stcore/health", timeout=0.25)
            if r.status_code == 200:
                return True
        except Exception:
            pass

        try:
            r2 = requests.get(base, timeout=0.25)
            return r2.status_code == 200
        except Exception:
            return False

    def _poll_ready(self) -> None:
        url = self._endpoint.url

        # If process died, show output hint.
        if self._proc is not None and self._proc.poll() is not None:
            self._poll_timer.stop()
            self.status.setText("Streamlit exited unexpectedly.")
            self._show_placeholder(self._read_recent_output() or "Streamlit exited unexpectedly.")
            return

        # Probe readiness quickly (no UI blocking).
        # IMPORTANT: don't treat 404/500 as "ready" — that causes the embedded view
        # to show a Chromium error page before Streamlit is actually serving.
        if self._is_streamlit_ready():
            self._poll_timer.stop()
            self._load(url)
            return

        # If it takes a while, keep the user informed.
        self.status.setText(f"Waiting for Streamlit… {url}")
        self._set_loading(f"Waiting for Streamlit…\n\n{url}")

    def _load(self, url: str) -> None:
        self.status.setText(f"Opened in browser: {url}")
        self._set_idle_placeholder(f"Opened in your browser:\n\n{url}\n")
        self._open_in_browser(url)

    def _open_in_browser(self, url: str | None = None) -> None:
        QtCore.QDesktopServices.openUrl(QtCore.QUrl(url or self._endpoint.url))

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

