from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    here = Path(__file__).resolve()
    root = here.parent.parent
    src = root / "src"
    sys.path.insert(0, str(src))


def main() -> None:
    _ensure_src_on_path()
    missing = []
    for key in [
        "GRAPH_TENANT_ID",
        "GRAPH_CLIENT_ID",
        "GRAPH_CLIENT_SECRET",
        "GRAPH_WORKBOOK_SHARING_URL",
    ]:
        if not (os.environ.get(key) or "").strip():
            missing.append(key)
    if missing:
        print("Missing environment variables:", ", ".join(missing))
        print("Set them before running. See README or previous instructions.")
        return

    try:
        from ms_graph_excel import append_summary_row  # type: ignore
    except Exception as e:
        print("Import failed. Ensure msal is installed and PYTHONPATH includes src.", e)
        return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    device_id = "TEST-PLATE-123"
    pass_fail = "Pass"
    tester = "Dummy Tester"
    model_id = "06"
    # Dummy body weight for example
    body_weight_n = 700.0
    try:
        append_summary_row(device_id, pass_fail, now, tester, body_weight_n, model_id)
        print("Success: appended dummy row to Excel.")
    except Exception as e:
        print("Failed to append row:", e)


if __name__ == "__main__":
    main()



