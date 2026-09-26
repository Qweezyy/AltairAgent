"use strict";
/* Altair — живой космический фон приветствия (до первого сообщения).
   Тема-зависимый: под тёмную тему — глубокий космос, под светлую — спокойное
   «рассветное»/бирюзовое небо, чтобы фон всегда сочетался с интерфейсом.

   Стили (по 2 на тему):
     тёмные:  "nebula" (цветные туманности, млечная полоса) • "amoled" (чистый чёрный)
     светлые: "dawn"   (тёплое рассветное небо)            • "aurora" (прохладное сияние)

   Общее: параллаксные мерцающие звёзды, диффракционные лучи у ярких, bloom,
   созвездия, редкие падающие звёзды, мягкая глубина. Лёгкий (один RAF-цикл, пауза
   на скрытой вкладке, статичный кадр при prefers-reduced-motion). Следит за размером
   контейнера (ResizeObserver) — фон растягивается на всю область при скрытии рельса.

   API: window.Cosmos.mount(container) / unmount() / leave(done) — fade out after the first message
        .setStyle(name)        — выбрать стиль (сам поймёт, тёмный он или светлый)
        .applyTheme(isDark)    — переключить фон под тему (вызывается из applyTheme UI)
        .darkStyle()/.lightStyle() — текущие сохранённые предпочтения. */
