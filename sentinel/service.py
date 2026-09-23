from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import logging
import os
import signal
import threading
import time
from .market import UTC, stamp
from .engine import Engine, check_live_model
from .providers import ProviderError

log = logging.getLogger("sentinel")


def setup_server(port):
    """Explicit provisioning mode: HTTP only, no market calls or Telegram messages."""
    class SetupHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/health", "/ready"):
                self.send_error(404)
                return
            body = json.dumps({"service": "setup", "alerts_enabled": False,
                               "message": "أضف Volume /data ومفاتيح Alpaca وتلقرام، ثم غيّر MODE إلى paper وأعد النشر.",
                               "required_variables": ["ALPACA_KEY", "ALPACA_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]}, ensure_ascii=False).encode()
            self.send_response(503 if self.path == "/ready" else 200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("0.0.0.0", port), SetupHandler)
    logging.info("setup_mode alerts_disabled; configure secrets and persistent volume, then MODE=paper")
    try:
        server.serve_forever()
    finally:
        server.server_close()


class InstanceLock:
    """OS lock released on crash. A persistent Railway volume + one replica is required."""
    def __init__(self, db_path):
        self.path = Path(db_path + ".lock")
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            self.file.write(b"0")
            self.file.flush()
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *_):
        self.file.close()


def run(config):
    with InstanceLock(config.db):
        engine = Engine(config)
        check_live_model(config, engine.gate)
        stop = threading.Event()
        heartbeat = {"at": time.monotonic()}
        started = time.monotonic()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/health", "/ready"):
                    self.send_error(404)
                    return
                healthy = time.monotonic() - heartbeat["at"] < 240
                success = engine.status["last_success"]
                ready = bool(success and (datetime.now(UTC) - stamp(success)).total_seconds() < 180 and not engine.status["error"])
                ok = healthy if self.path == "/health" else healthy and ready
                body = json.dumps({"ok": ok, **engine.status}, ensure_ascii=False).encode()
                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("0.0.0.0", config.port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def watchdog():
            while not stop.wait(30):
                if time.monotonic() - heartbeat["at"] > 240:
                    log.error("worker_stalled_restarting")
                    os._exit(1)

        threading.Thread(target=watchdog, daemon=True).start()
        def shutdown(*_):
            stop.set()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        failures = 0
        connections_verified = False
        log.info("started mode=%s feed=%s watchlist_size=%d", config.mode, config.day_feed, len(config.symbols))
        try:
            while not stop.is_set():
                try:
                    if not connections_verified:
                        engine.verify_connections()
                        connections_verified = True
                        log.info("connections_verified market_calendar_and_quotes=ok telegram_confirmation=recorded")
                    engine.cycle()
                    failures = 0
                except Exception as exc:
                    failures += 1
                    # Only class name is safe: exceptions may contain credential URLs.
                    reason = exc.safe_code if isinstance(exc, ProviderError) else type(exc).__name__
                    engine.status.update(service="degraded", error=reason)
                    log.error("cycle_failed reason=%s count=%d", reason, failures)
                heartbeat["at"] = time.monotonic()
                log.info("cycle session=%s active=%s status=%s", engine.status["session"], len(engine.store.active()), engine.status["service"])
                if failures >= 10 and time.monotonic() - started > 300:
                    raise RuntimeError("Repeated provider failures; inspect credentials and service status")
                stop.wait(min(120, config.poll * max(1, failures)))
        finally:
            server.shutdown()
            engine.store.db.close()
