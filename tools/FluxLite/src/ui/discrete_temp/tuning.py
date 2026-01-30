from __future__ import annotations

import os

# Re-export the tuning algorithm from a smaller module (keeps this module light).
from .pair_sweep_tuning import run_pair_sweep_tuning  # noqa: F401


def tuning_folder_for_test(test_folder: str) -> str:
    return os.path.join(str(test_folder), "tuning")


