from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def project_root() -> str:
    """
    Return the repository root directory.

    This project commonly stores user data folders (e.g. `temp_testing/`,
    `discrete_temp_testing/`, `live_test_logs/`) at the repo root. Relying on the
    process working directory (CWD) is brittle after refactors / packaging, so
    centralize the resolution here.
    """
    # src/project_paths.py -> repo root is parent of src/
    try:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    except Exception:
        return os.getcwd()


def data_dir(folder_name: str) -> str:
    """Return absolute path to a repo-root data folder (does not create it)."""
    return os.path.join(project_root(), str(folder_name or "").strip())


