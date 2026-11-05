"""跨进程/跨节点共享状态：SQLite 后端的限速与幂等。"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.gateway import GatewayConfig, create_server  # noqa: E402
from agentrpa_bridge.providers import AgentPool, AgentSpec  # noqa: E402
from agentrpa_bridge.store import MemoryStore, SQLiteStore, make_store  # noqa: E402

COUNTER = os.path.join(tempfile.mkdtemp(), "shared.txt")
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


class StoreSelectionTest(unittest.TestCase):
    def test_make_store(self):
        self.assertIsInstance(make_store("memory", "x.db", 60), MemoryStore)
        tmp = tempfile.mkdtemp()
        store = make_store("sqlite", os.path.join(tmp, "s.db"), 60)
        self.assertIsInstance(store, SQLiteStore)
        store.close()

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            make_store("redis", "x", 60)


class SQLiteRateLimitTest(unittest.TestCase):
    def test_two_instances_share_one_budget(self):
        path = os.path.join(tempfile.mkdtemp(), "rate.db")
        a = SQLiteStore(path)
        b = SQLiteStore(path)
        try:
            allowed = [a.allow("tenantA", limit=3) for _ in range(3)]
            self.assertEqual(allowed, [True, True, True])
            self.assertFalse(b.allow("tenantA", limit=3), "第二个实例没看到第一个实例的计数")
            self.assertTrue(b.allow("tenantB", limit=3), "不同租户不该互相影响")
        finally:
            a.close()
            b.close()

    def test_cross_process(self):
        path = os.path.join(tempfile.mkdtemp(), "proc.db")
        store = SQLiteStore(path)
        try:
            for _ in range(2):
                self.assertTrue(store.allow("t", limit=2))
            code = (
                "import sys; sys.path.insert(0, r'%s');"
                "from agentrpa_bridge.store import SQLiteStore;"
                "s = SQLiteStore(r'%s');"
                "print('ALLOWED' if s.allow('t', limit=2) else 'DENIED');"
                "s.close()" % (ROOT, path)
            )
            out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                 text=True, encoding="utf-8", timeout=30)
            self.assertIn("DENIED", out.stdout, out.stderr)
        finally:
            store.close()


class SQLiteIdempotencyTest(unittest.TestCase):
    def test_inflight_then_done_across_instances(self):
        path = os.path.join(tempfile.mkdtemp(), "idem.db")
        a = SQLiteStore(path, ttl=60)
        b = SQLiteStore(path, ttl=60)
        try:
            self.assertEqual(a.idem_begin("k1"), ("new", None, 0))
            verdict, _, _ = b.idem_begin("k1")
            self.assertEqual(verdict, "inflight")
            a.idem_finish("k1", 200, {"text": "done"})
            verdict, payload, code = b.idem_begin("k1")
            self.assertEqual((verdict, payload, code), ("done", {"text": "done"}, 200))
        finally:
            a.close()
            b.close()

    def test_expired_entry_is_reusable(self):
        path = os.path.join(tempfile.mkdtemp(), "idem2.db")
        store = SQLiteStore(path, ttl=1)
        try:
            store.idem_begin("k")
            store.idem_finish("k", 200, {"text": "old"})
            time.sleep(1.2)
            self.assertEqual(store.idem_begin("k")[0], "new")
        finally:
            store.close()


class TwoGatewayTest(unittest.TestCase):
    """两个网关进程共用一个 SQLite 库：同一个 key 只跑一次 agent。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "shared.db")
        if os.path.exists(COUNTER):
            os.remove(COUNTER)
        self.servers = []

    def tearDown(self):
        for httpd, pool in self.servers:
            httpd.shutdown()
            httpd.server_close()
            pool.close()

    def boot(self):
        cfg = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok": "t1"},
                            allow_anon_localhost=False, rate_limit_per_min=1000,
                            audit_dir=self.tmp, idempotency_ttl=60,
                            state_backend="sqlite", state_path=self.db)
        pool = AgentPool([AgentSpec(name="count", kind="cli", command=sys.executable,
                                    args=["-c", SCRIPT], timeout=30)], idle_ttl=0)
        httpd = create_server(cfg, pool)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.15)
        self.servers.append((httpd, pool))
        return httpd.server_address[1]

    def invoke(self, port, key, prompt="hi"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/invoke",
            data=json.dumps({"agent": "count", "prompt": prompt}).encode(),
            method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer tok", "Idempotency-Key": key})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def test_same_key_on_two_nodes_runs_once(self):
        p1, p2 = self.boot(), self.boot()
        first = self.invoke(p1, "order-9527")
        second = self.invoke(p2, "order-9527")     # 落到另一个"节点"
        self.assertEqual(first["text"], "1")
        self.assertEqual(second["text"], "1")
        self.assertTrue(second.get("idempotent_replay"))
        self.assertEqual(calls(), 1, "跨节点幂等失效，agent 被跑了两次")


if __name__ == "__main__":
    unittest.main()
