from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

_logger = logging.getLogger(__name__)


_dynamo_process: "subprocess.Popen[bytes] | None" = None


def get_dynamo_process() -> "subprocess.Popen[bytes] | None":
    """Get the DynamoDeluxe backend subprocess (for log viewing, etc.)."""
    return _dynamo_process


def _get_dynamo_path() -> Path:
    """Return the path to the DynamoDeluxe submodule."""
    return Path(__file__).resolve().parent / "DynamoDeluxe"


def _update_dynamo_deluxe() -> None:
    """Check for and pull the latest DynamoDeluxe submodule if updates are available."""
    submodule_path = _get_dynamo_path()
    if not submodule_path.exists():
        _logger.warning("DynamoDeluxe submodule not found at %s", submodule_path)
        return

    try:
        # Fetch latest from remote
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=submodule_path,
            capture_output=True,
            check=True,
        )

        # Check if we're behind
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=submodule_path,
            capture_output=True,
            text=True,
            check=True,
        )
        commits_behind = int(result.stdout.strip() or "0")

        if commits_behind > 0:
            _logger.info("DynamoDeluxe is %d commit(s) behind. Pulling updates...", commits_behind)
            subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=submodule_path,
                capture_output=True,
                check=True,
            )
            _logger.info("DynamoDeluxe updated successfully.")
        else:
            _logger.info("DynamoDeluxe is up to date.")

    except subprocess.CalledProcessError as exc:
        _logger.warning("Failed to update DynamoDeluxe: %s", exc)
    except Exception as exc:
        _logger.warning("Unexpected error updating DynamoDeluxe: %s", exc)


def _start_dynamo_backend() -> None:
    """Start the DynamoDeluxe backend as a subprocess."""
    global _dynamo_process

    dynamo_path = _get_dynamo_path()
    if not dynamo_path.exists():
        _logger.warning("DynamoDeluxe not found, skipping backend startup")
        return

    main_script = dynamo_path / "app" / "main.py"
    if not main_script.exists():
        _logger.warning("DynamoDeluxe main.py not found at %s", main_script)
        return

    # Set up environment matching the PyCharm run configuration
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["APP_ENV"] = "development"

    # Add DynamoDeluxe root to PYTHONPATH so "app.main" imports work
    pythonpath_parts = [str(dynamo_path)]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    # Working directory should be the "app" subfolder
    # so that relative paths like "../file_system" resolve correctly
    working_dir = dynamo_path / "app"

    try:
        _logger.info("Starting DynamoDeluxe backend...")
        _logger.info("  Python: %s", sys.executable)
        _logger.info("  Script: %s", main_script)
        _logger.info("  Working directory: %s", working_dir)
        _logger.info("  PYTHONPATH: %s", env["PYTHONPATH"])
        _dynamo_process = subprocess.Popen(
            [sys.executable, str(main_script)],
            cwd=working_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _logger.info("DynamoDeluxe backend started (PID: %d)", _dynamo_process.pid)

        # Check if process died immediately
        import time
        time.sleep(0.5)
        poll_result = _dynamo_process.poll()
        if poll_result is not None:
            _logger.error("DynamoDeluxe backend exited immediately with code: %d", poll_result)
            # Try to read any error output
            stdout_data, stderr_data = _dynamo_process.communicate(timeout=2)
            if stdout_data:
                _logger.error("stdout: %s", stdout_data.decode("utf-8", errors="replace")[:2000])
            if stderr_data:
                _logger.error("stderr: %s", stderr_data.decode("utf-8", errors="replace")[:2000])
            _dynamo_process = None
        else:
            _logger.info("DynamoDeluxe backend is running")
    except Exception as exc:
        _logger.error("Failed to start DynamoDeluxe backend: %s", exc)
        import traceback
        _logger.error(traceback.format_exc())


def _stop_dynamo_backend() -> None:
    """Stop the DynamoDeluxe backend subprocess."""
    global _dynamo_process

    if _dynamo_process is None:
        return

    try:
        _logger.info("Stopping DynamoDeluxe backend (PID: %d)...", _dynamo_process.pid)
        _dynamo_process.terminate()
        _dynamo_process.wait(timeout=10)
        _logger.info("DynamoDeluxe backend stopped.")
    except subprocess.TimeoutExpired:
        _logger.warning("DynamoDeluxe backend did not stop gracefully, killing...")
        _dynamo_process.kill()
        _dynamo_process.wait()
    except Exception as exc:
        _logger.error("Error stopping DynamoDeluxe backend: %s", exc)
    finally:
        _dynamo_process = None


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
    # Check for DynamoDeluxe updates before starting the app
    _update_dynamo_deluxe()

    # Start the DynamoDeluxe backend
    _start_dynamo_backend()

    # Qt is required; raise a clear error if unavailable
    try:
        import PySide6  # noqa: F401
    except Exception as exc:
        raise RuntimeError("PySide6 is required.") from exc

    try:
        return run_qt()
    finally:
        # Ensure backend is stopped when the Qt app exits
        _stop_dynamo_backend()


if __name__ == "__main__":
    raise SystemExit(main())

