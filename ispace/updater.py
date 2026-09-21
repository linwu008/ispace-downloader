"""Signed releases, side-by-side installation, and rollback; never overwrite user data."""

from contextlib import nullcontext
import base64
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Public verification key is populated by the release preparation script.
PUBLIC_KEY = "64lvrObQsgkPEdw5vc01R+GZfG1r/PUEMwXuyKiP9I0="
ORIGIN = "https://bnbucoursenest.cn"
MAX_PACKAGE = 300_000_000


def version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value or ""):
        raise ValueError("更新版本号无效")
    return tuple(map(int, value.split(".")))


def signed_bytes(meta):
    return json.dumps(
        {k: meta[k] for k in ("version", "url", "sha256")},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def verify(meta, public_key=PUBLIC_KEY):
    version(meta.get("version"))
    if not re.fullmatch(r"[a-f0-9]{64}", meta.get("sha256") or ""):
        raise ValueError("更新包校验值无效")
    u = urlsplit(meta.get("url") or "")
    if (
        u.scheme != "https"
        or u.netloc != "github.com"
        or not u.path.startswith("/linwu008/coursenest-releases/releases/download/")
    ):
        raise ValueError("更新来源不受信任")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        ).verify(
            base64.b64decode(meta.get("signature") or "", validate=True),
            signed_bytes(meta),
        )
    except Exception as exc:
        raise ValueError("更新签名校验失败，保留当前版本") from exc


