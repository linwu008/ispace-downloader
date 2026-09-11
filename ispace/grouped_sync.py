from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from . import catalog


def verified(path, expected, root):
    from .sync import digest
    if not path or not expected:
        return False
    path = Path(path)
    return path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and digest(path) == expected


def atomic_copy(source, destination):
    """Copy first, then publish without replacing an existing destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, filename = tempfile.mkstemp(prefix=".ispace-", suffix=".part", dir=destination.parent)
    os.close(handle)
    temporary = Path(filename)
    try:
        from .sync import digest
        expected = digest(source)
        with Path(source).open("rb") as src, temporary.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if digest(temporary) != expected:
            raise ValueError("复制后的内容校验失败，未保存目标文件")
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
            temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def sync_one(engine, course, resource, index):
    from .sync import digest, safe_name, strong_etag
    store = engine.store
    item = catalog.register(store, course["id"], resource)
    group = catalog.group_by_id(store, item["group_id"])
    root = Path(course["folder"]).resolve()
    temporary = None
    fresh = False
    cache_key = (course["id"], resource.key)
    try:
        folder = catalog.target_dir(store, group)
        folder.mkdir(parents=True, exist_ok=True)
        cached = engine.resource_cache.get(cache_key)
        if cached and verified(cached[1], cached[0], root):
            content_hash, source, headers = cached
        else:
            previous = store.resource(course["id"], resource.key)
            headers = engine.platform.metadata(resource)
            etag = strong_etag(headers)
            known = index.get(previous["digest"]) if previous else None
            if previous and etag and etag == previous["etag"] and verified(known, previous["digest"], root):
                content_hash, source = previous["digest"], Path(known)
            else:
                temporary_dir = root / ".ispace-temp"
                temporary_dir.mkdir(exist_ok=True)
                if temporary_dir.is_symlink() or not temporary_dir.resolve().is_relative_to(root):
                    raise ValueError("临时目录不能越过课程目录")
                handle, filename = tempfile.mkstemp(suffix=".part", dir=temporary_dir)
                os.close(handle)
                temporary = Path(filename)
                headers = engine.platform.download(resource, temporary)
                content_hash = digest(temporary)
                source = index.get(content_hash)
                if not verified(source, content_hash, root):
                    source, fresh = temporary, True
        source = Path(source)
        own_path = item["path"] if verified(item["path"], content_hash, root) else None
        if own_path and Path(own_path).resolve().is_relative_to(folder.resolve()):
            local, status, message = Path(own_path), "existing", "分组内内容已存在"
        else:
            matches = [p for p in folder.rglob("*") if p.is_file() and not p.name.endswith(".part") and not p.is_symlink() and p.resolve().is_relative_to(folder.resolve())]
            local = next((p for p in matches if p.stat().st_size == source.stat().st_size and digest(p) == content_hash), None)
            if local:
                status, message = "existing", "分组内内容已存在"
            else:
                with store.connect() as db:
                    managed = db.execute("SELECT 1 FROM materials WHERE path=? AND status IN ('downloaded','existing') LIMIT 1", (str(source),)).fetchone()
                pending = bool(own_path and item["status"] == "pending_organize") or (not fresh and not managed)
                if pending:
                    local, status, message = Path(own_path) if own_path else source, "pending_organize", "已有资料等待整理预览确认，原文件未移动"
                else:
                    name = safe_name(resource.name)
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    base = folder / f"{Path(name).stem}_{stamp}_{content_hash[:8]}{Path(name).suffix}"
                    for attempt in range(1000):
                        local = base if attempt == 0 else base.with_name(f"{base.stem}_{attempt}{base.suffix}")
                        try:
                            atomic_copy(source, local)
                            break
                        except FileExistsError:
                            continue
                    else:
                        raise ValueError("文件名冲突过多，未覆盖任何文件")
                    status, message = "downloaded", ("已下载：" if fresh else "复用本地内容：") + str(local.relative_to(root))
        index[content_hash] = local
        store.remember(course["id"], resource.key, local, content_hash, strong_etag(headers), headers.get("last-modified"), local.stat().st_size)
        catalog.record(store, item["id"], local, content_hash, status)
        engine.resource_cache[cache_key] = (content_hash, local, headers)
        if status == "pending_organize":
            engine.pending_organization += 1
        return ("downloaded" if status == "downloaded" else "skipped"), message
    except Exception as exc:
        from .moodle import safe_error
        with store.connect() as db:
            db.execute("UPDATE materials SET status='failed',error=? WHERE id=?", (safe_error(exc), item["id"]))
        raise
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
