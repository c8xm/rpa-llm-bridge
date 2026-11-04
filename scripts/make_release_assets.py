# -*- coding: utf-8 -*-
"""生成 Release 配图（Pillow）。输出到仓库 docs/assets/。"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = r"D:\github\rpa-llm-bridge\docs\assets"
os.makedirs(OUT, exist_ok=True)

BG = (15, 23, 32)
PANEL = (22, 32, 43)
PANEL2 = (28, 40, 53)
LINE = (58, 74, 92)
TEXT = (230, 237, 243)
MUTED = (139, 152, 165)
CYAN = (76, 194, 255)
GREEN = (52, 211, 153)
AMBER = (245, 158, 11)
PINK = (244, 114, 182)
PURPLE = (167, 139, 250)

FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_B = "C:/Windows/Fonts/msyhbd.ttc"
MONO = "C:/Windows/Fonts/consola.ttf"


def f(size, bold=False):
    path = FONT_B if bold and os.path.exists(FONT_B) else FONT
    return ImageFont.truetype(path, size)


def m(size):
    return ImageFont.truetype(MONO, size)


def canvas(w, h, title="", subtitle=""):
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 6], fill=CYAN)
    if title:
        d.text((56, 42), title, font=f(38, True), fill=TEXT)
    if subtitle:
        d.text((56, 96), subtitle, font=f(20), fill=MUTED)
    d.text((56, h - 46), "rpa-llm-bridge  ·  github.com/c8xm/rpa-llm-bridge",
           font=f(16), fill=(90, 104, 120))
    return img, d


def box(d, xy, title, lines, accent=CYAN, width=2, fill=PANEL, tsize=22, lsize=17):
    x1, y1, x2, y2 = xy
    d.rounded_rectangle([x1, y1, x2, y2], radius=14, fill=fill, outline=accent, width=width)
    d.text((x1 + 20, y1 + 14), title, font=f(tsize, True), fill=accent)
    y = y1 + 14 + tsize + 14
    for line in lines:
        d.text((x1 + 20, y), line, font=f(lsize), fill=TEXT if not line.startswith("·") else MUTED)
        y += lsize + 10
    return xy


def arrow(d, p1, p2, label="", color=LINE, width=3, above=True, dashed=False):
    x1, y1 = p1
    x2, y2 = p2
    if dashed:
        steps = 18
        for i in range(steps):
            if i % 2:
                continue
            d.line([x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps,
                    x1 + (x2 - x1) * (i + 1) / steps, y1 + (y2 - y1) * (i + 1) / steps],
                   fill=color, width=width)
    else:
        d.line([x1, y1, x2, y2], fill=color, width=width)
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    size = 12
    d.polygon([(x2, y2),
               (x2 - size * math.cos(ang - 0.4), y2 - size * math.sin(ang - 0.4)),
               (x2 - size * math.cos(ang + 0.4), y2 - size * math.sin(ang + 0.4))], fill=color)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        fnt = f(16)
        tw = d.textlength(label, font=fnt)
        ty = my - 30 if above else my + 8
        d.rectangle([mx - tw / 2 - 8, ty - 4, mx + tw / 2 + 8, ty + 22], fill=BG)
        d.text((mx - tw / 2, ty), label, font=fnt, fill=MUTED)


# ------------------------------------------------------------------ 1. 架构
def architecture():
    w, h = 1680, 1120
    img, d = canvas(w, h, "rpa-llm-bridge v2.0 · 双向桥架构",
                    "RPA 当“手”，智能体当“脑”，桥把两边都变成对方能调用的形状")

    # 方向 A
    d.text((56, 150), "方向 A · 智能体驱动本机（v0.1 内核 agentrpa）", font=f(22, True), fill=PURPLE)
    box(d, (56, 190, 400, 320), "智能体", ["Codex / Claude / Cursor"], PURPLE)
    box(d, (470, 178, 760, 332), "MCP 工具层", ["27 个工具", "Policy 闸门 · hitl"], CYAN)
    box(d, (830, 150, 1150, 250), "浏览器", ["Playwright"], GREEN, tsize=20)
    box(d, (830, 265, 1150, 365), "Windows 桌面", ["pywinauto / UIA"], GREEN, tsize=20)
    box(d, (830, 380, 1150, 470), "Office", ["openpyxl / python-docx"], GREEN, tsize=20)
    arrow(d, (400, 255), (470, 255), "stdio")
    for y in (200, 315, 425):
        arrow(d, (760, 255), (830, y), "")

    # 方向 B
    d.text((56, 520), "方向 B · RPA 调用智能体（v2.0 桥 agentrpa_bridge）", font=f(22, True), fill=AMBER)
    box(d, (56, 560, 400, 730), "影刀 / UiPath", ["Power Automate", "· 代码块或 HTTP 节点"], AMBER)
    box(d, (470, 548, 800, 742), "企业网关", ["/v1/invoke · /v1/jobs",
                                          "/v1/agents · /healthz"], CYAN)
    box(d, (470, 782, 800, 900), "治理面", ["Bearer 鉴权 · 租户限速", "并发闸门 · 审计 · 指标"], (100, 116, 139), tsize=20)
    box(d, (870, 520, 1200, 620), "codex mcp-server", ["MCP 常驻会话"], GREEN, tsize=20)
    box(d, (870, 632, 1200, 732), "dsh headless", ["一次性 · 长任务"], GREEN, tsize=20)
    box(d, (870, 744, 1200, 844), "豆包 / OpenAI 兼容", ["HTTP 模型后端"], GREEN, tsize=20)
    arrow(d, (400, 645), (470, 645), "HTTP")
    for y in (570, 682, 794):
        arrow(d, (800, 645), (870, y), "")
    arrow(d, (635, 730), (635, 782), "拦截", AMBER, above=False)

    # 方向 C
    d.text((56, 900), "方向 C · 智能体触发 RPA（反向 MCP）", font=f(22, True), fill=PINK)
    box(d, (1300, 520, 1624, 620), "智能体", ["WorkBuddy / Codex"], PURPLE, tsize=20)
    box(d, (1300, 660, 1624, 780), "yingdao_mcp_server", ["6 个工具 + 企业护栏"], PINK, tsize=20)
    box(d, (1300, 820, 1624, 900), "影刀开放 API", ["startJob / queryJob"], AMBER, tsize=20)
    arrow(d, (1462, 620), (1462, 660), "MCP")
    arrow(d, (1462, 780), (1462, 820), "HTTPS")
    img.save(os.path.join(OUT, "v2-architecture.png"))


# ------------------------------------------------------------------ 2. 调用时序
def gateway_flow():
    w, h = 1680, 820
    img, d = canvas(w, h, "一次 /v1/invoke 都经过了什么",
                    "每一步都有对应的超时、拒绝码与审计字段")
    steps = [
        ("RPA 代码块", ["ab.ask_agent()", "· 或 HTTP 节点"], AMBER),
        ("鉴权", ["Bearer → tenant", "401"], CYAN),
        ("限速", ["滑动窗口 60/min", "429"], CYAN),
        ("并发闸门", ["信号量 + 排队", "503"], CYAN),
        ("后端调用", ["MCP / CLI / HTTP", "502"], GREEN),
        ("审计与指标", ["指纹 + 耗时", "/metrics"], PURPLE),
        ("返回", ["status + text", "request_id"], TEXT),
    ]
    x = 56
    y = 250
    bw = 200
    gap = 40
    centers = []
    for i, (title, lines, color) in enumerate(steps):
        box(d, (x, y, x + bw, y + 190), title, lines, color, tsize=21, lsize=16)
        centers.append((x, x + bw))
        if i < len(steps) - 1:
            arrow(d, (x + bw + 4, y + 95), (x + bw + gap - 4, y + 95), "", LINE)
        x += bw + gap

    # 备注
    d.rounded_rectangle([56, 500, 1624, 700], radius=14, fill=PANEL, outline=LINE, width=2)
    notes = [
        ("超时分层", "HTTP 30s 排队 / 单次 timeout_s / 后端默认 600s，谁先到谁负责"),
        ("失败可读", "502 带 detail（进程 stderr 尾部），不吞异常；429/503 可退避重试"),
        ("可追溯", "tenant / flow / prompt_sha256 / duration_ms 落 jsonl，原文不入库"),
    ]
    y = 524
    for key, val in notes:
        d.text((80, y), key, font=f(19, True), fill=CYAN)
        d.text((230, y), val, font=f(18), fill=TEXT)
        y += 56
    img.save(os.path.join(OUT, "v2-gateway-flow.png"))


# ------------------------------------------------------------------ 3. 部署拓扑
def deploy_topology():
    w, h = 1680, 940
    img, d = canvas(w, h, "生产部署拓扑（内网 + 受控出网）",
                    "机器人机不持有任何模型 key；凭证、并发、审计集中在集成服务器")

    d.rounded_rectangle([56, 160, 470, 800], radius=16, fill=(18, 26, 35), outline=LINE, width=2)
    d.text((80, 180), "机器人机 ×N", font=f(22, True), fill=AMBER)
    box(d, (86, 230, 440, 340), "ShadowBot 客户端", ["机器人账号登录（非个人）"], AMBER, tsize=20)
    box(d, (86, 360, 440, 470), "影刀应用", ["发布 → 授权给 AccessKey"], AMBER, tsize=20)
    box(d, (86, 490, 440, 600), "Python 代码块", ["sdk/rpa_agent_block.py"], GREEN, tsize=20)
    d.text((86, 640), "装 .env：", font=f(17), fill=MUTED)
    d.text((86, 672), "AGENTRPA_GATEWAY=http://10.0.0.8:8765", font=m(15), fill=TEXT)
    d.text((86, 700), "AGENTRPA_TOKEN=***", font=m(15), fill=TEXT)

    d.rounded_rectangle([560, 160, 1120, 800], radius=16, fill=(18, 26, 35), outline=CYAN, width=2)
    d.text((584, 180), "集成服务器（内网）", font=f(22, True), fill=CYAN)
    box(d, (584, 230, 1096, 350), "agentrpa-bridge gateway", ["Nginx + TLS 前置", "127.0.0.1 → 内网 8765"], CYAN, tsize=20)
    box(d, (584, 370, 830, 500), "审计 jsonl", ["artifacts/audit"], PURPLE, tsize=19)
    box(d, (850, 370, 1096, 500), "/metrics", ["Prometheus"], PURPLE, tsize=19)
    box(d, (584, 520, 1096, 640), "后端进程池", ["codex mcp-server / dsh / 模型 HTTP"], GREEN, tsize=20)
    d.text((584, 680), "凭证：AGENTRPA_TOKENS、模型 key（KMS/Vault）", font=f(17), fill=MUTED)
    d.text((584, 712), "AGENTRPA_ALLOW_ANON=0", font=m(15), fill=TEXT)

    d.rounded_rectangle([1210, 160, 1624, 800], radius=16, fill=(18, 26, 35), outline=LINE, width=2)
    d.text((1234, 180), "外部服务（出网白名单）", font=f(22, True), fill=GREEN)
    for i, (name, desc) in enumerate([
        ("影刀开放 API", "startJob / queryJob"),
        ("模型端点", "Ark / DeepSeek / vLLM"),
        ("包与镜像", "pip / npm（仅构建期）"),
    ]):
        box(d, (1234, 250 + i * 160, 1600, 380 + i * 160), name, [desc], GREEN, tsize=19, lsize=16)

    arrow(d, (442, 300), (556, 300), "HTTP 8765", CYAN)
    arrow(d, (1120, 600), (1206, 470), "HTTPS 出网", GREEN, above=False)

    d.text((56, 830), "关键约束：机器人并发数 = 物理机器人数量；网关并发按 min(机器人数, 模型 QPS) 设置，不按 CPU 设。",
           font=f(18), fill=MUTED)
    img.save(os.path.join(OUT, "v2-deploy-topology.png"))


# ------------------------------------------------------------------ 4. 终端截图
def quickstart():
    lines = [
        ("PS D:\\rpa-llm-bridge> python examples/local_demo.py", CYAN),
        ("网关已启动: http://127.0.0.1:51062  agents=['codex']", TEXT),
        ("", TEXT),
        ("[同步] 200 status=succeeded 86ms", GREEN),
        ("   text = 【假智能体】收到 16 个字符，结论：把 11300 含税金额拆出税额", TEXT),
        ("[异步] 202 run_id=ed5bf71a6e244c57", GREEN),
        ("   最终状态=succeeded  text=【假智能体】收到 6 个字符，结论：异步任务示例", TEXT),
        ("", TEXT),
        ("[指标片段]", MUTED),
        ('agentrpa_invoke_ok_total 2', TEXT),
        ('agentrpa_agent_avg_ms{agent="codex"} 70', TEXT),
        ("", TEXT),
        ("[审计] artifacts\\audit\\agent-20261104.jsonl", MUTED),
        ("    {'tenant': 'demo', 'flow': 'invoice_tax', 'status': 'succeeded',", TEXT),
        ("     'duration_ms': 86, 'prompt_sha256': '96631bf89e32fb0b'}", TEXT),
        ("", TEXT),
        ("PS D:\\rpa-llm-bridge> python -m pytest", CYAN),
        ("110 passed in 16.58s", GREEN),
    ]
    w = 1320
    h = 120 + len(lines) * 34 + 40
    img = Image.new("RGB", (w, h), (12, 17, 24))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 44], fill=(30, 41, 54))
    for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([18 + i * 26, 15, 34 + i * 26, 31], fill=color)
    d.text((110, 12), "Windows PowerShell — rpa-llm-bridge", font=f(17), fill=MUTED)
    y = 64
    # Consolas 没有中文字形，终端图用等宽中文字体（宋体/等线）才能显示中文
    mono = None
    for cand in ("C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/msyh.ttc"):
        if os.path.exists(cand):
            mono = ImageFont.truetype(cand, 18)
            break
    font = mono or m(18)
    for text, color in lines:
        d.text((28, y), text, font=font, fill=color)
        y += 34
    img.save(os.path.join(OUT, "v2-quickstart.png"))


# ------------------------------------------------------------------ 5. 护栏
def guardrails():
    w, h = 1680, 860
    img, d = canvas(w, h, "v2.1 企业护栏：反向触发 RPA 的四道闸门",
                    "拒绝是结构化 JSON（isError: true），智能体可直接分支处理")

    cards = [
        ("① 应用白名单", ["YINGDAO_ALLOWED_APPS", "白名单外直接拒绝", "不产生任务"],
         "app_not_allowed", CYAN),
        ("② 人工审批", ["YINGDAO_APPROVAL_APPS", "必须带 approved_by", "审批人工号 / 工单号"],
         "requires_approval", AMBER),
        ("③ 单应用并发", ["YINGDAO_MAX_PER_APP", "= 可用机器人数", "满则拒绝，不排队"],
         "busy", PINK),
        ("④ 幂等窗口", ["YINGDAO_IDEMPOTENCY_TTL", "sha256(app + 参数)", "重复提交返回同一 jobId"],
         "deduplicated", GREEN),
    ]
    x = 56
    for title, lines, code, color in cards:
        box(d, (x, 200, x + 372, 500), title, lines, color, tsize=23, lsize=17)
        d.rounded_rectangle([x + 24, 430, x + 348, 476], radius=8, fill=PANEL2, outline=color, width=1)
        d.text((x + 40, 440), code, font=m(18), fill=color)
        x += 412

    d.rounded_rectangle([56, 550, 1624, 760], radius=14, fill=PANEL, outline=LINE, width=2)
    d.text((84, 574), "rpa_policy  ·  智能体可自查护栏与在跑任务数", font=f(21, True), fill=CYAN)
    sample = ('{"whitelist_enforced": true, "approval_apps": ["app_reconcile"],\n'
              ' "max_per_app": 2, "idempotency_ttl_s": 900.0,\n'
              ' "inflight": {"app_invoice": 1},\n'
              ' "stats": {"denied": 1, "deduplicated": 2, "busy": 1, "approved": 1}}')
    yy = 616
    for line in sample.splitlines():
        d.text((84, yy), line, font=m(18), fill=TEXT)
        yy += 30
    img.save(os.path.join(OUT, "v21-guardrails.png"))


# ------------------------------------------------------------------ 6. 阶段
def phases():
    w, h = 1680, 760
    img, d = canvas(w, h, "企业落地四阶段",
                    "每阶段都有硬出口条件，不达标不进下一阶段")
    d.line([110, 300, 1570, 300], fill=LINE, width=4)
    data = [
        ("P0", "单机 PoC", "1 周", ["能列应用", "跑完一条真流程", "审计有记录"], CYAN),
        ("P1", "单产线灰度", "2–4 周", ["成功率 ≥ 98%", "失败可定位", "回滚 < 5 分钟"], GREEN),
        ("P2", "网关集群", "1–2 月", ["单节点故障无感", "新增流程 ≤ 0.5 人日"], AMBER),
        ("P3", "规模化治理", "持续", ["责任人清单", "提示词版本化", "成本看板"], PURPLE),
    ]
    x = 110
    for tag, name, dur, items, color in data:
        d.ellipse([x - 14, 286, x + 14, 314], fill=color)
        d.text((x - 10, 200), tag, font=f(30, True), fill=color)
        d.text((x + 44, 206), name, font=f(21, True), fill=TEXT)
        d.text((x + 44, 240), dur, font=f(17), fill=MUTED)
        yy = 340
        for item in items:
            d.text((x - 10, yy), "· " + item, font=f(18), fill=TEXT)
            yy += 40
        x += 372
    img.save(os.path.join(OUT, "v21-phases.png"))


if __name__ == "__main__":
    architecture()
    gateway_flow()
    deploy_topology()
    quickstart()
    guardrails()
    phases()
    for name in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, name)
        if name.startswith(("v2-", "v21-")):
            from PIL import Image as I
            with I.open(p) as im:
                print(f"{name:28} {im.size[0]}x{im.size[1]}  {os.path.getsize(p)//1024} KB")
