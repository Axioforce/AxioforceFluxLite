from __future__ import annotations

import os
import sys
import logging
from typing import Optional, Any

import requests  # type: ignore

from . import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)


def run_qt() -> int:
    from PySide6 import QtWidgets  # type: ignore
    from .ui.main_window import MainWindow

    app = QtWidgets.QApplication(sys.argv)
    
    win = MainWindow()
    win.showMaximized()
    
    # Connect application quit to controller shutdown
    app.aboutToQuit.connect(win.controller.shutdown)

    rc = app.exec()
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


