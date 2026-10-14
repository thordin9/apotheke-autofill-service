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
        self.chat_status = 200
        self.chat_raw: bytes | None = None
        self.chat_delay = 0.0
        self.warm_delay = 0.0
        self.warm_status = 200
        self.release = threading.Event()
        self.release.set()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                fake.requests.append((self.path, body))
                if self.path == "/api/generate":
                    time.sleep(fake.warm_delay)
                    self._send(fake.warm_status, b'{"done":true}')
                    return
                fake.release.wait(10)
                time.sleep(fake.chat_delay)
                if fake.chat_raw is not None:
                    self._send(fake.chat_status, fake.chat_raw)
                    return
                msg = {"message": {"role": "assistant", "content": fake.chat_content}}
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

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
