// Progressive enhancement: navigation stays visible when JavaScript is unavailable.
(function () {
  "use strict";
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("primary-navigation");
  if (!toggle || !nav) return;
  toggle.hidden = false;
  document.documentElement.classList.add("nav-enhanced");
  function close() { toggle.setAttribute("aria-expanded", "false"); }
  toggle.addEventListener("click", function () {
    toggle.setAttribute("aria-expanded", String(toggle.getAttribute("aria-expanded") !== "true"));
  });
  nav.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { close(); toggle.focus(); }
  });
  nav.addEventListener("click", function (event) {
    if (event.target.closest("a")) close();
  });
})();
