"""Read-only browser smoke test; screenshots stay in ignored .runtime/."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent.parent
with sync_playwright() as engine:
    options = {"headless": True}
    if not Path(engine.chromium.executable_path).exists():
        options["channel"] = "msedge"
    browser = engine.chromium.launch(**options)
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8765", wait_until="networkidle")
    page.get_by_role("heading", name="把时间留给学习。").wait_for()
    page.wait_for_function("document.getElementById('notice').textContent !== '正在读取工作空间…'")
    assert page.locator("#login-form").count() == 1
    assert page.locator("#schedule-form").count() == 1
    assert not errors, errors
    page.screenshot(path=str(root / ".runtime" / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(root / ".runtime" / "mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows"
    print("Desktop and mobile UI passed; no JavaScript errors or horizontal overflow.")
    page.goto("https://ispace.bnbu.edu.cn/login/index.php", wait_until="domcontentloaded", timeout=60000)
    form = page.locator("form").filter(has=page.locator('input[type="password"]')).first
    assert form.locator('input[name="username"]').count() == 1
    assert form.locator('input[name="logintoken"]').count() == 1
    assert form.locator('button[type="submit"]').count() == 1
    print("Real public iSpace login form matched. No credentials submitted.")
    browser.close()
