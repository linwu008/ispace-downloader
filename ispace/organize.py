from __future__ import annotations

import json
import os
import stat
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from . import catalog
from .grouped_sync import atomic_copy, verified
from .moodle import safe_error
from .state import now
from .sync import digest


def _save(store, plan, status):
    with store.connect() as db:
        db.execute("UPDATE organization_plans SET payload=?,status=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), status, plan["id"]))


def load(store, identifier):
    with store.connect() as db:
        row = db.execute("SELECT payload,status FROM organization_plans WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ValueError("整理预览不存在，请重新生成")
    return {**json.loads(row["payload"]), "status": row["status"]}


def history(store):
    with store.connect() as db:
        rows = db.execute("SELECT id,created,status,payload FROM organization_plans ORDER BY created DESC LIMIT 15").fetchall()
    course_names = {str(c['id']): c['name'] for c in store.courses()}
    group_names = {g['id']: g['title'] for g in catalog.groups(store)}
    result = []
    for row in rows:
        plan = json.loads(row['payload'])
        result.append({'id': row['id'], 'created': row['created'], 'status': row['status'], 'result': plan.get('result'),
                       'course_names': [course_names.get(key, '未知课程') for key in plan.get('roots', {})],
                       'group_titles': list(dict.fromkeys(group_names[key] for key in plan.get('groups', {}) if key in group_names))})
    return result


def preview(store, course_id=None, group_id=None, folder=None, mode=None, item_id=None, target_group_id=None):
    change = None
    if group_id and mode:
        group = catalog.group_by_id(store, group_id)
        course_id = group["course_id"]
        if mode not in {"auto", "manual"}:
            raise ValueError("无效的目录模式")
        relative = catalog.auto_folder(group) if mode == "auto" else (folder or "").strip()
        target = catalog.target_dir(store, group, relative)
        change = {"kind": "group", "id": group_id, "folder": str(target.relative_to(Path(catalog.course_for(store, course_id)["folder"]).resolve())), "mode": mode, "applied": False}
    elif item_id is not None:
        item = catalog.material(store, item_id)
        group = catalog.group_by_id(store, target_group_id)
        if group["course_id"] != item["course_id"]:
            raise ValueError("只能调整到同一课程的分组")
        course_id = item["course_id"]
        change = {"kind": "material", "id": item_id, "group_id": group["id"], "applied": False}
    with store.connect() as db:
        items = [dict(row) for row in db.execute("SELECT * FROM materials WHERE (? IS NULL OR course_id=?) AND (? IS NULL OR group_id=?) AND (? IS NULL OR id=?)", (course_id, course_id, group_id, group_id, item_id, item_id))]
    plan = {"id": uuid.uuid4().hex, "created": now(), "change": change, "groups": {}, "items": {}, "roots": {}, "rows": []}
    if change:
        plan["rows"].append({"id": "settings", "action": "settings", "name": "应用目录设置" if change["kind"] == "group" else "应用资料分组", "source": "", "target": change.get("folder", group["title"]), "reason": "后续同步会沿用此设置；未选择整理的旧文件保留原位", "selectable": True, "status": "ready"})
        involved = [catalog.group_by_id(store, change["id"])] if change["kind"] == "group" else [catalog.group_by_id(store, change["group_id"])]
        for value in involved:
            plan["groups"][value["id"]] = {"revision": value["revision"], "folder": value["folder"]}
            plan["roots"][str(value["course_id"])] = catalog.course_for(store, value["course_id"])["folder"]
    for item in items:
        root = Path(catalog.course_for(store, item["course_id"])["folder"]).resolve()
        plan["roots"][str(item["course_id"])] = str(root)
        destination_group = change["group_id"] if change and change["kind"] == "material" else item["group_id"]
        group = catalog.group_by_id(store, destination_group)
        current_group = catalog.group_by_id(store, item["group_id"])
        for value in (group, current_group):
            plan["groups"][value["id"]] = {"revision": value["revision"], "folder": value["folder"]}
        plan["items"][str(item["id"])] = {"revision": item["revision"], "group_id": item["group_id"]}
        relative = change["folder"] if change and change["kind"] == "group" else group["folder"]
        destination = catalog.target_dir(store, group, relative)
        with store.connect() as db:
            versions = [dict(v) for v in db.execute("SELECT * FROM material_versions WHERE material_id=?", (item["id"],))]
        if not versions:
            plan["rows"].append({"id": f"unavailable-{item['id']}", "action": "skip", "name": item["name"], "source": "", "target": str(destination), "reason": "没有已校验的本地版本，后续下载使用新分组", "selectable": False, "status": "skipped"})
        for version in versions:
            source = Path(version["path"])
            target = destination / source.name
            if source.resolve().is_relative_to(destination.resolve()) and (not change or target.parent == source.parent):
                continue
            row = {"id": str(version["id"]), "version_id": version["id"], "item_id": item["id"], "course_id": item["course_id"], "group_id": destination_group, "name": item["name"], "source": str(source), "target": str(target), "digest": version["digest"], "action": "move", "reason": "先复制并校验，再清理不再被引用的原件", "selectable": True, "status": "ready"}
            if not verified(source, version["digest"], root):
                row.update(action="skip", reason="原文件缺失、内容已修改或路径不在课程目录内，保留原状", selectable=False, status="skipped")
            elif target.exists():
                if verified(target, version["digest"], root):
                    row.update(action="reuse", reason="目标已有相同内容，复用后再处理原件")
                else:
                    row.update(action="skip", reason="目标文件已存在且内容不同，不能覆盖", selectable=False, status="skipped")
            plan["rows"].append(row)
    # Unindexed files and historical versions cannot be moved safely by inference.
    if not change and not group_id:
        for course in store.courses():
            if not course["folder"] or (course_id is not None and course["id"] != course_id):
                continue
            root = Path(course["folder"]).resolve()
            with store.connect() as db:
                known = {Path(v[0]).resolve() for v in db.execute("SELECT path FROM material_versions")}
            for file in root.rglob("*"):
                if file.is_file() and file.resolve() not in known and ".ispace-temp" not in file.parts and not file.name.endswith(".part"):
                    plan["rows"].append({"id": "unmatched-" + uuid.uuid4().hex, "action": "skip", "name": file.name, "source": str(file), "target": "", "reason": "未关联资料或无法匹配的旧版本，保留原位置", "selectable": False, "status": "skipped"})
    sources = Counter(r.get("source") for r in plan["rows"] if r["selectable"] and r.get("source"))
    destinations = defaultdict(list)
    for row in plan["rows"]:
        if row.get("source") and row["selectable"]:
            destinations[str(Path(row["target"]).resolve())].append(row)
            if sources[row["source"]] > 1 and row["action"] == "move":
                row.update(action="copy", reason="多个分组需要此文件，先完成各副本再处理原件")
    for rows in destinations.values():
        if len({r["digest"] for r in rows}) > 1:
            for row in rows:
                row.update(action="skip", reason="多个不同内容的原文件将使用同一目标名称，请先调整目录或文件名", selectable=False, status="skipped")
    with store.connect() as db:
        db.execute("INSERT INTO organization_plans(id,created,payload) VALUES (?,?,?)", (plan["id"], plan["created"], json.dumps(plan, ensure_ascii=False)))
    return plan


def execute(store, identifier, selected):
    plan = load(store, identifier)
    selected = set(selected)
    allowed = {r["id"] for r in plan["rows"] if r["selectable"]}
    if not selected or not selected <= allowed:
        raise ValueError("请选择预览中可执行的项目")
    if plan["change"] and "settings" not in selected and not plan["change"]["applied"]:
        raise ValueError("整理资料时需要同时应用对应的分组设置")
    for course_id, expected in plan["roots"].items():
        if Path(catalog.course_for(store, int(course_id))["folder"]).resolve() != Path(expected).resolve():
            raise ValueError("课程目录已变化，请重新生成整理预览")
    for group_id, expected in plan["groups"].items():
        actual = catalog.group_by_id(store, group_id)
        if actual["revision"] != expected["revision"] or actual["folder"] != expected["folder"]:
            raise ValueError("分组目录设置已变化，请重新生成预览")
    for item_id, expected in plan["items"].items():
        actual = catalog.material(store, int(item_id))
        if actual["revision"] != expected["revision"] or actual["group_id"] != expected["group_id"]:
            raise ValueError("资料归属已变化，请重新生成预览")
    change = plan["change"]
    if change and not change["applied"]:
        with store.connect() as db:
            if change["kind"] == "group":
                group = catalog.group_by_id(store, change["id"])
                catalog.target_dir(store, group, change["folder"]).mkdir(parents=True, exist_ok=True)
                db.execute("UPDATE teaching_groups SET folder=?,mode=?,revision=revision+1 WHERE id=?", (change["folder"], change["mode"], change["id"]))
                db.execute("UPDATE materials SET status=CASE WHEN path IS NULL THEN status ELSE 'pending_organize' END WHERE group_id=?", (change["id"],))
                plan["groups"][group["id"]] = {"revision": group["revision"] + 1, "folder": change["folder"]}
            else:
                old = catalog.material(store, change["id"])
                db.execute("UPDATE materials SET group_id=?,manual_group=1,revision=revision+1,status=CASE WHEN path IS NULL THEN status ELSE 'pending_organize' END WHERE id=?", (change["group_id"], change["id"]))
                plan["items"][str(old["id"])] = {"revision": old["revision"] + 1, "group_id": change["group_id"]}
            change["applied"] = True
            next(r for r in plan["rows"] if r["id"] == "settings")["status"] = "done"
            db.execute("UPDATE organization_plans SET payload=?,status='running' WHERE id=?", (json.dumps(plan, ensure_ascii=False), plan["id"]))
    _save(store, plan, "running")
    done, failed, resumed = 0, 0, 0
    for row in plan["rows"]:
        if row["id"] not in selected or row["id"] == "settings":
            continue
        if row["status"] == "done":
            resumed += 1
            continue
        source, target = Path(row["source"]), Path(row["target"])
        root = Path(plan["roots"][str(row["course_id"])]).resolve()
        try:
            group = catalog.group_by_id(store, row["group_id"])
            destination = catalog.target_dir(store, group)
            if target.parent.resolve() != destination.resolve():
                raise ValueError("预览目标目录已失效")
            with store.connect() as db:
                version = db.execute("SELECT * FROM material_versions WHERE id=?", (row["version_id"],)).fetchone()
            if not version or version["digest"] != row["digest"] or Path(version["path"]) not in {source, target}:
                raise ValueError("文件索引发生变化，请重新预览")
            already_published = Path(version["path"]) == target and verified(target, row["digest"], root)
            if not already_published:
                if not verified(source, row["digest"], root):
                    raise ValueError("原文件内容或位置已变化，未移动")
                if target.exists():
                    if not verified(target, row["digest"], root):
                        raise ValueError("目标已有不同内容，未覆盖")
                else:
                    atomic_copy(source, target)
                if not verified(target, row["digest"], root):
                    raise ValueError("目标校验失败，保留原件")
                with store.connect() as db:
                    duplicate = db.execute("SELECT id FROM material_versions WHERE material_id=? AND path=? AND digest=? AND id<>?", (row["item_id"], str(target), row["digest"], row["version_id"])).fetchone()
                    if duplicate:
                        db.execute("DELETE FROM material_versions WHERE id=?", (duplicate["id"],))
                    db.execute("UPDATE material_versions SET path=? WHERE id=?", (str(target), row["version_id"]))
                    db.execute("UPDATE materials SET path=?,status='existing',error='' WHERE id=? AND path=? AND digest=?", (str(target), row["item_id"], str(source), row["digest"]))
            row.update(status="done", result="目标已校验，索引已更新")
            done += 1
        except Exception as exc:
            row.update(status="failed", result=safe_error(exc))
            failed += 1
        _save(store, plan, "running")
    # Never remove an original until every catalog reference has a verified destination.
    for row in plan["rows"]:
        if row["id"] not in selected or row["status"] != "done" or not row.get("source"):
            continue
        source = Path(row["source"])
        root = Path(plan["roots"][str(row["course_id"])]).resolve()
        with store.connect() as db:
            remaining = db.execute("SELECT 1 FROM material_versions WHERE path=? LIMIT 1", (str(source),)).fetchone()
        if not remaining and source.exists():
            try:
                if not verified(source, row["digest"], root):
                    raise ValueError("目标已保存，但原件后来发生变化，已保留")
                related = [r for r in plan["rows"] if r.get("source") == str(source) and r.get("status") == "done"]
                if not all(verified(Path(r["target"]), r["digest"], root) for r in related):
                    raise ValueError("目标副本发生变化，保留原件以便重新检查")
                original_mode = source.stat().st_mode
                read_only = os.name == "nt" and not (original_mode & stat.S_IWRITE)
                if read_only:
                    for copy in related:
                        destination_copy = Path(copy["target"])
                        destination_copy.chmod(destination_copy.stat().st_mode & ~stat.S_IWRITE)
                    source.chmod(original_mode | stat.S_IWRITE)
                try:
                    source.unlink()
                except OSError:
                    if read_only and source.exists():
                        source.chmod(original_mode)
                    raise
                with store.connect() as db:
                    db.execute("UPDATE resources SET path=? WHERE course_id=? AND path=? AND digest=?", (row["target"], row["course_id"], str(source), row["digest"]))
            except Exception as exc:
                row["result"] = safe_error(exc)
                row["status"] = "failed"
                failed += 1
    result = {"status": "partial" if failed else "success", "organized": done, "already_done": resumed, "failed": failed, "message": f"整理完成 {done} 项，已完成跳过 {resumed} 项，失败 {failed} 项；未选择的文件保留原位"}
    plan["result"] = result
    _save(store, plan, result["status"])
    return result
