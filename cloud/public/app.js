"use strict";
const $ = (id) => document.getElementById(id),
  el = (tag, value, cls) => {
    const n = document.createElement(tag);
    if (value !== undefined) n.textContent = value;
    if (cls) n.className = cls;
    return n;
  };
const raw = (tag, value, cls) => {
  const n = el(tag, value, cls);
  n.dataset.noTranslate = "true";
  return n;
};
const names = {
  overview: "总览",
  courses: "我的课程",
  devices: "我的设备",
  history: "任务记录",
  settings: "设置",
};
const statuses = {
  awaiting_confirmation: "待确认",
  queued: "等待设备执行",
  running: "正在执行",
  success: "已完成",
  partial: "部分完成",
  failed: "失败",
  auth_required: "请在助手重新登录",
  canceled: "已取消",
  canceling: "取消请求已提交",
  downloaded: "已下载",
  existing: "已下载",
  missing: "本地缺失",
  pending: "未下载",
  pending_organize: "待整理",
  skipped: "已下载",
};
const kinds = {
  schedule_resolve: "合并每日计划",
  refresh_courses: "刷新学校课程",
  add_courses: "添加课程",
  remove_courses: "移出课程",
  catalog: "更新资料清单",
  selection: "保存文件选择",
  sync: "同步学习资料",
  archive_export: "导出学期存档",
};
let state = null,
  jobs = [],
  current = null,
  draft = new Set(),
  mode = "selected",
  dirty = false,
  scheduleDirty = false,
  availableSelected = new Set(),
  noticeUntil = 0,
  lastSnapshot = -1;
let syncSubmitting = false;
let refreshPromise = null,
  lastGoodFetch = 0;
const UX = window.CourseNestExperience;
const time = (value) =>
  value
    ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString(
        "zh-CN",
      )
    : "尚未连接";
