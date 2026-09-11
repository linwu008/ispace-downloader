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
        self.resource_cache = {}
        self.pending_organization = 0

    def one(self, course, resource, index):
        from .grouped_sync import sync_one
        return sync_one(self, course, resource, index)

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
        pending_message = f"；{self.pending_organization} 项已有资料待确认整理" if self.pending_organization else ""
        self.store.finish(run_id, status, **counts, message="部分检查或下载失败，请查看详情并重试" if counts["failed"] else "已完成全部已绑定课程的检查" + pending_message)
        return {"status": status, "pending_organization": self.pending_organization, "message": ("部分检查失败" if counts["failed"] else "课程检查完成") + pending_message, **counts}
