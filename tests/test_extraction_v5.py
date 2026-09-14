from zipfile import ZipFile
from ispace.extraction import extract, MAX_TEXT


def test_text_is_bounded_and_unsupported_files_remain_unparsed(tmp_path):
    file=tmp_path/'lesson.txt';file.write_text('abc '*(MAX_TEXT//2),encoding='utf-8')
    assert len(extract(file)[0]['text'])<=MAX_TEXT
    executable=tmp_path/'lesson.exe';executable.write_bytes(b'not executed')
    assert extract(executable)==[]


def test_office_extracts_text_and_source_locators(tmp_path):
    file=tmp_path/'slides.pptx'
    with ZipFile(file,'w') as archive:
        archive.writestr('ppt/slides/slide2.xml','<a xmlns="urn:t"><t>Second</t></a>')
        archive.writestr('ppt/slides/slide1.xml','<a xmlns="urn:t"><t>First</t></a>')
    parts=extract(file)
    assert [p['text'] for p in parts]==['First','Second']
    assert parts[0]['locator']=='幻灯片 1'


def test_corrupt_document_does_not_prevent_archiving(tmp_path):
    file=tmp_path/'broken.pdf';file.write_bytes(b'bad pdf')
    assert extract(file)==[]


def test_upload_reads_only_catalogue_files_under_authorized_root(tmp_path):
    import json
    import httpx
    from types import SimpleNamespace
    from ispace.state import Store
    from ispace.archive_upload import sync_archives
    store=Store(tmp_path/'state')
    store.refresh_courses([{'id':1,'name':'Course'}])
    folder=tmp_path/'course';folder.mkdir();store.bind(1,str(folder),True)
    inside=folder/'a.txt';inside.write_text('knowledge',encoding='utf-8')
    outside=tmp_path/'private.txt';outside.write_text('private',encoding='utf-8')
    with store.connect() as db:
        db.execute("INSERT INTO teaching_groups(id,course_id,source_key,title,position,folder) VALUES ('g',1,'g','Week 1',1,'Week 1')")
        for n,path in enumerate([outside,inside],1):
            db.execute("INSERT INTO materials(id,course_id,source,source_group,group_id,name,url,path,status) VALUES (?,1,?,'g','g',?,'https://school.test/file',?,'downloaded')",(n,str(n),path.name,str(path)))
    sent=[]
    def handler(request):
        if request.url.path=='/api/device/archives':return httpx.Response(200,json=[{'id':'a','course_id':1,'automatic':1}])
        if request.url.path.endswith('/prepare'):
            data=json.loads(request.content);sent.append(data);return httpx.Response(200,json={'upload_id':'u'})
        if request.method=='PUT':assert request.content==b'knowledge'
        return httpx.Response(200,json={'ok':True})
    companion=SimpleNamespace(store=store,transport=httpx.MockTransport(handler))
    sync_archives(companion,{'server':'https://website.test','token':'test'})
    assert [f['name'] for f in sent]==['a.txt']
