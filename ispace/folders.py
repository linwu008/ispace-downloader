"""Local-only folder configuration and verified, non-overwriting migration."""
import tempfile
from pathlib import Path
from .sync import digest, safe_name
from .grouped_sync import atomic_copy

def check_path(store, value):
    path = Path(value).expanduser()
    if not path.is_absolute(): raise ValueError('请输入完整的绝对路径')
    path = path.resolve()
    for protected in (store.directory.resolve(), Path(__file__).resolve().parent.parent):
        if path == protected or path in protected.parents or protected in path.parents:
            raise ValueError('不能选择程序或账号数据所在目录')
    if path == Path(path.anchor) or '.git' in path.parts: raise ValueError('请选择专门的资料文件夹')
    if not path.is_dir(): raise ValueError('目录不存在，请先创建文件夹')
    try:
        with tempfile.TemporaryFile(dir=path) as probe: probe.write(b'check')
    except OSError as exc: raise ValueError('文件夹不可写，请检查权限或磁盘连接') from exc
    return path

def configure(store, course_id, folder, migrate=False):
    target = check_path(store, folder)
    target = store.validate_folder(course_id, str(target))
    course = next((c for c in store.courses() if c['id']==course_id), None)
    if not course: raise ValueError('请先刷新课程')
    old = Path(course['folder']).resolve() if course['folder'] else None
    if old == target: return {'moved':0}
    if old and (old in target.parents or target in old.parents):
        raise ValueError('新旧课程目录不能互相包含，请选择独立文件夹')
    mappings = []
    if migrate and old:
        with store.connect() as db:
            paths = {r[0] for r in db.execute('SELECT path FROM materials WHERE course_id=? UNION SELECT path FROM resources WHERE course_id=? UNION SELECT v.path FROM material_versions v JOIN materials m ON m.id=v.material_id WHERE m.course_id=?', (course_id,course_id,course_id)) if r[0]}
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_archive_files'").fetchone():
                paths.update(r[0] for r in db.execute('SELECT path FROM local_archive_files WHERE root=?',(str(old),)))
        for original in sorted(paths):
            source = Path(original)
            if not source.is_file(): continue
            if source.is_symlink() or not source.resolve().is_relative_to(old): raise ValueError('文件不在原课程目录内，未迁移')
            destination = target/source.resolve().relative_to(old)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.parent.resolve().is_relative_to(target): raise ValueError('目标子文件夹越过授权目录')
            value = digest(source)
            base = destination
            for index in range(1000):
                if destination.exists():
                    if not destination.is_symlink() and destination.is_file() and digest(destination)==value: break
                    destination=base.with_name(f'{base.stem}_保留{index+1}{base.suffix}')
                    continue
                try:
                    atomic_copy(source, destination)
                    break
                except FileExistsError: continue
            else: raise ValueError('同名文件过多，原文件保留')
            mappings.append((original,str(destination),value))
    with store.connect() as db:
        archived = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_archive_files'").fetchone()
        for source,destination,_ in mappings:
            if archived: db.execute('UPDATE local_archive_files SET path=?,root=? WHERE path=? AND root=?',(destination,str(target),source,str(old)))
            db.execute('UPDATE materials SET path=? WHERE course_id=? AND path=?',(destination,course_id,source))
            db.execute('UPDATE resources SET path=? WHERE course_id=? AND path=?',(destination,course_id,source))
            db.execute('UPDATE OR REPLACE material_versions SET path=? WHERE material_id IN (SELECT id FROM materials WHERE course_id=?) AND path=?',(destination,course_id,source))
        db.execute("UPDATE courses SET folder=?,enabled=1,membership='added' WHERE id=?",(str(target),course_id))
    retained=0
    for source,destination,value in mappings:
        try:
            if digest(source)==value and digest(destination)==value: Path(source).unlink()
            else: retained+=1
        except OSError: retained+=1
    return {'moved':len(mappings), 'retained':retained}

def configure_root(store, folder, migrate=False):
    root=check_path(store,folder)
    values=[]
    for course in store.courses():
        destination=root/(safe_name(course['name'])+'_'+str(course['id']))
        if not destination.resolve().is_relative_to(root): raise ValueError('课程子目录无效')
        destination.mkdir(exist_ok=True)
        values.append(configure(store,course['id'],str(destination),migrate))
    store.set('folder_setup',{'mode':'root','path':str(root)})
    return {'courses':len(values),'moved':sum(v['moved'] for v in values)}
