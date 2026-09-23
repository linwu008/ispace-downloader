import json

import httpx
import pytest

from ispace.moodle import Moodle, Resource, ResourceError
from ispace.security import LoginRequired


def page(body):
    return '<html><main role="main">' + body + '</main></html>'


def platform(handler):
    return Moodle(base="https://school.test", transport=httpx.MockTransport(handler))


def test_all_resource_types_nested_folder_and_forum_pagination(monkeypatch):
    monkeypatch.setattr("ispace.moodle.time.sleep", lambda _: None)
    routes = {
        "/course/info.php?id=1": '<ul class="teachers"><li><a href="/user/view.php?id=7">Teacher</a></li></ul>',
        "/course/view.php?id=1": '<a href="/mod/resource/view.php?id=10">File</a><a href="/mod/folder/view.php?id=20">Folder</a><a href="/mod/forum/view.php?id=30">Announcements</a><a href="/mod/assign/view.php?id=40">Assignment</a>',
        "/mod/resource/view.php?id=10": '<object data="/pluginfile.php/10/mod_resource/content/1/lecture.pdf"></object>',
        "/mod/folder/view.php?id=20": '<div class="foldertree"><a href="/pluginfile.php/20/mod_folder/content/0/nested/slides.pptx">Nested file</a></div>',
        "/mod/forum/view.php?id=30": '<a href="/mod/forum/discuss.php?d=300">Discussion</a><a href="/mod/forum/view.php?id=30&page=1">Next</a>',
        "/mod/forum/view.php?id=30&page=1": '<a href="/mod/forum/discuss.php?d=301">Second page</a>',
        "/mod/forum/discuss.php?d=300": '<article data-region="post"><div class="author"><a href="/user/view.php?id=7">Teacher</a></div><a href="/pluginfile.php/30/mod_forum/attachment/300/notice.zip">Notice</a></article>',
        "/mod/forum/discuss.php?d=301": '<article data-region="post"><div class="author"><a href="/user/view.php?id=7">Teacher</a></div><a href="/pluginfile.php/30/mod_forum/attachment/301/second.pdf">More</a></article><article data-region="post"><div class="author"><a href="/user/view.php?id=8">Student</a></div><a href="/pluginfile.php/30/mod_forum/attachment/302/student.pdf">Student work</a></article>',
        "/mod/assign/view.php?id=40": '<a href="/pluginfile.php/40/mod_assign/introattachment/0/task.docx">Instructions</a><a href="/pluginfile.php/40/assignsubmission_file/submission_files/0/private.docx">Submission</a><a href="/mod/assign/view.php?id=40&action=editsubmission">Do not follow</a>',
    }
    visited = []
    def handler(request):
        key = request.url.raw_path.decode()
        visited.append(key)
        return httpx.Response(200 if key in routes else 404, text=page(routes.get(key, "missing")))
    adapter = platform(handler)
    discovery = adapter.discover(1)
    assert not discovery.errors
    assert {r.name for r in discovery.resources} == {"lecture.pdf", "slides.pptx", "notice.zip", "second.pdf", "task.docx"}
    assert not any("action=" in key for key in visited)


def test_course_api_pagination():
    offsets = []
    def handler(request):
        if request.url.path == "/my/":
            return httpx.Response(200, text='<script>M.cfg={"userid":2,"sesskey":"test"}</script>')
        args = json.loads(request.content)[0]["args"]
        offsets.append(args["offset"])
        batch = [{"id": args["offset"] + 1, "fullname": "Course"}] if args["offset"] < 2 else []
        return httpx.Response(200, json=[{"data": {"courses": batch, "nextoffset": args["offset"] + 1}}])
    assert len(platform(handler).courses()) == 2
    assert offsets == [0, 1, 2]


def test_unknown_forum_author_reports_incomplete(monkeypatch):
    monkeypatch.setattr("ispace.moodle.time.sleep", lambda _: None)
    def handler(request):
        if request.url.path == "/course/view.php":
            body = '<a href="/mod/forum/discuss.php?d=1">Post</a>'
        elif request.url.path == "/mod/forum/discuss.php":
            body = '<a href="/pluginfile.php/1/mod_forum/attachment/1/a.pdf">Attachment</a>'
        else:
            body = ""
        return httpx.Response(200, text=page(body))
    result = platform(handler).discover(1)
    assert result.errors and not result.resources


