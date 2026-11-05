"""Playwright 浏览器驱动：持久化资料目录，元素 ref 短生命周期。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import BaseDriver, DriverError, PolicyBlocked

REF_ATTR = "data-agentrpa-ref"

_COLLECT_JS = """
(limit) => {
  const ATTR = 'data-agentrpa-ref';
  document.querySelectorAll('[' + ATTR + ']').forEach(el => el.removeAttribute(ATTR));
  const sel = 'a,button,input,textarea,select,[role=button],[contenteditable=true]';
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
    i += 1;
    const ref = 'e' + i;
    el.setAttribute(ATTR, ref);
    const text = (el.innerText || el.value || el.getAttribute('aria-label')
      || el.getAttribute('placeholder') || '').trim().slice(0, 80);
    out.push({
      ref, tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      text,
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || ''
    });
    if (i >= limit) break;
  }
  return out;
}
"""

_SNAPSHOT_JS = """
(maxChars) => {
  const bodyText = (document.body && document.body.innerText) || '';
  const text = bodyText.replace(/\\n{3,}/g, '\\n\\n').slice(0, maxChars);
  const links = [...document.querySelectorAll('a[href]')].slice(0, 200).map(a => ({
    text: (a.innerText || '').trim().slice(0, 60),
    href: a.href
  }));
  const forms = [...document.querySelectorAll('form')].slice(0, 20).map(f => ({
    action: f.action || '',
    method: (f.method || 'get').toUpperCase(),
    fields: [...f.querySelectorAll('input,textarea,select')].slice(0, 40).map(i => ({
      name: i.name || '', type: i.type || '', placeholder: i.placeholder || ''
    }))
  }));
  const meta = {};
  document.querySelectorAll('meta[name],meta[property]').forEach(m => {
    const k = m.getAttribute('name') || m.getAttribute('property');
    if (k) meta[k] = m.getAttribute('content') || '';
  });
  return { title: document.title, url: location.href, text, links, forms, meta };
}
"""

_EXTRACT_JS = """
(spec) => {
  const one = {};
  const multi = {};
  const limit = spec.limit || 20;
  for (const [k, sel] of Object.entries(spec.selectors || {})) {
    const el = document.querySelector(sel);
    one[k] = el ? (el.innerText || el.textContent || '').trim() : null;
  }
  for (const [k, sel] of Object.entries(spec.multi || {})) {
    multi[k] = [...document.querySelectorAll(sel)].slice(0, limit)
      .map(e => (e.innerText || e.textContent || '').trim());
  }
  return { fields: one, lists: multi, url: location.href, title: document.title };
}
"""


class WebDriver(BaseDriver):
    name = "browser"

    def __init__(self, settings, policy, audit):
        super().__init__(settings, policy, audit)
        self._pw = None
        self.context = None
        self.page = None

    # ---------- 生命周期 ----------
    async def start(self) -> dict[str, Any]:
        if self.page is not None and not self.page.is_closed():
            return {"driver": self.name, "started": True, "url": self.page.url}
        from playwright.async_api import async_playwright

        self.settings.ensure_dirs()
        self._pw = await async_playwright().start()
        launch: dict[str, Any] = {
            "headless": self.settings.headless,
            "slow_mo": 60,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if self.settings.browser_channel:
            launch["channel"] = self.settings.browser_channel
        if self.settings.browser_executable:
            launch["executable_path"] = self.settings.browser_executable
        self.context = await self._pw.chromium.launch_persistent_context(
            str(self.settings.profile_dir), **launch
        )
        self.context.set_default_timeout(self.settings.action_timeout_ms)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.audit.record("browser.start", profile=str(self.settings.profile_dir))
        return {"driver": self.name, "started": True, "profile": str(self.settings.profile_dir)}

    async def close(self) -> dict[str, Any]:
        if self.context is not None:
            await self.context.close()
            self.context = None
            self.page = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        self.audit.record("browser.close")
        return {"driver": self.name, "closed": True}

    async def health(self) -> dict[str, Any]:
        alive = self.page is not None and not self.page.is_closed()
        return {
            "driver": self.name,
            "ok": alive,
            "url": self.page.url if alive else "",
            "profile": str(self.settings.profile_dir),
        }

    def capabilities(self) -> list[str]:
        return [
            "navigate", "snapshot", "elements", "click", "fill",
            "press", "scroll", "back", "screenshot", "extract", "wait",
        ]

    # ---------- 分发 ----------
    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        if action != "navigate" and (self.page is None or self.page.is_closed()):
            raise DriverError("浏览器未启动，请先调用 browser_start")
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            raise DriverError(f"browser 不支持 action: {action}")
        return await handler(params)

    # ---------- 动作 ----------
    async def _do_navigate(self, params: dict[str, Any]) -> dict[str, Any]:
        url = str(params.get("url", ""))
        decision = self.policy.check_url(url)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.requires_human)
        await self.start()
        resp = await self.page.goto(url, wait_until="domcontentloaded", timeout=self.settings.nav_timeout_ms)
        await self.page.wait_for_timeout(self.settings.action_delay_ms)
        return {
            "url": self.page.url,
            "title": await self.page.title(),
            "status": resp.status if resp else None,
        }

    async def _do_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        max_chars = int(params.get("max_chars", self.settings.max_page_text_chars))
        data = await self.page.evaluate(_SNAPSHOT_JS, max_chars)
        return data

    async def _do_elements(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = int(params.get("limit", 80))
        items = await self.page.evaluate(_COLLECT_JS, limit)
        return {"count": len(items), "elements": items}

    async def _do_click(self, params: dict[str, Any]) -> dict[str, Any]:
        selector = self._selector(params)
        locator = self.page.locator(selector).first
        label = str(params.get("label", ""))
        try:
            label = label or (await locator.inner_text(timeout=2000)).strip()
        except Exception:  # noqa: BLE001 - 元素无文本属正常情况
            pass
        decision = self.policy.check_click(label)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.requires_human)
        await locator.click(timeout=self.settings.action_timeout_ms)
        await self.page.wait_for_timeout(self.settings.action_delay_ms)
        return {"clicked": selector, "label": label[:80], "url": self.page.url}

    async def _do_fill(self, params: dict[str, Any]) -> dict[str, Any]:
        selector = self._selector(params)
        text = str(params.get("text", ""))
        label = str(params.get("label", ""))
        locator = self.page.locator(selector).first
        if not label:
            label = " ".join(
                str(x or "")
                for x in (
                    await locator.get_attribute("name"),
                    await locator.get_attribute("placeholder"),
                    await locator.get_attribute("type"),
                )
            )
        decision = self.policy.check_input(label, text)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.requires_human)
        await locator.fill(text, timeout=self.settings.action_timeout_ms)
        if params.get("press_enter"):
            await locator.press("Enter")
        return {"filled": selector, "chars": len(text)}

    async def _do_press(self, params: dict[str, Any]) -> dict[str, Any]:
        key = str(params.get("key", "Enter"))
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(300)
        return {"pressed": key}

    async def _do_scroll(self, params: dict[str, Any]) -> dict[str, Any]:
        amount = int(params.get("amount", 900))
        direction = str(params.get("direction", "down"))
        delta = amount if direction == "down" else -amount
        await self.page.mouse.wheel(0, delta)
        await self.page.wait_for_timeout(400)
        return {"scrolled": delta}

    async def _do_back(self, params: dict[str, Any]) -> dict[str, Any]:
        await self.page.go_back()
        await self.page.wait_for_timeout(self.settings.action_delay_ms)
        return {"url": self.page.url}

    async def _do_wait(self, params: dict[str, Any]) -> dict[str, Any]:
        ms = int(params.get("ms", 1000))
        await self.page.wait_for_timeout(ms)
        return {"waited_ms": ms}

    async def _do_screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        self.settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        name = str(params.get("name") or f"shot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png")
        path = self.settings.artifacts_dir / name
        await self.page.screenshot(path=str(path), full_page=bool(params.get("full_page", False)))
        return {"path": str(path), "url": self.page.url}

    async def _do_extract(self, params: dict[str, Any]) -> dict[str, Any]:
        spec = {
            "selectors": dict(params.get("selectors") or {}),
            "multi": dict(params.get("multi") or {}),
            "limit": int(params.get("limit", 20)),
        }
        return await self.page.evaluate(_EXTRACT_JS, spec)

    # ---------- 工具 ----------
    def _selector(self, params: dict[str, Any]) -> str:
        ref = str(params.get("ref", "")).strip()
        if ref:
            return f'[{REF_ATTR}="{ref}"]'
        selector = str(params.get("selector", "")).strip()
        if not selector:
            raise DriverError("缺少 ref 或 selector")
        return selector
