from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit, unquote

import httpx
from bs4 import BeautifulSoup

from .security import LoginRequired
from .grouping import TeachingGroup, UNKNOWN, group_for

BASE = "https://ispace.bnbu.edu.cn"


class ResourceError(Exception):
    pass


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def logged_in(html):
    return bool(re.search(r'["\']?userid["\']?\s*:\s*[1-9]\d*', html) or soup_of(html).select_one('a[href*="/login/logout.php"]'))


def login_page(html):
    return bool(soup_of(html).select_one('input[type="password"], form#login'))


def canonical(url):
    bits = urlsplit(url)
    query = [(k, v) for k, values in parse_qs(bits.query).items() for v in values if k not in {"forcedownload", "sesskey", "token"}]
    return urlunsplit((bits.scheme, bits.netloc, bits.path, urlencode(sorted(query)), ""))


def file_url(url):
    path = unquote(urlsplit(url).path)
    return "/pluginfile.php/" in path and not any(x in path for x in ("/user/", "/theme_", "/assignsubmission_", "/assignfeedback_"))


@dataclass(frozen=True)
class Resource:
    url: str
    name: str
    source: str = ""
    group: TeachingGroup = UNKNOWN
    source_page: str = ""

    @property
    def key(self):
        return self.source or canonical(self.url)


@dataclass
class Discovery:
    resources: list[Resource] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


def browser_login(vault, username=None, password=None, manual=False):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout, Error as BrowserError
    with sync_playwright() as engine:
        launch_options = {"headless": not manual}
        if not Path(engine.chromium.executable_path).exists():
            launch_options["channel"] = "msedge"
        try:
            browser = engine.chromium.launch(**launch_options)
        except BrowserError:
            if launch_options.get("channel") == "msedge":
                raise
            browser = engine.chromium.launch(**{**launch_options, "channel": "msedge"})
        try:
            context = browser.new_context(storage_state=vault.load_session() or None)
            page = context.new_page()
            page.goto(BASE + "/my/", wait_until="domcontentloaded", timeout=45000)
            if not logged_in(page.content()):
                page.goto(BASE + "/login/index.php", wait_until="domcontentloaded", timeout=45000)
                if urlsplit(page.url).netloc != urlsplit(BASE).netloc:
                    raise LoginRequired("登录跳转到了其他域名，需要适配学校统一身份认证入口")
                if manual:
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        if logged_in(page.content()):
                            break
                        page.wait_for_timeout(1000)
                    else:
                        raise LoginRequired("手动登录等待超时，请重新打开登录窗口")
                else:
                    if not username or not password:
                        raise LoginRequired("请先在网页中登录")
                    if page.locator('iframe[src*="captcha"], input[name*="captcha"], .g-recaptcha').count():
                        raise LoginRequired("网站要求验证码，请使用手动登录窗口")
                    form = page.locator('form').filter(has=page.locator('input[type="password"]')).first
                    if form.count() != 1:
                        raise LoginRequired("未识别登录表单，请使用手动登录并报告页面变化")
                    action = urljoin(page.url, form.get_attribute("action") or page.url)
                    if urlsplit(action).netloc != urlsplit(BASE).netloc:
                        raise LoginRequired("登录表单地址发生变化，请使用手动登录")
                    form.locator('input[name="username"]').fill(username)
                    form.locator('input[type="password"]').fill(password)
                    form.locator('button[type="submit"], input[type="submit"], button#loginbtn').first.click()
                    try:
                        page.wait_for_function("!!document.querySelector('a[href*=\"/login/logout.php\"]') || (window.M && M.cfg && Number(M.cfg.userid)>0)", timeout=20000)
                    except BrowserTimeout:
                        raise LoginRequired("未能登录：请核对密码，或使用手动登录处理验证码；已停止自动重试") from None
            if not logged_in(page.content()):
                raise LoginRequired("未确认登录成功")
            vault.save_session(context.storage_state())
        finally:
            browser.close()