async function api(path, method = "GET", body) {
  if (window.CourseNestDemo.enabled())
    return window.CourseNestDemo.request(path, method, body);
  const res = await fetch("/api" + path, {
    method,
    signal: AbortSignal.timeout(15000),
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(state?.csrf ? { "X-CSRF-Token": state.csrf } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const value = await res.json();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) signedOut();
    throw Object.assign(Error(value.detail || "请求失败"), {
      status: res.status,
    });
  }
  return value;
}
function notice(message, error = false) {
  $("notice").hidden = false;
  $("notice").textContent = message;
  $("notice").className = "notice" + (error ? " error" : "");
  noticeUntil = Date.now() + 15000;
}
function fail(e) {
  notice(UX.error(e), true);
  const dialog = document.querySelector("dialog[open]");
  if (dialog) {
    let node = dialog.querySelector(".dialog-notice");
    if (!node) {
      node = el("p", undefined, "notice error dialog-notice");
      node.setAttribute("role", "alert");
      (dialog.querySelector(".dialog-body") || dialog).prepend(node);
    }
    node.textContent = UX.error(e);
    node.scrollIntoView({ block: "nearest" });
  }
}
function signedOut() {
  $("feature-view").hidden = true;
  $("feature-view").replaceChildren();
  featureViews.clear();
  featureOpen = false;
  $("session-loading").hidden = true;
  lastSnapshot = -1;
  state = null;
  current = null;
  dirty = false;
  draft.clear();
  scheduleDirty = false;
  jobs = [];
  $("workspace").hidden = true;
  location.replace(
    "/login.html?next=" + encodeURIComponent(location.hash || "#/overview"),
  );
  for (const d of document.querySelectorAll("dialog[open]")) d.close();
  if (featurePages[location.hash.slice(2)]) route();
}
function snapshot() {
  return state?.device?.snapshot || { courses: [], groups: [], materials: [] };
}
function courses() {
  return snapshot().courses || [];
}
function files() {
  return snapshot().materials || [];
}
function groups() {
  return snapshot().groups || [];
}
const featurePages = {
  archives: "/archives.html",
  download: "/download.html",
  account: "/account.html",
};
const featureViews = new Map();
let returnView = { hash: "#/overview", x: 0, y: 0 },
  featureOpen = false;
async function showFeature(name) {
  const host = $("feature-view");
  let view = featureViews.get(name);
  const reveal = () => {
    if (location.hash.slice(2) !== name) return;
    for (const child of host.children) child.hidden = child !== view;
    $("workspace").hidden = true;
    host.hidden = false;
    view.hidden = false;
  };
  if (view) {
    if (view.dataset.ready) reveal();
    return;
  }
  view = document.createElement("div");
  view.hidden = true;
  featureViews.set(name, view);
  host.append(view);
  const root = view.attachShadow({ mode: "open" });
  const endTransition = window.CourseNestTransition.begin();
  try {
    const response = await fetch(featurePages[name]);
    if (!response.ok) throw Error("页面暂时无法打开");
    const html = new DOMParser().parseFromString(
      await response.text(),
      "text/html",
    );
    const sheet = document.createElement("link");
    sheet.rel = "stylesheet";
    sheet.href = "/features.css";
    const content = document.createElement("div");
    content.dataset.feature = name;
    content.append(document.importNode(html.querySelector("main"), true));
    const v7sheet = document.createElement("link");
    v7sheet.rel = "stylesheet";
    v7sheet.href = "/v07.css";
    root.append(sheet, v7sheet, content);
    await window.mountCourseNestFeature(root, featurePages[name]);
    window.CourseNestI18n?.observe(root);
  } catch {
    featureViews.delete(name);
    root.replaceChildren();
    root.append(el("p", "页面暂时无法打开，请返回后重试。"));
    const back = el("a", "返回原来的页面");
    back.href = "/";
    root.append(back);
  } finally {
    view.dataset.ready = "true";
    reveal();
    endTransition();
  }
}
function navigateFeature(name) {
  if (!featureOpen)
    returnView = {
      hash: location.hash || "#/overview",
      x: scrollX,
      y: scrollY,
    };
  history.pushState(null, "", "#/" + name);
  route();
  window.scrollTo(0, 0);
}
document.addEventListener("click", (event) => {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    event.altKey
  )
    return;
  const link = event.composedPath().find((n) => n instanceof HTMLAnchorElement);
  if (!link || link.target || link.hasAttribute("download")) return;
  const url = new URL(link.href, location.href);
  if (url.origin !== location.origin) return;
  const name = Object.keys(featurePages).find(
    (k) => featurePages[k] === url.pathname,
  );
  if (name && !url.hash) {
    event.preventDefault();
    navigateFeature(name);
  } else if (
    featureOpen &&
    ["/", "/workspace.html"].includes(url.pathname) &&
    (!url.hash || url.hash === "#/overview")
  ) {
    event.preventDefault();
    history.pushState(null, "", returnView.hash);
    route();
  }
});
window.addEventListener("popstate", route);
function route() {
  const page = location.hash.slice(2) || "overview";
  if (featurePages[page]) {
    featureOpen = true;

    showFeature(page);
    document.title =
      { archives: "学期存档", download: "下载助手", account: "账号服务" }[
        page
      ] + " · CourseNest";
    return;
  }
  const restoring = featureOpen;
  featureOpen = false;
  $("feature-view").hidden = true;
  $("workspace").hidden = !state;
  if (page === "intro") {
    location.replace("/");
    return;
  }
  const chosen = names[page] ? page : "overview";
  document
    .querySelectorAll("[data-page]")
    .forEach((n) => (n.hidden = n.dataset.page !== chosen));
  document.querySelectorAll("#workspace-nav a").forEach((n) => {
    const active = n.hash === "#/" + chosen;
    n.classList.toggle("active", active);
    if (active) n.setAttribute("aria-current", "page");
    else n.removeAttribute("aria-current");
  });
  $("page-title").textContent = names[chosen];
  $("breadcrumb").textContent = "工作空间 / " + names[chosen];
  document.title = names[chosen] + " · BNBU CourseNest";
  $("sidebar").classList.remove("open");
  $("mobile-menu").setAttribute("aria-expanded", "false");
  if (restoring) window.scrollTo(returnView.x, returnView.y);
}
window.addEventListener("hashchange", route);
$("mobile-menu").onclick = () => {
  const open = $("sidebar").classList.toggle("open");
  $("mobile-menu").setAttribute("aria-expanded", String(open));
};
$("logout").onclick = async () => {
  try {
    await api("/auth/logout", "POST");
    $("demo-banner").hidden = true;
    signedOut();
  } catch (e) {
    fail(e);
  }
};
async function queue(kind, payload = {}) {
  const storageKey =
    "coursenest-request:" +
    state.user.id +
    ":" +
    state.device?.id +
    ":" +
    kind +
    ":" +
    JSON.stringify(payload);
  let attempt;
  try {
    attempt = JSON.parse(sessionStorage.getItem(storageKey));
  } catch {}
  if (!attempt || Date.now() - attempt.at > 120000)
    attempt = { id: crypto.randomUUID(), at: Date.now() };
  try {
    sessionStorage.setItem(storageKey, JSON.stringify(attempt));
  } catch {}
  let result;
  try {
    result = await api("/jobs", "POST", {
      kind,
      payload,
      request_id: attempt.id,
    });
    try {
      sessionStorage.removeItem(storageKey);
    } catch {}
  } catch (e) {
    if (e.status) {
      try {
        sessionStorage.removeItem(storageKey);
      } catch {}
    }
    throw e;
  }
  notice(
    window.CourseNestDemo.enabled()
      ? "模拟任务已创建，不会执行真实下载。"
      : kind === "sync" && snapshot().capabilities?.includes("confirm-v1")
        ? "同步请求已保存；电脑准备好后，请在确认窗口点击开始执行。"
        : state?.device?.online
          ? "任务已交给同步助手，完成后会更新状态。"
          : "任务已保存，电脑恢复在线后执行。",
  );
  await refresh();
  return result;
}
async function withFeedback(button, action) {
  if (button.disabled) return;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "正在处理…";
  try {
    return await action();
  } catch (e) {
    fail(e);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}
let onboardingKey = "";
function renderOnboarding() {
  const d = state.device,
    s = snapshot(),
    r = s.readiness;
  const fresh = d?.online && r && Math.abs(Date.now() / 1000 - r.at) < 90;
  const added = courses().filter((c) => c.membership === "added" && c.enabled);
  const checks = [
    ["安装并启动电脑助手", !!d?.last_seen, "/download.html", "下载与安装步骤"],
    ["将电脑与官网配对", !!d, "#/devices", "管理配对"],
    ["登录学校账号", fresh && r.auth, "#/devices", "检查学校登录"],
    [
      "选择课程并授权可用目录",
      fresh &&
        added.length > 0 &&
        added.every((c) => c.bound && r.courses?.includes(c.id)),
      "#/courses",
      "检查课程与目录",
    ],
    [
      "完成首次网站同步",
      jobs.some((j) => j.kind === "sync" && j.status === "success"),
      "#/history",
      "查看任务记录",
    ],
  ];
  const nextKey = JSON.stringify([state.user.id, checks.map((c) => !!c[1])]);
  if (nextKey === onboardingKey) return;
  onboardingKey = nextKey;
  const box = $("onboarding");
  box.replaceChildren();
  const details = el("details");
  details.open = checks.slice(0, 4).some((c) => !c[1]);
  details.append(
    el("summary", "使用检查清单 · " + checks.filter((c) => c[1]).length + "/5"),
  );
  const list = el("ol");
  for (const [label, done, href, action] of checks) {
    const row = el("li"),
      link = el("a", action);
    link.href = href;
    row.append(el("span", (done ? "已确认 · " : "待检查 · ") + label), link);
    list.append(row);
  }
  details.append(
    list,
    el(
      "small",
      "依据最近设备状态和最近 100 条网站任务判断；离线时学校登录与目录状态需重新检查。",
    ),
  );
  box.append(details);
}
function renderSearch() {
  const query = $("global-search").value.trim().toLocaleLowerCase(),
    unsaved = $("only-unsaved").checked;
  const box = $("global-results");
  box.replaceChildren();
  $("course-grid").hidden = !!query || unsaved;
  if (!query && !unsaved) {
    $("search-summary").textContent = "";
    return;
  }
  const matched = courses().filter((c) => c.membership === "added");
  const groupNames = new Map(groups().map((g) => [g.id, g.title]));
  const byCourse = new Map();
  for (const file of files()) {
    if (!byCourse.has(file.course_id)) byCourse.set(file.course_id, []);
    byCourse.get(file.course_id).push(file);
  }
  let count = 0;
  for (const c of matched) {
    const selected = (byCourse.get(c.id) || []).filter(
      (f) =>
        (!unsaved || !UX.savedStatuses.includes(f.status)) &&
        (!query ||
          [c.name, f.name, groupNames.get(f.group_id)]
            .join(" ")
            .toLocaleLowerCase()
            .includes(query)),
    );
    if (
      !selected.length &&
      (unsaved || !c.name.toLocaleLowerCase().includes(query))
    )
      continue;
    count += selected.length;
    const card = el("article", undefined, "search-result"),
      open = el("button", c.name, "text-button");
    open.dataset.noTranslate = "true";
    open.onclick = () => openCourse(c.id);
    card.append(open);
    for (const f of selected.slice(0, 30))
      card.append(
        raw("span", f.name),
        el("span", " · " + (statuses[f.status] || "未下载")),
        el("br"),
      );
    if (selected.length > 30)
      card.append(el("small", "仅列前 30 项，打开课程可查看全部结果。"));
    box.append(card);
  }
  $("search-summary").textContent =
    `匹配 ${count} 份文件 · 依据电脑最近上报的清单`;
  if (!box.children.length)
    box.append(el("p", "没有匹配内容。可调整关键词或刷新学校资料清单。"));
}
let digestAt = 0,
  digestBusy = false,
  digestUser = null;
async function loadDigest(force = false) {
  if (
    digestBusy ||
    (!force && digestUser === state.user.id && Date.now() - digestAt < 60000)
  )
    return;
  digestBusy = true;
  const userId = state.user.id,
    box = $("study-digest");
  try {
    if (window.CourseNestDemo.enabled()) {
      box.replaceChildren(
        el("h2", "课程要求与最近更新"),
        el(
          "p",
          "访客演示：连接电脑后，这里会汇总作业要求、课程通知和资料更新。",
        ),
      );
      return;
    }
    const data = await api("/v07/activity");
    if (state?.user.id !== userId) return;
    box.replaceChildren(
      el("h2", "课程要求与最近更新"),
      el(
        "p",
        "以下为已提取的课程原文节选，不代表全部作业或最终截止时间；请以学校原网页为准。",
      ),
    );
    for (const [title, items] of [
      ["作业与截止要求", data.assignments],
      ["最近更新的课程文字", data.recent],
    ]) {
      const section = el("section");
      section.append(el("h3", title));
      for (const n of items) {
        const item = el("details"),
          summary = raw("summary", n.course_name + " · " + n.title);
        item.append(
          summary,
          raw("p", n.excerpt),
          raw("small", n.semester),
          el("small", "提取于 " + time(n.updated)),
        );
        if (n.partial)
          item.append(el("p", "内容未完整提取，请打开学校原网页核对。"));
        if (n.url) {
          const link = el("a", "打开学校原网页 ↗");
          link.href = n.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          item.append(link);
        }
        const more = el("button", "查看完整课程内容", "text-button");
        more.onclick = () =>
          withFeedback(more, () =>
            window.CourseNestV07.courseNotes(n.course_id),
          );
        item.append(more);
        section.append(item);
      }
      if (!items.length)
        section.append(
          el("p", "暂无已提取内容。确认学期后，让电脑更新课程资料清单。"),
        );
      box.append(section);
    }
    box.append(
      el("small", "每栏最多显示最近 8 条，更多内容见“我的课程”和“学期存档”。"),
    );
    digestAt = Date.now();
    digestUser = userId;
  } catch (e) {
    // Keep existing content, but mark it as stale rather than turning it into an empty success state.
    let warning = box.querySelector(".digest-error");
    if (!warning) {
      warning = el("p", undefined, "digest-error notice error");
      box.append(warning);
    }
    warning.replaceChildren(el("span", "课程概览暂未刷新：" + UX.error(e)));
    const retry = el("button", "重新读取", "text-button");
    retry.onclick = () => withFeedback(retry, () => loadDigest(true));
    warning.append(retry);
  } finally {
    digestBusy = false;
  }
}
$("global-search").oninput = renderSearch;
$("only-unsaved").onchange = renderSearch;
function renderCourses() {
  renderSearch();
  const box = $("course-grid");
  box.replaceChildren();
  for (const c of courses().filter((c) => c.membership === "added")) {
    const card = el("button", undefined, "course-card");
    card.dataset.courseId = c.id;
    card.append(
      el("span", "▤", "eyebrow"),
      raw("h3", c.name),
      el(
        "small",
        `${groups().filter((g) => g.course_id === c.id).length} 个分组 · ${files().filter((f) => f.course_id === c.id && ["downloaded", "existing", "skipped"].includes(f.status)).length} 份已保存`,
      ),
      el(
        "span",
        !c.bound
          ? "等待本地授权目录"
          : c.sync_mode === "all"
            ? "整门课自动下载"
            : "仅同步所选文件",
        "pill",
      ),
    );
    card.onclick = () => openCourse(c.id);
    box.append(card);
  }
  if (!box.children.length)
    box.append(
      el(
        "div",
        state.device
          ? "还没有加入课程。先刷新学校列表，再添加需要的课程。"
          : "配对电脑后，你的课程会出现在这里。",
        "empty",
      ),
    );
}
function renderDevice() {
  const box = $("device-detail");
  box.replaceChildren();
  const d = state.device;
  if (d && !d.snapshot?.capabilities?.includes("cancel-v1")) {
    const upgrade = el("p", "助手有新版可用；原有同步仍可使用。 ");
    const link = el("a", "下载新版助手");
    link.href = "/download.html";
    upgrade.append(link);
    box.append(upgrade);
  }
  if (d && !d.snapshot?.capabilities?.includes("notes-v1"))
    box.append(
      el(
        "p",
        "课程文字与本地学期存档需要首次升级到 v0.7 助手；已有同步仍可继续。以后官网更新不要求同步升级助手。",
      ),
    );
  $("new-pair").disabled = !!d;
  if (d) {
    const row = el("div", undefined, "device-name"),
      copy = el("div");
    copy.append(
      raw("strong", d.name),
      el("p", `${UX.connection(d).title} · 最近连接 ${time(d.last_seen)}`),
    );
    copy.append(
      el(
        "p",
        "官网版本 " +
          state.version +
          " · 助手版本 " +
          (d.snapshot?.version || "未知"),
      ),
    );
    row.append(copy);
    const remove = el("button", "解除配对", "secondary");
    remove.onclick = async () => {
      if (
        !confirm(
          "解除后停止接收网站任务，已下载的本地文件保留。确认解除这台电脑？",
        )
      )
        return;
      try {
        await api("/device", "DELETE");
        $("pair-code").textContent = "";
        await refresh();
        notice("设备授权已撤销，本地文件保留。");
      } catch (e) {
        fail(e);
      }
    };
    row.append(remove);
    box.append(row);
  } else
    box.append(el("p", "尚未配对设备。请按下方步骤连接一台 Windows 电脑。"));
}
function jobTitle(job) {
  const c = courses().find((c) => c.id === job.payload.course_id);
  return kinds[job.kind] + (c ? " · " + c.name : "");
}
function renderJobs() {
  const box = $("job-list");
  box.replaceChildren();
  for (const job of jobs) {
    const row = el("article", undefined, "job"),
      copy = el("div");
    copy.append(
      el("h3", jobTitle(job)),
      el(
        "p",
        job.result.message ||
          (job.status === "awaiting_confirmation"
            ? "电脑准备好后，请在待确认任务中开始执行。"
            : job.status === "queued"
              ? "等待同步助手领取，尚未执行。"
              : job.status === "running"
                ? "电脑正在处理，请稍候。"
                : ""),
      ),
      el("small", time(job.created)),
    );
    const progress = UX.task(job);
    copy.append(
      el("p", progress.detail),
      el("small", "状态更新于 " + time(job.updated || job.created)),
    );
    if (progress.counts) copy.append(el("p", progress.counts));
    if (progress.stalled)
      copy.append(
        el(
          "p",
          "较长时间没有新的执行结果，不一定已卡住。请检查电脑助手；需要停止时可取消任务。",
          "notice",
        ),
      );
    row.append(
      copy,
      el(
        "span",
        statuses[job.status] || job.status,
        "pill " + (job.status === "failed" ? "failed" : ""),
      ),
    );
    if (["queued", "running", "awaiting_confirmation"].includes(job.status)) {
      const cancel = el("button", "取消任务", "text-button");
      cancel.onclick = async () => {
        cancel.disabled = true;
        try {
          await api("/jobs/" + job.id + "/cancel", "POST", {});
          await refresh();
        } catch (e) {
          fail(e);
        } finally {
          cancel.disabled = false;
        }
      };
      copy.append(cancel);
    }
    if (
      job.kind !== "schedule_resolve" &&
      ["failed", "partial", "auth_required"].includes(job.status)
    ) {
      const retry = el("button", "重新执行", "text-button");
      retry.onclick = () =>
        withFeedback(retry, () =>
          job.kind === "archive_export"
            ? api(
                "/v07/archives/" + job.payload.archive_id + "/export",
                "POST",
                { request_id: crypto.randomUUID() },
              ).then(refresh)
            : queue(job.kind, job.payload),
        );
      copy.append(retry);
    }
    box.append(row);
  }
  if (!jobs.length) box.append(el("p", "还没有网站任务。", "empty"));
}
function refresh() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = refreshOnce().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}
async function refreshOnce() {
  if (!$("session-loading").hidden) {
    $("session-status").textContent = "正在打开学习空间…";
    $("session-retry").hidden = true;
  }
  try {
    const value = await api("/me");
    state = value;
    $("demo-banner").hidden = !window.CourseNestDemo.enabled();
    $("session-loading").hidden = true;
    $("workspace").hidden = false;
    $("account-label").textContent = value.user.email;
    route();
    if (new URLSearchParams(location.search).has("setup")) {
      history.replaceState(null, "", location.pathname + location.hash);
      $("setup-dialog").showModal();
    }
    jobs = (await api("/jobs")).items;
    lastGoodFetch = Date.now();
    const d = value.device,
      s = snapshot();
    const connection = UX.connection(d);
    $("connection").textContent = connection.title;
    $("connection").className = "pill" + (!d?.online ? " offline" : "");
    $("connection-detail").replaceChildren(
      el("strong", connection.title),
      el("p", connection.detail),
      el("small", "最近收到电脑消息：" + time(d?.last_seen)),
    );
    const checkAgain = el("button", "刷新连接状态", "text-button");
    checkAgain.onclick = () => withFeedback(checkAgain, refresh);
    $("connection-detail").append(checkAgain);
    renderOnboarding();
    loadDigest();
    $("metric-courses").textContent = courses().filter(
      (c) => c.membership === "added",
    ).length;
    $("metric-files").textContent = files().filter((f) =>
      ["downloaded", "existing", "skipped"].includes(f.status),
    ).length;
    $("metric-jobs").textContent = jobs.filter((j) =>
      UX.activeStatuses.includes(j.status),
    ).length;
    $("metric-time").textContent = d?.schedule.enabled
      ? d.schedule.time
      : "未开启";
    $("snapshot-at").textContent = d ? "清单更新于 " + time(s.at) : "";
    $("sync-all").disabled = !d || syncSubmitting;
    $("sync-all").textContent = syncSubmitting
      ? "正在提交…"
      : window.CourseNestDemo.enabled()
        ? "模拟同步"
        : "立即同步";
    $("refresh-courses").disabled = !d;
    $("add-courses").disabled = !d;
    $("next-title").textContent = d
      ? `${d.name}，${connection.title}`
      : "连接你的学习电脑";
    $("next-copy").textContent = d
      ? "从课程页选择想同步的资料。浏览器关闭后，正在运行的助手也会继续完成任务。"
      : "配对同步助手后，课程与本地保存状态会出现在这里。";
    if (!scheduleDirty) {
      $("schedule-enabled").checked = !!d?.schedule.enabled;
      $("schedule-time").value = d?.schedule.time || "20:00";
    }
    $("local-schedule-note").textContent = s.local_schedule
      ? "请更新到 v0.6 助手，原电脑计划将迁移到官网；迁移完成前暂不启用第二套计划。"
      : "任务执行时，电脑需要开机联网且助手正在运行。";
    const migration = s.plan_migration;
    $("plan-conflict").hidden = !(
      migration?.pending &&
      migration.enabled &&
      d?.schedule.enabled &&
      migration.time !== d.schedule.time
    );
    if (!$("plan-conflict").hidden) {
      $("keep-local").textContent = "保留原电脑计划（" + migration.time + "）";
      $("keep-site").textContent = "保留官网计划（" + d.schedule.time + "）";
    }
    window.CourseNestV07.refresh(state).catch((e) => notice(e.message, true));
    renderDevice();
    renderJobs();
    const snapshotKey = JSON.stringify(s);
    if (snapshotKey !== lastSnapshot) {
      renderCourses();
      lastSnapshot = snapshotKey;
      if (current && !dirty && $("course-dialog").open) {
        const c = courses().find((c) => c.id === current);
        if (c) {
          draft = new Set(
            files()
              .filter(
                (f) =>
                  f.course_id === current &&
                  (f.selected || c.sync_mode === "all"),
              )
              .map((f) => f.id),
          );
          mode = c.sync_mode;
          $("course-mode").value = mode;
          renderGroups();
        }
      }
    }
    if (Date.now() > noticeUntil) {
      if (s.auth === "auth_required")
        notice("学校登录已过期，请在本地同步助手重新登录。", true);
      else $("notice").hidden = true;
    }
  } catch (e) {
    if (state) {
      const info = UX.connection(state.device, true);
      $("connection").textContent = info.title;
      $("connection-detail").replaceChildren(
        el("strong", info.title),
        el("p", info.detail),
        el("small", "网站上次刷新成功：" + time(lastGoodFetch / 1000)),
      );
      const retry = el("button", "重试", "secondary");
      retry.onclick = () => withFeedback(retry, refresh);
      $("connection-detail").append(retry);
      fail(e);
    } else if (!$("session-loading").hidden) {
      $("session-status").textContent = "暂时无法连接网站，请重试。";
      $("session-retry").hidden = false;
    }
  }
}
$("session-retry").onclick = () => refresh();
$("new-pair").onclick = async () => {
  try {
    const p = await api("/pairings", "POST");
    $("pair-code").textContent = p.code;
    $("pair-code").append(
      el("p", "10 分钟内有效 · 网站地址：" + location.origin, "hint"),
    );
  } catch (e) {
    fail(e);
  }
};
$("refresh-courses").onclick = () =>
  withFeedback($("refresh-courses"), () => queue("refresh_courses"));
