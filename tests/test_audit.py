"""审计日志测试：写入、读取、脱敏。"""

from __future__ import annotations

from agentrpa.core.audit import REDACTED, AuditLog, redact


def test_record_and_tail(tmp_path):
    log = AuditLog(tmp_path)
    log.record("run.start", run_id="r1", recipe="demo")
    log.record("step.ok", step_id="s1")
    entries = log.tail(10)
    assert len(entries) == 2
    assert entries[0]["event"] == "run.start"
    assert entries[0]["run_id"] == "r1"
    assert "ts" in entries[0]


def test_secrets_redacted(tmp_path):
    log = AuditLog(tmp_path)
    entry = log.record("fill", label="登录密码", text="hunter2", token="abc", cookie="sid=1")
    assert entry["text"] == REDACTED
    assert entry["token"] == REDACTED
    assert entry["cookie"] == REDACTED
    assert entry["label"] == "登录密码"


def test_nested_redaction():
    payload = {"outer": {"password": "x", "keep": 1}, "list": [{"secret": "y"}]}
    cleaned = redact(payload)
    assert cleaned["outer"]["password"] == REDACTED
    assert cleaned["outer"]["keep"] == 1
    assert cleaned["list"][0]["secret"] == REDACTED


def test_tail_limit(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(5):
        log.record("e", n=i)
    assert len(log.tail(3)) == 3
    assert log.tail(3)[-1]["n"] == 4


def test_empty_dir_tail(tmp_path):
    assert AuditLog(tmp_path / "nested").tail(5) == []
