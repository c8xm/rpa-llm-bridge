"""企业护栏：白名单 / 审批 / 并发 / 幂等，含 MCP 层端到端。"""
import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from agentrpa_bridge.mcp_client import McpStdioClient  # noqa: E402
from agentrpa_bridge.policy import (GuardConfig, PolicyDenied,  # noqa: E402
                                    RpaGuard)
import mock_yingdao_server  # noqa: E402

SERVER = os.path.join(ROOT, "agentrpa_bridge", "yingdao_mcp_server.py")


class GuardUnitTest(unittest.TestCase):
    def guard(self, **kw):
        cfg = GuardConfig(max_per_app=1, idem_ttl=60, **kw)
        clock = {"t": 1000.0}
        g = RpaGuard(cfg, clock=lambda: clock["t"])
        return g, clock

    def test_whitelist(self):
        g, _ = self.guard(allowed_apps=["app_a"])
        g.check_app("app_a")
        with self.assertRaises(PolicyDenied) as ctx:
            g.check_app("app_b")
        self.assertEqual(ctx.exception.code, "app_not_allowed")
        self.assertEqual(g.snapshot()["stats"]["denied"], 1)

    def test_empty_whitelist_allows_all(self):
        g, _ = self.guard()
        g.check_app("anything")
        self.assertFalse(g.snapshot()["whitelist_enforced"])

    def test_approval(self):
        g, _ = self.guard(approval_apps=["app_pay"])
        self.assertTrue(g.requires_approval("app_pay"))
        with self.assertRaises(PolicyDenied) as ctx:
            g.check_approval("app_pay", "")
        self.assertEqual(ctx.exception.code, "requires_approval")
        g.check_approval("app_pay", "E1024")
        self.assertEqual(g.snapshot()["stats"]["approved"], 1)

    def test_idempotency_and_expiry(self):
        g, clock = self.guard()
        key = g.idem_key("app_a", {"月份": "2026-11", "税号": "9131"})
        self.assertEqual(key, g.idem_key("app_a", {"税号": "9131", "月份": "2026-11"}))
        self.assertIsNone(g.lookup(key))
        g.commit(key, "job_1", "app_a")
        self.assertEqual(g.lookup(key), "job_1")
        clock["t"] += 61
        self.assertIsNone(g.lookup(key))

    def test_concurrency(self):
        g, _ = self.guard()
        g.acquire("app_a")
        with self.assertRaises(PolicyDenied) as ctx:
            g.acquire("app_a")
        self.assertEqual(ctx.exception.code, "busy")
        g.commit("k1", "job_1", "app_a")
        g.observe("job_1", "running")
        self.assertEqual(g.snapshot()["inflight"], {"app_a": 1})
        g.observe("job_1", "success")
        self.assertEqual(g.snapshot()["inflight"], {})

    def test_release_is_safe(self):
        g, _ = self.guard()
        g.release("never-acquired")
        self.assertEqual(g.snapshot()["inflight"], {})


class GuardMcpEndToEndTest(unittest.TestCase):
    """通过真实 MCP 进程验证护栏返回值。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = mock_yingdao_server.serve(0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.env = {
            "YINGDAO_API_BASE": cls.base,
            "YINGDAO_ACCESS_KEY_ID": "ak_mock",
            "YINGDAO_ACCESS_KEY_SECRET": "sk_mock",
            "YINGDAO_ALLOWED_APPS": "app_invoice,app_reconcile",
            "YINGDAO_APPROVAL_APPS": "app_reconcile",
            "YINGDAO_MAX_PER_APP": "1",
            "YINGDAO_IDEMPOTENCY_TTL": "60",
        }
        cls.client = McpStdioClient(sys.executable, [SERVER], timeout=30, env=cls.env)
        cls.client.start()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_policy_tool_exposes_config(self):
        snap = json.loads(self.client.call_tool("rpa_policy", {})["text"])
        self.assertTrue(snap["whitelist_enforced"])
        self.assertEqual(snap["approval_apps"], ["app_reconcile"])
        self.assertEqual(snap["max_per_app"], 1)

    def test_non_whitelisted_app_denied(self):
        out = self.client.call_tool("rpa_start_job", {"app_id": "app_ghost"})
        body = json.loads(out["text"])
        self.assertTrue(out["is_error"])
        self.assertEqual(body["error"], "app_not_allowed")

    def test_approval_required(self):
        out = self.client.call_tool("rpa_start_job", {"app_id": "app_reconcile",
                                                      "params": {"账户": "6222"}})
        self.assertTrue(out["is_error"])
        self.assertEqual(json.loads(out["text"])["error"], "requires_approval")

    def test_approved_job_runs_and_dedupes(self):
        args = {"app_id": "app_reconcile", "params": {"账户": "6222"},
                "approved_by": "工单-88123"}
        first = json.loads(self.client.call_tool("rpa_start_job", args)["text"])
        job_id = first["data"]["jobId"]

        # 同参数再提一次：返回同一个 jobId，标记 deduplicated
        second = json.loads(self.client.call_tool("rpa_start_job", args)["text"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["jobId"], job_id)

        # 轮询到终态，槽位释放
        for _ in range(20):
            detail = json.loads(self.client.call_tool("rpa_query_job",
                                                      {"job_id": job_id})["text"])["data"]
            if detail["status"] == "success":
                break
            time.sleep(0.3)
        self.assertEqual(detail["status"], "success")
        snap = json.loads(self.client.call_tool("rpa_policy", {})["text"])
        self.assertEqual(snap["inflight"], {})
        self.assertGreaterEqual(snap["stats"]["deduplicated"], 1)

    def test_concurrency_busy(self):
        # 先占住唯一槽位（不同参数，避开幂等）
        first = json.loads(self.client.call_tool("rpa_start_job", {
            "app_id": "app_invoice", "params": {"月份": "2026-10", "税号": "A"}})["text"])
        self.assertIn("jobId", first["data"])
        out = self.client.call_tool("rpa_start_job", {
            "app_id": "app_invoice", "params": {"月份": "2026-12", "税号": "B"}})
        self.assertTrue(out["is_error"])
        self.assertEqual(json.loads(out["text"])["error"], "busy")


if __name__ == "__main__":
    unittest.main()
