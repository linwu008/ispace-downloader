"use strict";
let token = "";
const node = (id) => document.getElementById(id);
async function call(path, method = "GET", body) {
  const r = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "x-ispace-token": token },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const v = await r.json();
  if (!r.ok) throw Error(v.detail || "操作失败");
  return v;
}
async function act(f) {
  try {
    await f();
    node("message").textContent = "操作已提交，请等待完成。";
  } catch (e) {
    node("message").textContent = e.message;
  }
}
async function refresh() {
  try {
    const state = await call("/state");
    token = state.csrf;
    const c = await call("/companion");
    node("connection").textContent =
      c.message || "请在官网“我的设备”生成配对码";
    node("pair").hidden = c.paired;
    node("message").textContent = state.busy
      ? "电脑正在处理任务…"
      : state.auth === "logged_in"
        ? "学校账号已连接"
        : "请连接学校账号";
    const list = node("courses");
    list.replaceChildren();
    for (const course of state.courses) {
      const row = document.createElement("p");
      row.textContent =
        course.name + " · " + (course.folder || "未设置保存位置") + " ";
      const button = document.createElement("button");
      button.textContent = "选择文件夹";
      button.onclick = () =>
        act(async () => {
          const choice = await call("/folder", "POST");
          if (choice.folder) {
            await call("/courses/" + course.id, "PUT", {
              folder: choice.folder,
              enabled: true,
            });
            await call("/courses/membership", "PUT", {
              course_ids: [course.id],
              added: true,
            });
          }
          await refresh();
        });
      row.append(button);
      list.append(row);
    }
  } catch (e) {
    node("message").textContent = e.message;
  }
}
node("pair").onsubmit = (e) => {
  e.preventDefault();
  act(async () => {
    await call("/companion/pair", "POST", {
      server: "https://bnbucoursenest.cn",
      code: node("code").value,
      name: "我的 Windows 电脑",
    });
    node("code").value = "";
    await refresh();
  });
};
node("login").onclick = () => act(() => call("/login/manual", "POST"));
node("refresh").onclick = () => act(() => call("/courses/refresh", "POST"));
refresh();
setInterval(() => {
  if (!document.hidden) refresh();
}, 5000);

node("disconnect").onclick = () =>
  act(async () => {
    if (confirm("断开配对？电脑文件保留。")) {
      await call("/companion/disconnect", "POST");
      await refresh();
    }
  });
node("disable-schedule").onclick = () =>
  act(() => call("/schedule", "PUT", { enabled: false, time: "00:00" }));
