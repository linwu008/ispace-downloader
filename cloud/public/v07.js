"use strict";
window.CourseNestV07 = (() => {
  const node = (tag, text) => {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const raw = (tag, value) => {
    const n = node(tag, value);
    n.dataset.noTranslate = "true";
    return n;
  };
  let csrf = "",
    pending = [],
    initialized = false,
    activeUser = "";
  async function api(path, method = "GET", value) {
    const r = await fetch("/api/v07" + path, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      ...(value ? { body: JSON.stringify(value) } : {}),
    });
    const data = await r.json();
    if (!r.ok) throw Error(data.detail || "操作失败");
    return data;
  }
  const button = (text, fn) => {
    const b = node("button", text);
    b.type = "button";
    b.onclick = async () => {
      b.disabled = true;
      try {
        await fn();
      } catch (e) {
        message(e.message);
      } finally {
        b.disabled = false;
      }
    };
    return b;
  };
  function message(text) {
    const p = document.getElementById("notice");
    if (p) {
      p.hidden = false;
      p.textContent = text;
    } else alert(text);
  }
  function dialog(title) {
    const d = node("dialog");
    d.className = "v07-dialog";
    d.append(node("h2", title));
    document.body.append(d);
    d.addEventListener("close", () => d.remove());
    return d;
  }
  async function termForm(host) {
    const terms = await api("/terms");
    const section = node("section");
    section.className = "v07-card";
    section.append(
      node("h3", "学年与学期"),
      node(
        "p",
        "首次同步前确认学期，新学期另建存档。已存资料不会因学校清空而删除。",
      ),
    );
    const input = node("input");
    input.placeholder = "2026–2027 / 第一学期";
    input.maxLength = 80;
    input.value = terms.find((t) => t.active)?.label || "";
    const select = node("select");
    select.append(new Option("新建学期", ""));
    for (const t of terms) select.append(new Option(t.label, t.id));
    select.onchange = () => {
      input.value = terms.find((t) => t.id === select.value)?.label || "";
    };
    section.append(
      select,
      input,
      button("确认学期", async () => {
        await api("/terms", "POST", {
          label: input.value,
          ...(select.value ? { id: select.value } : {}),
        });
        message("学期已确认");
      }),
    );
    host.append(section);
  }
  const categories = {
    overview: "课程说明",
    announcement: "通知",
    assignment: "作业要求与截止时间",
    attendance: "我的出勤",
    group: "我的分组",
    page: "课程网页",
    link: "学校链接",
  };
  function renderNotes(host, data) {
    const notes = data.notes || [];
    if (!notes.length)
      host.append(node("p", "尚未提取课程文字。请在电脑上线后更新资料清单。"));
    for (const category of Object.keys(categories)) {
      const values = notes.filter((n) => n.category === category);
      if (!values.length) continue;
      const box = node("section");
      box.append(node("h3", categories[category]));
      for (const n of values) {
        const item = node("details");
        const heading = node(
          "summary",
          (n.section ? n.section + " / " : "") + n.title,
        );
        heading.dataset.noTranslate = "true";
        item.append(heading);
        const body = node("pre", n.body);
        body.className = "course-note";
        item.append(body);
        const links = [
          ...new Set(n.body.match(/https?:\/\/[^\s<>\"()]+/g) || []),
        ];
        for (const url of links) {
          try {
            const parsed = new URL(url);
            if (!["https:", "http:"].includes(parsed.protocol)) continue;
            const link = node("a", url);
            link.href = parsed.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.dataset.noTranslate = "true";
            link.className = "school-content-link";
            item.append(link);
          } catch {}
        }
        item.append(
          node(
            "small",
            "同步于 " + new Date(n.updated * 1000).toLocaleString(),
          ),
        );
        if (n.partial)
          item.append(node("p", "内容未完整提取，请核对学校原网页。"));
        if (n.url) {
          const a = node("a", "打开学校原网页 ↗");
          a.href = n.url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          item.append(a);
        }
        const versions = (data.history || []).filter((v) => v.note_id === n.id);
        if (versions.length) {
          const history = node("details");
          history.append(node("summary", "历史版本 (" + versions.length + ")"));
          for (const v of versions)
            history.append(
              node("h4", new Date(v.created * 1000).toLocaleString()),
              node("pre", v.body),
            );
          item.append(history);
        }
        box.append(item);
      }
      host.append(box);
    }
  }
  async function courseNotes(id) {
    if (window.CourseNestDemo?.enabled()) {
      const d = dialog("课程通知与要求");
      d.append(
        node(
          "p",
          "访客演示：这里展示通知、作业要求、截止时间和学校链接。内容为演示，不读取真实资料。",
        ),
        node("h3", "Week 1 / Project"),
        node("p", "阅读第一章，星期五前在学校网站提交。"),
        button("关闭", () => d.close()),
      );
      d.showModal();
      return;
    }
    const d = dialog("课程通知与要求");
    d.append(button("关闭", () => d.close()));
    d.showModal();
    const archives = (await api("/archives")).filter((a) => a.course_id === id);
    if (!archives.length) {
      d.append(node("p", "请先确认学期，再让新版助手更新资料清单。"));
      return;
    }
    const select = node("select"),
      content = node("div");
    for (const a of archives) select.append(new Option(a.semester, a.id));
    async function update() {
      content.replaceChildren();
      renderNotes(content, await api("/archives/" + select.value));
    }
    select.onchange = () => update().catch((e) => message(e.message));
    d.append(select, content);
    await update();
  }
  async function archiveView(host, state) {
    csrf = state.csrf;
    host.replaceChildren();
    host.append(
      node("h1", "学期存档"),
      node(
        "p",
        "课程文字保存在网站；原文件保存在电脑。电脑离线时仍可查看文字与清单，原文件尚不支持云端下载和分享。",
      ),
    );
    await termForm(host);
    const list = node("div");
    host.append(list);
    const archives = await api("/archives");
    if (!archives.length)
      list.append(
        node("p", "确认学期后，已选择同步的资料会在助手连接时建立存档。"),
      );
    for (const a of archives) {
      const section = node("section");
      section.className = "v07-card";
      section.append(
        raw("h2", a.course_name),
        node(
          "p",
          a.semester +
            " · " +
            a.file_count +
            " 份电脑文件 · " +
            a.note_count +
            " 条课程内容",
        ),
      );
      section.append(
        button("查看内容", async () => {
          const d = dialog(a.course_name);
          d.append(button("关闭", () => d.close()));
          d.showModal();
          const data = await api("/archives/" + a.id);
          renderNotes(d, data);
          d.append(node("h3", "保存在电脑的文件"));
          for (const f of data.files)
            d.append(
              raw(
                "p",
                f.group_name +
                  " / " +
                  f.name +
                  " · " +
                  window.CourseNestI18n.text(
                    f.available ? "上次检查存在" : "本地缺失",
                  ),
              ),
            );
        }),
        button("导出到电脑", async () => {
          await api("/archives/" + a.id + "/export", "POST", {
            request_id: crypto.randomUUID(),
          });
          message(
            "导出任务已提交，电脑准备好后请确认。压缩包保存在电脑，包含课程文字和历史版本。",
          );
        }),
        button("修改学期归属", async () => {
          const terms = await api("/terms");
          const d = dialog("修改学期归属"),
            select = node("select");
          for (const t of terms) select.append(new Option(t.label, t.id));
          d.append(
            select,
            button("保存", async () => {
              await api("/archives/" + a.id + "/term", "POST", {
                term_id: select.value,
              });
              d.close();
              await archiveView(host, state);
            }),
            button("关闭", () => d.close()),
          );
          d.showModal();
        }),
      );
      list.append(section);
    }
  }
  async function decisions(showAll = false) {
    if (document.querySelector("dialog[open]")) return;
    const items = pending.filter((j) => showAll || (j.ready && !j.dismissed));
    if (!items.length) return;
    const d = dialog("待确认任务");
    d.id = "download-confirmation";
    let acted = false;
    d.append(node("p", "确认后由电脑执行。文件不会预先下载到云端。"));
    for (const j of items)
      d.append(
        node(
          "p",
          (j.kind === "archive_export" ? "导出学期存档" : "同步课程资料") +
            " · " +
            (j.ready ? "电脑已准备好" : "等待电脑登录学校并检查目录"),
        ),
      );
    const ids = items.filter((j) => j.ready).map((j) => j.id);
    if (ids.length)
      d.append(
        button("开始执行", async () => {
          await api("/confirm", "POST", { ids, action: "confirm" });
          acted = true;
          d.close();
          message("已确认，等待电脑开始执行");
        }),
      );
    d.append(button("稍后处理", () => d.close()));
    d.addEventListener("close", () => {
      if (!acted)
        api("/confirm", "POST", {
          ids: items.map((j) => j.id),
          action: "dismiss",
        }).catch(() => {});
    });
    d.showModal();
  }
  async function refresh(state) {
    if (!state || window.CourseNestDemo?.enabled()) return;
    csrf = state.csrf;
    if (activeUser !== state.user.id) {
      initialized = false;
      document.getElementById("v07-terms")?.remove();
      activeUser = state.user.id;
    }
    if (!initialized) {
      initialized = true;
      const host = node("div");
      host.id = "v07-terms";
      document.getElementById("course-grid").before(host);
      try {
        await termForm(host);
      } catch (e) {
        initialized = false;
        host.remove();
        throw e;
      }
    }
    const result = await api("/pending");
    pending = result.items;
    let buttonNode = document.getElementById("pending-button");
    if (!buttonNode) {
      buttonNode = button("待确认任务", () => decisions(true));
      buttonNode.id = "pending-button";
      document.getElementById("connection").after(buttonNode);
    }
    buttonNode.hidden = !pending.length;
    buttonNode.textContent = "待确认任务 (" + pending.length + ")";
    const ready = state.device?.snapshot?.readiness;
    if (state.device?.online && ready && !ready.auth)
      document.getElementById("connection").textContent =
        "电脑在线 · 请连接学校账号";
    await decisions();
  }
  return { refresh, archiveView, courseNotes };
})();