$("sync-all").onclick = async () => {
  if (syncSubmitting) return;
  syncSubmitting = true;
  $("sync-all").disabled = true;
  $("sync-all").textContent = "正在提交…";
  notice("正在提交同步请求，请稍候…");
  try {
    await queue("sync");
  } catch (e) {
    fail(
      ["TimeoutError", "AbortError"].includes(e.name)
        ? Error(
            "请求超时，暂时无法确认是否提交成功。请先查看任务记录，避免重复提交。",
          )
        : e,
    );
  } finally {
    syncSubmitting = false;
    $("sync-all").disabled = !state?.device;
    $("sync-all").textContent = window.CourseNestDemo.enabled()
      ? "模拟同步"
      : "立即同步";
  }
};
function renderAvailable() {
  const box = $("available-courses");
  box.replaceChildren();
  const q = $("course-search").value.toLowerCase();
  for (const c of courses().filter(
    (c) => c.membership !== "added" && c.name.toLowerCase().includes(q),
  )) {
    const row = el("label", undefined, "check"),
      check = el("input");
    check.type = "checkbox";
    check.checked = availableSelected.has(c.id);
    check.onchange = () =>
      check.checked
        ? availableSelected.add(c.id)
        : availableSelected.delete(c.id);
    row.append(check, raw("span", c.name));
    box.append(row);
  }
  if (!box.children.length)
    box.append(el("p", "没有其他课程。可先关闭浮窗，刷新学校列表。", "empty"));
}
$("add-courses").onclick = () => {
  availableSelected.clear();
  $("course-search").value = "";
  renderAvailable();
  $("add-dialog").showModal();
};
$("course-search").oninput = renderAvailable;
$("confirm-add").onclick = async () => {
  try {
    if (!availableSelected.size) throw Error("请至少选择一门课程");
    await queue("add_courses", { ids: [...availableSelected] });
    $("add-dialog").close();
  } catch (e) {
    fail(e);
  }
};
function key() {
  return `coursenest-draft:${state.user.email}:${state.device.id}:${current}`;
}
function persist() {
  dirty = true;
  sessionStorage.setItem(key(), JSON.stringify({ ids: [...draft], mode }));
  $("selection-count").textContent = `${draft.size} 份已选 · 尚未保存`;
}
function openCourse(id) {
  document.querySelectorAll(".dialog-notice").forEach((n) => n.remove());
  current = id;
  const c = courses().find((c) => c.id === id);
  mode = c.sync_mode;
  draft = new Set(
    files()
      .filter((f) => f.course_id === id && (f.selected || mode === "all"))
      .map((f) => f.id),
  );
  dirty = false;
  try {
    const saved = JSON.parse(sessionStorage.getItem(key()));
    if (saved) {
      draft = new Set(
        saved.ids.filter((id) =>
          files().some((f) => f.id === id && f.course_id === current),
        ),
      );
      mode = saved.mode;
      dirty = true;
    }
  } catch {}
  $("course-title").textContent = c.name;
  $("course-mode").value = mode;
  $("file-search").value = "";
  $("course-note").textContent = c.bound
    ? "文件保存到配对电脑的授权目录；请在电脑文件夹打开原文件。目录修改在电脑设置中完成。"
    : "可以浏览和选择资料；正式同步前，请在本地助手为该课程授权目录。";
  renderGroups();
  $("course-dialog").showModal();
}
function renderGroups() {
  const box = $("group-list"),
    opened = new Set(
      [...box.querySelectorAll("details[open]")].map((n) => n.dataset.group),
    );
  box.replaceChildren();
  const q = $("file-search").value.toLowerCase();
  for (const g of groups()
    .filter((g) => g.course_id === current)
    .sort((a, b) => a.position - b.position)) {
    const members = files().filter(
      (f) => f.course_id === current && f.group_id === g.id,
    );
    const shown = members.filter((f) =>
      (f.name + " " + g.title).toLowerCase().includes(q),
    );
    if (!shown.length) continue;
    const details = el("details", undefined, "group");
    details.dataset.group = g.id;
    details.open = !!q || opened.has(g.id) || opened.size === 0;
    details.append(raw("summary", `${g.title} · ${members.length}`));
    const tools = el("div", undefined, "group-tools");
    for (const [label, choose] of [
      ["选择本组", true],
      ["取消本组", false],
    ]) {
      const button = el("button", label, "text-button");
      button.onclick = () => {
        mode = "selected";
        $("course-mode").value = mode;
        members.forEach((f) => (choose ? draft.add(f.id) : draft.delete(f.id)));
        persist();
        renderGroups();
      };
      tools.append(button);
    }
    details.append(tools);
    for (const f of shown) {
      const row = el("div", undefined, "file-row"),
        label = el("label"),
        check = el("input");
      check.type = "checkbox";
      check.checked = draft.has(f.id);
      check.onchange = () => {
        mode = "selected";
        $("course-mode").value = mode;
        check.checked ? draft.add(f.id) : draft.delete(f.id);
        persist();
      };
      label.append(check, raw("span", f.name));
      const location = el("button", "查看位置", "text-button");
      location.onclick = () => showLocation(f, g);
      row.append(
        label,
        el("span", statuses[f.status] || f.status, "pill"),
        location,
      );
      details.append(row);
    }
    box.append(details);
  }
  if (!box.children.length)
    box.append(
      el(
        "p",
        q
          ? "没有匹配资料。"
          : "尚无资料清单，点击“更新资料清单”由助手读取，不会自动下载。",
        "empty",
      ),
    );
  $("selection-count").textContent =
    `${draft.size} 份已选${dirty ? " · 尚未保存" : ""}`;
}
$("file-search").oninput = renderGroups;
$("course-mode").onchange = () => {
  mode = $("course-mode").value;
  if (mode === "all")
    files()
      .filter((f) => f.course_id === current)
      .forEach((f) => draft.add(f.id));
  persist();
  renderGroups();
};
async function saveChoice(sync = false) {
  const button = $(sync ? "download-choice" : "save-choice");
  button.disabled = true;
  try {
    await queue("selection", { course_id: current, ids: [...draft], mode });
    sessionStorage.removeItem(key());
    dirty = false;
    $("selection-count").textContent = "选择已提交，等待助手应用";
    if (sync) {
      await queue("sync", { course_id: current });
      if (snapshot().capabilities?.includes("confirm-v1")) {
        $("course-dialog").close();
        await window.CourseNestV07.refresh(state);
      }
    }
  } catch (e) {
    fail(e);
  } finally {
    button.disabled = false;
  }
}
$("save-choice").onclick = () => saveChoice();
$("download-choice").onclick = () => saveChoice(true);
$("read-catalog").onclick = () =>
  withFeedback($("read-catalog"), () =>
    queue("catalog", { course_id: current }),
  );
