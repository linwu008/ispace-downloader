from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from filelock import FileLock

from .grouping import UNKNOWN


def migrate(store):
    with FileLock(store.directory / "migration.lock", timeout=30):
        with store.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= 2:
            return
        backup = store.directory / "index-pre-v0.2.sqlite3"
        if not backup.exists():
            with sqlite3.connect(store.path) as src, sqlite3.connect(backup) as dst:
                src.backup(dst)
        with store.connect() as db:
            db.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS legacy_resources AS SELECT * FROM resources;
                CREATE TABLE IF NOT EXISTS teaching_groups (
                    id TEXT PRIMARY KEY, course_id INTEGER NOT NULL, source_key TEXT NOT NULL,
                    title TEXT NOT NULL, position INTEGER NOT NULL, folder TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'auto', revision INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(course_id,source_key));
                CREATE TABLE IF NOT EXISTS materials (
                    id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, source TEXT NOT NULL,
                    source_group TEXT NOT NULL, group_id TEXT NOT NULL, name TEXT NOT NULL,
                    url TEXT NOT NULL, source_page TEXT NOT NULL DEFAULT '',
                    manual_group INTEGER NOT NULL DEFAULT 0, path TEXT, digest TEXT,
                    status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(course_id,source,source_group));
                CREATE TABLE IF NOT EXISTS material_versions (
                    id INTEGER PRIMARY KEY, material_id INTEGER NOT NULL, path TEXT NOT NULL,
                    digest TEXT NOT NULL, UNIQUE(material_id,path,digest));
                CREATE TABLE IF NOT EXISTS organization_plans (
                    id TEXT PRIMARY KEY, created TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'preview');
                CREATE INDEX IF NOT EXISTS materials_filter ON materials(course_id,group_id,status);
                PRAGMA user_version=2;
                COMMIT;
            ''')


def course_for(store, course_id):
    course = next((c for c in store.courses() if c["id"] == course_id), None)
    if not course or not course["folder"]:
        raise ValueError("请先绑定课程目录")
    return course


def safe_folder(title):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .") or "待分类"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", title, re.I):
        title = "_" + title
    return title[:90].rstrip(" .")


def groups(store, course_id=None):
    with store.connect() as db:
        return [dict(r) for r in db.execute('''SELECT g.*,
            (SELECT COUNT(*) FROM materials m WHERE m.group_id=g.id) AS file_count,
            (SELECT COUNT(*) FROM materials m WHERE m.group_id=g.id AND m.status='pending_organize') AS pending_count
            FROM teaching_groups g WHERE (? IS NULL OR g.course_id=?) ORDER BY g.course_id,g.position,g.id''', (course_id, course_id))]


def group_by_id(store, group_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM teaching_groups WHERE id=?", (group_id,)).fetchone()
    if not row:
        raise ValueError("分组不存在，请刷新资料列表")
    return dict(row)


def target_dir(store, group, relative=None):
    root = Path(course_for(store, group["course_id"])["folder"]).resolve()
    relative = relative if relative is not None else group["folder"]
    child = Path(relative.replace("\\", "/"))
    if any(re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((" ", ".")) or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I) for part in child.parts):
        raise ValueError("目录名称包含 Windows 不支持的字符或保留名称")
    if child.is_absolute() or child.drive or not child.parts or any(p in {"..", ".git", ".ispace-temp"} for p in child.parts):
        raise ValueError("请选择课程目录内的专用子文件夹")
    target = root / child
    if target.resolve() == root or not target.resolve().is_relative_to(root):
        raise ValueError("分组目录不能越过课程目录")
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("分组目录不能通过链接指向其他位置")
    for other in groups(store, group["course_id"]):
        if other["id"] == group["id"]:
            continue
        path = (root / other["folder"]).resolve()
        if target.resolve() == path or target.resolve().is_relative_to(path) or path.is_relative_to(target.resolve()):
            raise ValueError("不同分组的目录不能相同或相互包含")
    return target


def auto_folder(group):
    if group["source_key"] in {"unknown", "public"}:
        return safe_folder(group["title"])
    position = group["position"]
    number = f"{position // 1000:02d}" + (f".{position % 1000:02d}" if position % 1000 else "")
    return number + " " + safe_folder(group["title"])


def ensure_group(store, course_id, descriptor=UNKNOWN):
    identifier = hashlib.sha256(f"{course_id}:{descriptor.key}".encode()).hexdigest()[:24]
    with store.connect() as db:
        row = db.execute("SELECT * FROM teaching_groups WHERE id=?", (identifier,)).fetchone()
        if row:
            db.execute("UPDATE teaching_groups SET revision=revision+CASE WHEN title<>? OR position<>? THEN 1 ELSE 0 END,title=?,position=? WHERE id=?", (descriptor.title, descriptor.position, descriptor.title, descriptor.position, identifier))
        else:
            group = {"id": identifier, "course_id": course_id, "source_key": descriptor.key, "title": descriptor.title, "position": descriptor.position}
            relative = auto_folder(group)
            try:
                course = next(c for c in store.courses() if c["id"] == course_id)
                if course["folder"]:
                    target_dir(store, {**group, "folder": relative})
                elif any(g["folder"].casefold() == relative.casefold() for g in groups(store, course_id)):
                    raise ValueError("同名目录")
            except ValueError:
                relative += " — " + identifier[:6]
            db.execute("INSERT INTO teaching_groups(id,course_id,source_key,title,position,folder) VALUES (?,?,?,?,?,?)", (identifier, course_id, descriptor.key, descriptor.title, descriptor.position, relative))
    return group_by_id(store, identifier)


def material(store, material_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
    if not row:
        raise ValueError("资料不存在")
    return dict(row)


def register(store, course_id, resource):
    group = ensure_group(store, course_id, resource.group)
    with store.connect() as db:
        row = db.execute("SELECT * FROM materials WHERE course_id=? AND source=? AND source_group=?", (course_id, resource.key, group["id"])).fetchone()
        if row:
            db.execute("UPDATE materials SET name=?,url=?,source_page=? WHERE id=?", (resource.name, resource.url, resource.source_page, row["id"]))
            return {**dict(row), "name": resource.name, "url": resource.url, "source_page": resource.source_page}
        legacy = db.execute("SELECT * FROM legacy_resources WHERE course_id=? AND source=?", (course_id, resource.key)).fetchone()
        path, digest = (legacy["path"], legacy["digest"]) if legacy else (None, None)
        status = "pending_organize" if legacy else "pending"
        item_id = db.execute("INSERT INTO materials(course_id,source,source_group,group_id,name,url,source_page,path,digest,status) VALUES (?,?,?,?,?,?,?,?,?,?)", (course_id, resource.key, group["id"], group["id"], resource.name, resource.url, resource.source_page, path, digest, status)).lastrowid
        if path and digest:
            db.execute("INSERT INTO material_versions(material_id,path,digest) VALUES (?,?,?)", (item_id, path, digest))
    return material(store, item_id)


def record(store, item_id, path, digest, status, error=""):
    with store.connect() as db:
        db.execute("UPDATE materials SET path=?,digest=?,status=?,error=? WHERE id=?", (str(path) if path else None, digest, status, error, item_id))
        if path and digest:
            db.execute("INSERT OR IGNORE INTO material_versions(material_id,path,digest) VALUES (?,?,?)", (item_id, str(path), digest))


def material_page(store, q="", course_id=None, group_id=None, status=None, page=1, page_size=30, include_removed=False):
    page_size, page = min(max(page_size, 1), 100), max(page, 1)
    # LIKE wildcard characters in user searches are literal.
    pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    condition = r"""WHERE (? IS NULL OR m.course_id=?) AND (? IS NULL OR m.group_id=?)
        AND (m.name LIKE ? ESCAPE '\' OR g.title LIKE ? ESCAPE '\' OR c.name LIKE ? ESCAPE '\')"""
    params = (course_id, course_id, group_id, group_id, pattern, pattern, pattern)
    if not include_removed:
        condition += " AND c.membership='added'"
    joined = " FROM materials m JOIN teaching_groups g ON g.id=m.group_id JOIN courses c ON c.id=m.course_id "
    with store.connect() as db:
        rows = [dict(r) for r in db.execute("SELECT m.*,g.title AS group_title,g.folder AS group_folder,c.name AS course_name,c.sync_mode,c.membership" + joined + condition + " ORDER BY c.name,g.position,m.name,m.id", params)]
    for row in rows:
        row["exists"] = bool(row["path"] and Path(row["path"]).is_file())
        if row["path"] and not row["exists"] and row["status"] != "failed":
            row["status"] = "missing"
    if status:
        rows = [row for row in rows if row["status"] == status]
    total = len(rows)
    return {"items": rows[(page-1)*page_size:page*page_size], "total": total, "page": page, "page_size": page_size}
