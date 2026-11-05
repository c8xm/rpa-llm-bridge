"""Redis 后端：限速、幂等、作业事件（对着假 Redis 跑）。"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.store import RedisStore, make_store  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402


class RedisStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeRedis().start()
        cls.url = f"redis://127.0.0.1:{cls.server.port}/0"

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def store(self, ttl=60, url=None):
        return RedisStore(url or self.url, ttl=ttl)

    def test_url_parse(self):
        parsed = RedisStore._parse("rediss://:pwd@10.0.0.1:6380/3")
        self.assertEqual(parsed, {"host": "10.0.0.1", "port": 6380, "db": 3,
                                  "password": "pwd", "tls": True})

    def test_make_store_redis(self):
        self.assertEqual(make_store("redis", "x", 60, self.url).kind, "redis")

    def test_rate_limit_fixed_window(self):
        store = self.store()
        try:
            self.assertTrue(store.allow("t1", limit=2))
            self.assertTrue(store.allow("t1", limit=2))
            self.assertFalse(store.allow("t1", limit=2))
            self.assertTrue(store.allow("t2", limit=1))
        finally:
            store.close()

    def test_idempotency_lifecycle(self):
        store = self.store()
        other = self.store()
        try:
            self.assertEqual(store.idem_begin("k1"), ("new", None, 0))
            self.assertEqual(other.idem_begin("k1")[0], "inflight")
            store.idem_finish("k1", 200, {"text": "答案"})
            verdict, payload, code = other.idem_begin("k1")
            self.assertEqual((verdict, payload, code), ("done", {"text": "答案"}, 200))
            self.assertEqual(other.idem_get("k1")["payload"]["text"], "答案")
        finally:
            store.close()
            other.close()

    def test_job_events_and_done_flag(self):
        store = self.store()
        try:
            store.job_append("run1", {"seq": 1, "stage": "queued"})
            store.job_append("run1", {"seq": 2, "stage": "running"})
            events, done = store.job_read("run1")
            self.assertEqual([e["stage"] for e in events], ["queued", "running"])
            self.assertFalse(done)

            store.job_append("run1", {"seq": 3, "stage": "succeeded"})
            events, done = store.job_read("run1", after=1)
            self.assertEqual([e["seq"] for e in events], [2, 3])
            self.assertTrue(done)
        finally:
            store.close()

    def test_event_window_is_trimmed(self):
        store = self.store()
        try:
            for seq in range(1, 251):
                store.job_append("bulk", {"seq": seq, "stage": "text"})
            events, _ = store.job_read("bulk")
            self.assertEqual(len(events), 200)
            self.assertEqual(events[-1]["seq"], 250)
        finally:
            store.close()

    def test_payload_is_json_roundtrip(self):
        store = self.store()
        try:
            store.idem_finish("k2", 200, {"text": "中文", "n": 3})
            entry = store.idem_get("k2")
            self.assertEqual(json.loads(json.dumps(entry["payload"])), {"text": "中文", "n": 3})
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
