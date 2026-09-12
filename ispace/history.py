"""Task context snapshots and verified locations of recorded file versions."""
import sqlite3
from pathlib import Path

from filelock import FileLock

from .grouped_sync import verified


def ensure_schema(store):
    with FileLock(store.directory / 'migration.lock', timeout=30):
        with store.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='event_context'").fetchone():
                return
            has_history = db.execute('SELECT 1 FROM events LIMIT 1').fetchone()
        backup = store.directory / 'index-pre-history-locations.sqlite3'
        if has_history and not backup.exists():
            with sqlite3.connect(store.path) as src, sqlite3.connect(backup) as dst:
                src.backup(dst)
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS event_context (
                event_id INTEGER PRIMARY KEY, material_id INTEGER, course_name TEXT,
                group_title TEXT, group_folder TEXT, saved_path TEXT, saved_digest TEXT)''')


def record_context(db, event_id, course_id, material_id):
    course = db.execute('SELECT name FROM courses WHERE id=?', (course_id,)).fetchone()
    item = None
    if material_id is not None:
        item = db.execute('''SELECT m.*,g.title AS group_title,g.folder AS group_folder FROM materials m
            LEFT JOIN teaching_groups g ON g.id=m.group_id WHERE m.id=? AND m.course_id=?''', (material_id, course_id)).fetchone()
        if not item:
            raise ValueError('任务资料不属于该课程')
    db.execute('INSERT INTO event_context VALUES (?,?,?,?,?,?,?)', (event_id, material_id, course['name'] if course else None,
               item['group_title'] if item else None, item['group_folder'] if item else None,
               item['path'] if item else None, item['digest'] if item else None))


def context(db, event):
    snapshot = db.execute('SELECT * FROM event_context WHERE event_id=?', (event['id'],)).fetchone()
    course = db.execute('SELECT * FROM courses WHERE id=?', (event['course_id'],)).fetchone()
    item = None
    ambiguous = False
    if snapshot:
        if snapshot['material_id']:
            item = db.execute('SELECT * FROM materials WHERE id=? AND course_id=?', (snapshot['material_id'], event['course_id'])).fetchone()
    elif course and event['name'] != course['name']:
        candidates = db.execute('SELECT * FROM materials WHERE course_id=? AND name=? LIMIT 2', (event['course_id'], event['name'])).fetchall()
        item = candidates[0] if len(candidates) == 1 else None
        ambiguous = len(candidates) > 1
    group = db.execute('SELECT * FROM teaching_groups WHERE id=?', (item['group_id'],)).fetchone() if item else None
    return snapshot, course, item, group, ambiguous


def decorate(store, events):
    with store.connect() as db:
        for event in events:
            snapshot, course, item, group, ambiguous = context(db, event)
            event['course_name'] = (snapshot['course_name'] if snapshot else None) or (course['name'] if course else '未知课程')
            event['group_title'] = (snapshot['group_title'] if snapshot else None) or (group['title'] if group else '未能确定分组' if ambiguous else '课程检查 / 未关联文件')
            event['group_folder'] = (snapshot['group_folder'] if snapshot else None) or (group['folder'] if group else None)
    return events


def location(store, event_id):
    with store.connect() as db:
        row = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if not row:
            raise ValueError('任务记录不存在，请刷新页面')
        event = dict(row)
        snapshot, course, item, group, ambiguous = context(db, event)
        candidates = []
        expected = snapshot['saved_digest'] if snapshot else None
        if expected and snapshot['saved_path']:
            candidates.append(snapshot['saved_path'])
            if item:
                candidates.extend(r[0] for r in db.execute('SELECT path FROM material_versions WHERE material_id=? AND digest=?', (item['id'], expected)))
        elif item and item['path']:
            candidates.append(item['path'])
            expected = item['digest']
    event = decorate(store, [event])[0]
    result = {'event_id': event_id, 'name': event['name'], 'course_name': event['course_name'],
              'group_title': event['group_title'], 'group_folder': event['group_folder'],
              'current_group_title': group['title'] if group else None,
              'recorded_path': snapshot['saved_path'] if snapshot else None,
              'path': None, 'folder': None, 'exists': False, 'note': ''}
    if course and course['folder']:
        root = Path(course['folder']).resolve()
        for candidate in dict.fromkeys(candidates):
            try:
                path = Path(candidate)
                if verified(path, expected, root):
                    result.update(path=str(path.resolve()), folder=str(path.resolve().parent), exists=True)
                    break
            except OSError:
                continue
    if ambiguous:
        result['note'] = '旧记录存在多个同名资料，无法确认分组与文件位置。请在资料库按课程和分组查找。'
    elif result['exists']:
        if not snapshot:
            result['note'] = '旧记录未保存文件版本，以下为唯一匹配资料的当前保存位置。'
        elif not snapshot['saved_path']:
            result['note'] = '该次任务没有保存文件，以下为这份资料后来保存的位置。'
        elif Path(result['path']) != Path(snapshot['saved_path']):
            result['note'] = '文件已整理到新目录，以下位置已按该记录的内容指纹核对。'
        else:
            result['note'] = '文件存在，位置与该记录的内容指纹一致。'
    elif candidates:
        result['note'] = '未找到该记录对应的文件版本，文件可能已移动、删除或修改。'
    else:
        result['note'] = '该任务没有可定位的已保存文件，或旧记录缺少明确的资料关联。'
    return result
