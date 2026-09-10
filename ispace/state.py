from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_dir() -> Path:
    path = Path(os.environ.get("ISPACE_DATA_DIR", str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "iSpaceDownloader")))
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "index.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, folder TEXT, enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS resources (
                    course_id INTEGER NOT NULL, source TEXT NOT NULL, path TEXT NOT NULL,
                    digest TEXT NOT NULL, etag TEXT, modified TEXT, size INTEGER,
                    PRIMARY KEY(course_id, source));
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,
                    downloaded INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
                    message TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, course_id INTEGER,
                    name TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, created TEXT NOT NULL);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def courses(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM courses ORDER BY name")]

    def refresh_courses(self, courses):
        with self.connect() as db:
            for course in courses:
                db.execute("INSERT INTO courses(id,name) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (course["id"], course["name"]))

    def bind(self, course_id: int, folder: str, enabled: bool):
        path = Path(folder).expanduser()
        if not path.is_absolute():
            raise ValueError("请选择绝对路径，例如 D:\\学习资料\\数学")
        path = path.resolve()
        project = Path(__file__).resolve().parent.parent
        for protected in (self.directory, project):
            if path == protected or path in protected.parents or protected in path.parents:
                raise ValueError("课程目录不能包含程序、状态目录或位于这些目录内")
        if path == Path(path.anchor) or any(part.lower() == ".git" for part in path.parts):
            raise ValueError("请选择专门的课程文件夹")
        for course in self.courses():
            if course["id"] != course_id and course["folder"]:
                other = Path(course["folder"]).resolve()
                if path == other or path in other.parents or other in path.parents:
                    raise ValueError("不同课程的目录不能相同或相互包含")
        if not path.is_dir():
            raise ValueError("目录不存在，请先在资源管理器中创建")
        with self.connect() as db:
            result = db.execute("UPDATE courses SET folder=?,enabled=? WHERE id=?", (str(path), int(enabled), course_id))
            if not result.rowcount:
                raise ValueError("请先刷新课程列表")

    def resource(self, course_id, source):
        with self.connect() as db:
            row = db.execute("SELECT * FROM resources WHERE course_id=? AND source=?", (course_id, source)).fetchone()
            return dict(row) if row else None

    def remember(self, course_id, source, path, digest, etag, modified, size):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?)", (course_id, source, str(path), digest, etag, modified, size))

    def start_run(self):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted',finished=?,message='上次任务异常中断，请重试' WHERE status='running'", (now(),))
            return db.execute("INSERT INTO runs(started,status) VALUES (?,'running')", (now(),)).lastrowid

    def event(self, run_id, course_id, name, status, message):
        with self.connect() as db:
            db.execute("INSERT INTO events(run_id,course_id,name,status,message,created) VALUES (?,?,?,?,?,?)", (run_id, course_id, name, status, message, now()))

    def finish(self, run_id, status, downloaded, skipped, failed, message=""):
        with self.connect() as db:
            db.execute("UPDATE runs SET finished=?,status=?,downloaded=?,skipped=?,failed=?,message=? WHERE id=?", (now(), status, downloaded, skipped, failed, message, run_id))

    def history(self):
        with self.connect() as db:
            return {
                "runs": [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30")],
                "events": [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 200")],
            }
