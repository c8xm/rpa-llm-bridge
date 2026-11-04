import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.providers import (AgentError, AgentPool, AgentSpec,
                                       CliProvider, McpAgentProvider,
                                       OpenAICompatProvider, redact)

FAKE = os.path.join(ROOT, "tests", "fake_mcp_server.py")


class CliProviderTest(unittest.TestCase):
    def test_stdin_prompt_and_plain_stdout(self):
        spec = AgentSpec(name="c", kind="cli", command=sys.executable,
                         args=["-c", "import sys;print(sys.stdin.read().strip().upper())"])
        out = CliProvider(spec).invoke("abc")
        self.assertEqual(out["text"], "ABC")

    def test_json_result_unwrapped(self):
        spec = AgentSpec(name="c", kind="cli", command=sys.executable,
                         args=["-c", "import json;print(json.dumps({'result':'ok'}))"])
        self.assertEqual(CliProvider(spec).invoke("x")["text"], "ok")

    def test_nonzero_exit_raises(self):
        spec = AgentSpec(name="c", kind="cli", command=sys.executable,
                         args=["-c", "import sys;sys.stderr.write('boom');sys.exit(2)"])
        with self.assertRaises(AgentError) as ctx:
            CliProvider(spec).invoke("x")
        self.assertIn("2", str(ctx.exception))

    def test_timeout_raises(self):
        spec = AgentSpec(name="c", kind="cli", command=sys.executable,
                         args=["-c", "import time;time.sleep(5)"], timeout=0.5)
        with self.assertRaises(AgentError):
            CliProvider(spec).invoke("x")


class McpProviderTest(unittest.TestCase):
    def test_auto_pick_tool_and_call(self):
        spec = AgentSpec(name="fake", kind="mcp", command=sys.executable,
                         args=[FAKE], timeout=15)
        provider = McpAgentProvider(spec)
        try:
            out = provider.invoke("问题")
            self.assertEqual(spec.tool, "agent")
            self.assertEqual(out["text"], "答案是：问题")
            self.assertEqual(provider.health()["session"], "alive")
            # 会话复用：第二次不再冷启动
            self.assertEqual(provider.invoke("再问")["text"], "答案是：再问")
        finally:
            provider.close()

    def test_generic_arg_template(self):
        spec = AgentSpec(name="fake", kind="mcp", command=sys.executable, args=[FAKE],
                         tool="echo", arg_template={"prompt": "Q:{prompt}"}, timeout=15)
        provider = McpAgentProvider(spec)
        try:
            self.assertEqual(provider.invoke("hi")["text"], "Q:hi")
        finally:
            provider.close()

    def test_recovers_from_dead_session(self):
        spec = AgentSpec(name="fake", kind="mcp", command=sys.executable,
                         args=[FAKE], timeout=15)
        provider = McpAgentProvider(spec)
        try:
            provider.invoke("first")
            provider._client.proc.kill()   # 模拟进程崩溃
            provider._client.proc.wait(timeout=5)
            self.assertEqual(provider.invoke("second")["text"], "答案是：second")
        finally:
            provider.close()


class StubChat(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.headers.get("Authorization") != "Bearer sk-test":
            payload, code = {"error": "bad key"}, 401
        else:
            payload, code = {"model": body["model"],
                             "choices": [{"message": {"content": "模型答案"}}],
                             "usage": {"total_tokens": 12}}, 200
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class OpenAIProviderTest(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubChat)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_invoke(self):
        spec = AgentSpec(name="llm", kind="openai", base_url=self.base,
                         api_key="sk-test", model="deepseek-chat")
        out = OpenAICompatProvider(spec).invoke("你好")
        self.assertEqual(out["text"], "模型答案")
        self.assertEqual(out["raw"]["usage"]["total_tokens"], 12)

    def test_missing_key(self):
        spec = AgentSpec(name="llm", kind="openai", base_url=self.base, api_key="")
        saved = os.environ.pop("AGENTRPA_OPENAI_KEY", None)
        try:
            with self.assertRaises(AgentError):
                OpenAICompatProvider(spec).invoke("x")
        finally:
            if saved:
                os.environ["AGENTRPA_OPENAI_KEY"] = saved


class PoolTest(unittest.TestCase):
    def test_unknown_agent(self):
        pool = AgentPool([], idle_ttl=0)
        with self.assertRaises(AgentError):
            pool.invoke("nope", "x")

    def test_names_and_snapshot(self):
        pool = AgentPool([AgentSpec(name="a", kind="cli", command=sys.executable,
                                    args=["-c", "print(1)"])], idle_ttl=0)
        try:
            self.assertEqual(pool.names(), ["a"])
            self.assertEqual(pool.snapshot()[0]["name"], "a")
        finally:
            pool.close()


class RedactTest(unittest.TestCase):
    def test_masks_tokens(self):
        self.assertIn("[REDACTED]", redact("key sk-abcdefgh12345678 end"))
        self.assertIn("[REDACTED]", redact("Authorization: Bearer abcdefghijklmnop"))


if __name__ == "__main__":
    unittest.main()
