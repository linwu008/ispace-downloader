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
        term = post("/v07/terms", {"label": "2026–2027 / 第一学期"}, csrf)
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
        post(
            "/jobs",
            {"kind": "sync", "payload": {}, "request_id": "browser-sync-0000000001"},
            csrf,
        )
        page.reload()
        expect(page.locator("#download-confirmation")).to_be_visible(timeout=10000)
        page.get_by_role("button", name="稍后处理", exact=True).click()
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
