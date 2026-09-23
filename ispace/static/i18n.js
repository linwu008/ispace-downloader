"use strict";
window.CourseNestI18n = (() => {
  let language =
      localStorage.getItem("coursenest-language") === "en" ? "en" : "zh",
    dictionary = {};
  const original = new WeakMap(),
    translated = new WeakMap(),
    roots = new Set(),
    observers = new Map();
  const excluded = (e) =>
    e?.closest?.(
      "[data-no-translate],.course-note,.path,#course-title,#account-label,script,style,pre,code,textarea",
    );
  function text(value) {
    if (language === "zh") return value;
    const trimmed = value.trim();
    if (dictionary[trimmed]) return value.replace(trimmed, dictionary[trimmed]);
    // Only UI nodes reach this point: course titles, source text and paths are excluded.
    let result = value;
    for (const key of Object.keys(dictionary)
      .filter((k) => k.length > 1 && /[\u3400-\u9fff]/.test(k))
      .sort((a, b) => b.length - a.length)) {
      if (result.includes(key))
        result = result.split(key).join(dictionary[key]);
    }
    return result;
  }
  function apply(root) {
    const observer = observers.get(root);
    observer?.disconnect();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (excluded(n.parentElement)) continue;
      if (!original.has(n) || n.nodeValue !== translated.get(n))
        original.set(n, n.nodeValue);
      n.nodeValue = text(original.get(n));
      translated.set(n, n.nodeValue);
    }
    for (const e of root.querySelectorAll(
      "input[placeholder],button[title],a[title],[aria-label]",
    )) {
      for (const key of ["placeholder", "title", "aria-label"]) {
        if (!e.hasAttribute(key)) continue;
        const source = e.dataset["cn" + key.replace("-", "")];
        if (source === undefined)
          e.dataset["cn" + key.replace("-", "")] = e.getAttribute(key);
        e.setAttribute(key, text(source ?? e.getAttribute(key)));
      }
    }
    observer?.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
    });
  }
  function observe(root) {
    if (roots.has(root)) return;
    roots.add(root);
    let queued = false;
    const observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        apply(root);
      });
    });
    observers.set(root, observer);
    apply(root);
  }
  const nativeConfirm = window.confirm.bind(window),
    nativeAlert = window.alert.bind(window);
  window.confirm = (value) => nativeConfirm(text(String(value)));
  window.alert = (value) => nativeAlert(text(String(value)));
  function toggle() {
    language = language === "zh" ? "en" : "zh";
    localStorage.setItem("coursenest-language", language);
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    for (const root of roots) apply(root);
    document.getElementById("language-switch").textContent =
      language === "en" ? "中文" : "English";
  }
  async function start() {
    const local =
      location.pathname.startsWith("/static/") ||
      document.querySelector('script[src="/static/setup.js"]');
    try {
      dictionary = await (
        await fetch(local ? "/static/en.json" : "/en.json")
      ).json();
    } catch {}
    const b = document.getElementById("language-switch") || document.createElement("button");
    b.id = "language-switch";
    b.type = "button";
    b.dataset.noTranslate = "true";
    b.textContent = language === "en" ? "中文" : "English";
    b.onclick = toggle;
    if (!b.isConnected) document.body.append(b);
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    observe(document.body);
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start);
  else start();
  return { observe, text };
})();
