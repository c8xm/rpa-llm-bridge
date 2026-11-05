"""作业事件持久化：进程重启后 SSE 仍能回放（SQLite 后端）。"""
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

ECHO = "import sys; print('ANSWER:' + sys.stdin.read().strip())"


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "state.db")
        self.servers = []

    def tearDown(self):
        for httpd in self.servers:
            try:
                httpd.shutdown()
                httpd.server_close()
                httpd.state.close()
            except Exception:
                pass

    def boot(self, backend="sqlite"):
        cfg = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok": "t1"},
                            allow_anon_localhost=False, rate_limit_per_min=1000,
                            audit_dir=self.tmp, state_backend=backend,
                            state_path=self.db)
        pool = AgentPool([AgentSpec(name="echo", kind="cli", command=sys.executable,
                                    args=["-c", ECHO], timeout=20)], idle_ttl=0)
        httpd = create_server(cfg, pool)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.15)
        self.servers.append(httpd)
        return httpd

    def post_job(self, port):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/jobs",
            data=json.dumps({"agent": "echo", "prompt": "持久化"}).encode(),
            method="POST", headers={"Content-Type": "application/json",
                                    "Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())["run_id"]

    def wait_done(self, port, run_id, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/jobs/{run_id}",
                                         headers={"Authorization": "Bearer tok"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                job = json.loads(resp.read().decode())
            if job.get("done") or job.get("status") in ("succeeded", "failed"):
                return job
            time.sleep(0.2)
        raise AssertionError("作业未在预期时间内结束")

    def read_sse(self, port, run_id, since=0, limit=60):
        url = f"http://127.0.0.1:{port}/v1/jobs/{run_id}/events?since={since}"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer tok"})
        frames = []
        with urllib.request.urlopen(req, timeout=30) as resp:
            while len(frames) < limit:
                line = resp.readline().decode("utf-8", "replace")
                if not line:
                    break
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
                if line.strip() == "event: end":
                    break
        return frames

    def test_restart_replays_events(self):
        first = self.boot()
        run_id = self.post_job(first.server_address[1])
        self.wait_done(first.server_address[1], run_id)
        before = self.read_sse(first.server_address[1], run_id)
        stages_before = [f.get("stage") for f in before]
        self.assertIn("queued", stages_before)
        self.assertIn("succeeded", stages_before)

        first.shutdown()
        first.server_close()
        first.state.close()
        self.servers.remove(first)

        second = self.boot()                       # 全新进程视角，内存里没有这个作业
        port = second.server_address[1]
        after = self.read_sse(port, run_id)
        stages_after = [f.get("stage") for f in after]
        self.assertEqual(stages_before, stages_after,
                         "重启后回放的事件序列应与重启前一致")

        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/jobs/{run_id}",
                                     headers={"Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            job = json.loads(resp.read().decode())
        self.assertTrue(job.get("persisted"))
        self.assertEqual(job["status"], "recovered")

    def test_memory_backend_does_not_persist(self):
        first = self.boot(backend="memory")
        run_id = self.post_job(first.server_address[1])
        self.wait_done(first.server_address[1], run_id)
        first.shutdown()
        first.server_close()
        first.state.close()
        self.servers.remove(first)

        second = self.boot(backend="memory")
        req = urllib.request.Request(
            f"http://127.0.0.1:{second.server_address[1]}/v1/jobs/{run_id}",
            headers={"Authorization": "Bearer tok"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 404)
        ctx.exception.close()

    def test_since_resume_from_all_events(self):
        httpd = self.boot()
        port = httpd.server_address[1]
        run_id = self.post_job(port)
        self.wait_done(port, run_id)
        all_events = self.read_sse(port, run_id)
        cut = all_events[1]["seq"]
        rest = self.read_sse(port, run_id, since=cut)
        self.assertTrue(all(e["seq"] > cut for e in rest))


if __name__ == "__main__":
    unittest.main()
