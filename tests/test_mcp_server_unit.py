"""反向 MCP 服务的进程内单元测试（stdio 主循环另由子进程用例覆盖）。"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.policy import GuardConfig, RpaGuard  # noqa: E402
from agentrpa_bridge.yingdao_mcp_server import RpaTools  # noqa: E402


class FakeApi:
    def __init__(self):
        self.calls = []

    def list_apps(self, keyword=""):
        self.calls.append(("list", keyword))
        return {"code": 0, "data": {"apps": [{"id": "app_1"}]}}

    def app_params(self, app_id):
        return {"code": 0, "data": {"appId": app_id, "params": []}}

    def start_job(self, app_id, params=None, client_id=""):
        self.calls.append(("start", app_id, params))
        return {"code": 0, "data": {"jobId": "job_1"}}

    def query_job(self, job_id):
        return {"code": 0, "data": {"jobId": job_id, "status": "success",
                                    "output": {"rows": 3}}}

    def clients(self):
        return {"code": 0, "data": {"clients": [{"id": "bot"}]}}


class LocalFake:
    def available(self):
        return False

    def run(self, app, params=None):
        return {"returncode": 0}


def make_tools(**cfg):
    tools = RpaTools(guard=RpaGuard(GuardConfig(idem_ttl=60, **cfg)))
    tools.api = FakeApi()
    tools.local = LocalFake()
    return tools


class RpaToolsTest(unittest.TestCase):
    def test_tool_list_hides_local_when_unavailable(self):
        names = [t["name"] for t in make_tools().tools()]
        self.assertNotIn("rpa_run_local", names)
        self.assertIn("rpa_policy", names)

    def test_list_and_params(self):
        tools = make_tools()
        self.assertIn("app_1", tools.call("rpa_list_apps", {"keyword": "发票"}))
        self.assertIn("app_1", tools.call("rpa_app_params", {"app_id": "app_1"}))

    def test_start_job_dedupes(self):
        tools = make_tools()
        first = json.loads(tools.call("rpa_start_job", {"app_id": "app_1",
                                                        "params": {"m": "1"}}))
        second = json.loads(tools.call("rpa_start_job", {"app_id": "app_1",
                                                         "params": {"m": "1"}}))
        self.assertEqual(first["data"]["jobId"], "job_1")
        self.assertTrue(second["deduplicated"])

    def test_query_releases_slot(self):
        tools = make_tools(max_per_app=1)
        tools.call("rpa_start_job", {"app_id": "app_1", "params": {"m": "9"}})
        self.assertEqual(tools.guard.snapshot()["inflight"], {"app_1": 1})
        tools.call("rpa_query_job", {"job_id": "job_1"})
        self.assertEqual(tools.guard.snapshot()["inflight"], {})

    def test_unknown_tool_raises(self):
        with self.assertRaises(KeyError):
            make_tools().call("nope", {})

    def test_policy_tool(self):
        payload = json.loads(make_tools(allowed_apps=["app_1"]).call("rpa_policy", {}))
        self.assertTrue(payload["whitelist_enforced"])

    def test_job_id_extraction(self):
        self.assertEqual(RpaTools._job_id({"data": {"jobId": "j"}}), "j")
        self.assertEqual(RpaTools._job_id({"jobId": "j2"}), "j2")
        self.assertEqual(RpaTools._job_id({"data": {}}), "")


if __name__ == "__main__":
    unittest.main()
