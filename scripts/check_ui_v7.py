"""Browser acceptance for private notes, independent archives, confirmation and language."""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect, Error

root = Path(__file__).resolve().parent.parent
fixture = Path(tempfile.mkdtemp(prefix="coursenest-v07-"))
origin = "http://127.0.0.1:18772"
log = (fixture / "server.log").open("w")
process = subprocess.Popen(
    [shutil.which("node"), str(root / "cloud/dev.mjs")],
    env={**os.environ, "PORT": "18772", "COURSENEST_DB": str(fixture / "cloud.sqlite")},
    stdout=log,
    stderr=log,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
)
try:
    for _ in range(60):
        try:
            if httpx.get(origin + "/api/health", trust_env=False).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    with sync_playwright() as engine:
        try:
            browser = engine.chromium.launch(headless=True, channel="chromium")
        except Error:
            browser = engine.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        request = context.request

        def post(path, value, headers=None):
            response = request.post(
                origin + "/api" + path,
                data=value,
                headers={"Origin": origin, **(headers or {})},
            )
            assert response.ok, (path, response.status, response.text())
            return response.json()

        post(
            "/auth/register",
            {
                "email": "browser@example.org",
                "password": "12345678",
                "invite": "NEST-LOCAL-04",
            },
        )
        me = request.get(origin + "/api/me").json()
        csrf = {"X-CSRF-Token": me["csrf"]}
        code = post("/pairings", {}, csrf)["code"]
        device = post("/device/pair", {"code": code, "name": "Acceptance PC"})
        auth = {"Authorization": "Bearer " + device["token"]}
        snap = {
            "version": "0.7.0",
            "capabilities": [
                "confirm-v1",
                "notes-v1",
                "local-archive-v1",
                "cancel-v1",
                "schedule-v1",
            ],
            "courses": [
                {
                    "id": 1,
                    "name": "通知 — 原始课程名",
                    "membership": "added",
                    "enabled": True,
                    "bound": True,
                }
            ],
            "groups": [],
            "materials": [],
            "auth": "logged_in",
            "readiness": {
                "at": int(time.time()),
                "auth": True,
                "busy": False,
                "courses": [1],
            },
        }
        post("/device/poll", {"snapshot": snap, "paused": True}, auth)
        term_page = context.new_page()
        term_page.goto(origin + "/archives.html")
        field = term_page.locator('input[aria-label="学年与学期"]')
        expect(field).to_have_value("")
        field.fill("   ")
        with term_page.expect_response(lambda r: r.url.endswith("/api/v07/terms") and r.request.method == "POST") as saved:
            term_page.get_by_role("button", name="确认学期", exact=True).click()
        assert saved.value.ok
        term = saved.value.json()
        assert term["label"] == "2026–2027 / 第一学期"
        expect(field).to_have_value(term["label"])
        expect(term_page.locator(".term-status")).to_have_text("学期已确认")
        expect(term_page.locator(".v07-card select")).to_have_value(term["id"])
        term_page.reload()
        expect(field).to_have_value(term["label"])
        # Clearing an existing term keeps its label instead of renaming it to the default.
        field.fill("自定义学期")
        term_page.get_by_role("button", name="确认学期", exact=True).click()
        expect(term_page.locator(".term-status")).to_have_text("学期已确认")
        field.fill("")
        term_page.get_by_role("button", name="确认学期", exact=True).click()
        expect(field).to_have_value("自定义学期")
        expect(term_page.locator(".term-status")).to_have_text("学期已确认")
        # Failed requests must be visible next to the form, including on archive pages.
        term_page.route("**/api/v07/terms", lambda r: r.fulfill(status=503, json={"detail":"暂时无法保存"}) if r.request.method == "POST" else r.continue_())
        term_page.get_by_role("button", name="确认学期", exact=True).click()
        expect(term_page.locator(".term-status")).to_have_text("暂时无法保存")
        term_page.unroute("**/api/v07/terms")
        field.fill(term["label"])
        term_page.get_by_role("button", name="确认学期", exact=True).click()
        expect(term_page.locator(".term-status")).to_have_text("学期已确认")
        assert len(request.get(origin + "/api/v07/terms").json()) == 1
        term_page.close()
        archive = post(
            "/v07/device/index",
            {
                "term_id": term["id"],
                "course_id": 1,
                "files": [
                    {
                        "source_key": "file1",
                        "name": "通知.pdf",
                        "group_name": "Week 1",
                        "sha": "a" * 64,
                        "bytes": 100,
                        "available": True,
                    }
                ],
                "notes": [
                    {
                        "source_key": "note1",
                        "title": "通知原文",
                        "category": "assignment",
                        "body": "请在周五提交。Due Friday.",
                        "url": "https://school.example/mod/assign/view.php?id=1",
                        "section": "Week 1",
                        "partial": True,
                    }
                ],
            },
            auth,
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(origin + "/workspace.html")
        expect(page.locator("#workspace")).to_be_visible()
        expect(page.locator("#welcome")).to_be_hidden()
        page.locator('nav a[href="#/courses"]').click()
        page.locator("#course-grid").get_by_text(
            "通知 — 原始课程名", exact=True
        ).click()
        page.get_by_role("button", name="课程通知与要求").click()
        expect(page.locator(".course-note")).to_contain_text("Due Friday")
        page.locator(".v07-dialog details").first.locator("summary").first.click()
        expect(page.locator(".course-note")).to_be_visible()
        page.locator(".v07-dialog button").first.click()
        page.locator('[data-close="course-dialog"]').click()
        page.locator("#language-switch").click()
        expect(page.locator("#language-switch")).to_have_text("中文")
        page.locator("#course-grid").get_by_text(
            "通知 — 原始课程名", exact=True
        ).click()
        page.locator("#read-catalog").locator(
            "xpath=following-sibling::button[1]"
        ).click()
        expect(page.locator(".course-note")).to_have_text("请在周五提交。Due Friday.")
        page.goto(origin + "/archives.html")
        expect(
            page.get_by_role("heading", name="Semester archives", exact=False)
        ).to_be_visible()
        expect(page.get_by_text("通知 — 原始课程名", exact=True)).to_be_visible()
        page.screenshot(path=str(root / ".runtime/v07-archives-en.png"), full_page=True)
        page.locator("#language-switch").click()
        page.goto(origin + "/workspace.html")
        expect(page.locator("#sync-all")).to_be_enabled()
        page.route("**/api/jobs", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"同步服务暂时不可用，请稍后重试"}') if route.request.method == "POST" else route.continue_())
        page.locator("#sync-all").click()
        expect(page.locator("#notice")).to_contain_text("同步服务暂时不可用")
        expect(page.locator("#sync-all")).to_be_enabled()
        page.unroute("**/api/jobs")
        held = []
        page.route("**/api/jobs", lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
        page.locator("#sync-all").click()
        expect(page.locator("#sync-all")).to_be_disabled()
        expect(page.locator("#sync-all")).to_have_text("正在提交…")
        expect(page.locator("#notice")).to_contain_text("正在提交同步请求")
        page.wait_for_timeout(300)
        assert len(held) == 1
        held[0].continue_()
        page.unroute("**/api/jobs")
        expect(page.locator("#download-confirmation")).to_be_visible(timeout=10000)
        with page.expect_response(lambda r: r.url.endswith("/api/v07/confirm") and r.request.method == "POST") as dismissed:
            page.get_by_role("button", name="稍后处理", exact=True).click()
        assert dismissed.value.ok
        page.reload()
        expect(page.locator("#workspace")).to_be_visible()
        expect(page.locator("#pending-button")).to_be_visible()
        expect(page.locator("#download-confirmation")).not_to_be_visible()
        page.locator("#pending-button").click()
        page.get_by_role("button", name="开始执行").click()
        claimed = post("/device/poll", {}, auth)
        assert claimed["job"]["kind"] == "sync"
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(origin + "/archives.html")
        expect(page.get_by_role("heading", name="学期存档", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(
            path=str(root / ".runtime/v07-archives-mobile.png"), full_page=True
        )
        # No helper required for retained course text, even after pairing is revoked.
        response = request.delete(
            origin + "/api/device", headers={"Origin": origin, **csrf}
        )
        assert response.ok
        page.reload()
        page.get_by_role("button", name="查看内容").click()
        expect(page.locator(".course-note")).to_contain_text("Due Friday")
        assert not errors, errors
        browser.close()
    print(
        "PASS v0.7 browser: notes, private archive, confirmation, language, mobile, revoked device retention."
    )
finally:
    process.terminate()
    process.wait(timeout=10)
    log.close()