def test_direct_resource_redirect(monkeypatch):
    monkeypatch.setattr("ispace.moodle.time.sleep", lambda _: None)
    def handler(request):
        if request.url.path == "/course/view.php":
            return httpx.Response(200, text=page('<a href="/mod/resource/view.php?id=1">File</a>'))
        if request.url.path == "/mod/resource/view.php":
            return httpx.Response(303, headers={"location": "/pluginfile.php/1/mod_resource/content/1/a.pdf"})
        if request.url.path.startswith("/pluginfile"):
            return httpx.Response(200, content=b"pdf bytes")
        return httpx.Response(200, text=page(""))
    result = platform(handler).discover(1)
    assert [r.name for r in result.resources] == ["a.pdf"]


@pytest.mark.parametrize("status,headers,content,error", [
    (302,{"location":"/login/index.php"},b"",LoginRequired),
    (200,{"content-type":"text/html"},b'<form id="login"><input type="password"></form>',LoginRequired),
    (200,{"content-type":"text/html"},b'<html>server error</html>',ResourceError),
    (302,{"location":"https://elsewhere.test/a.pdf"},b"",ResourceError),
    (200,{"content-length":"100"},b'short',ResourceError),
])
def test_invalid_attachment_never_accepted(tmp_path, status, headers, content, error):
    adapter = platform(lambda _: httpx.Response(status, headers=headers, content=content))
    with pytest.raises(error):
        adapter.download(Resource("https://school.test/pluginfile.php/1/mod_resource/content/1/a.pdf", "a.pdf"), tmp_path / "a.part")


def test_head_not_supported_falls_back_to_hash():
    assert platform(lambda _: httpx.Response(405)).metadata(Resource("https://school.test/a.pdf", "a.pdf")) == {}



def test_attendance_calendar_is_leaf_and_files_still_discovered(monkeypatch):
    monkeypatch.setattr("ispace.moodle.time.sleep", lambda _: None)
    visited = []
    def handler(request):
        visited.append(request.url.raw_path.decode())
        if request.url.path == "/course/view.php":
            body = ''.join(f'<a href="/mod/attendance/view.php?id=9&{q}">Attendance</a>'
                           for q in ['curdate=100', 'view=3', 'mode=1', 'studentid=55'])
            body += '<a href="/mod/folder/view.php?id=5">Files</a>'
        elif request.url.path == "/mod/attendance/view.php":
            assert dict(request.url.params) == {'id': '9'}
            body = ('<table class="attwidth"><tr><td class="datecol">Monday</td>'
                    '<td class="statuscol">Present</td></tr></table>'
                    '<a href="?id=9&curdate=200">Next</a><a href="?id=9&curdate=0">Previous</a>'
                    '<a href="?id=9&view=3">All</a><a href="?id=9&studentid=55">Other</a>')
        elif request.url.path == "/mod/folder/view.php":
            body = '<a href="/pluginfile.php/5/mod_folder/content/0/slides.pdf">Slides</a>'
        else:
            body = ''
        return httpx.Response(200, text=page(body))
    result = platform(handler).discover(1)
    assert not result.errors
    assert [r.name for r in result.resources] == ['slides.pdf']
    assert len([u for u in visited if '/attendance/' in u]) == 1
    assert 'Present' in next(n for n in result.notes if n['category'] == 'attendance')['body']
    assert len(visited) == 4


@pytest.mark.parametrize('limit', ['pages', 'time'])
def test_discovery_budget_keeps_found_files_reports_partial(monkeypatch, limit):
    monkeypatch.setattr('ispace.moodle.time.sleep', lambda _: None)
    visited, clock = [], [0]
    monkeypatch.setattr('ispace.moodle.time.monotonic', lambda: clock[0])
    if limit == 'pages':
        monkeypatch.setattr('ispace.moodle.DISCOVERY_PAGE_LIMIT', 2)
    def handler(request):
        visited.append(str(request.url))
        body = '<a href="/pluginfile.php/5/mod_folder/content/0/slides.pdf">Slides</a>'
        if request.url.path == '/course/view.php':
            body += '<a href="/mod/forum/view.php?id=1&page=0">Forum</a>' * 10
        if request.url.path == '/mod/forum/view.php':
            n = int(request.url.params['page'])
            body = f'<a href="?id=1&page={n+1}">Next</a>'
            if limit == 'time': clock[0] = 121
        return httpx.Response(200, text=page(body))
    result = platform(handler).discover(1)
    assert result.errors and '上限' in result.errors[0]
    assert result.resources
    assert len(visited) == 3
