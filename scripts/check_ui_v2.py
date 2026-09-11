"""Exercise v0.2 UI against an isolated database via intercepted API calls."""
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect

from ispace import catalog
from ispace.grouping import TeachingGroup
from ispace.moodle import Resource
from ispace.state import Store
from ispace.sync import digest
from ispace.web import create_app

project = Path(__file__).resolve().parent.parent
fixture = Path(tempfile.mkdtemp(prefix="ispace-v02-browser-"))
root = fixture / "course"
root.mkdir()
store = Store(fixture / "state")
store.refresh_courses([{"id": 1, "name": "测试课程 · 信息技术"}])
store.bind(1, str(root), True)
original = root / "第一课资料.pdf"
original.write_bytes(b"fixture content; not a real school document")
for number in (1, 2):
    group = TeachingGroup(f"section:{number}", f"Week {number} — 教学资料", number * 1000)
    item = catalog.register(store, 1, Resource("https://example.test/lesson.pdf", "第一课资料.pdf", group=group))
    catalog.record(store, item["id"], original, digest(original), "pending_organize")
app = create_app(store)
client = TestClient(app, base_url="http://127.0.0.1:8765")
with sync_playwright() as engine:
    browser = engine.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def intercept(route):
        request = route.request
        path = request.url.split("127.0.0.1:8765", 1)[1]
        headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}}
        response = client.request(request.method, path, headers=headers, content=request.post_data_buffer)
        route.fulfill(status=response.status_code, headers={"content-type": "application/json"}, body=response.content)

    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:8765", wait_until="networkidle")
    expect(page.locator("#material-list tr")).to_have_count(2)
    page.locator(".teaching-groups summary").click()
    page.locator("#library").scroll_into_view_if_needed()
    page.screenshot(path=str(project / ".runtime" / "v02-library.png"), full_page=True)
    page.locator("#file-search").fill("Week 2")
    expect(page.locator("#material-list tr")).to_have_count(1)
    expect(page.locator("#material-total")).to_have_text("1")
    page.locator("#file-search").fill("")
    expect(page.locator("#material-total")).to_have_text("2")
    page.locator("#preview-organization").click()
    page.locator("#organization-dialog").wait_for(state="visible")
    assert original.exists(), "Preview must not move files"
    assert page.locator("#preview-rows tr").count() == 2
    page.screenshot(path=str(project / ".runtime" / "v02-preview.png"))
    page.locator("#execute-organization").click()
    expect(page.locator("#organization-result")).to_contain_text("整理完成 2 项", timeout=30000)
    assert not original.exists()
    assert len(list(root.rglob("*.pdf"))) == 2
    page.locator(".teaching-groups summary").click() if not page.locator(".teaching-groups").evaluate("e=>e.open") else None
    page.locator(".teaching-group input").first.fill("自定义第一周")
    page.get_by_role("button", name="预览目录修改").first.click()
    page.locator("#organization-dialog").wait_for(state="visible")
    assert not (root / "自定义第一周").exists()
    page.locator("#execute-organization").click()
    expect(page.locator(".teaching-group input").first).to_have_value("自定义第一周", timeout=30000)
    expect(page.locator("#organization-result")).to_contain_text("整理完成 1 项", timeout=30000)
    assert len(list((root / "自定义第一周").glob("*.pdf"))) == 1
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#material-list tr").first.bounding_box()["height"] < 500
    page.screenshot(path=str(project / ".runtime" / "v02-mobile.png"), full_page=True)
    assert not errors, errors
    print("v0.2 browser checks passed: search, groups, preview, confirmation, directory override, mobile layout.")
    browser.close()
app.state.service.pool.shutdown(wait=True)
client.close()
