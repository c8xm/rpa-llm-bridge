"""集中配置：全部来自环境变量，缺省值对本地只读场景是安全的。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ALLOWED_DOMAINS = [
    "jd.com",
    "360buy.com",
    "taobao.com",
    "tmall.com",
    "pinduoduo.com",
    "yangkeduo.com",
]


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    p = Path(raw) if raw else default
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _csv(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    # 浏览器
    headless: bool = False
    browser_channel: str = ""
    browser_executable: str = ""
    profile_dir: Path = PROJECT_ROOT / ".tmp" / "browser-profile"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"
    audit_dir: Path = PROJECT_ROOT / "artifacts" / "audit"
    runs_dir: Path = PROJECT_ROOT / "artifacts" / "runs"
    recipes_dir: Path = PROJECT_ROOT / "agentrpa" / "recipes" / "examples"

    # 超时与节流
    nav_timeout_ms: int = 45000
    action_timeout_ms: int = 15000
    action_delay_ms: int = 700
    max_page_text_chars: int = 18000

    # 策略
    allowed_domains: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_DOMAINS))
    allowed_apps: list[str] = field(default_factory=list)
    allow_state_changing_actions: bool = False
    extra_blocked_terms: list[str] = field(default_factory=list)
    require_hitl_for_state_changes: bool = True

    # 运行模式
    dry_run: bool = False
    debug: bool = False

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        base = root or PROJECT_ROOT
        return cls(
            headless=_flag("PLAYWRIGHT_HEADLESS", False),
            browser_channel=os.getenv("BROWSER_CHANNEL", "").strip(),
            browser_executable=os.getenv("BROWSER_EXECUTABLE_PATH", "").strip(),
            profile_dir=_path("BROWSER_PROFILE_DIR", base / ".tmp" / "browser-profile"),
            artifacts_dir=_path("ARTIFACTS_DIR", base / "artifacts"),
            audit_dir=_path("AUDIT_DIR", base / "artifacts" / "audit"),
            runs_dir=_path("RUNS_DIR", base / "artifacts" / "runs"),
            recipes_dir=_path("RECIPES_DIR", base / "agentrpa" / "recipes" / "examples"),
            nav_timeout_ms=_int("NAVIGATION_TIMEOUT_MS", 45000),
            action_timeout_ms=_int("ACTION_TIMEOUT_MS", 15000),
            action_delay_ms=_int("ACTION_DELAY_MS", 700),
            max_page_text_chars=_int("MAX_PAGE_TEXT_CHARS", 18000),
            allowed_domains=[d.lower() for d in _csv("ALLOWED_DOMAINS", DEFAULT_ALLOWED_DOMAINS)],
            allowed_apps=_csv("ALLOWED_APPS", []),
            allow_state_changing_actions=_flag("ALLOW_STATE_CHANGING_ACTIONS", False),
            extra_blocked_terms=_csv("EXTRA_BLOCKED_TERMS", []),
            require_hitl_for_state_changes=_flag("REQUIRE_HITL_FOR_STATE_CHANGES", True),
            dry_run=_flag("AGENTRPA_DRY_RUN", False),
            debug=_flag("AGENTRPA_DEBUG", False),
        )

    def ensure_dirs(self) -> None:
        for p in (self.artifacts_dir, self.audit_dir, self.runs_dir, self.profile_dir):
            p.mkdir(parents=True, exist_ok=True)