def unpack(data, meta, destination):
    if hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise ValueError("更新包不完整，保留当前版本")
    destination = Path(destination).resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if (
            sum(i.file_size for i in archive.infolist()) > 1_000_000_000
            or len(archive.infolist()) > 20000
        ):
            raise ValueError("更新包大小异常")
        paths = []
        names = set()
        for info in archive.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            mode = info.external_attr >> 16
            folded = str(name).casefold().rstrip("/")
            if folded in names or any(
                p.rstrip(" .") != p
                or re.fullmatch(
                    r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.I
                )
                for p in name.parts
            ):
                raise ValueError("更新包包含重复或保留路径")
            names.add(folded)
            if (
                name.is_absolute()
                or ".." in name.parts
                or any(":" in p for p in name.parts)
                or stat.S_ISLNK(mode)
            ):
                raise ValueError("更新包包含不安全路径")
            if not name.parts or name.parts[0] != "CourseNestHelper":
                raise ValueError("更新包结构无效")
            paths.append((info, destination.joinpath(*name.parts)))
        if not any(
            p == destination / "CourseNestHelper" / "CourseNestHelper.exe"
            and not info.is_dir()
            for info, p in paths
        ):
            raise ValueError("更新包缺少助手")
        for info, path in paths:
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, path.open("xb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
    return destination / "CourseNestHelper" / "CourseNestHelper.exe"


def switch_script(
    candidate, old, data_dir, expected_version, parent_pid, port=8765, attempts=40
):
    q = lambda p: "'" + str(p).replace("'", "''") + "'"
    return f"""$ErrorActionPreference='Stop'
$env:ISPACE_DATA_DIR={q(data_dir)}
Wait-Process -Id {parent_pid} -ErrorAction SilentlyContinue
$candidate=$null
$healthy=$false
try{{
$candidate=Start-Process -FilePath {q(candidate)} -PassThru -WindowStyle Hidden
$healthy=$false
for($attempt=0;$attempt -lt {attempts};$attempt++){{
  Start-Sleep -Milliseconds 500
  try{{$state=Invoke-RestMethod -Uri 'http://127.0.0.1:{port}/api/state' -TimeoutSec 2;if($state.version -eq {q(expected_version)}){{$healthy=$true;break}}}}catch{{}}
}}
}}catch{{}}
if(-not $healthy){{
  if($candidate -and -not $candidate.HasExited){{$candidate.Kill();$candidate.WaitForExit()}}
  Set-Content -LiteralPath {q(data_dir / "update-rollback.txt")} -Value {q(expected_version)}
  Start-Process -FilePath {q(old)} -WindowStyle Hidden
}}else{{
  @{{path={q(candidate)};version={q(expected_version)}}} | ConvertTo-Json | Set-Content -LiteralPath {q(data_dir / 'active-helper.json')} -Encoding UTF8
  $key='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'
  $existing=Get-ItemPropertyValue -LiteralPath $key -Name BNBUCourseNest -ErrorAction SilentlyContinue
  if($existing){{Set-ItemProperty -LiteralPath $key -Name BNBUCourseNest -Value ('"'+{q(candidate)}+'"')}}
}}
"""


def preferred_executable(data_dir, current_version):
    """Original shortcuts follow the last healthy side-by-side installation."""
    data_dir = Path(data_dir).resolve()
    try:
        active = json.loads((data_dir / "active-helper.json").read_text("utf-8-sig"))
        target = Path(active["path"])
        if (
            version(active["version"]) > version(current_version)
            and target.name == "CourseNestHelper.exe"
            and target.is_file()
            and not target.is_symlink()
            and target.resolve().is_relative_to(data_dir / "versions")
        ):
            return target
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


class Updater:
    def __init__(self, store, service):
        self.store, self.service = store, service
        self.meta = None
        self.state = {"status": "idle", "message": "自动检查更新；点击后升级"}
        self.companion = None
        receipt = self.store.directory / "update-rollback.txt"
        self.failed_version = (
            receipt.read_text(encoding="utf-8-sig").strip()
            if receipt.exists()
            else None
        )
        if receipt.exists():
            self.state = {
                "status": "error",
                "message": "上次升级启动失败，已恢复原版本；配置和文件保留",
            }

        self.shutdown = None
        self.lock = threading.Lock()

    def check(self):
        from . import __version__

        try:
            r = httpx.get(ORIGIN + "/api/v07/update", timeout=15, trust_env=False)
            r.raise_for_status()
            meta = r.json()
            if version(meta["version"]) <= version(__version__):
                self.state = {
                    "status": "current",
                    "message": "助手已是最新版本",
                    "version": __version__,
                }
                return self.state
            if meta["version"] == self.failed_version:
                raise ValueError("上次升级启动失败，已恢复旧版；此更新暂不自动重试")
            verify(meta)
            self.meta = meta
            required = version(__version__) < version(meta.get("min_version", "0.4.0"))
            self.state = {
                "status": "available",
                "version": meta["version"],
                "required": required,
                "message": ("需要升级：" if required else "可选更新：")
                + str(meta.get("reason", "新增电脑功能"))
                + "；配对、目录和文件保留",
            }
        except Exception as exc:
            self.state = {
                "status": "error",
                "message": (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "暂时无法检查更新，当前版本仍可使用"
                ),
            }
        return self.state

    def install(self):
        if not self.lock.acquire(blocking=False):
            raise ValueError("更新正在准备中")
        try:
            if not getattr(sys, "frozen", False) or not self.shutdown:
                raise ValueError("源码运行环境请使用正式助手更新")
            if self.service.busy():
                raise ValueError("请等待当前任务结束后升级")
            if not self.meta:
                raise ValueError("请先检查更新")
            verify(self.meta)
            self.state = {
                "status": "downloading",
                "message": "正在下载并验证更新，旧版本会保留",
            }
            with httpx.Client(
                timeout=60, trust_env=False, follow_redirects=False
            ) as client:
                url = self.meta["url"]
                for _ in range(6):
                    u = urlsplit(url)
                    if (
                        u.scheme != "https"
                        or u.hostname
                        not in {
                            "github.com",
                            "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com",
                        }
                        or u.username
                        or u.password
                    ):
                        raise ValueError("更新下载跳转不受信任")
                    with client.stream("GET", url) as r:
                        if r.is_redirect:
                            url = r.headers["location"]
                            continue
                        r.raise_for_status()
                        parts = []
                        size = 0
                        for chunk in r.iter_bytes():
                            size += len(chunk)
                            if size > MAX_PACKAGE:
                                raise ValueError("更新包过大")
                            parts.append(chunk)
                        break
                else:
                    raise ValueError("更新下载跳转过多")
            folder = (
                self.store.directory
                / "versions"
                / (self.meta["version"] + "-" + str(time.time_ns()))
            )
            candidate = unpack(b"".join(parts), self.meta, folder)
            connector = getattr(self, "companion", None)
            with connector.lock() if connector else nullcontext(), self.service.lock():
                # Independent data location survives either candidate or rollback startup.
                old = Path(sys.executable).resolve()
                script = self.store.directory / "apply-update.ps1"
                script.write_text(
                    switch_script(
                        candidate,
                        old,
                        self.store.directory,
                        self.meta["version"],
                        os.getpid(),
                    ),
                    encoding="utf-8-sig",
                )
                subprocess.Popen(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                    ],
                    creationflags=0x08000000,
                )
                self.state = {
                    "status": "restarting",
                    "message": "更新已验证，正在切换版本",
                }
                self.shutdown()
        except Exception as exc:
            self.state = {
                "status": "error",
                "message": (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "更新失败，当前版本和资料已保留"
                ),
            }
            raise
        finally:
            self.lock.release()

    def loop(self, stop):
        while not stop.is_set():
            self.check()
            if (
                self.state["status"] == "available"
                and self.store.setting("auto_update", False)
                and not self.service.busy()
            ):
                try:
                    self.install()
                except Exception:
                    pass
            stop.wait(3600)
