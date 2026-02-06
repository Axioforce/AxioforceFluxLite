from __future__ import annotations

import os
from dataclasses import dataclass

from tools.MetricsEditor import paths


@dataclass(frozen=True)
class RefreshStatus:
    ok: bool
    message: str


def refresh_analytics_and_capture_configs() -> list[RefreshStatus]:
    """
    Attempts to refresh the repo snapshot folders:
      - file_system/analytics_db
      - file_system/capture_config_from_db

    This requires Firebase credentials to be configured; failures are reported
    to the UI but should not stop the editor from starting.
    """
    statuses: list[RefreshStatus] = []

    # Safety: allow users to run the editor with AXF_FIREBASE_CRED pointing at dev, while keeping
    # the "Pull from Firebase" button reading from production snapshots (most up-to-date).
    _saved_override = os.environ.pop("AXF_FIREBASE_CRED", None)
    # Ensure DynamoDeluxe config loads in dev layout:
    # - APP_ENV=development => base_path resolves to ../file_system
    # - cwd=.../DynamoDeluxe/app => ../file_system points at the repo file_system/
    os.environ.setdefault("APP_ENV", "development")
    try:
        app_cwd = paths.dynamo_root() / "app"
        if app_cwd.exists():
            os.chdir(str(app_cwd))
    except Exception:
        pass

    try:
        # Import lazily so the editor can still run in "offline snapshot" mode
        # even when DynamoDeluxe-only dependencies are not installed.
        from app.data_maintenance.data_maintenance import (  # type: ignore
            retrieve_analytics_from_db,
            retrieve_capture_configurations_from_db,
        )
    except Exception as e:
        hint = ""
        if "dynamo_config" in str(e):
            hint = (
                " Hint: DynamoDeluxe expects APP_ENV=development + cwd at DynamoDeluxe/app so it uses the repo "
                "`file_system/` (otherwise it looks for an `_internal/file_system` bundle)."
            )
        statuses.append(
            RefreshStatus(
                ok=False,
                message=(
                    "Failed to import DynamoDeluxe data maintenance module. "
                    "Install DynamoDeluxe dependencies (or run in offline snapshot mode). "
                    f"Error: {e}.{hint}"
                ),
            )
        )
        return statuses

    try:
        try:
            retrieve_analytics_from_db()
            statuses.append(RefreshStatus(ok=True, message="Refreshed analytics_db from Firebase."))
        except Exception as e:
            statuses.append(RefreshStatus(ok=False, message=f"Failed refreshing analytics_db: {e}"))

        try:
            retrieve_capture_configurations_from_db()
            statuses.append(RefreshStatus(ok=True, message="Refreshed capture_config_from_db from Firebase."))
        except Exception as e:
            statuses.append(RefreshStatus(ok=False, message=f"Failed refreshing capture_config_from_db: {e}"))
    finally:
        if _saved_override:
            os.environ["AXF_FIREBASE_CRED"] = _saved_override

    return statuses


def pull_cas_from_prod() -> RefreshStatus:
    """
    Pull capture_analytic_setting from prod (unsets AXF_FIREBASE_CRED to ensure prod).
    """
    _saved_override = os.environ.pop("AXF_FIREBASE_CRED", None)
    os.environ.setdefault("APP_ENV", "development")
    try:
        app_cwd = paths.dynamo_root() / "app"
        if app_cwd.exists():
            os.chdir(str(app_cwd))
    except Exception:
        pass

    try:
        from app.data_maintenance.data_maintenance import (  # type: ignore
            retrieve_capture_analytic_settings_from_db,
        )
        retrieve_capture_analytic_settings_from_db()
        return RefreshStatus(ok=True, message="Refreshed capture_analytic_setting_from_db from prod Firebase.")
    except Exception as e:
        return RefreshStatus(ok=False, message=f"Failed refreshing capture_analytic_setting_from_db: {e}")
    finally:
        if _saved_override:
            os.environ["AXF_FIREBASE_CRED"] = _saved_override


def pull_capture_from_prod() -> RefreshStatus:
    """
    Pull capture configs from prod (unsets AXF_FIREBASE_CRED to ensure prod).
    """
    _saved_override = os.environ.pop("AXF_FIREBASE_CRED", None)
    os.environ.setdefault("APP_ENV", "development")
    try:
        app_cwd = paths.dynamo_root() / "app"
        if app_cwd.exists():
            os.chdir(str(app_cwd))
    except Exception:
        pass

    try:
        from app.data_maintenance.data_maintenance import (  # type: ignore
            retrieve_capture_configurations_from_db,
        )
        retrieve_capture_configurations_from_db()
        return RefreshStatus(ok=True, message="Refreshed capture_config_from_db from prod Firebase.")
    except Exception as e:
        return RefreshStatus(ok=False, message=f"Failed refreshing capture_config_from_db: {e}")
    finally:
        if _saved_override:
            os.environ["AXF_FIREBASE_CRED"] = _saved_override


