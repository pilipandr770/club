// Личный помощник Clubi. Строки берём из data-атрибутов (CSP: без inline-скриптов).
(function () {
  var root = document.getElementById("assistant");
  if (!root) { return; }
  var toggle = root.querySelector(".assistant-toggle");
  var panel = root.querySelector(".assistant-panel");
  var log = root.querySelector(".assistant-log");
  var form = root.querySelector(".assistant-form");
  var input = root.querySelector("textarea");
  var contextLine = root.querySelector(".assistant-context");
  var KEY = "aiko-chat-" + root.dataset.user;
  var state = { open: false, messages: [] };

  function load() {
    try {
      var saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
      if (saved && Array.isArray(saved.messages)) { state = saved; }
    } catch (e) { /* хранилище недоступно: работаем без него */ }
  }
  function save() {
    try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* ничего */ }
  }
  function addBubble(role, text) {
    var item = document.createElement("p");
    item.className = "assistant-msg assistant-" + role;
    item.textContent = text;
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
    return item;
  }
  function render() {
    log.textContent = "";
    if (!state.messages.length) { addBubble("assistant", root.dataset.intro); }
    state.messages.forEach(function (m) { addBubble(m.role, m.text); });
    panel.hidden = !state.open;
    toggle.setAttribute("aria-expanded", state.open ? "true" : "false");
  }
  function setOpen(value) {
    state.open = value;
    panel.hidden = !value;
    toggle.setAttribute("aria-expanded", value ? "true" : "false");
    save();
    if (value) { input.focus(); }
  }

  toggle.addEventListener("click", function () { setOpen(!state.open); });
  root.querySelector(".assistant-close").addEventListener("click", function () { setOpen(false); });
  root.querySelector(".assistant-clear").addEventListener("click", function () {
    state.messages = []; contextLine.textContent = ""; save(); render();
  });
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text) { return; }
    var history = state.messages.slice(-8);
    state.messages.push({ role: "user", text: text });
    addBubble("user", text);
    input.value = "";
    var pending = addBubble("assistant", root.dataset.thinking);
    pending.classList.add("assistant-pending");
    form.querySelector("button").disabled = true;

    fetch(root.dataset.endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": root.dataset.csrf },
      body: JSON.stringify({ message: text, path: location.pathname, history: history })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) { return { ok: response.ok, data: data }; });
    }).then(function (result) {
      var reply = result.ok ? result.data.answer : (result.data.error || root.dataset.failed);
      pending.textContent = reply;
      pending.classList.remove("assistant-pending");
      if (result.ok) {
        state.messages.push({ role: "assistant", text: reply });
        contextLine.textContent = result.data.context ? root.dataset.sees.replace("%s", result.data.context) : "";
      } else {
        state.messages.pop();
      }
      save();
    }).catch(function () {
      pending.textContent = root.dataset.failed;
      pending.classList.remove("assistant-pending");
      state.messages.pop();
      save();
    }).then(function () {
      form.querySelector("button").disabled = false;
      log.scrollTop = log.scrollHeight;
    });
  });

  load();
  render();
})();
