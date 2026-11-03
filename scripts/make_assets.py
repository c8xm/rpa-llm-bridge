r"""生成文档配图：架构图 + 真实运行输出截图。

用法：.\.venv\Scripts\python.exe scripts/make_assets.py
产物：docs/assets/architecture.png、docs/assets/demo.png
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

FONT_CJK = r"C:\Windows\Fonts\msyh.ttc"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
FONT_MONO_BOLD = r"C:\Windows\Fonts\consolab.ttf"

INK = "#0f172a"
SUB = "#475569"
LINE = "#94a3b8"


def font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:  # pragma: no cover - 非 Windows 环境
        return ImageFont.load_default()


def rounded(draw, box, fill, outline, radius=16, width=3):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def center_text(draw, box, lines, fill, size_title, size_sub=None, gap=10):
    x1, y1, x2, y2 = box
    f1 = font(FONT_CJK, size_title)
    f2 = font(FONT_CJK, size_sub or size_title - 6)
    heights = [size_title] + [size_sub or size_title - 6] * (len(lines) - 1)
    total = sum(heights) + gap * (len(lines) - 1)
    y = (y1 + y2) / 2 - total / 2
    for index, text in enumerate(lines):
        f = f1 if index == 0 else f2
        h = heights[index]
        draw.text(((x1 + x2) / 2, y + h / 2), text, font=f, fill=fill, anchor="mm")
        y += h + gap


def arrow(draw, x1, y1, x2, y2, color=LINE, width=3, head=13):
    draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
    if abs(x2 - x1) < 2:
        direction = 1 if y2 > y1 else -1
        draw.polygon(
            [(x2, y2), (x2 - head, y2 - head * direction), (x2 + head, y2 - head * direction)],
            fill=color,
        )


def elbow(draw, x_from, y_from, x_to, y_down, color=LINE):
    """先竖后横再竖的折线箭头。"""
    mid = y_from + (y_down - y_from) / 2
    draw.line([(x_from, y_from), (x_from, mid)], fill=color, width=3)
    draw.line([(x_from, mid), (x_to, mid)], fill=color, width=3)
    arrow(draw, x_to, mid, x_to, y_down, color)


def architecture() -> Path:
    W, H = 1760, 1060
    img = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(img)

    d.text((44, 40), "rpa-llm-bridge / AgentRPA 内核 · 架构", font=font(FONT_CJK, 34), fill=INK)
    d.text(
        (44, 86),
        "统一 action 契约 → 三驱动可插拔；策略与审计横切所有动作",
        font=font(FONT_CJK, 22),
        fill=SUB,
    )

    client = (430, 150, 1330, 262)
    server = (530, 320, 1230, 424)

    cols = [(120, 600), (640, 1120), (1160, 1640)]
    core_y = (490, 616)
    band_y = (664, 716)
    drv_y = (764, 890)
    end_y = (950, 1050)

    rounded(d, client, "#dbeafe", "#2563eb")
    center_text(d, client, ["MCP 客户端", "Codex · Claude Desktop · Cursor"], INK, 30, 22)

    rounded(d, server, "#e0f2fe", "#0284c7")
    center_text(d, server, ["server.py · 27 个 MCP 工具", "参数收敛与路由"], INK, 30, 22)

    arrow(d, 880, 262, 880, 320)

    core_labels = [
        ("RecipeRunner", "顺序 / 重试 / 断点续跑"),
        ("Policy", "白名单 · 敏感输入 · 状态变更闸门"),
        ("AuditLog", "jsonl 落盘 · 字段脱敏"),
    ]
    core_colors = [("#ede9fe", "#7c3aed"), ("#ede9fe", "#7c3aed"), ("#ede9fe", "#7c3aed")]
    for (left, right), (title, sub), (fill, outline) in zip(cols, core_labels, core_colors):
        box = (left, core_y[0], right, core_y[1])
        rounded(d, box, fill, outline)
        center_text(d, box, [title, sub], INK, 30, 21)

    elbow(d, 880, 424, 360, core_y[0])
    elbow(d, 880, 424, 880, core_y[0])
    elbow(d, 880, 424, 1400, core_y[0])

    band = (120, band_y[0], 1640, band_y[1])
    rounded(d, band, "#f1f5f9", "#94a3b8", radius=12)
    d.text(
        (880, (band_y[0] + band_y[1]) / 2),
        "统一 action 契约    execute(action, params) → dict",
        font=font(FONT_CJK, 26),
        fill=INK,
        anchor="mm",
    )

    for left, right in cols:
        arrow(d, (left + right) / 2, core_y[1], (left + right) / 2, band_y[0])
        arrow(d, (left + right) / 2, band_y[1], (left + right) / 2, drv_y[0])

    drivers = [
        ("WebDriver", "Playwright · 持久化 Profile", "#dcfce7", "#16a34a"),
        ("DesktopDriver", "pywinauto UIA · 应用白名单", "#dcfce7", "#16a34a"),
        ("OfficeDriver", "openpyxl · python-docx", "#dcfce7", "#16a34a"),
    ]
    for (left, right), (title, sub, fill, outline) in zip(cols, drivers):
        box = (left, drv_y[0], right, drv_y[1])
        rounded(d, box, fill, outline)
        center_text(d, box, [title, sub], INK, 30, 21)

    ends = [
        ("Chromium", "京东 / 淘宝 / 内网系统"),
        ("Windows 应用", "ERP / OA / 客户端"),
        ("Excel · Word", "报表读写的落点"),
    ]
    for (left, right), (title, sub) in zip(cols, ends):
        box = (left, end_y[0], right, end_y[1])
        rounded(d, box, "#f8fafc", "#64748b")
        center_text(d, box, [title, sub], INK, 28, 21)
        arrow(d, (left + right) / 2, drv_y[1], (left + right) / 2, end_y[0])

    path = OUT / "architecture.png"
    img.save(path, "PNG")
    return path


def capture_terminal() -> list[str]:
    py = sys.executable
    lines: list[str] = []

    run = subprocess.run(
        [py, "-m", "pytest"], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    body = (run.stdout or "").strip().splitlines()
    lines.append("PS D:\\github\\rpa-llm-bridge> python -m pytest")
    lines.extend(body[-2:] if body else ["(no output)"])

    probe = (
        "import asyncio, server;"
        "tools = asyncio.run(server.mcp.list_tools());"
        "print(f'tools registered: {len(tools)}')"
    )
    run2 = subprocess.run(
        [py, "-c", probe], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    lines.append("")
    lines.append("PS> python -c \"import server; asyncio.run(server.mcp.list_tools())\"")
    lines.extend((run2.stdout or "").strip().splitlines())

    probe2 = (
        "from agentrpa.core.policy import Policy;"
        "p = Policy(allowed_domains=['jd.com']);"
        "d = p.check_url('https://jd.com.evil.cn/x');"
        "print(f'allowed={d.allowed}  reason={d.reason}')"
    )
    run3 = subprocess.run(
        [py, "-c", probe2], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    lines.append("")
    lines.append("PS> python -c \"Policy(allowed_domains=['jd.com']).check_url('https://jd.com.evil.cn/x')\"")
    lines.extend((run3.stdout or "").strip().splitlines())
    lines.append("")
    lines.append(
        "PS> python -c \"Policy(['jd.com']).check_click('立即购买').requires_human\""
    )
    run4 = subprocess.run(
        [py, "-c", "from agentrpa.core.policy import Policy;"
                   "print(Policy(allowed_domains=['jd.com']).check_click('立即购买').requires_human)"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines.extend((run4.stdout or "").strip().splitlines())

    lines.append("")
    lines.append("PS> python -c \"Policy(['jd.com']).check_input('支付密码', 'x').reason\"")
    run5 = subprocess.run(
        [py, "-c", "from agentrpa.core.policy import Policy;"
                   "print(Policy(allowed_domains=['jd.com']).check_input('支付密码', 'x').reason)"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines.extend((run5.stdout or "").strip().splitlines())
    return lines


def draw_mixed(draw, xy, text, size, fill, bold=False):
    """Consolas 无中文字形，逐字回退到微软雅黑，避免出现方框。"""
    mono = font(FONT_MONO_BOLD if bold else FONT_MONO, size)
    cjk = font(FONT_CJK, size)
    x, y = xy
    for ch in text:
        f = cjk if ord(ch) > 0x2E80 else mono
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f)
    return x


def demo_image(lines: list[str]) -> Path:
    pad, lh = 46, 34
    width = 1500
    height = pad * 2 + lh * len(lines) + 30

    img = Image.new("RGB", (width, height), "#0d1117")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, width - 1, height - 1), radius=18, outline="#30363d", width=3)

    for i in range(3):
        d.ellipse((24 + i * 26, 22, 38 + i * 26, 36), fill=["#ff5f56", "#ffbd2e", "#27c93f"][i])

    y = pad + 18
    for line in lines:
        if line.startswith("PS"):
            draw_mixed(d, (pad, y), line, 21, "#7ee787")
        elif "passed" in line:
            draw_mixed(d, (pad, y), line, 22, "#58a6ff", bold=True)
        elif "allowed=False" in line or line.strip() == "True":
            draw_mixed(d, (pad, y), line, 22, "#ffa657", bold=True)
        else:
            draw_mixed(d, (pad, y), line, 22, "#c9d1d9")
        y += lh

    path = OUT / "demo.png"
    img.save(path, "PNG")
    return path


if __name__ == "__main__":
    arch = architecture()
    lines = capture_terminal()
    demo = demo_image(lines)
    print(f"wrote {arch}")
    print(f"wrote {demo}")
