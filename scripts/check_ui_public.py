"""Public homepage/account separation and existing-session navigation acceptance."""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parent.parent
fixture = Path(tempfile.mkdtemp(prefix="coursenest-public-"))
origin = "http://127.0.0.1:18773"
log = (fixture / "server.log").open("w")
process = subprocess.Popen(
    [shutil.which("node"), str(root / "cloud/dev.mjs")],
    env={**os.environ, "PORT": "18773", "COURSENEST_DB": str(fixture / "cloud.sqlite")},
    stdout=log, stderr=log,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
)
try:
    for _ in range(60):
        try:
            if httpx.get(origin + "/api/health", trust_env=False).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    else:
        raise RuntimeError("Local test server did not start")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge" if os.name == "nt" else "chromium", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(origin)
        expect(page.locator(".hero")).to_be_visible()
        expect(page.locator('input[type="password"]')).to_have_count(0)
        expect(page.locator(".faq-item")).to_have_count(0)
        page.goto(origin + "/#faq")
        expect(page.locator(".help-teaser")).to_be_in_viewport()
        page.locator('.help-teaser a[href="/help.html"]').click()
        expect(page).to_have_url(origin + "/help.html")
        expect(page.locator("#faq-title")).to_be_in_viewport()
        expect(page.locator(".faq-item")).to_have_count(13)
        first_faq = page.locator(".faq-item").first
        first_faq.locator("summary").focus()
        page.keyboard.press("Enter")
        expect(first_faq.locator(".faq-answer")).to_be_visible()
        expect(first_faq).to_contain_text("完整解压")
        page.keyboard.press("Enter")
        expect(first_faq.locator(".faq-answer")).to_be_hidden()
        page.locator('.site-header a[href="/"]').first.click()
        for tab in ("notes", "archive", "files"):
            page.locator(f'[data-preview="{tab}"]').click()
            expect(page.locator(f'[data-panel="{tab}"]')).to_be_visible()
        page.locator('[data-preview="files"]').press("ArrowRight")
        expect(page.locator('[data-panel="notes"]')).to_be_visible()
        page.locator("#language-switch").click()
        expect(page.locator(".hero h1")).to_contain_text("Your course materials")
        page.locator('.site-header a[href="/help.html"]').click()
        expect(page.locator("#faq-title")).to_have_text("Help centre")
        page.locator(".faq-item").first.locator("summary").click()
        expect(page.locator(".faq-answer").first).to_contain_text("fully extract")
        for question in page.locator(".faq-item summary").all():
            if not question.evaluate("e => e.parentElement.open"):
                question.click()
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("#faq").screenshot(path=str(root / f".runtime/faq-en-{width}.png"))
        page.locator("#language-switch").click()
        expect(page.locator("#faq-title")).to_have_text("帮助中心")
        page.locator("#faq").screenshot(path=str(root / ".runtime/faq-zh.png"))
        page.locator(".nav-login").click()
        expect(page.locator("#auth-form")).to_be_visible()
        expect(page.locator("#auth-form")).to_be_visible()
        page.locator('a[href="/help.html"]').click()
        expect(page).to_have_url(origin + "/help.html")
        expect(page.locator("#faq-title")).to_be_in_viewport()
        page.goto(origin + "/login.html")
        page.locator('[data-modal="guide"]').click()
        expect(page.locator("#info-dialog")).to_contain_text("Windows 10/11")
        page.keyboard.press("Escape")
        page.locator("#register-tab").click()
        expect(page.locator("#invite-field")).to_be_visible()
        page.locator("#email").fill("public@example.test")
        page.locator("#password").fill("12345678")
        page.locator("#invite").fill("NEST-LOCAL-04")
        page.locator("#auth-submit").click()
        expect(page.locator("#workspace")).to_be_visible()
        expect(page.locator("#setup-dialog")).to_be_visible()
        page.locator('[data-close="setup-dialog"]').click()
        expect(page.locator("#workspace-nav .nav-icon")).to_have_count(9)
        for name in ("courses", "devices", "history", "settings", "overview"):
            link = page.locator(f'#workspace-nav a[href="#/{name}"]')
            link.click()
            expect(link).to_have_attribute("aria-current", "page")
            expect(page.locator("#workspace-nav .active")).to_have_count(1)
            expect(page.locator(f'[data-page="{name}"]')).to_be_visible()
        page.locator('#workspace-nav a[href="#/courses"]').hover()
        page.screenshot(path=str(root / ".runtime/sidebar-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("#workspace-nav")).to_be_hidden()
        page.locator("#mobile-menu").click()
        expect(page.locator("#mobile-menu")).to_have_attribute("aria-expanded", "true")
        expect(page.locator("#workspace-nav")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(root / ".runtime/sidebar-mobile.png"))
        page.emulate_media(reduced_motion="reduce")
        assert page.locator("#workspace-nav .nav-item").first.evaluate("e => getComputedStyle(e).transitionDuration") == "0s"
        page.locator('#workspace-nav a[href="#/settings"]').click()
        expect(page.locator("#mobile-menu")).to_have_attribute("aria-expanded", "false")
        expect(page.locator("#workspace-nav")).to_be_hidden()
        expect(page.locator('[data-page="settings"]')).to_be_visible()
        page.emulate_media(reduced_motion="no-preference")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator('#sidebar a[href="/help.html"]').click()
        expect(page).to_have_url(origin + "/help.html")
        expect(page.locator("#faq-title")).to_be_in_viewport()
        # Signed-in visitors still get the product homepage, with a workspace link.
        page.goto(origin)
        expect(page.locator(".hero")).to_be_visible()
        expect(page.locator(".nav-login")).to_contain_text("进入学习空间")
        # Session checking never exposes the password form to an existing user.
        pending = []
        page.route("**/api/me", lambda route: pending.append(route), times=1)
        page.goto(origin + "/login.html?next=%23%2Fsettings")
        expect(page.locator("#auth-loading")).to_be_visible()
        expect(page.locator("#auth-form")).to_be_hidden()
        page.wait_for_timeout(200)
        assert pending
        pending.pop().continue_()
        expect(page).to_have_url(origin + "/workspace.html#/settings")
        expect(page.locator('[data-page="settings"]')).to_be_visible()
        page.goto(origin + "/#/courses")
        expect(page).to_have_url(origin + "/workspace.html#/courses")
        expect(page.locator('[data-page="courses"]')).to_be_visible()
        page.locator("#logout").click()
        expect(page.locator("#auth-form")).to_be_visible()
        # A server error is visible and can be corrected without leaving the form.
        page.locator("#email").fill("public@example.test")
        page.locator("#password").fill("wrong-password")
        page.locator("#auth-submit").click()
        expect(page.locator("#auth-note")).not_to_be_empty()
        expect(page.locator("#auth-submit")).to_be_enabled()
        page.locator("#password").fill("12345678")
        page.locator("#auth-submit").click()
        expect(page.locator("#workspace")).to_be_visible()
        expect(page.locator('[data-page="courses"]')).to_be_visible()
        guest = browser.new_context(viewport={"width": 390, "height": 844})
        gp = guest.new_page()
        gp.on("pageerror", lambda e: errors.append(str(e)))
        gp.goto(origin + "/login.html?mode=register&next=https://example.org")
        expect(gp.locator("#invite-field")).to_be_visible()
        gp.locator("#language-switch").click()
        expect(gp.locator("#auth-title")).to_contain_text("Create")
        assert gp.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Open registration hides the invite input; the server remains authoritative.
        gp.route("**/api/health", lambda r: r.fulfill(json={"registration": "open"}))
        gp.reload()
        expect(gp.locator("#auth-form")).to_be_visible()
        expect(gp.locator("#invite-field")).to_be_hidden()
        gp.locator("#guest-enter").click()
        expect(gp.locator("#demo-banner")).to_be_visible()
        expect(gp.locator("#workspace")).to_be_visible()
        assert not errors, errors
        browser.close()
    print("PASS public pages: homepage, account flows, bilingual mobile, guest entry, old links, session restoration.")
finally:
    process.terminate()
    process.wait(timeout=10)
    log.close()
