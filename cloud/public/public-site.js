"use strict";
(() => {
  const $ = (selector) => document.querySelector(selector);
  const t = (text) => window.CourseNestI18n?.text(text) || text;
  function preview(name) {
    document
      .querySelectorAll("[data-panel]")
      .forEach((n) => (n.hidden = n.dataset.panel !== name));
    document.querySelectorAll("[data-preview]").forEach((n) => {
      n.setAttribute("aria-selected", String(n.dataset.preview === name));
      n.tabIndex = n.dataset.preview === name ? 0 : -1;
    });
  }
  document
    .querySelectorAll("[data-preview], [data-show-preview]")
    .forEach((button) => {
      button.addEventListener("click", () =>
        preview(button.dataset.preview || button.dataset.showPreview),
      );
    });
  document.querySelectorAll("[role=tablist]").forEach((list) =>
    list.addEventListener("keydown", (event) => {
      if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key))
        return;
      const tabs = [...list.querySelectorAll("[role=tab]")];
      const index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? tabs.length - 1
            : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
              tabs.length;
      tabs[next].click();
      tabs[next].focus();
    }),
  );
  if ($("[data-preview]")) preview("files");

  const messages = {
    guide: [
      "从注册到第一次同步",
      `<ol>
      <li><b>创建课巢账号</b><p>填写自己的邮箱、8–128 位密码与邀请码。课巢账号和学校账号独立。</p></li>
      <li><b>下载并启动电脑助手</b><p>支持 Windows 10/11 x64，需要 Edge，无需 Python。完整解压 ZIP，保留 _internal 文件夹，再运行 CourseNestHelper.exe。</p><a href="/download.html">下载 Windows 助手 ↗</a></li>
      <li><b>配对你的电脑</b><p>登录官网，在“我的设备”生成配对码，输入电脑设置。请使用同一官网生成的有效配对码。</p></li>
      <li><b>连接学校与设置目录</b><p>在电脑设置完成学校登录，选择统一总目录或逐课程目录，也可粘贴完整路径。</p></li>
      <li><b>回到官网，开始同步</b><p>选择课程与文件，统一设置每日计划。手机可提交任务；电脑开机联网、助手准备好并确认后执行下载。</p></li>
      </ol><p>原文件保存在电脑，本地存档并不等于云端备份。</p>`,
    ],
    help: [
      "让课巢变得更好",
      `<p>遇到问题，或有一个小建议？</p><p>微信：guaottttt<br><a href="mailto:2738026702@qq.com">2738026702@qq.com</a></p>`,
    ],
  };
  function modal(key) {
    const item = messages[key];
    if (!item) return;
    $("#dialog-title").textContent = item[0];
    $("#dialog-content").innerHTML = item[1]; // Static product copy only.
    if (!$("#info-dialog").open) $("#info-dialog").showModal();
  }
  document
    .querySelectorAll("[data-modal]")
    .forEach((b) => b.addEventListener("click", () => modal(b.dataset.modal)));
  document
    .querySelectorAll(".dialog-close, .dialog-done")
    .forEach((b) =>
      b.addEventListener("click", () => $("#info-dialog").close()),
    );
  $("#info-dialog")?.addEventListener("click", (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    )
      event.currentTarget.close();
  });
  let toastTimer;
  function toast(message) {
    $("#toast").textContent = message;
    $("#toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => ($("#toast").hidden = true), 4500);
  }
  document.querySelectorAll("[data-copy]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(b.dataset.copy);
        toast("微信号已复制");
      } catch {
        toast("微信：guaottttt");
      }
    }),
  );
  $("#guest-enter")?.addEventListener("click", () => {
    window.CourseNestDemo.enter();
    location.assign("/workspace.html#/overview");
  });

  async function request(path, body) {
    const response = await fetch("/api" + path, {
      method: body ? "POST" : "GET",
      signal: AbortSignal.timeout(15000),
      ...(body
        ? {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }
        : {}),
    });
    const value = await response.json();
    if (!response.ok)
      throw Object.assign(new Error(value.detail || "请求失败"), {
        status: response.status,
      });
    return value;
  }
  if (!$("#auth-form")) {
    // Homepage remains a product page even when an account is already signed in.
    request("/me")
      .then(() => {
        const entry = $("[data-workspace-entry]");
        if (entry) {
          entry.href = "/workspace.html#/overview";
          entry.textContent = "进入学习空间 ↗";
        }
      })
      .catch(() => {});
    return;
  }

  const query = new URLSearchParams(location.search);
  const next = query.get("next") || "#/overview";
  // Only known in-app destinations are accepted, never arbitrary redirect URLs.
  const destination =
    /^#\/(overview|courses|devices|history|settings|archives|download|account)$/.test(
      next,
    )
      ? next
      : "#/overview";
  let registering = query.get("mode") === "register",
    inviteRequired = true;
  function auth(mode) {
    registering = mode === "register";
    document.querySelectorAll("[data-auth]").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.auth === mode));
      b.tabIndex = b.dataset.auth === mode ? 0 : -1;
    });
    $("#invite-field").hidden = !(registering && inviteRequired);
    $("#invite").required = registering && inviteRequired;
    $("#password").autocomplete = registering
      ? "new-password"
      : "current-password";
    $("#auth-title").textContent = registering
      ? "创建你的学习空间"
      : "回到你的学习空间";
    $("#auth-subtitle").textContent = registering
      ? "用一个账号，安顿好每个学期。"
      : "登录课巢，继续上一次的学习。";
    $("#submit-text").textContent = registering ? "创建账号" : "登录学习空间";
    $("#auth-note").textContent = "";
  }
  document
    .querySelectorAll("[data-auth]")
    .forEach((b) => b.addEventListener("click", () => auth(b.dataset.auth)));
  auth(registering ? "register" : "login");
  $("#toggle-password").addEventListener("click", () => {
    const password = $("#password");
    password.type = password.type === "password" ? "text" : "password";
    $("#toggle-password").setAttribute(
      "aria-label",
      t(password.type === "password" ? "显示密码" : "隐藏密码"),
    );
  });
  $("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("#auth-submit").disabled) return;
    $("#auth-submit").disabled = true;
    const wasRegistering = registering;
    try {
      await request(wasRegistering ? "/auth/register" : "/auth/login", {
        email: $("#email").value.trim(),
        password: $("#password").value,
        invite: $("#invite").value,
      });
      $("#password").value = "";
      window.CourseNestDemo.exit();
      location.replace(
        "/workspace.html" + (wasRegistering ? "?setup=1" : "") + destination,
      );
    } catch (error) {
      $("#auth-note").textContent = error.status
        ? error.message
        : "暂时无法连接网站，请重试。";
      $("#auth-submit").disabled = false;
    }
  });
  async function restoreSession() {
    $("#session-retry").hidden = true;
    $("#session-status").textContent = "正在连接学习空间…";
    try {
      await request("/me");
      window.CourseNestDemo.exit();
      location.replace(
        "/workspace.html" +
          (query.has("guide") ? "?setup=1" : "") +
          destination,
      );
    } catch (error) {
      if (error.status !== 401) {
        $("#session-status").textContent = "暂时无法连接网站，请重试。";
        $("#session-retry").hidden = false;
        return;
      }
      $("#auth-loading").hidden = true;
      $(".auth-form-wrap").hidden = false;
      if (query.has("guide")) modal("guide");
    }
  }
  $("#session-retry").onclick = restoreSession;
  request("/health")
    .then((health) => {
      inviteRequired = health.registration !== "open";
      $("#local-hint").hidden = !health.local;
      auth(registering ? "register" : "login");
    })
    .catch(() => {});
  restoreSession();
})();
