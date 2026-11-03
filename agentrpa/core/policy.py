"""策略闸门：所有驱动共用一套判定，不放任任何驱动自行决定安全边界。"""

from __future__ import annotations

from urllib.parse import urlparse

from .types import Decision, Step

SENSITIVE_FIELD_HINTS = (
    "password",
    "passwd",
    "pwd",
    "密码",
    "验证码",
    "captcha",
    "otp",
    "短信",
    "支付密码",
    "pay password",
    "银行卡",
    "card number",
    "身份证",
    "id card",
    "cvv",
    "安全码",
    "pin code",
)

STATE_CHANGING_HINTS = (
    "购买",
    "立即购买",
    "加入购物车",
    "结算",
    "支付",
    "付款",
    "提交订单",
    "下单",
    "删除",
    "注销",
    "解绑",
    "转账",
    "退款",
    "buy now",
    "add to cart",
    "checkout",
    "place order",
    "pay",
    "delete",
    "unsubscribe",
    "transfer",
)

# 允许列出页面元素，但点击时必须走人工确认的动作种类
IRREVERSIBLE_ACTION_HINTS = (
    "发布",
    "发送",
    "提交",
    "publish",
    "post",
    "send",
    "submit",
)


class Policy:
    """域名白名单 + 应用白名单 + 敏感输入拦截 + 状态变更闸门。"""

    def __init__(
        self,
        allowed_domains: list[str],
        allowed_apps: list[str] | None = None,
        allow_state_changing: bool = False,
        require_hitl_for_state_changes: bool = True,
        extra_blocked_terms: list[str] | None = None,
    ):
        self.allowed_domains = [d.strip().lower().lstrip(".") for d in allowed_domains if d.strip()]
        self.allowed_apps = [a.strip().lower() for a in (allowed_apps or []) if a.strip()]
        self.allow_state_changing = allow_state_changing
        self.require_hitl_for_state_changes = require_hitl_for_state_changes
        self.extra_blocked_terms = [t.strip().lower() for t in (extra_blocked_terms or []) if t.strip()]

    # ---------- 目标 ----------
    def check_url(self, url: str) -> Decision:
        if not url:
            return Decision(False, "empty url")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return Decision(False, f"scheme not allowed: {parsed.scheme or 'none'}")
        host = (parsed.hostname or "").lower()
        if not host:
            return Decision(False, "host missing")
        if self.allowed_domains and not self._domain_matches(host):
            return Decision(False, f"domain not in allowlist: {host}")
        return Decision(True, "ok")

    def _domain_matches(self, host: str) -> bool:
        for domain in self.allowed_domains:
            if host == domain or host.endswith("." + domain):
                return True
        return False

    def check_app(self, app_path: str) -> Decision:
        if not self.allowed_apps:
            return Decision(False, "ALLOWED_APPS 为空，桌面驱动未授权任何应用")
        low = app_path.replace("/", "\\").lower()
        for allowed in self.allowed_apps:
            if low.endswith(allowed) or allowed in low:
                return Decision(True, "ok")
        return Decision(False, f"app not in allowlist: {app_path}")

    # ---------- 输入 ----------
    def is_sensitive_field(self, label: str) -> bool:
        low = (label or "").lower()
        return any(hint in low for hint in SENSITIVE_FIELD_HINTS)

    def check_input(self, label: str, text: str) -> Decision:
        if self.is_sensitive_field(label):
            return Decision(False, f"敏感字段禁止自动输入: {label}")
        return Decision(True, "ok")

    # ---------- 动作 ----------
    def is_state_changing(self, text: str) -> bool:
        low = (text or "").lower()
        return any(hint in low for hint in STATE_CHANGING_HINTS) or any(
            hint in low for hint in self.extra_blocked_terms
        )

    def check_click(self, label: str) -> Decision:
        if not self.is_state_changing(label):
            return Decision(True, "ok")
        if self.allow_state_changing and not self.require_hitl_for_state_changes:
            return Decision(True, "state-changing action explicitly allowed")
        if self.require_hitl_for_state_changes:
            return Decision(False, f"状态变更动作需要人工接管: {label}", requires_human=True)
        return Decision(False, f"状态变更动作已被禁用: {label}")

    def check_step(self, step: Step) -> Decision:
        """执行前的静态检查，先于驱动调用。"""
        if step.action == "navigate":
            return self.check_url(str(step.params.get("url", "")))
        if step.action == "launch":
            return self.check_app(str(step.params.get("app", "")))
        if step.action == "click":
            label = " ".join(str(step.params.get(k, "")) for k in ("ref", "selector", "label"))
            if not label.strip():
                return Decision(True, "ok")
            return self.check_click(label)
        return Decision(True, "ok")

    def summary(self) -> dict:
        return {
            "allowed_domains": self.allowed_domains,
            "allowed_apps": self.allowed_apps,
            "allow_state_changing_actions": self.allow_state_changing,
            "require_hitl_for_state_changes": self.require_hitl_for_state_changes,
            "extra_blocked_terms": self.extra_blocked_terms,
            "sensitive_field_hints": list(SENSITIVE_FIELD_HINTS),
            "state_changing_hints": list(STATE_CHANGING_HINTS),
        }
