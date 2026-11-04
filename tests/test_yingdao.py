import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentrpa_bridge.mcp_client import McpStdioClient
from agentrpa_bridge.yingdao_api import (YingDaoError, YingDaoOpenApi,
                                        _split_args)

SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "agentrpa_bridge", "yingdao_mcp_server.py")
SEEN = []


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        SEEN.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": raw})
        if "broken" in self.path:
            self._send({"code": 1001, "message": "signature invalid"})
        elif self.path.endswith("/app/list"):
            self._send({"code": 0, "data": {"apps": [{"id": "app_1", "name": "发票下载"}]}})
        elif self.path.endswith("/job/start"):
            self._send({"code": 0, "data": {"jobId": "job_9"}})
        else:
            self._send({"code": 0, "data": {}})

    def do_GET(self):
        SEEN.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": b""})
        if "/job/detail" in self.path:
            self._send({"code": 0, "data": {"jobId": "job_9", "status": "success",
                                            "output": {"rows": 12}}})
        else:
            self._send({"code": 0, "data": {}})


class YingDaoApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def api(self):
        return YingDaoOpenApi("ak_test", "sk_test", base_url=self.base, timeout=10)

    def test_list_apps_and_signature_header(self):
        SEEN.clear()
        data = self.api().list_apps("发票")
        self.assertEqual(data["data"]["apps"][0]["id"], "app_1")
        sent = SEEN[-1]
        self.assertEqual(sent["headers"].get("x-access-key-id"), "ak_test")
        self.assertTrue(sent["headers"].get("x-signature"))
        self.assertIn("发票", json.loads(sent["body"].decode("utf-8"))["keyword"])

    def test_start_job_and_query(self):
        data = self.api().start_job("app_1", {"月份": "2026-10"})
        self.assertEqual(data["data"]["jobId"], "job_9")
        detail = self.api().query_job("job_9")
        self.assertEqual(detail["data"]["status"], "success")

    def test_business_error_raises(self):
        api = self.api()
        api.paths["app_list"] = ("POST", "/oapi/dispatch/v1/app/broken")
        with self.assertRaises(YingDaoError) as ctx:
            api.list_apps()
        self.assertIn("signature invalid", str(ctx.exception))


class YingDaoMcpServerTest(unittest.TestCase):
    def test_tools_over_stdio(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        client = McpStdioClient(sys.executable, [SERVER], timeout=20, env={
            "YINGDAO_API_BASE": base,
            "YINGDAO_ACCESS_KEY_ID": "ak_test",
            "YINGDAO_ACCESS_KEY_SECRET": "sk_test",
        })
        try:
            client.start()
            names = [t["name"] for t in client.list_tools()]
            self.assertIn("rpa_list_apps", names)
            self.assertIn("rpa_start_job", names)
            out = client.call_tool("rpa_list_apps", {})
            self.assertFalse(out["is_error"])
            self.assertIn("app_1", out["text"])
            started = client.call_tool("rpa_start_job", {"app_id": "app_1",
                                                         "params": {"月份": "2026-10"}})
            self.assertIn("job_9", started["text"])
        finally:
            client.close()
            httpd.shutdown()
            httpd.server_close()


class SplitArgsTest(unittest.TestCase):
    def test_quoted_segments(self):
        self.assertEqual(_split_args('startapp --app "{app}" --params "{params_json}"'),
                         ["startapp", "--app", "{app}", "--params", "{params_json}"])


if __name__ == "__main__":
    unittest.main()
