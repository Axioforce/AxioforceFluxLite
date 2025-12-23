from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

import requests


@dataclass(frozen=True)
class HttpJsonError(RuntimeError):
    url: str
    status_code: int | None
    message: str
    response_text: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        code = self.status_code if self.status_code is not None else "unknown"
        return f"HTTP {code} for {self.url}: {self.message}"


def get_json(
    url: str,
    *,
    timeout_s: float = 10.0,
    headers: Mapping[str, str] | None = None,
) -> dict:
    resp = requests.get(str(url), headers=dict(headers or {}), timeout=float(timeout_s))
    if resp.status_code // 100 != 2:
        raise HttpJsonError(url=str(url), status_code=int(resp.status_code), message=resp.text[:500], response_text=resp.text)
    try:
        return resp.json() or {}
    except Exception as e:
        raise HttpJsonError(url=str(url), status_code=int(resp.status_code), message=f"Invalid JSON response: {e}", response_text=resp.text) from e


def post_json(
    url: str,
    body: Any,
    *,
    timeout_s: float = 30.0,
    headers: Mapping[str, str] | None = None,
) -> dict:
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(dict(headers or {}))
    resp = requests.post(str(url), data=json.dumps(body), headers=hdrs, timeout=float(timeout_s))
    if resp.status_code // 100 != 2:
        # Try to pull a useful error message out of JSON, but fall back to text.
        msg = resp.text
        try:
            payload = resp.json() or {}
            msg = payload.get("message") or payload.get("error") or msg
        except Exception:
            pass
        raise HttpJsonError(url=str(url), status_code=int(resp.status_code), message=str(msg)[:500], response_text=resp.text)
    try:
        return resp.json() or {}
    except Exception as e:
        raise HttpJsonError(url=str(url), status_code=int(resp.status_code), message=f"Invalid JSON response: {e}", response_text=resp.text) from e


