"""Office 驱动：Excel 读写与 Word 读取，走本地文件，不启动 Office 进程。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BaseDriver, DriverError

XLSX_HINT = "Office 驱动需要 openpyxl / python-docx：pip install agentrpa[office]"


class OfficeDriver(BaseDriver):
    name = "office"

    def capabilities(self) -> list[str]:
        return ["sheets", "read", "write", "append"]

    async def health(self) -> dict[str, Any]:
        ok = True
        detail = {}
        try:
            import openpyxl  # noqa: F401
            detail["openpyxl"] = True
        except ImportError:
            ok = False
            detail["openpyxl"] = False
        try:
            import docx  # noqa: F401
            detail["python-docx"] = True
        except ImportError:
            ok = False
            detail["python-docx"] = False
        return {"driver": self.name, "ok": ok, **detail}

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            raise DriverError(f"office 不支持 action: {action}")
        return handler(params)

    # ---------- 动作 ----------
    def _do_sheets(self, params: dict[str, Any]) -> dict[str, Any]:
        openpyxl = self._openpyxl()
        path = self._path(params)
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            return {"path": str(path), "sheets": wb.sheetnames}
        finally:
            wb.close()

    def _do_read(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        if path.suffix.lower() == ".docx":
            return self._read_docx(path, int(params.get("limit", 200)))
        return self._read_xlsx(path, params)

    def _do_write(self, params: dict[str, Any]) -> dict[str, Any]:
        openpyxl = self._openpyxl()
        path = self._path(params)
        cells = dict(params.get("cells") or {})
        if not cells:
            raise DriverError("write 需要 cells，例如 {\"B2\": 42}")
        wb = openpyxl.load_workbook(path) if path.exists() else openpyxl.Workbook()
        ws = self._target_sheet(wb, params.get("sheet"), fresh=not path.exists())
        for coord, value in cells.items():
            ws[str(coord)] = value
        wb.save(path)
        return {"path": str(path), "sheet": ws.title, "written": len(cells)}

    def _do_append(self, params: dict[str, Any]) -> dict[str, Any]:
        openpyxl = self._openpyxl()
        path = self._path(params)
        rows = list(params.get("rows") or [])
        if not rows:
            raise DriverError("append 需要 rows（二维数组）")
        wb = openpyxl.load_workbook(path) if path.exists() else openpyxl.Workbook()
        ws = self._target_sheet(wb, params.get("sheet"), fresh=not path.exists())
        for row in rows:
            ws.append(list(row))
        wb.save(path)
        return {"path": str(path), "appended": len(rows), "rows_total": ws.max_row}

    # ---------- 内部 ----------
    @staticmethod
    def _openpyxl():
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover
            raise DriverError(XLSX_HINT) from exc
        return openpyxl

    @staticmethod
    def _target_sheet(wb, name, fresh: bool = False):
        """新建工作簿时直接改默认表名，避免多出一张空的 Sheet。"""
        if fresh and name:
            wb.active.title = str(name)
            return wb.active
        return OfficeDriver._sheet(wb, name, create=True)

    @staticmethod
    def _sheet(wb, name, create: bool = False):
        """按名取表；create=True 时缺失则新建，避免 KeyError 直接冒到调用方。"""
        if not name:
            return wb.active
        if name in wb.sheetnames:
            return wb[name]
        if create:
            return wb.create_sheet(title=str(name))
        raise DriverError(f"工作表不存在: {name}; 现有: {wb.sheetnames}")

    def _path(self, params: dict[str, Any]) -> Path:
        raw = str(params.get("path", "")).strip()
        if not raw:
            raise DriverError("需要 path")
        path = Path(raw)
        if not path.is_absolute():
            path = (self.settings.artifacts_dir.parent / path).resolve()
        return path

    def _read_xlsx(self, path: Path, params: dict[str, Any]) -> dict[str, Any]:
        openpyxl = self._openpyxl()
        if not path.exists():
            raise DriverError(f"文件不存在: {path}")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = self._sheet(wb, params.get("sheet"))
            rng = params.get("range")
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else v for v in row])
                if len(rows) >= int(params.get("max_rows", 500)):
                    break
            if rng:
                rows = self._slice(rows, str(rng))
            return {"path": str(path), "sheet": ws.title, "rows": rows}
        finally:
            wb.close()

    @staticmethod
    def _slice(rows: list[list[Any]], rng: str) -> list[list[Any]]:
        import re

        match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rng.upper())
        if not match:
            return rows
        c1, r1, c2, r2 = match.groups()
        start_col = ord(c1[0]) - 65
        end_col = ord(c2[0]) - 65 + 1
        return [row[start_col:end_col] for row in rows[int(r1) - 1 : int(r2)]]

    def _read_docx(self, path: Path, limit: int) -> dict[str, Any]:
        try:
            import docx
        except ImportError as exc:  # pragma: no cover
            raise DriverError(XLSX_HINT) from exc
        if not path.exists():
            raise DriverError(f"文件不存在: {path}")
        document = docx.Document(str(path))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()][:limit]
        tables = []
        for table in document.tables[:5]:
            tables.append([[cell.text.strip() for cell in row.cells] for row in table.rows[:50]])
        return {"path": str(path), "paragraphs": paragraphs, "tables": tables}
