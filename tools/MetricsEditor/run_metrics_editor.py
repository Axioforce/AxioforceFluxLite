from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """
    Convenience launcher for the Streamlit Metrics Editor.

    Equivalent to:
      python -m streamlit run tools/MetricsEditor/metrics_editor_app.py

    This launcher also sets up the runtime environment so the tool behaves
    as if it lived inside DynamoDeluxe:
    - Adds repo root + DynamoDeluxe root to PYTHONPATH
    - Uses DynamoDeluxe/app as cwd so data_maintenance.py relative paths work
    """
    repo_root = Path(__file__).resolve().parents[2]
    entrypoint = (repo_root / "tools" / "MetricsEditor" / "metrics_editor_app.py").resolve()
    dynamo_root = (repo_root / "FluxDeluxe" / "DynamoDeluxe").resolve()

    env = os.environ.copy()
    env.setdefault("APP_ENV", "development")
    env.setdefault("PYTHONUNBUFFERED", "1")
    pythonpath_parts = [str(repo_root)]
    if dynamo_root.exists():
        pythonpath_parts.append(str(dynamo_root))
        env.setdefault("METRICS_EDITOR_DYNAMO_ROOT", str(dynamo_root))
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    cwd = (dynamo_root / "app") if (dynamo_root / "app").exists() else str(entrypoint.parent)

    cmd = [sys.executable, "-m", "streamlit", "run", str(entrypoint)]
    raise SystemExit(subprocess.call(cmd, cwd=str(cwd), env=env))


if __name__ == "__main__":
    main()

