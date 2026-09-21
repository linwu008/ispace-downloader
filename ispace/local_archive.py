"""Durable local file references. Course text is uploaded from memory, never cached here."""

import hashlib
import html
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from .cancellation import check as checkpoint


def schema(store):
    with store.connect() as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS local_archive_files(
          owner TEXT NOT NULL,archive_id TEXT NOT NULL,source_key TEXT NOT NULL,path TEXT NOT NULL,
          root TEXT NOT NULL,sha TEXT NOT NULL,name TEXT NOT NULL,group_name TEXT NOT NULL,
          PRIMARY KEY(owner,archive_id,source_key));""")


def owner_key(config):
    return config["server"] + ":" + config["device_id"]


def recover(companion, config):
    """Re-associate files after pairing again; the server verifies account ownership first."""
    from .sync import digest

    store = companion.store
    schema(store)
    with store.connect() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM local_archive_files WHERE owner<>?", (owner_key(config),)
            )
        ]
    archives = {}
    for row in rows:
        if row["owner"].rsplit(":", 1)[0] == config["server"]:
            archives.setdefault((row["owner"], row["archive_id"]), []).append(row)
    for (old_owner, archive_id), records in archives.items():
        files = []
        for row in records:
            p = Path(row["path"])
            if (
                p.is_file()
                and not p.is_symlink()
                and p.resolve().is_relative_to(Path(row["root"]).resolve())
                and digest(p) == row["sha"]
            ):
                files.append({"source_key": row["source_key"], "sha": row["sha"]})
        if not files:
            continue
        try:
            companion.request(
                config,
                "/v07/device/recover",
                {"archive_id": archive_id, "files": files[:500]},
            )
        except ValueError:
            continue  # A different signed-in account must never receive this old index.
        with store.connect() as db:
            db.execute(
                "UPDATE OR IGNORE local_archive_files SET owner=? WHERE owner=? AND archive_id=?",
                (owner_key(config), old_owner, archive_id),
            )


def publish(companion, config, term):
    if not term:
        return
    store = companion.store
    schema(store)
    recover(companion, config)
    for course in store.courses():
        if course["membership"] != "added":
            continue
        with store.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT m.*,g.title AS group_name FROM materials m JOIN teaching_groups g ON g.id=m.group_id WHERE m.course_id=? AND (m.selected=1 OR ?=1) AND m.path IS NOT NULL AND m.digest IS NOT NULL",
                    (course["id"], course["sync_mode"] == "all"),
                )
            ]
        files, local = [], []
        for row in rows:
            root = Path(course["folder"]).resolve() if course["folder"] else None
            p = Path(row["path"])
            if not root or p.is_symlink() or not p.resolve().is_relative_to(root):
                continue
            key = row["source"] + "|" + row["source_group"]
            available = p.is_file()
            files.append(
                {
                    "source_key": key,
                    "name": row["name"],
                    "group_name": row["group_name"],
                    "sha": row["digest"],
                    "bytes": p.stat().st_size if available else 0,
                    "available": available,
                }
            )
            local.append(
                (
                    key,
                    str(p.resolve()),
                    str(root),
                    row["digest"],
                    row["name"],
                    row["group_name"],
                )
            )
        notes = companion.service.pending_content.get(course["id"], [])
        fingerprint = hashlib.sha256(
            json.dumps(files, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        cache_key = (
            "archive-index:"
            + hashlib.sha256(
                (
                    owner_key(config) + ":" + term["id"] + ":" + str(course["id"])
                ).encode()
            ).hexdigest()
        )
        if not notes and store.setting(cache_key) == fingerprint:
            continue
        total = max(1, (len(files) + 199) // 200, (len(notes) + 19) // 20)
        for offset in range(total):
            reply = companion.request(
                config,
                "/v07/device/index",
                {
                    "term_id": term["id"],
                    "course_id": course["id"],
                    "files": files[offset * 200 : (offset + 1) * 200],
                    "notes": notes[offset * 20 : (offset + 1) * 20],
                },
            )
            with store.connect() as db:
                db.executemany(
                    "INSERT OR REPLACE INTO local_archive_files VALUES (?,?,?,?,?,?,?,?)",
                    [
                        (owner_key(config), reply["archive_id"], *row)
                        for row in local[offset * 200 : (offset + 1) * 200]
                    ],
                )
        # A semester directory holds only file references, never a duplicate file or course text cache.
        from .sync import safe_name

        folder = (
            store.directory
            / "semester-archives"
            / (safe_name(term["label"]) + "-" + term["id"][:8])
            / (safe_name(course["name"]) + "-" + reply["archive_id"][:8])
        )
        folder.mkdir(parents=True, exist_ok=True)
        manifest = folder / "files.json"
        temporary = folder / "files.json.part"
        temporary.write_text(
            json.dumps(
                {
                    "archive_id": reply["archive_id"],
                    "storage": "local",
                    "files": [
                        {"name": r[4], "group": r[5], "path": r[1], "sha256": r[3]}
                        for r in local
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "utf-8",
        )
        os.replace(temporary, manifest)
        store.set(cache_key, fingerprint)
        companion.service.pending_content.pop(course["id"], None)


def export(companion, config, archive_id):
    schema(companion.store)
    data = companion.request(config, "/v07/device/export", {"archive_id": archive_id})
    with companion.store.connect() as db:
        records = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM local_archive_files WHERE owner=? AND archive_id=?",
                (owner_key(config), archive_id),
            )
        ]
    destination = companion.store.directory / "exports"
    destination.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".part", dir=destination)
    os.close(fd)
    target = destination / (archive_id + "-" + str(time.time_ns()) + ".zip")
    recorded = {r["source_key"] for r in records}
    missing = [
        f["name"] for f in data.get("files", []) if f["source_key"] not in recorded
    ]
    manifest = []
    from .sync import digest, safe_name

    try:
        with zipfile.ZipFile(
            temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        ) as z:
            for i, row in enumerate(records):
                checkpoint()
                p, root = Path(row["path"]), Path(row["root"])
                if (
                    not p.is_file()
                    or p.is_symlink()
                    or not p.resolve().is_relative_to(root.resolve())
                    or digest(p) != row["sha"]
                ):
                    missing.append(row["name"])
                    continue
                name = f"{safe_name(data.get('semester', '学期'))}/{safe_name(data['course_name'])}/{safe_name(row['group_name'])}/{i+1}-{safe_name(row['name'])}"
                hasher = hashlib.sha256()
                with p.open("rb") as src, z.open(name, "w") as dst:
                    while chunk := src.read(1024 * 1024):
                        checkpoint()
                        hasher.update(chunk)
                        dst.write(chunk)
                if hasher.hexdigest() != row["sha"]:
                    raise ValueError("导出期间文件发生变化，请重试")
                manifest.append({"name": name, "sha256": row["sha"]})
            esc = html.escape
            sections = [
                '<!doctype html><meta charset="utf-8"><title>CourseNest</title><h1>'
                + esc(data["course_name"])
                + "</h1>"
            ]
            for n in data["notes"]:
                sections.append(
                    "<p>"
                    + esc(n.get("section", ""))
                    + "</p><h2>"
                    + esc(n["title"])
                    + "</h2><p>"
                    + esc(n["category"])
                    + "</p><pre>"
                    + esc(n["body"])
                    + "</pre><p>"
                    + esc(n["url"])
                    + "</p>"
                )
            sections.append("<h2>历史版本 / History</h2>")
            for n in data["history"]:
                sections.append(
                    "<h3>" + esc(n["title"]) + "</h3><pre>" + esc(n["body"]) + "</pre>"
                )
            z.writestr("课程说明.html", "".join(sections))
            z.writestr(
                "manifest.json",
                json.dumps(
                    {"archive_id": archive_id, "files": manifest, "missing": missing},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "status": "partial" if missing else "success",
        "message": f"已导出到电脑：{target}；缺失文件 {len(missing)} 个",
        "downloaded": len(manifest),
        "failed": len(missing),
    }
