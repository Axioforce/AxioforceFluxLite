from __future__ import annotations

import logging
import sys
from pathlib import Path


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def run_qt() -> int:
    # Windows taskbar icon grouping is tied to an AppUserModelID.
    try:
        if sys.platform.startswith("win"):
            import ctypes  # noqa: WPS433

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Axioforce.AxioforceFluxDeluxe")
    except Exception:
        pass

    from PySide6 import QtGui, QtWidgets  # type: ignore

    from .ui.main_window import MainWindow

    app = QtWidgets.QApplication(sys.argv)

    # App/window icon
    try:
        icon_path = Path(__file__).resolve().parent / "ui" / "assets" / "icons" / "fluxliteicon.svg"
        icon = QtGui.QIcon(str(icon_path))
        if not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:
        pass

    # Global theme (QSS). Rewrite url("assets/...") to absolute paths so icons work reliably.
    try:
        import re

        qss_path = Path(__file__).resolve().parent / "ui" / "theme.qss"
        qss_text = qss_path.read_text(encoding="utf-8")
        ui_dir = qss_path.parent

        def _abs_url(m: "re.Match[str]") -> str:
            rel = m.group("rel")
            abs_path = (ui_dir / rel).resolve().as_posix()
            return f'url("{abs_path}")'

        qss_text = re.sub(
            r"url\(\s*(?P<q>['\"]?)(?P<rel>assets/[^)\"']+)(?P=q)\s*\)",
            _abs_url,
            qss_text,
        )
        app.setStyleSheet(qss_text)
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to load Qt theme.qss: %s", exc)

    win = MainWindow()
    win.showMaximized()

    rc = app.exec()
    return int(rc)


def main() -> int:
    # Qt is required; raise a clear error if unavailable
    try:
        import PySide6  # noqa: F401
    except Exception as exc:
        raise RuntimeError("PySide6 is required.") from exc
    return run_qt()


if __name__ == "__main__":
    raise SystemExit(main())

