"""策略闸门测试：白名单、敏感输入、状态变更动作。"""

from __future__ import annotations

from agentrpa.core.policy import Policy
from agentrpa.core.types import Step


def test_domain_allowlist_hits(policy):
    assert policy.check_url("https://item.jd.com/123.html").allowed
    assert policy.check_url("https://www.example.com/a").allowed


def test_domain_allowlist_blocks_lookalike(policy):
    decision = policy.check_url("https://jd.com.evil.cn/x")
    assert not decision.allowed
    assert "allowlist" in decision.reason


def test_domain_blocks_non_http_scheme(policy):
    assert not policy.check_url("file:///etc/passwd").allowed
    assert not policy.check_url("javascript:alert(1)").allowed


def test_sensitive_field_detected():
    p = Policy(allowed_domains=["example.com"])
    assert p.is_sensitive_field("登录密码")
    assert p.is_sensitive_field("one-time password")
    assert p.is_sensitive_field("CVV")
    assert not p.is_sensitive_field("收货人姓名")


def test_sensitive_input_rejected():
    p = Policy(allowed_domains=["example.com"])
    decision = p.check_input("支付密码", "123456")
    assert not decision.allowed


def test_state_changing_click_requires_human():
    p = Policy(allowed_domains=["example.com"], require_hitl_for_state_changes=True)
    decision = p.check_click("立即购买")
    assert not decision.allowed
    assert decision.requires_human


def test_state_changing_click_allowed_when_explicitly_enabled():
    p = Policy(
        allowed_domains=["example.com"],
        allow_state_changing=True,
        require_hitl_for_state_changes=False,
    )
    assert p.check_click("加入购物车").allowed


def test_state_changing_hard_disabled():
    p = Policy(
        allowed_domains=["example.com"],
        allow_state_changing=False,
        require_hitl_for_state_changes=False,
    )
    decision = p.check_click("结算")
    assert not decision.allowed
    assert not decision.requires_human


def test_extra_blocked_terms():
    p = Policy(allowed_domains=["example.com"], extra_blocked_terms=["退订"])
    assert p.check_click("退订通知").requires_human or not p.check_click("退订通知").allowed


def test_app_allowlist():
    p = Policy(allowed_domains=["example.com"], allowed_apps=["notepad.exe"])
    assert p.check_app(r"C:\Windows\System32\notepad.exe").allowed
    assert not p.check_app(r"C:\Tools\unknown.exe").allowed


def test_app_allowlist_empty_denies_all():
    p = Policy(allowed_domains=["example.com"])
    assert not p.check_app(r"C:\Windows\notepad.exe").allowed


def test_check_step_navigate(policy):
    ok = Step(id="a", action="navigate", params={"url": "https://example.com"})
    bad = Step(id="b", action="navigate", params={"url": "https://evil.test"})
    assert policy.check_step(ok).allowed
    assert not policy.check_step(bad).allowed


def test_summary_contains_lists(policy):
    summary = policy.summary()
    assert "example.com" in summary["allowed_domains"]
    assert summary["require_hitl_for_state_changes"] is True
