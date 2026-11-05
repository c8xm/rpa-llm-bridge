"""模拟 `dsh headless --json`：按行输出事件流。"""
import json
import sys

task = sys.stdin.read().strip()
for event in (
    {"type": "session", "sessionId": "s-1"},
    {"type": "text", "text": "正在分析：" + task[:20]},
    {"type": "tool_call", "name": "read_file", "status": "ok"},
    {"type": "final", "text": "答案：" + task},
):
    print(json.dumps(event, ensure_ascii=False), flush=True)
