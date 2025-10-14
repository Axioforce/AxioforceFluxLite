from __future__ import annotations

import os
import sys
from typing import Optional, Any

import requests  # type: ignore

from . import config


def _discover_socket_port(host: str, http_port: int, timeout_s: float = 0.7) -> Optional[int]:
    """Attempt to discover the socket.io port by querying the backend HTTP config.

    Tries several common endpoints and searches the returned JSON for a key
    resembling "socketPort". Returns an int port on success, otherwise None.
    """
    try:
        base = host.strip()
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"http://{base}"
        # remove trailing slash for consistent formatting
        if base.endswith('/'):
            base = base[:-1]

        candidates = [
            "config",
            "dynamo/config",
            "api/config",
            "flux/config",
            "v1/config",
            "backend/config",
        ]

        def _find_socket_port(obj: Any) -> Optional[int]:
            try:
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        key = str(k).lower()
                        if "socketport" in key or ("socket" in key and "port" in key):
                            try:
                                port_val = int(v)
                                if 1000 <= port_val <= 65535:
                                    return port_val
                            except Exception:
                                pass
                        # recurse
                        found = _find_socket_port(v)
                        if found is not None:
                            return found
                elif isinstance(obj, list):
                    for item in obj:
                        found = _find_socket_port(item)
                        if found is not None:
                            return found
            except Exception:
                pass
            return None

        headers = {"Accept": "application/json"}
        for path in candidates:
            try:
                url = f"{base}:{http_port}/{path}"
                resp = requests.get(url, headers=headers, timeout=timeout_s)
                if resp.status_code != 200:
                    continue
                data = None
                try:
                    data = resp.json()
                except Exception:
                    continue
                port = _find_socket_port(data)
                if port is not None:
                    return port
            except Exception:
                continue
    except Exception:
        return None
    return None


def run_qt() -> int:
    from PySide6 import QtWidgets  # type: ignore
    from .ui.main_window import MainWindow
    from .controller import Controller

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    ctrl = Controller(win)
    win.showMaximized()
    app.aboutToQuit.connect(ctrl.stop)

    # Auto-connect on startup using discovery with env overrides and sensible fallbacks
    host = os.environ.get("SOCKET_HOST", config.SOCKET_HOST)
    env_port = os.environ.get("SOCKET_PORT")
    if env_port is not None:
        try:
            port = int(env_port)
        except Exception:
            port = int(config.SOCKET_PORT)
    else:
        http_port = int(os.environ.get("HTTP_PORT", str(config.HTTP_PORT)))
        discovered = _discover_socket_port(host, http_port)
        port = int(discovered) if discovered is not None else int(config.SOCKET_PORT)
    ctrl.connect(host, port)
    # Wire rate change callbacks after connect
    try:
        win.on_sampling_rate_changed(ctrl.request_sampling_rate)
        win.on_emission_rate_changed(ctrl.request_emission_rate)
    except Exception:
        pass
    rc = app.exec()
    ctrl.stop()
    return int(rc)


# Tkinter support has been removed. Qt is now the only UI backend.


def main() -> int:
    # Qt is required; raise a clear error if unavailable
    try:
        import PySide6  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "PySide6 is required. Tkinter fallback has been removed."
        ) from exc
    return run_qt()


if __name__ == "__main__":
    raise SystemExit(main())


