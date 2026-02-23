"""
Queue-tick proxy for the MMM web container.

Listens on $PORT (Cloud Run's external port).  Handles ``?queue_tick=1``
requests directly in the same process (no WebSocket / Streamlit session
required) and TCP-relays *all other* requests — including WebSocket
upgrades — transparently to Streamlit which runs on an internal port
(``STREAMLIT_PORT``, default 8501).

This solves the fundamental mismatch between Cloud Tasks (plain HTTP) and
Streamlit (Python executes only inside a WebSocket session): Cloud Tasks
now hit an actual HTTP handler that performs the queue tick and returns JSON.
"""

import http.client
import json
import logging
import os
import select
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("queue_tick_proxy")

PORT = int(os.environ.get("PORT", 8080))
STREAMLIT_PORT = int(os.environ.get("STREAMLIT_PORT", 8501))

# Ensure /app is importable regardless of working directory.
sys.path.insert(0, "/app")

# Lazy imports: resolved the first time a queue-tick request arrives so that
# startup failures in unrelated modules do not crash the proxy.
_tick_imports_lock = threading.Lock()
_tick_imports_done = False
_queue_tick_once_headless = None
_schedule_next_tick_if_needed = None
_prepare_and_launch_job = None
_GCS_BUCKET = None
_DEFAULT_QUEUE_NAME = None


def _ensure_tick_imports() -> None:
    global _tick_imports_done, _queue_tick_once_headless  # noqa: PLW0603
    global _schedule_next_tick_if_needed, _prepare_and_launch_job  # noqa: PLW0603
    global _GCS_BUCKET, _DEFAULT_QUEUE_NAME  # noqa: PLW0603
    if _tick_imports_done:
        return
    with _tick_imports_lock:
        if _tick_imports_done:
            return
        from app_shared import (  # noqa: PLC0415
            DEFAULT_QUEUE_NAME,
            GCS_BUCKET,
            _schedule_next_tick_if_needed as _sntin,
            queue_tick_once_headless,
        )
        from app_split_helpers import (  # noqa: PLC0415
            prepare_and_launch_job,
        )

        _queue_tick_once_headless = queue_tick_once_headless
        _schedule_next_tick_if_needed = _sntin
        _prepare_and_launch_job = prepare_and_launch_job
        _GCS_BUCKET = GCS_BUCKET
        _DEFAULT_QUEUE_NAME = DEFAULT_QUEUE_NAME
        _tick_imports_done = True
        logger.info(
            "[PROXY] Queue tick modules imported (DEFAULT_QUEUE_NAME=%s, GCS_BUCKET=%s)",
            DEFAULT_QUEUE_NAME,
            GCS_BUCKET,
        )


def _do_queue_tick(queue_name: str) -> dict:
    """Import queue logic lazily and run one headless tick."""
    _ensure_tick_imports()
    result = _queue_tick_once_headless(  # type: ignore[misc]
        queue_name, _GCS_BUCKET, launcher=_prepare_and_launch_job
    )
    logger.info("[PROXY] Tick result for '%s': %s", queue_name, result)
    _schedule_next_tick_if_needed(queue_name, result)  # type: ignore[misc]
    return result or {}


# ─── TCP relay used for WebSocket proxying ───────────────────────────────────


def _relay(src: socket.socket, dst: socket.socket) -> None:
    """Relay raw bytes between two sockets until one side closes."""
    try:
        while True:
            ready, _, _ = select.select([src], [], [], 60)
            if not ready:
                break
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass


# ─── HTTP request handler ────────────────────────────────────────────────────


class _ProxyHandler(BaseHTTPRequestHandler):
    server_version = "QueueTickProxy/1.0"
    protocol_version = "HTTP/1.1"

    def log_request(self, code="-", size="-"):  # type: ignore[override]
        pass  # suppress default per-request noise; we log selectively

    def log_error(self, fmt: str, *args: object) -> None:
        logger.error("Proxy: " + fmt, *args)

    # ─── helpers ─────────────────────────────────────────────────────────────

    def _is_queue_tick(self) -> bool:
        return parse_qs(urlparse(self.path).query).get("queue_tick") == ["1"]

    def _is_websocket_upgrade(self) -> bool:
        return (
            "websocket" in self.headers.get("Upgrade", "").lower()
            or "upgrade" in self.headers.get("Connection", "").lower()
        )

    # ─── queue tick endpoint ─────────────────────────────────────────────────

    def _handle_queue_tick(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        queue_name = (
            qs.get("name")
            or [os.environ.get("DEFAULT_QUEUE_NAME", "default")]
        )[0]
        logger.info(
            "[PROXY] queue_tick request for queue '%s'", queue_name
        )
        try:
            result = _do_queue_tick(queue_name)
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            logger.exception("[PROXY] queue_tick handler raised: %s", exc)
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    # ─── plain HTTP proxy ─────────────────────────────────────────────────────

    def _proxy_http(self) -> None:
        try:
            body_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(body_len) if body_len else b""
            hdrs = {
                k: v
                for k, v in self.headers.items()
                if k.lower()
                not in ("host", "connection", "transfer-encoding")
            }
            hdrs["Host"] = f"127.0.0.1:{STREAMLIT_PORT}"
            conn = http.client.HTTPConnection(
                "127.0.0.1", STREAMLIT_PORT, timeout=30
            )
            conn.request(
                self.command, self.path, body=body or None, headers=hdrs
            )
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in ("connection", "transfer-encoding"):
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(resp.read())
        except Exception as exc:
            logger.error("[PROXY] HTTP proxy error: %s", exc)
            try:
                self.send_error(502, str(exc))
            except Exception:
                pass

    # ─── WebSocket TCP tunnel ─────────────────────────────────────────────────

    def _proxy_websocket(self) -> None:
        try:
            target = socket.create_connection(
                ("127.0.0.1", STREAMLIT_PORT), timeout=10
            )
        except OSError as exc:
            logger.error(
                "[PROXY] Cannot connect to Streamlit for WS: %s", exc
            )
            try:
                self.send_error(502, "Streamlit unavailable")
            except Exception:
                pass
            return

        # Replay the original request line + headers to Streamlit so it can
        # complete the WebSocket handshake.
        req_line = (
            f"{self.command} {self.path} {self.request_version}\r\n".encode()
        )
        target.sendall(req_line)
        for k, v in self.headers.items():
            target.sendall(f"{k}: {v}\r\n".encode())
        target.sendall(b"\r\n")

        client_sock = self.connection
        t1 = threading.Thread(
            target=_relay, args=(client_sock, target), daemon=True
        )
        t2 = threading.Thread(
            target=_relay, args=(target, client_sock), daemon=True
        )
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        target.close()

    # ─── HTTP method dispatch ─────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        if self._is_queue_tick():
            self._handle_queue_tick()
        elif self._is_websocket_upgrade():
            self._proxy_websocket()
        else:
            self._proxy_http()

    # Mirror all other verbs to the HTTP proxy.
    do_POST = _proxy_http  # type: ignore[assignment]
    do_PUT = _proxy_http  # type: ignore[assignment]
    do_PATCH = _proxy_http  # type: ignore[assignment]
    do_DELETE = _proxy_http  # type: ignore[assignment]
    do_HEAD = _proxy_http  # type: ignore[assignment]
    do_OPTIONS = _proxy_http  # type: ignore[assignment]


# ─── entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _ProxyHandler)
    logger.info(
        "[PROXY] Queue-tick proxy listening on :%d → Streamlit:%d",
        PORT,
        STREAMLIT_PORT,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
