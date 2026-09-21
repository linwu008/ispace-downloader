"use strict";
window.mountCourseNestFeature = async function(root = document, pageUrl = location.href) {
const featureLocation = new URL(pageUrl, location.origin);
const $ = (id) => root.querySelector("#" + id),
  make = (tag, text) => {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    return e;
  };
let csrf = "",
  state = null;
async function api(path, method = "GET", data) {
  if (window.CourseNestDemo?.enabled()) return window.CourseNestDemo.request(path,method,data);
  const r = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    ...(data ? { body: JSON.stringify(data) } : {}),
  });
  const b = await r.json();
  if (!r.ok) throw Error(b.detail || "操作失败");
  return b;
}
async function act(fn) {
  try {
    await fn();
  } catch (e) {
    if ($("message")) $("message").textContent = e.message;
  }
}
function button(text, fn) {
  const b = make("button", text);
  b.onclick = () =>
    act(async () => {
      b.disabled = true;
      try {
        await fn();
      } finally {
        b.disabled = false;
      }
    });
  return b;
}
async function initialize() {
  if(window.CourseNestDemo?.enabled()) {
    const notice=make("p","访客演示：所有资料和操作均为模拟，不会连接真实电脑或保存文件。");
    notice.setAttribute("role","status");
    root.querySelector("main")?.prepend(notice);
  }
  const feature = root.querySelector("[data-feature]")?.dataset.feature || document.body.dataset.feature;
  if (feature === "download") {
    const h = await api("/helper");
    $("download").replaceChildren();
    if (h.demo) { $("download").textContent="演示模式：此处不实际下载安装程序。"; $("local-link").removeAttribute("href"); $("local-link").textContent="演示模式不会打开真实电脑设置"; return; }
    if (h.url) {
      const a = make("a", "下载 Windows 助手 v" + h.version);
      a.href = h.url;
      $("download").append(a);
      $("checksum").textContent = "SHA-256：" + h.sha256;
    } else $("download").textContent = "新版安装包正在准备，暂未开放下载。";
    if (/Android|iPhone|iPad/i.test(navigator.userAgent)) {
      $("local-link").removeAttribute("href");
      $("local-link").textContent = "请在 Windows 电脑上下载并完成设置。";
    }
    return;
  }
  if(feature === 'archives' && window.CourseNestV07 && !window.CourseNestDemo?.enabled()) {
    try {state=await api('/me');csrf=state.csrf;const main=root.querySelector('main');const back=make('a','返回官网');back.href='/';await window.CourseNestV07.archiveView(main,state);main.prepend(back);}catch(e){const target=$('message')||root.querySelector('main');target.textContent=e.message;}return;
  }
  const availability = await api("/features");
  if (feature === "archives" && !availability.archive_enabled) {
    $("create").closest("section").hidden = true;
    $("message").textContent = "学期存档、分享和知识总结暂未启用。目前资料仅保存到配对电脑，请妥善备份；官网课程同步可正常使用。";
    return;
  }
  if (feature === "account" && !availability.mail_enabled) {
    $("forgot").hidden = true;
    $("verify").hidden = true;
    $("message").textContent = "邮件服务暂未启用，暂时无法找回密码或验证邮箱。已有账号仍可正常登录。";
    return;
  }
  try {
    state = await api("/me");
    csrf = state.csrf;
  } catch {}
  if (feature === "account") {
    const parts = featureLocation.hash.slice(1).split("/"),
      kind = parts[0],
      token = parts[1];
    $("forgot").onsubmit = (e) => {
      e.preventDefault();
      act(async () => {
        $("message").textContent = (
          await api("/auth/forgot", "POST", { email: $("email").value })
        ).message;
      });
    };
    $("verify").onclick = () =>
      act(async () => {
        $("message").textContent = (
          await api("/account/verify", "POST", {})
        ).message;
      });
    if (kind === "reset" && token) {
      $("reset").hidden = false;
      $("forgot").hidden = true;
      $("reset").onsubmit = (e) => {
        e.preventDefault();
        act(async () => {
          $("message").textContent = (
            await api("/auth/token", "POST", {
              token,
              password: $("password").value,
            })
          ).message;
          $("password").value = "";
          history.replaceState(null, "", location.pathname);
          $("reset").hidden = true;
        });
      };
    }
    if (kind === "verify" && token) {
      $("confirm-token").hidden = false;
      $("confirm-token").onclick = () =>
        act(async () => {
          $("message").textContent = (
            await api("/auth/token", "POST", { token })
          ).message;
          history.replaceState(null, "", location.pathname);
          $("confirm-token").hidden = true;
        });
    }
    return;
  }
  if (!state) {
    $("message").textContent = "请先返回官网登录，再打开学期存档。";
    $("create").hidden = true;
    return;
  }
  for (const c of state.device?.snapshot?.courses || []) {
    const o = make("option", c.name);
    o.value = c.id;
    $("course").append(o);
  }
  $("create").onsubmit = (e) => {
    e.preventDefault();
    act(async () => {
      await api("/archives", "POST", {
        semester: $("semester").value,
        course_id: Number($("course").value),
        language: $("language").value,
        automatic: $("automatic").checked,
      });
      await list();
    });
  };
  async function list() {
    const archives = await api("/archives");
    $("archives").replaceChildren();
    for (const a of archives) {
      const card = make("section");
      card.append(
        make("h2", a.semester + " · " + a.course_name),
        make(
          "p",
          a.files + " 份文件 · " + (a.bytes / 1000000).toFixed(1) + " MB",
        ),
        button("查看存档", () => detail(a.id)),
      );
      $("archives").append(card);
    }
  }
  async function detail(id, share = "") {
    const d = await api(
        "/archives/" +
          id +
          (share ? "?share=" + encodeURIComponent(share) : ""),
      ),
      box = $("detail");
    box.hidden = false;
    box.replaceChildren();
    box.append(
      make("h2", d.archive.course_name),
      make("p", d.archive.semester),
    );
    for (const f of d.files) {
      const p = make("p");
      const link = make("a", f.group_name + " / " + f.name);
      link.href =
        "/api/archives/" +
        id +
        "/files/" +
        f.id +
        (share ? "?share=" + encodeURIComponent(share) : "");
      p.append(link);
      if (d.archive.user_id === state.user?.id)
        p.append(
          button("删除云端副本", async () => {
            if (confirm("删除云端副本？电脑文件保留。")) {
              await api("/archives/" + id + "/files/" + f.id, "DELETE");
              await detail(id);
            }
          }),
        );
      box.append(p);
    }
    if (d.archive.user_id === state.user?.id)
      box.append(
        button("生成 / 更新知识总结", async () => {
          if (
            confirm("将资料文字交给云端 AI 生成总结，消耗共享免费额度。继续？")
          ) {
            await api("/archives/" + id + "/summary", "POST", {});
            await detail(id);
          }
        }),
      );
    for (const summary of d.summaries) {
      box.append(make("h3", "总结状态：" + summary.status));
      let r;
      try {
        r = JSON.parse(summary.result);
      } catch {}
      if (r) {
        box.append(make("pre", r.overview));
        for (const g of r.groups || [])
          box.append(make("h4", g.group), make("pre", g.text));
        box.append(
          make("small", "未覆盖资料：" + (r.excluded || []).join("、")),
        );
        for (const s of r.sources || [])
          box.append(make("small", s.name + " · " + s.locator));
      } else if (summary.result) box.append(make("p", summary.result));
    }
    box.append(button("刷新状态", () => detail(id, share)));
    if (d.archive.user_id === state.user?.id) {
      const auto = make("input");
      auto.type = "checkbox";
      auto.checked = !!d.archive.automatic;
      const label = make("label", "自动存档全部已取得资料");
      label.prepend(auto);
      const lang = make("select");
      for (const [v, t] of [
        ["zh", "中文"],
        ["en", "英文"],
        ["bilingual", "中英双语"],
      ]) {
        const o = make("option", t);
        o.value = v;
        lang.append(o);
      }
      lang.value = d.archive.language;
      box.append(
        label,
        lang,
        button("保存存档设置", async () => {
          await api("/archives/" + id, "PATCH", {
            automatic: auto.checked,
            language: lang.value,
          });
          await detail(id);
        }),
      );
      const select = make("div");
      select.append(make("h3", "手动选择已取得的课程资料"));
      const checks = [];
      for (const f of state.device?.snapshot?.materials || []) {
        if (f.course_id !== d.archive.course_id) continue;
        const label = make("label"),
          c = make("input");
        c.type = "checkbox";
        c.value = f.id;
        c.checked = (d.selected_ids || []).includes(f.id);
        label.append(c, document.createTextNode(f.name));
        select.append(label);
        checks.push(c);
      }
      select.append(
        button("保存手动上传选择", async () => {
          await api("/archives/" + id + "/selection", "POST", {
            ids: checks.filter((c) => c.checked).map((c) => Number(c.value)),
          });
          $("message").textContent =
            "选择已保存；助手在线后上传已取得的文件。自动模式仍会上传所有已取得资料。";
        }),
      );
      box.append(select);
      box.append(
        button("删除整个云端档案", async () => {
          if (confirm("删除该学期课程的云端档案及分享？电脑文件保留。")) {
            await api("/archives/" + id, "DELETE");
            box.hidden = true;
            await list();
          }
        }),
      );
      const email = make("input");
      email.type = "email";
      email.placeholder = "已注册的接收者邮箱";
      box.append(
        make("h3", "分享存档"),
        email,
        button("指定邮箱分享", async () => {
          await api("/archives/" + id + "/shares", "POST", {
            email: email.value,
          });
          $("message").textContent = "分享已创建";
        }),
        button("创建登录后可访问的链接", async () => {
          const r = await api("/archives/" + id + "/shares", "POST", {});
          const input = make("input");
          input.value = r.url;
          input.readOnly = true;
          box.append(input);
          input.select();
        }),
      );
      try {
        for (const s of await api("/archives/" + id + "/shares"))
          if (!s.revoked)
            box.append(
              make("p", s.email || "链接分享"),
              button("撤销分享", async () => {
                await api("/archives/" + id + "/shares/" + s.id, "DELETE");
                await detail(id);
              }),
            );
      } catch {}
    }
    const account = await api("/account");
    if (account.qa_enabled) {
      const q = make("input");
      q.placeholder = "针对本课程资料提问";
      box.append(
        q,
        button("提问", async () => {
          const r = await api("/archives/" + id + "/question", "POST", {
            question: q.value,
          });
          box.append(make("pre", r.answer));
          for (const s of r.sources)
            box.append(make("small", s.name + " · " + s.locator));
        }),
      );
    }
  }
  await list();
  const match = featureLocation.hash.match(/^#([a-f0-9]+)(?:\?share=(.*))?$/);
  if (match) await detail(match[1], match[2] || "");
}
await act(initialize);
};
if (document.body.dataset.feature) window.mountCourseNestFeature();
