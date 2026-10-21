// Лот аукциона: живая цена и обратный отсчёт. Строки берём из data-атрибутов (CSP: без inline-скриптов).
(function () {
  var root = document.getElementById("auction-live");
  if (!root || root.dataset.status !== "open") { return; }
  var priceEl = document.getElementById("live-price");
  var timeEl = document.getElementById("live-time");
  var bidsEl = document.getElementById("live-bids");
  var leaderEl = document.getElementById("live-leader");
  var reserveEl = document.getElementById("live-reserve");
  var buyLabel = document.getElementById("buy-label");
  var amountField = document.getElementById("descending-amount");
  var left = null;
  var toStart = null;
  var toDrop = null;
  var lastBids = null;

  function format(seconds) {
    var d = Math.floor(seconds / 86400);
    var h = Math.floor((seconds % 86400) / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = seconds % 60;
    var clock = (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
    return d > 0 ? d + "d " + clock : clock;
  }

  function paint() {
    if (!timeEl) { return; }
    var parts = [];
    if (toStart) { parts.push(root.dataset.labelStart + ": " + format(toStart)); }
    else if (left !== null) { parts.push(root.dataset.labelLeft + ": " + format(left)); }
    if (toDrop !== null && toDrop !== undefined) { parts.push(root.dataset.labelDrop + ": " + format(toDrop)); }
    timeEl.textContent = parts.join(" · ");
  }

  function tick() {
    if (left !== null && left > 0) { left -= 1; }
    if (toStart) { toStart -= 1; }
    if (toDrop) { toDrop -= 1; }
    paint();
  }

  function apply(state) {
    if (state.status !== "open") {
      if (timeEl) { timeEl.textContent = root.dataset.labelEnded; }
      return false;
    }
    priceEl.textContent = state.price;
    left = state.seconds_left;
    toStart = state.seconds_to_start;
    toDrop = state.seconds_to_drop;
    if (buyLabel) { buyLabel.textContent = buyLabel.textContent.replace(/[\d.,]+\s*€/, state.price); }
    if (amountField) { amountField.value = state.price_plain; }
    if (bidsEl && state.bids !== lastBids) {
      bidsEl.textContent = state.bids_label;
      lastBids = state.bids;
    }
    if (leaderEl) { leaderEl.hidden = !state.leader; }
    if (reserveEl) { reserveEl.textContent = state.reserve_met ? root.dataset.labelReserveOk : root.dataset.labelReserveNo; }
    paint();
    return true;
  }

  function poll() {
    fetch(root.dataset.stateUrl, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (state) { if (state && !apply(state)) { clearInterval(pollTimer); clearInterval(tickTimer); } })
      .catch(function () { /* сеть недоступна: попробуем в следующий раз */ });
  }

  var tickTimer = setInterval(tick, 1000);
  var pollTimer = setInterval(poll, 5000);
  poll();
})();
