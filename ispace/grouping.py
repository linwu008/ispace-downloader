"""Teaching groups derived from Moodle structure, never download timestamps."""
from dataclasses import dataclass
import hashlib
import re
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class TeachingGroup:
    key: str = "unknown"
    title: str = "待分类"
    position: int = 999999


UNKNOWN = TeachingGroup()
PUBLIC = TeachingGroup("public", "课程公共资料", 0)
TEMPORAL = re.compile(r"(?:\bweek\s*\d+|\b(?:lesson|lecture|lab|session)\s*\d+|第\s*[零〇一二三四五六七八九十百\d]+\s*[周课讲节]|\b\d+\s*[-–—.:：]\s*\S)", re.I)
BROAD = {"lectures", "lecture", "labs", "lab", "submissions", "assignments", "resources", "materials", "课程资料", "教学资料", "讲义", "实验"}
GENERAL = {"general", "general information", "announcements", "course information", "course info", "通用", "常规", "课程信息", "公告"}


def clean_title(value):
    return re.sub(r"\s+", " ", value).strip()[:240]


def is_section(node):
    return node.name in {"li", "section", "div"} and ("course-section" in node.get("class", []) or node.get("data-for") == "section" or bool(re.fullmatch(r"section-\d+", node.get("id", ""))))


def section_group(section):
    heading = section.select_one('.sectionname, .section-title, h2, h3')
    title = clean_title(heading.get_text(" ", strip=True)) if heading else ""
    if title.casefold() in GENERAL:
        return PUBLIC
    identifier = section.get("data-id") or section.get("id")
    if not title or not identifier:
        return UNKNOWN
    match = re.search(r"(\d+)$", section.get("id", ""))
    order = int(match[1]) if match else int(section.get("data-number", 1))
    return TeachingGroup("section:" + str(identifier), title, order * 1000)


def group_for(node, inherited=UNKNOWN, page_url=""):
    section = next((parent for parent in node.parents if is_section(parent)), None)
    group = section_group(section) if section else inherited
    boundary = section or node.find_parent(attrs={"role": "main"}) or node.find_parent(id="region-main")
    if boundary and (section is not None or urlsplit(page_url).path in {"/course/view.php", "/course/section.php"}):
        # Moodle label activities frequently put Week / lesson headings in strong tags.
        for previous in node.previous_elements:
            if previous is boundary:
                break
            if not getattr(previous, "name", None) or previous.name not in {"h2", "h3", "h4", "h5", "strong", "b"}:
                continue
            if previous.find_parent("a"):
                continue
            previous_section = next((p for p in previous.parents if is_section(p)), None)
            if section is not None and previous_section is not section:
                break
            title = clean_title(previous.get_text(" ", strip=True))
            if TEMPORAL.search(title) and title != group.title:
                activity = previous.find_parent(id=re.compile(r"^module-"))
                identity = activity.get("id") if activity else hashlib.sha256(title.casefold().encode()).hexdigest()[:12]
                within = [h for h in boundary.select("h2,h3,h4,h5,strong,b") if TEMPORAL.search(h.get_text(" ", strip=True))]
                order = next((i for i, h in enumerate(within, 1) if h is previous), 1)
                return TeachingGroup(group.key + ":heading:" + identity, title, group.position + order)
    href = node.get("href", "")
    if group.title.casefold() in BROAD and "/mod/folder/view.php" in href:
        name_node = node.select_one('.instancename') or node
        title = clean_title(name_node.get_text(" ", strip=True))
        title = re.sub(r"\s+(?:Folder|文件夹)$", "", title, flags=re.I).strip()
        module_id = parse_qs(urlsplit(href).query).get("id", [""])[0]
        if title and module_id:
            links = boundary.select('a[href*="/mod/folder/view.php"]') if boundary else [node]
            order = next((i for i, link in enumerate(links, 1) if link is node), 1)
            return TeachingGroup(group.key + ":folder:" + module_id, group.title + " — " + title, group.position + order)
    return group
