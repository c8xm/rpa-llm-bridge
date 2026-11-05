"""Office 驱动测试：依赖 openpyxl / python-docx，缺失时跳过。"""

from __future__ import annotations

import pytest

pytest.importorskip("openpyxl")

from agentrpa.drivers.base import DriverError
from agentrpa.drivers.office import OfficeDriver


@pytest.fixture
def driver(settings, policy, audit) -> OfficeDriver:
    return OfficeDriver(settings, policy, audit)


async def test_write_creates_named_sheet(driver, settings):
    target = settings.artifacts_dir / "t.xlsx"
    out = await driver.execute(
        "write", {"path": str(target), "sheet": "汇总", "cells": {"A1": "x", "B2": 7}}
    )
    assert out["written"] == 2
    assert target.exists()
    sheets = await driver.execute("sheets", {"path": str(target)})
    assert sheets["sheets"] == ["汇总"]


async def test_append_and_read_roundtrip(driver, settings):
    target = settings.artifacts_dir / "t2.xlsx"
    await driver.execute("write", {"path": str(target), "sheet": "S", "cells": {"A1": "h"}})
    await driver.execute("append", {"path": str(target), "sheet": "S", "rows": [["a", 1], ["b", 2]]})
    data = await driver.execute("read", {"path": str(target), "sheet": "S", "max_rows": 10})
    assert data["rows"] == [["h", ""], ["a", 1], ["b", 2]]


async def test_missing_sheet_raises(driver, settings):
    target = settings.artifacts_dir / "t3.xlsx"
    await driver.execute("write", {"path": str(target), "sheet": "S", "cells": {"A1": 1}})
    with pytest.raises(DriverError):
        await driver.execute("read", {"path": str(target), "sheet": "nope"})


async def test_missing_file_raises(driver, settings):
    with pytest.raises(DriverError):
        await driver.execute("read", {"path": str(settings.artifacts_dir / "absent.xlsx")})


async def test_health_reports_deps(driver):
    health = await driver.health()
    assert health["driver"] == "office"
    assert health["openpyxl"] is True


async def test_unknown_action_raises(driver):
    with pytest.raises(DriverError):
        await driver.execute("explode", {})
