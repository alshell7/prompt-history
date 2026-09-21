"""Loopback HTTP server backing the browser UI.

Three endpoints: the page, a JSON snapshot, and the zip archive. Nothing is
written to disk by the server and nothing leaves the machine.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .api import PromptHistory
from .config import Config
from .export import build_zip_bytes, render

WEB_DIR = Path(__file__).parent / "web"

# Query parameters the UI may pass through to narrow a download.
FILTER_KEYS = ("search", "tool", "since", "until", "projects",
               "exclude_projects", "min_words", "max_words")


class _Handler(BaseHTTPRequestHandler):
    config: Config = Config()
    server_version = "PromptHistory"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _filters(self, query: dict) -> dict:
        out = {}
        for key in FILTER_KEYS:
            values = query.get(key)
            if not values or not values[0]:
                continue
            raw = values[0]
            if key in ("projects", "exclude_projects"):
                out[key] = [p for p in raw.split(",") if p]
            elif key in ("min_words", "max_words"):
                try:
                    out[key] = int(raw)
                except ValueError:
                    continue
            else:
                out[key] = raw
        return out

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            page = WEB_DIR / "index.html"
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            return

        if path == "/api/data":
            try:
                payload = PromptHistory(self.config).snapshot()
            except Exception as exc:          # a bad source must not blank the UI
                payload = {"error": f"{type(exc).__name__}: {exc}"}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/download.zip":
            try:
                snapshot = PromptHistory(self.config).snapshot(**self._filters(query))
                body = build_zip_bytes(snapshot)
            except Exception as exc:
                self._send(500, f"Export failed: {exc}".encode(),
                           "text/plain; charset=utf-8")
                return
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._send(200, body, "application/zip",
                       {"Content-Disposition":
                        f'attachment; filename="prompt-history-{stamp}.zip"'})
            return

        if path.startswith("/api/export."):
            fmt = path.rsplit(".", 1)[-1]
            types = {"md": "text/markdown", "json": "application/json",
                     "csv": "text/csv", "txt": "text/plain"}
            if fmt not in types:
                self._send(404, b"Unknown format", "text/plain; charset=utf-8")
                return
            snapshot = PromptHistory(self.config).snapshot(**self._filters(query))
            body = render(snapshot, fmt)
            body = body.encode("utf-8") if isinstance(body, str) else body
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._send(200, body, f"{types[fmt]}; charset=utf-8",
                       {"Content-Disposition":
                        f'attachment; filename="prompt-history-{stamp}.{fmt}"'})
            return

        self._send(404, b"Not found", "text/plain; charset=utf-8")


def serve(config: Config | None = None, verbose: bool = False) -> None:
    cfg = config or Config.load()
    handler = type("Handler", (_Handler,), {"config": cfg})

    httpd = None
    for offset in range(20):              # walk forward if the port is busy
        try:
            httpd = ThreadingHTTPServer((cfg.host, cfg.port + offset), handler)
            break
        except OSError as exc:
            if offset == 19:
                raise SystemExit(
                    f"Could not bind {cfg.host}:{cfg.port}-{cfg.port + 19} ({exc}). "
                    "Pass --port with a free port."
                )
    assert httpd is not None
    httpd.verbose = verbose

    url = f"http://{cfg.host}:{httpd.server_address[1]}/"
    print(f"Prompt History is running at {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if cfg.open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        httpd.server_close()
