"""v1 release acceptance: stale data, feedback, search, mobile, archive state."""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect, Error

root = Path(__file__).resolve().parent.parent
fixture = Path(tempfile.mkdtemp(prefix="coursenest-v1-"))
origin = "http://127.0.0.1:18774"
log = (fixture / "server.log").open("w")
process = subprocess.Popen(
    [shutil.which("node"), str(root / "cloud/dev.mjs")],
    env={**os.environ, "PORT": "18774", "COURSENEST_DB": str(fixture / "cloud.sqlite")},
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
        term=post("/v07/terms",{"label":"Acceptance semester"},csrf)
        snap["paused"]=True
        snap["groups"]=[{"id":"g1","course_id":1,"title":"Week 1"}]
        snap["materials"]=[{"id":1,"course_id":1,"group_id":"g1","name":"Saved.pdf","status":"downloaded"},{"id":2,"course_id":1,"group_id":"g1","name":"Pending.pdf","status":"pending"}]
        post("/device/poll",{"snapshot":snap,"paused":True},auth)
        post("/v07/device/index",{"term_id":term["id"],"course_id":1,"files":[{"source_key":"f1","name":"Saved.pdf","group_name":"Week 1","sha":"a"*64,"bytes":10,"available":False}],"notes":[{"source_key":"n1","title":"Project deadline","category":"assignment","body":"Due Friday, check school page.","url":"https://school.example/task","section":"Week 1"}]},auth)
        page=context.new_page()
        errors=[]
        page.on("pageerror",lambda e: errors.append(str(e)))
        page.goto(origin+"/workspace.html")
        expect(page.locator("#study-digest")).to_contain_text("Project deadline")
        expect(page.locator("#onboarding")).to_contain_text("使用检查清单")
        page.locator('nav a[href="#/courses"]').click()
        page.locator("#global-search").fill("Pending")
        expect(page.locator("#global-results")).to_contain_text("Pending.pdf")
        expect(page.locator("#global-results")).not_to_contain_text("Saved.pdf")
        page.locator("#global-search").fill("")
        page.locator("#only-unsaved").check()
        expect(page.locator("#global-results")).to_contain_text("Pending.pdf")
        expect(page.locator("#global-results")).not_to_contain_text("Saved.pdf")
        page.locator("#only-unsaved").uncheck()
        expect(page.locator("#course-grid")).to_be_visible()
        # API failure retains data and explicitly marks connectivity as unknown.
        page.route("**/api/me",lambda route:route.abort("failed"))
        page.get_by_role("button",name="刷新连接状态",exact=True).click()
        expect(page.locator("#connection")).to_have_text("连接状态待刷新")
        expect(page.locator("#connection-detail")).to_contain_text("上次成功")
        expect(page.locator("#course-grid")).to_contain_text("原始课程名")
        page.unroute("**/api/me")
        page.locator("#connection-detail").get_by_role("button",name="重试",exact=True).click()
        expect(page.locator("#connection")).not_to_have_text("连接状态待刷新")
        # A server-accepted response lost in transit must reuse the same id on retry.
        sent=[]
        def lose_response(route):
            if route.request.method=="POST":
                sent.append(route.request.post_data_json)
                route.fetch()
                route.abort("failed")
            else: route.continue_()
        page.route("**/api/jobs",lose_response)
        page.locator("#sync-all").click()
        expect(page.locator("#sync-all")).to_be_enabled()
        expect(page.locator("#notice")).to_contain_text("连接超时")
        page.unroute("**/api/jobs")
        # The task is not ready while paused, so no auto-dialog interrupts the retry.
        with page.expect_request(lambda r:r.url.endswith("/api/jobs") and r.method=="POST") as second:
            page.locator("#sync-all").click()
        assert second.value.post_data_json["request_id"]==sent[0]["request_id"]
        assert len(request.get(origin+"/api/jobs").json()["items"])==1
        expect(page.locator("#sync-all")).to_be_enabled()
        page.locator('nav a[href="#/history"]').click()
        expect(page.locator("#job-list")).to_contain_text("等待用户确认")
        page.locator("#job-list").get_by_role("button",name="取消任务").click()
        expect(page.locator("#job-list")).to_contain_text("已取消")
        # Overview remains usable on a narrow screen and source text is retained.
        page.set_viewport_size({"width":390,"height":844})
        page.goto(origin+"/workspace.html")
        expect(page.locator("#study-digest")).to_contain_text("Project deadline")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(root/".runtime/v1-overview-mobile.png"),full_page=True)
        page.locator("#language-switch").click()
        expect(page.locator("#study-digest")).to_contain_text("Course requirements and recent updates")
        expect(page.locator("#study-digest")).to_contain_text("Project deadline")
        page.locator("#language-switch").click()
        page.goto(origin+"/archives.html")
        expect(page.get_by_text("上次检查存在 0 份 · 本地缺失 1 份",exact=True)).to_be_visible()
        expect(page.get_by_text("最近成功导出：",exact=False)).to_contain_text("尚无成功记录")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors,errors
        browser.close()
    print("PASS v1 browser: stale state/recovery, course search, lost-response idempotency, cancellation, mobile, bilingual content, archive honesty.")
finally:
    process.terminate()
    process.wait(timeout=10)
    log.close()
