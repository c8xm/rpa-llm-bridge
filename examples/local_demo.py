# -*- coding: utf-8 -*-
"""端到端演示：不需要装 codex/claude、不需要联网。

跑法：
    python examples/local_demo.py

它做三件事：
  1. 起一个假 agent（用 python 回显，替代 codex mcp-server）；
  2. 起网关，带 token 与审计；
  3. 用 RPA 侧 SDK 的同一套 HTTP 契约调两次（同步 + 异步），并打印审计落盘结果。
"""
import json
import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.gateway import GatewayConfig, create_server  # noqa: E402
from agentrpa_bridge.providers import AgentPool, AgentSpec  # noqa: E402

FAKE_AGENT = (
    "import sys,json;"
    "p=sys.stdin.read().strip();"
    "print(json.dumps({'result':'【假智能体】收到 %d 个字符，结论：%s' % (len(p), p[:40])},"
    " ensure_ascii=False))"
)


def main():
    audit_dir = os.path.join(ROOT, "artifacts", "audit")
    config = GatewayConfig(host="127.0.0.1", port=0, tokens={"tok-demo": "demo"},
                           allow_anon_localhost=False, max_concurrency=2,
                           audit_dir=audit_dir, rate_limit_per_min=100)
    pool = AgentPool([AgentSpec(name="codex", kind="cli", command=sys.executable,
                                args=["-c", FAKE_AGENT], timeout=30)], idle_ttl=0)
    httpd = create_server(config, pool)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    print(f"网关已启动: {base}  agents={pool.names()}")

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer tok-demo"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())

    code, body = post("/v1/invoke", {"agent": "codex", "prompt": "把 11300 含税金额拆出税额",
                                     "meta": {"flow": "invoice_tax"}})
    print(f"\n[同步] {code} status={body['status']} {body['duration_ms']}ms")
    print("   text =", body["text"])

    code, job = post("/v1/jobs", {"agent": "codex", "prompt": "异步任务示例",
                                  "meta": {"flow": "batch_report"}})
    run_id = job["run_id"]
    print(f"\n[异步] {code} run_id={run_id}")
    for _ in range(50):
        req = urllib.request.Request(f"{base}/v1/jobs/{run_id}",
                                     headers={"Authorization": "Bearer tok-demo"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            job = json.loads(resp.read().decode())
        if job["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    print(f"   最终状态={job['status']}  text={job.get('result', {}).get('text')}")

    req = urllib.request.Request(f"{base}/metrics",
                                 headers={"Authorization": "Bearer tok-demo"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        metrics = resp.read().decode()
    print("\n[指标片段]")
    print("\n".join(line for line in metrics.splitlines()
                    if line.startswith("agentrpa_invoke") or "avg_ms" in line))

    files = [f for f in os.listdir(audit_dir) if f.startswith("agent-")]
    if files:
        path = os.path.join(audit_dir, sorted(files)[-1])
        print(f"\n[审计] {path}")
        with open(path, encoding="utf-8") as fh:
            for line in fh.readlines()[-2:]:
                rec = json.loads(line)
                print("   ", {k: rec[k] for k in ("tenant", "flow", "agent", "status",
                                                  "duration_ms", "prompt_sha256")})

    httpd.shutdown()
    pool.close()
    httpd.server_close()
    print("\n完成。")


if __name__ == "__main__":
    main()
