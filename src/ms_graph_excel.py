from __future__ import annotations

import base64
import json
import re
from typing import List, Optional, Tuple

import requests

# Support importing as a package (src.ui uses relative imports) and as a script helper (examples)
try:
    from . import config as _cfg  # type: ignore
except Exception:  # pragma: no cover - fallback for direct script usage
    try:
        import config as _cfg  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ImportError("Unable to import config module from package or top-level.") from e
config = _cfg


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphAuthError(Exception):
    pass


class GraphApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Graph API error {status}: {message}")
        self.status = status
        self.message = message


def _get_access_token() -> str:
    try:
        import msal  # type: ignore
    except Exception as e:
        raise GraphAuthError("msal is not installed. Install with: pip install msal") from e

    tenant = (config.GRAPH_TENANT_ID or "").strip()
    client_id = (config.GRAPH_CLIENT_ID or "").strip()
    client_secret = (config.GRAPH_CLIENT_SECRET or "").strip()
    if not tenant or not client_id or not client_secret:
        raise GraphAuthError("GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET must be set in environment.")

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=[GRAPH_SCOPE])
    if not result or "access_token" not in result:
        raise GraphAuthError(str(result.get("error_description") if isinstance(result, dict) else "Unknown auth error"))
    return str(result["access_token"])


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _encode_share_id(url: str) -> str:
    # https://learn.microsoft.com/graph/api/shares-get?view=graph-rest-1.0&tabs=http
    # shares/{shareId} where shareId = "u!" + base64url(utf8(link)) without padding
    raw = url.encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"u!{b64}"


def _parse_start_row_from_address(address: str) -> int:
    # address like "Sheet1!A1:E3" -> start cell is A1 -> row 1
    try:
        m = re.search(r"!([A-Z]+)(\d+)", address)
        if m:
            return int(m.group(2))
    except Exception:
        pass
    return 1


def _request_json(method: str, url: str, token: str, body: Optional[dict] = None) -> dict:
    resp = requests.request(method, url, headers=_headers(token), data=(json.dumps(body) if body is not None else None), timeout=15)
    if resp.status_code // 100 != 2:
        # Try to surface Graph error message if present
        try:
            err = resp.json()
            msg = err.get("error", {}).get("message") or resp.text
        except Exception:
            msg = resp.text
        raise GraphApiError(resp.status_code, msg)
    try:
        return resp.json()
    except Exception:
        return {}


def _ensure_worksheet(token: str, drive_id: str, item_id: str, sheet_name: str) -> None:
    # Check if worksheet exists; if not, add it
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets"
    data = _request_json("GET", url, token)
    names = [str(w.get("name", "")) for w in data.get("value", [])]
    if sheet_name not in names:
        _request_json("POST", url + "/add", token, body={"name": sheet_name})


def _ensure_headers(token: str, drive_id: str, item_id: str, sheet_name: str, headers_row: List[str]) -> None:
    # If A1 is empty, write headers into A1:... span
    cols = len(headers_row)
    last_col_letter = _col_letter(cols)
    rng_url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets('{sheet_name}')/range(address='A1:{last_col_letter}1')"
    rng = _request_json("GET", rng_url, token)
    values = rng.get("values", [])
    has_any = bool(values and values[0] and any(str(x).strip() for x in values[0]))
    if not has_any:
        _request_json("PATCH", rng_url, token, body={"values": [headers_row]})


def _col_letter(col_index_1_based: int) -> str:
    # 1 -> A, 26 -> Z, 27 -> AA
    col = col_index_1_based
    letters = []
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _get_next_row_index(token: str, drive_id: str, item_id: str, sheet_name: str, required_cols: int) -> int:
    # Determine next row by usedRange; if sheet empty, next is 2 (after headers)
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets('{sheet_name}')/usedRange(valuesOnly=true)"
    data = _request_json("GET", url, token)
    address = str(data.get("address", "A1"))
    row_count = int(data.get("rowCount", 0) or 0)
    start_row = _parse_start_row_from_address(address)
    if row_count <= 0:
        # No data; assume headers at row 1
        return 2
    # If usedRange is smaller than our required columns (e.g., first write), still append after last used row
    return start_row + row_count


def _resolve_drive_item(token: str) -> Tuple[str, str]:
    # Returns (drive_id, item_id)
    share_url = (config.GRAPH_WORKBOOK_SHARING_URL or "").strip()
    item_id = (config.GRAPH_WORKBOOK_ITEM_ID or "").strip()
    drive_id = (config.GRAPH_DRIVE_ID or "").strip()
    upn = (config.GRAPH_USER_UPN or "").strip()
    wpath = (config.GRAPH_WORKBOOK_PATH or "").strip().lstrip("/")

    if share_url:
        share_id = _encode_share_id(share_url)
        data = _request_json("GET", f"{GRAPH_BASE}/shares/{share_id}/driveItem", token)
        di = data
        drv = di.get("parentReference", {}).get("driveId")
        itm = di.get("id")
        if not drv or not itm:
            raise GraphApiError(404, "Unable to resolve driveItem from sharing URL.")
        return str(drv), str(itm)

    if item_id and drive_id:
        return drive_id, item_id

    # Fallback: resolve by user UPN + path within their OneDrive
    if upn and wpath:
        data = _request_json("GET", f"{GRAPH_BASE}/users/{upn}/drive/root:/{wpath}", token)
        drv = data.get("parentReference", {}).get("driveId")
        itm = data.get("id")
        if not drv or not itm:
            raise GraphApiError(404, "Unable to resolve driveItem from user path.")
        return str(drv), str(itm)

    raise GraphApiError(400, "No workbook reference provided. Set GRAPH_WORKBOOK_SHARING_URL or GRAPH_WORKBOOK_ITEM_ID+GRAPH_DRIVE_ID.")


def append_summary_row(device_id: str, pass_fail: str, date_text: str, tester: str, model_id: str, worksheet_name: Optional[str] = None) -> None:
    token = _get_access_token()
    drive_id, item_id = _resolve_drive_item(token)
    sheet = (worksheet_name or config.GRAPH_WORKSHEET_NAME or "Summary").strip()

    # Ensure worksheet and headers
    _ensure_worksheet(token, drive_id, item_id, sheet)
    headers_row = ["DeviceID", "Pass/Fail", "DateTime", "Tester", "ModelID"]
    _ensure_headers(token, drive_id, item_id, sheet, headers_row)

    # Compute next row
    next_row = _get_next_row_index(token, drive_id, item_id, sheet, len(headers_row))
    last_col_letter = _col_letter(len(headers_row))
    rng_url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets('{sheet}')/range(address='A{next_row}:{last_col_letter}{next_row}')"
    values = [[str(device_id), str(pass_fail), str(date_text), str(tester), str(model_id)]]
    _request_json("PATCH", rng_url, token, body={"values": values})


