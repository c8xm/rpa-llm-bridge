# 贡献指南

## 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest
```

## 提交前

1. `pytest` 全绿。新增逻辑必须带测试；测试不允许启动真实浏览器或桌面进程。
2. 新增 action 时同步更新：驱动的 `capabilities()`、`server.py` 的工具、README 工具清单。
3. 新增策略规则时，在 `tests/test_policy.py` 里补一条正例 + 一条反例。
4. 不要提交 `.venv/`、`artifacts/`、`.tmp/`、`.env`。

## 新增一个驱动

```python
from agentrpa.drivers.base import BaseDriver, DriverError

class MyDriver(BaseDriver):
    name = "myapp"

    def capabilities(self) -> list[str]:
        return ["ping"]

    async def execute(self, action, params):
        if action != "ping":
            raise DriverError(f"不支持: {action}")
        return {"pong": params}
```

然后在 `server.py` 注册进 `drivers` 字典，并在 `tests/` 里用一个假驱动验证执行器行为。

## 新增一个 Recipe

放到 `agentrpa/recipes/examples/<name>.yaml`，字段约束见 `agentrpa/recipes/loader.py`：

- `name` 必填且唯一
- `steps[].id` 必填，同一流程内不可重复
- `steps[].action` 必填
- `steps[].on_error` 只能是 `abort | skip | retry`
- 不可逆步骤加 `hitl: true`

## 代码风格

- 行宽 100，`ruff` 检查。
- 注释写"为什么"，不写"做了什么"。
- 中文注释与中文文档为主，标识符保持英文。

## PR

一个 PR 解决一件事。描述里写清：动机、方案、验证方式、影响面（尤其是策略相关的改动）。
