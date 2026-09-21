"""Read-only, conservative Moodle content extraction. Never submit forms or run OCR."""

import re
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode, urlunsplit
from bs4 import BeautifulSoup


def clean_url(value):
    u = urlsplit(value)
    if u.scheme not in {"http", "https"} or u.username or u.password:
        return ""
    query = {
        k: v
        for k, v in parse_qs(u.query).items()
        if k.lower() not in {"sesskey", "token", "wstoken", "access_token", "auth"}
    }
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(query, doseq=True), ""))


def extract(html, url, section="", teachers=None):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one('#region-main, [role="main"]')
    if not main:
        return []
    path = urlsplit(url).path
    heading = soup.select_one("h1") or soup.title
    title = heading.get_text(" ", strip=True)[:200] if heading else "课程内容"
    category = "page"
    nodes = []
    if "/mod/assign/" in path:
        category = "assignment"
        # Deliberately exclude personal submissions, feedback and grades.
        nodes = main.select(
            '#intro, [data-region="activity-dates"], .activity-dates, .description'
        )
    elif "/mod/attendance/" in path:
        category = "attendance"
        # The upstream Attendance view.php defaults to the logged-in student.
        # A studentid override, management page or roster must never be ingested.
        query = parse_qs(urlsplit(url).query)
        if (
            not path.endswith("/view.php")
            or "studentid" in query
            or main.select_one(
                'input[name="studentid"], .userinfobox, a[href*="take.php"]'
            )
        ):
            nodes = []
        else:
            nodes = main.select(
                '[data-region="my-attendance"], #my-attendance, .attendance-user-report'
            )
            if not nodes:
                nodes = [
                    table
                    for table in main.select("table.attwidth")
                    if table.select_one(".datecol")
                    and table.select_one(".statuscol")
                    and not table.select_one('a[href*="/user/"]')
                ]
    elif "/group/" in path or "/mod/group" in path or "/mod/choicegroup/" in path:
        category = "group"
        nodes = main.select('[data-region="my-group"], #my-groups, .mygroup')
    elif "/mod/forum/" in path:
        category = "announcement"
        # Discussion replies are outside v0.7's scope.
        node = main.select_one(
            '[data-region="post"] .posting, .forumpost .posting, .discussioncontent'
        )
        if node:
            post = node.find_parent(attrs={"data-region": "post"}) or node.find_parent(
                class_="forumpost"
            )
            author = (
                post.select_one(
                    '.author a[href*="/user/"], a[data-region="post-author"], .row.header a[href*="/user/"]'
                )
                if post
                else None
            )
            author_id = (
                parse_qs(urlsplit(urljoin(url, author.get("href", ""))).query).get(
                    "id", [""]
                )[0]
                if author
                else ""
            )
            if author_id and author_id in (teachers or set()):
                nodes = [node]
    elif "/mod/url/" in path:
        category = "link"
        nodes = main.select("#intro, .urlworkaround")
    elif "/course/" in path:
        category = "overview"
        nodes = main.select(
            ".summary, .activity.modtype_label .contentwithoutlink, .activity.modtype_text .contentwithoutlink"
        )
    else:
        nodes = main.select("#intro, .generalbox, .contentwithoutlink")
    rows = []
    for i, node in enumerate(nodes):
        # Copy: sanitization must not change the file crawler's DOM.
        n = BeautifulSoup(str(node), "html.parser")
        partial = bool(n.select("img, iframe, object, video, audio, canvas"))
        for bad in n.select(
            'script, style, form, nav, button, input, iframe, object, embed, .pointscol, a[href*="/attendance/attendance.php"]'
        ):
            bad.decompose()
        for link in n.select("a[href]"):
            target = clean_url(urljoin(url, link["href"]))
            if target:
                link.append(" (" + target + ")")
        value = n.get_text("\n", strip=True)
        if not value and not partial:
            continue
        from .grouping import group_for, UNKNOWN

        group = group_for(node, UNKNOWN, url)
        node_section = group.title if group != UNKNOWN else section
        stable = node.get("id") or ("content-" + str(i))
        rows.append(
            {
                "source_key": clean_url(url) + "#" + stable,
                "category": category,
                "title": title,
                "body": value[:30000],
                "url": clean_url(url),
                "section": node_section,
                "partial": partial or len(value) > 30000,
            }
        )
    if not rows and category in {
        "assignment",
        "attendance",
        "group",
        "link",
        "announcement",
    }:
        rows.append(
            {
                "source_key": clean_url(url) + "#unparsed",
                "category": category,
                "title": title,
                "body": "此页面暂不能完整提取，请在学校原网页查看。",
                "url": clean_url(url),
                "section": section,
                "partial": True,
            }
        )
    return rows
