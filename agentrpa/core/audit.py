"""审计日志：所有策略判定与动作落地都写 jsonl，敏感字段脱敏。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SECRET_KEY_HINTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "cookie",
    "authorization",
    "secret",
    "otp",
    "captcha",
    "card",
    "cvv",
)

REDACTED = "***(redacted)"

# 字段标签命中这些词时，同一条记录里的 text / value 一并脱敏
SENSITIVE_LABEL_HINTS = (
    "password",
    "passwd",
    "pwd",
    "密码",
    "验证码",
    "captcha",
    "otp",
    "card",
    "cvv",
    "pin",
)


def redact(value: Any, key: str = "") -> Any:
    if key and any(hint in key.lower() for hint in SECRET_KEY_HINTS):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class AuditLog:
    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file(self) -> Path:
        return self.dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        label = str(fields.get("label") or "").lower()
        if label and any(hint in label for hint in SENSITIVE_LABEL_HINTS):
            for key in ("text", "value", "input"):
                if key in fields:
                    fields[key] = REDACTED
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            **redact(fields),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self._file().open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return entry

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        files = sorted(self.dir.glob("*.jsonl"))
        if not files:
            return []
        lines: list[str] = []
        for path in reversed(files):
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines() + lines
            if len(lines) >= limit:
                break
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
