"""SSE 进度流：provider 事件解析、作业事件缓冲、端点推送与续传。"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.gateway import GatewayConfig, create_server  # noqa: E402
from agentrpa_bridge.providers import AgentPool, AgentSpec, CliProvider  # noqa: E402

FAKE = os.path.join(ROOT, "tests", "fake_stream_agent.py")


class CliStreamTest(unittest.TestCase):
    def test_events_are_parsed(self):
        spec = AgentSpec(name="dsh", kind="cli", command=sys.executable,
                         args=[FAKE, "--json"], timeout=30)
        events = []
        result = CliProvider(spec).invoke("核对账单", on_event=events.append)
        stages = [e["stage"] for e in events]
        self.assertEqual(stages, ["session", "text", "tool_call", "final"])
        self.assertIn("tool_call", stages)
        self.assertEqual(result["text"], "答案：核对账单")
        self.assertTrue(result["raw"]["streamed"])

    def test_without_json_flag_emits_single_event(self):
        spec = AgentSpec(name="plain", kind="cli", command=sys.executable,
                         args=["-c", "import sys;print(sys.stdin.read().strip())"], timeout=20)
        events = []
        CliProvider(spec).invoke("hi", on_event=events.append)
        self.assertEqual([e["stage"] for e in events], ["backend_running"])


class JobStreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok": "t1"},
                            allow_anon_localhost=False, rate_limit_per_min=1000,
                            audit_dir=self.tmp)
        pool = AgentPool([AgentSpec(name="dsh", kind="cli", command=sys.executable,
                                    args=[FAKE, "--json"], timeout=30)], idle_ttl=0)
        self.httpd = create_server(cfg, pool)
        self.pool = pool
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        time.sleep(0.15)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.pool.close()

    def post_job(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/jobs",
            data=json.dumps({"agent": "dsh", "prompt": "运行测试"}).encode(),
            method="POST", headers={"Content-Type": "application/json",
                                    "Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())["run_id"]

    def read_sse(self, run_id, since=None, limit=40):
        url = f"http://127.0.0.1:{self.port}/v1/jobs/{run_id}/events"
        if since is not None:
            url += f"?since={since}"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer tok"})
        frames = []
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream; charset=utf-8")
            while len(frames) < limit:
                line = resp.readline().decode("utf-8", "replace")
                if not line:
                    break
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
                if line.strip() == "event: end":
                    break
        return frames

    def test_stream_until_end(self):
        run_id = self.post_job()
        frames = self.read_sse(run_id)
        stages = [f.get("stage") for f in frames]
        for expected in ("queued", "running", "text", "final", "succeeded"):
            self.assertIn(expected, stages, f"缺事件 {expected}；实际 {stages}")
        seqs = [f["seq"] for f in frames]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_resume_with_since(self):
        run_id = self.post_job()
        time.sleep(1.5)
        first = self.read_sse(run_id)
        self.assertGreater(len(first), 2)
        cut = first[1]["seq"]
        rest = self.read_sse(run_id, since=cut)
        self.assertTrue(all(f["seq"] > cut for f in rest))
        self.assertEqual([f["seq"] for f in first if f["seq"] > cut],
                         [f["seq"] for f in rest])

    def test_sdk_stream_helper(self):
        sys.path.insert(0, os.path.join(ROOT, "sdk"))
        import rpa_agent_block as block  # noqa: PLC0415

        events = []
        text = block.ask_agent_stream(
            "运行测试", on_event=events.append, agent="dsh", timeout=30,
            gateway=f"http://127.0.0.1:{self.port}", token="tok")
        self.assertEqual(text, "答案：运行测试")
        stages = [e.get("stage") for e in events]
        self.assertIn("queued", stages)
        self.assertIn("final", stages)


if __name__ == "__main__":
    unittest.main()
