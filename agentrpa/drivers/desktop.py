"""Windows 桌面驱动：pywinauto (UIA)，应用级白名单。"""

from __future__ import annotations

from typing import Any

from .base import BaseDriver, DriverError, PolicyBlocked

HINT = "桌面驱动需要 pywinauto：pip install agentrpa[desktop]"


def _require_pywinauto():
    try:
        from pywinauto import Application, Desktop
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise DriverError(HINT) from exc
    return Application, Desktop


class DesktopDriver(BaseDriver):
    name = "desktop"

    def __init__(self, settings, policy, audit):
        super().__init__(settings, policy, audit)
        self.app = None
        self.win = None

    def capabilities(self) -> list[str]:
        return ["launch", "windows", "focus", "elements", "click", "fill", "press", "read"]

    async def health(self) -> dict[str, Any]:
        return {
            "driver": self.name,
            "ok": self.win is not None,
            "title": self._title(),
        }

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            raise DriverError(f"desktop 不支持 action: {action}")
        return handler(params)

    # ---------- 动作 ----------
    def _do_launch(self, params: dict[str, Any]) -> dict[str, Any]:
        Application, _ = _require_pywinauto()
        app_path = str(params.get("app", ""))
        decision = self.policy.check_app(app_path)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.requires_human)
        self.app = Application(backend="uia").start(app_path)
        windows = self.app.windows()
        if windows:
            self.win = windows[0]
        self.audit.record("desktop.launch", app=app_path)
        return {"app": app_path, "title": self._title()}

    def _do_windows(self, params: dict[str, Any]) -> dict[str, Any]:
        _, Desktop = _require_pywinauto()
        items = []
        for w in Desktop(backend="uia").windows():
            try:
                items.append({"title": w.window_text(), "class": w.class_name()})
            except Exception:  # noqa: BLE001 - 个别窗口句柄已失效
                continue
        return {"count": len(items), "windows": items}

    def _do_focus(self, params: dict[str, Any]) -> dict[str, Any]:
        Application, Desktop = _require_pywinauto()
        title = str(params.get("title", ""))
        if not title:
            raise DriverError("focus 需要 title")
        self.win = Desktop(backend="uia").window(title_re=title)
        self.win.set_focus()
        self.app = Application(backend="uia").connect(handle=self.win.handle)
        return {"title": self._title()}

    def _do_elements(self, params: dict[str, Any]) -> dict[str, Any]:
        window = self._window()
        limit = int(params.get("limit", 60))
        out = []
        for ctrl in window.descendants()[:limit]:
            try:
                out.append(
                    {
                        "name": ctrl.window_text(),
                        "type": ctrl.element_info.control_type,
                        "automation_id": ctrl.element_info.automation_id,
                    }
                )
            except Exception:  # noqa: BLE001 - 控件可能已销毁
                continue
        return {"count": len(out), "elements": out}

    def _do_click(self, params: dict[str, Any]) -> dict[str, Any]:
        window = self._window()
        name = str(params.get("name", ""))
        control_type = str(params.get("control_type", "Button"))
        if not name:
            raise DriverError("click 需要 name")
        decision = self.policy.check_click(name)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.requires_human)
        window.child_window(title=name, control_type=control_type).click_input()
        return {"clicked": name, "control_type": control_type}

    def _do_fill(self, params: dict[str, Any]) -> dict[str, Any]:
        window = self._window()
        name = str(params.get("name", ""))
        text = str(params.get("text", ""))
        decision = self.policy.check_input(name, text)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.requires_human)
        window.child_window(title=name, control_type="Edit").set_edit_text(text)
        return {"filled": name, "chars": len(text)}

    def _do_press(self, params: dict[str, Any]) -> dict[str, Any]:
        window = self._window()
        sequence = str(params.get("keys", "{ENTER}"))
        window.type_keys(sequence, with_spaces=True)
        return {"keys": sequence}

    def _do_read(self, params: dict[str, Any]) -> dict[str, Any]:
        window = self._window()
        limit = int(params.get("limit", 200))
        texts = [window.window_text()]
        for ctrl in window.descendants()[:limit]:
            try:
                value = ctrl.window_text()
            except Exception:  # noqa: BLE001
                continue
            if value:
                texts.append(value)
        return {"title": self._title(), "texts": texts[:limit]}

    # ---------- 工具 ----------
    def _window(self):
        if self.win is None:
            raise DriverError("尚未连接窗口，请先 launch 或 focus")
        return self.win

    def _title(self) -> str:
        if self.win is None:
            return ""
        try:
            return self.win.window_text()
        except Exception:  # noqa: BLE001
            return ""
