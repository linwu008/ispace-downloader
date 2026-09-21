"use strict";
window.CourseNestTransition = (() => {
  let timer,
    count = 0,
    overlay;
  function end() {
    count = Math.max(0, count - 1);
    if (!count) {
      clearTimeout(timer);
      if (overlay) overlay.hidden = true;
    }
  }
  function begin() {
    count++;
    if (count > 1) return end;
    timer = setTimeout(() => {
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "route-loading";
        overlay.setAttribute("role", "status");
        const logo = document.createElement("img");
        logo.src = "/logo.png";
        logo.alt = "CourseNest";
        const text = document.createElement("p");
        text.textContent = "正在准备页面…";
        overlay.append(logo, text);
        document.body.append(overlay);
      }
      overlay.hidden = false;
    }, 500);
    return end;
  }
  return { begin };
})();
setTimeout(() => {
  const e = document.getElementById("session-loading");
  if (e && !e.hidden) e.classList.add("slow");
}, 500);
