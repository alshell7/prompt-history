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
from .config import Config, read_sync_state, write_sync_state
from .export import build_zip_bytes, render
from .folderpick import pick_folder
from .sync import SyncError

WEB_DIR = Path(__file__).parent / "web"

# Query parameters the UI may pass through to narrow a download.
FILTER_KEYS = ("search", "tool", "since", "until", "projects",
               "exclude_projects", "min_words", "max_words")


def _auto_sync(config: Config, history: PromptHistory) -> dict | None:
    """Mirror to the sync folder when the user has switched that on."""
    if not (config.sync_enabled and config.sync_folder):
        return None
    try:
        return history.sync().to_dict()
    except SyncError as exc:
        return {"error": str(exc)}
    except OSError as exc:
        return {"error": f"Could not write to the sync folder: {exc}"}


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
                config = Config.load()        # pick up UI changes to sync.json
                self.__class__.config = config
                history = PromptHistory(config)
                payload = history.snapshot()
                payload["sync"] = {
                    **{k: getattr(config, k) for k in (
                        "sync_folder", "sync_enabled", "sync_layout",
                        "sync_include_tool", "sync_format", "sync_prune")},
                    "picker": True,
                    "last": _auto_sync(config, history),
                }
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

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path

        # Open a native folder chooser on this machine and return the path.
        if path == "/api/sync/pick":
            chosen, error = pick_folder()
            self._json(200, {"folder": chosen, "error": error})
            return

        if path == "/api/sync/settings":
            body = self._body()
            allowed = ("sync_folder", "sync_enabled", "sync_layout",
                       "sync_include_tool", "sync_format", "sync_prune")
            values = {k: v for k, v in body.items() if k in allowed}
            try:
                write_sync_state(values)
            except OSError as exc:
                self._json(500, {"error": f"Could not save settings: {exc}"})
                return
            self.__class__.config = Config.load()
            self._json(200, {"saved": read_sync_state()})
            return

        if path == "/api/sync/run":
            body = self._body()
            config = Config.load()
            self.__class__.config = config
            history = PromptHistory(config)
            try:
                report = history.sync(
                    body.get("folder") or config.sync_folder,
                    dry_run=bool(body.get("dry_run")),
                )
            except SyncError as exc:
                self._json(400, {"error": str(exc)})
                return
            except OSError as exc:
                self._json(500, {"error": f"Could not write to the folder: {exc}"})
                return
            self._json(200, report.to_dict())
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

    if cfg.sync_enabled and cfg.sync_folder:
        report = _auto_sync(cfg, PromptHistory(cfg))
        if report and report.get("error"):
            print(f"Sync skipped: {report['error']}", flush=True)
        elif report:
            print(f"Synced to {report['folder']}. {report['summary']}", flush=True)

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
