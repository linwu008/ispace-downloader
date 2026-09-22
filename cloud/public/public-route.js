"use strict";
// Preserve links already distributed by the helper and saved as bookmarks.
if (
  ["/", "/index.html"].includes(location.pathname) &&
  location.hash.startsWith("#/")
) {
  document.documentElement.dataset.entering = "true";
  location.replace(location.hash === "#/intro" ? "/" : "/workspace.html" + location.search + location.hash);
}
