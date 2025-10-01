from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import socketio  # type: ignore

from . import config


JsonCallback = Callable[[dict], None]


@dataclass
class ConnectionStatus:
    connected: bool = False
    last_error: Optional[str] = None
    last_connect_time: Optional[float] = None
    last_disconnect_time: Optional[float] = None


class IoClient:
    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or config.SOCKET_HOST
        self.port = int(port or config.SOCKET_PORT)
        self._sio = socketio.Client(reconnection=False)
        self._on_json: Optional[JsonCallback] = None
        self.status = ConnectionStatus()
        self._lock = threading.Lock()

        # Wire events
        self._sio.on("connect", self._on_connect)
        self._sio.on("disconnect", self._on_disconnect)
        self._sio.on("jsonData", self._on_json_data)

        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def set_json_callback(self, cb: JsonCallback) -> None:
        self._on_json = cb

    def _on_connect(self) -> None:
        self.status.connected = True
        self.status.last_error = None
        self.status.last_connect_time = time.time()

    def _on_disconnect(self) -> None:
        self.status.connected = False
        self.status.last_disconnect_time = time.time()

    def _on_json_data(self, data: dict) -> None:
        if self._on_json is not None:
            try:
                self._on_json(data)
            except Exception:
                # Swallow to avoid breaking the socket thread
                pass

    def _run_forever(self) -> None:
        backoff_s = 0.5
        max_backoff_s = 5.0
        # Ensure URL has scheme
        base = self.host
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"http://{base}"
        url = f"{base}:{self.port}"
        while not self._stop_flag.is_set():
            try:
                self._sio.connect(url, wait=True, wait_timeout=2.0)
                # Block here; will return on disconnect or stop
                while not self._stop_flag.is_set() and self._sio.connected:
                    self._sio.sleep(0.05)
                if self._stop_flag.is_set():
                    break
            except Exception as e:
                self.status.last_error = str(e)
                time.sleep(backoff_s)
                backoff_s = min(max_backoff_s, backoff_s * 1.7)
                continue

            # If disconnected without stop, attempt reconnect with backoff
            time.sleep(backoff_s)
            backoff_s = min(max_backoff_s, backoff_s * 1.4)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_forever, name="IoClientThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        try:
            if self._sio.connected:
                self._sio.disconnect()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    # Public emit API
    def emit(self, event: str, data: Optional[dict] = None) -> None:
        try:
            if data is None:
                self._sio.emit(event)
            else:
                self._sio.emit(event, data)
        except Exception:
            pass

    # Event subscription helpers
    def on(self, event: str, handler: Callable[[dict], None]) -> None:
        try:
            self._sio.on(event, handler)
        except Exception:
            pass

    def once(self, event: str, handler: Callable[[dict], None]) -> None:
        called = {"v": False}

        def _wrapper(data: dict) -> None:
            if called["v"]:
                return
            called["v"] = True
            try:
                handler(data)
            except Exception:
                pass

        try:
            self._sio.on(event, _wrapper)
        except Exception:
            pass


