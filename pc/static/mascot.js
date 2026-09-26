"use strict";
/* Altair — маскот «Звёздыш» (Alti). Пухлая 4-конечная звезда с лицом.
   Чистый inline-SVG: объёмный радиальный градиент (свет слева-сверху),
   глянцевый блик, rim-контур, мягкая тень-контакт, glow — по спеке бренда
   (docs/PLAN_BRAND_UI_MOTION.md, раздел «Маскот»). Без сборки и зависимостей.

   window.Mascot.svg({ size, satellites, mood, id }) -> строка SVG.
     size       — пиксели (число или CSS-строка), по умолчанию 96.
     satellites — рисовать 2 звезды-спутника (ПК/телефон/сервер), по умолчанию true.
     mood       — "idle" | "think" | "happy" | "help" (влияет на глаза/наклон).
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
    if (mood === "help") {
      // Тревога — глаза выше + «?».
      return `<rect x="${ex1 - w / 2}" y="${ey - 1.4}" width="${w}" height="${h}" rx="${r}" fill="#241608"/>
              <rect x="${ex2 - w / 2}" y="${ey - 1.4}" width="${w}" height="${h}" rx="${r}" fill="#241608"/>
              <circle cx="${ex1 + 0.4}" cy="${ey - 0.9}" r="0.42" fill="#fff" opacity="0.9"/>`;
    }
    const eh = mood === "think" ? h * 0.7 : h;
    return `<rect x="${ex1 - w / 2}" y="${ey - eh / 2}" width="${w}" height="${eh}" rx="${r}" fill="#241608"/>
            <rect x="${ex2 - w / 2}" y="${ey - eh / 2}" width="${w}" height="${eh}" rx="${r}" fill="#241608"/>
            <circle cx="${ex1 + 0.45}" cy="${ey - eh / 2 + 0.55}" r="0.42" fill="#fff" opacity="0.9"/>
            <circle cx="${ex2 + 0.45}" cy="${ey - eh / 2 + 0.55}" r="0.42" fill="#fff" opacity="0.9"/>`;
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
    const mood = opts.mood || "idle";
    const cls = "alti-mascot alti-" + mood + (opts.still ? " alti-still" : "") + (opts.class ? " " + opts.class : "");

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
        ${eyes(mood)}
      </g>
    </svg>`;
  }

  window.Mascot = { svg };
})();
