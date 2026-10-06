"use strict";
/* Altair — маскот «Звёздыш» (Alti). Пухлая 4-конечная звезда с лицом.
   Чистый inline-SVG: объёмный радиальный градиент (свет слева-сверху),
   глянцевый блик, rim-контур, мягкая тень-контакт, glow — по спеке бренда
   (docs/PLAN_BRAND_UI_MOTION.md, раздел «Маскот»). Без сборки и зависимостей.

   window.Mascot.svg({ size, satellites, mood, id }) -> строка SVG.
     size       — пиксели (число или CSS-строка), по умолчанию 96.
     satellites — рисовать 2 звезды-спутника (ПК/телефон/сервер), по умолчанию true.
     mood       — "idle" | "think" | "happy" | "help" | "sleep" | "sad" (eyes and motion).
     act        — what Alti is busy with: "read" | "write" | "shell" | "search" | "web" | "plan" |
                  "agent" | "think" | "tool" | "answer" — a prop in its hands and a motion of its
                  own (see .alti-act-* in redesign.layout.css). Mascot.act(toolName) picks one.
     id         — суффикс для уникальных id градиентов (несколько маскотов на странице).
*/
(function () {
  // Силуэт «Звёздыша»: пухлая 4-конечная звезда с округлыми лопастями и мягкими
  // остриями (по референсу пользователя). Координаты 24×24, центр 12,12.
  const STAR = "M12 0.6 C13.2 7.7 16.3 10.8 23.4 12 C16.3 13.2 13.2 16.3 12 23.4 C10.8 16.3 7.7 13.2 0.6 12 C7.7 10.8 10.8 7.7 12 0.6 Z";
  let _seq = 0;

  function eyes(mood) {
    // Два чистых глаза-пилюли (#241608) + мягкий блик. Без щёчек.
    const ex1 = 9.7, ex2 = 14.3, ey = 12.4, w = 1.5, h = 3.0, r = 0.75;
    if (mood === "happy") {
      // Прищур — довольные дуги.
      return `<path d="M8.9 12.7 q0.8 -1.1 1.6 0" fill="none" stroke="#241608" stroke-width="1.3" stroke-linecap="round"/>
              <path d="M13.5 12.7 q0.8 -1.1 1.6 0" fill="none" stroke="#241608" stroke-width="1.3" stroke-linecap="round"/>`;
    }
    if (mood === "sleep") {
      // Asleep: closed eyes (arcs down) and a small "z" floating up (see .alti-z).
      return `<path d="M8.9 12.3 q0.8 1.0 1.6 0" fill="none" stroke="#241608" stroke-width="1.2" stroke-linecap="round"/>
              <path d="M13.5 12.3 q0.8 1.0 1.6 0" fill="none" stroke="#241608" stroke-width="1.2" stroke-linecap="round"/>`;
    }
    if (mood === "sad") {
      // Something went wrong: brows tilted up in the middle, eyes a little lower.
      return `<rect x="${ex1 - w / 2}" y="${ey - 0.9}" width="${w}" height="${h * 0.8}" rx="${r}" fill="#241608"/>
              <rect x="${ex2 - w / 2}" y="${ey - 0.9}" width="${w}" height="${h * 0.8}" rx="${r}" fill="#241608"/>
              <path d="M8.6 10.1 l1.9 -0.7" stroke="#241608" stroke-width="0.7" stroke-linecap="round"/>
              <path d="M15.4 10.1 l-1.9 -0.7" stroke="#241608" stroke-width="0.7" stroke-linecap="round"/>`;
    }
    if (mood === "help") {
      // Тревога — глаза выше + «?».
      return `<rect x="${ex1 - w / 2}" y="${ey - 1.4}" width="${w}" height="${h}" rx="${r}" fill="#241608"/>
              <rect x="${ex2 - w / 2}" y="${ey - 1.4}" width="${w}" height="${h}" rx="${r}" fill="#241608"/>
              <circle cx="${ex1 + 0.4}" cy="${ey - 0.9}" r="0.42" fill="#fff" opacity="0.9"/>`;
    }
    if (mood === "read") {
      // Reading: the eyes slide along a line (the .alti-act-read motion moves the pupils).
      return `<g class="alti-pupils"><rect x="${ex1 - w / 2 + 0.5}" y="${ey - h / 2 + 0.3}" width="${w}" height="${h * 0.85}" rx="${r}" fill="#241608"/>
              <rect x="${ex2 - w / 2 + 0.5}" y="${ey - h / 2 + 0.3}" width="${w}" height="${h * 0.85}" rx="${r}" fill="#241608"/></g>`;
    }
    if (mood === "focus") {
      // At work: eyes a little lower and narrowed, looking at what it holds.
      return `<rect x="${ex1 - w / 2 + 0.3}" y="${ey - 0.6}" width="${w}" height="${h * 0.62}" rx="${r}" fill="#241608"/>
              <rect x="${ex2 - w / 2 + 0.3}" y="${ey - 0.6}" width="${w}" height="${h * 0.62}" rx="${r}" fill="#241608"/>
              <circle cx="${ex1 + 0.75}" cy="${ey - 0.15}" r="0.32" fill="#fff" opacity="0.85"/>
              <circle cx="${ex2 + 0.75}" cy="${ey - 0.15}" r="0.32" fill="#fff" opacity="0.85"/>`;
    }
    const eh = mood === "think" ? h * 0.7 : h;
    return `<rect x="${ex1 - w / 2}" y="${ey - eh / 2}" width="${w}" height="${eh}" rx="${r}" fill="#241608"/>
            <rect x="${ex2 - w / 2}" y="${ey - eh / 2}" width="${w}" height="${eh}" rx="${r}" fill="#241608"/>
            <circle cx="${ex1 + 0.45}" cy="${ey - eh / 2 + 0.55}" r="0.42" fill="#fff" opacity="0.9"/>
            <circle cx="${ex2 + 0.45}" cy="${ey - eh / 2 + 0.55}" r="0.42" fill="#fff" opacity="0.9"/>`;
  }

  // What Alti holds while it works, drawn by its lower right point (24×24 box). Each prop has
  // its own class, so the motion is CSS and the drawing stays put.
  const PROPS = {
    read: `<g class="alti-prop alti-prop-doc"><rect x="16.2" y="14.6" width="6" height="7.6" rx="1" fill="#fbf6ea" stroke="#c9761a" stroke-width="0.45"/>
             <path d="M17.4 16.6h3.6M17.4 18.1h3.6M17.4 19.6h2.4" stroke="#c9a46a" stroke-width="0.5" stroke-linecap="round"/></g>`,
    write: `<g class="alti-prop alti-prop-pen"><g transform="rotate(38 19.5 18)"><rect x="18.7" y="13.4" width="1.7" height="7.6" rx="0.4" fill="#ffcf5a" stroke="#b8862e" stroke-width="0.35"/>
             <path d="M18.7 21 L19.55 22.6 L20.4 21 Z" fill="#3a2a1a"/><rect x="18.7" y="13.4" width="1.7" height="1.2" fill="#e98a8a"/></g></g>`,
    shell: `<g class="alti-prop alti-prop-term"><rect x="15.4" y="15.6" width="7.6" height="5.8" rx="1.1" fill="#1d1a24" stroke="#c9761a" stroke-width="0.45"/>
             <path d="M16.9 17.4 l1.2 1 l-1.2 1" fill="none" stroke="#ffc247" stroke-width="0.6" stroke-linecap="round" stroke-linejoin="round"/>
             <rect class="alti-caret" x="18.9" y="19.2" width="1.9" height="0.6" rx="0.2" fill="#ffc247"/></g>`,
    search: `<g class="alti-prop alti-prop-lens"><circle cx="19" cy="17.6" r="2.4" fill="rgba(180,220,255,0.35)" stroke="#c9761a" stroke-width="0.7"/>
             <path d="M20.7 19.3 L22.8 21.4" stroke="#8a5a1a" stroke-width="1" stroke-linecap="round"/></g>`,
    web: `<g class="alti-prop alti-prop-globe"><circle cx="19.4" cy="18.4" r="3" fill="#7fb8ff" stroke="#2f6fb8" stroke-width="0.45"/>
             <ellipse class="alti-meridian" cx="19.4" cy="18.4" rx="1.3" ry="3" fill="none" stroke="#e8f3ff" stroke-width="0.4"/>
             <path d="M16.5 18.4h5.8M17 16.9h4.8M17 19.9h4.8" stroke="#e8f3ff" stroke-width="0.35"/></g>`,
    plan: `<g class="alti-prop alti-prop-plan"><rect x="16" y="14.8" width="6.4" height="7.4" rx="1" fill="#fbf6ea" stroke="#c9761a" stroke-width="0.45"/>
             <path d="M17.2 16.8l0.6 0.6l1-1.1M17.2 18.8l0.6 0.6l1-1.1" fill="none" stroke="#3aa76d" stroke-width="0.45" stroke-linecap="round"/>
             <path d="M19.4 16.9h2M19.4 18.9h2M17.3 20.7h4.1" stroke="#c9a46a" stroke-width="0.45" stroke-linecap="round"/></g>`,
    tool: `<g class="alti-prop alti-prop-gear"><circle cx="19.4" cy="18.4" r="2.2" fill="none" stroke="#9aa4b2" stroke-width="1.5" stroke-dasharray="0.9 0.75"/>
             <circle cx="19.4" cy="18.4" r="1.6" fill="#c4ccd6" stroke="#7d8794" stroke-width="0.35"/><circle cx="19.4" cy="18.4" r="0.6" fill="#5b6370"/></g>`,
    think: `<g class="alti-prop alti-prop-sparks"><path class="alti-spark s1" d="M20.5 3.4 l0.35 1 l1 0.35 l-1 0.35 l-0.35 1 l-0.35 -1 l-1 -0.35 l1 -0.35 Z" fill="#fff0c2"/>
             <path class="alti-spark s2" d="M3.4 5.2 l0.28 0.8 l0.8 0.28 l-0.8 0.28 l-0.28 0.8 l-0.28 -0.8 l-0.8 -0.28 l0.8 -0.28 Z" fill="#ffe08a"/>
             <path class="alti-spark s3" d="M21.6 15.6 l0.25 0.7 l0.7 0.25 l-0.7 0.25 l-0.25 0.7 l-0.25 -0.7 l-0.7 -0.25 l0.7 -0.25 Z" fill="#fff6d8"/></g>`,
  };
  PROPS.answer = PROPS.write;

  // Which kind of work a tool is (the same groups as the terminal's cli/tools_view.py).
  const ACT = {
    read: ["read_file", "read_document", "list_directory", "view_image", "code_map", "find_symbol", "memory_read",
      "recall", "tool_output", "git_log", "git_blame", "git_status", "git_diff", "read_skill", "list_skills"],
    write: ["write_file", "edit_file", "apply_patch", "delete_path", "remember", "memory_edit", "create_skill"],
    shell: ["execute_command", "run_python", "run_tests", "run_lint", "type_check", "run_background",
      "start_dev_server", "git_commit", "test_coverage"],
    search: ["grep_search", "find_files", "ast_search", "search_chats", "tool_search", "find_images"],
    web: ["web_search", "fetch_url", "browse_page", "http_request", "download_file", "deep_research"],
    plan: ["update_plan", "write_plan"],
    agent: ["spawn_subagent"],
  };
  function act(name) {
    name = String(name || "");
    if (name.startsWith("browser_")) return "web";
    for (const k in ACT) if (ACT[k].includes(name)) return k;
    return "tool";
  }

  function mini(id, tx, ty, s) {
    // Маленькая звезда-спутник с центром в (tx,ty). Тот же силуэт и градиент.
    return `<g transform="translate(${tx} ${ty}) scale(${s}) translate(-12 -12)">
      <path d="${STAR}" fill="url(#alti-fill-${id})" stroke="url(#alti-rim-${id})" stroke-width="0.5"/>
      <path d="${STAR}" fill="url(#alti-gloss-${id})"/>
    </g>`;
  }

  function svg(opts) {
    opts = opts || {};
    const id = opts.id || "m" + _seq++;
    const size = opts.size == null ? 96 : opts.size;
    const sizeCss = typeof size === "number" ? size + "px" : size;
    const satellites = opts.satellites !== false;
    const act = opts.act || "";
    // Busy: the face that goes with the work (reading eyes, a squint while writing…).
    const mood = opts.mood || ({ think: "think", agent: "think", read: "read", search: "read", web: "read",
      write: "focus", answer: "focus", shell: "focus", plan: "think", tool: "focus" }[act] || "idle");
    const cls = "alti-mascot alti-" + mood + (act ? " alti-busy alti-act-" + act : "") + (opts.still ? " alti-still" : "") + (opts.class ? " " + opts.class : "");
    const prop = act && PROPS[act] ? PROPS[act] : "";

    // По референсу: крупный спутник справа-сверху, маленький — слева-снизу.
    const sat = satellites
      ? `<g class="alti-sat alti-sat-a">${mini(id, 20.6, 4.4, 0.4)}</g>
         <g class="alti-sat alti-sat-b">${mini(id, 3.6, 19.2, 0.28)}</g>`
      : "";

    return `<svg class="${cls}" viewBox="0 0 24 24" width="${sizeCss}" height="${sizeCss}" role="img" aria-label="Alti" style="overflow:visible">
      <defs>
        <radialGradient id="alti-fill-${id}" cx="0.36" cy="0.30" r="0.85">
          <stop offset="0" stop-color="#FFF6DA"/>
          <stop offset="0.42" stop-color="#FFD37A"/>
          <stop offset="0.80" stop-color="#F1A93C"/>
          <stop offset="1" stop-color="#D6811E"/>
        </radialGradient>
        <linearGradient id="alti-rim-${id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#FFE9A8"/>
          <stop offset="1" stop-color="#C9761A"/>
        </linearGradient>
        <linearGradient id="alti-gloss-${id}" x1="0.3" y1="0" x2="0.5" y2="0.6">
          <stop offset="0" stop-color="#ffffff" stop-opacity="0.55"/>
          <stop offset="0.4" stop-color="#ffffff" stop-opacity="0.05"/>
          <stop offset="1" stop-color="#000000" stop-opacity="0.12"/>
        </linearGradient>
        <filter id="alti-glow-${id}" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="0.7" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <ellipse class="alti-shadow" cx="12" cy="22.4" rx="6.2" ry="1.15" fill="#000" opacity="0.28"/>
      ${sat}
      <g class="alti-core" filter="url(#alti-glow-${id})" style="transform-origin:12px 12px">
        <path d="${STAR}" fill="url(#alti-fill-${id})" stroke="url(#alti-rim-${id})" stroke-width="0.5"/>
        <path d="${STAR}" fill="url(#alti-gloss-${id})"/>
        <ellipse cx="9.6" cy="8.7" rx="2.7" ry="1.7" fill="#fff" opacity="0.35" transform="rotate(-24 9.6 8.7)"/>
        <g class="alti-gaze"><g class="alti-eyes">${eyes(mood)}</g></g>
      </g>
      ${mood === "sleep" ? `<text class="alti-z" x="17.6" y="6.6" font-size="4.2" font-weight="700" fill="currentColor">z</text>` : ""}
      ${prop}
    </svg>`;
  }

  // ---------------------------------------------------------------- reactions and small lives
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const FX = ["hop", "twirl", "wink", "giggle", "sparkle"];
  const IDLE = ["look", "blink2", "twirl", "sparkle", "stretch"];
  const PARTICLES = { hop: ["♥", "♡", "♥"], sparkle: ["✦", "✧", "✦", "✧", "✦"], twirl: ["✧", "✦"], wink: ["✦"], giggle: ["♪", "✧"] };

  // Little hearts and stars fly out of Alti: a layer over the page, removed when they land.
  function burst(svgEl, kind) {
    const glyphs = PARTICLES[kind] || ["✦"];
    const r = svgEl.getBoundingClientRect();
    if (!r.width) return;
    let layer = document.getElementById("alti-particles");
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "alti-particles";
      document.body.appendChild(layer);
    }
    glyphs.forEach((g, i) => {
      const p = document.createElement("span");
      p.className = "alti-particle" + (g === "♥" || g === "♡" ? " heart" : "");
      p.textContent = g;
      const angle = (-90 + (i - (glyphs.length - 1) / 2) * 34 + (Math.random() * 16 - 8)) * Math.PI / 180;
      const dist = r.width * (0.55 + Math.random() * 0.35) + 12;
      p.style.left = r.left + r.width / 2 + "px";
      p.style.top = r.top + r.height * 0.35 + "px";
      p.style.setProperty("--dx", Math.cos(angle) * dist + "px");
      p.style.setProperty("--dy", Math.sin(angle) * dist + "px");
      p.style.setProperty("--s", String(Math.max(0.6, Math.min(1.6, r.width / 60))));
      p.style.animationDelay = i * 45 + "ms";
      layer.appendChild(p);
      setTimeout(() => p.remove(), 1100 + i * 45);
    });
  }

  // One short reaction (a click, or a moment of idle life): a class that plays once.
  function react(svgEl, kind) {
    if (!svgEl || reduced()) return;
    kind = kind || FX[Math.floor(Math.random() * FX.length)];
    [...FX, ...IDLE].forEach((k) => svgEl.classList.remove("alti-fx-" + k));
    void svgEl.getBoundingClientRect();           // restart the animation if it is the same one
    svgEl.classList.add("alti-fx-" + kind);
    if (PARTICLES[kind] && !svgEl.closest(".brand-mark")) burst(svgEl, kind);
    else if (kind === "hop" || kind === "sparkle") burst(svgEl, kind);
    clearTimeout(svgEl._fxTimer);
    svgEl._fxTimer = setTimeout(() => svgEl.classList.remove("alti-fx-" + kind), 1200);
    return kind;
  }

  // Alti in a place: clicking it gets a reaction (and the keyboard too, for the ones in focus).
  function clickable(holder, label) {
    if (!holder || holder._altiClick) return;
    holder._altiClick = true;
    holder.classList.add("alti-clickable");
    holder.setAttribute("role", "button");
    holder.setAttribute("tabindex", "0");
    if (label) holder.setAttribute("aria-label", label);
    const go = (e) => {
      e.preventDefault(); e.stopPropagation();
      const svgEl = holder.querySelector(".alti-mascot");
      // Clicked again before the last one ended: it giggles instead of repeating itself.
      const busy = svgEl && [...svgEl.classList].some((c) => c.startsWith("alti-fx-"));
      react(svgEl, busy ? "giggle" : undefined);
    };
    holder.addEventListener("click", go);
    holder.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") go(e); });
    // The window can be dragged by its title: a press on Alti must stay a click.
    holder.addEventListener("mousedown", (e) => e.stopPropagation());
  }

  // Now and then Alti does something on its own (looks around, blinks twice, twirls): only while
  // the page is visible and the mascot is on screen, never with reduced motion.
  function idle(holder, minMs = 7000, maxMs = 15000) {
    if (!holder || holder._altiIdle) return;
    holder._altiIdle = true;
    const tick = () => {
      if (!holder.isConnected) return;
      const svgEl = holder.querySelector(".alti-mascot");
      if (svgEl && document.visibilityState === "visible" && !reduced() && svgEl.getBoundingClientRect().width) {
        const busy = [...svgEl.classList].some((c) => c.startsWith("alti-fx-"));
        if (!busy) react(svgEl, IDLE[Math.floor(Math.random() * IDLE.length)]);
      }
      holder._altiIdleTimer = setTimeout(tick, minMs + Math.random() * (maxMs - minMs));
    };
    holder._altiIdleTimer = setTimeout(tick, minMs + Math.random() * (maxMs - minMs));
  }

  // The eyes follow the pointer (the welcome Alti): a small shift of the gaze group, at most a
  // quarter of an eye, so the face never leaves its place.
  function follow(holder) {
    if (!holder || holder._altiFollow || reduced()) return;
    holder._altiFollow = true;
    let raf = 0, lastX = 0, lastY = 0;
    const move = (e) => {
      lastX = e.clientX; lastY = e.clientY;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!holder.isConnected) { window.removeEventListener("pointermove", move); return; }
        const gaze = holder.querySelector(".alti-gaze");
        const r = holder.getBoundingClientRect();
        if (!gaze || !r.width) return;
        const dx = lastX - (r.left + r.width / 2), dy = lastY - (r.top + r.height / 2);
        const d = Math.hypot(dx, dy) || 1;
        const k = Math.min(1, d / 260);
        gaze.setAttribute("transform", `translate(${((dx / d) * 0.9 * k).toFixed(2)} ${((dy / d) * 0.7 * k).toFixed(2)})`);
      });
    };
    window.addEventListener("pointermove", move, { passive: true });
  }

  window.Mascot = { svg, act, react, clickable, idle, follow };
})();
