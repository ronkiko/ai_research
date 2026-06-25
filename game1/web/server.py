from __future__ import annotations

import argparse
import errno
import json
import os
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Type
from urllib.parse import parse_qs, urlparse

from app.application import GameApplication

from .api import ApiRouter

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
FALLBACK_PORT_MAX = 8799
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _handler_class(router: ApiRouter) -> Type[BaseHTTPRequestHandler]:
    class GameRequestHandler(BaseHTTPRequestHandler):
        server_version = "Game1Web/0.1"

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def log_message(self, format: str, *args) -> None:
            return

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api(method, parsed)
                return

            if method != "GET":
                self._send_text(HTTPStatus.METHOD_NOT_ALLOWED, "Method Not Allowed")
                return

            if parsed.path in ("", "/"):
                self._serve_static("index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/static/app.js":
                self._serve_static("app.js", "application/javascript; charset=utf-8")
                return
            if parsed.path == "/static/style.css":
                self._serve_static("style.css", "text/css; charset=utf-8")
                return
            self._send_text(HTTPStatus.NOT_FOUND, "Not Found")

        def _handle_api(self, method: str, parsed) -> None:
            raw_body = self._read_body() if method in {"POST", "PUT", "DELETE"} else b""
            status, payload = router.dispatch(method, parsed.path, parse_qs(parsed.query), raw_body)
            self._send_json(status, payload)

        def _read_body(self) -> bytes:
            header = self.headers.get("Content-Length", "0").strip() or "0"
            try:
                length = max(0, int(header))
            except ValueError:
                length = 0
            return self.rfile.read(length) if length else b""

        def _serve_static(self, filename: str, content_type: str) -> None:
            path = os.path.join(STATIC_DIR, filename)
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
            except OSError:
                self._send_text(HTTPStatus.NOT_FOUND, "Not Found")
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: HTTPStatus, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return GameRequestHandler


def _create_server(host: str, port: int) -> ThreadingHTTPServer:
    router = ApiRouter(GameApplication.create_default())
    return ThreadingHTTPServer((host, port), _handler_class(router))


def _bind_server(host: str, requested_port: int | None) -> ThreadingHTTPServer:
    if requested_port is None:
        for port in range(DEFAULT_PORT, FALLBACK_PORT_MAX + 1):
            try:
                return _create_server(host, port)
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    continue
                raise RuntimeError(f"Could not bind server on {host}:{port}: {exc}") from exc
        raise RuntimeError(
            f"Could not find a free port in range {DEFAULT_PORT}..{FALLBACK_PORT_MAX} for host {host}."
        )

    if requested_port == 0:
        try:
            return _create_server(host, 0)
        except OSError as exc:
            raise RuntimeError(f"Could not bind server on {host}:0: {exc}") from exc

    try:
        return _create_server(host, requested_port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise RuntimeError(f"Port {requested_port} is already in use on host {host}.") from exc
        raise RuntimeError(f"Could not bind server on {host}:{requested_port}: {exc}") from exc


def _build_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the game1 web server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host (default: {DEFAULT_HOST})")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            f"Bind port. Default tries {DEFAULT_PORT}..{FALLBACK_PORT_MAX}; "
            "--port 0 lets the OS choose a free port."
        ),
    )
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser window.")
    args = parser.parse_args(argv)

    if args.port is not None and args.port < 0:
        parser.error("--port must be 0 or a positive integer")

    try:
        server = _bind_server(args.host, args.port)
    except RuntimeError as exc:
        print(str(exc))
        return 1

    actual_port = int(server.server_address[1])
    url = _build_url(args.host, actual_port)

    print("Game1 web engine")
    print(f"URL: {url}")
    print("Press Ctrl+C to stop.")

    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
