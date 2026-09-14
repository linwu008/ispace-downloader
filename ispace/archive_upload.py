"""Uploads only catalogued files inside a locally authorized course folder."""
import hashlib
from pathlib import Path
import httpx
from .extraction import extract


def sync_archives(companion, config):
    with httpx.Client(base_url=config['server'], headers={'Authorization': 'Bearer '+config['token']}, trust_env=False, timeout=120, transport=companion.transport) as client:
        response=client.get('/api/device/archives')
        if response.status_code in {403,404,503}:
            return
        response.raise_for_status()
        for archive in response.json():
            selected=archive.get('selected_ids', [])
            if not archive['automatic'] and not selected:
                continue
            course=next((x for x in companion.store.courses() if x['id']==archive['course_id']),None)
            if not course or not course['folder']:
                continue
            root=Path(course['folder']).resolve()
            with companion.store.connect() as db:
                files=[dict(x) for x in db.execute('SELECT m.*,g.title group_name FROM materials m JOIN teaching_groups g ON g.id=m.group_id WHERE m.course_id=? AND m.path IS NOT NULL',(course['id'],))]
            for f in files:
                if not archive['automatic'] and f['id'] not in selected:
                    continue
                path=Path(f['path']).resolve()
                if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>50000000:
                    continue
                data=path.read_bytes()
                if not data:
                    continue
                digest=hashlib.sha256(data).hexdigest()
                source=hashlib.sha256((f['source']+'\n'+f['source_group']).encode()).hexdigest()
                response=client.post('/api/device/archive/prepare',json={'archive_id':archive['id'],'source_key':source,'name':f['name'],'group_name':f['group_name'],'bytes':len(data),'sha':digest})
                if response.status_code==409:
                    companion.store.set('archive_status', {'message':response.json().get('detail','上传暂未完成')})
                    return
                response.raise_for_status()
                result=response.json()
                if result.get('unchanged') or result.get('excluded'):
                    continue
                upload=result['upload_id']
                client.put('/api/device/archive/upload/'+upload,content=data).raise_for_status()
                client.post('/api/device/archive/commit',json={'upload_id':upload,'parts':extract(path,data)}).raise_for_status()
                companion.store.set('archive_status', {'message':'已存档到网站：'+f['name']})
                return  # Bound each poll: keep heartbeats and remote commands responsive.
