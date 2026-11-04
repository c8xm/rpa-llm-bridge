"""影刀（ShadowBot / YingDao）开放 API 与本地客户端封装。

企业里两种接法都能用：
  · 开放 API 模式：控制台的 AccessKeyId / AccessKeySecret，跨机器、跨网络，
    适合"流程跑在机器人池里，智能体在别处"的结构。
  · 本地模式：直接拉起本机 ShadowBot.exe，零凭据、低延迟，适合单机工位。

⚠️ 接口路径与签名规则以影刀开放平台控制台的当下文档为准，这里把两处都做成可替换：
   · `YingDaoOpenApi.paths`  —— 路径表，直接在构造时覆盖；
   · `YingDaoOpenApi.sign`   —— 签名算法，默认 HMAC-SHA256(path\\ntimestamp\\nbody)，
                                控制台若要求别的拼法，换成自己的 lambda 即可。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PATHS = {
    "app_list": ("POST", "/oapi/dispatch/v1/app/list"),
    "app_params": ("POST", "/oapi/dispatch/v1/app/param/list"),
    "start_job": ("POST", "/oapi/dispatch/v1/job/start"),
    "query_job": ("GET", "/oapi/dispatch/v1/job/detail"),
    "client_list": ("POST", "/oapi/dispatch/v1/client/list"),
    "upload": ("POST", "/oapi/file/v1/upload"),
}


class YingDaoError(RuntimeError):
    pass


class YingDaoOpenApi:
    def __init__(self, access_key_id: str = "", access_key_secret: str = "",
                 base_url: str = "", paths: dict | None = None,
                 signer=None, timeout: float = 60.0):
        self.access_key_id = access_key_id or os.environ.get("YINGDAO_ACCESS_KEY_ID", "")
        self.access_key_secret = access_key_secret or os.environ.get("YINGDAO_ACCESS_KEY_SECRET", "")
        self.base_url = (base_url or os.environ.get("YINGDAO_API_BASE",
                                                    "https://api.yingdao.com")).rstrip("/")
        self.paths = dict(DEFAULT_PATHS)
        if paths:
            self.paths.update(paths)
        self.signer = signer or self.sign
        self.timeout = timeout

    # ---------- 签名 ----------
    def sign(self, path: str, timestamp: str, body: bytes) -> str:
        payload = f"{path}\n{timestamp}\n".encode("utf-8") + body
        return hmac.new(self.access_key_secret.encode("utf-8"), payload,
                        hashlib.sha256).hexdigest()

    # ---------- 传输 ----------
    def _call(self, name: str, payload: dict | None = None, query: dict | None = None):
        method, path = self.paths[name]
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") \
            if method == "POST" else b""
        url = self.base_url + path
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        timestamp = str(int(time.time() * 1000))
        req = urllib.request.Request(url, data=body or None, method=method, headers={
            "Content-Type": "application/json",
            "x-access-key-id": self.access_key_id,
            "x-timestamp": timestamp,
            "x-signature": self.signer(path, timestamp, body),
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise YingDaoError(f"{name} HTTP {exc.code}: "
                               f"{exc.read().decode('utf-8', 'replace')[:300]}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise YingDaoError(f"{name} 连接失败: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise YingDaoError(f"{name} 返回非 JSON: {raw[:200]}") from exc
        if isinstance(data, dict) and data.get("code") not in (None, 0, "0", 200, "200"):
            raise YingDaoError(f"{name} 业务错误: {data.get('code')} {data.get('message') or data.get('msg')}")
        return data

    # ---------- 业务 ----------
    def list_apps(self, keyword: str = "", page: int = 1, size: int = 50) -> dict:
        return self._call("app_list", {"keyword": keyword, "page": page, "size": size})

    def app_params(self, app_id: str) -> dict:
        return self._call("app_params", {"appId": app_id})

    def start_job(self, app_id: str, params: dict | None = None,
                  client_id: str = "", priority: int = 0) -> dict:
        body = {"appId": app_id, "params": params or {}, "priority": priority}
        if client_id:
            body["clientId"] = client_id
        return self._call("start_job", body)

    def query_job(self, job_id: str) -> dict:
        return self._call("query_job", query={"jobId": job_id})

    def clients(self) -> dict:
        return self._call("client_list", {"page": 1, "size": 100})

    def upload_file(self, path: str) -> dict:
        """multipart 上传（stdlib 手工拼，避免引入 requests）。"""
        if not os.path.exists(path):
            raise YingDaoError(f"文件不存在: {path}")
        boundary = "----agentrpa" + str(int(time.time() * 1000))
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            content = fh.read()
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            content, b"\r\n", f"--{boundary}--\r\n".encode(),
        ])
        method, url_path = self.paths["upload"]
        timestamp = str(int(time.time() * 1000))
        req = urllib.request.Request(
            self.base_url + url_path, data=body, method=method,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "x-access-key-id": self.access_key_id,
                     "x-timestamp": timestamp,
                     "x-signature": self.signer(url_path, timestamp, body)})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))


class YingDaoLocal:
    """本地模式：直接拉起 ShadowBot 客户端跑应用。

    ShadowBot 的命令行参数随版本变化，这里用模板拼，env 里改：
      YINGDAO_SHADOWBOT   ShadowBot.exe 路径
      YINGDAO_LAUNCH_ARGS 启动参数模板，支持 {app} {params_json}
    """

    def __init__(self, exe: str = "", args_template: str = "", user_folder: str = ""):
        self.exe = exe or os.environ.get("YINGDAO_SHADOWBOT", "")
        self.args_template = args_template or os.environ.get(
            "YINGDAO_LAUNCH_ARGS", 'startapp --app "{app}" --params "{params_json}"')
        self.user_folder = user_folder or os.environ.get("YINGDAO_USER_FOLDER", "")

    def available(self) -> bool:
        return bool(self.exe and os.path.exists(self.exe))

    def run(self, app: str, params: dict | None = None, timeout: float = 600) -> dict:
        if not self.available():
            raise YingDaoError("未配置 YINGDAO_SHADOWBOT 或文件不存在")
        argv = [self.exe] + [
            part.format(app=app, params_json=json.dumps(params or {}, ensure_ascii=False))
            for part in _split_args(self.args_template)
        ]
        env = os.environ.copy()
        if self.user_folder:
            env["SHADOWBOT_USER_FOLDER"] = self.user_folder
        started = time.monotonic()
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
        return {"returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-2000:],
                "stderr": (proc.stderr or "")[-1000:],
                "duration_ms": int((time.monotonic() - started) * 1000)}


def _split_args(template: str) -> list:
    """按空格切分但保留引号内的空格（Windows 命令行友好）。"""
    out, buf, quote = [], [], None
    for ch in template:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch == " ":
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out