def pull_metrics_from_prod() -> RefreshStatus:
    """
    Pull analytics (metrics) from prod (unsets AXF_FIREBASE_CRED to ensure prod).
    """
    _saved_override = os.environ.pop("AXF_FIREBASE_CRED", None)
    os.environ.setdefault("APP_ENV", "development")
    try:
        app_cwd = paths.dynamo_root() / "app"
        if app_cwd.exists():
            os.chdir(str(app_cwd))
    except Exception:
        pass

    try:
        from app.data_maintenance.data_maintenance import (  # type: ignore
            retrieve_analytics_from_db,
        )
        retrieve_analytics_from_db()
        return RefreshStatus(ok=True, message="Refreshed analytics_db from prod Firebase.")
    except Exception as e:
        return RefreshStatus(ok=False, message=f"Failed refreshing analytics_db: {e}")
    finally:
        if _saved_override:
            os.environ["AXF_FIREBASE_CRED"] = _saved_override


def pull_cas_from_dev() -> RefreshStatus:
    """
    Pull capture_analytic_setting from dev (sets AXF_FIREBASE_CRED to dev cred).
    """
    dev_cred = str(paths.dynamo_root() / "file_system" / "firebase-dev-key.json")
    _saved_override = os.environ.get("AXF_FIREBASE_CRED")
    os.environ["AXF_FIREBASE_CRED"] = dev_cred
    os.environ.setdefault("APP_ENV", "development")
    try:
        app_cwd = paths.dynamo_root() / "app"
        if app_cwd.exists():
            os.chdir(str(app_cwd))
    except Exception:
        pass

    try:
        from app.data_maintenance.data_maintenance import (  # type: ignore
            retrieve_capture_analytic_settings_from_db,
        )
        retrieve_capture_analytic_settings_from_db()
        return RefreshStatus(ok=True, message="Refreshed capture_analytic_setting_from_db from dev Firebase.")
    except Exception as e:
        return RefreshStatus(ok=False, message=f"Failed refreshing capture_analytic_setting_from_db: {e}")
    finally:
        if _saved_override is not None:
            os.environ["AXF_FIREBASE_CRED"] = _saved_override
        else:
            os.environ.pop("AXF_FIREBASE_CRED", None)


def pull_capture_from_dev() -> RefreshStatus:
    """
    Pull capture configs from dev (sets AXF_FIREBASE_CRED to dev cred).
    """
    dev_cred = str(paths.dynamo_root() / "file_system" / "firebase-dev-key.json")
    _saved_override = os.environ.get("AXF_FIREBASE_CRED")
    os.environ["AXF_FIREBASE_CRED"] = dev_cred
    os.environ.setdefault("APP_ENV", "development")
    try:
        app_cwd = paths.dynamo_root() / "app"
        if app_cwd.exists():
            os.chdir(str(app_cwd))
    except Exception:
        pass

    try:
        from app.data_maintenance.data_maintenance import (  # type: ignore
            retrieve_capture_configurations_from_db,
        )
        retrieve_capture_configurations_from_db()
        return RefreshStatus(ok=True, message="Refreshed capture_config_from_db from dev Firebase.")
    except Exception as e:
        return RefreshStatus(ok=False, message=f"Failed refreshing capture_config_from_db: {e}")
    finally:
        if _saved_override is not None:
            os.environ["AXF_FIREBASE_CRED"] = _saved_override
        else:
            os.environ.pop("AXF_FIREBASE_CRED", None)


def pull_metrics_from_dev() -> RefreshStatus:
    """
    Pull analytics (metrics) from dev (sets AXF_FIREBASE_CRED to dev cred).
    """
    dev_cred = str(paths.dynamo_root() / "file_system" / "firebase-dev-key.json")
    _saved_override = os.environ.get("AXF_FIREBASE_CRED")
    os.environ["AXF_FIREBASE_CRED"] = dev_cred
    os.environ.setdefault("APP_ENV", "development")
    try:
        app_cwd = paths.dynamo_root() / "app"
        if app_cwd.exists():
            os.chdir(str(app_cwd))
    except Exception:
        pass

    try:
        from app.data_maintenance.data_maintenance import (  # type: ignore
            retrieve_analytics_from_db,
        )
        retrieve_analytics_from_db()
        return RefreshStatus(ok=True, message="Refreshed analytics_db from dev Firebase.")
    except Exception as e:
        return RefreshStatus(ok=False, message=f"Failed refreshing analytics_db: {e}")
    finally:
        if _saved_override is not None:
            os.environ["AXF_FIREBASE_CRED"] = _saved_override
        else:
            os.environ.pop("AXF_FIREBASE_CRED", None)