$("remove-course").onclick = async () => {
  if (!confirm("移出后停止该课程同步，已下载文件保留。")) return;
  try {
    await queue("remove_courses", { ids: [current] });
    $("course-dialog").close();
  } catch (e) {
    fail(e);
  }
};
function showLocation(f, g) {
  const box = $("location-body");
  box.replaceChildren();
  box.append(
    raw("h3", f.name),
    el(
      "p",
      courses().find((c) => c.id === f.course_id)?.name + " / " + g.title,
    ),
  );
  box.append(el("div", f.path || "该文件尚未保存到本地。", "path"));
  box.append(
    el(
      "p",
      "这是同步助手最近上报的位置。文件位于 " +
        state.device.name +
        "；打开该电脑上的助手可预览内容并在资源管理器中定位。",
      "hint",
    ),
  );
  $("location-dialog").showModal();
}
document
  .querySelectorAll("[data-close]")
  .forEach(
    (button) => (button.onclick = () => $(button.dataset.close).close()),
  );
$("schedule-enabled").onchange = $("schedule-time").oninput = () =>
  (scheduleDirty = true);
$("schedule-form").onsubmit = async (event) => {
  event.preventDefault();
  await withFeedback(
    $("schedule-form").querySelector("button[type=submit],button"),
    async () => {
      try {
        await api("/schedule", "PUT", {
          enabled: $("schedule-enabled").checked,
          time: $("schedule-time").value,
        });
        scheduleDirty = false;
        await refresh();
        notice("网站每日计划已保存。");
      } catch (e) {
        fail(e);
      }
    },
  );
};
$("demo-exit").onclick = () => {
  window.CourseNestDemo.exit();
  $("demo-banner").hidden = true;
  location.replace("/");
};
$("setup-guide").onclick = () => {
  $("demo-folder-controls").hidden = !window.CourseNestDemo.enabled();
  $("setup-dialog").showModal();
};
$("demo-folder-save").onclick = () => {
  $("demo-folder-note").textContent = "模拟保存成功，没有访问真实文件夹。";
};
$("guide-local").addEventListener("click", (e) => {
  if (window.CourseNestDemo.enabled()) {
    e.preventDefault();
    $("demo-folder-controls").hidden = false;
  }
});
for (const which of ["local", "site"])
  $("keep-" + which).onclick = async () => {
    try {
      await api("/schedule", "PUT", {
        enabled: true,
        time:
          which === "local"
            ? snapshot().plan_migration.time
            : state.device.schedule.time,
      });
      await refresh();
      notice("已提交计划选择，等待助手确认。");
    } catch (e) {
      fail(e);
    }
  };
// Keep controls disabled for the entire request; never automatically retry a write.
for (const id of [
  "new-pair",
  "confirm-add",
  "remove-course",
  "logout",
  "keep-local",
  "keep-site",
]) {
  const control = $(id),
    action = control.onclick;
  control.onclick = () => withFeedback(control, action);
}
refresh();
setInterval(() => {
  if (!document.hidden && state) refresh();
}, 5000);

if (/Android|iPhone|iPad/i.test(navigator.userAgent)) {
  for (const link of document.querySelectorAll('a[href^="http://127.0.0.1"]')) {
    link.removeAttribute("href");
    link.textContent = "请在 Windows 电脑上完成助手设置";
  }
}

if (/Android|iPhone|iPad/i.test(navigator.userAgent)) {
  $("guide-local").removeAttribute("href");
  $("guide-local").textContent = "请在电脑上完成此步骤";
}

const notesButton = el("button", "课程通知与要求", "secondary");
notesButton.onclick = () =>
  withFeedback(notesButton, () => window.CourseNestV07.courseNotes(current));
$("read-catalog").after(notesButton);
