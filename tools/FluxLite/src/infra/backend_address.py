from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .. import config


def _normalize_scheme_host_no_port(host: str | None) -> str:
    """
    Normalize a host string into 'scheme://hostname' with no port and no trailing slash.

    Examples:
      - 'localhost' -> 'http://localhost'
      - 'http://localhost:3001' -> 'http://localhost'
      - 'https://example.com/' -> 'https://example.com'
    """
    raw = (host or "").strip() or "http://localhost"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    scheme = parsed.scheme or "http"
    hostname = parsed.hostname or "localhost"
    return f"{scheme}://{hostname}"


@dataclass(frozen=True)
class BackendAddress:
    host: str
    port: int

    def base_url(self) -> str:
        return f"{_normalize_scheme_host_no_port(self.host)}:{int(self.port)}"

    def process_csv_url(self) -> str:
        return f"{self.base_url()}/api/device/process-csv"

    def get_groups_url(self) -> str:
        return f"{self.base_url()}/api/get-groups"


def backend_address_from_config() -> BackendAddress:
    host = getattr(config, "SOCKET_HOST", "http://localhost")
    port = int(getattr(config, "HTTP_PORT", 3001))
    return BackendAddress(host=str(host), port=int(port))


