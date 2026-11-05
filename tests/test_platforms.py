import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import export_mcp_config as exporter  # noqa: E402

from agentrpa_bridge.providers import AgentError, AgentSpec, CliProvider, OpenAICompatProvider, default_specs

FAKE_DSH = os.path.join(ROOT, "tests", "fake_dsh.py")


class DshCliTest(unittest.TestCase):
    """DeepSeek Harness：dsh headless - ，任务走 stdin，stdout 即最终答案。"""

    def spec(self):
        return AgentSpec(name="dsh", kind="cli", command=sys.executable,
                         args=[FAKE_DSH, "headless", "-"], timeout=20)

    def test_prompt_via_stdin(self):
        out = CliProvider(self.spec()).invoke("统计这几个文件的差异")
        self.assertEqual(out["text"], "DSH:统计这几个文件的差异")

    def test_default_specs_contains_dsh_and_doubao(self):
        names = [s.name for s in default_specs()]
        self.assertIn("dsh", names)
        self.assertIn("doubao", names)
        dsh = next(s for s in default_specs() if s.name == "dsh")
        self.assertEqual(dsh.args[0], "headless")
        doubao = next(s for s in default_specs() if s.name == "doubao")
        self.assertIn("ark.cn-beijing.volces.com", doubao.base_url)


class StubArk(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        StubArk.seen.append({"path": self.path, "model": body.get("model"),
                             "auth": self.headers.get("Authorization")})
        payload = {"model": body.get("model"),
                   "choices": [{"message": {"content": "豆包答案"}}],
                   "usage": {"total_tokens": 8}}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class DoubaoTest(unittest.TestCase):
    """豆包 = 火山方舟 Ark，OpenAI 兼容；model 必须是接入点 id。"""

    def setUp(self):
        StubArk.seen.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubArk)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/v3"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_endpoint_id_passthrough(self):
        spec = AgentSpec(name="doubao", kind="openai", base_url=self.base,
                         model="ep-20261104-abc", api_key="ark-key")
        out = OpenAICompatProvider(spec).invoke("写一句通知")
        self.assertEqual(out["text"], "豆包答案")
        self.assertEqual(StubArk.seen[-1]["model"], "ep-20261104-abc")
        self.assertEqual(StubArk.seen[-1]["auth"], "Bearer ark-key")
        self.assertTrue(StubArk.seen[-1]["path"].endswith("/chat/completions"))

    def test_missing_model_is_explained(self):
        spec = AgentSpec(name="doubao", kind="openai", base_url=self.base,
                         model="", api_key="ark-key")
        with self.assertRaises(AgentError) as ctx:
            OpenAICompatProvider(spec).invoke("x")
        self.assertIn("缺少 model", str(ctx.exception))


class ExportConfigTest(unittest.TestCase):
    def test_merge_preserves_existing_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mcp.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {"wecom": {"command": "uvx", "args": ["wecom"]}},
                           "otherKey": 1}, fh)
            touched, backup = exporter.merge_json(path, exporter.server_defs())
            self.assertIn("yingdao", touched)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertIn("wecom", data["mcpServers"])
            self.assertIn("yingdao", data["mcpServers"])
            self.assertEqual(data["otherKey"], 1)
            self.assertTrue(backup and os.path.exists(backup))

    def test_merge_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mcp.json")
            exporter.merge_json(path, exporter.server_defs())
            touched, _ = exporter.merge_json(path, exporter.server_defs())
            self.assertEqual(touched, [])

    def test_v01_server_optional(self):
        self.assertNotIn("agentrpa", exporter.server_defs())
        self.assertIn("agentrpa", exporter.server_defs(v01_dir=r"D:\github\rpa-llm-bridge"))

    def test_toml_snippet_shape(self):
        toml = exporter.toml_snippet(exporter.server_defs())
        self.assertIn("[mcp_servers.yingdao]", toml)
        self.assertIn('command = "', toml)
        self.assertIn("enabled = true", toml)


class ShimTest(unittest.TestCase):
    """npm 装的 dsh/claude/codex 都是 .cmd shim，必须经 cmd /c 才能启动。"""

    def test_cmd_shim_wrapped(self):
        from agentrpa_bridge.mcp_client import argv_for
        if os.name == "nt":
            self.assertEqual(argv_for("nope.bat", ["x"])[:2], ["cmd", "/c"])
        self.assertEqual(argv_for(sys.executable, ["-V"])[0], sys.executable)


if __name__ == "__main__":
    unittest.main()
