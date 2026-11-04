import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sdk"))

import rpa_agent_block as block  # noqa: E402

FAKE = os.path.join(ROOT, "tests", "fake_mcp_server.py")


class StubGateway(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization")
        if auth != "Bearer tok":
            payload = {"error": "unauthorized"}
            code = 401
        else:
            payload = {"status": "succeeded", "agent": body.get("agent"),
                       "text": "网关答案：" + body.get("prompt", "")}
            code = 200
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class BlockTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        os.environ.pop("AGENTRPA_GATEWAY", None)
        os.environ["AGENTRPA_CODEX_CMD"] = sys.executable
        os.environ["AGENTRPA_CODEX_ARGS"] = FAKE

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def test_stdio_mode(self):
        self.assertEqual(block.ask_agent("一加一"), "答案是：一加一")

    def test_gateway_mode(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubGateway)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        os.environ["AGENTRPA_GATEWAY"] = f"http://127.0.0.1:{httpd.server_address[1]}"
        os.environ["AGENTRPA_TOKEN"] = "tok"
        try:
            self.assertEqual(block.ask_agent("发票"), "网关答案：发票")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_gateway_bad_token_raises(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubGateway)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        os.environ["AGENTRPA_GATEWAY"] = f"http://127.0.0.1:{httpd.server_address[1]}"
        os.environ["AGENTRPA_TOKEN"] = "wrong"
        try:
            with self.assertRaises(RuntimeError):
                block.ask_agent("x")
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
