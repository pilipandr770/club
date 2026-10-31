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
  var suggest = root.querySelector(".assistant-suggest");
  function render() {
    if (suggest) { suggest.hidden = state.messages.length > 0; }
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
    state.messages = []; if (contextLine) { contextLine.textContent = ""; } save(); render();
  });
  function grow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 112) + "px";
  }
  input.addEventListener("input", grow);
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text) { return; }
    var history = state.messages.slice(-8);
    state.messages.push({ role: "user", text: text });
    if (suggest) { suggest.hidden = true; }
    addBubble("user", text);
    input.value = "";
    grow();
    var pending = addBubble("assistant", root.dataset.thinking);
    pending.classList.add("assistant-pending");
    form.querySelector(".assistant-send").disabled = true;

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
        if (contextLine) { contextLine.textContent = result.data.context ? root.dataset.sees.replace("%s", result.data.context) : ""; }
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
      form.querySelector(".assistant-send").disabled = false;
      log.scrollTop = log.scrollHeight;
    });
  });

  // --- голосовой ввод: запись в браузере, расшифровка на сервере, текст попадает в поле ввода ---
  if (suggest) {
    suggest.addEventListener("click", function (event) {
      var chip = event.target.closest("[data-q]");
      if (!chip) { return; }
      input.value = chip.dataset.q;
      form.requestSubmit();
    });
  }

  // Подсказки на самой странице (например, под результатом поиска по закону): открывают чат и отправляют вопрос
  document.addEventListener("click", function (event) {
    var chip = event.target.closest("[data-assistant-ask]");
    if (!chip || form.querySelector(".assistant-send").disabled) { return; }
    event.preventDefault();
    setOpen(true);
    input.value = chip.dataset.assistantAsk;
    form.requestSubmit();
  });

  var mic = form.querySelector(".assistant-mic");
  var consentBox = root.querySelector(".assistant-voice-consent");
  var voiceStatus = root.querySelector(".assistant-voice-status");
  var MAX_SECONDS = 60;
  var recorder = null, chunks = [], stream = null, timer = null, voiceOk = root.dataset.voiceOk === "1";

  function supportedType() {
    var candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    for (var i = 0; i < candidates.length; i++) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(candidates[i])) { return candidates[i]; }
    }
    return "";
  }
  function setRecording(on) {
    var label = on ? root.dataset.micStop : root.dataset.micStart;
    mic.setAttribute("aria-pressed", on ? "true" : "false");
    mic.setAttribute("aria-label", label);
    mic.title = label;
    mic.classList.toggle("assistant-mic-on", on);
  }
  function stopTracks() {
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (timer) { clearTimeout(timer); timer = null; }
  }
  function upload(blob) {
    voiceStatus.textContent = root.dataset.micWorking;
    mic.disabled = true;
    var body = new FormData();
    body.append("audio", blob, "clip");
    fetch(root.dataset.transcribe, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": root.dataset.csrf }, body: body })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) { return { ok: response.ok, data: data }; });
      }).then(function (result) {
        if (result.ok && result.data.text) {
          input.value = (input.value ? input.value.trim() + " " : "") + result.data.text;
          grow();
          voiceStatus.textContent = "";
          input.focus();
        } else {
          voiceStatus.textContent = result.data.error || root.dataset.failed;
        }
      }).catch(function () { voiceStatus.textContent = root.dataset.failed; })
      .then(function () { mic.disabled = false; });
  }
  function startRecording() {
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      stream = s;
      var type = supportedType();
      recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = function (e) { if (e.data && e.data.size) { chunks.push(e.data); } };
      recorder.onstop = function () {
        var blob = new Blob(chunks, { type: (recorder.mimeType || type || "audio/webm").split(";")[0] });
        stopTracks();
        setRecording(false);
        if (blob.size) { upload(blob); }
      };
      recorder.start();
      setRecording(true);
      voiceStatus.textContent = "";
      timer = setTimeout(function () { if (recorder && recorder.state === "recording") { recorder.stop(); } }, MAX_SECONDS * 1000);
    }).catch(function () { voiceStatus.textContent = root.dataset.micDenied; });
  }
  if (mic && root.dataset.voice !== "0" && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder) {
    mic.hidden = false;
    mic.addEventListener("click", function () {
      if (recorder && recorder.state === "recording") { recorder.stop(); return; }
      if (!voiceOk) { consentBox.hidden = false; return; }
      startRecording();
    });
    root.querySelector(".assistant-voice-agree").addEventListener("click", function () {
      fetch(root.dataset.voiceConsentUrl, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": root.dataset.csrf } })
        .then(function (response) {
          if (response.ok) { voiceOk = true; consentBox.hidden = true; startRecording(); }
        });
    });
  }

  load();
  render();
})();
