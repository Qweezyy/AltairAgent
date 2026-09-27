"use strict";
/* Altair — клиент интерфейса (редизайн). Чистый JS, без сборки.
   Полный порт функционала старого UI на новый дизайн. Контракт /ws + /api. */

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const escAttr = (s) => esc(s).replace(/`/g, "&#96;");
const iconSvg = (n, c = "icon") => `<svg class="${c}"><use href="#i-${n}"/></svg>`;
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }
const humanSize = (n) => { if (!n) return "0 B"; const u = ["B", "KB", "MB", "GB"]; const i = Math.floor(Math.log(n) / Math.log(1024)); return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`; };
const fmtCost = (v) => (v >= 0.01 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`);
const plural = (n, one, few, many) => { const a = n % 10, b = n % 100; if (a === 1 && b !== 11) return one; if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return few; return many; };
const LS = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

// ------------------------------------------------------------------ state
const state = {
  ws: null, connected: false, reconnectTimer: null,
  sessionId: null, running: false,
  workspace: "", autoWorkspace: true, model: "", mode: "manual", modes: [],
  tools: [], webMode: "auto", deepResearch: false, chosenSkills: new Set(), skills: [],
  attachments: [], stickToBottom: true,
  answerEl: null, answerText: "", turnHadInline: false, stepsEl: null, thinkingEl: null, activityEl: null, activityTimer: null,
  steps: new Map(), stepArgs: new Map(), artifacts: new Map(), undoable: new Set(),
  filesNew: new Set(), filesOpen: new Set(), filesFilter: "", routing: false,
  renderPending: false, userTurn: 0, contextTokens: 0, previewMode: "page", previewPath: null,
  commands: [], version: "", updateInfo: null, reconnectEl: null,
};
const els = {};
function cacheEls() {
  Object.assign(els, {
    app: $("#app"), feed: $("#feed"), feedInner: $("#feed-inner"), input: $("#input"),
    composer: $("#composer"), sendBtn: $("#send-btn"), sessions: $("#sessions"),
    chatTitle: $("#chat-title"), chatSub: $("#chat-sub"), wsVal: $("#ws-val"), modeVal: $("#mode-val"),
    ctxRing: $("#ctx-ring"), suggestions: $("#suggestions"), attachPreview: $("#attach-preview"),
    jump: $("#jump-latest"), dock: $("#dock"), chatCol: $("#chat-col"), work: $(".work"), toasts: $("#toasts"), overlayRoot: $("#overlay-root"),
    fileInput: $("#file-input"), composerFlags: $("#composer-flags"),
    modelVal: $("#model-val"), cmdPopup: $("#cmd-popup"),
  });
}

// ------------------------------------------------------------------ websocket
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => { state.connected = true; clearTimeout(state.reconnectTimer); clearReconnect(); };
  ws.onclose = () => { state.connected = false; setRunning(false); state.reconnectTimer = setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch { return; }
    // Любое событие, кроме самой попытки реконнекта, означает, что связь есть —
    // убираем плашку реконнекта, чтобы она не висела навсегда.
    if (m.type !== "reconnecting" && m.type !== "pong") clearReconnect();
    HANDLERS[m.type]?.(m);
  };
}
const send = (o) => { if (state.ws?.readyState === 1) state.ws.send(JSON.stringify(o)); };

// ------------------------------------------------------------------ feed helpers
const append = (n) => { els.feedInner.appendChild(n); bumpStatus(); scrollFeed(); return n; };
const clearWelcome = () => {
  $(".welcome", els.feedInner)?.remove();
  if (!els.app?.classList.contains("welcome-active")) { window.Cosmos?.unmount(); return; }
  // The first message: the sky fades to the plain background instead of vanishing; meanwhile
  // the header and feed stay see-through (.welcome-leaving) so the fade is visible.
  els.app.classList.remove("welcome-active");
  els.app.classList.add("welcome-leaving");
  window.Cosmos ? window.Cosmos.leave(() => els.app.classList.remove("welcome-leaving")) : els.app.classList.remove("welcome-leaving");
};
function scrollFeed(force = false) {
  if (!force && !state.stickToBottom) { els.jump.hidden = false; return; }
  els.feed.scrollTop = els.feed.scrollHeight; els.jump.hidden = true;
}
// Thinking is over (the answer or a tool call began, or the turn ended): the block folds into
// a one-line summary even if it was open, like a finished round of tools.
function finishThinking() {
  const box = state.thinkingEl; state.thinkingEl = null;
  if (!box || box._done) return;
  box._done = true;
  box.classList.remove("open"); $(".thinking-body", box).hidden = true;
  const secs = Math.max(1, Math.round((performance.now() - (box._t0 || performance.now())) / 1000));
  typeText($(".thinking-label", box), T("st.thoughtFor", { s: secs }));
}
function endTurn() { finishRound(); finishThinking(); state.answerEl = null; state.turnHadInline = false; clearActivity(); }
function clearActivity() { statusEnd(); state.reconnectEl = null; }

// -------- живой статус внизу сообщения (кружок + время работы + фаза) --------
function statusStart() {
  if (state.statusEl) return;
  state.runStart = Date.now();
  state.statusMode = "wait"; state.statusModeStart = Date.now();
  state.statusEl = append(el(`<div class="run-status"><span class="rs-dot"></span><span class="rs-phase"></span><span class="rs-time"></span></div>`));
  statusPaint();
  clearInterval(state.statusTimer);
  state.statusTimer = setInterval(statusPaint, 1000);
}
function statusMode(mode) { if (state.statusEl && state.statusMode !== mode) { state.statusMode = mode; state.statusModeStart = Date.now(); statusPaint(); } }
function statusPaint() {
  if (!state.statusEl) return;
  const elapsed = Math.round((Date.now() - state.runStart) / 1000);
  const inMode = (Date.now() - state.statusModeStart) / 1000;
  let phase;
  if (state.statusMode === "think") phase = inMode > 25 ? T("st.thinkAlmost") : inMode > 12 ? T("st.thinkMore") : T("st.thinking");
  else phase = T("st.waiting");
  $(".rs-phase", state.statusEl).textContent = phase;
  $(".rs-time", state.statusEl).textContent = `· ${elapsed} ${T("u.sec")}`;
  bumpStatus();
}
function bumpStatus() { if (state.statusEl && state.statusEl.parentElement === els.feedInner && els.feedInner.lastElementChild !== state.statusEl) els.feedInner.appendChild(state.statusEl); }
function statusEnd() { if (state.statusEl) { state.statusEl.remove(); state.statusEl = null; } clearInterval(state.statusTimer); }
function clearReconnect() { if (state.reconnectEl) { state.reconnectEl.remove(); state.reconnectEl = null; } }

// ------------------------------------------------------------------ markdown
const INLINE_IMG = /!\[([^\]]*)\]\(<img:([^>]+)>\)/g;
function preInlineImg(src) { return src.replace(INLINE_IMG, (_, alt, q) => `<img class="md-inline-img" data-imgq="${escAttr(q)}" alt="${escAttr(alt)}">`); }
function mdParse(src) {
  if (window.marked) { try { return marked.parse(src, { breaks: false, gfm: true }); } catch {} }
  return `<p>${esc(src).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>")}</p>`;
}
function streamHtml(text) { return mdParse(preInlineImg(String(text ?? ""))); }
function renderFinal(container, text, baseDir = "") {
  const store = [];
  let src = preInlineImg(String(text ?? ""));
  if (window.maskMath) src = maskMath(src, store);
  let html = mdParse(src);
  if (window.unmaskMath) html = unmaskMath(html, store);
  container.innerHTML = html;
  postProcess(container, baseDir);
}
// Where a link in an answer or a previewed document points, as a file path: models write
// "dir/file.md", "D:\\x\\y.md", "file:dir/x.py" or "/files/..." alike. Relative paths are
// relative to `baseDir` (the previewed document's folder) or else the workspace. Returns null
// for a link that is not a file (web, mail, #anchor) and "" for one that cannot be opened here
// (sandbox:, javascript: and other schemes).
function fileLinkTarget(href, baseDir = "") {
  let h = String(href || "").trim();
  if (!h || h.startsWith("#") || /^(https?|mailto|tel):/i.test(h)) return null;
  let m;
  if ((m = h.match(/^file:\/*(.+)$/i))) { h = m[1]; baseDir = ""; }
  else if ((m = h.match(/^\/files\/(.+)$/))) h = m[1];
  else if (/^[a-z][a-z0-9+.-]*:/i.test(h) && !/^[a-zA-Z]:[\\/]/.test(h)) return "";
  h = h.split("#")[0].split("?")[0];
  try { h = decodeURIComponent(h); } catch {}
  h = h.replace(/\\/g, "/");
  if (!h) return "";
  if (/^[a-zA-Z]:\//.test(h) || h.startsWith("/")) return h;
  const out = [];
  for (const seg of `${baseDir ? baseDir + "/" : ""}${h}`.split("/")) {
    if (!seg || seg === ".") continue;
    if (seg === "..") out.pop(); else out.push(seg);
  }
  return out.join("/");
}
function openFileTarget(path) {
  if (!path) { toast(T("link.unavailable"), "error"); return; }
  if (/^[a-zA-Z]:\/|^\//.test(path)) {
    const ws = String(state.workspace || "").replace(/\\/g, "/").replace(/\/+$/, "");
    if (ws && path.toLowerCase().startsWith(ws.toLowerCase() + "/")) { selectFileInTree(path.slice(ws.length + 1)); return; }
    openPreviewFile(path);   // outside the workspace: show it without the tree
    return;
  }
  selectFileInTree(path);
}
function postProcess(container, baseDir = "") {
  // код-блоки: подсветка + заголовок с языком и копированием
  $$("pre > code", container).forEach((code) => {
    const pre = code.parentElement;
    if (pre.dataset.done) return; pre.dataset.done = "1";
    let lang = ([...code.classList].find((c) => c.startsWith("language-")) || "").replace("language-", "");
    // Диаграммы Mermaid рисуем как SVG прямо в ответе, а не как код.
    if (lang === "mermaid") { renderMermaidBlock(pre, code.textContent || ""); return; }
    try { if (window.hljs) { hljs.highlightElement(code); lang = lang || (code.result?.language || ""); } } catch {}
    const head = el(`<div class="code-header"><span>${esc(lang || T("code.lang"))}</span><button class="btn-icon small" data-tip="${escAttr(T("a.copy"))}">${iconSvg("copy", "icon icon-sm")}</button></div>`);
    $("button", head).addEventListener("click", () => { navigator.clipboard?.writeText(code.textContent); toast(T("t.copied")); });
    pre.before(head);
  });
  if (window.renderMath) try { renderMath(container); } catch {}
  $$("a[href]", container).forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (/^https?:/.test(href)) { a.target = "_blank"; a.rel = "noopener"; return; }
    // A link to a file opens the Files tab with it selected. Left as a plain link it would
    // take the whole app window to the server's 404 page, with no way back.
    const target = fileLinkTarget(href, baseDir);
    if (target === null) return;
    a.classList.add(target ? "file-link" : "dead-link");
    a.setAttribute("href", "#");
    a.dataset.fileTarget = target;
    if (!target) a.dataset.tip = T("link.unavailable");
    a.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openFileTarget(target); });
  });
  $$(".md-inline-img", container).forEach(async (img) => {
    if (img.dataset.loaded) return; img.dataset.loaded = "1";
    try { const d = await (await fetch(`/api/find-image?q=${encodeURIComponent(img.dataset.imgq)}`)).json(); if (d.ok && d.url) img.src = d.url; else img.remove(); } catch { img.remove(); }
  });
}
// ------------------------------------------------------------------ mermaid
let _mermaidReady = false;
let _mermaidSeq = 0;
// Инициализация Mermaid под текущую тему (тёмная/светлая). Ленивая: строит
// конфиг один раз, дальше только перекрашивает при смене темы.
function ensureMermaid() {
  if (!window.mermaid) return false;
  const dark = (document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const want = dark ? "dark" : "default";
  if (_mermaidReady && _mermaidTheme === want) return true;
  try {
    window.mermaid.initialize({
      startOnLoad: false,
      theme: want,
      securityLevel: "strict", // без произвольных ссылок/скриптов из диаграммы
      fontFamily: "inherit",
      themeVariables: { fontSize: "14px" },
    });
    _mermaidReady = true; _mermaidTheme = want;
    return true;
  } catch (e) { console.error("mermaid init", e); return false; }
}
let _mermaidTheme = "";
// Рисует один блок ```mermaid``` как SVG на месте <pre>. При ошибке синтаксиса
// возвращает исходный код (не роняем ответ), с пометкой.
async function renderMermaidBlock(pre, code) {
  const fig = el(`<figure class="chat-figure mermaid-fig"></figure>`);
  pre.replaceWith(fig);
  const src = String(code || "").trim();
  const fail = (msg) => {
    fig.innerHTML = "";
    const p = el(`<pre class="mermaid-error"><code></code></pre>`);
    $("code", p).textContent = src;
    fig.appendChild(el(`<div class="mermaid-errmsg">${esc(T("mermaid.error"))}${msg ? ": " + esc(String(msg).slice(0, 160)) : ""}</div>`));
    fig.appendChild(p);
  };
  // Mermaid грузится с defer — если ещё не готов, ждём чуть-чуть.
  if (!window.mermaid) { for (let i = 0; i < 40 && !window.mermaid; i++) await new Promise((r) => setTimeout(r, 75)); }
  if (!ensureMermaid()) return fail("Mermaid недоступен");
  try {
    const id = "mmd" + ++_mermaidSeq;
    const { svg } = await window.mermaid.render(id, src);
    fig.innerHTML = svg;
    fig.dataset.src = src; // для перерисовки при смене темы
  } catch (e) {
    console.error("mermaid render", e);
    // Mermaid при ошибке вставляет свой div с id в <body> — уберём мусор.
    try { document.getElementById("dmmd" + _mermaidSeq)?.remove(); } catch {}
    fail(e && e.message);
  }
}
// Перерисовать все диаграммы при смене темы приложения.
function restyleMermaid() {
  if (!window.mermaid) return;
  _mermaidReady = false; // заставит ensureMermaid переинициализироваться под новую тему
  $$(".mermaid-fig[data-src]").forEach(async (fig) => {
    try {
      if (!ensureMermaid()) return;
      const id = "mmd" + ++_mermaidSeq;
      const { svg } = await window.mermaid.render(id, fig.dataset.src);
      fig.innerHTML = svg;
    } catch (e) { console.error("mermaid restyle", e); }
  });
}
// Делит стример на «завершённый» префикс (полные блоки) и «активный» хвост.
// Незакрытый ```-блок целиком уходит в хвост, чтобы не ломать вёрстку.
function computeStreamParts(text) {
  const lines = text.split("\n");
  let inCode = false, fencePos = -1, pos = 0;
  for (const line of lines) {
    if (/^\s*```/.test(line)) { if (!inCode) { inCode = true; fencePos = pos; } else { inCode = false; fencePos = -1; } }
    pos += line.length + 1;
  }
  if (inCode && fencePos >= 0) return { committedEnd: fencePos, activeCode: true, active: text.slice(fencePos) };
  const lastBreak = text.lastIndexOf("\n\n");
  const committedEnd = lastBreak >= 0 ? lastBreak + 2 : 0;
  return { committedEnd, activeCode: false, active: text.slice(committedEnd) };
}
let _lastRender = 0;
function scheduleAnswerRender() {
  if (state.renderPending) return;
  state.renderPending = true;
  // Мемоизация: завершённые блоки парсятся marked ОДИН раз (инкрементально,
  // только новый кусок), заново на каждый токен разбирается лишь короткий хвост.
  const run = () => {
    state.renderPending = false; _lastRender = performance.now();
    if (!state.answerEl) return;
    const body = $(".answer-body", state.answerEl);
    const text = state.answerText;
    const md = (state.md = state.md || { end: 0, html: "" });
    const { committedEnd, activeCode, active } = computeStreamParts(text);
    if (committedEnd > md.end) { md.html += mdParse(preInlineImg(text.slice(md.end, committedEnd))); md.end = committedEnd; }
    // Хвост: незакрытый код-блок — простым <pre> без подсветки, иначе обычный Markdown.
    const activeHtml = activeCode
      ? `<pre class="code-stream"><code>${esc(active.replace(/^\s*```[^\n]*\n?/, ""))}</code></pre>`
      : mdParse(preInlineImg(active));
    body.innerHTML = md.html + activeHtml;
    scrollFeed();
  };
  const wait = Math.max(0, 66 - (performance.now() - _lastRender));
  requestAnimationFrame(() => (wait > 8 ? setTimeout(run, wait) : run()));
}

// ------------------------------------------------------------------ блоки ленты
function userBlock(text, attachments, turn) {
  const hasAtt = attachments && attachments.length;
  const node = el(`<div class="msg-user" data-turn="${turn}"><div class="mu-bubble"><div class="mu-text">${esc(text)}</div>${hasAtt ? `<div class="attach-list"></div>` : ""}</div><div class="msg-actions msg-actions-user"><button class="btn-icon small" data-act="copy" data-tip="${escAttr(T("a.copy"))}">${iconSvg("copy", "icon icon-sm")}</button><button class="btn-icon small" data-act="return" data-tip="${escAttr(T("a.returnHere"))}">${iconSvg("undo", "icon icon-sm")}</button><button class="btn-icon small" data-act="fork" data-tip="${escAttr(T("a.forkHere"))}">${iconSvg("branch", "icon icon-sm")}</button></div></div>`);
  if (hasAtt) { const list = $(".attach-list", node); attachments.forEach((a) => list.appendChild(attachCard(a, false))); }
  $('[data-act="copy"]', node).addEventListener("click", () => { navigator.clipboard?.writeText(text); toast(T("t.copied")); });
  $('[data-act="return"]', node).addEventListener("click", async () => { if (await confirmDialog({ message: T("cf.returnMsg"), danger: true, confirmText: T("a.returnHere") })) rewindTo(turn, text); });
  $('[data-act="fork"]', node).addEventListener("click", () => forkFrom(turn));
  return node;
}
function ensureAnswer() {
  if (!state.answerEl) {
    state.answerEl = append(el(`<div class="msg-agent"><div class="answer-body md"></div></div>`));
    state.answerText = ""; finishThinking(); state.md = { end: 0, html: "" };
  }
  return state.answerEl;
}

// --------- раунд вызовов инструментов (печатающаяся строка → раскрываемая таблица)
// Печать по букве. Токен на элементе позволяет отменить прошлую печать.
function typeText(el, text) {
  text = text || "";
  const token = (el._typeTok = (el._typeTok || 0) + 1);
  el.textContent = "";
  let i = 0;
  const total = text.length;
  const step = () => {
    if (el._typeTok !== token) return;           // началась новая печать — стоп
    // Печатаем «пачками», чтобы длинная строка не тянулась вечно (~0.6с максимум).
    const chunk = Math.max(1, Math.ceil(total / 50));
    i = Math.min(total, i + chunk);
    el.textContent = text.slice(0, i);
    if (i < total) setTimeout(step, 14);
    else scrollFeed();
  };
  step();
}

function startRound() {
  const node = append(el(`<div class="tools" data-open="0"><button class="tools-head" type="button"><span class="tr-caret">${iconSvg("chevron-right", "icon icon-sm")}</span><span class="tools-title"></span></button><div class="tools-body" hidden></div></div>`));
  node._tools = [];                               // {label} по каждому инструменту раунда
  node._rows = new Map();                          // call_id -> строка
  node._done = false;
  $(".tools-head", node).addEventListener("click", () => {
    const open = node.dataset.open === "1"; node.dataset.open = open ? "0" : "1";
    $(".tools-body", node).hidden = open;
  });
  state.round = node;
  return node;
}
function diffHtml(name, args) {
  const d = diffCounts(name, args);
  return d ? `<span class="tr-diff"><span class="add">+${d.add}</span> <span class="del">−${d.del}</span></span>` : "";
}
function roundAddTool(call_id, name, args) {
  const round = state.round || startRound();
  const L = stepLabel(name, args, "run");
  const row = el(`<div class="tool-row" data-open="0"><button class="tool-row-head" type="button"><span class="tr-caret">${iconSvg("chevron-right", "icon icon-sm")}</span><span class="tr-ico">${iconSvg(L.icon, "icon icon-sm")}</span><span class="tr-label">${L.label}</span>${diffHtml(name, args)}<span class="tr-status"><span class="spin">${iconSvg("refresh", "icon icon-sm")}</span></span></button><div class="tool-row-out" hidden></div></div>`);
  $(".tools-body", round).appendChild(row);
  round._rows.set(call_id, row);
  row._summary = { label: L.label };
  round._tools.push(row._summary);
  // Живой счётчик времени выполнения инструмента: видно, что долгий инструмент
  // (deep_research и т.п.) реально работает, а не завис (#4).
  row._t0 = Date.now();
  row._timer = setInterval(() => {
    const s = Math.round((Date.now() - row._t0) / 1000);
    if (s < 2) return;
    let e = $(".tr-elapsed", row);
    if (!e) { e = document.createElement("span"); e.className = "tr-elapsed"; $(".tr-status", row).before(e); }
    e.textContent = s + T("u.sec");
  }, 1000);
  // Печатающаяся строка-заголовок = текущий инструмент.
  typeText($(".tools-title", round), L.label);
  scrollFeed();
}
function roundFinishTool(call_id, name, args, ok, output) {
  const round = state.round; if (!round) return;
  const row = round._rows.get(call_id); if (!row) return;
  if (row._timer) { clearInterval(row._timer); row._timer = null; }
  $(".tr-elapsed", row)?.remove();
  if ((name || "").includes("research")) { state.researchEl?.remove(); state.researchEl = null; }
  row.classList.add(ok ? "ok" : "fail");
  const L = stepLabel(name, args, ok ? "ok" : "fail");
  if (row._summary) row._summary.label = L.label;
  // Обновляем метку и дифф (аргументы приходят полными только сейчас).
  $(".tr-label", row).innerHTML = L.label;
  const head = $(".tool-row-head", row);
  $(".tr-diff", row)?.remove();
  const dh = diffHtml(name, args);
  if (dh) $(".tr-status", row).insertAdjacentHTML("beforebegin", dh);
  $(".tr-status", row).innerHTML = ok ? iconSvg("check", "icon icon-sm") : iconSvg("x", "icon icon-sm");
  row._out = { name, args, output: output || "" };
  const outBox = $(".tool-row-out", row);
  const bind = () => {
    if (row._built) return; row._built = true;
    outBox.innerHTML = toolOutputHtml(name, args, output || "");
  };
  head.addEventListener("click", () => {
    const open = row.dataset.open === "1"; row.dataset.open = open ? "0" : "1";
    if (!open) bind();
    outBox.hidden = open;
  });
}
function finishRound() {
  const round = state.round; if (!round || round._done) { state.round = null; return; }
  round._done = true;
  // Итоговая строка: перечисление того, что сделано (без мс, без вывода).
  const labels = round._tools.map((t) => t.label.replace(/<[^>]+>/g, "")).filter(Boolean);
  const summary = labels.join(" · ");
  typeText($(".tools-title", round), summary || T("tool.actions"));
  // Folds into its summary line even if it was opened while running (a click opens it again).
  round.dataset.open = "0"; $(".tools-body", round).hidden = true;
  state.round = null;
}
// Построение раунда из истории (шаги уже завершены).
function startHistoryRound() {
  const node = el(`<div class="tools" data-open="0"><button class="tools-head" type="button"><span class="tr-caret">${iconSvg("chevron-right", "icon icon-sm")}</span><span class="tools-title"></span></button><div class="tools-body" hidden></div></div>`);
  node._labels = [];
  $(".tools-head", node).addEventListener("click", () => { const open = node.dataset.open === "1"; node.dataset.open = open ? "0" : "1"; $(".tools-body", node).hidden = open; });
  return node;
}
function historyAddRow(round, e) {
  const ok = e.ok !== false; const L = stepLabel(e.name, e.args, ok ? "ok" : "fail");
  const row = el(`<div class="tool-row ${ok ? "ok" : "fail"}" data-open="0"><button class="tool-row-head" type="button"><span class="tr-caret">${iconSvg("chevron-right", "icon icon-sm")}</span><span class="tr-ico">${iconSvg(L.icon, "icon icon-sm")}</span><span class="tr-label">${L.label}</span>${diffHtml(e.name, e.args)}<span class="tr-status">${ok ? iconSvg("check", "icon icon-sm") : iconSvg("x", "icon icon-sm")}</span></button><div class="tool-row-out" hidden></div></div>`);
  const outBox = $(".tool-row-out", row); let built = false;
  $(".tool-row-head", row).addEventListener("click", () => { const open = row.dataset.open === "1"; row.dataset.open = open ? "0" : "1"; if (!open && !built) { built = true; outBox.innerHTML = toolOutputHtml(e.name, e.args, e.output || ""); } outBox.hidden = open; });
  $(".tools-body", round).appendChild(row);
  round._labels.push(L.label.replace(/<[^>]+>/g, ""));
}
function finalizeHistoryRound(round) { if (round) $(".tools-title", round).textContent = round._labels.join(" · ") || T("tool.actions"); }

// Тело вывода конкретного инструмента (команда/запрос сверху, затем результат).
function toolOutputHtml(name, args, output) {
  args = args || {};
  let head = "";
  if (name === "execute_command" || name === "run_background") head = args.command ? `<div class="out-cmd mono">$ ${esc(String(args.command))}</div>` : "";
  else if (name === "web_search" || name === "deep_research") head = args.query ? `<div class="out-cmd">${esc(String(args.query))}</div>` : "";
  else if (args.url) head = `<div class="out-cmd mono">${esc(String(args.url))}</div>`;
  else if (args.path) head = `<div class="out-cmd mono">${esc(String(args.path))}</div>`;
  const body = output ? `<pre class="out-body">${esc(String(output))}</pre>` : `<div class="out-empty dim">${esc(T("tool.noOutput"))}</div>`;
  return head + body + toolShotsHtml(output);
}
// Если в выводе инструмента есть скриншот (путь к картинке) — показываем его
// прямо в результате, а не только в «Превью».
function toolShotsHtml(output) {
  const seen = new Set();
  const imgs = [];
  const re = /([\w./\\-]+\.(?:png|jpe?g|gif|webp))/gi;
  let m;
  while ((m = re.exec(String(output || ""))) && imgs.length < 4) {
    const p = m[1].replace(/^["'(]+|["')]+$/g, "");
    if (seen.has(p)) continue; seen.add(p);
    imgs.push(`<img class="out-shot" src="/files/${absPath(p)}" alt="" loading="lazy" />`);
  }
  return imgs.length ? `<div class="out-shots">${imgs.join("")}</div>` : "";
}
const fileOf = (p) => (p || "").split(/[\\/]/).filter(Boolean).slice(-1)[0] || p || "";
function diffCounts(name, args) {
  if (!args) return null;
  try {
    if (name === "edit_file" && args.old_string != null) return { add: String(args.new_string || "").split("\n").length, del: String(args.old_string || "").split("\n").length };
    if (name === "write_file" && args.content != null) return { add: String(args.content).split("\n").length, del: 0 };
    if (name === "apply_patch") { const p = String(args.patch || args.input || args.diff || ""); let add = 0, del = 0; for (const l of p.split("\n")) { if (l.startsWith("+") && !l.startsWith("+++")) add++; else if (l.startsWith("-") && !l.startsWith("---")) del++; } return add || del ? { add, del } : null; }
  } catch {}
  return null;
}
// Human label for a tool call (as in Claude Code) + icon + extra info. `phase` keeps it honest:
// "run" — started or still waiting for approval ("Creating hello.txt"), "ok" — done
// ("Created hello.txt"), "fail" — denied or failed ("Did not create hello.txt").
function stepLabel(name, args, phase = "ok") {
  args = args || {};
  const q = (s) => `<span class="step-quote">«${esc(String(s).slice(0, 90))}»</span>`;
  const diff = diffCounts(name, args);
  const dh = diff ? ` <span class="step-diff"><span class="add">+${diff.add}</span> <span class="del">-${diff.del}</span></span>` : "";
  const P = phase === "run" ? ".run" : phase === "fail" ? ".fail" : "";
  const t = (key, params) => T(key + P, params);
  switch (name) {
    case "execute_command": case "run_background": return { icon: "terminal", label: t("tool.cmd"), extra: args.command ? `<span class="mono step-quote">${esc(String(args.command).slice(0, 80))}</span>` : "" };
    case "write_file": return { icon: "doc", label: t("tool.wrote", { f: esc(fileOf(args.path)) }), extra: dh };
    case "edit_file": case "apply_patch": return { icon: "doc", label: t("tool.edited", { f: esc(fileOf(args.path) || T("tool.filesFallback")) }), extra: dh };
    case "read_file": case "read_document": return { icon: "eye", label: t("tool.read", { f: esc(fileOf(args.path)) }), extra: "" };
    case "list_directory": return { icon: "folder", label: t("tool.list", { f: esc(fileOf(args.path) || ".") }), extra: "" };
    case "web_search": return { icon: "globe", label: t("tool.websearch"), extra: args.query ? q(args.query) : "" };
    case "fetch_url": case "browse_page": return { icon: "globe", label: t("tool.openPage"), extra: args.url ? `<span class="step-quote">${esc(String(args.url).slice(0, 70))}</span>` : "" };
    case "deep_research": return { icon: "flask", label: t("tool.research"), extra: args.query ? q(args.query) : "" };
    case "grep_search": case "find_files": case "ast_search": return { icon: "search", label: t("tool.codeSearch"), extra: (args.pattern || args.query) ? q(args.pattern || args.query) : "" };
    case "run_tests": return { icon: "check", label: t("tool.runTests"), extra: "" };
    case "delete_path": return { icon: "trash", label: t("tool.deleted", { f: esc(fileOf(args.path)) }), extra: "" };
    case "create_chart": return { icon: "doc", label: t("tool.chart", { f: esc(fileOf(args.path) || args.title || "") }), extra: "" };
    case "tool_search": return { icon: "search", label: t("tool.toolSearch"), extra: "" };
    default: {
      const a = Object.entries(args).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" ").slice(0, 90);
      return { icon: "wrench", label: esc(name), extra: a ? `<span class="step-arg">${esc(a)}</span>` : "" };
    }
  }
}

// ---------------------------------------------- инлайн-медиа и генеративный UI
// Единый рендер вложения в ленте: фото/видео/аудио проигрываются прямо в чате,
// прочее — чип со скачиванием. Используется и вживую, и при перезагрузке истории.
function mediaFigure(m) {
  const url = `/files/${absPath(m.path)}`;
  const cap = m.caption ? `<figcaption>${esc(m.caption)}</figcaption>` : "";
  const kind = m.kind || m.media_kind || "file";
  const fig = el(`<figure class="chat-figure"></figure>`);
  if (kind === "image") {
    const img = el(`<img src="${esc(url)}" loading="lazy" alt="${esc(m.name || m.path)}" />`);
    img.addEventListener("click", () => openPreviewFile(m.path));
    fig.appendChild(img);
  } else if (kind === "video") {
    fig.appendChild(el(`<video class="chat-media" src="${esc(url)}" controls playsinline preload="metadata"></video>`));
  } else if (kind === "audio") {
    fig.appendChild(el(`<audio class="chat-media" src="${esc(url)}" controls preload="metadata"></audio>`));
  } else {
    fig.appendChild(el(`<a class="attach-chip" href="${esc(url)}" download target="_blank" rel="noopener">${iconSvg("download")}<span><span class="attach-name">${esc(m.name || m.path)}</span> <span class="attach-meta">${esc(kind)}${m.size_bytes ? " · " + humanSize(m.size_bytes) : ""}</span></span></a>`));
  }
  if (cap) fig.appendChild(el(cap));
  return fig;
}
// Мост «виджет → приложение»: авто-высота (без внутренней прокрутки) и обратный
// вызов sendPrompt(text) — интерактивный виджет может отправить сообщение агенту.
const WIDGET_BRIDGE =
  '<script>(function(){' +
  // Height: pure and frequent (auto-fit, no internal scroll).
  'function H(){try{var d=document.documentElement,b=document.body,' +
  'y=Math.max(d.scrollHeight,b?b.scrollHeight:0,d.offsetHeight);' +
  'parent.postMessage({__widget:1,type:"height",h:y},"*")}catch(e){}}' +
  // Width: intrinsic content width via max-content. One-shot (not in the height
  // loop) so its brief layout mutation never destabilizes the height reading.
  'function W(){try{var b=document.body;if(!b)return;var pw=b.style.width,pd=b.style.display;' +
  'b.style.display="inline-block";b.style.width="max-content";var w=b.scrollWidth;' +
  'b.style.width=pw;b.style.display=pd;if(w>0)parent.postMessage({__widget:1,type:"width",w:w},"*");}catch(e){}}' +
  'window.sendPrompt=function(t){parent.postMessage({__widget:1,type:"prompt",text:String(t==null?"":t)},"*")};' +
  'window.addEventListener("load",function(){H();W()});window.addEventListener("resize",H);' +
  'try{new ResizeObserver(H).observe(document.documentElement)}catch(e){}' +
  'setTimeout(H,60);setTimeout(function(){H();W()},400);setTimeout(function(){H();W()},900);setInterval(H,1500)})();<\/script>';
// Токены темы приложения → в виджет, чтобы генеративный UI выглядел «родным»
// (те же фон/поверхности/акцент/радиусы), а не белым прямоугольником. Песочница
// не видит родителя, поэтому значения прокидываем стилем в srcdoc.
const _THEME_MAP = {
  "--app-bg": "--bg", "--app-surface": "--surface-1", "--app-surface2": "--surface-2",
  "--app-elevated": "--elevated", "--app-border": "--border", "--app-border-strong": "--border-strong",
  "--app-text": "--text-1", "--app-dim": "--text-2", "--app-faint": "--text-3",
  "--app-accent": "--accent", "--app-accent-contrast": "--accent-contrast",
  "--app-danger": "--danger", "--app-success": "--success", "--app-radius": "--r-md",
  "--app-radius-sm": "--r-sm", "--app-shadow": "--shadow-2", "--app-shadow-sm": "--shadow-1",
};
function widgetThemeStyle() {
  let cs;
  try { cs = getComputedStyle(document.documentElement); } catch { return ""; }
  const decls = Object.entries(_THEME_MAP)
    .map(([out, src]) => { const v = cs.getPropertyValue(src).trim(); return v ? `${out}:${v}` : ""; })
    .filter(Boolean).join(";");
  let scheme = document.documentElement.getAttribute("data-theme");
  if (scheme !== "dark" && scheme !== "light") {
    scheme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return decls ? `<style id="app-theme">:root{${decls};color-scheme:${scheme}}</style>` : "";
}
let _widgetSeq = 0;
function widgetFigure(html, kind, caption) {
  const id = "w" + ++_widgetSeq;
  const f = el(`<iframe class="widget-frame ${kind === "graphic" ? "graphic" : ""}" data-widget="${id}" sandbox="allow-scripts allow-forms allow-popups" loading="lazy"></iframe>`);
  // Медиа внутри виджета: агент ссылается на файл рабочей папки как /files/<путь>,
  // здесь путь достраивается до абсолютного (как в остальной ленте).
  const body = String(html || "").replace(
    /(src|href)=(["'])\/files\/([^"']+)\2/gi,
    (_m, attr, q, p) => `${attr}=${q}/files/${absPath(p)}${q}`,
  );
  f.srcdoc = widgetThemeStyle() + WIDGET_BRIDGE + body;
  const fig = el(`<figure class="chat-figure widget-fig"></figure>`);
  fig.appendChild(f);
  if (caption) fig.appendChild(el(`<figcaption>${esc(caption)}</figcaption>`));
  return fig;
}
// Виджет попросил действие: подстроить высоту или отправить сообщение агенту.
window.addEventListener("message", (ev) => {
  const d = ev.data;
  if (!d || d.__widget !== 1) return;
  if (d.type === "height" && d.h > 0) {
    const frame = $$(".widget-frame").find((f) => f.contentWindow === ev.source);
    if (frame) frame.style.height = Math.min(d.h + 2, 2400) + "px";
  } else if (d.type === "width" && d.w > 0) {
    // Ширина по контенту: узкий контент (карточка/SVG) — узкий виджет, широкий
    // (дашборд/таблица) — до 96cqw ленты. Не для simple-графики (у неё своя ширина).
    const frame = $$(".widget-frame").find((f) => f.contentWindow === ev.source);
    const fig = frame && frame.closest(".widget-fig");
    if (fig && !frame.classList.contains("graphic")) {
      fig.style.width = `min(96cqw, ${Math.max(280, Math.round(d.w + 4))}px)`;
    }
  } else if (d.type === "prompt" && typeof d.text === "string" && d.text.trim()) {
    widgetSendPrompt(d.text.trim());
  }
});
// Отправка сообщения агенту из интерактивного виджета: если агент занят — как
// уточнение по ходу (steering), иначе — новый запуск с блоком пользователя в ленте.
function widgetSendPrompt(text) {
  clearWelcome();
  // Пока агент занят — сервер сам примет это как уточнение по ходу (steering.queued).
  if (!state.running) { endTurn(); els.feedInner.appendChild(userBlock(text, [], state.userTurn++)); scrollFeed(true); updateWorkspaceLock(); }
  send({ type: "run", task: text, model: state.model || undefined, workspace: state.workspace, options: runOptions() });
}

// ------------------------------------------------------------------ HANDLERS
const HANDLERS = {
  ready(m) {
    // Восстановление чата: сервер на каждом подключении даёт НОВУЮ пустую сессию.
    // При реконнекте (обрыв сети) возвращаемся в текущий чат, при первом запуске —
    // в последний открытый (из localStorage). Так работа переживает перезапуск.
    const prevId = state.sessionId;
    const prevHadContent = state.currentHasContent;
    state.sessionId = m.session_id; state.tools = m.tools || []; state.modes = m.modes || [];
    state.model = m.model || ""; state.mode = m.approval_mode || "manual"; state.version = m.version || "";
    setModel(state.model);
    setWorkspace(m.workspace); updateMode(state.mode);
    let restoreId = "";
    if (prevId && prevHadContent) restoreId = prevId;                 // реконнект — не терять чат
    else if (!prevId) { try { restoreId = LS.get("last_session_id", ""); } catch {} }  // старт — последний чат
    if (restoreId && restoreId !== m.session_id) { send({ type: "load_session", session_id: restoreId }); }
    else { loadSession(m.session); }
    if ((m.warnings || []).some((w) => /LLM_API_KEY/i.test(w))) toast(T("ev.noApiKey"), "error");
    refreshSessions(); syncProviders().then(loadModels); checkUpdate();
    // The server's own texts (approvals, the run log, errors) follow the UI language.
    send({ type: "ui_lang", lang: window.I18N?.lang?.() || "en" });
    window.BrowserPanel?.onReady();
  },
  "context.usage": (m) => { state.contextExact = !!m.exact; updateRing(m.tokens); },
  browser_state: (m) => window.BrowserPanel?.handle(m),
  browser_frame: (m) => window.BrowserPanel?.handle(m),
  browser_host_cmd: (m) => window.BrowserPanel?.handle(m),
  browser_dialog: (m) => window.BrowserPanel?.handle(m),
  browser_downloads: (m) => window.BrowserPanel?.handle(m),
  browser_agent_active: (m) => window.BrowserPanel?.handle(m),
  "browser.handoff": (m) => showHandoff(m),
  state(m) { setRunning(m.state === "running" || m.state === "waiting_approval"); setStatus(m.state); },
  "session.loaded"(m) { setWorkspace(m.workspace); if (m.mode) updateMode(m.mode); state.autoWorkspace = !!m.auto_workspace; loadSession(m.session); if (state.jumpTo) setTimeout(jumpToMatch, 60); else refreshSessions(); },
  "session.title"(m) { applySessionTitle(m); },
  "workspace.updated"(m) { setWorkspace(m.workspace); toast(T("ev.wsUpdated")); },
  "workspace.error"(m) { toast(m.message, "error"); },
  "mode.updated"(m) { updateMode(m.mode); },
  "run.started"() { clearWelcome(); endTurn(); setRunning(true); statusStart(); state.browserAutoOpened = false; },
  "step.started"() {},
  "tool.pending"() {},
  "reasoning.delta"(m) {
    if (!state.thinkingEl) {
      const box = append(el(`<div class="thinking"><button class="thinking-row" type="button"><span class="thinking-caret">${iconSvg("chevron-right", "icon icon-sm")}</span><span class="thinking-label">${esc(T("st.thinkingDots"))}</span></button><div class="thinking-body" hidden></div></div>`));
      // The block itself, not state.thinkingEl: that one is cleared once thinking ends, and
      // a block left open could then never be closed again.
      $(".thinking-row", box).addEventListener("click", () => { box.classList.toggle("open"); $(".thinking-body", box).hidden = !box.classList.contains("open"); });
      box._t = ""; box._t0 = performance.now();
      state.thinkingEl = box;
    }
    state.thinkingEl._t += m.text; $(".thinking-body", state.thinkingEl).textContent = state.thinkingEl._t; scrollFeed();
    statusMode("think");
  },
  "tool.started"(m) {
    finishThinking();
    state.answerEl = null;                          // после раунда инструментов — новый ответ
    state.stepArgs.set(m.call_id, m.args);
    roundAddTool(m.call_id, m.name, m.args);
    // Агент пошёл в браузер — показываем это пользователю в реальном времени (#3).
    if (String(m.name || "").startsWith("browser_") && m.name !== "browser_downloads") autoOpenBrowser();
    statusMode("wait");
  },
  "tool.finished"(m) {
    // Событие finished не содержит args — берём сохранённые из started (для диффов/подписей).
    roundFinishTool(m.call_id, m.name, state.stepArgs.get(m.call_id) || m.args || {}, m.ok, m.output);
    // Native tabs update by themselves; the screencast fallback needs fresh tabs/address.
    if (String(m.name || "").startsWith("browser_") && paneVisible("browser") && !window.BrowserPanel?.isEmbedded()) send({ type: "browser_state" });
  },
  "text.delta"(m) { finishRound(); finishThinking(); ensureAnswer(); state.answerText += m.text; scheduleAnswerRender(); statusMode("wait"); },
  "plan.updated"(m) { renderPlan(m.steps); },
  "question.asked"(m) { renderQuestion(m); },
  "approval.requested"(m) { renderApproval(m); },
  "approval.resolved"(m) { $(`[data-approval="${m.request_id}"]`)?.remove(); },
  "secret.requested"(m) { toast(T("ev.secretReq", { name: m.name }), "error"); openSettings("secrets"); },
  "show_image"(m) { endAnswerStream(); state.turnHadInline = true; append(mediaFigure({ ...m, kind: "image" })); },
  "show_html"(m) { endAnswerStream(); state.turnHadInline = true; append(widgetFigure(m.html, m.kind, m.caption)); },
  "show_file"(m) { endAnswerStream(); state.turnHadInline = true; append(mediaFigure(m)); },
  "artifact.created"(m) { addArtifact(m); },
  "checkpoint.created"(m) { if (m.recoverable) { state.undoable.add(m.path); renderArtifacts(); } },
  "checkpoint.restored"(m) { toast(m.message || T("ev.checkpointRestored", { path: m.path })); if (state.previewPath === m.path) openPreviewFile(m.path); },
  "run_rollback"(m) {
    if (m.error) { toast(m.error, "error"); return; }
    toast(m.message || T("ev.runRollback", { n: (m.restored || []).length }));
    // Обновляем открытый предпросмотр, если его файл откатили, и панель изменений.
    if (state.previewPath && (m.restored || []).includes(state.previewPath)) openPreviewFile(state.previewPath);
    (m.restored || []).forEach((p) => state.undoable.delete(p));
    renderArtifacts();
  },
  "run_interrupted"(m) {
    // Прошлый прогон этого чата не завершился штатно (процесс умер) — предлагаем продолжить.
    const step = m.step ? T("ev.interruptedStep", { step: m.step }) : "";
    const task = m.task
      ? `<div class="muted" style="margin-top:6px">${esc(T("ev.taskLabel", { task: String(m.task).slice(0, 200) }))}</div>`
      : "";
    const node = append(el(`<div class="card"><div class="card-head">${iconSvg("alert")} ${esc(T("ev.interrupted"))}${esc(step)}</div><div class="card-body"><div>${esc(T("ev.interruptedBody"))}${task}<div class="row" style="gap:8px;margin-top:12px"><button class="btn" data-i="resume">${esc(T("a.continue"))}</button><button class="btn-icon small" data-i="dismiss" data-tip="${escAttr(T("a.hide"))}">${iconSvg("x", "icon icon-sm")}</button></div></div></div>`));
    $('[data-i="resume"]', node).addEventListener("click", () => { send({ type: "resume_run" }); node.remove(); });
    $('[data-i="dismiss"]', node).addEventListener("click", () => { send({ type: "dismiss_interrupted" }); node.remove(); });
  },
  "research.progress"(m) { showResearchProgress(m); },
  "model.routed"(m) { showRouted(m); },
  "reconnecting"(m) { showReconnect(m); },
  "run.finished"(m) {
    finishRound();
    state.researchEl?.remove(); state.researchEl = null;
    if (state.turnHadInline) {
      // Текст ответа уже разложен по блокам вокруг медиа/виджетов — не перекрываем
      // его полным m.text (иначе дубль). Финализируем только текущий хвост.
      const tail = (state.answerText || "").trim();
      if (tail && state.answerEl) renderFinal($(".answer-body", state.answerEl), state.answerText);
      else if (state.answerEl) state.answerEl.remove();
      const host = tail && state.answerEl ? state.answerEl : append(el(`<div class="msg-agent"></div>`));
      addAnswerFooter(host, m);
    } else {
      ensureAnswer(); renderFinal($(".answer-body", state.answerEl), m.text || state.answerText); addAnswerFooter(state.answerEl, m);
    }
    endTurn(); setRunning(false); refreshSessions();
  },
  "run.failed"(m) { clearActivity(); append(el(`<div class="card"><div class="card-head">${iconSvg("alert")} ${esc(T("ev.error"))}</div><div class="card-body"><div class="muted">${esc(m.message)}</div></div></div>`)); endTurn(); setRunning(false); },
  "run.cancelled"() { clearActivity(); endTurn(); setRunning(false); },
  "steering.queued"() { toast(T("ev.steeringQueued")); },
  "reminder.fired"(m) { append(el(`<div class="reminder">${iconSvg("bell")}<span>${esc(m.text || m.note || T("ev.reminder"))}</span></div>`)); toast(m.note || m.text || T("ev.reminder")); },
  log(m) { if (m.level === "warning" || m.level === "error") toast(m.text, m.level === "error" ? "error" : ""); },
  pong() {},
};
function endAnswerStream() { if (state.answerEl) { $(".stream-caret", state.answerEl)?.remove(); const b = $(".answer-body", state.answerEl); if (b) renderFinal(b, state.answerText); state.answerEl = null; } }
function addAnswerFooter(node, m, turn) {
  const text = m.text || state.answerText || "";
  const dur = m.duration_ms ? `${(m.duration_ms / 1000).toFixed(1)} ${T("u.sec")}` : "";
  if (turn == null && typeof state.userTurn === "number") turn = state.userTurn - 1;
  // Откатить весь прогон (вернуть файлы к состоянию до него) — доступно, если
  // известен run_id (живой ответ или из истории).
  const rollback = m.run_id
    ? `<button class="btn-icon small" data-a="rollback" data-tip="${escAttr(T("a.rollbackRun"))}">${iconSvg("undo", "icon icon-sm")}</button>`
    : "";
  const foot = el(`<div class="msg-actions msg-actions-agent">
    <button class="btn-icon small" data-a="copy" data-tip="${escAttr(T("a.copy"))}">${iconSvg("copy", "icon icon-sm")}</button>
    <button class="btn-icon small" data-a="fork" data-tip="${escAttr(T("a.forkHere"))}">${iconSvg("branch", "icon icon-sm")}</button>
    <button class="btn-icon small" data-a="pin" data-tip="${escAttr(T("a.pin"))}">${iconSvg("pin", "icon icon-sm")}</button>
    <button class="btn-icon small" data-a="speak" data-tip="${escAttr(T("a.speak"))}">${iconSvg("volume", "icon icon-sm")}</button>
    ${rollback}
    <span class="ma-time">${dur}</span>
  </div>`);
  $('[data-a="copy"]', foot).addEventListener("click", () => { navigator.clipboard?.writeText(text); toast(T("t.copied")); });
  $('[data-a="fork"]', foot).addEventListener("click", () => forkFrom(turn));
  $('[data-a="pin"]', foot).addEventListener("click", (e) => togglePin(node, e.currentTarget));
  $('[data-a="speak"]', foot).addEventListener("click", (e) => speak(text, e.currentTarget));
  if (m.run_id) $('[data-a="rollback"]', foot).addEventListener("click", async () => {
    if (await confirmDialog({ message: T("cf.rollbackRun"), danger: true })) {
      send({ type: "rollback_run", run_id: m.run_id });
    }
  });
  node.appendChild(foot);
}
// Озвучка ответа (Web Speech API), повторное нажатие — стоп.
function speak(text, btn) {
  try {
    const synth = window.speechSynthesis; if (!synth) { toast(T("ev.ttsUnavailable"), "error"); return; }
    if (synth.speaking) { synth.cancel(); btn?.classList.remove("on"); return; }
    const u = new SpeechSynthesisUtterance(text.replace(/[#*`_>]/g, "").slice(0, 4000));
    u.lang = (window.I18N && window.I18N.lang() === "ru") ? "ru-RU" : "en-US"; u.onend = () => btn?.classList.remove("on");
    btn?.classList.add("on"); synth.speak(u);
  } catch { toast(T("ev.ttsFailed"), "error"); }
}
function togglePin(node, btn) {
  const on = node.classList.toggle("pinned"); btn?.classList.toggle("on", on);
  toast(on ? T("t.pinned") : T("t.unpinned"));
}
function forkFrom(turn) {
  send({ type: "fork_session", turn: (turn == null ? null : turn) });
  toast(T("t.branching"));
}
function showReconnect(m) {
  if (!state.reconnectEl) { clearActivity(); state.reconnectEl = append(el(`<div class="reconnect"><span class="spin">${iconSvg("refresh", "icon icon-sm")}</span><span class="rc-txt"></span></div>`)); }
  $(".rc-txt", state.reconnectEl).textContent = T("ev.reconnect", { a: m.attempt, max: m.max_attempts, delay: m.delay_s ? T("ev.reconnectDelay", { s: m.delay_s }) : "" });
  scrollFeed();
}
// Маршрутизация: показываем, какой модели ушла задача (#5).
function showRouted(m) {
  clearWelcome();
  const sub = (m.note && (String(m.note).match(/\d+\/\d+/) || [])[0]) || "";
  append(el(`<div class="routed">${iconSvg("cpu", "icon icon-sm")}<span class="routed-txt">${esc(T("ev.routedTo"))} <b>${esc(shortModel(m.model || "?"))}</b>${sub ? ` <span class="routed-sub">${esc(sub)}</span>` : ""}</span></div>`));
  scrollFeed();
}
// Ход глубокого исследования (и подобных долгих инструментов) — живая строка (#4).
function showResearchProgress(m) {
  if (!state.researchEl || !state.researchEl.isConnected) {
    state.researchEl = append(el(`<div class="research-prog"><span class="spin">${iconSvg("refresh", "icon icon-sm")}</span><span class="rp-txt"></span></div>`));
  }
  const cnt = (m.total ? ` ${m.done}/${m.total}` : "");
  $(".rp-txt", state.researchEl).textContent = `${T("ev.research." + (m.phase || "read"), {}) || m.phase} — ${m.text || ""}${cnt}`;
  scrollFeed();
}

// ------------------------------------------------------------------ план/вопрос/approval
function renderPlan(steps) {
  const rows = steps.map((s, i) => `<div class="plan-step ${s.status}"><span class="plan-dot"></span><span class="plan-num">${i + 1}</span><span class="plan-txt grow">${esc(s.title)}</span></div>`).join("");
  const done = steps.filter((s) => s.status === "completed").length;
  const html = `<div class="plan" id="live-plan"><div class="plan-head">${esc(T("plan.title"))} · ${done}/${steps.length}</div>${rows}</div>`;
  const cur = $("#live-plan"); if (cur) cur.replaceWith(el(html)); else append(el(html));
}
const RISK_TIERS = {
  high: { key: "risk.high", cls: "risk-high" },
  critical: { key: "risk.critical", cls: "risk-critical" },
};
function renderApproval(m) {
  clearActivity();
  const tier = RISK_TIERS[m.tier];
  const badge = tier ? `<span class="risk-badge ${tier.cls}">${iconSvg("alert", "icon icon-sm")}${esc(T(tier.key))}</span>` : "";
  const reasons = tier && (m.reasons || []).length
    ? `<ul class="approval-reasons">${(m.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
    : "";
  const card = el(`<div class="card" data-approval="${m.request_id}"><div class="card-head">${iconSvg("shield")} ${esc(T("ap.title"))} ${badge}</div><div class="card-body"><div><b>${esc(m.name)}</b> — ${esc(m.reason || "")}</div>${reasons}<div class="approval-meta">${esc(JSON.stringify(m.args || {}).slice(0, 500))}</div></div><div class="card-actions"><button class="btn btn-primary" data-scope="once">${esc(T("ap.allowOnce"))}</button><button class="btn btn-outline" data-scope="project">${esc(T("ap.allowProject"))}</button><button class="btn btn-outline" data-scope="global">${esc(T("ap.allowGlobal"))}</button><span class="grow"></span><button class="btn btn-danger" data-scope="deny">${esc(T("ap.deny"))}</button></div></div>`);
  $$("[data-scope]", card).forEach((b) => b.addEventListener("click", () => { send({ type: "approval", request_id: m.request_id, scope: b.dataset.scope }); card.remove(); }));
  append(card);
}
function renderQuestion(m) {
  clearActivity();
  const chosen = {};
  const blocks = (m.questions || []).map((q, qi) => {
    if (q.kind === "ranking") { chosen[qi] = (q.options || []).map((o) => o.label); return `<div class="q-block" data-rank="${qi}"><div class="q-title">${esc(q.question)}</div><div class="rank-list">${(q.options || []).map((o, oi) => `<div class="rank-item" data-label="${escAttr(o.label)}"><span class="rank-num"></span><span class="grow">${esc(o.label)}</span><span class="rank-moves"><button data-dir="up">${iconSvg("arrow-up", "icon icon-sm")}</button><button data-dir="down">${iconSvg("arrow-down", "icon icon-sm")}</button></span></div>`).join("")}</div></div>`; }
    const opts = (q.options || []).map((o, oi) => `<button class="q-opt" data-q="${qi}" data-o="${oi}"><div class="grow"><div class="q-opt-label">${esc(o.label)}${o.recommended ? ` <span class="q-opt-rec">${esc(T("q.recommend"))}</span>` : ""}</div>${o.description ? `<div class="q-opt-desc">${esc(o.description)}</div>` : ""}</div></button>`).join("");
    return `<div class="q-block"><div class="q-title">${esc(q.question)}</div><div class="q-opts">${opts}</div></div>`;
  }).join("");
  const card = el(`<div class="card"><div class="card-head">${iconSvg("message")} ${esc(T("q.title"))}</div><div class="card-body">${blocks}</div><div class="card-actions"><button class="btn btn-ghost" id="q-skip">${esc(T("q.decide"))}</button><span class="grow"></span><button class="btn btn-primary" id="q-send">${esc(T("q.answer"))}</button></div></div>`);
  const multi = (qi) => m.questions[qi].kind === "multiple";
  $$(".q-opt", card).forEach((b) => b.addEventListener("click", () => {
    const qi = b.dataset.q, oi = +b.dataset.o, label = m.questions[qi].options[oi].label;
    chosen[qi] = chosen[qi] || [];
    if (multi(qi)) { const i = chosen[qi].indexOf(label); if (i >= 0) { chosen[qi].splice(i, 1); b.classList.remove("chosen"); } else { chosen[qi].push(label); b.classList.add("chosen"); } }
    else { chosen[qi] = [label]; $$(`.q-opt[data-q="${qi}"]`, card).forEach((x) => x.classList.remove("chosen")); b.classList.add("chosen"); }
  }));
  $$("[data-rank]", card).forEach((block) => {
    const qi = block.dataset.rank;
    const renumber = () => { $$(".rank-item", block).forEach((it, i) => { $(".rank-num", it).textContent = i + 1; }); chosen[qi] = $$(".rank-item", block).map((it) => it.dataset.label); };
    renumber();
    $$(".rank-moves button", block).forEach((b) => b.addEventListener("click", () => {
      const item = b.closest(".rank-item");
      if (b.dataset.dir === "up" && item.previousElementSibling) item.parentElement.insertBefore(item, item.previousElementSibling);
      if (b.dataset.dir === "down" && item.nextElementSibling) item.parentElement.insertBefore(item.nextElementSibling, item);
      renumber();
    }));
  });
  $("#q-send", card).addEventListener("click", () => { send({ type: "answer", request_id: m.request_id, answers: chosen }); card.remove(); });
  $("#q-skip", card).addEventListener("click", () => { send({ type: "answer", request_id: m.request_id, answers: {} }); card.remove(); });
  append(card);
}

// ------------------------------------------------------------------ загрузка сессии
function loadSession(session) {
  if (!session) return;
  state.sessionId = session.id; els.chatTitle.textContent = dispTitle(session.title) || T("app.newChat");
  if (session.model) setModel(session.model);
  state.artifacts.clear(); state.undoable.clear();
  (session.artifacts || []).forEach((a) => state.artifacts.set(a.path, a));
  renderArtifacts();
  els.feedInner.innerHTML = ""; state.userTurn = 0;
  const tl = session.timeline || [];
  // Помним последний чат с содержимым — чтобы вернуть его при следующем запуске.
  state.currentHasContent = tl.length > 0;
  if (tl.length) { try { LS.set("last_session_id", session.id); } catch {} }
  if (!tl.length) { showWelcome(); return; }
  // An old chat is never a welcome screen: drop the sky at once (the fade is only for the
  // first message of a new chat).
  window.Cosmos?.unmount();
  els.app?.classList.remove("welcome-active", "welcome-leaving");
  let group = null;
  const closeGroup = () => { if (group) { finalizeHistoryRound(group); group = null; } };
  for (const e of tl) {
    if (e.kind === "user") { closeGroup(); els.feedInner.appendChild(userBlock(e.text, e.attachments, state.userTurn++)); }
    else if (e.kind === "step") { if (!group) { group = startHistoryRound(); els.feedInner.appendChild(group); } historyAddRow(group, e); }
    else if (e.kind === "text") { closeGroup(); if ((e.text || "").trim()) { const n = el(`<div class="msg-agent"><div class="answer-body md"></div></div>`); renderFinal($(".answer-body", n), e.text); els.feedInner.appendChild(n); } }
    else if (e.kind === "image") { closeGroup(); els.feedInner.appendChild(mediaFigure({ ...e, kind: "image" })); }
    else if (e.kind === "media") { closeGroup(); els.feedInner.appendChild(mediaFigure({ ...e, kind: e.media_kind || "file" })); }
    else if (e.kind === "widget") { closeGroup(); els.feedInner.appendChild(widgetFigure(e.html, e.widget_kind, e.caption)); }
    else if (e.kind === "answer") { closeGroup(); const n = el(`<div class="msg-agent">${(e.text || "").trim() ? `<div class="answer-body md"></div>` : ""}</div>`); if ((e.text || "").trim()) renderFinal($(".answer-body", n), e.text); addAnswerFooter(n, { text: e.full || e.text, duration_ms: e.duration_ms || 0, run_id: e.run_id }, state.userTurn - 1); els.feedInner.appendChild(n); }
    else if (e.kind === "error") { closeGroup(); els.feedInner.appendChild(el(`<div class="card"><div class="card-head">${iconSvg("alert")} ${esc(T("ev.error"))}</div><div class="card-body"><div class="muted">${esc(e.text)}</div></div></div>`)); }
  }
  closeGroup();
  scrollFeed(true);
  updateWorkspaceLock();
}
const STARTERS = [
  { icon: "folder", key: "welcome.s1" },
  { icon: "cpu", key: "welcome.s2" },
  { icon: "globe", key: "welcome.s3" },
  { icon: "doc", key: "welcome.s4" },
];
const T = (k, v) => (window.I18N ? window.I18N.t(k, v) : k);
// Режимы приходят с сервера (id стабильны) — локализуем по id, иначе берём серверный текст.
const _hasKey = (k) => !!(window.I18N && window.I18N.DICT && window.I18N.DICT.en && window.I18N.DICT.en[k]);
const modeLabel = (m, field) => { const k = `mode.${m.id}.${field}`; return _hasKey(k) ? T(k) : (m[field] || ""); };
// Заголовок сессии по умолчанию с сервера — показываем локализованным.
const dispTitle = (t) => (t === "Новый диалог" ? T("app.newChat") : (t || ""));
// Маскот «Звёздыш» (Alti) для приветствия; фолбэк — брендовая звезда, если mascot.js не загрузился.
const _SPARKLE = "M12 0.5 C13 8.2 15.8 11 23.5 12 C15.8 13 13 15.8 12 23.5 C11 15.8 8.2 13 0.5 12 C8.2 11 11 8.2 12 0.5 Z";
const ALTAIR_STAR = `<svg viewBox="0 0 72 72" fill="currentColor" aria-hidden="true"><path transform="translate(6 12) scale(2.1)" d="${_SPARKLE}"/><path transform="translate(48 6) scale(0.85)" d="${_SPARKLE}"/></svg>`;
function showWelcome() {
  state.currentHasContent = false;   // пустой чат — при реконнекте его не восстанавливаем
  const st = STARTERS.map((s) => `<button class="starter" data-text="${escAttr(T(s.key))}">${iconSvg(s.icon, "icon icon-sm")}<span>${esc(T(s.key))}</span></button>`).join("");
  const mark = window.Mascot ? window.Mascot.svg({ mood: "idle", size: 96, satellites: true }) : ALTAIR_STAR;
  els.feedInner.innerHTML = `<div class="welcome"><span class="w-mark w-mark-mascot">${mark}</span><h1>${esc(T("welcome.title"))}</h1><p>${esc(T("welcome.subtitle"))}</p><div class="welcome-starters">${st}</div></div>`;
  $$(".starter", els.feedInner).forEach((b) => b.addEventListener("click", () => { els.input.value = b.dataset.text; autoGrow(); els.input.focus(); updateSendBtn(); }));
  // Живой космический фон на всё рабочее полотно — только на приветствии (до первого
  // сообщения). Стиль выбирается в «Настройки → Внешний вид» и следует за темой.
  els.app?.classList.remove("welcome-leaving");
  els.app?.classList.add("welcome-active");
  window.Cosmos?.mount(els.work || els.chatCol);
  updateWorkspaceLock();   // на приветствии чат ещё не начат — папку можно выбрать
}

// ------------------------------------------------------------------ рельс сессий
let _sessTimer = null;
function refreshSessions() {
  clearTimeout(_sessTimer);
  _sessTimer = setTimeout(async () => {
    const q = ($("#session-search")?.value || "").trim();
    if (q) { renderSearch(q); return; }
    try { state.sessionList = (await (await fetch("/api/sessions")).json()).sessions || []; renderSessions(state.sessionList); } catch {}
  }, 250);
}
function sessionRow(s) {
  const title = dispTitle(s.title) || T("side.untitled");
  const item = el(`<div class="session-item ${s.id === state.sessionId ? "active" : ""}" role="button" tabindex="0" data-id="${escAttr(s.id)}">${iconSvg("message", "icon icon-sm")}<span class="s-title truncate">${esc(title)}</span><span class="s-ren btn-icon small" data-tip="${escAttr(T("side.rename"))}">${iconSvg("doc", "icon icon-sm")}</span><span class="s-del btn-icon small" data-tip="${escAttr(T("side.delete"))}">${iconSvg("trash", "icon icon-sm")}</span></div>`);
  item.addEventListener("click", (e) => {
    if (e.target.closest(".s-del")) { deleteSession(s.id); e.stopPropagation(); return; }
    if (e.target.closest(".s-ren")) { e.stopPropagation(); startRename(item, s.id, s.title); return; }
    if (e.target.closest(".s-edit")) return;
    if (s.id !== state.sessionId) send({ type: "load_session", session_id: s.id });
  });
  item.addEventListener("dblclick", (e) => { if (!e.target.closest(".s-del,.s-edit")) startRename(item, s.id, s.title); });
  return item;
}
function renderSessions(list) {
  list = list || state.sessionList || [];
  // The new chat shows up right after the user's first message, before the server has it.
  const pending = state.pendingChat && !list.some((x) => x.id === state.pendingChat.id) ? [state.pendingChat] : [];
  const all = [...pending, ...list];
  els.sessions.innerHTML = `<div class="rail-section-label">${esc(T("side.chats"))}</div>` + (all.length ? "" : `<div class="dim" style="padding:8px 12px">${esc(T("side.empty"))}</div>`);
  for (const s of all) els.sessions.appendChild(sessionRow(s));
}
// Inline rename in the rail (also from the chat title in the header).
function startRename(row, id, current) {
  const span = row ? $(".s-title", row) : null;
  const input = el(`<input class="s-edit field" value="${escAttr(dispTitle(current) || "")}" maxlength="120" />`);
  if (span) { span.replaceWith(input); } else return;
  input.focus(); input.select();
  let done = false;
  const finish = (save) => {
    if (done) return; done = true;
    const title = input.value.trim();
    if (save && title && title !== dispTitle(current)) send({ type: "rename_session", session_id: id, title });
    const back = el(`<span class="s-title truncate">${esc(save && title ? title : dispTitle(current) || T("side.untitled"))}</span>`);
    input.replaceWith(back);
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); finish(true); } else if (e.key === "Escape") finish(false); e.stopPropagation(); });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("click", (e) => e.stopPropagation());
}
// A title arrived (from the model, typed out) or the user renamed a chat.
function applySessionTitle(m) {
  const list = state.sessionList || [];
  const known = list.find((x) => x.id === m.session_id);
  if (known) known.title = m.title;
  if (state.pendingChat && state.pendingChat.id === m.session_id) state.pendingChat.title = m.title;
  const row = els.sessions.querySelector(`.session-item[data-id="${CSS.escape(m.session_id)}"] .s-title`);
  if (row) { if (m.renamed) row.textContent = m.title; else typeText(row, m.title); }
  if (m.session_id === state.sessionId) { if (m.renamed) els.chatTitle.textContent = m.title; else typeText(els.chatTitle, m.title); }
}
// Search in chat contents: one row per chat with the matching fragment; a click opens the
// chat right at that spot, highlighted.
async function renderSearch(q) {
  let d; try { d = await (await fetch(`/api/sessions/search?q=${encodeURIComponent(q)}`)).json(); } catch { return; }
  if ((($("#session-search")?.value) || "").trim() !== q) return;  // typed on meanwhile
  const res = d.results || [];
  els.sessions.innerHTML = `<div class="rail-section-label">${esc(T("side.found", { n: res.length }))}</div>` + (res.length ? "" : `<div class="dim" style="padding:8px 12px">${esc(T("side.notFound"))}</div>`);
  const words = q.toLowerCase().split(/\s+/).filter((w) => w.length > 1);
  const mark = (text) => { let h = esc(text); words.forEach((w) => { h = h.replace(new RegExp(w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), (x) => `<mark>${x}</mark>`); }); return h; };
  for (const r of res) {
    const item = el(`<button class="session-item search-hit ${r.id === state.sessionId ? "active" : ""}">${iconSvg("message", "icon icon-sm")}<span class="s-col"><span class="s-title truncate">${mark(dispTitle(r.title) || T("side.untitled"))}</span>${r.snippet ? `<span class="s-snippet">${mark(r.snippet)}</span>` : ""}</span></button>`);
    item.addEventListener("click", () => {
      state.jumpTo = q;
      if (r.id === state.sessionId) jumpToMatch(); else send({ type: "load_session", session_id: r.id });
    });
    els.sessions.appendChild(item);
  }
}
function jumpToMatch() {
  const q = (state.jumpTo || "").trim(); state.jumpTo = "";
  if (!q) return;
  $$("mark.find-hit", els.feedInner).forEach((m) => m.replaceWith(document.createTextNode(m.textContent)));
  const words = q.toLowerCase().split(/\s+/).filter((w) => w.length > 1);
  const needles = [q.toLowerCase(), ...words];
  const walker = document.createTreeWalker(els.feedInner, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.parentElement && n.parentElement.closest(".answer-body, .msg-user, .user-text, .md") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT) });
  for (const needle of needles) {
    walker.currentNode = els.feedInner;
    let node;
    while ((node = walker.nextNode())) {
      const at = node.nodeValue.toLowerCase().indexOf(needle);
      if (at === -1) continue;
      const range = document.createRange();
      range.setStart(node, at); range.setEnd(node, at + needle.length);
      const hit = document.createElement("mark"); hit.className = "find-hit";
      range.surroundContents(hit);
      hit.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
  }
}
async function deleteSession(id) { await fetch(`/api/sessions/${id}`, { method: "DELETE" }); if (id === state.sessionId) send({ type: "new_session" }); refreshSessions(); }

// ------------------------------------------------------------------ панель работы
function addArtifact(a) {
  state.artifacts.set(a.path, a);
  renderArtifacts();
  // Раньше здесь принудительно открывалась панель файлов при каждом созданном
  // файле — это мешало. Теперь только помечаем новизну и, если панель уже
  // открыта, обновляем дерево, чтобы новый файл появился сам.
  state.filesNew.add(a.path);
  if (paneVisible("artifacts")) refreshFilesTree();
}
// «Файлы» — обозреватель рабочей папки (как файловая система): ленивое дерево,
// фильтр, новые файлы от агента появляются сами. renderArtifacts сохраняет имя
// (его зовут события), но теперь строит именно дерево файлов.
function renderArtifacts() { ensureFilesPane(); if (paneVisible("artifacts")) refreshFilesTree(); }

function ensureFilesPane() {
  const v = $("#view-artifacts"); if (!v || v.dataset.built === "1") return;
  v.dataset.built = "1";
  v.innerHTML = `<div class="files-pane"><div class="files-bar"><span class="files-search">${iconSvg("search", "icon icon-sm")}<input id="files-filter" placeholder="${escAttr(T("files.filter"))}" spellcheck="false" /></span><button class="btn-icon small" id="files-refresh" data-tip="${escAttr(T("a.refresh"))}">${iconSvg("refresh", "icon icon-sm")}</button></div><div class="files-tree" id="files-tree"></div></div>`;
  const inp = $("#files-filter", v);
  inp.addEventListener("input", () => { state.filesFilter = inp.value.trim(); refreshFilesTree(); });
  $("#files-refresh", v).addEventListener("click", () => refreshFilesTree());
}

async function filesFetchDir(path) {
  try {
    const r = await fetch(`/api/files/dir?workspace=${encodeURIComponent(state.workspace)}&path=${encodeURIComponent(path || "")}`);
    return (await r.json()).entries || [];
  } catch { return []; }
}
function fileIcon(name, dir) {
  if (dir) return "folder";
  return /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(name) ? "image" : "doc";
}
function fileRow(entry, depth) {
  const isNew = state.filesNew.has(entry.path);
  const open = state.filesOpen.has(entry.path);
  const caret = entry.dir ? `<span class="ft-caret ${open ? "open" : ""}">${iconSvg("chevron-right", "icon icon-sm")}</span>` : `<span class="ft-caret ft-caret-none"></span>`;
  const row = el(`<div class="ft-row${isNew ? " ft-new" : ""}" data-path="${escAttr(entry.path)}" data-dir="${entry.dir ? 1 : 0}" style="padding-left:${6 + depth * 14}px">${caret}<span class="ft-ico">${iconSvg(fileIcon(entry.name, entry.dir), "icon icon-sm")}</span><span class="ft-name truncate">${esc(entry.name)}</span>${entry.dir ? "" : `<span class="ft-size">${entry.size ? humanSize(entry.size) : ""}</span>`}</div>`);
  return row;
}
// Рекурсивно добавляет строки папки в контейнер; уже раскрытые подпапки
// разворачивает следом (их дети — в собственный вложенный блок сразу после строки).
async function filesRenderChildren(container, path, depth) {
  const entries = await filesFetchDir(path);
  for (const e of entries) {
    container.appendChild(fileRow(e, depth));
    if (e.dir && state.filesOpen.has(e.path)) {
      const box = el(`<div class="ft-children"></div>`);
      container.appendChild(box);
      await filesRenderChildren(box, e.path, depth + 1);
    }
  }
}
let _treeGen = 0, _treeLatest = Promise.resolve();
function refreshFilesTree() {
  const run = _refreshFilesTree(++_treeGen);
  _treeLatest = run;
  return run;
}
async function _refreshFilesTree(gen) {
  ensureFilesPane();
  const tree = $("#files-tree"); if (!tree) return;
  // Built aside and swapped in by the latest call only: two refreshes at once (a workspace
  // switch and a file link) used to both append into the cleared tree and list it twice.
  const next = document.createElement("div");
  // Filter mode: a flat list of matches by name/path (like the @ search).
  if (state.filesFilter) {
    let files = [];
    try { files = (await (await fetch(`/api/files/list?workspace=${encodeURIComponent(state.workspace)}&q=${encodeURIComponent(state.filesFilter)}&limit=200`)).json()).files || []; } catch {}
    if (!files.length) next.innerHTML = `<div class="empty small">${esc(T("files.none"))}</div>`;
    for (const p of files) next.appendChild(fileRow({ name: p.split("/").pop(), path: p, dir: false, size: 0 }, 0));
  } else {
    await filesRenderChildren(next, "", 0);
    if (!next.firstChild) next.innerHTML = `<div class="empty small">${esc(T("files.empty"))}</div>`;
  }
  // A newer refresh started meanwhile: its tree wins; callers still get a finished tree.
  if (gen !== _treeGen) { await _treeLatest; return; }
  tree.replaceChildren(...next.childNodes);
}
// Делегированные клики по дереву: раскрытие папок и открытие файлов.
function initFilesTree() {
  const v = $("#view-artifacts"); if (!v) return;
  v.addEventListener("click", async (e) => {
    const row = e.target.closest(".ft-row"); if (!row || !v.contains(row)) return;
    const path = row.dataset.path;
    if (row.dataset.dir === "1") {
      const next = row.nextElementSibling;
      const kids = next && next.classList.contains("ft-children") ? next : null;
      if (state.filesOpen.has(path)) { state.filesOpen.delete(path); kids?.remove(); $(".ft-caret", row)?.classList.remove("open"); }
      else {
        state.filesOpen.add(path); $(".ft-caret", row)?.classList.add("open");
        const depth = Math.round((parseInt(row.style.paddingLeft) - 6) / 14) + 1;
        const box = el(`<div class="ft-children"></div>`);
        row.after(box);
        await filesRenderChildren(box, path, depth);
      }
    } else {
      state.filesNew.delete(path);
      row.classList.remove("ft-new");
      openPreviewFile(path);
    }
  });
  // Правый клик по файлу/папке — контекстное меню (как в проводнике).
  v.addEventListener("contextmenu", (e) => {
    const row = e.target.closest(".ft-row"); if (!row || !v.contains(row)) return;
    e.preventDefault();
    filesContextMenu(e.clientX, e.clientY, row.dataset.path, row.dataset.dir === "1", row.querySelector(".ft-name")?.textContent || row.dataset.path);
  });
}
// Абсолютный путь в нативном виде (Windows — с «\»), для «копировать».
function absPathNative(rel) {
  const abs = absPath(rel);
  return /^[a-zA-Z]:/.test(abs) ? abs.replace(/\//g, "\\") : abs;
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast(T("t.copied")); }
  catch { toast(T("files.copyFail"), "error"); }
}
// Контекстное меню файла/папки в панели «Файлы».
async function filesContextMenu(x, y, path, isDir, name) {
  const rel = path;
  // Программы «Открыть в …» подтягиваем заранее (только для файлов).
  let openers = [];
  if (!isDir) {
    try { openers = (await (await fetch(`/api/files/openers?workspace=${encodeURIComponent(state.workspace)}&path=${encodeURIComponent(rel)}`)).json()).openers || []; } catch {}
  }
  const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace: state.workspace, path: rel, ...body }) }).then((r) => r.json());
  const items = [];
  if (!isDir) {
    items.push({ icon: "paperclip", text: T("files.attachCtx"), onClick: () => attachFileAsContext(rel, name, isDir) });
    items.push({ sep: true });
  }
  items.push({ label: T("files.copy") });
  items.push({ icon: "copy", text: T("files.copyPath"), onClick: () => copyText(rel) });
  items.push({ icon: "copy", text: T("files.copyAbsPath"), onClick: async () => {
    let abs = absPathNative(rel);
    try { const d = await (await fetch(`/api/files/abspath?workspace=${encodeURIComponent(state.workspace)}&path=${encodeURIComponent(rel)}`)).json(); if (d.ok && d.abspath) abs = d.abspath; } catch {}
    copyText(abs);
  } });
  items.push({ icon: "copy", text: T("files.copyName"), onClick: () => copyText(name) });
  items.push({ sep: true });
  if (!isDir) {
    items.push({ label: T("files.openWith") });
    items.push({ icon: "external", text: T("files.openDefault"), onClick: () => post("/api/files/open", {}) });
    for (const op of openers.slice(0, 8)) {
      items.push({ icon: "external", text: op.name, onClick: () => post("/api/files/open", { exec: op.exec }) });
    }
    items.push({ icon: "dots", text: T("files.openOther"), onClick: () => post("/api/files/open", { exec: "__choose__" }) });
    items.push({ sep: true });
  }
  items.push({ icon: "folder", text: T("files.reveal"), onClick: () => post("/api/files/reveal", {}) });
  if (isDir) {
    items.push({ icon: "terminal", text: T("files.openTerminal"), onClick: () => openFolderInTerminal(rel) });
  }
  openMenuAt(x, y, items);
}
// «Прикрепить как контекст» — файл идёт вложением к следующему сообщению.
function attachFileAsContext(rel, name, isDir) {
  if (isDir) { toast(T("files.dirNoAttach"), "error"); return; }
  if (state.attachments.some((a) => a.rel === rel)) { toast(T("files.alreadyAttached")); return; }
  state.attachments.push({ path: absPath(rel), rel, name, src: absPathNative(rel), kind: attachType(name, "") === "photo" ? "image" : "file" });
  renderAttachPreview();
  toast(T("files.attached"));
}
// «Открыть в терминале» — показываем терминал и выполняем cd в папку.
function openFolderInTerminal(rel) {
  if (!paneVisible("terminal")) togglePane("terminal");
  const abs = absPathNative(rel);
  // `pushd` меняет и диск, и папку И в PowerShell (шелл по умолчанию), И в cmd —
  // в отличие от `cd /d` (только cmd) или голого `cd` (в cmd не сменит диск).
  const cmd = `pushd "${abs}"\r`;
  setTimeout(() => window.AgentTerminal?.sendText?.(cmd), 250);
}
// Открыть панель «Файлы» и подсветить/раскрыть до конкретного файла (клик по
// ссылке агента в тексте).
async function selectFileInTree(path) {
  const rel = String(path || "").replace(/^\/+/, "");
  if (!paneVisible("artifacts")) togglePane("artifacts");
  ensureFilesPane();
  // Сбрасываем фильтр, чтобы дерево было полным.
  state.filesFilter = ""; const flt = $("#files-filter"); if (flt) flt.value = "";
  // Раскрываем все родительские папки по пути.
  const parts = rel.split("/"); parts.pop();
  let acc = "";
  for (const seg of parts) { acc = acc ? `${acc}/${seg}` : seg; state.filesOpen.add(acc); }
  await refreshFilesTree();
  const tree = $("#files-tree");
  const row = tree && $(`.ft-row[data-path="${(window.CSS && CSS.escape) ? CSS.escape(rel) : rel}"]`, tree);
  if (row) {
    $$(".ft-row.ft-selected", tree).forEach((r) => r.classList.remove("ft-selected"));
    row.classList.add("ft-selected");
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    if (row.dataset.dir !== "1") openPreviewFile(rel);
  } else {
    toast(T("files.notFound"), "error");
  }
}
window.AltairOpenFile = selectFileInTree;
// ---------------------------------------------------------- многопанельный док
const PANES = ["terminal", "diff", "browser", "artifacts", "preview"];
function paneEl(id) { return $(`#pane-${id}`); }
function paneVisible(id) { const p = paneEl(id); return p && !p.hidden; }
function anyPaneOpen() { return PANES.some(paneVisible); }

function updateDock() {
  const open = PANES.filter(paneVisible);
  els.dock.hidden = open.length === 0;
  // Первая видимая панель — без верхнего разделителя.
  PANES.forEach((id) => paneEl(id)?.classList.toggle("pane--first", id === open[0]));
  // Подсветка кнопок-переключателей.
  $$(".dock-tgl").forEach((b) => b.classList.toggle("on", paneVisible(b.dataset.pane)));
  // Сброс выравнивания высот, когда состав панелей поменялся.
  open.forEach((id) => { const p = paneEl(id); if (!p.style.flexBasis) p.style.flex = "1 1 0"; });
}

function openPane(id) {
  const p = paneEl(id); if (!p) return;
  p.hidden = false;
  updateDock();
  if (id === "terminal") window.AgentTerminal?.onTabShown();
  if (id === "diff") loadDiff();
  if (id === "browser") window.BrowserPanel?.onPaneOpen();
  if (id === "artifacts") refreshFilesTree();
}
function closePane(id) {
  const p = paneEl(id); if (!p) return;
  p.hidden = true; p.classList.remove("pane--max"); p.style.flex = ""; p.style.flexBasis = "";
  els.dock.classList.remove("dock--max");
  if (id === "browser") window.BrowserPanel?.onPaneClose();
  updateDock();
  window.BrowserPanel?.relayout();
}
function togglePane(id) { paneVisible(id) ? userClosePane(id) : userOpenPane(id); }
// The agent working in the browser shows the panel once per task. Closed by the user, it stays
// closed (the agent keeps browsing in the background) until the user opens it again.
function userOpenPane(id) { if (id === "browser") setBrowserDismissed(false); openPane(id); }
function userClosePane(id) { if (id === "browser") setBrowserDismissed(true); closePane(id); }
function setBrowserDismissed(on) { state.browserDismissed = on; try { LS.set("browser_dismissed", on ? "1" : ""); } catch {} }
function autoOpenBrowser() {
  if (paneVisible("browser") || state.browserAutoOpened) return;
  if (state.browserDismissed === undefined) { try { state.browserDismissed = LS.get("browser_dismissed", "") === "1"; } catch { state.browserDismissed = false; } }
  if (state.browserDismissed) return;
  state.browserAutoOpened = true;
  openPane("browser");
}
window.autoOpenBrowser = autoOpenBrowser;

// Разворот панели на всё окно приложения (чат скрыт, рейл остаётся).
function maximizePane(id) {
  const p = paneEl(id); if (!p) return;
  const on = !p.classList.contains("pane--max");
  PANES.forEach((x) => paneEl(x)?.classList.remove("pane--max"));
  els.dock.classList.toggle("dock--max", on);
  p.classList.toggle("pane--max", on);
}

// Вынос панели отдельным окном ОС (связь сохраняется — то же приложение/бэкенд).
function popoutPane(id) {
  const url = location.origin + `/?pane=${id}`;
  const title = paneEl(id)?.querySelector(".pane-title")?.textContent || id;
  const T = window.__TAURI__;
  const WV = T && ((T.webviewWindow && T.webviewWindow.WebviewWindow) || (T.webview && T.webview.WebviewWindow));
  if (WV) {
    try {
      const w = new WV(`pane-${id}-${Date.now()}`, { url, title, width: 980, height: 720, decorations: true });
      w.once?.("tauri://created", () => closePane(id));
      w.once?.("tauri://error", (e) => { console.error("popout", e); toast(T("ev.popoutFail2"), "error"); });
      return;
    } catch (e) { console.error("popout", e); toast(T("ev.popoutFail", { e: (e && e.message || e) }), "error"); return; }
  }
  const w = window.open(url, `pane-${id}`, "width=980,height=720");
  if (w) closePane(id); else toast(T("ev.popupBlocked"), "error");
}

// Совместимость со старыми вызовами.
function openPanel(view) { openPane(view); }
function switchPanel(view) { openPane(view); }
async function openPreviewFile(path) {
  openPanel("preview"); state.previewPath = path;
  const v = $("#view-preview"); v.innerHTML = `<div class="empty">${esc(T("prev.loading"))}</div>`;
  try {
    const r = await fetch(`/api/file?path=${encodeURIComponent(absPath(path))}`);
    if (!r.ok) { v.innerHTML = `<div class="empty">${esc(T("prev.openFail"))}</div>`; return; }
    const info = await r.json(); const raw = `/files/${absPath(path)}`;
    // The server reports a .md file as text in the markdown language: show it as a document.
    const isMd = info.kind === "markdown" || info.language === "markdown";
    const tools = `<div class="preview-tools">${info.kind === "html" || isMd ? `<div class="segmented" id="pv-mode"><button data-m="page" class="${state.previewMode === "page" ? "on" : ""}">${esc(T("prev.page"))}</button><button data-m="code" class="${state.previewMode === "code" ? "on" : ""}">${esc(T("prev.code"))}</button></div>` : ""}<span class="grow"></span><a class="btn btn-outline" href="${esc(raw)}" target="_blank" rel="noopener">${iconSvg("external", "icon icon-sm")} ${esc(T("a.inBrowser"))}</a>${info.content != null ? `<button class="btn btn-outline" id="pv-copy">${iconSvg("copy", "icon icon-sm")} ${esc(T("a.copy"))}</button>` : ""}</div>`;
    let bodyHtml = "";
    if (info.kind === "image") bodyHtml = `<img class="preview-img" src="${esc(raw)}" alt="" />`;
    else if (info.kind === "html" && state.previewMode === "page") bodyHtml = `<iframe class="preview-frame" src="${esc(raw)}" sandbox="allow-scripts allow-forms" style="height:70dvh"></iframe>`;
    else if (isMd && state.previewMode === "page") bodyHtml = `<div class="md" id="pv-md"></div>`;
    else bodyHtml = `<pre class="md"><code>${esc(info.content || "")}</code></pre>`;
    v.innerHTML = tools + bodyHtml;
    if (isMd && state.previewMode === "page") renderFinal($("#pv-md", v), info.content || "", String(path).replace(/\\/g, "/").split("/").slice(0, -1).join("/"));
    else postProcess(v);
    $("#pv-copy", v)?.addEventListener("click", () => { navigator.clipboard?.writeText(info.content || ""); toast(T("t.copied")); });
    $$("#pv-mode button", v).forEach((b) => b.addEventListener("click", () => { state.previewMode = b.dataset.m; openPreviewFile(path); }));
  } catch { v.innerHTML = `<div class="empty">${esc(T("prev.loadErr"))}</div>`; }
}
async function loadDiff() {
  const v = $("#view-diff"); v.innerHTML = `<div class="empty">${esc(T("diff.loading"))}</div>`;
  try {
    const d = await (await fetch(`/api/git/diff?workspace=${encodeURIComponent(state.workspace)}`)).json();
    if (!d.available) { v.innerHTML = `<div class="empty">${iconSvg("git", "icon")}<div>${esc(d.reason || T("diff.noRepo"))}</div></div>`; return; }
    const head = `<div class="preview-tools"><span class="chip">${iconSvg("git", "icon icon-sm")} ${esc(d.branch || "")}</span><span class="grow"></span><button class="btn btn-outline" id="diff-refresh">${iconSvg("refresh", "icon icon-sm")} ${esc(T("a.refresh"))}</button></div>`;
    if (!d.diff?.trim() && !(d.untracked || []).length) { v.innerHTML = head + `<div class="empty">${iconSvg("check", "icon")}<div>${esc(T("diff.noChanges"))}</div></div>`; }
    else { v.innerHTML = head + `<div class="diff">${renderDiff(d.diff || "")}${(d.untracked || []).map((f) => `<div class="diff-line diff-add">＋ ${esc(T("diff.newFile", { f }))}</div>`).join("")}</div>`; }
    $("#diff-refresh", v).addEventListener("click", loadDiff);
    $$("[data-revert-hunk]", v).forEach((b) => b.addEventListener("click", async () => {
      if (!(await confirmDialog({ message: T("cf.revertHunk"), danger: true }))) return;
      b.disabled = true;
      try {
        const r = await (await fetch("/api/git/revert_hunk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace: state.workspace, file: b.dataset.file, hunk: +b.dataset.revertHunk }) })).json();
        if (r.ok) { toast(T("diff.hunkReverted")); loadDiff(); if (state.previewPath) openPreviewFile(state.previewPath); }
        else { toast(r.error || T("diff.revertFail"), "error"); b.disabled = false; }
      } catch { toast(T("t.error"), "error"); b.disabled = false; }
    }));
  } catch { v.innerHTML = `<div class="empty">${esc(T("t.error"))}</div>`; }
}
function renderDiff(diff) {
  // Структурируем на файлы → ханки; у каждого ханка кнопка «Откатить» (reject).
  let out = "", curFile = "", hunkIdx = -1;
  for (const l of diff.split("\n")) {
    if (l.startsWith("diff --git")) {
      curFile = l.replace("diff --git a/", "").split(" ")[0]; hunkIdx = -1;
      out += `<div class="diff-file-head">${esc(curFile)}</div>`;
    } else if (l.startsWith("+++") || l.startsWith("---") || l.startsWith("index") || l.startsWith("new file") || l.startsWith("deleted file") || l.startsWith("rename ") || l.startsWith("similarity ")) {
      continue;
    } else if (l.startsWith("@@")) {
      hunkIdx++;
      out += `<div class="diff-hunk-head"><span class="diff-line diff-hunk grow">${esc(l)}</span><button class="btn-icon small" data-revert-hunk="${hunkIdx}" data-file="${escAttr(curFile)}" data-tip="${escAttr(T("a.revertHunk"))}">${iconSvg("undo", "icon icon-sm")}</button></div>`;
    } else {
      let cls = l.startsWith("+") ? "diff-add" : l.startsWith("-") ? "diff-del" : "";
      out += `<div class="diff-line ${cls}">${esc(l)}</div>`;
    }
  }
  return out;
}

// ------------------------------------------------------------------ верх: селекторы, статус, стоимость
function wsName(p) { return (p || "").split(/[\\/]/).filter(Boolean).slice(-1)[0] || p || T("ws.folderFallback"); }
function sameWs(a, b) { return (a || "").replace(/[\\/]+$/, "").toLowerCase() === (b || "").replace(/[\\/]+$/, "").toLowerCase(); }
// Внутренняя папка чата (storage/chat_files/<hex>) — это НЕ рабочая папка проекта,
// такие с рандомными именами не показываем и не даём выбрать (#3).
function isChatFolder(p) { return /[\\/](?:storage[\\/])?chat_files[\\/]/i.test(p || ""); }
function rememberWorkspace(p) {
  if (!p || isChatFolder(p)) return;
  let list = []; try { list = JSON.parse(LS.get("recent_workspaces", "[]")); } catch {}
  list = [p, ...list.filter((x) => !sameWs(x, p) && !isChatFolder(x))].slice(0, 40);
  LS.set("recent_workspaces", JSON.stringify(list));
}
function setWorkspace(ws) {
  state.workspace = ws || "";
  LS.set("local_ai_workspace", state.workspace);
  rememberWorkspace(state.workspace);
  // Служебная папка чата (chat_files/<hex>) — это НЕ выбор пользователя: в чипе и
  // в топбаре показываем нейтральную «Выбрать папку», а не рандомный хеш (#5).
  const unset = !state.workspace || isChatFolder(state.workspace);
  const label = unset ? T("ws.notChosen") : wsName(state.workspace);
  els.wsVal.textContent = label;
  els.wsVal.parentElement.setAttribute("data-tip", unset ? T("ws.notChosen") : state.workspace);
  els.wsVal.parentElement.classList.toggle("ws-unset", unset);
  const tag = $("#ws-tag");
  if (tag) { tag.textContent = label; tag.hidden = unset; tag.title = unset ? "" : state.workspace; }
}
function chooseWorkspace(p) { if (p) send({ type: "set_workspace", workspace: p }); }
// Рабочую папку выбирают ОДИН раз — до первого сообщения; дальше она заблокирована.
function chatStarted() { return !!$(".msg-user", els.feedInner); }
function updateWorkspaceLock() {
  const b = document.getElementById("sel-workspace");
  if (!b) return;
  const locked = chatStarted();
  b.classList.toggle("ws-locked", locked);
  b.setAttribute("data-tip", locked ? T("ws.locked") : T("composer.workspace"));
}
async function pickWorkspaceNative() {
  try {
    const d = await (await fetch("/api/dialog/select-folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initial_dir: state.workspace }) })).json();
    if (d.ok && d.path) chooseWorkspace(d.path);
    else if (!d.native_window) toast(T("ev.nativeDialogUnavailable"), "error");
  } catch { toast(T("ev.nativeDialogUnavailable"), "error"); }
}
function updateMode(mode) { state.mode = mode; const m = state.modes.find((x) => x.id === mode); els.modeVal.textContent = m ? modeLabel(m, "title") : mode; }
function updateRing(tokens) {
  state.contextTokens = tokens || 0;
  const budget = state.modelContext || 120000;   // окно текущей модели, иначе дефолт
  const pct = Math.min(100, Math.round((state.contextTokens / budget) * 100));
  const c = 2 * Math.PI * 10;
  const fill = els.ctxRing.querySelector(".fill");
  fill.style.strokeDasharray = c;
  fill.style.strokeDashoffset = c * (1 - pct / 100);
  fill.style.stroke = pct > 90 ? "var(--danger)" : "var(--accent)";
  const t = els.ctxRing.querySelector("#ctx-pct"); if (t) t.textContent = pct;
  // Numbers, not just a percentage: "412K of 1M" shows at once whether the window size is
  // what the user set, and whether the count came from the provider or is an estimate.
  const used = fmtTokensShort(state.contextTokens), total = fmtTokensShort(budget);
  els.ctxRing.setAttribute("data-tip", T(state.contextExact ? "comp.contextTipExact" : "comp.contextTipEst", { pct, used, total }));
}
function setStatus(s) { const d = $("#status-dot"); if (!d) return; d.className = "status-dot" + (s === "running" || s === "waiting_approval" ? " running" : ""); }

// ------------------------------------------------------------------ запуск/стоп/steering
function setRunning(r) { state.running = r; updateSendBtn(); }
function updateSendBtn() { const has = els.input.value.trim().length > 0; const stop = state.running && !has; els.sendBtn.classList.toggle("stop", stop); els.sendBtn.innerHTML = iconSvg(stop ? "stop" : "send"); els.sendBtn.setAttribute("data-tip", stop ? T("comp.stop") : state.running ? T("comp.steer") : T("composer.run")); }
function runOptions() { return { web_mode: state.webMode, deep_research: state.deepResearch, routing: state.routing, skills: [...state.chosenSkills], attachments: state.attachments.map((a) => a.path) }; }
function submitComposer() {
  const text = els.input.value.trim();
  if (state.running && !text) { send({ type: "stop" }); return; }
  if (!text) return;
  clearWelcome(); if (!state.running) endTurn();
  els.feedInner.appendChild(userBlock(text, state.attachments.map((a) => ({ name: a.name, kind: a.kind, path: a.path, src: a.src })), state.userTurn++)); scrollFeed(true); updateWorkspaceLock();
  // A new chat shows up in the rail at once, as "New chat" until the model names it.
  if (!state.currentHasContent && !(state.sessionList || []).some((x) => x.id === state.sessionId)) {
    state.pendingChat = { id: state.sessionId, title: "Новый диалог" };
    if (!(($("#session-search")?.value) || "").trim()) renderSessions();
  }
  // Чат получил содержимое — запоминаем его как последний открытый (переживёт перезапуск).
  state.currentHasContent = true; try { LS.set("last_session_id", state.sessionId); } catch {}
  send({ type: "run", task: text, model: state.model || undefined, workspace: state.workspace, options: runOptions() });
  els.input.value = ""; autoGrow(); clearAttachments(); updateSendBtn(); hideCmdPopup();
}
// Промпт пользователя для заданного turn (из ленты).
function userPromptForTurn(turn) {
  const un = $$(".msg-user").find((n) => +n.dataset.turn === turn);
  return un ? ($(".mu-text", un)?.textContent || "") : "";
}
// Rewind: удаляем сообщение turn и всё после него (в ленте и на сервере), а его
// промпт возвращаем в строку ввода для правки. Автозапуска нет — юзер сам решит.
function rewindTo(turn, text) {
  if (state.running) { toast(T("comp.stopFirst"), "info"); return; }
  const node = $$(".msg-user").find((n) => +n.dataset.turn === turn);
  if (node) { let n = node; while (n) { const nx = n.nextElementSibling; n.remove(); n = nx; } }
  send({ type: "rewind", turn });          // сервер обрезает историю к этому turn
  state.userTurn = turn;                    // следующий ввод займёт этот же turn
  els.input.value = text || ""; autoGrow(); els.input.focus(); updateSendBtn();
  if (!$(".msg-user", els.feedInner) && !$(".msg-agent", els.feedInner)) showWelcome();
  updateWorkspaceLock();
}

// ------------------------------------------------------------------ вложения
// Тип вложения по имени/mime → ключ возможности модели.
const capName = (t) => T("cap." + t);
function attachType(name, mime) {
  mime = mime || "";
  if (mime.startsWith("image") || /\.(png|jpe?g|gif|webp|bmp|svg|heic)$/i.test(name)) return "photo";
  if (mime.startsWith("video") || /\.(mp4|mov|webm|mkv|avi|m4v)$/i.test(name)) return "video";
  if (mime.startsWith("audio") || /\.(mp3|wav|ogg|m4a|flac|aac)$/i.test(name)) return "audio";
  return "files";
}
// Отсекаем то, что текущая модель не принимает (по настройкам), с понятной ошибкой.
function filterByCaps(items) {
  const caps = currentModelCaps();
  const okList = [], blocked = new Set();
  for (const it of items) { const t = attachType(it.name, it.mime); if (caps[t]) okList.push(it); else blocked.add(t); }
  blocked.forEach((t) => toast(T("comp.capBlocked", { model: shortModel(state.model), cap: capName(t) }), "error"));
  return okList;
}
async function uploadFiles(fileList) {
  const allowed = filterByCaps([...fileList].map((f) => ({ file: f, name: f.name || "", mime: f.type || "" })));
  if (!allowed.length) return;
  fileList = allowed.map((a) => a.file);
  const fd = new FormData(); [...fileList].forEach((f) => fd.append("files", f));
  try {
    const d = await (await fetch("/api/attachments/upload", { method: "POST", body: fd })).json();
    (d.paths || []).forEach((p, i) => {
      const f = fileList[i];
      const t = attachType(f?.name || p, f?.type || "");
      const att = { path: p, name: f?.name || p.split(/[\\/]/).pop(), kind: t === "photo" ? "image" : "file" };
      // Локальный objectURL — надёжная миниатюра для фото/видео (файл в AppData
      // может быть не отдаваем через /files/).
      if ((t === "photo" || t === "video") && f) { try { att.preview = URL.createObjectURL(f); } catch {} }
      state.attachments.push(att);
    });
    (d.errors || []).forEach((e) => toast(String(e), "error")); renderAttachPreview();
  } catch { toast(T("ev.uploadFail"), "error"); }
}
async function pickNative(kind) {
  try {
    const d = await (await fetch("/api/dialog/select-files", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initial_dir: state.workspace, kind }) })).json();
    if (d.ok && d.paths?.length) {
      const items = d.paths.map((p) => ({ path: p, name: p.split(/[\\/]/).pop() }));
      filterByCaps(items).forEach((it) => state.attachments.push({ path: it.path, name: it.name, src: it.path, kind: attachType(it.name, "") === "photo" ? "image" : "file" }));
      renderAttachPreview();
    } else if (!d.native_window) els.fileInput.click();
  } catch { els.fileInput.click(); }
}
function attFileSrc(a) {
  // Путь к файлу для отдачи через /files/ (когда нет локального objectURL).
  const abs = absPath(a.rel || a.path);
  return "/files/" + abs.replace(/^[/]+/, "");
}
function fileExt(name) { const m = /\.([a-z0-9]+)$/i.exec(name || ""); return m ? m[1].toUpperCase() : "FILE"; }
// Единая карточка вложения (композер и отправленное сообщение). Фото — миниатюрой
// во всю карточку; остальное — путь-источник (если известен) + имя + бейдж типа.
function attachCard(a, removable) {
  const t = attachType(a.name, "");
  const card = el(`<div class="att-card att-${t}"></div>`);
  const fileBody = () => {
    const body = el(`<div class="att-body"></div>`);
    if (a.src) body.appendChild(el(`<div class="att-src" title="${escAttr(a.src)}">${esc(a.src)}</div>`));
    body.appendChild(el(`<div class="att-name" title="${escAttr(a.name)}">${esc(a.name)}</div>`));
    body.appendChild(el(`<span class="att-badge">${esc(fileExt(a.name))}</span>`));
    return body;
  };
  if (t === "photo") {
    const img = el(`<img class="att-thumb" alt="${escAttr(a.name)}">`);
    img.src = a.preview || (a.path || a.rel ? attFileSrc(a) : "");
    img.addEventListener("error", () => { img.remove(); card.classList.remove("att-photo"); card.appendChild(fileBody()); });
    card.appendChild(img);
  } else {
    card.appendChild(fileBody());
  }
  if (removable) {
    const rm = el(`<button class="att-x" aria-label="${escAttr(T("files.remove"))}">${iconSvg("x", "icon icon-sm")}</button>`);
    rm.addEventListener("click", () => {
      const i = state.attachments.indexOf(a);
      if (a.preview) { try { URL.revokeObjectURL(a.preview); } catch {} }
      if (i >= 0) state.attachments.splice(i, 1);
      renderAttachPreview();
    });
    card.appendChild(rm);
  }
  return card;
}
function renderAttachPreview() {
  const box = els.attachPreview;
  box.innerHTML = "";
  state.attachments.forEach((a) => box.appendChild(attachCard(a, true)));
}
function clearAttachments() {
  state.attachments.forEach((a) => { if (a.preview) { try { URL.revokeObjectURL(a.preview); } catch {} } });
  state.attachments = []; renderAttachPreview();
}

// ------------------------------------------------------------------ флаги композера + модели
function renderFlags() {
  const f = [];
  if (state.webMode === "force") f.push(T("comp.web"));
  if (state.webMode === "off") f.push(T("comp.noWeb"));
  if (state.deepResearch) f.push("research");
  if (state.chosenSkills.size) f.push(T("comp.skills", { n: state.chosenSkills.size }));
  updateRoutingUI();
  let html = f.map((t) => `<span class="flag-pill">${esc(t)}</span>`).join("");
  // Роутинг — кликабельная пилюля-тумблер на ОДИН запрос (сама возможность
  // включается в Настройках → Агент). Вкл — выбор модели прячется (модель
  // выбирает оценщик), выкл — возвращается.
  if (state.routeAvailable) {
    html += `<button type="button" class="flag-pill flag-toggle${state.routing ? " on" : ""}" id="flag-routing" data-i18n-tip="menu.routing" data-tip="${escAttr(T("menu.routing"))}">${iconSvg("cpu", "icon icon-sm")}<span>${esc(T("comp.routing"))}</span></button>`;
  }
  els.composerFlags.innerHTML = html;
  document.getElementById("flag-routing")?.addEventListener("click", () => { state.routing = !state.routing; renderFlags(); });
}
// При включённом роутинге модель выбирает оценщик — прячем ручной выбор модели.
function updateRoutingUI() {
  const sel = document.getElementById("sel-model");
  if (sel) sel.style.display = state.routing ? "none" : "";
}
async function loadModels() {
  try { const d = await (await fetch("/api/models")).json(); const list = d.models || d.data || (Array.isArray(d) ? d : []);
    const dl = $("#model-options"); if (dl && list.length) dl.innerHTML = list.slice(0, 200).map((m) => `<option value="${escAttr(m.id || m.name || m)}">${esc((m.id || m.name || m) + (m.context ? ` · ${Math.round(m.context / 1000)}k` : ""))}</option>`).join("");
  } catch {}
}

// ------------------------------------------------------------------ слэш-команды
async function loadCommands() { try { state.commands = (await (await fetch("/api/commands")).json()).commands || []; } catch {} }
// Единый popup композера: слэш-команды (в начале) и @-упоминания файлов (по каретке).
function updateCmdPopup() {
  const val = els.input.value;
  const slash = val.match(/^\/(\S*)$/);
  if (slash) { renderCmdItems(slash[1].toLowerCase()); return; }
  const at = atToken();
  if (at) { renderFileItems(at.q); return; }
  hideCmdPopup();
}
function renderCmdItems(q) {
  const matches = state.commands.filter((c) => c.name.toLowerCase().startsWith(q)).slice(0, 8);
  if (!matches.length) { hideCmdPopup(); return; }
  els.cmdPopup.innerHTML = matches.map((c, i) => `<button class="cmd-item ${i === 0 ? "active" : ""}" data-kind="cmd" data-name="${escAttr(c.name)}"><span class="cmd-name">/${esc(c.name)}</span><span class="cmd-desc grow">${esc(c.description || "")}</span></button>`).join("");
  els.cmdPopup.hidden = false;
  bindPopupItems();
}
let _fileReq = 0;
async function renderFileItems(q) {
  const token = ++_fileReq;
  let files = [];
  try { files = (await (await fetch(`/api/files/list?workspace=${encodeURIComponent(state.workspace)}&q=${encodeURIComponent(q)}`)).json()).files || []; } catch {}
  if (token !== _fileReq) return;                 // пришёл устаревший ответ
  if (!files.length) { hideCmdPopup(); return; }
  els.cmdPopup.innerHTML = files.slice(0, 12).map((f, i) => `<button class="cmd-item ${i === 0 ? "active" : ""}" data-kind="file" data-file="${escAttr(f)}"><span class="cmd-name mono">@${esc(f.split("/").pop())}</span><span class="cmd-desc grow">${esc(f)}</span></button>`).join("");
  els.cmdPopup.hidden = false;
  bindPopupItems();
}
function bindPopupItems() {
  $$(".cmd-item", els.cmdPopup).forEach((b) => b.addEventListener("mousedown", (e) => { e.preventDefault(); applyPopupItem(b); }));
}
function applyPopupItem(b) {
  if (b.dataset.kind === "file") applyMention(b.dataset.file);
  else applyCommand(b.dataset.name);
}
function hideCmdPopup() { els.cmdPopup.hidden = true; }
// @-токен перед кареткой: {q, start} где start — индекс символа '@'.
function atToken() {
  const el = els.input; const pos = el.selectionStart ?? el.value.length;
  const before = el.value.slice(0, pos);
  const m = before.match(/(?:^|\s)@([^\s@]*)$/);
  return m ? { q: m[1], start: pos - m[1].length - 1 } : null;
}
function applyCommand(name) {
  const c = state.commands.find((x) => x.name === name); if (!c) return;
  const rest = els.input.value.replace(/^\/\S*\s?/, "");
  els.input.value = /\{\{(ввод|input)\}\}/.test(c.template) ? c.template.replace(/\{\{(ввод|input)\}\}/, rest) : c.template + (rest ? "\n" + rest : "");
  hideCmdPopup(); autoGrow(); els.input.focus(); updateSendBtn();
}
function applyMention(relPath) {
  const at = atToken(); const el = els.input;
  const pos = el.selectionStart ?? el.value.length;
  const start = at ? at.start : pos;
  const mention = "@" + relPath + " ";
  el.value = el.value.slice(0, start) + mention + el.value.slice(pos);
  const caret = start + mention.length; el.setSelectionRange(caret, caret);
  // Файл — в контекст, с проверкой возможностей модели (files).
  const name = relPath.split("/").pop();
  if (filterByCaps([{ name, mime: "" }]).length && !state.attachments.some((a) => a.rel === relPath)) {
    state.attachments.push({ path: absPath(relPath), rel: relPath, name, src: absPathNative(relPath), kind: attachType(name, "") === "photo" ? "image" : "file" });
    renderAttachPreview();
  }
  hideCmdPopup(); autoGrow(); updateSendBtn(); el.focus();
}

// ------------------------------------------------------------------ меню/модалки
function buildMenuEl(items) {
  const menu = el(`<div class="menu"></div>`);
  for (const it of items) {
    if (!it) continue;
    if (it.sep) { menu.appendChild(el(`<div class="menu-sep"></div>`)); continue; }
    if (it.label) { menu.appendChild(el(`<div class="menu-label">${esc(it.label)}</div>`)); continue; }
    if (it.node) { menu.appendChild(it.node); continue; }
    const body = `<span class="grow"><span class="mi-text">${esc(it.text)}</span>${it.desc ? `<span class="mi-desc">${esc(it.desc)}</span>` : ""}</span>`;
    const right = it.num != null ? `<span class="mi-num">${it.num}</span>` : it.chosen ? iconSvg("check", "icon icon-sm") : "";
    const mi = el(`<button class="menu-item ${it.danger ? "danger" : ""} ${it.chosen ? "chosen" : ""}">${it.icon ? iconSvg(it.icon, "icon icon-sm") : ""}${body}${right}</button>`);
    mi.addEventListener("click", () => { if (!it.keepOpen) closeMenu(); it.onClick?.(); });
    menu.appendChild(mi);
  }
  return menu;
}
function openMenu(anchor, items, above) {
  closeMenu();
  const menu = buildMenuEl(items);
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 12))}px`;
  menu.style.top = above
    ? `${Math.max(8, r.top - menu.offsetHeight - 6)}px`
    : `${Math.min(r.bottom + 6, window.innerHeight - menu.offsetHeight - 8)}px`;
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
  return menu;
}
// Контекстное меню у курсора (правый клик). Позиционируется по точке, не по
// элементу, и держится в пределах окна.
function openMenuAt(x, y, items) {
  closeMenu();
  const menu = buildMenuEl(items);
  menu.classList.add("menu-context");
  document.body.appendChild(menu);
  menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 12))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 12))}px`;
  // Только клик-закрытие. Contextmenu-closer НЕ вешаем: для папки меню строится
  // синхронно в том же событии, и closer от прошлого меню тут же убил бы новое
  // (баг «открывается один раз»). Повторный ПКМ и так закрывает старое (closeMenu
  // в начале openMenuAt).
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
  return menu;
}
function closeMenu() { $(".menu")?.remove(); }
function openModal({ title, bodyHtml, footHtml, onMount, wide }) {
  const overlay = el(`<div class="overlay"><div class="modal" ${wide ? 'style="width:min(760px,100%)"' : ""}><div class="modal-head"><span class="modal-title grow">${esc(title)}</span><button class="btn-icon" data-close>${iconSvg("x")}</button></div><div class="modal-body">${bodyHtml}</div>${footHtml ? `<div class="modal-foot">${footHtml}</div>` : ""}</div></div>`);
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
  overlay.addEventListener("click", (e) => { if (e.target === overlay || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", onKey);
  els.overlayRoot.appendChild(overlay); onMount?.(overlay, close); return { overlay, close };
}
// Красивое подтверждение вместо системного confirm(). Возвращает Promise<bool>.
function confirmDialog({ title, message, confirmText, cancelText, danger } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (val) => { if (done) return; done = true; overlay.remove(); document.removeEventListener("keydown", onKey); resolve(val); };
    const overlay = el(`<div class="overlay confirm-overlay">
      <div class="confirm-box" role="alertdialog" aria-modal="true">
        <div class="confirm-title">${esc(title || T("cf.title"))}</div>
        ${message ? `<div class="confirm-msg">${esc(message)}</div>` : ""}
        <div class="confirm-actions">
          <button class="btn btn-outline" data-cancel>${esc(cancelText || T("cf.cancel"))}</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-ok>${esc(confirmText || T("cf.ok"))}</button>
        </div>
      </div></div>`);
    const onKey = (e) => { if (e.key === "Escape") finish(false); else if (e.key === "Enter") finish(true); };
    overlay.addEventListener("click", (e) => { if (e.target === overlay || e.target.closest("[data-cancel]")) finish(false); else if (e.target.closest("[data-ok]")) finish(true); });
    document.addEventListener("keydown", onKey);
    els.overlayRoot.appendChild(overlay);
    setTimeout(() => $("[data-ok]", overlay)?.focus(), 30);
  });
}

// ------------------------------------------------------------------ командная палитра (Ctrl/Cmd-K)
async function openPalette() {
  if ($(".palette-overlay")) { closePalette(); return; }
  // Статические действия — переиспользуют существующие обработчики.
  const actions = [
    { icon: "plus", label: T("app.newChat"), hint: T("h.action"), run: () => send({ type: "new_session" }) },
    state.running && { icon: "stop", label: T("pal.stop"), hint: T("h.action"), run: () => send({ type: "stop" }) },
    { icon: "trash", label: T("pal.clearChat"), hint: T("h.action"), run: async () => { if (await confirmDialog({ message: T("cf.clearChat"), danger: true })) { send({ type: "reset" }); showWelcome(); } } },
    { icon: "settings", label: T("nav.settings"), hint: T("h.open"), run: () => openSettings() },
    { icon: "brain", label: T("nav.memory"), hint: T("h.open"), run: () => openSettings("memory") },
    { icon: "slash", label: T("nav.commands"), hint: T("h.open"), run: () => openSettings("commands") },
    { icon: "key", label: T("nav.secrets"), hint: T("h.open"), run: () => openSettings("secrets") },
    { icon: "download", label: T("nav.updates"), hint: T("h.open"), run: () => openUpdate() },
    { icon: "terminal", label: T("top.terminal"), hint: T("h.panel"), run: () => togglePane("terminal") },
    { icon: "git", label: T("top.diff"), hint: T("h.panel"), run: () => togglePane("diff") },
    { icon: "globe", label: T("top.browser"), hint: T("h.panel"), run: () => togglePane("browser") },
    { icon: document.documentElement.dataset.theme === "dark" ? "sun" : "moon", label: T("pal.toggleTheme"), hint: T("h.appearance"), run: () => applyTheme(effectiveDark() ? "light" : "dark") },
  ].filter(Boolean);
  // Динамика: чаты и слэш-команды.
  let sessions = [];
  try { sessions = ((await (await fetch("/api/sessions")).json()).sessions || []).slice(0, 40); } catch {}
  const sessionItems = sessions.filter((s) => s.id !== state.sessionId).map((s) => ({ icon: "message", label: dispTitle(s.title) || T("side.untitled"), hint: T("h.chat"), run: () => send({ type: "load_session", session_id: s.id }) }));
  const cmdItems = (state.commands || []).map((c) => ({ icon: "slash", label: "/" + c.name, hint: c.description || T("h.command"), run: () => applyCommand(c.name) }));
  const all = [...actions, ...cmdItems, ...sessionItems];

  const overlay = el(`<div class="overlay palette-overlay">
    <div class="palette">
      <div class="palette-head">${iconSvg("search", "icon icon-sm")}<input class="palette-input" placeholder="${escAttr(T("pal.searchPh"))}" spellcheck="false" /></div>
      <div class="palette-list" id="pal-list"></div>
      <div class="palette-foot"><span><b>↑↓</b> ${esc(T("pal.nav"))}</span><span><b>↵</b> ${esc(T("pal.select"))}</span><span><b>esc</b> ${esc(T("pal.close"))}</span></div>
    </div></div>`);
  const listEl = $("#pal-list", overlay);
  const inputEl = $(".palette-input", overlay);
  let active = 0, view = all;

  const render = () => {
    const q = inputEl.value.trim().toLowerCase();
    view = q ? all.filter((it) => (it.label + " " + it.hint).toLowerCase().includes(q)) : all;
    if (active >= view.length) active = Math.max(0, view.length - 1);
    listEl.innerHTML = view.length
      ? view.map((it, i) => `<div class="pal-row${i === active ? " active" : ""}" data-i="${i}"><span class="pal-ico">${iconSvg(it.icon, "icon icon-sm")}</span><span class="pal-label grow truncate">${esc(it.label)}</span><span class="pal-hint">${esc(it.hint)}</span></div>`).join("")
      : `<div class="pal-empty dim">${esc(T("pal.empty"))}</div>`;
    const act = $(".pal-row.active", listEl); if (act) act.scrollIntoView({ block: "nearest" });
  };
  const move = (d) => { if (!view.length) return; active = (active + d + view.length) % view.length; render(); };
  const choose = () => { const it = view[active]; if (!it) return; closePalette(); it.run(); };

  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); choose(); }
  };
  overlay._onKey = onKey;
  inputEl.addEventListener("input", () => { active = 0; render(); });
  inputEl.addEventListener("keydown", onKey);
  listEl.addEventListener("mousemove", (e) => { const r = e.target.closest(".pal-row"); if (r && +r.dataset.i !== active) { active = +r.dataset.i; render(); } });
  listEl.addEventListener("click", (e) => { const r = e.target.closest(".pal-row"); if (r) { active = +r.dataset.i; choose(); } });
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) closePalette(); });

  els.overlayRoot.appendChild(overlay);
  render(); inputEl.focus();
}
function closePalette() { $(".palette-overlay")?.remove(); }

// ------------------------------------------------------------------ plus-menu (вложения/веб/research/навыки/пресеты)
async function openPlusMenu(anchor) {
  if (!state.skills.length) { try { state.skills = (await (await fetch("/api/skills")).json()).skills || []; } catch {} }
  // Текущее состояние маршрутизации и готовность тиров — для тумблера у ввода.
  let routeReady = false;
  try {
    const rs = await (await fetch("/api/settings")).json();
    state.routing = !!rs.model_routing;
    const jt = rs.model_tiers || {};
    routeReady = !!((jt.fast && jt.fast.model && jt.strong && jt.strong.model) || (rs.model_fast && rs.model_strong));
    state.routeAvailable = routeReady;   // пилюля-тумблер роутинга видна, когда настроен
    // Модели тиров — чтобы при роутинге проверять, можно ли прикрепить медиа.
    state.tierModels = [
      (jt.fast && jt.fast.model) || rs.model_fast,
      (jt.strong && jt.strong.model) || rs.model_strong,
      (jt.router && jt.router.model) || rs.model_router,
    ].filter(Boolean);
  } catch {}
  const webSeg = el(`<div class="menu-item" style="cursor:default"><span class="grow">${esc(T("menu.web"))}</span><div class="segmented">${[["auto", T("menu.webAuto")], ["force", T("menu.webAlways")], ["off", T("menu.webOff")]].map(([v, t]) => `<button data-web="${v}" class="${state.webMode === v ? "on" : ""}">${esc(t)}</button>`).join("")}</div></div>`);
  $$("[data-web]", webSeg).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); state.webMode = b.dataset.web; if (state.webMode === "off") state.deepResearch = false; $$("[data-web]", webSeg).forEach((x) => x.classList.toggle("on", x === b)); renderFlags(); }));
  const items = [
    { label: T("menu.attachments") },
    { text: T("menu.photoVideo"), icon: "image", onClick: () => pickNative("media") },
    { text: T("menu.filesArchives"), icon: "file", onClick: () => pickNative("any") },
    { sep: true }, { label: T("menu.modes") },
    { node: webSeg },
    { text: `${T("menu.deepResearch")}${state.deepResearch ? " — " + T("menu.on") : ""}`, icon: "flask", chosen: state.deepResearch, keepOpen: true, onClick: () => { state.deepResearch = !state.deepResearch; if (state.deepResearch && state.webMode === "off") state.webMode = "auto"; renderFlags(); closeMenu(); } },
    { text: `${T("menu.routing")}${state.routing ? " — " + T("menu.on") : ""}`, icon: "cpu", chosen: state.routing, keepOpen: true, onClick: () => {
        state.routing = !state.routing;   // на этот ответ (уходит в options.routing)
        renderFlags(); updateRoutingUI(); closeMenu();
        if (state.routing && !routeReady) { toast(T("menu.routingNeedTiers"), "info"); openSettings("agent"); }
        else if (state.routing) toast(T("menu.routingOn"));
      } },
    { text: T("menu.presets"), icon: "zap", onClick: () => openPresets(anchor) },
  ];
  if (state.skills.length) {
    items.push({ sep: true }, { label: T("menu.skills") });
    state.skills.slice(0, 12).forEach((s) => items.push({ text: s.name, icon: "spark", chosen: state.chosenSkills.has(s.name), keepOpen: true, onClick: () => { if (state.chosenSkills.has(s.name)) state.chosenSkills.delete(s.name); else state.chosenSkills.add(s.name); renderFlags(); closeMenu(); openPlusMenu(anchor); } }));
  }
  openMenu(anchor, items, true);
}

// ------------------------------------------------------------------ пресеты
async function openPresets(anchor) {
  const presets = (await (await fetch("/api/presets")).json()).presets || [];
  const items = [{ label: T("menu.presetsTitle") }];
  presets.forEach((p) => items.push({ text: `${p.name} · ${p.approval_mode}/${p.web_mode}${p.skills?.length ? "/" + p.skills.length : ""}`, icon: "zap", onClick: () => applyPreset(p) }));
  items.push({ sep: true }, { text: T("menu.savePreset"), icon: "plus", onClick: savePreset });
  openMenu(anchor, items, true);
}
function applyPreset(p) {
  if (p.model) setModel(p.model);
  send({ type: "set_mode", mode: p.approval_mode });
  state.webMode = p.web_mode || "auto"; state.deepResearch = !!p.deep_research; state.chosenSkills = new Set(p.skills || []);
  renderFlags(); toast(T("menu.presetApplied", { name: p.name }));
}
async function savePreset() {
  const name = prompt(T("menu.presetNamePrompt")); if (!name) return;
  const body = { name, model: state.model, approval_mode: state.mode, web_mode: state.webMode, deep_research: state.deepResearch, skills: [...state.chosenSkills] };
  const d = await (await fetch("/api/presets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  toast(d.ok ? T("menu.presetSaved") : d.error || T("t.error"), d.ok ? "" : "error");
}

// ------------------------------------------------------------------ экспорт / очистка
function openExport(anchor) {
  openMenu(anchor, [
    { label: T("menu.exportChat") },
    { text: "Markdown (.md)", icon: "doc", onClick: () => doExport("md") },
    { text: "HTML", icon: "doc", onClick: () => doExport("html") },
    { text: "PDF", icon: "doc", onClick: () => doExport("pdf") },
  ]);
}
async function doExport(format) {
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/export?format=${format}`);
    if (!r.ok) { toast(T("menu.exportFail"), "error"); return; }
    const blob = await r.blob(); const cd = r.headers.get("content-disposition") || "";
    const name = (cd.match(/filename="?([^"]+)"?/) || [])[1] || `chat.${format}`;
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click(); URL.revokeObjectURL(a.href);
  } catch { toast(T("menu.exportFail"), "error"); }
}

// ------------------------------------------------------------------ модель и провайдеры
const shortModel = (id) => (id || "").split("/").slice(-1)[0] || id || T("misc.model");
// Нормализуем модель к объекту {id,name,context,photo,video,audio,files}.
// Старый формат (строка-id) поддерживаем ради обратной совместимости.
function normModel(m) {
  if (typeof m === "string") return { id: m, name: "", context: 0, api_key: "", photo: true, video: false, audio: false, files: true };
  return { id: m.id || "", name: m.name || "", context: +m.context || 0, api_key: m.api_key || "", photo: m.photo !== false, video: !!m.video, audio: !!m.audio, files: m.files !== false };
}
// Token counts as people write them: "1M"/"1М"/"1 млн" -> 1000000, "200K"/"200к"/"200 тыс" ->
// 200000, "1.5m"/"1,5M" -> 1500000, "128000"/"128 000"/"1,000,000"/"1.000.000" -> as is.
// Empty or junk -> 0.
function parseTokens(v) {
  let s = String(v == null ? "" : v).trim().toLowerCase().replace(/[\s\u00a0\u202f_']/g, "");
  if (!s) return 0;
  const m = s.match(/^([\d.,]+)(k|к|тыс|thousand|m|м|млн|mln|million|b|g)?/);
  if (!m) return 0;
  let digits = m[1];
  // Groups of three after a separator are thousands ("1,000,000", "1.000.000"); a single
  // separator otherwise is the decimal point ("1.5", "1,5").
  if (/^\d{1,3}([.,]\d{3})+$/.test(digits) && !(/^\d+[.,]\d{3}$/.test(digits) && m[2])) digits = digits.replace(/[.,]/g, "");
  else digits = digits.replace(",", ".");
  const num = parseFloat(digits); if (!isFinite(num)) return 0;
  const suf = m[2] || "";
  const mult = ["k", "к", "тыс", "thousand"].includes(suf) ? 1e3 : ["m", "м", "млн", "mln", "million"].includes(suf) ? 1e6 : ["b", "g"].includes(suf) ? 1e9 : 1;
  return Math.round(num * mult);
}
// Rounded for display: 412345 -> "412K", 1500000 -> "1.5M".
function fmtTokensShort(n) {
  n = +n || 0;
  if (n >= 1e6) return (Math.round(n / 1e5) / 10) + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "K";
  return String(n);
}
// Человекочитаемо: 1000000→"1M", 200000→"200K", иначе число.
function fmtTokens(n) {
  n = +n || 0;
  if (n >= 1e6 && n % 1e5 === 0) return (n / 1e6) + "M";
  if (n >= 1e3 && n % 1e3 === 0) return (n / 1e3) + "K";
  return n ? String(n) : "";
}
function getProviders() { try { const l = JSON.parse(LS.get("providers", "")) || []; l.forEach((p) => { p.models = (p.models || []).map(normModel); }); return l; } catch { return []; } }
// The list is mirrored to the backend (core/providers.py): keys are resolved there per model,
// and the list survives another window origin (port, dev vs release build).
let _provSync = null;
function pushProviders(list) {
  clearTimeout(_provSync);
  _provSync = setTimeout(() => { fetch("/api/providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ providers: list }) }).catch(() => {}); }, 400);
}
function setProviders(list) { LS.set("providers", JSON.stringify(list)); pushProviders(list); }
async function syncProviders() {
  let server = [];
  try { server = (await (await fetch("/api/providers")).json()).providers || []; } catch { return; }
  const local = getProviders();
  if (!local.length && server.length) { LS.set("providers", JSON.stringify(server)); if (typeof renderModelPicker === "function") renderModelPicker(); }
  else if (local.length) pushProviders(local);
}
function findModel(id) { for (const p of getProviders()) { const m = (p.models || []).find((x) => x.id === id); if (m) return m; } return null; }
// Централизованный список моделей из вкладки «Модели и провайдеры» — для всех
// мест выбора модели (композер, маршрутизация). Каждый пункт несёт свой провайдер.
function providerModelOptions() {
  const opts = [];
  getProviders().forEach((p) => (p.models || []).forEach((m) => {
    if (m.id) opts.push({ value: `${p.id}::${m.id}`, group: p.name || hostName(p.base_url) || T("composer.model"), label: m.name || m.id, model: m.id, base_url: p.base_url || "", api_key: m.api_key || p.api_key || "" });
  }));
  return opts;
}
// Опции <option> для выбора модели (сгруппированы по провайдеру) — для тира и запасных.
function tierOptionsHtml(selVal) {
  const opts = providerModelOptions();
  const groups = {}; opts.forEach((o) => { (groups[o.group] = groups[o.group] || []).push(o); });
  let inner = `<option value="">${esc(T("mdl.notSelected"))}</option>`;
  for (const g in groups) inner += `<optgroup label="${escAttr(g)}">` + groups[g].map((o) => `<option value="${escAttr(o.value)}" ${o.value === selVal ? "selected" : ""}>${esc(o.label)}</option>`).join("") + `</optgroup>`;
  return inner;
}
function modelSelectHtml(id, label, selVal, help) {
  if (!providerModelOptions().length) return `<div class="form-row"><label class="form-label">${label}</label><div class="form-help">${esc(T("mdl.addFirst"))}</div></div>`;
  return `<div class="form-row"><label class="form-label">${label}</label><select class="field" id="set-${id}">${tierOptionsHtml(selVal)}</select>${help ? `<span class="form-help">${help}</span>` : ""}</div>`;
}
// Значение для предвыбора тира: из model_tiers (сервер) или плоских полей.
function tierSelectedValue(s, tier, flatKey) {
  const t = (s.model_tiers && s.model_tiers[tier]) || null;
  const model = t ? t.model : (s[flatKey] || "");
  if (!model) return "";
  const base = t ? t.base_url : "";
  const opts = providerModelOptions();
  const hit = opts.find((o) => o.model === model && (!base || o.base_url === base)) || opts.find((o) => o.model === model);
  return hit ? hit.value : "";
}
const _ALL_CAPS = { photo: true, video: true, audio: true, files: true, context: 0 };
// Способности активной модели для гейтинга вложений. При роутинге (модель
// выбирает оценщик) медиа может уйти любому тиру — разрешаем тип только если его
// поддерживают ВСЕ настроенные тир-модели, иначе прикрепить нельзя.
function currentModelCaps() {
  if (state.routing) {
    const resolved = (state.tierModels || []).map(findModel).filter(Boolean);
    if (!resolved.length) return _ALL_CAPS;
    const every = (k) => resolved.every((m) => m[k]);
    return { photo: every("photo"), video: every("video"), audio: every("audio"), files: every("files"), context: 0 };
  }
  return findModel(state.model) || _ALL_CAPS;
}
function setModel(id) {
  state.model = id || "";
  const m = findModel(id);
  state.modelContext = m && m.context ? m.context : 0;
  if (els.modelVal) els.modelVal.textContent = shortModel(state.model);
  updateRing(state.contextTokens || 0);
  updateAttachAvailability();
}
function hostName(url) { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } }
async function loadModels() {
  if (getProviders().length) return;
  let s = {}; try { s = await (await fetch("/api/settings")).json(); } catch {}
  // Модели пользователь вводит сам — НЕ подтягиваем каталог провайдера
  // автоматически (#6). Берём только уже настроенные им модели из .env.
  const models = [];
  if (s.default_model) models.push(s.default_model);
  setProviders([{ id: "default", name: hostName(s.llm_base_url) || T("misc.provider"), base_url: s.llm_base_url || "", api_key: "", models }]);
}
function openModelMenu(anchor) {
  const provs = getProviders();
  const items = [];
  if (!provs.length) items.push({ label: T("composer.model") }, { text: T("mdl.menuAdd"), icon: "plus", onClick: () => openSettings("models") });
  for (const p of provs) {
    items.push({ label: p.name });
    (p.models || []).forEach((m) => { const label = m.name || m.id; items.push({ text: label, desc: m.name && m.name !== m.id ? m.id : (m.context ? `${Math.round(m.context / 1000)}k` : ""), chosen: m.id === state.model, onClick: () => chooseModel(p, m) }); });
  }
  items.push({ sep: true }, { text: T("mdl.menuConfig"), icon: "settings", onClick: () => openSettings("models") });
  openMenu(anchor, items, true);
}
async function chooseModel(p, m) {
  setModel(m.id);
  const body = {};
  if (p && p.base_url) body.llm_base_url = p.base_url;
  if (p) { body.default_model = m.id; }
  // Ключ модели важнее ключа провайдера (пусто → базовый ключ провайдера).
  const key = (m && m.api_key) || (p && p.api_key) || "";
  if (key) body.llm_api_key = key;
  // Единый источник размера окна — контекст модели: он же задаёт бюджет
  // управления контекстом на сервере (дублирующее поле в «Агенте» убрано).
  if (m && m.context) body.context_token_budget = m.context;
  if (Object.keys(body).length) {
    try { await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); } catch {}
  }
}
// Заглушка индикации доступности вложений по возможностям модели (тултипы).
function updateAttachAvailability() {
  const caps = currentModelCaps();
  const b = $("#btn-plus"); if (b) b.setAttribute("data-tip", T("comp.attachTip") + (caps.video || caps.audio ? "" : T("comp.attachTipHint")));
}

// ------------------------------------------------------------------ акцентный цвет
const ACCENTS = [
  { key: "accent.coral", hue: null }, { key: "accent.amber", hue: 38 }, { key: "accent.blue", hue: 212 },
  { key: "accent.teal", hue: 182 }, { key: "accent.green", hue: 150 }, { key: "accent.violet", hue: 268 }, { key: "accent.rose", hue: 342 },
];
function applyAccent(hue) {
  const root = document.documentElement.style;
  if (hue == null || hue === "" ) { root.removeProperty("--accent"); root.removeProperty("--accent-contrast"); LS.set("accent_hue", ""); return; }
  const L = 55;
  root.setProperty("--accent", `hsl(${hue} 62% ${L}%)`);
  root.setProperty("--accent-contrast", L >= 62 ? "hsl(0 0% 12%)" : "hsl(0 0% 100%)");
  LS.set("accent_hue", String(hue));
}
function loadAccent() { const h = LS.get("accent_hue", ""); if (h) applyAccent(+h); }

// ------------------------------------------------------------------ иконка приложения
// Те же варианты, что и на Android (общий «Звёздыш», разные космос-фоны). Смена
// работает только в нативной оболочке Tauri (меняет иконку окна/таскбара вживую).
const APP_ICONS = ["default", "aurora", "blue", "ember", "milky", "minimal", "rose", "violet"];
function tauriInvoke() {
  const T = window.__TAURI__;
  return (T && (T.core?.invoke || T.invoke || T.tauri?.invoke)) || null;
}
function appIconSupported() { return !!tauriInvoke(); }
function currentAppIcon() { const v = LS.get("app_icon", "default"); return APP_ICONS.includes(v) ? v : "default"; }
async function setAppIcon(variant) {
  if (!APP_ICONS.includes(variant)) return;
  LS.set("app_icon", variant);
  const invoke = tauriInvoke();
  if (!invoke) return;
  try { await invoke("set_app_icon", { variant }); }
  catch (e) {
    console.error("set_app_icon", e);
    const detail = (e && (e.message || e)) ? String(e.message || e).slice(0, 140) : "";
    toast(T("appear.iconFail") + (detail ? ": " + detail : ""), "error");
  }
}
// Применяем сохранённую иконку при старте (окно открывается с дефолтной из бандла).
function loadAppIcon() { if (appIconSupported()) { const v = currentAppIcon(); if (v !== "default") setAppIcon(v); } }

// ------------------------------------------------------------------ модалки: настройки/память/команды/секреты
const SETTINGS_NAV = [
  { gkey: "set.g.settings", items: [
    { id: "models", icon: "cpu", tkey: "set.i.models" },
    { id: "agent", icon: "spark", tkey: "set.i.agent" },
    { id: "instructions", icon: "doc", tkey: "set.i.instructions" },
    { id: "search", icon: "globe", tkey: "set.i.search" },
  ] },
  { gkey: "set.g.data", items: [
    { id: "memory", icon: "brain", tkey: "set.i.memory" },
    { id: "secrets", icon: "key", tkey: "set.i.secrets" },
    { id: "commands", icon: "slash", tkey: "set.i.commands" },
  ] },
  { gkey: "set.g.extend", items: [
    { id: "skills", icon: "spark", tkey: "set.i.skills" },
    { id: "mcp", icon: "wrench", tkey: "set.i.mcp" },
  ] },
  { gkey: "set.g.browser", items: [
    { id: "browser", icon: "globe", tkey: "set.i.browser" },
  ] },
  { gkey: "set.g.devices", items: [
    { id: "pair", icon: "phone", tkey: "set.i.pair" },
  ] },
  { gkey: "set.g.appearance", items: [
    { id: "appearance", icon: "sun", tkey: "set.i.appearance" },
  ] },
  { gkey: "set.g.platform", items: [
    { id: "about", icon: "settings", tkey: "set.i.about" },
  ] },
];
// Недавно удалённые чаты — восстановление случайно удалённого (мягкое удаление
// в корзину, 30 дней). Открывается кнопкой в подвале рельса.
async function openTrash() {
  let list = [];
  try { list = (await (await fetch("/api/trash/sessions")).json()).sessions || []; } catch {}
  const body = list.length
    ? `<div class="trash-list">${list.map((s) => `<div class="trash-item" data-id="${escAttr(s.id)}"><div class="grow" style="min-width:0"><div class="trash-title truncate">${esc(dispTitle(s.title) || T("side.untitled"))}</div><div class="trash-meta">${esc(T("trash.msgs", { n: s.message_count || 0 }))}</div></div><button class="btn btn-outline btn-sm" data-restore>${esc(T("trash.restore"))}</button></div>`).join("")}</div>`
    : `<div class="empty" style="padding:24px;text-align:center;color:var(--text-3)">${esc(T("trash.empty"))}</div>`;
  const { overlay, close } = openModal({ title: T("trash.title"), bodyHtml: body });
  $$(".trash-item [data-restore]", overlay).forEach((b) => b.addEventListener("click", async () => {
    const id = b.closest(".trash-item").dataset.id;
    try {
      const r = await (await fetch(`/api/sessions/${encodeURIComponent(id)}/restore`, { method: "POST" })).json();
      if (r.ok) { toast(T("trash.restored")); close(); refreshSessions(); }
      else toast(T("t.error"), "error");
    } catch { toast(T("t.error"), "error"); }
  }));
}
async function openSettings(section = "models") {
  // Не стопкой: если настройки уже открыты (например, при смене языка переоткрываем) — закрываем старое окно.
  document.querySelectorAll(".overlay").forEach((o) => { if (o.querySelector(".settings-modal")) o.remove(); });
  let s = {}; try { s = await (await fetch("/api/settings")).json(); } catch {}
  const nav = SETTINGS_NAV.map((g) => `<div class="nav-group"><div class="nav-label">${esc(T(g.gkey))}</div>${g.items.map((n) => { const title = T(n.tkey); return `<button data-sec="${n.id}" data-title="${escAttr(title.toLowerCase())}"><span class="nav-ico">${iconSvg(n.icon, "icon icon-sm")}</span><span>${esc(title)}</span></button>`; }).join("")}</div>`).join("");
  openModal({
    wide: true,
    title: T("set.title"),
    bodyHtml: `<div class="settings-nav"><div class="nav-search">${iconSvg("search", "icon icon-sm")}<input id="set-search" placeholder="${escAttr(T("set.searchPh"))}" spellcheck="false" /></div>${nav}</div><div class="settings-main" id="settings-main"></div>`,
    onMount: (ov) => {
      ov.querySelector(".modal").classList.add("settings-modal");
      const main = $("#settings-main", ov);
      const openSec = (sec) => {
        $$(".settings-nav button", ov).forEach((b) => b.classList.toggle("active", b.dataset.sec === sec));
        renderSettingsSection(sec, main, s);
      };
      $$(".settings-nav button", ov).forEach((b) => b.addEventListener("click", () => openSec(b.dataset.sec)));
      $("#set-search", ov).addEventListener("input", (e) => {
        const q = e.target.value.trim().toLowerCase();
        $$(".settings-nav button", ov).forEach((b) => { b.hidden = q && !b.dataset.title.includes(q); });
        $$(".nav-group", ov).forEach((g) => { g.hidden = ![...g.querySelectorAll("button")].some((b) => !b.hidden); });
      });
      openSec(section);
    },
  });
}
function renderSettingsSection(sec, main, s) {
  const F = (id, label, val, type = "text", help = "") => `<div class="form-row"><label class="form-label">${label}</label><input class="field" id="set-${id}" type="${type}" value="${escAttr(val ?? "")}" />${help ? `<span class="form-help">${help}</span>` : ""}</div>`;
  const save = async (payload, reload) => { try { const d = await (await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).json(); if (d.ok) { toast(T("t.saved")); if (reload) setTimeout(() => location.reload(), 600); } else toast(d.error || T("t.error"), "error"); } catch { toast(T("t.error"), "error"); } };

  if (sec === "models") {
    const render = () => {
      const provs = getProviders();
      main.innerHTML = `<div class="settings-section"><h2>${esc(T("mdl.title"))}</h2><p class="sr-desc" style="margin-bottom:16px">${esc(T("mdl.desc"))}</p><div id="prov-list"></div><button class="btn btn-outline" id="prov-add" style="margin-top:12px">${iconSvg("plus", "icon icon-sm")} ${esc(T("mdl.add"))}</button></div>`;
      const list = $("#prov-list", main);
      const CAPS = [["photo", T("mdl.capPhoto")], ["video", T("mdl.capVideo")], ["audio", T("mdl.capAudio")], ["files", T("mdl.capFiles")]];
      provs.forEach((p, i) => {
        const card = el(`<div class="prov-card">
          <div class="row"><input class="field" data-f="name" placeholder="${escAttr(T("mdl.provName"))}" value="${escAttr(p.name)}" style="max-width:220px"/><span class="grow"></span><button class="btn-icon small" data-del data-tip="${escAttr(T("mdl.delProv"))}">${iconSvg("trash", "icon icon-sm")}</button></div>
          <input class="field" data-f="base_url" placeholder="${escAttr(T("mdl.baseUrl"))}" value="${escAttr(p.base_url)}"/>
          <input class="field" data-f="api_key" type="password" placeholder="${escAttr(T("mdl.apiKey"))}" value="${escAttr(p.api_key || "")}"/>
          <div class="prov-models-label">${esc(T("mdl.models"))}</div>
          <div class="prov-models"></div>
          <button class="btn btn-outline btn-sm" data-add-model>${iconSvg("plus", "icon icon-sm")} ${esc(T("mdl.addModel"))}</button>
        </div>`);
        const collectProv = () => { p.name = $('[data-f="name"]', card).value.trim(); p.base_url = $('[data-f="base_url"]', card).value.trim(); p.api_key = $('[data-f="api_key"]', card).value; setProviders(provs); };
        $$('[data-f]', card).forEach((inp) => inp.addEventListener("change", collectProv));
        $("[data-del]", card).addEventListener("click", () => { provs.splice(i, 1); setProviders(provs); render(); });

        const mBox = $(".prov-models", card);
        const drawModels = () => {
          mBox.innerHTML = "";
          (p.models || []).forEach((m, mi) => {
            const row = el(`<div class="model-row">
              <div class="model-row-top"><input class="field" data-m="id" placeholder="${escAttr(T("mdl.id"))}" value="${escAttr(m.id)}"/><input class="field" data-m="name" placeholder="${escAttr(T("mdl.name"))}" value="${escAttr(m.name)}"/><input class="field model-ctx" data-m="context" placeholder="${escAttr(T("mdl.ctx"))}" value="${escAttr(fmtTokens(m.context))}"/><button class="btn-icon small" data-del-model data-tip="${escAttr(T("mdl.delModel"))}">${iconSvg("trash", "icon icon-sm")}</button></div>
              <input class="field" data-m="api_key" type="password" placeholder="${escAttr(T("mdl.apiKeyModel"))}" value="${escAttr(m.api_key || "")}"/>
              <div class="model-caps">${CAPS.map(([k, lbl]) => `<label class="cap-check"><input type="checkbox" data-cap="${k}" ${m[k] ? "checked" : ""}/> ${lbl}</label>`).join("")}</div>
            </div>`);
            const collectModel = () => { m.id = $('[data-m="id"]', row).value.trim(); m.name = $('[data-m="name"]', row).value.trim(); m.context = parseTokens($('[data-m="context"]', row).value); m.api_key = $('[data-m="api_key"]', row).value; CAPS.forEach(([k]) => { m[k] = $(`[data-cap="${k}"]`, row).checked; }); setProviders(provs); };
            $$('[data-m], [data-cap]', row).forEach((inp) => inp.addEventListener("change", collectModel));
            $("[data-del-model]", row).addEventListener("click", () => { p.models.splice(mi, 1); setProviders(provs); drawModels(); });
            mBox.appendChild(row);
          });
        };
        drawModels();
        $("[data-add-model]", card).addEventListener("click", () => { p.models.push({ id: "", name: "", context: 0, api_key: "", photo: true, video: false, audio: false, files: true }); setProviders(provs); drawModels(); });
        list.appendChild(card);
      });
      $("#prov-add", main).addEventListener("click", () => { provs.push({ id: "p" + Date.now(), name: T("mdl.new"), base_url: "", api_key: "", models: [] }); setProviders(provs); render(); });
    };
    render();
  } else if (sec === "appearance") {
    const cur = document.documentElement.dataset.theme || "system";
    const accentHue = LS.get("accent_hue", "");
    const dark = effectiveDark();
    const bgStyles = dark ? [["nebula", T("appear.bgNebula")], ["amoled", T("appear.bgAmoled")]] : [["dawn", T("appear.bgDawn")], ["aurora", T("appear.bgAurora")]];
    const curBg = dark ? (window.Cosmos?.darkStyle?.() || "nebula") : (window.Cosmos?.lightStyle?.() || "dawn");
    const curLang = window.I18N?.lang?.() || "en";
    const curIcon = currentAppIcon();
    main.innerHTML = `<div class="settings-section"><h2>${esc(T("appear.title"))}</h2>
      <div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("appear.language"))}</div><div class="sr-desc">${esc(T("appear.languageDesc"))}</div></div><div class="sr-control"><div class="theme-seg" id="lang-seg">${[["en", "English"], ["ru", "Русский"]].map(([v, t]) => `<button data-lang="${v}" class="${curLang === v ? "on" : ""}">${esc(t)}</button>`).join("")}</div></div></div>
      <div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("appear.theme"))}</div><div class="sr-desc">${esc(T("appear.themeDesc"))}</div></div><div class="sr-control"><div class="theme-seg" id="theme-seg">${[["light", T("appear.light")], ["dark", T("appear.dark")], ["system", T("appear.system")]].map(([v, t]) => `<button data-t="${v}" class="${cur === v ? "on" : ""}">${esc(t)}</button>`).join("")}</div></div></div>
      <div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("appear.accent"))}</div><div class="sr-desc">${esc(T("appear.accentDesc"))}</div></div><div class="sr-control"><div class="swatches" id="swatches">${ACCENTS.map((a) => `<div class="swatch ${String(a.hue ?? "") === accentHue ? "on" : ""}" data-hue="${a.hue ?? ""}" title="${esc(T(a.key))}" style="background:${a.hue == null ? "hsl(15 56% 57%)" : `hsl(${a.hue} 62% 55%)`}"></div>`).join("")}</div></div></div>
      <div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("appear.bg"))}</div><div class="sr-desc">${esc(dark ? T("appear.bgDescDark") : T("appear.bgDescLight"))}</div></div><div class="sr-control"><div class="theme-seg" id="cosmos-seg">${bgStyles.map(([v, t]) => `<button data-bg="${v}" class="${curBg === v ? "on" : ""}">${esc(t)}</button>`).join("")}</div></div></div>
      ${appIconSupported() ? `<div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("appear.icon"))}</div><div class="sr-desc">${esc(T("appear.iconDesc"))}</div></div><div class="sr-control"><div class="icon-swatches" id="icon-swatches">${APP_ICONS.map((v) => `<button class="icon-swatch ${v === curIcon ? "on" : ""}" data-icon="${v}" title="${esc(T("appIcon." + v))}"><img src="/static/icons/variants/${v}.png?v=60" alt="${esc(v)}" /></button>`).join("")}</div></div></div>` : ""}
    </div>`;
    $$("#lang-seg button", main).forEach((b) => b.addEventListener("click", () => {
      if (window.I18N?.lang?.() === b.dataset.lang) return;
      window.I18N?.setLang(b.dataset.lang);   // сохранит + переведёт статику + событие
      openSettings("appearance");             // переоткрываем окно целиком в новом языке
    }));
    $$("#theme-seg button", main).forEach((b) => b.addEventListener("click", () => { applyTheme(b.dataset.t === "system" ? "" : b.dataset.t); renderSettingsSection("appearance", main, s); }));
    $$("#swatches .swatch", main).forEach((sw) => sw.addEventListener("click", () => { applyAccent(sw.dataset.hue === "" ? null : +sw.dataset.hue); $$("#swatches .swatch", main).forEach((x) => x.classList.toggle("on", x === sw)); }));
    $$("#cosmos-seg button", main).forEach((b) => b.addEventListener("click", () => { window.Cosmos?.setStyle(b.dataset.bg); $$("#cosmos-seg button", main).forEach((x) => x.classList.toggle("on", x === b)); }));
    $$("#icon-swatches .icon-swatch", main).forEach((b) => b.addEventListener("click", () => { setAppIcon(b.dataset.icon); $$("#icon-swatches .icon-swatch", main).forEach((x) => x.classList.toggle("on", x === b)); }));
  } else if (sec === "search") {
    main.innerHTML = `<div class="settings-section"><h2>${esc(T("web.title"))}</h2>${F("tavily_key", T("web.tavily"), "", "password", s.tavily_key_set ? T("web.setEmpty") : T("web.tavilyHint"))}${F("brave_key", T("web.brave"), "", "password", s.brave_key_set ? T("web.setEmpty") : T("web.optional"))}${F("searxng_url", T("web.searxng"), s.searxng_url, "text", T("web.searxngHint"))}<button class="btn btn-primary" id="save-search" style="margin-top:12px">${esc(T("common.save"))}</button></div>`;
    $("#save-search", main).addEventListener("click", () => { const p = { searxng_url: $("#set-searxng_url", main).value }; const tv = $("#set-tavily_key", main).value.trim(); if (tv) p.tavily_api_key = tv; const bv = $("#set-brave_key", main).value.trim(); if (bv) p.brave_api_key = bv; save(p); });
  } else if (sec === "agent") {
    const tg = (id, title, desc, on) => `<div class="setting-row"><div class="sr-main"><div class="sr-title">${title}</div><div class="sr-desc">${desc}</div></div><div class="sr-control"><div class="toggle ${on ? "on" : ""}" id="${id}"></div></div></div>`;
    main.innerHTML = `<div class="settings-section"><h2>${esc(T("ag.title"))}</h2>
      ${F("agent_language", T("ag.lang"), s.agent_language)}
      ${F("max_steps", T("ag.maxSteps"), s.max_steps, "number")}
      ${F("max_run_tokens", T("ag.maxTokens"), fmtTokens(s.max_run_tokens), "text", T("ag.maxTokensHint"))}
      ${F("max_parallel_tools", T("ag.parallel"), s.max_parallel_tools, "number")}
      ${tg("tg-compact", T("ag.compact"), T("ag.compactDesc"), s.context_compaction)}
      ${tg("tg-clear", T("ag.clearOld"), T("ag.clearOldDesc"), s.tool_result_clearing)}
      ${tg("tg-toolsearch", T("ag.toolSearch"), T("ag.toolSearchDesc"), s.tool_search)}
      ${tg("tg-verify", T("ag.verify"), T("ag.verifyDesc"), s.verification_gate)}
      ${tg("tg-sub", T("ag.sub"), T("ag.subDesc"), s.allow_subagents)}
      ${tg("tg-titles", T("ag.titles"), T("ag.titlesDesc"), s.chat_titles !== false)}
      <h3 class="sr-subhead">${esc(T("ag.tiersHead"))}</h3>
      <p class="sr-desc" style="margin:-4px 0 12px">${esc(T("ag.tiersDesc"))}</p>
      ${tg("tg-routing", T("ag.routing"), T("ag.routingDesc"), s.model_routing)}
      ${modelSelectHtml("model_fast", T("ag.fast"), tierSelectedValue(s, "fast", "model_fast"))}
      <div class="tier-fb" data-tier="fast"><div class="fb-list"></div><button type="button" class="btn btn-ghost btn-sm" data-add-fb="fast">${iconSvg("plus", "icon icon-sm")} ${esc(T("ag.addBackup"))}</button></div>
      ${modelSelectHtml("model_strong", T("ag.strong"), tierSelectedValue(s, "strong", "model_strong"))}
      <div class="tier-fb" data-tier="strong"><div class="fb-list"></div><button type="button" class="btn btn-ghost btn-sm" data-add-fb="strong">${iconSvg("plus", "icon icon-sm")} ${esc(T("ag.addBackup"))}</button></div>
      ${modelSelectHtml("model_router", T("ag.router"), tierSelectedValue(s, "router", "model_router"), T("ag.routerHint"))}
      <button class="btn btn-primary" id="save-agent" style="margin-top:12px">${esc(T("common.save"))}</button></div>`;
    const flags = { context_compaction: !!s.context_compaction, tool_result_clearing: !!s.tool_result_clearing, tool_search: !!s.tool_search, verification_gate: !!s.verification_gate, allow_subagents: !!s.allow_subagents, model_routing: !!s.model_routing, chat_titles: s.chat_titles !== false };
    const bind = (id, key) => $(id, main).addEventListener("click", (e) => { flags[key] = !flags[key]; e.currentTarget.classList.toggle("on", flags[key]); });
    bind("#tg-compact", "context_compaction"); bind("#tg-clear", "tool_result_clearing"); bind("#tg-toolsearch", "tool_search"); bind("#tg-verify", "verification_gate"); bind("#tg-sub", "allow_subagents"); bind("#tg-routing", "model_routing"); bind("#tg-titles", "chat_titles");
    const optFor = (val) => providerModelOptions().find((x) => x.value === val);
    const collectTier = (id) => { const sel = $("#set-" + id, main); if (!sel || !sel.value) return null; const o = optFor(sel.value); return o ? { model: o.model, base_url: o.base_url, api_key: o.api_key } : null; };
    // Запасные модели тира: ряды select. valueFromOpt подбирает value по model.
    const valueFromModel = (mdl) => (providerModelOptions().find((x) => x.model === mdl) || {}).value || "";
    const addFbRow = (tier, selVal) => {
      const listBox = $(`.tier-fb[data-tier="${tier}"] .fb-list`, main);
      const row = el(`<div class="fb-row"><select class="field fb-sel">${tierOptionsHtml(selVal)}</select><button type="button" class="btn-icon small" data-del-fb data-tip="${escAttr(T("mdl.delModel"))}">${iconSvg("x", "icon icon-sm")}</button></div>`);
      $("[data-del-fb]", row).addEventListener("click", () => row.remove());
      listBox.appendChild(row);
    };
    ["fast", "strong"].forEach((tier) => {
      const existing = ((s.model_tiers && s.model_tiers[tier] && s.model_tiers[tier].fallbacks) || []);
      existing.forEach((fb) => addFbRow(tier, valueFromModel(fb.model)));
      $(`[data-add-fb="${tier}"]`, main).addEventListener("click", () => addFbRow(tier, ""));
    });
    const fbOf = (tier) => [...$$(`.tier-fb[data-tier="${tier}"] .fb-sel`, main)].map((sel) => { const o = optFor(sel.value); return o ? { model: o.model, base_url: o.base_url, api_key: o.api_key } : null; }).filter(Boolean);
    $("#save-agent", main).addEventListener("click", () => {
      const tiers = {}; const f = collectTier("model_fast"), st = collectTier("model_strong"), rt = collectTier("model_router");
      if (f) { const fb = fbOf("fast"); if (fb.length) f.fallbacks = fb; tiers.fast = f; }
      if (st) { const fb = fbOf("strong"); if (fb.length) st.fallbacks = fb; tiers.strong = st; }
      if (rt) tiers.router = rt;
      const p = { agent_language: $("#set-agent_language", main).value, max_steps: Math.max(0, parseInt($("#set-max_steps", main).value, 10) || 0), max_run_tokens: parseTokens($("#set-max_run_tokens", main).value), max_parallel_tools: +$("#set-max_parallel_tools", main).value || 5, model_tiers: JSON.stringify(tiers), ...flags };
      save(p);
    });
  } else if (sec === "instructions") {
    main.innerHTML = `<div class="settings-section"><h2>${esc(T("ins.title"))}</h2>
      <p class="sr-desc" style="margin-bottom:12px">${esc(T("ins.desc"))}</p>
      <textarea class="field" id="set-custom_instructions" rows="10" placeholder="${escAttr(T("ins.ph"))}">${esc(s.custom_instructions || "")}</textarea>
      <button class="btn btn-primary" id="save-instr" style="margin-top:12px">${esc(T("common.save"))}</button></div>`;
    $("#save-instr", main).addEventListener("click", () => save({ custom_instructions: $("#set-custom_instructions", main).value }));
  } else if (sec === "memory") {
    renderMemorySection(main);
  } else if (sec === "secrets") {
    renderSecretsSection(main);
  } else if (sec === "commands") {
    renderCommandsSection(main);
  } else if (sec === "skills") {
    window.ExtensionsSettings.renderSkills(main);
  } else if (sec === "mcp") {
    window.ExtensionsSettings.renderMcp(main);
  } else if (sec === "browser") {
    const netMode = s.browser_network || "auto";
    const netBlock = `<div class="settings-section" style="margin-bottom:22px"><h2>${esc(T("brw.netTitle"))}</h2>
      <p class="sr-desc" style="margin-bottom:12px">${esc(T("brw.netDesc"))}</p>
      <div class="theme-seg" id="net-seg">${["auto", "direct", "vpn"].map((m) => `<button data-net="${m}" class="${netMode === m ? "on" : ""}">${esc(T("brw.net." + m))}</button>`).join("")}</div></div>`;
    main.innerHTML = netBlock + `<div class="settings-section"><h2>${esc(T("brw.title"))}</h2>
      <p class="sr-desc" style="margin-bottom:16px">${esc(T("brw.desc"))}</p>
      <div class="form-row"><label class="form-label">${esc(T("brw.profile"))}</label><select class="field" id="ff-profile"><option value="">${esc(T("brw.loading"))}</option></select></div>
      <div class="row" style="gap:8px;margin:8px 0"><button class="btn btn-outline" id="ff-load">${esc(T("brw.showSites"))}</button><span class="grow"></span><button class="btn btn-primary" id="ff-import" disabled>${esc(T("brw.importSel"))}</button></div>
      <div id="ff-domains" class="ff-domains dim">${esc(T("brw.pickHint"))}</div>
    </div>`;
    $$("#net-seg button", main).forEach((b) => b.addEventListener("click", () => {
      $$("#net-seg button", main).forEach((x) => x.classList.toggle("on", x === b));
      save({ browser_network: b.dataset.net });
    }));
    const sel = $("#ff-profile", main), box = $("#ff-domains", main), importBtn = $("#ff-import", main);
    (async () => {
      try {
        const d = await (await fetch("/api/firefox/profiles")).json();
        if (!d.profiles?.length) { sel.innerHTML = `<option value="">${esc(T("brw.noProfiles"))}</option>`; return; }
        sel.innerHTML = d.profiles.map((p) => `<option value="${escAttr(p.path)}">${esc(p.name)}</option>`).join("");
      } catch { sel.innerHTML = `<option value="">${esc(T("brw.error"))}</option>`; }
    })();
    const selectedDomains = () => $$("#ff-domains input:checked", main).map((i) => i.value);
    const refreshBtn = () => { importBtn.disabled = selectedDomains().length === 0; };
    $("#ff-load", main).addEventListener("click", async () => {
      if (!sel.value) return;
      box.className = "ff-domains dim"; box.textContent = T("brw.loadingSites");
      try {
        const d = await (await fetch(`/api/firefox/domains?profile=${encodeURIComponent(sel.value)}`)).json();
        if (!d.ok) { box.textContent = d.error || T("brw.cookieErr"); return; }
        if (!d.domains.length) { box.textContent = T("brw.noSites"); return; }
        box.className = "ff-domains";
        box.innerHTML = `<label class="ff-row ff-all"><input type="checkbox" id="ff-allcb"/> <b>${esc(T("brw.selectAll", { n: d.domains.length }))}</b></label>` +
          d.domains.map((x) => `<label class="ff-row"><input type="checkbox" value="${escAttr(x.domain)}"/> ${esc(x.domain)} <span class="dim">· ${x.count}</span></label>`).join("");
        $("#ff-allcb", box).addEventListener("change", (e) => { $$("#ff-domains input:not(#ff-allcb)", main).forEach((i) => i.checked = e.target.checked); refreshBtn(); });
        $$("#ff-domains input:not(#ff-allcb)", main).forEach((i) => i.addEventListener("change", refreshBtn));
      } catch { box.textContent = T("brw.loadErr"); }
    });
    importBtn.addEventListener("click", async () => {
      const domains = selectedDomains(); if (!domains.length) return;
      importBtn.disabled = true; importBtn.textContent = T("brw.importing");
      try {
        const d = await (await fetch("/api/firefox/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: sel.value, domains }) })).json();
        if (d.ok) toast(T("brw.imported", { n: d.imported })); else toast(d.error || T("t.error"), "error");
      } catch { toast(T("brw.importErr"), "error"); }
      importBtn.textContent = T("brw.importSel"); importBtn.disabled = false;
    });
  } else if (sec === "pair") {
    renderPairSection(main);
  } else if (sec === "about") {
    main.innerHTML = `<div class="settings-section"><h2>${esc(T("abt.title"))}</h2><div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("abt.version"))}</div><div class="sr-desc">${esc(state.version || "")}</div></div><div class="sr-control"><button class="btn btn-outline" id="ab-upd">${esc(T("abt.checkUpd"))}</button></div></div><div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("abt.configFile"))}</div><div class="sr-desc mono" style="overflow-wrap:anywhere">${esc(s.config_path || "")}</div></div></div><div class="setting-row"><div class="sr-main"><div class="sr-title">${esc(T("abt.tools"))}</div><div class="sr-desc">${state.tools.length}</div></div></div></div>`;
    $("#ab-upd", main).addEventListener("click", openUpdate);
  }
}
async function renderMemorySection(main) {
  main.innerHTML = `<div class="settings-section"><h2>${esc(T("mem.title"))}</h2><p class="sr-desc" style="margin-bottom:12px">${T("mem.desc")}</p><div id="mem-list" class="dim">${esc(T("mem.loading"))}</div></div>`;
  try {
    const facts = ((await (await fetch("/api/memory")).json()).facts) || [];
    const byCat = {}; facts.forEach((f) => { (byCat[f.category] = byCat[f.category] || []).push(f); });
    const box = $("#mem-list", main); box.classList.remove("dim");
    box.innerHTML = facts.length ? (Object.entries(byCat).map(([cat, list]) => `<div class="cat-label">${esc(cat)}</div>` + list.map((f) => `<div class="list-row"><span class="grow lr-title">${esc(f.text)}</span><button class="btn-icon small" data-forget="${f.id}" data-tip="${escAttr(T("side.delete"))}">${iconSvg("trash", "icon icon-sm")}</button></div>`).join("")).join("") + `<button class="btn btn-danger" id="mem-clear" style="margin-top:14px">${esc(T("mem.clear"))}</button>`) : `<div class="empty">${iconSvg("brain", "icon")}<div>${esc(T("mem.empty"))}</div></div>`;
    $$("[data-forget]", box).forEach((b) => b.addEventListener("click", async () => { await fetch(`/api/memory/${b.dataset.forget}`, { method: "DELETE" }); b.closest(".list-row").remove(); }));
    $("#mem-clear", box)?.addEventListener("click", async () => { if (await confirmDialog({ message: T("mem.clearConfirm"), danger: true })) { await fetch("/api/memory/all", { method: "DELETE" }); renderMemorySection(main); } });
  } catch { $("#mem-list", main).textContent = T("mem.loadError"); }
}
async function renderCommandsSection(main) {
  main.innerHTML = `<div class="settings-section"><h2>${esc(T("cmd.title"))}</h2><p class="sr-desc" style="margin-bottom:12px">${T("cmd.desc")}</p><div id="cmd-list"></div><div class="divider" style="margin:14px 0"></div><div class="form-row"><label class="form-label">${esc(T("cmd.new"))}</label><input class="field" id="cmd-name" placeholder="${escAttr(T("cmd.namePh"))}" /><input class="field" id="cmd-desc" placeholder="${escAttr(T("cmd.descPh"))}" /><textarea class="field" id="cmd-tpl" rows="3" placeholder="${escAttr(T("cmd.tplPh"))}"></textarea></div><button class="btn btn-primary" id="cmd-save">${esc(T("cmd.add"))}</button></div>`;
  const renderList = async () => { const list = (await (await fetch("/api/commands")).json()).commands || []; $("#cmd-list", main).innerHTML = list.map((c) => `<div class="list-row"><div class="grow"><div class="lr-title mono">/${esc(c.name)}</div><div class="lr-sub">${esc(c.description || "")}</div></div><button class="btn-icon small" data-del="${escAttr(c.name)}" data-tip="${escAttr(T("side.delete"))}">${iconSvg("trash", "icon icon-sm")}</button></div>`).join("") || `<div class="dim">${esc(T("cmd.none"))}</div>`; $$("[data-del]", main).forEach((b) => b.addEventListener("click", async () => { await fetch(`/api/commands/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); renderList(); loadCommands(); })); };
  renderList();
  $("#cmd-save", main).addEventListener("click", async () => { const name = $("#cmd-name", main).value.trim(); const template = $("#cmd-tpl", main).value.trim(); if (!name || !template) { toast(T("cmd.needNameTpl"), "error"); return; } const d = await (await fetch("/api/commands", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, description: $("#cmd-desc", main).value.trim(), template }) })).json(); if (d.ok) { $("#cmd-name", main).value = $("#cmd-desc", main).value = $("#cmd-tpl", main).value = ""; renderList(); loadCommands(); } else toast(d.error || T("t.error"), "error"); });
}
async function renderSecretsSection(main, prefill) {
  main.innerHTML = `<div class="settings-section"><h2>${esc(T("sec.title"))}</h2><p class="sr-desc" style="margin-bottom:12px">${T("sec.desc")}</p><div id="sec-list"></div><div class="divider" style="margin:14px 0"></div><div class="form-row"><label class="form-label">${esc(T("sec.new"))}</label><input class="field" id="sec-name" placeholder="${escAttr(T("sec.namePh"))}" value="${escAttr(prefill || "")}"/><input class="field" id="sec-val" type="password" placeholder="${escAttr(T("sec.valPh"))}" /></div><button class="btn btn-primary" id="sec-save">${esc(T("sec.save"))}</button></div>`;
  const renderList = async () => { const list = (await (await fetch(`/api/secrets?workspace=${encodeURIComponent(state.workspace)}`)).json()).secrets || []; $("#sec-list", main).innerHTML = list.map((s) => `<div class="list-row"><div class="grow"><div class="lr-title mono">${esc(s.name)}</div><div class="lr-sub">${esc(s.masked || "••••")}</div></div><button class="btn-icon small" data-del="${escAttr(s.name)}" data-tip="${escAttr(T("side.delete"))}">${iconSvg("trash", "icon icon-sm")}</button></div>`).join("") || `<div class="dim">${esc(T("sec.none"))}</div>`; $$("[data-del]", main).forEach((b) => b.addEventListener("click", async () => { await fetch(`/api/secrets/${encodeURIComponent(b.dataset.del)}?workspace=${encodeURIComponent(state.workspace)}`, { method: "DELETE" }); renderList(); })); };
  renderList(); if (prefill) $("#sec-val", main).focus();
  $("#sec-save", main).addEventListener("click", async () => { const name = $("#sec-name", main).value.trim(); const value = $("#sec-val", main).value; if (!name || !value) { toast(T("sec.needNameVal"), "error"); return; } const d = await (await fetch("/api/secrets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, value, workspace: state.workspace }) })).json(); if (d.ok) { $("#sec-name", main).value = $("#sec-val", main).value = ""; renderList(); } else toast(d.error || T("t.error"), "error"); });
}
// Связывание телефона: QR + ссылка altair://pair. Телефон сканирует камерой.
async function renderPairSection(main) {
  main.innerHTML = `<div class="settings-section"><h2>${esc(T("pair.title"))}</h2><div class="dim" id="pair-body">${esc(T("pair.loading"))}</div></div>`;
  const body = $("#pair-body", main);
  const load = async (rotate) => {
    body.classList.add("dim"); body.textContent = T("pair.loading");
    let d;
    try {
      const url = rotate ? "/api/pair/rotate" : `/api/pair?workspace=${encodeURIComponent(state.workspace || "")}`;
      const opts = rotate ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace: state.workspace || "" }) } : {};
      d = await (await fetch(url, opts)).json();
    } catch { body.textContent = T("pair.error"); return; }
    if (!d || !d.ok) { body.textContent = T("pair.error"); return; }
    body.classList.remove("dim");
    const qr = d.qr_svg ? `<div class="pair-qr-box">${d.qr_svg}</div>` : `<div class="pair-qr-box pair-qr-missing">${esc(T("pair.qrUnavailable"))}</div>`;
    // What the phone will really reach: the addresses the bridge listens on right now.
    const lanWarn = !d.bridge_lan
      ? `<div class="pair-warn">${iconSvg("alert", "icon icon-sm")}<span>${esc(T("pair.lanOff"))}</span></div>`
      : d.loopback_only
        ? `<div class="pair-warn">${iconSvg("alert", "icon icon-sm")}<span>${esc(T("pair.lanFailed"))}</span></div>`
        : `<div class="pair-note">${esc(T("pair.listening", { addrs: (d.listening || []).join(", ") }))}</div>`;
    const restartHint = d.bridge_lan && !d.loopback_only ? `<div id="pair-fw"></div>` : "";
    body.innerHTML = `
      <p class="sr-desc" style="margin-bottom:16px">${esc(T("pair.desc"))}</p>
      ${lanWarn}
      <div class="pair-grid">
        ${qr}
        <div class="pair-side">
          <div class="pair-field"><span class="form-label">${esc(T("pair.address"))}</span><code class="pair-addr">${esc(d.url)}</code></div>
          <div class="pair-field"><span class="form-label">${esc(T("pair.linkLabel"))}</span><code class="pair-link" id="pair-link">${esc(d.link)}</code></div>
          <div class="row" style="gap:8px;margin-top:4px">
            <button class="btn btn-outline" id="pair-copy">${iconSvg("copy", "icon icon-sm")} ${esc(T("pair.copy"))}</button>
            <button class="btn btn-ghost" id="pair-rotate">${iconSvg("refresh", "icon icon-sm")} ${esc(T("pair.regenerate"))}</button>
          </div>
        </div>
      </div>
      <ol class="pair-steps"><li>${esc(T("pair.step1"))}</li><li>${esc(T("pair.step2"))}</li><li>${esc(T("pair.step3"))}</li></ol>
      <div class="setting-row" style="border-top:1px solid var(--border);margin-top:16px;padding-top:16px">
        <div class="sr-main"><div class="sr-title">${esc(T("pair.lanToggle"))}</div><div class="sr-desc">${esc(T("pair.lanToggleDesc"))}</div></div>
        <div class="sr-control"><div class="toggle ${d.bridge_lan ? "on" : ""}" id="pair-lan"></div></div>
      </div>
      ${restartHint}`;
    $("#pair-copy", body).addEventListener("click", () => { navigator.clipboard?.writeText(d.link); toast(T("pair.copied")); });
    $("#pair-rotate", body).addEventListener("click", async () => { if (await confirmDialog({ message: T("pair.regenerateConfirm"), danger: true })) load(true).then(() => toast(T("pair.regenerated"))); });
    $("#pair-lan", body).addEventListener("click", async (e) => {
      const on = !e.currentTarget.classList.contains("on");
      e.currentTarget.classList.toggle("on", on);
      try { await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bridge_lan: on }) }); } catch { toast(T("t.error"), "error"); }
      load();
    });
    const fwBox = $("#pair-fw", body);
    if (fwBox) renderFirewall(fwBox, load);
  };
  load();
}

// Windows Firewall: a missed or cancelled first-run prompt leaves the phone timing out with no
// hint. Show it plainly and offer the one-click fix (Windows asks for confirmation itself).
async function renderFirewall(box, reload) {
  let fw;
  try { fw = await (await fetch("/api/pair/firewall")).json(); } catch { return; }
  if (!fw || !fw.supported) return;
  if (fw.allowed) { box.innerHTML = `<div class="pair-note">${esc(T("pair.fwOk"))}</div>`; return; }
  box.innerHTML = `<div class="pair-warn">${iconSvg("alert", "icon icon-sm")}<span>${esc(T(fw.blocked ? "pair.fwBlocked" : "pair.fwMissing"))}</span>
    <button class="btn btn-primary small" id="pair-fw-allow" style="margin-left:auto">${esc(T("pair.fwAllow"))}</button></div>`;
  $("#pair-fw-allow", box).addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try {
      const r = await (await fetch("/api/pair/firewall", { method: "POST" })).json();
      toast(T(r.ok ? "pair.fwDone" : "pair.fwCancelled"), r.ok ? undefined : "error");
    } catch { toast(T("t.error"), "error"); }
    reload();
  });
}

// ------------------------------------------------------------------ обзор папок
// Пикер рабочей папки — выпадающее меню (как в Claude Code): недавние папки
// сверху (галочка у текущей), «Открыть папку…» снизу открывает нативный диалог.
async function openWorkspaceMenu(anchor) {
  let recent = [];
  try { recent = (await (await fetch("/api/workspace/recent")).json()).recent || []; } catch {}
  let local = []; try { local = JSON.parse(LS.get("recent_workspaces", "[]")); } catch {}
  const all = [];
  for (const p of [state.workspace, ...local, ...recent]) {
    // Внутренние папки чата (chat_files/<hex>) в список не пускаем.
    if (p && !isChatFolder(p) && !all.some((x) => sameWs(x, p))) all.push(p);
  }
  const items = [{ label: T("ws.recent") }];
  all.slice(0, 30).forEach((p) => items.push({
    text: wsName(p), desc: p, icon: "folder",
    chosen: sameWs(p, state.workspace), onClick: () => chooseWorkspace(p),
  }));
  items.push({ sep: true }, { text: T("ws.open"), icon: "external", onClick: pickWorkspaceNative });
  openMenu(anchor, items, true);
}

// ------------------------------------------------------------------ обновления
async function checkUpdate() {
  try { const d = await (await fetch("/api/update/check")).json(); state.updateInfo = d; if (d.available) $("#btn-update").classList.add("has-update"); } catch {}
}
function openUpdate() {
  const d = state.updateInfo || {};
  const body = d.available ? `<p>${esc(T("upd.newVersion", { version: d.version || "" }))}${state.version ? esc(T("upd.youHave", { cur: state.version })) : ""}</p>${d.notes ? `<div class="md">${esc(d.notes)}</div>` : ""}` : `<div class="empty">${iconSvg("check", "icon")}<div>${esc(T("upd.upToDate"))}${state.version ? ` (${esc(state.version)})` : ""}</div></div>`;
  openModal({ title: T("upd.title"), bodyHtml: body, footHtml: d.available && d.installable ? `<span class="grow"></span><button class="btn btn-primary" id="upd-install">${esc(T("upd.install"))}</button>` : "",
    onMount: (ov) => { $("#upd-install", ov)?.addEventListener("click", async () => { toast(T("upd.installing")); try { const r = await (await fetch("/api/update/install", { method: "POST" })).json(); toast(r.ok ? T("upd.installed") : r.error || T("t.error"), r.ok ? "" : "error"); } catch { toast(T("upd.installErr"), "error"); } }); } });
}

// ------------------------------------------------------------------ toasts
function toast(text, kind = "") {
  const t = el(`<div class="toast ${kind}">${kind === "error" ? iconSvg("alert") : ""}<span>${esc(text)}</span></div>`);
  els.toasts.appendChild(t); setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 200); }, 4200);
}

// ------------------------------------------------------------------ прочее
function absPath(p) { if (/^([a-zA-Z]:[\\/]|\/)/.test(p)) return p.replace(/\\/g, "/"); return `${state.workspace}/${p}`.replace(/\\/g, "/"); }
function autoGrow() {
  const t = els.input;
  if (!t.value.trim()) { t.style.height = ""; return; } // пусто → естественная высота в одну строку
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 200) + "px";
}

// ------------------------------------------------------------------ тема
const THEME_KEY = "agent_theme";
function effectiveDark() { const t = document.documentElement.dataset.theme; if (t === "dark") return true; if (t === "light") return false; return !(window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches); }
function applyTheme(theme) {
  if (!theme || theme === "system") { delete document.documentElement.dataset.theme; LS.set(THEME_KEY, ""); }
  else { document.documentElement.dataset.theme = theme; LS.set(THEME_KEY, theme); }
  const btn = $("#btn-theme"); if (btn) btn.innerHTML = iconSvg(effectiveDark() ? "sun" : "moon", "icon icon-sm");
  window.AgentTerminal?.applyTheme?.();
  // Фон приветствия следует за темой: тёмный космос ↔ светлое небо.
  window.Cosmos?.applyTheme?.(effectiveDark());
  // Диаграммы Mermaid перерисовываем под новую тему.
  try { restyleMermaid(); } catch {}
}

// ------------------------------------------------------------------ панель-ресайз
function initPanelResize() {
  // Горизонтальный размер дока.
  const h = $("#dock-resize");
  const saved = +LS.get("local_ai_work_width", 0);
  if (saved) els.dock.style.setProperty("--dock-w", saved + "px");
  if (h) h.addEventListener("pointerdown", (e) => {
    e.preventDefault(); const start = e.clientX; const w0 = els.dock.offsetWidth; h.setPointerCapture(e.pointerId);
    const move = (ev) => { const w = Math.min(Math.max(260, w0 + (start - ev.clientX)), els.app.clientWidth - 60); els.dock.style.setProperty("--dock-w", w + "px"); };
    const up = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); LS.set("local_ai_work_width", els.dock.offsetWidth); };
    document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
  });

  // Вертикальный размер каждой панели (тянем верхний край: растёт эта, ужимается соседняя сверху).
  $$(".pane-vresize").forEach((handle) => {
    handle.addEventListener("pointerdown", (e) => {
      const pane = handle.closest(".pane");
      const open = PANES.map(paneEl).filter((p) => p && !p.hidden);
      const idx = open.indexOf(pane); if (idx <= 0) return;
      const prev = open[idx - 1];
      e.preventDefault(); handle.setPointerCapture(e.pointerId);
      const startY = e.clientY, h0 = pane.offsetHeight, p0 = prev.offsetHeight;
      const move = (ev) => {
        const d = ev.clientY - startY;
        const nh = Math.max(64, h0 - d), np = Math.max(64, p0 + d);
        pane.style.flex = `0 0 ${nh}px`; prev.style.flex = `0 0 ${np}px`;
      };
      const up = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); };
      document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
    });
  });

  // Кнопки в шапке каждой панели (развернуть / отдельным окном / закрыть).
  els.dock.addEventListener("click", (e) => {
    const btn = e.target.closest(".pane-act"); if (!btn) return;
    const id = btn.closest(".pane").dataset.pane;
    if (btn.dataset.act === "close") userClosePane(id);
    else if (btn.dataset.act === "max") maximizePane(id);
    else if (btn.dataset.act === "pop") popoutPane(id);
  });
}

// ---------------------------------------------------------- браузер в панели (static/browser.js)
// Хэндофф: агент просит вмешаться в общий браузер и ждёт «Готово».
function showHandoff(m) {
  openPane("browser");
  const body = $("#pane-browser .browser-body"); if (!body) return;
  $("#browser-handoff")?.remove();
  const banner = el(`<div class="handoff" id="browser-handoff">
    <div class="handoff-ico">${iconSvg("alert", "icon icon-sm")}</div>
    <div class="grow"><div class="handoff-title">${esc(T("handoff.title"))}</div><div class="handoff-reason">${esc(m.reason || "")}${m.hint ? " — " + esc(m.hint) : ""}</div></div>
    <button class="btn handoff-done">${esc(T("handoff.done"))}</button>
  </div>`);
  banner.querySelector(".handoff-done").addEventListener("click", () => {
    send({ type: "handoff_done", request_id: m.request_id });
    banner.remove();
  });
  body.insertBefore(banner, body.firstChild);
  toast(T("handoff.toast"), "info");
}

// ------------------------------------------------------------------ init
function init() {
  cacheEls();
  // Онбордингу и части модулей нужны глобальные ссылки (в classic-скрипте const
  // не попадает в window).
  window.send = send; window.state = state;
  window.addEventListener("i18n:changed", (e) => send({ type: "ui_lang", lang: e.detail?.lang || "en" }));
  window.I18N?.apply(document);
  // Звёздочку-бренд в рельсе заменяем на маскота Альти (статичный, без анимации).
  const brandMark = $(".brand-mark");
  if (brandMark && window.Mascot) brandMark.innerHTML = window.Mascot.svg({ size: 22, satellites: false, still: true, id: "brand" });
  applyTheme(LS.get(THEME_KEY, ""));
  loadAccent();
  loadAppIcon();
  // Тема «Система»: следим за сменой оформления ОС, чтобы фон/термина обновились.
  try {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (!LS.get(THEME_KEY, "")) applyTheme("");
    });
  } catch (e) {}
  // Смена языка UI: перерисовываем статические строки и приветствие (если открыто).
  window.addEventListener("i18n:changed", () => {
    window.I18N?.apply(document);
    if ($(".welcome", els.feedInner)) showWelcome();
    if (state.mode) updateMode(state.mode);   // ярлык режима не через data-i18n
    refreshSessions();                         // локализованные заголовки в рельсе
  });

  // Соло-режим: панель, вынесенная отдельным окном (?pane=id) — то же приложение/бэкенд.
  const soloPane = new URLSearchParams(location.search).get("pane");
  if (soloPane && PANES.includes(soloPane)) {
    document.body.classList.add("solo");
    openPane(soloPane);
  }

  els.feed.addEventListener("scroll", () => { const d = els.feed.scrollHeight - els.feed.scrollTop - els.feed.clientHeight; state.stickToBottom = d < 90; if (state.stickToBottom) els.jump.hidden = true; });
  els.jump.addEventListener("click", () => { state.stickToBottom = true; scrollFeed(true); });

  els.composer.addEventListener("submit", (e) => { e.preventDefault(); submitComposer(); });
  // Ввод держим лёгким: высота и кнопка — сразу, а popup команд/@-файлов (может
  // фетчить) — дебаунсом, чтобы не дёргать поток на каждую клавишу (это и мешало
  // плавному переключению раскладки во время печати).
  let _cmdTimer = null;
  els.input.addEventListener("input", () => {
    autoGrow(); updateSendBtn();
    clearTimeout(_cmdTimer);
    _cmdTimer = setTimeout(updateCmdPopup, 110);
  });
  els.input.addEventListener("keydown", (e) => {
    if (!els.cmdPopup.hidden && e.key === "Enter") { const a = $(".cmd-item.active", els.cmdPopup); if (a) { e.preventDefault(); applyPopupItem(a); return; } }
    if (!els.cmdPopup.hidden && e.key === "Tab") { const a = $(".cmd-item.active", els.cmdPopup); if (a) { e.preventDefault(); applyPopupItem(a); return; } }
    if (!els.cmdPopup.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); const its = $$(".cmd-item", els.cmdPopup); let i = its.findIndex((x) => x.classList.contains("active")); its[i]?.classList.remove("active"); i = (i + (e.key === "ArrowDown" ? 1 : -1) + its.length) % its.length; its[i]?.classList.add("active"); return; }
    if (e.key === "Escape") { if (!els.cmdPopup.hidden) { hideCmdPopup(); return; } if (state.running) send({ type: "stop" }); return; }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitComposer(); }
  });
  els.input.addEventListener("blur", () => setTimeout(hideCmdPopup, 150));

  $("#sel-model").addEventListener("click", (e) => openModelMenu(e.currentTarget));
  $("#btn-plus").addEventListener("click", (e) => openPlusMenu(e.currentTarget));
  els.fileInput.addEventListener("change", () => { if (els.fileInput.files.length) uploadFiles(els.fileInput.files); els.fileInput.value = ""; });
  els.composer.addEventListener("dragover", (e) => e.preventDefault());
  els.composer.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });

  $("#sel-workspace").addEventListener("click", (e) => {
    if (chatStarted()) { toast(T("ws.lockedToast"), "info"); return; }
    openWorkspaceMenu(e.currentTarget);
  });
  $("#sel-mode").addEventListener("click", (e) => openMenu(e.currentTarget, [
    { label: T("composer.mode") },
    ...state.modes.map((m, i) => ({ text: modeLabel(m, "title"), desc: modeLabel(m, "hint"), num: i + 1, chosen: m.id === state.mode, onClick: () => send({ type: "set_mode", mode: m.id }) })),
  ], true));
  $("#btn-new").addEventListener("click", () => send({ type: "new_session" }));
  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-memory").addEventListener("click", () => openSettings("memory"));
  $("#btn-commands").addEventListener("click", () => openSettings("commands"));
  $("#btn-secrets").addEventListener("click", () => openSettings("secrets"));
  $("#btn-trash").addEventListener("click", openTrash);
  $("#btn-update").addEventListener("click", openUpdate);
  $("#btn-theme").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  // Переключатели панелей (терминал / изменения / браузер) + меню «Ещё».
  $$(".dock-tgl").forEach((b) => b.addEventListener("click", () => togglePane(b.dataset.pane)));
  $("#btn-more").addEventListener("click", (e) => openMenu(e.currentTarget, [
    { text: T("menu.files"), chosen: paneVisible("artifacts"), onClick: () => togglePane("artifacts") },
    { text: T("menu.preview"), chosen: paneVisible("preview"), onClick: () => togglePane("preview") },
    { label: T("menu.dialog") },
    { text: T("menu.exportChat"), onClick: () => openExport($("#btn-more")) },
    { text: T("menu.clearChat"), onClick: async () => { if (await confirmDialog({ message: T("cf.clearChat"), danger: true })) { send({ type: "reset" }); showWelcome(); } } },
  ], false));
  $("#btn-terminal-restart")?.addEventListener("click", () => window.AgentTerminal?.onTabShown());
  initFilesTree();
  // Отразить состояние маршрутизации в чипах у ввода при старте.
  fetch("/api/settings").then((r) => r.json()).then((s) => {
    const jt = s.model_tiers || {};
    state.routeAvailable = !!((jt.fast && jt.fast.model && jt.strong && jt.strong.model) || (s.model_fast && s.model_strong));
    state.routing = !!s.model_routing && state.routeAvailable;
    state.tierModels = [(jt.fast && jt.fast.model) || s.model_fast, (jt.strong && jt.strong.model) || s.model_strong, (jt.router && jt.router.model) || s.model_router].filter(Boolean);
    renderFlags();
  }).catch(() => {});
  $("#session-search").addEventListener("input", refreshSessions);
  // The app window must never leave the app: any link that would navigate it (to this server
  // or to another scheme) is opened as a file, or the web page in a browser, instead.
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.("a[href]");
    if (!a || e.defaultPrevented || a.target === "_blank" || a.hasAttribute("download")) return;
    const href = a.getAttribute("href") || "";
    if (href.startsWith("#") || href.startsWith("javascript:void")) return;
    let url; try { url = new URL(a.href, location.href); } catch { return; }
    e.preventDefault();
    if (/^https?:$/.test(url.protocol) && url.origin !== location.origin) { window.open(url.href, "_blank", "noopener"); return; }
    openFileTarget(fileLinkTarget(href) || "");
  }, true);
  els.chatTitle.addEventListener("dblclick", () => {
    if (!state.currentHasContent) return;
    const row = els.sessions.querySelector(`.session-item[data-id="${CSS.escape(state.sessionId)}"]`);
    if (row) startRename(row, state.sessionId, els.chatTitle.textContent);
  });
  $("#rail-collapse").addEventListener("click", toggleRail);
  $("#rail-open").addEventListener("click", toggleRail);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K" || e.code === "KeyK")) { e.preventDefault(); openPalette(); }
  });

  initPanelResize();
  initWindowControls();
  updateSendBtn(); updateRing(0); renderFlags(); loadCommands(); connect();
  requestAnimationFrame(autoGrow);
  setInterval(() => send({ type: "ping" }), 25000);
  // Мастер первого запуска (язык, провайдер+ключ, папка). Показывается один раз.
  window.Onboarding?.maybe();
}
// Кнопки управления окном в нативной оболочке (Tauri, без системной рамки).
function initWindowControls() {
  const T = window.__TAURI__;
  if (!T || !T.window) return; // в браузере — обычное окно, без своих кнопок
  document.body.classList.add("tauri");
  const winctl = $("#winctl");
  if (winctl) winctl.hidden = false;
  // Имя функции менялось между версиями: v2 — getCurrentWindow, ранее — getCurrent.
  const getWin = T.window.getCurrentWindow || T.window.getCurrent;
  let appWin;
  try { appWin = getWin ? getWin() : T.window.appWindow; } catch (e) { console.error("win api", e); }
  if (!appWin) { console.error("Tauri window API недоступен"); return; }
  const guard = (p) => Promise.resolve(p).catch((e) => { console.error("win ctl", e); toast(T("win.ctlFail", { e: (e && e.message || e) }), "error"); });
  const syncMax = async () => {
    try {
      const max = await appWin.isMaximized();
      $(".win-ico-max", winctl).hidden = max;
      $(".win-ico-restore", winctl).hidden = !max;
    } catch {}
  };
  $("#win-min").addEventListener("click", () => guard(appWin.minimize()));
  $("#win-max").addEventListener("click", () => guard(appWin.toggleMaximize()).then(syncMax));
  $("#win-close").addEventListener("click", () => guard(appWin.close()));
  try { appWin.onResized(syncMax); } catch {}
  syncMax();
}
function toggleRail() {
  const mobile = window.matchMedia("(max-width: 900px)").matches;
  if (mobile) els.app.dataset.rail = els.app.dataset.rail === "open" ? "collapsed" : "open";
  else els.app.dataset.rail = els.app.dataset.rail === "collapsed" ? "expanded" : "collapsed";
  $("#rail-open").hidden = !(mobile || els.app.dataset.rail === "collapsed");
}
document.addEventListener("DOMContentLoaded", init);
