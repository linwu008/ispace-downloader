"""Course membership and persistent resource subscriptions."""
import sqlite3
from filelock import FileLock

from . import catalog
from .state import now


def migrate(store):
    with FileLock(store.directory / 'migration.lock', timeout=30):
        with store.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] >= 3:
                return
        backup = store.directory / 'index-pre-v0.3.sqlite3'
        if not backup.exists():
            with sqlite3.connect(store.path) as src, sqlite3.connect(backup) as dst:
                src.backup(dst)
        with store.connect() as db:
            db.executescript('''BEGIN IMMEDIATE;
                ALTER TABLE courses ADD COLUMN membership TEXT NOT NULL DEFAULT 'available';
                ALTER TABLE courses ADD COLUMN sync_mode TEXT NOT NULL DEFAULT 'all';
                ALTER TABLE courses ADD COLUMN catalog_at TEXT;
                ALTER TABLE courses ADD COLUMN catalog_status TEXT;
                ALTER TABLE courses ADD COLUMN catalog_error TEXT NOT NULL DEFAULT '';
                ALTER TABLE materials ADD COLUMN selected INTEGER NOT NULL DEFAULT 0;
                UPDATE courses SET membership='added' WHERE folder IS NOT NULL OR enabled=1;
                UPDATE materials SET selected=1;
                CREATE TABLE preview_cache (
                    material_id INTEGER PRIMARY KEY, filename TEXT NOT NULL, digest TEXT NOT NULL,
                    etag TEXT, created REAL NOT NULL, mime TEXT NOT NULL);
                PRAGMA user_version=3;
                COMMIT;''')


def get(store, course_id):
    course = next((c for c in store.courses() if c['id'] == course_id), None)
    if not course:
        raise ValueError('课程不存在，请刷新学校课程列表')
    return course


def membership(store, course_ids, added):
    values = [get(store, identifier) for identifier in set(course_ids)]
    with store.connect() as db:
        for course in values:
            if added:
                mode = course['sync_mode'] if course['membership'] == 'removed' else 'selected'
                if course['membership'] != 'added':
                    db.execute("UPDATE courses SET membership='added',sync_mode=?,enabled=0 WHERE id=?", (mode, course['id']))
            else:
                db.execute("UPDATE courses SET membership='removed',enabled=0 WHERE id=?", (course['id'],))


def selection(store, course_id, selected_ids, mode='selected'):
    course = get(store, course_id)
    if course['membership'] != 'added':
        raise ValueError('请先将课程加入我的课程')
    if mode not in {'all', 'selected'}:
        raise ValueError('无效的同步模式')
    with store.connect() as db:
        known = {r[0] for r in db.execute('SELECT id FROM materials WHERE course_id=?', (course_id,))}
        if not set(selected_ids) <= known:
            raise ValueError('所选文件不属于该课程，请刷新文件列表')
        db.execute('UPDATE materials SET selected=0 WHERE course_id=?', (course_id,))
        db.executemany('UPDATE materials SET selected=1 WHERE id=?', [(i,) for i in set(selected_ids)])
        db.execute('UPDATE courses SET sync_mode=? WHERE id=?', (mode, course_id))


def discover(store, platform, course_id):
    course = get(store, course_id)
    if course['membership'] != 'added':
        raise ValueError('请先添加课程')
    try:
        result = platform.discover(course_id)
        for resource in result.resources:
            catalog.register(store, course_id, resource)
        status = 'partial' if result.errors else 'success'
        message = '；'.join(result.errors) if result.errors else f'已读取 {len(result.resources)} 项资料，尚未下载的文件可勾选'
    except Exception as exc:
        from .moodle import safe_error
        with store.connect() as db:
            db.execute("UPDATE courses SET catalog_status='failed',catalog_error=? WHERE id=?", (safe_error(exc), course_id))
        raise
    with store.connect() as db:
        db.execute('UPDATE courses SET catalog_at=?,catalog_status=?,catalog_error=? WHERE id=?', (now(), status, '；'.join(result.errors), course_id))
    return {'status': status, 'message': message}


def summaries(store):
    with store.connect() as db:
        counts = {r['course_id']: dict(r) for r in db.execute('''SELECT course_id,COUNT(*) AS file_count,
            SUM(selected) AS selected_count FROM materials GROUP BY course_id''')}
    result = store.courses()
    for course in result:
        course.update({k: v for k, v in counts.get(course['id'], {'file_count': 0, 'selected_count': 0}).items() if k != 'course_id'})
        # Count all rows, not just a page, for large courses.
        with store.connect() as db:
            paths = [r[0] for r in db.execute('SELECT path FROM materials WHERE course_id=?', (course['id'],))]
        from pathlib import Path
        course['downloaded_count'] = sum(bool(p and Path(p).is_file()) for p in paths)
    return result