class Moodle:
    def __init__(self, state=None, base=BASE, transport=None):
        self.base = base.rstrip("/")
        cookies = httpx.Cookies()
        for cookie in (state or {}).get("cookies", []):
            cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""), path=cookie.get("path", "/"))
        self.client = httpx.Client(cookies=cookies, transport=transport, trust_env=False, timeout=httpx.Timeout(60, connect=20), headers={"User-Agent": "iSpaceDownloader/0.2 (personal course backup)", "Accept-Encoding": "identity"})

    def interrupt(self):
        response = getattr(self, '_active_response', None)
        if response is not None:
            stream = response.extensions.get('network_stream')
            try:
                import socket
                if stream:
                    connection = stream.get_extra_info('socket')
                    if connection: connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.client.close()

    def close(self):
        self.client.close()

    def allowed(self, url):
        target, base = urlsplit(url), urlsplit(self.base)
        return target.scheme == base.scheme and target.netloc == base.netloc

    def request(self, method, url, **kwargs):
        from .cancellation import check
        check()
        url = urljoin(self.base + "/", url)
        for _ in range(8):
            if not self.allowed(url):
                raise ResourceError("资源跳转到外部站点，本版不自动下载")
            if "/login/" in urlsplit(url).path:
                raise LoginRequired("登录状态已过期，请重新登录")
            response = self.client.send(self.client.build_request(method, url, **kwargs), stream=True)
            self._active_response = response
            check()
            if response.is_redirect:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise ResourceError("下载跳转缺少目标地址")
                url = urljoin(url, location)
                if method != "HEAD":
                    method, kwargs = "GET", {}
                continue
            if "/login/" in urlsplit(str(response.url)).path:
                response.close()
                raise LoginRequired("登录状态已过期，请重新登录")
            if response.status_code in (401, 403):
                response.close()
                raise LoginRequired("登录已过期或没有访问权限，请检查登录状态")
            return response
        raise ResourceError("网站重定向次数异常")

    def page(self, url):
        response = self.request("GET", url)
        try:
            response.raise_for_status()
            if file_url(str(response.url)):
                return str(response.url), ""
            response.read()
            html = response.text
            if login_page(html):
                raise LoginRequired("登录状态已过期，请重新登录")
            if soup_of(html).select_one('.errorbox, [data-region="error-message"], .alert-danger'):
                raise ResourceError("学校页面报告错误或资源不可访问")
            return str(response.url), html
        finally:
            response.close()

    def check_login(self):
        _, html = self.page("/my/")
        if not logged_in(html):
            raise LoginRequired("请登录学校账号")
        return html

    def courses(self):
        html = self.check_login()
        match = re.search(r'["\']sesskey["\']\s*:\s*["\']([^"\']+)', html)
        if not match:
            raise ResourceError("未识别 Moodle 会话信息，需检查网站页面结构")
        courses, offset = {}, 0
        for _ in range(100):
            response = self.request("POST", "/lib/ajax/service.php?sesskey=" + match[1], json=[{
                "index": 0, "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                "args": {"classification": "all", "limit": 100, "offset": offset, "sort": "fullname"},
            }])
            try:
                response.raise_for_status()
                response.read()
                payload = response.json()[0]
            except (ValueError, IndexError, KeyError):
                raise ResourceError("课程接口格式无法识别，未将不完整结果作为课程列表") from None
            finally:
                response.close()
            if payload.get("error"):
                raise ResourceError("学校未开放当前课程列表接口，需要适配实际页面")
            data = payload.get("data", {})
            batch = data.get("courses")
            if not isinstance(batch, list):
                raise ResourceError("课程接口未返回完整课程列表")
            old_count = len(courses)
            for course in batch:
                courses[int(course["id"])] = {"id": int(course["id"]), "name": soup_of(course["fullname"]).get_text(" ", strip=True)}
            next_offset = int(data.get("nextoffset", 0))
            if not batch or next_offset <= 0:
                return list(courses.values())
            if len(courses) == old_count or next_offset <= offset:
                raise ResourceError("课程分页没有前进，课程列表可能不完整")
            offset = next_offset
        raise ResourceError("课程列表超过读取上限")

    def discover(self, course_id):
        result = Discovery()
        if not hasattr(self, "course_content"): self.course_content = {}
        seen_files, seen_pages, teachers = set(), set(), set()
        try:
            info_url, info_html = self.page(f"/course/info.php?id={course_id}")
            for link in soup_of(info_html).select('.teachers a[href*="/user/"], .coursecontacts a[href*="/user/"]'):
                teachers.update(parse_qs(urlsplit(urljoin(info_url, link["href"])).query).get("id", []))
        except LoginRequired:
            raise
        except (ResourceError, httpx.HTTPError):
            pass
        queue = deque([(self.base + f"/course/view.php?id={course_id}", UNKNOWN)])
        while queue:
            if len(seen_pages) >= 5000:
                result.errors.append("达到页面读取上限，部分资源尚未检查")
                break
            url, inherited = queue.popleft()
            page_key = (canonical(url), inherited.key)
            if page_key in seen_pages:
                continue
            seen_pages.add(page_key)
            try:
                actual_url, html = self.page(url)
                if file_url(actual_url):
                    key = canonical(actual_url)
                    association = (key, inherited.key)
                    if association not in seen_files:
                        seen_files.add(association)
                        result.resources.append(Resource(actual_url, unquote(urlsplit(actual_url).path.rsplit("/", 1)[-1]), key, inherited, url))
                    continue
                content = soup_of(html).select_one('[role="main"], #region-main')
                if content is None:
                    raise ResourceError("未识别课程内容区域，需适配页面")
                for navigation in content.select(".activity-navigation, .activity-navigation-container, .nextprev, nav, [data-region=activity-navigation]"):
                    navigation.decompose()
                from .course_content import extract
                result.notes.extend(extract(html, actual_url, inherited.title, teachers))
                path = urlsplit(actual_url).path
                is_forum = "/mod/forum/" in path
                discovered_here = 0
                for node in content.select('a[href], object[data], embed[src], iframe[src]'):
                    target = urljoin(actual_url, node.get("href") or node.get("data") or node.get("src"))
                    parts = urlsplit(target)
                    group = group_for(node, inherited, actual_url)
                    if file_url(target):
                        if not self.allowed(target):
                            result.errors.append("发现外部附件，本版无法自动读取")
                            continue
                        if is_forum:
                            post = node.find_parent(attrs={"data-region": "post"}) or node.find_parent(class_="forumpost") or node.find_parent("article")
                            author = post.select_one('.author a[href*="/user/"], a[data-region="post-author"], .row.header a[href*="/user/"]') if post else None
                            author_id = parse_qs(urlsplit(urljoin(actual_url, author.get("href", ""))).query).get("id", [""])[0] if author else ""
                            if not author_id or author_id not in teachers:
                                if not author_id or not teachers:
                                    result.errors.append("讨论附件作者身份无法确认，请在学校页面检查该讨论")
                                continue
                        key = canonical(target)
                        discovered_here += 1
                        association = (key, group.key)
                        if association not in seen_files:
                            seen_files.add(association)
                            result.resources.append(Resource(target, unquote(parts.path.rsplit("/", 1)[-1]) or node.get_text(strip=True), key, group, actual_url))
                        continue
                    if not self.allowed(target):
                        continue
                    query = parse_qs(parts.query)
                    supported = parts.path in {"/mod/resource/view.php", "/mod/folder/view.php", "/mod/forum/view.php", "/mod/forum/discuss.php", "/mod/assign/view.php", "/mod/page/view.php", "/mod/url/view.php", "/mod/attendance/view.php", "/mod/groupselect/view.php", "/mod/choicegroup/view.php"}
                    if supported and not any(k in query for k in ("action", "sesskey", "edit", "delete", "submit")):
                        if (canonical(target), group.key) not in seen_pages:
                            queue.append((target, group))
                    elif parts.path == "/course/view.php" and query.get("id") == [str(course_id)] and "section" in query:
                        queue.append((target, group))
                    elif parts.path == "/course/section.php" and query.get("id"):
                        queue.append((target, group))
                if path == "/mod/resource/view.php" and discovered_here == 0:
                    raise ResourceError("文件资源页未发现可下载附件，可能需要适配嵌入方式")
                time.sleep(0.15)
            except LoginRequired:
                raise
            except (ResourceError, httpx.HTTPError) as exc:
                result.errors.append(f"页面读取失败（{urlsplit(url).path}）：{safe_error(exc)}")
        self.course_content[course_id] = list({n["source_key"]:n for n in result.notes}.values())
        result.errors = list(dict.fromkeys(result.errors))
        return result

    def metadata(self, resource):
        response = self.request("HEAD", resource.url)
        try:
            if response.status_code in (405, 501):
                return {}
            response.raise_for_status()
            return dict(response.headers)
        finally:
            response.close()

    def download(self, resource, target: Path, max_bytes=None):
        response = self.request("GET", resource.url)
        try:
            response.raise_for_status()
            if max_bytes is not None and int(response.headers.get("content-length", "0")) > max_bytes:
                raise ResourceError("文件超过 50 MB，请下载后在本机打开")
            total, prefix = 0, b""
            with target.open("wb") as output:
                for chunk in response.iter_bytes(128 * 1024):
                    from .cancellation import check
                    check()
                    prefix = (prefix + chunk)[:16384]
                    if max_bytes is not None and total + len(chunk) > max_bytes:
                        raise ResourceError("文件超过 50 MB，请下载后在本机打开")
                    output.write(chunk)
                    total += len(chunk)
            if login_page(prefix.decode("utf-8", errors="ignore")):
                raise LoginRequired("附件返回登录页面，已停止保存")
            is_html = "text/html" in response.headers.get("content-type", "").lower() or prefix.lstrip().lower().startswith((b"<!doctype html", b"<html"))
            if is_html and not resource.name.lower().endswith((".html", ".htm")):
                raise ResourceError("附件返回网页，未将网页保存为学习文件")
            size = response.headers.get("content-length")
            if size and not response.headers.get("content-encoding") and int(size) != total:
                raise ResourceError("文件长度不符，下载不完整")
            return dict(response.headers)
        finally:
            response.close()


def safe_error(exc):
    if isinstance(exc, (LoginRequired, ResourceError, ValueError)):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"学校服务器返回 HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "网络请求失败，请检查联网情况后重试"
    if isinstance(exc, OSError):
        return "文件读写失败，请检查目录权限、磁盘空间及文件是否被占用"
    return f"操作失败（{type(exc).__name__}），请检查环境或重试"
