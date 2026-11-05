"""网关幂等：同一个 Idempotency-Key 只能让 agent 跑一次。"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.gateway import GatewayConfig, create_server  # noqa: E402
from agentrpa_bridge.providers import AgentPool, AgentSpec  # noqa: E402

COUNTER = os.path.join(tempfile.mkdtemp(), "calls.txt")
SCRIPT = ("import sys;"
          f"p=r'{COUNTER}';"
          "open(p,'a').write('x');"
          "print(open(p).read().count('x'))")


def calls() -> int:
    try:
        with open(COUNTER, encoding="utf-8") as fh:
            return fh.read().count("x")
    except FileNotFoundError:
        return 0


class IdempotencyTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(COUNTER):
            os.remove(COUNTER)
        self.cfg = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok": "t1"},
                                 allow_anon_localhost=False, rate_limit_per_min=1000,
                                 audit_dir=tempfile.mkdtemp(),
                                 idempotency_ttl=60)
        self.pool = AgentPool([AgentSpec(name="count", kind="cli", command=sys.executable,
                                         args=["-c", SCRIPT], timeout=30)], idle_ttl=0)
        self.httpd = create_server(self.cfg, self.pool)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        time.sleep(0.15)

    def tearDown(self):
        self.httpd.shutdown()
        self.pool.close()
        self.httpd.server_close()

    def invoke(self, key=None, prompt="hello"):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer tok"}
        if key:
            headers["Idempotency-Key"] = key
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/invoke",
            data=json.dumps({"agent": "count", "prompt": prompt}).encode(),
            method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_same_key_runs_once(self):
        code1, first = self.invoke("order-88123")
        code2, second = self.invoke("order-88123")
        self.assertEqual(code1, 200)
        self.assertEqual(code2, 200)
        self.assertEqual(first["text"], "1")
        self.assertEqual(second["text"], "1")
        self.assertTrue(second.get("idempotent_replay"))
        self.assertEqual(calls(), 1)

    def test_different_keys_run_separately(self):
        self.invoke("order-A")
        self.invoke("order-B")
        self.assertEqual(calls(), 2)

    def test_without_key_runs_every_time(self):
        self.invoke()
        self.invoke()
        self.assertEqual(calls(), 2)

    def test_concurrent_same_key_runs_once(self):
        results = []

        def worker():
            try:
                results.append(self.invoke("order-C")[1])
            except Exception as exc:  # noqa: BLE001
                results.append({"error": repr(exc)})

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(calls(), 1)
        self.assertEqual(len(results), 3)
        replays = [r for r in results if r.get("idempotent_replay")]
        self.assertGreaterEqual(len(replays), 2)
        self.assertTrue(all(r.get("status") == "succeeded" for r in results))


if __name__ == "__main__":
    unittest.main()
