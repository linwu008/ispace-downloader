import threading
import time
from pathlib import Path
import pytest
from test_companion import setup
from ispace import catalog, folders
from ispace.cancellation import check


def downloaded(setup):
    companion,store,platform,root=setup
    companion.execute({'kind':'catalog','payload':{'course_id':1}})
    companion.execute({'kind':'selection','payload':{'course_id':1,'ids':[],'mode':'all'}})
    companion.execute({'kind':'sync','payload':{}})
    return companion,store,platform,root


def test_migrate_collision_preserves_both_and_updates_index(setup,tmp_path):
    _,store,_,root=downloaded(setup)
    target=tmp_path/'new';target.mkdir()
    originals=list(root.rglob('*.png'))+list(root.rglob('*.txt'))
    for source in originals:
        dst=target/source.relative_to(root);dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(b'other contents')
    result=folders.configure(store,1,str(target),True)
    assert result['moved']==2
    assert len(list(target.rglob('*.png')))==2
    assert not any(p.exists() for p in originals)
    assert all(Path(x['path']).is_relative_to(target) for x in catalog.material_page(store)['items'])
    assert any(p.read_bytes()==b'other contents' for p in target.rglob('*.png'))


def test_future_folder_keeps_existing_files(setup,tmp_path):
    _,store,_,root=downloaded(setup)
    target=tmp_path/'new';target.mkdir()
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert folders.configure(store,1,str(target),False)['moved']==0
    assert all(p.read_bytes()==b for p,b in before.items())
    assert not list(target.iterdir())


def test_root_applies_to_new_courses_and_rejects_protected(setup,tmp_path):
    _,store,_,_=setup
    target=tmp_path/'semester';target.mkdir()
    folders.configure_root(store,str(target))
    store.refresh_courses([{'id':1,'name':'Computing'},{'id':2,'name':'English'}])
    assert all(Path(c['folder']).is_relative_to(target) for c in store.courses())
    with pytest.raises(ValueError):folders.check_path(store,str(store.directory))
    with pytest.raises(ValueError):folders.check_path(store,'relative')


def test_copy_failure_preserves_original_and_binding(setup,tmp_path,monkeypatch):
    _,store,_,root=downloaded(setup)
    target=tmp_path/'new';target.mkdir()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(folders,'atomic_copy',fail)
    with pytest.raises(OSError):folders.configure(store,1,str(target),True)
    assert store.courses()[0]['folder']==str(root.resolve())
    assert len(list(root.rglob('*.png')))==1


def test_running_download_cancel_cleans_temp_and_can_restart(setup):
    companion,store,platform,root=setup
    companion.execute({'kind':'catalog','payload':{'course_id':1}})
    companion.execute({'kind':'selection','payload':{'course_id':1,'ids':[],'mode':'all'}})
    began=threading.Event();normal=platform.download
    def slow(*args,**kwargs):
        began.set()
        while True:check();time.sleep(.01)
    platform.download=slow
    results=[]
    thread=threading.Thread(target=lambda:results.append(companion.execute({'kind':'sync','payload':{}})))
    thread.start();assert began.wait(5)
    companion.service.cancel();thread.join(5)
    assert not thread.is_alive() and results[0]['status']=='canceled'
    assert not list(root.rglob('*.part')) and not companion.service.busy()
    platform.download=normal
    assert companion.execute({'kind':'sync','payload':{}})['status']=='success'


def test_plan_migrates_once_and_preserves_time(setup,monkeypatch):
    companion,store,_,_=setup
    store.set('schedule_enabled',True);store.set('schedule_time','19:42')
    calls=[]
    from ispace import scheduler
    def disable(store,enabled,clock):calls.append((enabled,clock));store.set('schedule_enabled',enabled)
    monkeypatch.setattr(scheduler,'configure',disable)
    companion.migrate_plan();companion.migrate_plan()
    assert calls==[(False,'19:42')]
    assert store.setting('website_plan')['pending']
    companion.execute({'kind':'schedule_resolve','payload':{'enabled':True,'time':'21:00'}})
    assert store.setting('website_plan')=={'pending':False,'enabled':True,'time':'21:00'}


def test_network_interrupt_unblocks_waiting_socket():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from ispace.moodle import Moodle
    started=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Length','100000');self.end_headers()
            self.wfile.write(b'x');self.wfile.flush();started.set();release.wait(10)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    platform=Moodle(base=f'http://127.0.0.1:{server.server_port}')
    ended=threading.Event()
    def download():
        try:
            response=platform.request('GET',platform.base+'/file')
            for chunk in response.iter_bytes():pass
        except Exception:pass
        finally:ended.set()
    thread=threading.Thread(target=download);thread.start()
    try:
        assert started.wait(3)
        time.sleep(.05)
        platform.interrupt()
        assert ended.wait(3), 'cancel must unblock the active network read'
    finally:
        release.set();server.shutdown();server.server_close();thread.join(5);platform.close()
