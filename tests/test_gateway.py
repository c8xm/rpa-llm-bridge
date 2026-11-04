import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentrpa_bridge.gateway import GatewayConfig, create_server
from agentrpa_bridge.providers import AgentPool, AgentSpec

ECHO_SCRIPT = "import sys; print('ANSWER:' + sys.stdin.read().strip())"


def make_pool(tmpdir):
    specs = [AgentSpec(name="echo", kind="cli", command=sys.executable,
                       args=["-c", ECHO_SCRIPT], timeout=20),
             AgentSpec(name="slow", kind="cli", command=sys.executable,
                       args=["-c", "import time;time.sleep(5)"], timeout=20)]
    return AgentPool(specs, idle_ttl=0)


class GatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_artifacts")
        os.makedirs(cls.tmp, exist_ok=True)
        cfg = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok-a": "tenantA"},
                            allow_anon_localhost=True, max_concurrency=2,
                            queue_timeout=2, rate_limit_per_min=1000,
                            audit_dir=cls.tmp, job_ttl=60)
        cls.httpd = create_server(cfg, make_pool(cls.tmp))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.state.pool.close()
        cls.httpd.server_close()

    # ---- helpers ----
    def _call(self, path, body=None, token=None, method=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    # ---- cases ----
    def test_healthz_public(self):
        code, body = self._call("/healthz")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")

    def test_auth_required_with_bad_token(self):
        # 用错 token 验证鉴权分支：不依赖 allow_anon 开关，避免跨用例改共享状态
        code, body = self._call("/v1/invoke", {"agent": "echo", "prompt": "x"},
                                token="not-a-real-token")
        self.assertEqual(code, 401)
        self.assertEqual(body["error"], "unauthorized")
        self.assertTrue(body["request_id"])

    def test_invoke_with_token(self):
        code, body = self._call("/v1/invoke", {"agent": "echo", "prompt": "你好",
                                               "meta": {"flow": "invoice_ocr"}},
                                token="tok-a")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["text"], "ANSWER:你好")
        self.assertTrue(body["request_id"])

    def test_localhost_anon_allowed(self):
        code, body = self._call("/v1/invoke", {"agent": "echo", "prompt": "anon"})
        self.assertEqual(code, 200)
        self.assertEqual(body["text"], "ANSWER:anon")

    def test_unknown_agent(self):
        code, body = self._call("/v1/invoke", {"agent": "ghost", "prompt": "x"}, token="tok-a")
        self.assertEqual(code, 502)
        self.assertIn("未知 agent", body["detail"])

    def test_async_job_roundtrip(self):
        code, body = self._call("/v1/jobs", {"agent": "echo", "prompt": "异步"}, token="tok-a")
        self.assertEqual(code, 202)
        run_id = body["run_id"]
        deadline = time.time() + 15
        while time.time() < deadline:
            code, job = self._call(f"/v1/jobs/{run_id}", token="tok-a")
            if job.get("status") in ("succeeded", "failed"):
                break
            time.sleep(0.2)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["text"], "ANSWER:异步")

    def test_rate_limit(self):
        # 单独起一个限额 3/min 的实例，避免污染主实例的桶
        cfg = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok-b": "tenantB"},
                            allow_anon_localhost=False, max_concurrency=2,
                            rate_limit_per_min=3, audit_dir=self.tmp)
        httpd = create_server(cfg, make_pool(self.tmp))
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        try:
            codes = []
            for _ in range(6):
                req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/agents")
                req.add_header("Authorization", "Bearer tok-b")
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        codes.append(resp.status)
                except urllib.error.HTTPError as exc:
                    codes.append(exc.code)
                    exc.close()
            self.assertIn(429, codes)
            self.assertEqual(codes.count(200), 3)
        finally:
            httpd.shutdown()
            httpd.state.pool.close()
            httpd.server_close()

    def test_metrics_and_agents(self):
        self._call("/v1/invoke", {"agent": "echo", "prompt": "m"}, token="tok-a")
        code, _ = self._call("/v1/agents", token="tok-a")
        self.assertEqual(code, 200)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/metrics", timeout=10) as resp:
            text = resp.read().decode("utf-8")
        self.assertIn("agentrpa_invoke_ok_total", text)

    def test_audit_written_and_redacted(self):
        self._call("/v1/invoke", {"agent": "echo", "prompt": "key=sk-abcdefgh12345678"},
                   token="tok-a")
        files = [f for f in os.listdir(self.tmp) if f.startswith("agent-")]
        self.assertTrue(files)
        blob = "".join(open(os.path.join(self.tmp, f), encoding="utf-8").read() for f in files)
        self.assertIn("invoice_ocr" if "invoice_ocr" in blob else "invoke", blob)
        self.assertNotIn("sk-abcdefgh12345678", blob)
        self.assertIn("[REDACTED]", blob)


if __name__ == "__main__":
    unittest.main()
