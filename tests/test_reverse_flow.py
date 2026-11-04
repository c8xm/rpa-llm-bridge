"""反方向全链路：MCP 服务 → 假影刀开放 API（无需真实影刀环境）。"""
import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from agentrpa_bridge.mcp_client import McpStdioClient  # noqa: E402
import mock_yingdao_server  # noqa: E402

SERVER = os.path.join(ROOT, "agentrpa_bridge", "yingdao_mcp_server.py")


def data(out):
    return json.loads(out["text"])["data"]


class ReverseFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = mock_yingdao_server.serve(0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.client = McpStdioClient(sys.executable, [SERVER], timeout=30, env={
            "YINGDAO_API_BASE": cls.base,
            "YINGDAO_ACCESS_KEY_ID": "ak_mock",
            "YINGDAO_ACCESS_KEY_SECRET": "sk_mock",
        })
        cls.client.start()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_list_apps(self):
        apps = data(self.client.call_tool("rpa_list_apps", {}))["apps"]
        self.assertTrue(any(a["id"] == "app_invoice" for a in apps))

    def test_filter_apps(self):
        apps = data(self.client.call_tool("rpa_list_apps", {"keyword": "对账"}))["apps"]
        self.assertEqual([a["id"] for a in apps], ["app_reconcile"])

    def test_params_and_job_lifecycle(self):
        params = data(self.client.call_tool("rpa_app_params", {"app_id": "app_invoice"}))
        self.assertEqual([p["name"] for p in params["params"]], ["月份", "税号"])

        started = data(self.client.call_tool("rpa_start_job", {
            "app_id": "app_invoice", "params": {"月份": "2026-11", "税号": "9131"}}))
        job_id = started["jobId"]
        self.assertTrue(job_id.startswith("job_"))

        detail = {}
        for _ in range(20):
            detail = data(self.client.call_tool("rpa_query_job", {"job_id": job_id}))
            if detail["status"] == "success":
                break
            time.sleep(0.3)
        self.assertEqual(detail["status"], "success")
        self.assertEqual(detail["output"]["rows"], 128)
        self.assertIn("2026-11", detail["output"]["params"]["月份"])

    def test_unknown_app_reports_error(self):
        out = self.client.call_tool("rpa_start_job", {"app_id": "app_ghost"})
        self.assertTrue(out["is_error"] or "不存在" in out["text"])

    def test_clients(self):
        clients = data(self.client.call_tool("rpa_clients", {}))["clients"]
        self.assertTrue(all(c["online"] for c in clients))


if __name__ == "__main__":
    unittest.main()
