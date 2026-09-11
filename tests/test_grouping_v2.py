import httpx
import pytest

from ispace.grouping import group_for, TeachingGroup, UNKNOWN
from ispace.moodle import Moodle, soup_of


@pytest.mark.parametrize('title',['Week 1 — Introduction','第一课 基础','第二周','Topic 3: Practice'])
def test_school_section_name_is_preserved(title):
    soup=soup_of(f'<li id="section-2" data-id="25" class="course-section"><h3 class="sectionname">{title}</h3><a href="/mod/resource/view.php?id=1">File</a></li>')
    group=group_for(soup.a)
    assert group.title==title and group.key=='section:25' and group.position==2000


def test_subheading_does_not_use_publication_date():
    soup=soup_of('<li id="section-1" data-id="8"><h3 class="sectionname">Lectures</h3><div id="module-12"><strong>Week 2 — Calculus</strong></div><a href="/mod/resource/view.php?id=1">2026-09-30 File</a></li>')
    group=group_for(soup.a)
    assert group.title=='Week 2 — Calculus' and 'module-12' in group.key


def test_unstructured_and_general_resources():
    assert group_for(soup_of('<main role="main"><a>file.pdf</a></main>').a)==UNKNOWN
    soup=soup_of('<li id="section-0"><h3 class="sectionname">General</h3><a>Course Info</a></li>')
    assert group_for(soup.a).key=='public'


def test_real_lecture_folder_shape():
    soup=soup_of('<li id="section-1" data-id="78539"><h3 class="sectionname">Lectures</h3><a href="/mod/folder/view.php?id=378151"><span class="instancename">1 Data <span>Folder</span></span></a></li>')
    group=group_for(soup.a)
    assert group.title=='Lectures — 1 Data'
    assert group.key=='section:78539:folder:378151'


def test_same_folder_referenced_in_two_weeks_keeps_both(monkeypatch):
    monkeypatch.setattr('ispace.moodle.time.sleep',lambda _:None)
    def handler(request):
        if request.url.path=='/course/view.php':
            html=''.join(f'<li id="section-{i}" data-id="{20+i}"><h3 class="sectionname">Week {i}</h3><a href="/mod/folder/view.php?id=5">Materials</a></li>' for i in (1,2))
        elif request.url.path=='/mod/folder/view.php':
            html='<a href="/pluginfile.php/5/mod_folder/content/0/deep/file.pdf">File</a>'
        else: html=''
        return httpx.Response(200,text='<main role="main">'+html+'</main>')
    adapter=Moodle(base='https://school.test',transport=httpx.MockTransport(handler))
    result=adapter.discover(1)
    assert not result.errors
    assert len(result.resources)==2
    assert {r.group.title for r in result.resources}=={'Week 1','Week 2'}
    assert len({r.key for r in result.resources})==1
