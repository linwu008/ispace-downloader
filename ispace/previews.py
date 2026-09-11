"""Bounded, local-only file previews. Cache is not a downloaded material."""
import os
import time
import uuid
from pathlib import Path

from . import catalog
from .grouped_sync import verified
from .moodle import Resource, ResourceError

MAX_BYTES = 50 * 1024 * 1024
TEXT_BYTES = 1024 * 1024
TTL = 24 * 60 * 60
TEXT_EXTENSIONS = {'.txt', '.md', '.csv', '.json', '.py', '.log', '.xml', '.yaml', '.yml'}


def kind(name):
    ext = Path(name).suffix.lower()
    if ext == '.pdf':
        return 'application/pdf'
    return {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}.get(ext) or ('text/plain' if ext in TEXT_EXTENSIONS else None)


def validate(path, name):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('文件超过 50 MB，请下载后在本机打开')
    mime = kind(name)
    if not mime:
        raise ValueError('此格式暂不支持预览，请下载后在本机打开')
    with path.open('rb') as stream:
        prefix = stream.read(1024)
    valid = {'application/pdf': prefix.startswith(b'%PDF-'), 'image/png': prefix.startswith(b'\x89PNG\r\n\x1a\n'),
             'image/jpeg': prefix.startswith(b'\xff\xd8\xff'), 'image/gif': prefix.startswith((b'GIF87a', b'GIF89a')),
             'image/webp': prefix.startswith(b'RIFF') and prefix[8:12] == b'WEBP', 'text/plain': b'\0' not in prefix}
    if not valid[mime]:
        raise ValueError('内容与文件格式不符，无法预览，请重新检查')
    return mime


def directory(store):
    folder = store.directory / 'preview-cache'
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        raise ValueError('预览缓存目录不能使用链接')
    folder.mkdir(exist_ok=True)
    return folder


def cache_path(store, filename):
    if Path(filename).name != filename or not filename.endswith('.cache'):
        raise ValueError('无效的预览缓存')
    path = directory(store) / filename
    if path.is_symlink() or not path.resolve().is_relative_to(directory(store).resolve()):
        raise ValueError('预览缓存路径无效')
    return path


def cached(store, material_id):
    with store.connect() as db:
        row = db.execute('SELECT * FROM preview_cache WHERE material_id=?', (material_id,)).fetchone()
    if not row or time.time() - row['created'] >= TTL:
        return None
    path = cache_path(store, row['filename'])
    if not verified(path, row['digest'], directory(store)):
        return None
    return {**dict(row), 'path': path}


def cleanup(store):
    root = directory(store)
    with store.connect() as db:
        db.execute('DELETE FROM preview_cache WHERE created<?', (time.time() - TTL,))
        used = {row[0] for row in db.execute('SELECT filename FROM preview_cache')}
    for path in root.iterdir():
        if path.is_file() and not path.is_symlink() and path.name not in used and path.suffix in {'.cache', '.part'}:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # A viewer may still be reading it; retry at the next cleanup.


def available(store, item):
    from .courses import get
    course = get(store, item['course_id'])
    if item['path'] and course['folder'] and verified(item['path'], item['digest'], Path(course['folder'])):
        path = Path(item['path'])
        return {'path': path, 'mime': validate(path, item['name']), 'temporary': False}
    cache = cached(store, item['id'])
    if cache:
        return {'path': cache['path'], 'mime': validate(cache['path'], item['name']), 'temporary': True}
    return None


def prepare(store, item_id, platform=None):
    from .sync import digest, strong_etag
    item = catalog.material(store, item_id)
    if not kind(item['name']):
        raise ValueError('此格式暂不支持预览，请下载后在本机打开')
    ready = available(store, item)
    if not ready:
        if platform is None:
            return None
        cleanup(store)
        resource = Resource(item['url'], item['name'], item['source'])
        if not platform.allowed(resource.url):
            raise ValueError('资源地址不属于学校平台')
        root = directory(store)
        path = root / (uuid.uuid4().hex + '.part')
        try:
            headers = platform.download(resource, path, max_bytes=MAX_BYTES)
            mime = validate(path, item['name'])
            value = digest(path)
            target = path.with_suffix('.cache')
            os.replace(path, target)
            with store.connect() as db:
                db.execute('INSERT OR REPLACE INTO preview_cache VALUES (?,?,?,?,?,?)', (item_id, target.name, value, strong_etag(headers), time.time(), mime))
            ready = {'path': target, 'mime': mime, 'temporary': True}
        finally:
            path.unlink(missing_ok=True)
    return {'status': 'success', 'message': '临时预览已准备，未保存到课程目录' if ready['temporary'] else '正在预览本地文件',
            'preview': {'item_id': item_id, 'mime': ready['mime'], 'temporary': ready['temporary'], 'url': f'/api/materials/{item_id}/preview/content', 'truncated': ready['mime'] == 'text/plain' and ready['path'].stat().st_size > TEXT_BYTES}}
