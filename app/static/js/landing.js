// Лендинг: 3D-сфера на canvas и наклон карточек за курсором. Без внешних библиотек (CSP, DSGVO).
// Если пользователь просит меньше движения (prefers-reduced-motion), анимация останавливается на одном кадре.
(function () {
  "use strict";

  var motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  var finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  var reduceMotion = motionQuery.matches;

  // --- наклон карточек ----------------------------------------------------
  // Значения --rx и --ry задают поворот в градусах; CSS применяет их в transform.
  function setTilt(element, rx, ry) {
    element.style.setProperty("--rx", rx.toFixed(2) + "deg");
    element.style.setProperty("--ry", ry.toFixed(2) + "deg");
  }

  if (finePointer.matches) {
    document.querySelectorAll("[data-tilt]").forEach(function (element) {
      element.addEventListener("pointermove", function (event) {
        if (reduceMotion || !finePointer.matches) return;
        var box = element.getBoundingClientRect();
        var x = (event.clientX - box.left) / box.width - 0.5;
        var y = (event.clientY - box.top) / box.height - 0.5;
        setTilt(element, -y * 6, x * 6);
      });
      element.addEventListener("pointerleave", function () {
        setTilt(element, 0, 0);
      });
    });
  }

  // --- сфера на canvas -----------------------------------------------------
  var canvas = document.querySelector("[data-sphere]");
  if (!canvas || !canvas.getContext) {
    return;
  }
  var context = canvas.getContext("2d");
  if (!context) return;
  var points = [];
  var links = [];
  var visible = true;
  var paused = false;
  var motionToggle = document.querySelector("[data-motion-toggle]");
  var lastTime = 0;
  var color = accentColor();
  var POINT_COUNT = 140;
  var LINK_DISTANCE = 0.42; // доля радиуса, при которой точки соединяются линией
  var rotation = { x: 0.2, y: 0 };
  var target = { x: 0.2, y: 0 };
  var pointer = { x: 0, y: 0 };
  var width = 0;
  var height = 0;
  var running = false;
  var frameId = null;

  // Точки равномерно по сфере (золотое сечение)
  for (var i = 0; i < POINT_COUNT; i++) {
    var y = 1 - (i / (POINT_COUNT - 1)) * 2;
    var radius = Math.sqrt(1 - y * y);
    var theta = i * Math.PI * (3 - Math.sqrt(5));
    points.push({ x: Math.cos(theta) * radius, y: y, z: Math.sin(theta) * radius, phase: Math.random() * Math.PI * 2 });
  }

  // Connections are invariant under rotation: compute once, rather than every frame.
  for (var a = 0; a < points.length; a++) {
    for (var b = a + 1; b < points.length; b++) {
      if (Math.hypot(points[a].x - points[b].x, points[a].y - points[b].y,
          points[a].z - points[b].z) < LINK_DISTANCE) links.push([a, b]);
    }
  }

  function accentColor() {
    var value = getComputedStyle(document.documentElement).getPropertyValue("--color-accent").trim();
    return value || "#1f5fbf";
  }

  function resize() {
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    var box = canvas.getBoundingClientRect();
    width = box.width;
    height = box.height;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    color = accentColor();
    if (!running || reduceMotion) draw(0);
  }

  function project(point, scale, cx, cy) {
    // Поворот вокруг осей X и Y, затем простая перспектива
    var cosY = Math.cos(rotation.y), sinY = Math.sin(rotation.y);
    var cosX = Math.cos(rotation.x), sinX = Math.sin(rotation.x);
    var x1 = point.x * cosY + point.z * sinY;
    var z1 = -point.x * sinY + point.z * cosY;
    var y1 = point.y * cosX - z1 * sinX;
    var z2 = point.y * sinX + z1 * cosX;
    var perspective = 2.4 / (2.4 + z2);
    return { x: cx + x1 * scale * perspective, y: cy + y1 * scale * perspective, z: z2, s: perspective };
  }

  function draw(time) {
    context.clearRect(0, 0, width, height);
    var cx = width / 2;
    var cy = height / 2;
    var scale = Math.min(width, height) * 0.42;

    // Плавно догоняем курсор
    rotation.y += (target.y - rotation.y) * 0.06 + (reduceMotion ? 0 : 0.0016);
    rotation.x += (target.x - rotation.x) * 0.06;

    var projected = points.map(function (point) {
      return project(point, scale, cx, cy);
    });

    // Линии между близкими точками: метафора матчинга
    context.lineWidth = 1;
    links.forEach(function (link) {
      var first = projected[link[0]], second = projected[link[1]];
      var depth = (first.s + second.s) / 2;
      context.strokeStyle = color;
      context.globalAlpha = Math.max(0.05, Math.min(0.35, (depth - 0.6) * 0.5));
      context.beginPath();
      context.moveTo(first.x, first.y);
      context.lineTo(second.x, second.y);
      context.stroke();
    });

    // Точки: дальние бледнее, ближние ярче и чуть пульсируют
    for (var p = 0; p < projected.length; p++) {
      var q = projected[p];
      var pulse = reduceMotion ? 1 : 0.85 + 0.15 * Math.sin(time / 600 + points[p].phase);
      context.globalAlpha = Math.max(0.2, Math.min(1, (q.s - 0.6) * 1.6)) * pulse;
      context.fillStyle = color;
      context.beginPath();
      context.arc(q.x, q.y, 1.6 + q.s * 1.4, 0, Math.PI * 2);
      context.fill();
    }
    context.globalAlpha = 1;
  }

  function loop(time) {
    if (time - lastTime >= 1000 / 30) { draw(time); lastTime = time; }
    if (running && !reduceMotion) {
      frameId = requestAnimationFrame(loop);
    }
  }

  function start() {
    if (running || paused || reduceMotion || !visible || document.hidden) return;
    running = true;
    frameId = requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
    if (frameId) cancelAnimationFrame(frameId);
  }

  window.addEventListener("pointermove", function (event) {
    if (reduceMotion || !finePointer.matches) return;
    pointer.x = event.clientX / window.innerWidth - 0.5;
    pointer.y = event.clientY / window.innerHeight - 0.5;
    target.y = pointer.x * 1.4;
    target.x = 0.2 + pointer.y * 0.9;
  });

  window.addEventListener("resize", resize);

  document.addEventListener("visibilitychange", function () {
    document.hidden ? stop() : start();
  });
  motionQuery.addEventListener("change", function () {
    reduceMotion = motionQuery.matches;
    document.querySelectorAll("[data-tilt]").forEach(function (element) { setTilt(element, 0, 0); });
    stop();
    reduceMotion ? draw(0) : start();
  });

  if (motionToggle) {
    motionToggle.hidden = reduceMotion;
    motionToggle.addEventListener("click", function () {
      paused = !paused;
      motionToggle.setAttribute("aria-pressed", String(paused));
      paused ? stop() : start();
    });
    motionQuery.addEventListener("change", function () { motionToggle.hidden = reduceMotion; });
  }

  resize();
  if ("IntersectionObserver" in window) {
    // Не тратим батарею, когда сфера не на экране
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      visible ? start() : stop();
    }).observe(canvas);
  } else {
    start();
  }
})();
