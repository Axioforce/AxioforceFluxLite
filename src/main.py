from __future__ import annotations

import os
import sys

from . import config


def run_qt() -> int:
    from PySide6 import QtWidgets  # type: ignore
    from .view_qt import MainWindow
    from .controller import Controller

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    ctrl = Controller(win)
    win.showMaximized()
    app.aboutToQuit.connect(ctrl.stop)

    # Auto-connect on startup using env override if present
    host = os.environ.get("SOCKET_HOST", config.SOCKET_HOST)
    port = int(os.environ.get("SOCKET_PORT", str(config.SOCKET_PORT)))
    ctrl.connect(host, port)
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


