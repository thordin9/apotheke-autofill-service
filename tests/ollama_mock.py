"""Tiny threaded fake of the Ollama HTTP API (no model needed)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOllama:
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.chat_content: str = json.dumps(
            {
                "name": {"value": "Cordless drill", "confidence": 0.8},
                "category": {"value": "tools", "confidence": 0.9},
                "tags": [{"value": "bosch", "confidence": 0.6}, {"value": "red"}],
                "description": {"value": "Red Bosch 18V drill", "confidence": 0.7},
            }
        )
        self.chat_queue: list[str] = []  # if set, consumed one per chat call
        self.chat_status = 200
        self.chat_raw: bytes | None = None
        self.chat_delay = 0.0
        self.warm_delay = 0.0
        self.warm_status = 200
        self.load_duration_ns = 0  # reported on every chat answer
        # GET /api/ps: models Ollama has in memory; None answers 404 (old Ollama).
        self.loaded: list[str] | None = ["moondream:latest"]
        # GET /api/tags: installed models; None answers 404.
        self.tags: list[str] | None = None
        # When set, loads and chats replace `loaded` with their model, like
        # Ollama with OLLAMA_MAX_LOADED_MODELS=1.
        self.track_loads = False
        self.release = threading.Event()
        self.release.set()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.requests.append((self.path, {}))
                if self.path == "/api/ps" and fake.loaded is not None:
                    body = {"models": [{"name": n, "model": n} for n in fake.loaded]}
                    self._send(200, json.dumps(body).encode())
                elif self.path == "/api/tags" and fake.tags is not None:
                    body = {"models": [{"name": n, "model": n} for n in fake.tags]}
                    self._send(200, json.dumps(body).encode())
                else:
                    self._send(404, b'{"error":"not found"}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                fake.requests.append((self.path, body))
                if fake.track_loads and body.get("model"):
                    name = body["model"]
                    fake.loaded = [name if ":" in name else f"{name}:latest"]
                if self.path == "/api/generate":
                    time.sleep(fake.warm_delay)
                    self._send(fake.warm_status, b'{"done":true}')
                    return
                fake.release.wait(10)
                time.sleep(fake.chat_delay)
                if fake.chat_raw is not None:
                    self._send(fake.chat_status, fake.chat_raw)
                    return
                content = fake.chat_queue.pop(0) if fake.chat_queue else fake.chat_content
                msg = {
                    "message": {"role": "assistant", "content": content},
                    "load_duration": fake.load_duration_ns,
                }
                self._send(fake.chat_status, json.dumps(msg).encode())

            def _send(self, status, data):
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def chats(self) -> list[dict]:
        return [b for p, b in self.requests if p == "/api/chat"]

    def generates(self) -> list[dict]:
        return [b for p, b in self.requests if p == "/api/generate"]

    def gets(self, path: str) -> int:
        return sum(1 for p, _ in self.requests if p == path)

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