(function () {
  var STYLES = {
    // ---- тёмные ----
    nebula: {
      light: false,
      bgTop: "#0a0918", bgMid: "#070610", bgBot: "#040309",
      starColors: ["#ffffff", "#ffffff", "#f4f7ff", "#fff2cf", "#cfe0ff", "#ffe6b0", "#dfe8ff"],
      heroColors: ["#fff6dc", "#dfeaff", "#ffe3a6"],
      density: 10500, heroCount: 9, milky: true, vignette: 0.62,
      nebulae: [
        { x: 0.22, y: 0.30, r: 0.7, c: "rgba(96,74,210,0.16)" },
        { x: 0.80, y: 0.24, r: 0.55, c: "rgba(58,120,220,0.12)" },
        { x: 0.66, y: 0.82, r: 0.75, c: "rgba(70,60,180,0.13)" },
        { x: 0.14, y: 0.80, r: 0.5, c: "rgba(150,80,200,0.09)" },
      ],
    },
    amoled: {
      light: false,
      bgTop: "#000000", bgMid: "#000000", bgBot: "#000000",
      starColors: ["#ffffff", "#ffffff", "#ffd98a", "#9fe6ff", "#e8d6ff"],
      heroColors: ["#ffd36c", "#8fe0ff", "#ffffff"],
      density: 14500, heroCount: 11, milky: false, vignette: 0.0,
      nebulae: [
        { x: 0.20, y: 0.26, r: 0.55, c: "rgba(96,50,220,0.07)" },
        { x: 0.82, y: 0.80, r: 0.6, c: "rgba(0,150,255,0.06)" },
      ],
    },
    // ---- светлые ----
    dawn: {
      // Тёплое рассветное небо: мягкие персик/золото/роза, сдержанные звёзды-пылинки.
      light: true,
      bgTop: "#eaf1fb", bgMid: "#f5eef1", bgBot: "#fdf6ea",
      starColors: ["#c7b58a", "#b3c0da", "#d6c496", "#c5b4cd", "#a8b5cb"],
      heroColors: ["#ffd7a0", "#ffc8d6", "#cdd9ff"],
      density: 13000, heroCount: 7, milky: true, vignette: 0.0,
      nebulae: [
        { x: 0.24, y: 0.28, r: 0.74, c: "rgba(255,190,140,0.22)" },
        { x: 0.80, y: 0.22, r: 0.60, c: "rgba(255,214,150,0.18)" },
        { x: 0.68, y: 0.80, r: 0.80, c: "rgba(236,168,196,0.17)" },
        { x: 0.14, y: 0.78, r: 0.55, c: "rgba(178,170,232,0.15)" },
      ],
    },
    aurora: {
      // Прохладное сияние: бирюза/голубой/фиалка на светлой базе.
      light: true,
      bgTop: "#e6f0f7", bgMid: "#edf0f6", bgBot: "#f3f0f8",
      starColors: ["#9fb0cc", "#a3c1c8", "#b1a8cf", "#9bb7d6"],
      heroColors: ["#bfe6f0", "#c9d6ff", "#dcc9f2"],
      density: 13000, heroCount: 7, milky: false, vignette: 0.0,
      nebulae: [
        { x: 0.26, y: 0.30, r: 0.76, c: "rgba(120,205,190,0.20)" },
        { x: 0.78, y: 0.24, r: 0.62, c: "rgba(140,175,235,0.17)" },
        { x: 0.62, y: 0.80, r: 0.80, c: "rgba(180,150,225,0.16)" },
        { x: 0.16, y: 0.74, r: 0.52, c: "rgba(150,205,225,0.14)" },
      ],
    },
  };
  var DARK_STYLES = ["nebula", "amoled"];
  var LIGHT_STYLES = ["dawn", "aurora"];

  function reduced() {
    try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; }
  }
  function isDarkNow() {
    var t = document.documentElement.dataset.theme;
    if (t === "dark") return true;
    if (t === "light") return false;
    try { return !window.matchMedia("(prefers-color-scheme: light)").matches; } catch (e) { return true; }
  }
  function readPref(key, allowed, fallback) {
    try { var v = localStorage.getItem(key); if (allowed.indexOf(v) >= 0) return v; } catch (e) {}
    return fallback;
  }
  function readDark() { return readPref("cosmos_style", DARK_STYLES, "nebula"); }
  function readLight() { return readPref("cosmos_style_light", LIGHT_STYLES, "dawn"); }

  function Cosmos() {
    this.canvas = null; this.ctx = null; this.raf = 0; this.stars = []; this.heroes = [];
    this.consts = []; this.shoot = null; this.t = 0; this.nextShoot = 2600;
    this.w = 0; this.h = 0; this.dpr = 1;
    this.light = !isDarkNow();
    this.style = this.light ? readLight() : readDark();
    this._onResize = this.resize.bind(this); this._onVis = this._vis.bind(this);
    this._ro = null;
  }

  Cosmos.prototype.darkStyle = function () { return readDark(); };
  Cosmos.prototype.lightStyle = function () { return readLight(); };

  Cosmos.prototype.mount = function (container) {
    if (!container) return;
    this.unmount();
    this.container = container;
    this.light = !isDarkNow();
    this.style = this.light ? readLight() : readDark();
    var c = document.createElement("canvas");
    c.className = "cosmos-bg";
    container.insertBefore(c, container.firstChild);
    this.canvas = c; this.ctx = c.getContext("2d");
    this.resize(); this.seed();
    window.addEventListener("resize", this._onResize);
    document.addEventListener("visibilitychange", this._onVis);
    try { this._ro = new ResizeObserver(this._onResize); this._ro.observe(container); } catch (e) {}
    if (reduced()) { this.draw(); return; }
    this.last = performance.now(); this.loop();
  };

  // After the first message the sky does not cut to the plain background: it fades out while
  // drifting slightly forward, as if flying past the stars (same timing as the Android app).
  // Keeps animating while it fades; `done` runs once the canvas is gone.
  Cosmos.prototype.leave = function (done) {
    var c = this.canvas, self = this;
    if (!c) { if (done) done(); return; }
    var calm = reduced();
    var ms = calm ? 350 : 1600;
    c.style.transformOrigin = "50% 40%";
    c.style.transition = "opacity " + ms + "ms cubic-bezier(.4,0,.2,1), transform " + ms + "ms cubic-bezier(.4,0,.2,1)";
    void c.offsetWidth;  // commit the start state so the transition actually runs
    c.style.opacity = "0";
    if (!calm) c.style.transform = "scale(1.12)";
    clearTimeout(this._leaveTimer);
    this._leaveTimer = setTimeout(function () {
      self._leaveTimer = 0;
      if (self.canvas === c) self.unmount();
      if (done) done();
    }, ms + 60);
  };

  Cosmos.prototype.unmount = function () {
    if (this._leaveTimer) { clearTimeout(this._leaveTimer); this._leaveTimer = 0; }
    if (this.raf) cancelAnimationFrame(this.raf), this.raf = 0;
    window.removeEventListener("resize", this._onResize);
    document.removeEventListener("visibilitychange", this._onVis);
    if (this._ro) { try { this._ro.disconnect(); } catch (e) {} this._ro = null; }
    if (this.canvas && this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    this.canvas = null; this.ctx = null; this.stars = []; this.heroes = []; this.consts = []; this.shoot = null;
  };

  // Выбрать стиль вручную (из настроек). Сохраняет в нужную ячейку (тёмн./светл.)
  // и, если стиль относится к активной сейчас теме, сразу применяет.
  Cosmos.prototype.setStyle = function (style) {
    var S = STYLES[style]; if (!S) return;
    try { localStorage.setItem(S.light ? "cosmos_style_light" : "cosmos_style", style); } catch (e) {}
    if (!!S.light === this.light) {
      this.style = style;
      if (this.canvas) { this.seed(); this.draw(); }
    }
  };

  // Переключить фон под тему интерфейса (light/dark). Вызывается из applyTheme UI.
  Cosmos.prototype.applyTheme = function (isDark) {
    var light = !isDark;
    if (light === this.light && this.canvas) return;
    this.light = light;
    this.style = light ? readLight() : readDark();
    if (this.canvas) { this.seed(); this.draw(); }
  };

  Cosmos.prototype._vis = function () {
    if (document.hidden) { if (this.raf) cancelAnimationFrame(this.raf), this.raf = 0; }
    else if (this.canvas && !this.raf && !reduced()) { this.last = performance.now(); this.loop(); }
  };

  Cosmos.prototype.resize = function () {
    if (!this.canvas || !this.container) return;
    var rect = this.container.getBoundingClientRect();
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = Math.max(1, rect.width); this.h = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.w * this.dpr);
    this.canvas.height = Math.round(this.h * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (reduced() && this.stars.length) this.draw();
  };

  Cosmos.prototype.seed = function () {
    var S = STYLES[this.style];
    var n = Math.round(Math.min(220, Math.max(60, (this.w * this.h) / S.density)));
    this.stars = [];
    for (var i = 0; i < n; i++) {
      var layer = i % 3;
      this.stars.push({
        x: Math.random(), y: Math.random(),
        r: (layer + 1) * 0.32 + Math.random() * 0.75,
        c: S.starColors[(Math.random() * S.starColors.length) | 0],
        tw: Math.random() * 6.283, sp: 0.5 + Math.random() * 1.5,
        drift: (layer + 1) * 0.0013, layer: layer,
      });
    }
    this.heroes = [];
    for (var j = 0; j < S.heroCount; j++) {
      this.heroes.push({
        x: Math.random(), y: Math.random(),
        r: 1.1 + Math.random() * 1.3,
        c: S.heroColors[(Math.random() * S.heroColors.length) | 0],
        tw: Math.random() * 6.283, sp: 0.4 + Math.random() * 0.8,
        rays: !S.light && (this.style === "amoled" || Math.random() > 0.4),
      });
    }
    this.consts = [
      { nodes: [[0.16, 0.2], [0.24, 0.3], [0.33, 0.26], [0.4, 0.36], [0.3, 0.44]], edges: [[0, 1], [1, 2], [2, 3], [1, 4]] },
      { nodes: [[0.72, 0.62], [0.8, 0.7], [0.86, 0.6], [0.9, 0.72], [0.78, 0.8]], edges: [[0, 1], [1, 2], [2, 3], [1, 4]] },
      { nodes: [[0.6, 0.16], [0.68, 0.24], [0.76, 0.18], [0.72, 0.32]], edges: [[0, 1], [1, 2], [1, 3]] },
    ];
  };

  Cosmos.prototype.loop = function () {
    var self = this;
    this.raf = requestAnimationFrame(function (now) {
      var dt = Math.min(50, now - self.last); self.last = now; self.t += dt;
      self.step(dt); self.draw(); self.loop();
    });
  };

  Cosmos.prototype.step = function (dt) {
    // Всё движение намеренно медленное — премиальный, спокойный космос.
    for (var i = 0; i < this.stars.length; i++) {
      var s = this.stars[i];
      s.tw += dt * 0.0008 * s.sp;
      s.y -= s.drift * dt * 0.012; if (s.y < -0.02) { s.y = 1.02; s.x = Math.random(); }
    }
    for (var h = 0; h < this.heroes.length; h++) this.heroes[h].tw += dt * 0.0006 * this.heroes[h].sp;
    this.nextShoot -= dt;
    if (!this.shoot && this.nextShoot <= 0) {
      this.nextShoot = 11000 + Math.random() * 12000; // редкие, чтобы не суетить
      this.shoot = { x: 0.03 + Math.random() * 0.55, y: 0.03 + Math.random() * 0.32, vx: 0.30 + Math.random() * 0.24, vy: 0.14 + Math.random() * 0.14, life: 0, max: 1200 };
    }
    if (this.shoot) { this.shoot.life += dt; if (this.shoot.life > this.shoot.max) this.shoot = null; }
  };

  Cosmos.prototype._glint = function (x, y, len, w, color, a) {
    var ctx = this.ctx;
    ctx.save();
    ctx.translate(x, y);
    for (var k = 0; k < 2; k++) {
      ctx.rotate(k * Math.PI / 2);
      var g = ctx.createLinearGradient(-len, 0, len, 0);
      g.addColorStop(0, "rgba(255,255,255,0)");
      g.addColorStop(0.5, color);
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = g; ctx.globalAlpha = a;
      ctx.fillRect(-len, -w / 2, len * 2, w);
    }
    ctx.restore();
  };

  Cosmos.prototype.draw = function () {
    if (!this.ctx) return;
    if (STYLES[this.style].light) this._drawLight();
    else this._drawDark();
  };

  // -------- тёмный космос (свечение поверх тьмы, composite "lighter") --------
  Cosmos.prototype._drawDark = function () {
    var ctx = this.ctx, w = this.w, h = this.h, S = STYLES[this.style];
    var bg = ctx.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, S.bgTop); bg.addColorStop(0.5, S.bgMid); bg.addColorStop(1, S.bgBot);
    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);

    ctx.globalCompositeOperation = "lighter";

    if (S.milky) {
      ctx.save();
      ctx.translate(w * 0.5, h * 0.5); ctx.rotate(-0.5); ctx.translate(-w * 0.5, -h * 0.5);
      var mg = ctx.createLinearGradient(0, h * 0.35, 0, h * 0.65);
      mg.addColorStop(0, "rgba(120,130,220,0)");
      mg.addColorStop(0.5, "rgba(150,140,210,0.10)");
      mg.addColorStop(1, "rgba(120,130,220,0)");
      ctx.fillStyle = mg; ctx.fillRect(-w * 0.5, h * 0.3, w * 2, h * 0.4);
      ctx.restore();
    }

    var pulse = 0.5 + 0.5 * Math.sin(this.t * 0.00016);
    for (var k = 0; k < S.nebulae.length; k++) {
      var nb = S.nebulae[k], cx = nb.x * w, cy = nb.y * h;
      var rr = nb.r * Math.max(w, h) * (0.9 + 0.12 * (k % 2 ? pulse : 1 - pulse));
      var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rr);
      g.addColorStop(0, nb.c); g.addColorStop(0.6, nb.c.replace(/[\d.]+\)$/, "0.04)")); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, rr, 0, 6.2832); ctx.fill();
    }

    for (var c = 0; c < this.consts.length; c++) {
      var co = this.consts[c], ns = co.nodes, tw = 0.35 + 0.25 * Math.sin(this.t * 0.0009 + c);
      ctx.strokeStyle = "rgba(255,224,150," + (0.09 + 0.06 * tw) + ")"; ctx.lineWidth = 0.7;
      for (var e = 0; e < co.edges.length; e++) {
        var a1 = ns[co.edges[e][0]], b1 = ns[co.edges[e][1]];
        ctx.beginPath(); ctx.moveTo(a1[0] * w, a1[1] * h); ctx.lineTo(b1[0] * w, b1[1] * h); ctx.stroke();
      }
      for (var nn = 0; nn < ns.length; nn++) {
        var nx = ns[nn][0] * w, ny = ns[nn][1] * h;
        ctx.fillStyle = "rgba(255,236,190," + (0.7 + 0.3 * tw) + ")";
        ctx.beginPath(); ctx.arc(nx, ny, 1.4, 0, 6.2832); ctx.fill();
      }
    }

    for (var i = 0; i < this.stars.length; i++) {
      var s = this.stars[i], sa = 0.5 + 0.5 * (0.5 + 0.5 * Math.sin(s.tw));
      ctx.globalAlpha = sa; ctx.fillStyle = s.c;
      ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r, 0, 6.2832); ctx.fill();
      if (s.layer === 2) { ctx.globalAlpha = sa * 0.16; ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r * 3.2, 0, 6.2832); ctx.fill(); }
    }

    for (var hh = 0; hh < this.heroes.length; hh++) {
      var H = this.heroes[hh], hx = H.x * w, hy = H.y * h, ha = 0.6 + 0.4 * (0.5 + 0.5 * Math.sin(H.tw));
      var bloom = ctx.createRadialGradient(hx, hy, 0, hx, hy, H.r * 8);
      bloom.addColorStop(0, H.c); bloom.addColorStop(0.32, "rgba(255,255,255,0.06)"); bloom.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = ha * 0.32; ctx.fillStyle = bloom;
      ctx.beginPath(); ctx.arc(hx, hy, H.r * 8, 0, 6.2832); ctx.fill();
      if (H.rays) this._glint(hx, hy, H.r * (this.style === "amoled" ? 13 : 8), 0.9, H.c, ha * 0.38);
      ctx.globalAlpha = ha; ctx.fillStyle = "#fff";
      ctx.beginPath(); ctx.arc(hx, hy, H.r, 0, 6.2832); ctx.fill();
    }

    if (this.shoot) {
      var sh = this.shoot, p = sh.life / sh.max, ease = p < 0.15 ? p / 0.15 : p > 0.75 ? (1 - p) / 0.25 : 1;
      var phx = (sh.x + sh.vx * p) * w, phy = (sh.y + sh.vy * p) * h;
      var ptx = (sh.x + sh.vx * Math.max(0, p - 0.09)) * w, pty = (sh.y + sh.vy * Math.max(0, p - 0.09)) * h;
      var lg = ctx.createLinearGradient(ptx, pty, phx, phy);
      lg.addColorStop(0, "rgba(255,240,200,0)"); lg.addColorStop(1, "rgba(255,244,214," + (0.9 * ease) + ")");
      ctx.globalAlpha = 1; ctx.strokeStyle = lg; ctx.lineWidth = 2; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(ptx, pty); ctx.lineTo(phx, phy); ctx.stroke();
      this._glint(phx, phy, 10, 1.2, "rgba(255,255,255,0.9)", ease * 0.8);
      ctx.fillStyle = "rgba(255,255,255," + ease + ")"; ctx.beginPath(); ctx.arc(phx, phy, 1.7, 0, 6.2832); ctx.fill();
    }

    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;

    if (S.vignette > 0) {
      var vg = ctx.createRadialGradient(w * 0.5, h * 0.45, Math.min(w, h) * 0.3, w * 0.5, h * 0.5, Math.max(w, h) * 0.75);
      vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, "rgba(0,0,0," + S.vignette + ")");
      ctx.fillStyle = vg; ctx.fillRect(0, 0, w, h);
    }
  };

  // -------- светлое небо (мягкие пастельные слои, composite "source-over") --------
  Cosmos.prototype._drawLight = function () {
    var ctx = this.ctx, w = this.w, h = this.h, S = STYLES[this.style];
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
    var bg = ctx.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, S.bgTop); bg.addColorStop(0.55, S.bgMid); bg.addColorStop(1, S.bgBot);
    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);

    // Мягкое верхнее свечение — воздух и глубина.
    var top = ctx.createRadialGradient(w * 0.5, -h * 0.18, 0, w * 0.5, -h * 0.18, Math.max(w, h) * 0.95);
    top.addColorStop(0, "rgba(255,255,255,0.55)"); top.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = top; ctx.fillRect(0, 0, w, h);

    // Мягкая тёплая диагональная лента (dawn).
    if (S.milky) {
      ctx.save();
      ctx.translate(w * 0.5, h * 0.5); ctx.rotate(-0.5); ctx.translate(-w * 0.5, -h * 0.5);
      var mg = ctx.createLinearGradient(0, h * 0.35, 0, h * 0.65);
      mg.addColorStop(0, "rgba(255,210,170,0)");
      mg.addColorStop(0.5, "rgba(255,206,158,0.16)");
      mg.addColorStop(1, "rgba(255,210,170,0)");
      ctx.fillStyle = mg; ctx.fillRect(-w * 0.5, h * 0.3, w * 2, h * 0.4);
      ctx.restore();
    }

    // Пастельные туманности (мягкое дыхание).
    var pulse = 0.5 + 0.5 * Math.sin(this.t * 0.00016);
    for (var k = 0; k < S.nebulae.length; k++) {
      var nb = S.nebulae[k], cx = nb.x * w, cy = nb.y * h;
      var rr = nb.r * Math.max(w, h) * (0.9 + 0.10 * (k % 2 ? pulse : 1 - pulse));
      var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rr);
      g.addColorStop(0, nb.c); g.addColorStop(0.55, nb.c.replace(/[\d.]+\)$/, "0.05)")); g.addColorStop(1, nb.c.replace(/[\d.]+\)$/, "0)"));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, rr, 0, 6.2832); ctx.fill();
    }

    // Созвездия — едва заметные тёплые/прохладные линии.
    var line = this.style === "dawn" ? "180,140,90" : "90,120,160";
    for (var c = 0; c < this.consts.length; c++) {
      var co = this.consts[c], ns = co.nodes, tw = 0.35 + 0.25 * Math.sin(this.t * 0.0009 + c);
      ctx.strokeStyle = "rgba(" + line + "," + (0.10 + 0.06 * tw) + ")"; ctx.lineWidth = 0.7;
      for (var e = 0; e < co.edges.length; e++) {
        var a1 = ns[co.edges[e][0]], b1 = ns[co.edges[e][1]];
        ctx.beginPath(); ctx.moveTo(a1[0] * w, a1[1] * h); ctx.lineTo(b1[0] * w, b1[1] * h); ctx.stroke();
      }
      for (var nn = 0; nn < ns.length; nn++) {
        var nx = ns[nn][0] * w, ny = ns[nn][1] * h;
        ctx.fillStyle = "rgba(" + line + "," + (0.35 + 0.25 * tw) + ")";
        ctx.beginPath(); ctx.arc(nx, ny, 1.3, 0, 6.2832); ctx.fill();
      }
    }

    // Звёзды-пылинки: мягкие цветные точки с низкой прозрачностью.
    for (var i = 0; i < this.stars.length; i++) {
      var s = this.stars[i], sa = 0.22 + 0.30 * (0.5 + 0.5 * Math.sin(s.tw));
      ctx.globalAlpha = sa; ctx.fillStyle = s.c;
      ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r * 0.9, 0, 6.2832); ctx.fill();
    }
    ctx.globalAlpha = 1;

    // «Герои»: мягкие тёплые/прохладные ореолы (как далёкие светила).
    for (var hh = 0; hh < this.heroes.length; hh++) {
      var H = this.heroes[hh], hx = H.x * w, hy = H.y * h, ha = 0.5 + 0.5 * (0.5 + 0.5 * Math.sin(H.tw));
      var bloom = ctx.createRadialGradient(hx, hy, 0, hx, hy, H.r * 10);
      bloom.addColorStop(0, _hexA(H.c, 0.5 * ha));
      bloom.addColorStop(0.5, _hexA(H.c, 0.12 * ha));
      bloom.addColorStop(1, _hexA(H.c, 0));
      ctx.fillStyle = bloom; ctx.beginPath(); ctx.arc(hx, hy, H.r * 10, 0, 6.2832); ctx.fill();
      ctx.globalAlpha = 0.55 * ha; ctx.fillStyle = "#fff";
      ctx.beginPath(); ctx.arc(hx, hy, H.r * 0.7, 0, 6.2832); ctx.fill();
      ctx.globalAlpha = 1;
    }

    // Падающая звезда — деликатная, цвета акцента.
    if (this.shoot) {
      var sh = this.shoot, p = sh.life / sh.max, ease = p < 0.15 ? p / 0.15 : p > 0.75 ? (1 - p) / 0.25 : 1;
      var phx = (sh.x + sh.vx * p) * w, phy = (sh.y + sh.vy * p) * h;
      var ptx = (sh.x + sh.vx * Math.max(0, p - 0.09)) * w, pty = (sh.y + sh.vy * Math.max(0, p - 0.09)) * h;
      var col = this.style === "dawn" ? "233,160,90" : "110,150,190";
      var lg = ctx.createLinearGradient(ptx, pty, phx, phy);
      lg.addColorStop(0, "rgba(" + col + ",0)"); lg.addColorStop(1, "rgba(" + col + "," + (0.6 * ease) + ")");
      ctx.strokeStyle = lg; ctx.lineWidth = 1.6; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(ptx, pty); ctx.lineTo(phx, phy); ctx.stroke();
      ctx.fillStyle = "rgba(" + col + "," + (0.7 * ease) + ")"; ctx.beginPath(); ctx.arc(phx, phy, 1.6, 0, 6.2832); ctx.fill();
    }
  };

  // hex "#rrggbb" -> "rgba(r,g,b,a)"
  function _hexA(hex, a) {
    if (hex[0] !== "#") return hex;
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  window.Cosmos = new Cosmos();
})();
