import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Tuple
from urllib.parse import unquote, urlparse

from .web_state import WebStateAgent


def start_web_server(
    host: str,
    port: int,
    state_agent: WebStateAgent,
    static_dir: Path,
) -> Tuple[ThreadingHTTPServer, threading.Thread]:
    handler = _make_handler(state_agent, static_dir)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _make_handler(state_agent: WebStateAgent, static_dir: Path):
    static_root = static_dir.resolve()

    class StorageAgentsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                self._send_json(state_agent.snapshot())
                return
            if parsed.path == "/":
                self._send_file(static_root / "index.html")
                return
            self._send_static(parsed.path)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, payload: object) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, url_path: str) -> None:
            relative = unquote(url_path.lstrip("/"))
            path = (static_root / relative).resolve()
            if static_root not in path.parents and path != static_root:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            self._send_file(path)

        def _send_file(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type, _ = mimetypes.guess_type(str(path))
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return StorageAgentsHandler
