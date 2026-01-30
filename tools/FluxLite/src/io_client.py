from __future__ import annotations

import os
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
        debug = str(os.environ.get("FLUXLITE_SOCKET_DEBUG", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        # NOTE: reconnection is handled by our own loop in _run_forever; keep socketio reconnection off.
        self._sio = socketio.Client(reconnection=False, logger=debug, engineio_logger=debug)
        self._on_json: Optional[JsonCallback] = None
        self.status = ConnectionStatus()
        self._lock = threading.Lock()

        # Wire events.
        #
        # IMPORTANT: do NOT register internal connect/disconnect handlers here because
        # external callers (HardwareService) register their own "connect"/"disconnect"
        # handlers using `on()`, and python-socketio uses a single handler per event.
        # Registering here would be overwritten (or would overwrite theirs), causing
        # misleading connection state and missing logs.
        self._sio.on("jsonData", self._on_json_data)
        self._sio.on("simpleJsonData", self._on_simple_json_data)
        # Optional catch-all: helps diagnose which events are actually arriving.
        # Enable with FLUXLITE_SOCKET_DEBUG_EVENTS=1.
        try:
            debug_events = str(os.environ.get("FLUXLITE_SOCKET_DEBUG_EVENTS", "") or "").strip().lower() in {"1", "true", "yes", "on"}
            if debug_events:
                self._sio.on("*", self._on_any_event)
        except Exception:
            pass

        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._url: Optional[str] = None

    def set_json_callback(self, cb: JsonCallback) -> None:
        self._on_json = cb

    # NOTE: connect/disconnect handlers are registered by the owner (HardwareService).

    def _on_json_data(self, data: dict) -> None:
        if self._on_json is not None:
            try:
                self._on_json(data)
            except Exception:
                # Swallow to avoid breaking the socket thread
                pass

    def _on_simple_json_data(self, payload) -> None:
        """
        DynamoDeluxe can emit msgpack frames on `simpleJsonData`.
        Decode them and forward through the same json callback path so the UI behaves identically.
        """
        if self._on_json is None:
            return
        try:
            # Server sends bytes from msgpack.packb(...)
            if isinstance(payload, (bytes, bytearray, memoryview)):
                try:
                    import msgpack  # type: ignore

                    data = msgpack.unpackb(bytes(payload), raw=False)
                except Exception as e:
                    raise RuntimeError(f"failed to decode simpleJsonData msgpack: {e}") from e
            else:
                # Some servers may send already-decoded dicts.
                data = payload
            if isinstance(data, dict):
                self._on_json(data)
        except Exception as e:
            try:
                self.status.last_error = str(e)
            except Exception:
                pass
            try:
                print(f"[IoClient] simpleJsonData decode failed: {e}")
            except Exception:
                pass

    def _on_any_event(self, event, *args) -> None:
        try:
            # Avoid printing huge payloads; just show event + type summary.
            summary = []
            for a in args[:2]:
                summary.append(type(a).__name__)
            print(f"[IoClient] recv event={event} args={summary}")
        except Exception:
            pass

    def _run_forever(self) -> None:
        backoff_s = 0.5
        max_backoff_s = 5.0
        # Ensure URL has scheme
        base = self.host
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"http://{base}"
        url = f"{base}:{self.port}"
        self._url = url
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
        # IMPORTANT: do not pre-block on `self._sio.connected`. In practice we've seen
        # false negatives (handler fires but `.connected` still reads False briefly).
        # Always attempt the emit; if it fails, log the exception.
        try:
            if str(event) in (
                "createDeviceGroup",
                "saveGroup",
                "createTemporaryGroup",
                "reinitializeDeviceGroups",
                "reinitializeConnectedDevices",
            ):
                try:
                    sid = getattr(self._sio, "sid", None)
                    print(f"[IoClient] emit: event={event} connected={getattr(self._sio, 'connected', None)} sid={sid} url={self._url}")
                except Exception:
                    pass
            if data is None:
                self._sio.emit(event)
            else:
                self._sio.emit(event, data)
        except Exception as e:
            # Do not swallow silently; otherwise UI prints "Emitting ..." but backend never sees it.
            try:
                self.status.last_error = str(e)
            except Exception:
                pass
            try:
                print(f"[IoClient] emit failed: event={event} err={e}")
            except Exception:
                pass

    # Event subscription helpers
    def on(self, event: str, handler: Callable[[dict], None]) -> None:
        try:
            self._sio.on(event, handler)
        except Exception as e:
            try:
                self.status.last_error = str(e)
            except Exception:
                pass
            try:
                print(f"[IoClient] on failed: event={event} err={e}")
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


