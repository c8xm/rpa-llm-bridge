"""v2.2.0 加固回归：鉴权、超限 body、超时重试、串行锁、PII 脱敏、槽位回收。"""
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
from agentrpa_bridge.policy import GuardConfig, PolicyDenied, RpaGuard  # noqa: E402
from agentrpa_bridge.providers import AgentError, AgentPool, AgentSpec, redact  # noqa: E402

FAKE = os.path.join(ROOT, "tests", "fake_mcp_server.py")
ECHO = "import sys; print('ANSWER:' + sys.stdin.read().strip())"


def spin(config=None, specs=None):
    cfg = config or GatewayConfig(host="127.0.0.1", port=0, tokens={},
                                  allow_anon_localhost=True, rate_limit_per_min=1000,
                                  audit_dir=tempfile.mkdtemp())
    pool = AgentPool(specs or [AgentSpec(name="echo", kind="cli", command=sys.executable,
                                         args=["-c", ECHO], timeout=20)], idle_ttl=0)
    httpd = create_server(cfg, pool)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.15)
    return httpd, pool, httpd.server_address[1]


def stop(httpd, pool):
    httpd.shutdown()
    pool.close()
    httpd.server_close()


class AuthHardeningTest(unittest.TestCase):
    """空 tokens 不能再是"任意 Bearer 都放行"。"""

    def test_bogus_token_rejected_when_no_tokens_configured(self):
        httpd, pool, port = spin()
        try:
            for token in ("completely-made-up", "sk-whatever"):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/invoke",
                    data=json.dumps({"agent": "echo", "prompt": "x"}).encode(),
                    method="POST", headers={"Content-Type": "application/json",
                                            "Authorization": f"Bearer {token}"})
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=10)
                self.assertEqual(ctx.exception.code, 401)
                ctx.exception.close()
        finally:
            stop(httpd, pool)

    def test_localhost_anon_still_works(self):
        httpd, pool, port = spin()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/invoke",
                data=json.dumps({"agent": "echo", "prompt": "anon"}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            stop(httpd, pool)

    def test_configured_token_ok_and_wrong_token_rejected(self):
        cfg = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok-a": "tenantA"},
                            allow_anon_localhost=False, rate_limit_per_min=1000,
                            audit_dir=tempfile.mkdtemp())
        httpd, pool, port = spin(cfg)
        try:
            def call(token):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/invoke",
                    data=json.dumps({"agent": "echo", "prompt": "x"}).encode(),
                    method="POST", headers={"Content-Type": "application/json",
                                            "Authorization": f"Bearer {token}"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return resp.status
                except urllib.error.HTTPError as exc:
                    exc.close()
                    return exc.code

            self.assertEqual(call("tok-a"), 200)
            self.assertEqual(call("tok-b"), 401)
        finally:
            stop(httpd, pool)


class PayloadLimitTest(unittest.TestCase):
    def test_oversize_returns_413_and_server_survives(self):
        httpd, pool, port = spin()
        try:
            import http.client
            big = json.dumps({"agent": "echo", "prompt": "x" * (3 * 1024 * 1024)}).encode()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/v1/invoke", body=big,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 413)
            resp.read()
            conn.close()

            # 服务端仍然健康：新连接照常 200
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/invoke",
                data=json.dumps({"agent": "echo", "prompt": "after"}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as ok:
                self.assertEqual(ok.status, 200)
        finally:
            stop(httpd, pool)


class TimeoutNoRetryTest(unittest.TestCase):
    """超时应立即返回，不能重试把预算翻倍。"""

    def test_timeout_is_not_retried(self):
        pool = AgentPool([AgentSpec(name="slow", kind="mcp", command=sys.executable,
                                    args=[FAKE], tool="sleep",
                                    arg_template={"seconds": "3"}, timeout=30)], idle_ttl=0)
        t0 = time.monotonic()
        try:
            with self.assertRaises(AgentError) as ctx:
                pool.invoke("slow", "x", timeout=0.6)
        finally:
            pool.close()
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0, f"耗时 {elapsed:.2f}s，疑似重试翻倍")
        self.assertIn("超时", str(ctx.exception))


class ProviderLockTest(unittest.TestCase):
    """锁等待要受调用方预算约束，慢请求不能拖死并发池。"""

    def test_second_caller_fails_fast_with_busy(self):
        pool = AgentPool([AgentSpec(name="fake", kind="mcp", command=sys.executable,
                                    args=[FAKE], tool="sleep",
                                    arg_template={"seconds": "2"}, timeout=30)], idle_ttl=0)
        result = {}

        def slow():
            try:
                pool.invoke("fake", "first", timeout=30)
            except AgentError:
                pass

        worker = threading.Thread(target=slow)
        worker.start()
        time.sleep(0.2)
        t0 = time.monotonic()
        try:
            with self.assertRaises(AgentError) as ctx:
                pool.invoke("fake", "second", timeout=0.4)
            result["elapsed"] = time.monotonic() - t0
            result["msg"] = str(ctx.exception)
        finally:
            worker.join()
            pool.close()
        self.assertLess(result["elapsed"], 1.5, "锁等待没受 timeout 约束")
        self.assertIn("busy", result["msg"])


class RedactionTest(unittest.TestCase):
    def test_pii_masked(self):
        text = ("联系人 13800138000 身份证 11010519491231002X "
                "卡号 6222021234567890123 邮箱 zhang.san@corp.example.com "
                "key sk-abcdefgh12345678")
        masked = redact(text)
        for raw in ("13800138000", "11010519491231002X", "6222021234567890123",
                    "zhang.san@corp.example.com", "sk-abcdefgh12345678"):
            self.assertNotIn(raw, masked)
        self.assertIn("[PHONE]", masked)
        self.assertIn("[ID_CARD]", masked)
        self.assertIn("[BANK_CARD]", masked)
        self.assertIn("[EMAIL]", masked)

    def test_short_numbers_not_masked(self):
        self.assertEqual(redact("订单 20261104 金额 11300"), "订单 20261104 金额 11300")

    def test_audit_prompt_head_redacted(self):
        httpd, pool, port = spin()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/invoke",
                data=json.dumps({"agent": "echo",
                                 "prompt": "客户 13800138000 要开发票"}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.assertEqual(resp.status, 200)
            audit_dir = httpd.state.config.audit_dir
            blob = ""
            for name in os.listdir(audit_dir):
                with open(os.path.join(audit_dir, name), encoding="utf-8") as fh:
                    blob += fh.read()
            self.assertNotIn("13800138000", blob)
            self.assertIn("[PHONE]", blob)
        finally:
            stop(httpd, pool)


class SlotLeakTest(unittest.TestCase):
    """没人轮询终态时，槽位要按 max_hold 回收。"""

    def test_expired_slot_is_reclaimed(self):
        clock = {"t": 1000.0}
        guard = RpaGuard(GuardConfig(max_per_app=1, max_hold=60, idem_ttl=0),
                         clock=lambda: clock["t"])
        guard.acquire("app_a")
        with self.assertRaises(PolicyDenied) as ctx:
            guard.acquire("app_a")
        self.assertEqual(ctx.exception.code, "busy")

        clock["t"] += 61
        guard.acquire("app_a")                      # 过期槽位被回收，重新可用
        snap = guard.snapshot()
        self.assertEqual(snap["inflight"], {"app_a": 1})
        self.assertGreaterEqual(snap["stats"]["expired"], 1)


if __name__ == "__main__":
    unittest.main()
