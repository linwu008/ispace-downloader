"use strict";
const $ = (id) => document.getElementById(id),
  el = (tag, value, cls) => {
    const n = document.createElement(tag);
    if (value !== undefined) n.textContent = value;
    if (cls) n.className = cls;
    return n;
  };
const raw=(tag,value,cls)=>{const n=el(tag,value,cls);n.dataset.noTranslate='true';return n;};
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
const time = (value) =>
  value
    ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString(
        "zh-CN",
      )
    : "尚未连接";
async function api(path, method = "GET", body) {
  if (window.CourseNestDemo.enabled()) return window.CourseNestDemo.request(path, method, body);
  const res = await fetch("/api" + path, {
    method,
    ...(["/me", "/jobs"].includes(path) ? { signal: AbortSignal.timeout(15000) } : {}),
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(state?.csrf ? { "X-CSRF-Token": state.csrf } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const value = await res.json();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) signedOut();
    throw Error(value.detail || "请求失败");
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
  notice(e.message, true);
  const dialog = document.querySelector("dialog[open]");
  if (dialog) {
    let node = dialog.querySelector(".dialog-notice");
    if (!node) {
      node = el("p", undefined, "notice error dialog-notice");
      node.setAttribute("role", "alert");
      dialog.querySelector(".dialog-body").prepend(node);
    }
    node.textContent = e.message;
    node.scrollIntoView({ block: "nearest" });
  }
}
function signedOut() {
  $("feature-view").hidden = true;
  $("feature-view").replaceChildren(); featureViews.clear(); featureOpen = false;
  $("session-loading").hidden = true;
  lastSnapshot = -1;
  state = null;
  current = null;
  dirty = false;
  draft.clear();
  scheduleDirty = false;
  jobs = [];
  $("workspace").hidden = true;
  location.replace("/login.html?next=" + encodeURIComponent(location.hash || "#/overview"));
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
const featurePages = {archives: "/archives.html", download: "/download.html", account: "/account.html"};
const featureViews = new Map();
let returnView = { hash: "#/overview", x: 0, y: 0 }, featureOpen = false;
async function showFeature(name) {
  const host = $("feature-view");
  let view = featureViews.get(name);
  const reveal = () => {
    if(location.hash.slice(2)!==name)return;
    for(const child of host.children)child.hidden=child!==view;
    $("workspace").hidden=true; host.hidden=false;view.hidden=false;
  };
  if (view) { if(view.dataset.ready)reveal(); return; }
  view = document.createElement("div");
  view.hidden=true;featureViews.set(name, view); host.append(view);
  const root = view.attachShadow({mode: "open"});
  const endTransition=window.CourseNestTransition.begin();
  try {
    const response = await fetch(featurePages[name]);
    if (!response.ok) throw Error("页面暂时无法打开");
    const html = new DOMParser().parseFromString(await response.text(), "text/html");
    const sheet = document.createElement("link"); sheet.rel = "stylesheet"; sheet.href = "/features.css";
    const content = document.createElement("div"); content.dataset.feature = name;
    content.append(document.importNode(html.querySelector("main"), true));
    const v7sheet=document.createElement("link");v7sheet.rel="stylesheet";v7sheet.href="/v07.css";root.append(sheet,v7sheet,content);
    await window.mountCourseNestFeature(root, featurePages[name]);
    window.CourseNestI18n?.observe(root);
  } catch {
    featureViews.delete(name); root.replaceChildren();
    root.append(el("p", "页面暂时无法打开，请返回后重试。"));
    const back = el("a", "返回原来的页面"); back.href = "/"; root.append(back);
  } finally {view.dataset.ready="true";reveal();endTransition();}
}
function navigateFeature(name) {
  if (!featureOpen) returnView = {hash:location.hash || "#/overview", x:scrollX, y:scrollY};
  history.pushState(null, "", "#/" + name); route(); window.scrollTo(0,0);
}
document.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const link = event.composedPath().find(n => n instanceof HTMLAnchorElement);
  if (!link || link.target || link.hasAttribute("download")) return;
  const url = new URL(link.href, location.href);
  if (url.origin !== location.origin) return;
  const name = Object.keys(featurePages).find(k => featurePages[k] === url.pathname);
  if (name && !url.hash) { event.preventDefault(); navigateFeature(name); }
  else if (featureOpen && ["/", "/workspace.html"].includes(url.pathname) && (!url.hash || url.hash === "#/overview")) {
    event.preventDefault(); history.pushState(null, "", returnView.hash); route();
  }
});
window.addEventListener("popstate", route);
function route() {
  const page = location.hash.slice(2) || "overview";
  if (featurePages[page]) {
    featureOpen = true;

    showFeature(page);
    document.title = ({archives:"学期存档", download:"下载助手", account:"账号服务"})[page] + " · CourseNest";
    return;
  }
  const restoring = featureOpen;
  featureOpen = false; $("feature-view").hidden = true;
  $("workspace").hidden = !state;
  if(page === 'intro'){ location.replace('/'); return; }
  const chosen = names[page] ? page : "overview";
  document
    .querySelectorAll("[data-page]")
    .forEach((n) => (n.hidden = n.dataset.page !== chosen));
  document
    .querySelectorAll("nav a")
    .forEach((n) => n.classList.toggle("active", n.hash === "#/" + chosen));
  $("page-title").textContent = names[chosen];
  $("breadcrumb").textContent = "工作空间 / " + names[chosen];
  document.title = names[chosen] + " · BNBU CourseNest";
  $("sidebar").classList.remove("open");
  if (restoring) window.scrollTo(returnView.x, returnView.y);
}
window.addEventListener("hashchange", route);
$("mobile-menu").onclick = () => $("sidebar").classList.toggle("open");
$("logout").onclick = async () => {
  try {
    await api("/auth/logout", "POST");
    $("demo-banner").hidden=true;
    signedOut();
  } catch (e) {
    fail(e);
  }
};
async function queue(kind, payload = {}) {
  const result = await api("/jobs", "POST", {
    kind,
    payload,
    request_id: crypto.randomUUID(),
  });
  notice(
    window.CourseNestDemo.enabled() ? "模拟任务已创建，不会执行真实下载。"
      : kind === "sync" && snapshot().capabilities?.includes("confirm-v1")
        ? "同步请求已保存；电脑准备好后，请在确认窗口点击开始执行。"
        : state?.device?.online
          ? "任务已交给同步助手，完成后会更新状态。"
          : "任务已保存，电脑恢复在线后执行。",
  );
  await refresh();
  return result;
}
function renderCourses() {
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
    const link = el("a", "下载新版助手"); link.href = "/download.html";
    upgrade.append(link); box.append(upgrade);
  }
  if(d && !d.snapshot?.capabilities?.includes('notes-v1'))box.append(el('p','课程文字与本地学期存档需要首次升级到 v0.7 助手；已有同步仍可继续。以后官网更新不要求同步升级助手。'));
  $("new-pair").disabled = !!d;
  if (d) {
    const row = el("div", undefined, "device-name"),
      copy = el("div");
    copy.append(
      raw("strong", d.name),
      el("p", `${d.online ? "在线" : "离线"} · 最近连接 ${time(d.last_seen)}`),
    );
    copy.append(el("p", "官网版本 "+state.version+" · 助手版本 "+(d.snapshot?.version||"未知")));
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
      cancel.onclick = async () => {cancel.disabled=true;try {await api("/jobs/"+job.id+"/cancel", "POST", {});await refresh();}catch(e){fail(e);}finally{cancel.disabled=false;}};
      copy.append(cancel);
    }
    if (job.kind !== "schedule_resolve" && ["failed", "partial", "auth_required"].includes(job.status)) {
      const retry = el("button", "重新执行", "text-button");
      retry.onclick = () => (job.kind==='archive_export' ? api('/v07/archives/'+job.payload.archive_id+'/export','POST',{request_id:crypto.randomUUID()}).then(refresh) : queue(job.kind, job.payload)).catch(fail);
      copy.append(retry);
    }
    box.append(row);
  }
  if (!jobs.length) box.append(el("p", "还没有网站任务。", "empty"));
}
async function refresh() {
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
    if(new URLSearchParams(location.search).has("setup")) {
      history.replaceState(null,"",location.pathname+location.hash);
      $("setup-dialog").showModal();
    }
    jobs = (await api("/jobs")).items;
    const d = value.device,
      s = snapshot();
    $("connection").textContent = d
      ? s.paused
        ? "助手已暂停"
        : d.online
          ? "电脑在线"
          : "电脑离线"
      : "尚未配对";
    $("connection").className = "pill" + (!d?.online ? " offline" : "");
    $("metric-courses").textContent = courses().filter(
      (c) => c.membership === "added",
    ).length;
    $("metric-files").textContent = files().filter((f) =>
      ["downloaded", "existing", "skipped"].includes(f.status),
    ).length;
    $("metric-jobs").textContent = jobs.filter((j) =>
      ["queued", "running", "canceling"].includes(j.status),
    ).length;
    $("metric-time").textContent = d?.schedule.enabled
      ? d.schedule.time
      : "未开启";
    $("snapshot-at").textContent = d ? "清单更新于 " + time(s.at) : "";
    $("sync-all").disabled = !d || syncSubmitting;
    $("sync-all").textContent = syncSubmitting ? "正在提交…" : window.CourseNestDemo.enabled() ? "模拟同步" : "立即同步";
    $("refresh-courses").disabled = !d;
    $("add-courses").disabled = !d;
    $("next-title").textContent = d
      ? `${d.name}，${d.online ? "已准备好" : "等待上线"}`
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
    $("plan-conflict").hidden = !(migration?.pending && migration.enabled && d?.schedule.enabled && migration.time !== d.schedule.time);
    if (!$("plan-conflict").hidden) {
      $("keep-local").textContent = "保留原电脑计划（"+migration.time+"）";
      $("keep-site").textContent = "保留官网计划（"+d.schedule.time+"）";
    }
    window.CourseNestV07.refresh(state).catch(e=>notice(e.message,true));
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
    if (state) fail(e);
    else if (!$("session-loading").hidden) {
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
$("refresh-courses").onclick = () => queue("refresh_courses").catch(fail);
$("sync-all").onclick = async () => {
  if (syncSubmitting) return;
  syncSubmitting = true;
  $("sync-all").disabled = true;
  $("sync-all").textContent = "正在提交…";
  notice("正在提交同步请求，请稍候…");
  try {
    await queue("sync");
  } catch (e) {
    fail(["TimeoutError", "AbortError"].includes(e.name)
      ? Error("请求超时，暂时无法确认是否提交成功。请先查看任务记录，避免重复提交。")
      : e);
  } finally {
    syncSubmitting = false;
    $("sync-all").disabled = !state?.device;
    $("sync-all").textContent = window.CourseNestDemo.enabled() ? "模拟同步" : "立即同步";
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
      if(snapshot().capabilities?.includes("confirm-v1")){ $("course-dialog").close(); await window.CourseNestV07.refresh(state); }
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
  queue("catalog", { course_id: current }).catch(fail);
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
};
$("demo-exit").onclick = () => {window.CourseNestDemo.exit();$("demo-banner").hidden=true;location.replace("/");};
$("setup-guide").onclick = () => {$("demo-folder-controls").hidden=!window.CourseNestDemo.enabled();$("setup-dialog").showModal();};
$("demo-folder-save").onclick = () => {$("demo-folder-note").textContent="模拟保存成功，没有访问真实文件夹。";};
$("guide-local").addEventListener("click",e=>{if(window.CourseNestDemo.enabled()){e.preventDefault();$("demo-folder-controls").hidden=false;}});
for (const which of ["local","site"]) $("keep-"+which).onclick = async () => {try{await api("/schedule","PUT",{enabled:true,time:which==="local"?snapshot().plan_migration.time:state.device.schedule.time});await refresh();notice("已提交计划选择，等待助手确认。");}catch(e){fail(e);}};
refresh();
setInterval(() => {
  if (!document.hidden && state) refresh();
}, 5000);

if(/Android|iPhone|iPad/i.test(navigator.userAgent)){for(const link of document.querySelectorAll('a[href^="http://127.0.0.1"]')){link.removeAttribute('href');link.textContent='请在 Windows 电脑上完成助手设置';}}

if (/Android|iPhone|iPad/i.test(navigator.userAgent)) {
  $("guide-local").removeAttribute("href");
  $("guide-local").textContent="请在电脑上完成此步骤";
}

const notesButton=el('button','课程通知与要求','secondary'); notesButton.onclick=()=>window.CourseNestV07.courseNotes(current).catch(fail);$('read-catalog').after(notesButton);
