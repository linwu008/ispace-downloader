from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .moodle import ResourceError, safe_error
from .security import LoginRequired


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "attachment"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I):
        name = "_" + name
    suffix = Path(name).suffix[:20]
    return (Path(name).stem[:100].rstrip(" .") or "attachment") + suffix


def scan(folder: Path):
    index = {}
    if not folder.is_dir():
        raise ResourceError("课程目录不可用，请确认磁盘已连接且目录存在")
    for current, directories, files in os.walk(folder, followlinks=False, onerror=lambda error: (_ for _ in ()).throw(error)):
        directories[:] = [name for name in directories if name not in {".git", ".ispace-temp"} and not (Path(current) / name).is_symlink() and not (hasattr(Path(current) / name, "is_junction") and (Path(current) / name).is_junction())]
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or name.endswith(".part"):
                continue
            if not path.resolve().is_relative_to(folder.resolve()):
                continue
            index.setdefault(digest(path), path)
    return index


def strong_etag(headers):
    value = headers.get("etag", "")
    return value if value.startswith('"') and value.endswith('"') and not value.startswith('W/') else None


class SyncEngine:
    def __init__(self, store, platform, sleep=time.sleep):
        self.store, self.platform, self.sleep = store, platform, sleep

    def one(self, course, resource, index):
        folder = Path(course["folder"]).resolve()
        previous = self.store.resource(course["id"], resource.key)
        headers = self.platform.metadata(resource)
        etag = strong_etag(headers)
        if previous and etag and etag == previous["etag"] and previous["digest"] in index:
            local = index[previous["digest"]]
            self.store.remember(course["id"], resource.key, local, previous["digest"], etag, headers.get("last-modified"), local.stat().st_size)
            return "skipped", "远端版本未变，本地内容已核对"
        temporary_dir = folder / ".ispace-temp"
        temporary_dir.mkdir(exist_ok=True)
        if temporary_dir.is_symlink() or not temporary_dir.resolve().is_relative_to(folder):
            raise ResourceError("临时目录不能指向课程目录之外")
        handle, filename = tempfile.mkstemp(suffix=".part", dir=temporary_dir)
        os.close(handle)
        temporary = Path(filename)
        try:
            download_headers = self.platform.download(resource, temporary)
            content_hash = digest(temporary)
            local = index.get(content_hash)
            if local and local.exists() and digest(local) == content_hash:
                status, message = "skipped", "内容已存在，未保存重复副本"
            else:
                name = safe_name(resource.name)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                name = f"{Path(name).stem}_{stamp}_{content_hash[:8]}{Path(name).suffix}"
                local = folder / name
                for attempt in range(1000):
                    target = local if attempt == 0 else local.with_name(f"{local.stem}_{attempt}{local.suffix}")
                    try:
                        if os.name == "nt":
                            os.rename(temporary, target)  # Windows rename fails instead of replacing an existing file.
                        else:
                            os.link(temporary, target)
                            temporary.unlink()
                        local = target
                        break
                    except FileExistsError:
                        continue
                else:
                    raise ResourceError("同名文件过多，未覆盖已有文件")
                index[content_hash] = local
                status, message = "downloaded", local.name
            self.store.remember(course["id"], resource.key, local, content_hash, strong_etag(download_headers), download_headers.get("last-modified"), local.stat().st_size)
            return status, message
        finally:
            temporary.unlink(missing_ok=True)

    def run(self, run_id):
        counts = {"downloaded": 0, "skipped": 0, "failed": 0}
        courses = [c for c in self.store.courses() if c["enabled"] and c["folder"]]
        if not courses:
            raise ResourceError("请先为至少一门课程绑定目录并启用同步")
        for course in courses:
            try:
                folder = Path(course["folder"]).resolve()
                index = scan(folder)
                discovery = self.platform.discover(course["id"])
                for error in discovery.errors:
                    counts["failed"] += 1
                    self.store.event(run_id, course["id"], course["name"], "failed", error)
                for resource in discovery.resources:
                    for attempt in range(4):  # Initial attempt plus at most three retries.
                        try:
                            status, message = self.one(course, resource, index)
                            counts[status] += 1
                            self.store.event(run_id, course["id"], resource.name, status, message)
                            break
                        except LoginRequired:
                            raise
                        except Exception as exc:
                            if attempt == 3:
                                counts["failed"] += 1
                                self.store.event(run_id, course["id"], resource.name, "failed", safe_error(exc))
                            else:
                                self.sleep(2 ** attempt)
            except LoginRequired:
                self.store.finish(run_id, "auth_required", **counts, message="登录过期，检查尚未完成，请重新登录后重试")
                raise
            except Exception as exc:
                counts["failed"] += 1
                self.store.event(run_id, course["id"], course["name"], "failed", safe_error(exc))
        status = "partial" if counts["failed"] else "success"
        self.store.finish(run_id, status, **counts, message="部分检查或下载失败，请查看详情并重试" if counts["failed"] else "已完成全部已绑定课程的检查")
        return {"status": status, **counts}
