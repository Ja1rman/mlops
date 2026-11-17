import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

MODES = {"ok", "rate_limit", "quota", "unavailable", "timeout", "broken"}
STATE = {
    "mode": "ok", "retry_after": 2, "delay": 12, "calls": 0,
    "last_source_markers": [], "last_stream": None,
}
LOCK = Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, data, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def read_json(self):
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size <= 32_768:
            raise ValueError()
        body = json.loads(self.rfile.read(size))
        if not isinstance(body, dict):
            raise ValueError()
        return body

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ready"})
        elif self.path == "/control":
            with LOCK:
                state = STATE.copy()
            self.respond(200, state)
        else:
            self.respond(404, {"error": "not_found"})

    def do_POST(self):
        try:
            body = self.read_json()
        except ValueError:
            self.respond(400, {"error": "invalid_input"})
            return
        if self.path == "/control":
            self.control(body)
        elif self.path == "/v1/chat/completions":
            self.chat(body)
        else:
            self.respond(404, {"error": "not_found"})

    def control(self, body):
        mode = body.get("mode")
        retry_after = body.get("retry_after", 2)
        delay = body.get("delay", 12)
        if (not isinstance(mode, str) or mode not in MODES or type(retry_after) is not int
                or not 0 <= retry_after <= 1800 or type(delay) not in (int, float) or not 0 <= delay <= 45):
            self.respond(400, {"error": "invalid_control"})
            return
        with LOCK:
            STATE.update(mode=mode, retry_after=retry_after, delay=delay)
            state = STATE.copy()
        self.respond(200, state)

    def chat(self, body):
        if self.headers.get("Authorization") != "Bearer demo-only":
            self.respond(401, {"error": {"code": "invalid_api_key"}})
            return
        try:
            content = body["messages"][-1]["content"]
            markers = [int(number) for number in re.findall(r"(?m)^\[(\d+)\] ", content)]
        except (KeyError, IndexError, TypeError):
            self.respond(400, {"error": "invalid_messages"})
            return
        with LOCK:
            STATE.update(calls=STATE["calls"] + 1, last_source_markers=markers, last_stream=body.get("stream") is True)
            mode, retry_after, delay = (STATE[key] for key in ("mode", "retry_after", "delay"))

        if mode == "timeout":
            time.sleep(delay)
        if mode == "rate_limit":
            self.respond(429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": retry_after})
        elif mode == "quota":
            self.respond(429, {"error": {"code": "insufficient_quota"}}, {"Retry-After": retry_after})
        elif mode == "unavailable":
            self.respond(503, {"error": {"code": "service_unavailable"}})
        elif mode == "broken":
            self.respond(200, {"choices": []})
        else:
            answer = "Имитатор подтвердил получение синтетического фрагмента [1]" if markers else ""
            self.respond(200, {"choices": [{"message": {"content": answer}}]})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8000"))), Handler).serve_forever()
