/**
 * Altair: поддержка сохранения сессий, выбора рабочей папки через проводник Windows,
 * интерактивного планирования (Manus) и управления артефактами.
 */

const els = {
  // Сайдбар
  sidebar: document.getElementById("sidebar"),
  sidebarToggle: document.getElementById("btn-sidebar-toggle"),
  sidebarOpen: document.getElementById("btn-sidebar-open"),
  sidebarBackdrop: document.getElementById("sidebar-backdrop"),
  newChatBtn: document.getElementById("btn-new-chat"),
  sessionsList: document.getElementById("sidebar-sessions-list"),
  chatTitle: document.getElementById("chat-header-title"),
  // Статус и модель
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  modelInput: document.getElementById("model-input"),
  workspace: document.getElementById("workspace-label"),
  // Фид и мониторы
  feed: document.getElementById("feed-container"),
  feedItems: document.getElementById("feed-items"),
  newMessages: document.getElementById("btn-new-messages"),
  // План
  planWidget: document.getElementById("plan-widget"),
  planCounter: document.getElementById("plan-counter"),
  planProgressBar: document.getElementById("plan-progress-bar"),
  planStepsList: document.getElementById("plan-steps-list"),
  planToggleBtn: document.getElementById("btn-plan-toggle"),
  // Вкладки монитора
  tabButtons: document.querySelectorAll(".tab-btn"),
  tabContents: document.querySelectorAll(".tab-content"),
  artifactsList: document.getElementById("artifacts-list"),
  artifactCount: document.getElementById("artifact-count"),
  previewFilename: document.getElementById("preview-filename"),
  previewBody: document.getElementById("preview-body"),
  previewActions: document.getElementById("preview-actions"),
  // Форма ввода и плашка папки
  openFolderPickerBtn: document.getElementById("btn-open-folder-picker"),
  workspaceChip: document.getElementById("workspace-chip-name"),
  modeChip: document.getElementById("mode-chip"),
  modeName: document.getElementById("mode-name"),
  modeMenu: document.getElementById("mode-menu"),
  presetChip: document.getElementById("preset-chip"),
  presetName: document.getElementById("preset-name"),
  presetMenu: document.getElementById("preset-menu"),
  workspaceError: document.getElementById("workspace-error"),
  usageMeter: document.getElementById("usage-meter"),
  memoryBtn: document.getElementById("btn-memory"),
  memoryModal: document.getElementById("memory-modal"),
  memoryList: document.getElementById("memory-list"),
  memoryCount: document.getElementById("memory-count"),
  memoryClear: document.getElementById("btn-memory-clear"),
  cmdPopup: document.getElementById("cmd-popup"),
  commandsBtn: document.getElementById("btn-commands"),
  commandsModal: document.getElementById("commands-modal"),
  commandsList: document.getElementById("commands-list"),
  secretsBtn: document.getElementById("btn-secrets"),
  secretsModal: document.getElementById("secrets-modal"),
  secretsList: document.getElementById("secrets-list"),
  secretsCount: document.getElementById("secrets-count"),
  secretNewName: document.getElementById("secret-new-name"),
  secretNewValue: document.getElementById("secret-new-value"),
  secretSaveBtn: document.getElementById("btn-secret-save"),
  commandsCount: document.getElementById("commands-count"),
  cmdNewName: document.getElementById("cmd-new-name"),
  cmdNewDesc: document.getElementById("cmd-new-desc"),
  cmdNewTemplate: document.getElementById("cmd-new-template"),
  cmdSaveBtn: document.getElementById("btn-cmd-save"),
  // Меню «плюс»
  plusBtn: document.getElementById("btn-plus"),
  plusMenu: document.getElementById("plus-menu"),
  plusSkills: document.getElementById("plus-skills"),
  webSegments: document.getElementById("web-mode-segments"),
  deepState: document.getElementById("deep-state"),
  attachList: document.getElementById("attach-list"),
  composerFlags: document.getElementById("composer-flags"),
  // Модалка обзора папок
  folderModal: document.getElementById("folder-modal"),
  fmPath: document.getElementById("fm-path"),
  fmList: document.getElementById("fm-list"),
  fmQuick: document.getElementById("fm-quick"),
  fmStatus: document.getElementById("fm-status"),
  fmSelected: document.getElementById("fm-selected"),
  fmUp: document.getElementById("fm-up"),
  fmGo: document.getElementById("fm-go"),
  fmNative: document.getElementById("fm-native"),
  fmChoose: document.getElementById("fm-choose"),
  form: document.getElementById("task-form"),
  input: document.getElementById("task-input"),
  submit: document.getElementById("submit-btn"),
  stop: document.getElementById("stop-btn"),
  clearChat: document.getElementById("btn-clear-chat"),
  exportBtn: document.getElementById("btn-export"),
  exportMenu: document.getElementById("export-menu"),
  layout: document.getElementById("app-layout"),
  themeBtn: document.getElementById("btn-theme"),
  workClose: document.getElementById("btn-work-close"),
  workToggles: document.getElementById("work-toggles"),
  workBackdrop: document.getElementById("work-backdrop"),
  workResize: document.getElementById("work-resize-handle"),
  settingsBtn: document.getElementById("btn-settings"),
  settingsModal: document.getElementById("settings-modal"),
  settingsNote: document.getElementById("settings-note"),
  settingsPath: document.getElementById("settings-path"),
  saveSettingsBtn: document.getElementById("btn-save-settings"),
  setupBanner: document.getElementById("setup-banner"),
  setupOpen: document.getElementById("btn-setup-open"),
  apiKeyHint: document.getElementById("api-key-hint"),
  updateBtn: document.getElementById("btn-update"),
  versionLabel: document.getElementById("version-label"),
  updateNote: document.getElementById("update-note"),
};

const state = {
  socket: null,
  reconnectTimer: null,
  currentSessionId: null,
  currentWorkspace: localStorage.getItem("local_ai_workspace") || "",
  stickyWorkspace: localStorage.getItem("local_ai_workspace") || "",
  currentTitle: "Новый диалог",
  answerBlock: null,
  answerText: "",
  reconnectNode: null,
  contextTokens: 0,
  toolBlocks: new Map(),
  thinking: null,
  stepGroup: null,
  activity: null,
  activityTimer: null,
  lastTask: "",
  stickToBottom: true,
  previewMode: "page",
  previewFile: null,
  mode: "manual",
  modes: [],
  artifacts: new Map(),
  running: false,
  planCollapsed: true,
  // Порядковый номер следующего запроса пользователя (для «Повторить»).
  userTurn: 0,
  // Меню «плюс»: вложения и переключатели текущего запроса.
  attachments: [],
  webMode: localStorage.getItem("local_ai_web_mode") || "auto",
  deepResearch: false,
  skills: [],
  chosenSkills: new Set(),
  // Пути файлов, у которых есть снимок для отката.
  undoable: new Set(),
  models: [],
  presets: [],
  commands: [],
};

function showWorkspaceError(message) {
  if (!els.workspaceError) return;
  if (!message) {
    els.workspaceError.hidden = true;
    els.workspaceError.textContent = "";
    return;
  }
  els.workspaceError.hidden = false;
  els.workspaceError.textContent = message;
}

function updateWorkspaceUI(ws, { sticky = true } = {}) {
  if (!ws) return;
  state.currentWorkspace = ws;
  // «Липкой» (запоминаемой для новых чатов) делаем только выбранную вручную папку
  // проекта. Авто-папку чата не запоминаем — иначе следующие чаты потеряют свою.
  if (sticky) {
    state.stickyWorkspace = ws;
    localStorage.setItem("local_ai_workspace", ws);
  }
  showWorkspaceError("");
  if (els.workspace) els.workspace.textContent = ws;
  if (els.workspaceChip) {
    // На бирке — только имя папки, полный путь остаётся в подсказке и шапке.
    const parts = ws.split(/[\\/]/).filter(Boolean);
    els.workspaceChip.textContent = parts[parts.length - 1] || ws;
    els.workspaceChip.parentElement.title = ws;
  }
}

// ---------------------------------------------------------------- markdown

function escapeHTML(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

if (typeof marked !== "undefined") {
  const renderer = new marked.Renderer();
  renderer.link = function ({ href, title, text }) {
    const titleAttr = title ? ` title="${escapeHTML(title)}"` : "";
    return `<a href="${escapeHTML(href)}" target="_blank" rel="noopener noreferrer"${titleAttr}>${text}</a>`;
  };
  marked.setOptions({
    renderer: renderer,
    gfm: true,
    breaks: true,
    pedantic: false,
  });
}

function fallbackMarkdown(text) {
  const blocks = [];
  let html = escapeHTML(text).replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code class="language-${lang}">${code.replace(/\n$/, "")}</code></pre>`);
    return `@@CODE${blocks.length - 1}@@`;
  });

  html = html
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/^###\s+(.*)$/gm, "<h3>$1</h3>")
    .replace(/^##\s+(.*)$/gm, "<h2>$1</h2>")
    .replace(/^#\s+(.*)$/gm, "<h1>$1</h1>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/^\s*[-*]\s+(.*)$/gm, "<li>$1</li>")
    .replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, "<ul>$1</ul>")
    .replace(/\n{2,}/g, "<br><br>")
    .replace(/\n/g, "<br>");

  return html.replace(/@@CODE(\d+)@@/g, (_, index) => blocks[Number(index)]);
}

function renderMarkdown(text) {
  if (!text) return "";
  let processedText = String(text);
  const codeBlockCount = (processedText.match(/```/g) || []).length;
  if (codeBlockCount % 2 !== 0) processedText += "\n```";

  const mathStore = [];
  processedText = maskMath(processedText, mathStore);

  let html;
  if (typeof marked !== "undefined" && marked.parse) {
    try {
      html = marked.parse(processedText);
    } catch {
      html = fallbackMarkdown(processedText);
    }
  } else {
    html = fallbackMarkdown(processedText);
  }
  return unmaskMath(html, mathStore);
}

/** Токены человекочитаемо: 31030 -> «31.0к». */
function formatTokens(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}к` : String(n);
}

/** Стоимость: очень маленькие суммы не округляем в ноль. */
function formatCost(usd) {
  if (!usd) return "";
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}

// ---------------------------------------------- кольцо контекстного окна

const CONTEXT_WINDOWS_KEY = "local_ai_context_windows";
const DEFAULT_CONTEXT_WINDOW = 128000;

/** «256k», «1M», «128к», «200000» -> число токенов (или null). */
function parseSize(raw) {
  const s = String(raw || "").trim().toLowerCase().replace(/[\s_]/g, "").replace(",", ".");
  if (!s) return null;
  const m = s.match(/^([\d.]+)([kmкм]?)$/);
  if (!m) return null;
  const n = parseFloat(m[1]);
  if (!isFinite(n)) return null;
  const mult = { k: 1e3, "к": 1e3, m: 1e6, "м": 1e6 }[m[2]] || 1;
  return Math.round(n * mult);
}

/** Число -> компактно: 128000 -> «128k», 1000000 -> «1M». */
function formatSize(n) {
  if (!n) return "0";
  if (n >= 1e6) return `${(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n % 1e3 ? 1 : 0)}k`;
  return String(n);
}

function contextWindowsMap() {
  try { return JSON.parse(localStorage.getItem(CONTEXT_WINDOWS_KEY) || "{}"); }
  catch { return {}; }
}

function currentModelName() {
  return (els.modelInput && els.modelInput.value.trim()) || state.currentModel || "default";
}

function getContextWindow(model) {
  const map = contextWindowsMap();
  return map[model || currentModelName()] || map.default || DEFAULT_CONTEXT_WINDOW;
}

function setContextWindow(model, size) {
  const map = contextWindowsMap();
  map[model] = size;
  localStorage.setItem(CONTEXT_WINDOWS_KEY, JSON.stringify(map));
}

/** Перерисовывает кольцо: заполнение = токены чата / размер окна модели. */
function updateContextRing() {
  const ring = document.getElementById("context-ring");
  if (!ring) return;
  const fill = ring.querySelector(".ring-fill");
  const label = ring.querySelector(".ring-label");
  const tokens = state.contextTokens || 0;
  const window = getContextWindow();
  const frac = Math.max(0, Math.min(1, tokens / window));
  const C = 2 * Math.PI * 15.5; // длина окружности r=15.5
  if (fill) {
    fill.style.strokeDasharray = `${C}`;
    fill.style.strokeDashoffset = `${C * (1 - frac)}`;
  }
  const pct = Math.round(frac * 100);
  if (label) label.textContent = `${pct}%`;
  ring.dataset.level = frac >= 0.9 ? "full" : frac >= 0.7 ? "warn" : "ok";
  ring.title =
    `Контекст: ${formatSize(tokens)} / ${formatSize(window)} токенов (${pct}%). ` +
    `Клик — задать размер окна для модели «${currentModelName()}».`;
}

/** Текст ответа без служебного подвала — то, что уходит в буфер. */
function answerPlainText(block) {
  const clone = block.cloneNode(true);
  clone.querySelectorAll(".answer-footer").forEach((footer) => footer.remove());
  return clone.innerText.trim();
}

/** Подвал ответа: сводка о прогоне слева, кнопка копирования справа.
 *
 *  Раньше кнопка висела абсолютом поверх первой строки и перекрывала текст
 *  («solve_math (S» вместо «(SymPy)»). В подвале она никому не мешает и
 *  находится там, где её и ищут — под ответом.
 */
function addAnswerFooter(block, meta = {}) {
  if (block.querySelector(".answer-footer")) return;

  const footer = document.createElement("div");
  footer.className = "answer-footer";

  const summary = document.createElement("span");
  summary.className = "run-summary";
  if (meta.duration_ms) {
    const seconds = (meta.duration_ms / 1000).toFixed(meta.duration_ms < 10000 ? 1 : 0);
    const tokens = meta.usage && meta.usage.total_tokens
      ? ` · ${formatTokens(meta.usage.total_tokens)} токенов` : "";
    const cost = meta.cost_usd ? ` · ${formatCost(meta.cost_usd)}` : "";
    summary.textContent = `${seconds} с · шагов: ${meta.steps || 0}${tokens}${cost}`;
  }
  footer.appendChild(summary);

  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn-copy-answer";
  button.title = "Скопировать ответ";
  button.textContent = "Копировать";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(answerPlainText(block));
      button.textContent = "Скопировано";
      setTimeout(() => (button.textContent = "Копировать"), 2000);
    } catch {
      button.textContent = "Не вышло";
    }
  });
  footer.appendChild(button);

  block.appendChild(footer);
}

// Кэш подобранных картинок: запрос -> Promise<url|null> (переживает пере-рендеры
// при стриминге, поэтому один и тот же запрос ищется на сервере только раз).
const inlineImageCache = new Map();

function _resolveImageQuery(query) {
  if (inlineImageCache.has(query)) return inlineImageCache.get(query);
  const promise = fetch(`/api/find-image?q=${encodeURIComponent(query)}`)
    .then((r) => r.json())
    .then((d) => (d && d.ok && d.url ? { url: d.url, title: d.title || "" } : null))
    .catch(() => null);
  inlineImageCache.set(query, promise);
  return promise;
}

/** Подставляет реальные картинки на месте маркеров ![подпись](<img:запрос>). */
function resolveInlineImages(container) {
  container.querySelectorAll('img[src^="img:"]').forEach((img) => {
    let query = img.getAttribute("src").slice(4).trim();
    try { query = decodeURIComponent(query); } catch { /* оставить как есть */ }
    if (!query) { img.remove(); return; }
    const caption = img.getAttribute("alt") || "";
    // Пока ищем — прячем «битую» иконку.
    img.dataset.imgQuery = query;
    img.style.display = "none";

    _resolveImageQuery(query).then((res) => {
      if (!img.isConnected) return;
      if (!res) { img.remove(); return; }
      const fig = document.createElement("figure");
      fig.className = "answer-inline-image";
      const real = document.createElement("img");
      real.src = res.url;
      real.loading = "lazy";
      real.alt = caption || res.title || query;
      real.addEventListener("error", () => fig.remove());
      fig.appendChild(real);
      if (caption) {
        const cap = document.createElement("figcaption");
        cap.textContent = caption;
        fig.appendChild(cap);
      }
      img.replaceWith(fig);
    });
  });
}

function postProcessMarkdown(container) {
  if (!container) return;
  renderMath(container);
  resolveInlineImages(container);
  if (typeof hljs !== "undefined") {
    container.querySelectorAll("pre code").forEach((codeEl) => {
      if (!codeEl.dataset.highlighted) {
        hljs.highlightElement(codeEl);
        codeEl.dataset.highlighted = "yes";
      }
    });
  }

  container.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".code-header")) return;

    const code = pre.querySelector("code");
    let lang = "";
    if (code) {
      const match = code.className.match(/language-([a-zA-Z0-9_-]+)/);
      if (match) lang = match[1];
    }

    const header = document.createElement("div");
    header.className = "code-header";

    const langLabel = document.createElement("span");
    langLabel.className = "code-lang";
    langLabel.textContent = lang || "code";

    const copyBtn = document.createElement("button");
    copyBtn.className = "btn-copy-code";
    copyBtn.textContent = "Копировать";
    copyBtn.type = "button";
    copyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const codeText = code ? code.innerText : pre.innerText;
      try {
        await navigator.clipboard.writeText(codeText);
        copyBtn.textContent = "Скопировано!";
        copyBtn.classList.add("copied");
        setTimeout(() => {
          copyBtn.textContent = "Копировать";
          copyBtn.classList.remove("copied");
        }, 2000);
      } catch {
        copyBtn.textContent = "Ошибка";
      }
    });

    header.appendChild(langLabel);
    header.appendChild(copyBtn);
    pre.insertBefore(header, pre.firstChild);
  });
}

// ---------------------------------------------------------------- feed utils

/** Разворачивает или сворачивает блок с деталями. */
function toggleBlock(node, bodySelector, caretSelector, force) {
  const body = node.querySelector(bodySelector);
  const open = force !== undefined ? force : body.hidden;
  body.hidden = !open;
  node.classList.toggle("is-open", open);
  const caret = node.querySelector(caretSelector);
  if (caret) caret.textContent = open ? "▾" : "▸";
}

/** Открывает группу шагов или возвращает текущую.
 *
 *  Зачем: между двумя репликами агент делает 5-15 вызовов. Развёрнутый список
 *  оттесняет сам ответ за пределы экрана, поэтому пачка сворачивается в одну
 *  строку — как папка в файловом менеджере.
 */
function ensureStepGroup() {
  if (state.stepGroup) return state.stepGroup;

  const node = appendFeed(
    `<button type="button" class="group-toggle" hidden>
       <span class="group-caret">▾</span>
       <span class="group-label"></span>
     </button>
     <div class="group-items"></div>`,
    "step-group",
  );

  const group = {
    node,
    items: node.querySelector(".group-items"),
    toggle: node.querySelector(".group-toggle"),
    count: 0,
    started: Date.now(),
    failed: 0,
  };

  group.toggle.addEventListener("click", () => {
    const collapsed = group.items.hidden;
    group.items.hidden = !collapsed;
    node.classList.toggle("is-collapsed", !collapsed);
    node.querySelector(".group-caret").textContent = collapsed ? "▾" : "▸";
  });

  state.stepGroup = group;
  return group;
}

/** Закрывает группу: показывает итоговую строку и сворачивает длинные пачки. */
function finishStepGroup() {
  const group = state.stepGroup;
  state.stepGroup = null;
  if (!group) return;

  if (group.count === 0) {
    group.node.remove();
    return;
  }

  const seconds = Math.round((Date.now() - group.started) / 1000);
  const failures = group.failed ? ` · ошибок: ${group.failed}` : "";
  const time = seconds > 1 ? ` · ${seconds} с` : "";
  group.node.querySelector(".group-label").textContent =
    `${group.count} ${pluralSteps(group.count)}${time}${failures}`;
  group.toggle.hidden = false;
  group.node.classList.toggle("has-failures", group.failed > 0);

  // Короткие пачки оставляем открытыми: сворачивать два шага незачем.
  if (group.count >= 3) {
    group.items.hidden = true;
    group.node.classList.add("is-collapsed");
    group.node.querySelector(".group-caret").textContent = "▸";
  }
}

function pluralSteps(count) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "шаг";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "шага";
  return "шагов";
}

/** Строка шага. Используется и в прямом эфире, и при открытии сохранённого чата. */
function renderStepRow(name, args, result) {
  const group = ensureStepGroup();
  const node = document.createElement("div");
  node.className = "step";

  const status = result
    ? `<span class="step-status ${result.ok ? "success" : "failed"}">${
        result.ok ? `${result.duration_ms} мс` : "ошибка"
      }</span>`
    : `<span class="step-status running">выполняется…</span>`;

  node.innerHTML =
    `<button class="step-row" type="button">
       <span class="step-caret">▸</span>
       <span class="step-name">${escapeHTML(name)}</span>
       <span class="step-hint">${escapeHTML(describeArgs(args))}</span>
       ${status}
     </button>
     <div class="step-body" hidden>
       <div class="tool-args"><pre>${escapeHTML(JSON.stringify(args || {}, null, 2))}</pre></div>
       <div class="tool-output"><pre>${escapeHTML(result ? result.output || "" : "ожидание…")}</pre></div>
     </div>`;

  group.items.appendChild(node);
  group.count += 1;
  if (result && !result.ok) group.failed += 1;
  scrollFeed();
  return node;
}

/** Сворачивает размышления, когда агент перешёл к действию или ответу. */
function closeThinking() {
  if (!state.thinking) return;
  state.thinking.node.querySelector(".thinking-label").textContent = "Размышления";
  state.thinking = null;
}

function appendFeed(html, className) {
  const node = document.createElement("div");
  node.className = className;
  node.innerHTML = html;
  els.feedItems.appendChild(node);
  scrollFeed();
  return node;
}

/** Сообщение пользователя с кнопкой «Повторить»: перезапуск этого же запроса
 *  (можно другой моделью), с откатом истории к этой точке. Порядковый номер
 *  запроса (turn) должен совпадать со счётом на сервере. */
function appendUserMessage(text, extraHtml = "") {
  const turn = state.userTurn++;
  const node = appendFeed(escapeHTML(text) + extraHtml, "user-message");
  node.dataset.turn = String(turn);
  node.dataset.task = text; // сырой текст, без экранирования

  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "btn-retry";
  retry.title = "Повторить этот запрос (текущей моделью)";
  retry.textContent = "↻ Повторить";
  retry.addEventListener("click", () => rerunTask(node));
  node.appendChild(retry);
  return node;
}

/** Перезапускает запрос из сообщения: обрезает ленту и историю до него. */
function rerunTask(node) {
  if (state.running) return;
  const turn = Number(node.dataset.turn);
  const task = node.dataset.task || "";
  if (!task) return;

  // Убираем это сообщение и всё, что после него: сейчас соберём заново.
  while (els.feedItems.lastChild && els.feedItems.lastChild !== node) {
    els.feedItems.removeChild(els.feedItems.lastChild);
  }
  els.feedItems.removeChild(node);
  state.userTurn = turn; // следующее сообщение займёт тот же номер

  if (!send({
    type: "run",
    task,
    rerun_turn: turn,
    model: els.modelInput.value.trim() || undefined,
    workspace: state.currentWorkspace || undefined,
    options: runOptions(),
  })) return;

  appendUserMessage(task);
  setRunning(true);
  setStatus("Выполняет задачу", "working");
}

function logLine(text, level = "info") {
  // Журнал убран из интерфейса, но входящие служебные события не должны ломать UI.
  void text;
  void level;
}

function setStatus(text, dotClass) {
  els.statusText.textContent = text;
  els.statusDot.className = `status-dot ${dotClass}`;
}

/** Прокручиваем вниз, только если пользователь и так внизу: иначе чтение
 *  старого сообщения прерывалось бы рывком при каждом новом токене. */
function scrollFeed(force = false) {
  if (!force && !state.stickToBottom) {
    els.newMessages.hidden = false;
    return;
  }
  els.feed.scrollTop = els.feed.scrollHeight;
  els.newMessages.hidden = true;
}

function updateStickiness() {
  const distance = els.feed.scrollHeight - els.feed.scrollTop - els.feed.clientHeight;
  state.stickToBottom = distance < 80;
  if (state.stickToBottom) els.newMessages.hidden = true;
}

// Рендер ответа во время стрима — не на КАЖДЫЙ токен (это O(n²): полный разбор
// Markdown всей накопленной строки на каждый чанк — заметно тормозит на длинных
// ответах), а не чаще одного раза за кадр (requestAnimationFrame их коалесцирует).
let _answerRenderPending = false;
function scheduleAnswerRender() {
  if (_answerRenderPending) return;
  _answerRenderPending = true;
  requestAnimationFrame(() => {
    _answerRenderPending = false;
    if (!state.answerBlock) return; // блок сменился (пошёл инструмент) — пропускаем
    state.answerBlock.innerHTML = renderMarkdown(state.answerText);
    postProcessMarkdown(state.answerBlock);
    scrollFeed();
  });
}

/** Живой индикатор: что агент делает прямо сейчас и сколько это уже длится.
 *  Долгое ожидание без обратной связи читается как зависание. */
function setActivity(text) {
  if (!state.activity) {
    state.activity = appendFeed(
      `<span class="activity-dot"></span><span class="activity-text"></span><span class="activity-time"></span>`,
      "activity",
    );
    state.activity.dataset.started = String(Date.now());
    state.activityTimer = setInterval(() => {
      if (!state.activity) return;
      const seconds = Math.round((Date.now() - Number(state.activity.dataset.started)) / 1000);
      state.activity.querySelector(".activity-time").textContent = seconds > 2 ? `${seconds} с` : "";
    }, 1000);
  }
  state.activity.querySelector(".activity-text").textContent = text;
  els.feedItems.appendChild(state.activity); // индикатор всегда последний
  scrollFeed();
}

//: Человеческие названия фаз исследования (событие research.progress).
const RESEARCH_PHASES = {
  plan: "Планирую поиск",
  search: "Ищу источники",
  read: "Читаю",
  digest: "Конспектирую",
  report: "Собираю отчёт",
};

function clearActivity() {
  if (state.activityTimer) clearInterval(state.activityTimer);
  state.activityTimer = null;
  if (state.activity) state.activity.remove();
  state.activity = null;
}

/** Убирает карточку переподключения: связь восстановлена или задача завершилась. */
function clearReconnect() {
  if (state.reconnectNode) {
    state.reconnectNode.remove();
    state.reconnectNode = null;
  }
}

function setEmptyState(empty) {
  document.querySelector(".chat-section").classList.toggle("is-empty", empty);
}

function showWelcome() {
  setEmptyState(true);
  state.userTurn = 0; // пустой чат — нумерация запросов с нуля
  els.feedItems.innerHTML =
    `<div class="welcome"><h2>Что сделать в этой папке?</h2>` +
    `<p>Агент читает и правит файлы, запускает тесты и ищет в интернете — ` +
    `строго внутри выбранной рабочей папки.</p></div>`;
}

function clearWelcome() {
  const welcome = els.feedItems.querySelector(".welcome");
  if (welcome) welcome.remove();
  setEmptyState(false);
}

// Одна кнопка на два состояния: пока задача идёт, она останавливает её.
// Две отдельные кнопки рядом — лишний шум и источник ошибочных нажатий.
function setRunning(running) {
  state.running = running;
  els.submit.classList.toggle("is-stop", running);
  els.submit.querySelector(".btn-run__label").textContent = running ? "Стоп" : "Запустить";
  els.submit.title = running ? "Остановить выполнение" : "Запустить задачу";
  els.input.disabled = false;
}

/** Короткое описание вызова: самый информативный аргумент инструмента. */
function describeArgs(args) {
  if (!args || typeof args !== "object") return "";
  const meaningless = new Set([".", "./", "", "*"]);
  for (const key of ["path", "file", "query", "command", "pattern", "name", "url", "destination"]) {
    const value = args[key];
    // "." как путь — это «вся рабочая папка»: подсказка из одной точки только шумит.
    if (typeof value === "string" && value.trim() && !meaningless.has(value.trim())) {
      return value.trim();
    }
  }
  const first = Object.values(args).find((v) => typeof v === "string" && v.trim());
  return first ? String(first).slice(0, 80) : "";
}

function send(payload) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify(payload));
    return true;
  }
  logLine("Нет соединения с сервером.", "error");
  return false;
}

function humanSize(bytes) {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

// ------------------------------------------------------------ рендер плана

function renderPlan(steps) {
  if (!steps || steps.length === 0) {
    els.planWidget.style.display = "none";
    return;
  }

  els.planWidget.style.display = "block";
  const total = steps.length;
  const completed = steps.filter((s) => s.status === "completed").length;
  const percent = Math.round((completed / total) * 100);

  els.planCounter.textContent = `${completed}/${total}`;
  els.planProgressBar.style.width = `${percent}%`;

  const icons = {
    completed: `<svg class="step-icon completed" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>`,
    in_progress: `<svg class="step-icon in_progress" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2.5"><circle cx="12" cy="12" r="10" stroke-dasharray="32" stroke-dashoffset="12"></circle></svg>`,
    pending: `<svg class="step-icon pending" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2"><circle cx="12" cy="12" r="9"></circle></svg>`,
    failed: `<svg class="step-icon failed" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`,
  };

  els.planStepsList.innerHTML = steps
    .map((step, idx) => {
      const icon = icons[step.status] || icons.pending;
      return `<div class="plan-step-item ${escapeHTML(step.status)}">
        ${icon}
        <span class="step-text"><b>${idx + 1}.</b> ${escapeHTML(step.title)}</span>
      </div>`;
    })
    .join("");
}

// ---------------------------------------------------------- рендер артефактов

function renderArtifacts() {
  const count = state.artifacts.size;
  els.artifactCount.textContent = String(count);
  // Дублируем счётчик на кнопку в шапке (скрываем бейдж при нуле).
  const topBadge = document.getElementById("artifact-count-top");
  if (topBadge) {
    topBadge.textContent = String(count);
    topBadge.dataset.zero = count === 0 ? "1" : "0";
  }

  if (count === 0) {
    els.artifactsList.innerHTML = `<div class="empty-state"><p>Артефакты ещё не созданы.</p></div>`;
    return;
  }

  const kindIcons = {
    code: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>`,
    markdown: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>`,
    image: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>`,
    data: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg>`,
    file: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>`,
  };

  els.artifactsList.innerHTML = Array.from(state.artifacts.values())
    .reverse()
    .map((art) => {
      const icon = kindIcons[art.kind] || kindIcons.file;
      return `<div class="artifact-card" data-path="${escapeHTML(art.path)}">
        <div class="artifact-info">
          <div class="artifact-icon-wrap">${icon}</div>
          <div class="artifact-details">
            <span class="artifact-name" title="${escapeHTML(art.name)}">${escapeHTML(art.name)}</span>
            <span class="artifact-meta">${escapeHTML(art.path)} · ${humanSize(art.size_bytes)}</span>
          </div>
        </div>
        <div class="artifact-actions">
          ${state.undoable.has(art.path)
            ? `<button class="btn-artifact-undo" data-path="${escapeHTML(art.path)}" title="Откатить последнее изменение файла">↩ Откатить</button>`
            : ""}
          <button class="btn-artifact-view" data-action="view" data-path="${escapeHTML(art.path)}">Просмотр</button>
        </div>
      </div>`;
    })
    .join("");

  els.artifactsList.querySelectorAll(".btn-artifact-view").forEach((btn) => {
    btn.addEventListener("click", () => {
      openArtifactPreview(btn.dataset.path);
    });
  });

  els.artifactsList.querySelectorAll(".btn-artifact-undo").forEach((btn) => {
    btn.addEventListener("click", () => {
      const path = btn.dataset.path;
      // Откат необратим для текущего содержимого — подтверждаем.
      if (!confirm(`Откатить последнее изменение файла «${path}»?`)) return;
      send({ type: "restore_checkpoint", path });
      btn.disabled = true;
      btn.textContent = "Откат…";
    });
  });
}

/** Абсолютный путь файла: сервер отдаёт его только из рабочих папок. */
function absolutePath(relative) {
  if (/^[a-zA-Z]:[\\/]/.test(relative) || relative.startsWith("/")) return relative;
  const root = (state.currentWorkspace || "").replace(/[\\/]+$/, "");
  return root ? `${root}/${relative}` : relative;
}

/** Панель превью: html показываем как страницу, код — с подсветкой. */
async function openArtifactPreview(path) {
  const art = state.artifacts.get(path);
  const target = absolutePath(art ? art.path : path);

  switchTab("preview");
  els.previewFilename.textContent = art ? art.name : path;
  els.previewBody.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  state.previewFile = null;

  let info;
  try {
    const response = await fetch(`/api/file?path=${encodeURIComponent(target)}`);
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${response.status}`);
    }
    info = await response.json();
  } catch (error) {
    els.previewBody.innerHTML =
      `<div class="empty-state">Не удалось открыть файл.<br>${escapeHTML(String(error.message || error))}</div>`;
    return;
  }

  state.previewFile = info;
  els.previewFilename.textContent = `${info.name} · ${info.size_text}`;
  els.previewFilename.title = info.path;
  renderPreview(info, state.previewMode);
}

function renderPreview(info, mode) {
  const rawUrl = `/files/${info.path.replace(/\\/g, "/")}`;
  els.previewActions.innerHTML = "";

  if (info.kind === "html") {
    // Для html даём переключатель «Страница | Код»: сверстанный результат
    // важнее исходника, но исходник тоже нужен.
    addPreviewToggle(info, mode);
    addPreviewLink(rawUrl);
  } else if (info.kind === "text" && info.language) {
    addPreviewLink(rawUrl);
  }
  addPreviewCopy(info);

  if (info.kind === "image") {
    els.previewBody.innerHTML = `<img class="preview-image" src="${escapeHTML(rawUrl)}" alt="${escapeHTML(info.name)}">`;
    return;
  }
  if (info.kind === "binary") {
    els.previewBody.innerHTML = `<div class="empty-state">Двоичный файл (${escapeHTML(info.size_text)}) — показать нечем.</div>`;
    return;
  }
  if (info.kind === "too_big") {
    els.previewBody.innerHTML =
      `<div class="empty-state">Файл слишком большой для просмотра (${escapeHTML(info.size_text)}).</div>`;
    return;
  }
  if (info.kind === "html" && mode !== "code") {
    // sandbox без allow-same-origin: скрипты страницы работают, но до данных
    // приложения не дотянутся.
    els.previewBody.innerHTML =
      `<iframe class="preview-frame" src="${escapeHTML(rawUrl)}" sandbox="allow-scripts allow-forms"></iframe>`;
    return;
  }

  if (info.language === "markdown") {
    els.previewBody.innerHTML = `<div class="preview-markdown"></div>`;
    const holder = els.previewBody.firstElementChild;
    holder.innerHTML = renderMarkdown(info.content);
    postProcessMarkdown(holder);
    return;
  }

  els.previewBody.innerHTML =
    `<pre><code class="language-${escapeHTML(info.language || "plaintext")}">${escapeHTML(info.content)}</code></pre>`;
  if (typeof hljs !== "undefined") {
    els.previewBody.querySelectorAll("pre code").forEach((el) => hljs.highlightElement(el));
  }
}

function addPreviewToggle(info, mode) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn-copy-code";
  button.textContent = mode === "code" ? "Страница" : "Код";
  button.addEventListener("click", () => {
    state.previewMode = mode === "code" ? "page" : "code";
    renderPreview(info, state.previewMode);
  });
  els.previewActions.appendChild(button);
}

function addPreviewLink(url) {
  const link = document.createElement("a");
  link.className = "btn-copy-code";
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "В браузере";
  els.previewActions.appendChild(link);
}

function addPreviewCopy(info) {
  if (!info.content) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn-copy-code";
  button.textContent = "Копировать";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(info.content);
      button.textContent = "Скопировано";
      setTimeout(() => (button.textContent = "Копировать"), 2000);
    } catch {
      button.textContent = "Ошибка";
    }
  });
  els.previewActions.appendChild(button);
}


const TAB_TITLES = {
  artifacts: "Артефакты",
  preview: "Превью",
  changes: "Изменения",
  terminal: "Терминал",
};

function switchTab(tabName) {
  els.tabButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  els.tabContents.forEach((content) => {
    content.classList.toggle("active", content.id === `tab-${tabName}`);
  });
  // Заголовок панели отражает активную вкладку (внутренний ряд вкладок скрыт —
  // переключение идёт кнопками в шапке).
  const title = document.querySelector(".work-drawer-title");
  if (title && TAB_TITLES[tabName]) title.textContent = TAB_TITLES[tabName];
  // Терминал стартует лениво: PTY-оболочка поднимается только при первом
  // открытии вкладки, а не на каждом запуске приложения.
  if (tabName === "terminal" && window.AgentTerminal) {
    window.AgentTerminal.onTabShown();
  }
  // Вкладка «Изменения» подтягивает свежий git-дифф при открытии.
  if (tabName === "changes") {
    loadChanges();
  }
}

// ------------------------------------------------------ вкладка «Изменения»

/** Загружает текущий git-дифф рабочей папки и рисует его с подсветкой. */
async function loadChanges() {
  const body = document.getElementById("changes-body");
  const branchEl = document.getElementById("changes-branch");
  if (!body) return;
  body.innerHTML = `<div class="empty-state">Загрузка изменений…</div>`;
  try {
    const ws = state.currentWorkspace || "";
    const res = await fetch(`/api/git/diff?workspace=${encodeURIComponent(ws)}`);
    const data = await res.json();
    if (!data.available) {
      branchEl.textContent = "—";
      body.innerHTML = `<div class="empty-state">${escapeHTML(data.reason || "Изменения недоступны")}.</div>`;
      return;
    }
    branchEl.textContent = "⎇ " + (data.branch || "—");
    const hasDiff = data.diff && data.diff.trim();
    const untracked = data.untracked || [];
    if (!hasDiff && untracked.length === 0) {
      body.innerHTML = `<div class="empty-state">Изменений нет — рабочее дерево чистое.</div>`;
      return;
    }
    let html = hasDiff ? renderDiff(data.diff) : "";
    if (untracked.length) {
      const items = untracked.map((f) => `<div class="diff-line diff-added">＋ новый файл: ${escapeHTML(f)}</div>`).join("");
      html += `<div class="diff-file"><div class="diff-file__head">Новые файлы</div>${items}</div>`;
    }
    body.innerHTML = html;
  } catch (err) {
    body.innerHTML = `<div class="empty-state">Не удалось загрузить изменения: ${escapeHTML(err.message)}</div>`;
  }
}

/** Превращает unified diff в HTML с подсветкой добавленных/удалённых строк. */
function renderDiff(diff) {
  const blocks = [];
  let current = null;
  const flush = () => {
    if (current) blocks.push(current);
    current = null;
  };
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("diff --git")) {
      flush();
      // "diff --git a/path b/path" → показываем путь.
      const m = raw.match(/ b\/(.+)$/);
      current = { file: m ? m[1] : raw, lines: [] };
      continue;
    }
    if (!current) current = { file: "изменения", lines: [] };
    if (raw.startsWith("index ") || raw.startsWith("--- ") || raw.startsWith("+++ ")) continue;
    let cls = "diff-ctx";
    if (raw.startsWith("@@")) cls = "diff-hunk";
    else if (raw.startsWith("+")) cls = "diff-added";
    else if (raw.startsWith("-")) cls = "diff-removed";
    current.lines.push(`<div class="diff-line ${cls}">${escapeHTML(raw || " ")}</div>`);
  }
  flush();
  return blocks
    .map(
      (b) =>
        `<div class="diff-file"><div class="diff-file__head">${escapeHTML(b.file)}</div>${b.lines.join("")}</div>`,
    )
    .join("");
}

// ------------------------------------------------------------ сайдбар сессий

async function loadSessionsList() {
  try {
    const res = await fetch("/api/sessions");
    if (!res.ok) return;
    const data = await res.json();
    renderSessionsList(data.sessions || []);
  } catch (err) {
    console.error("Ошибка загрузки сессий:", err);
  }
}

function renderSessionsList(sessions) {
  if (!sessions || sessions.length === 0) {
    els.sessionsList.innerHTML = `<div class="empty-state" style="padding: 1rem;"><p>Нет сохранённых чатов</p></div>`;
    return;
  }

  els.sessionsList.innerHTML = sessions
    .map((s) => {
      const isActive = s.id === state.currentSessionId ? "active" : "";
      const dateStr = s.updated_at ? new Date(s.updated_at * 1000).toLocaleDateString() : "";
      const wsShort = s.workspace ? s.workspace.split(/[\\/]/).filter(Boolean).slice(-2).join("/") : "";
      return `<div class="session-item ${isActive}" data-id="${escapeHTML(s.id)}">
        <div class="session-info">
          <span class="session-title" title="${escapeHTML(s.title)}">${escapeHTML(s.title)}</span>
          <span class="session-meta-sub">${escapeHTML(wsShort)} · ${dateStr}</span>
        </div>
        <button class="btn-session-delete" data-id="${escapeHTML(s.id)}" title="Удалить чат">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </button>
      </div>`;
    })
    .join("");

  els.sessionsList.querySelectorAll(".session-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      if (e.target.closest(".btn-session-delete")) return;
      const id = item.dataset.id;
      if (id !== state.currentSessionId) {
        send({ type: "load_session", session_id: id });
      }
    });
  });

  els.sessionsList.querySelectorAll(".btn-session-delete").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (confirm("Удалить этот чат?")) {
        await fetch(`/api/sessions/${id}`, { method: "DELETE" });
        if (state.currentSessionId === id) {
          send({ type: "new_session", workspace: state.stickyWorkspace || "" });
        } else {
          loadSessionsList();
        }
      }
    });
  });
}

/** Восстанавливает один элемент сохранённой ленты. */
function restoreTimelineEntry(entry) {
  if (entry.kind === "user") {
    finishStepGroup();
    appendUserMessage(entry.text);
    return;
  }
  if (entry.kind === "answer") {
    finishStepGroup();
    const block = appendFeed("", "agent-response-block");
    block.innerHTML = renderMarkdown(entry.text);
    postProcessMarkdown(block);
    addAnswerFooter(block, {
      duration_ms: entry.duration_ms,
      steps: entry.steps,
      usage: entry.usage,
      cost_usd: entry.cost_usd,
    });
    return;
  }
  if (entry.kind === "error") {
    finishStepGroup();
    appendFeed(
      `<div class="error-title">Задача прервана</div><div class="error-text">${escapeHTML(entry.text)}</div>`,
      "system-message error-block",
    );
    return;
  }
  if (entry.kind === "step") {
    const node = renderStepRow(entry.name, entry.args, {
      ok: entry.ok,
      duration_ms: entry.duration_ms,
      output: entry.output,
    });
    node.querySelector(".step-row").addEventListener("click", () =>
      toggleBlock(node, ".step-body", ".step-caret"),
    );
  }
}

function restoreSessionUI(session, workspace, { sticky = true } = {}) {
  state.currentSessionId = session.id;
  state.currentTitle = session.title || "Новый диалог";
  updateWorkspaceUI(workspace || session.workspace || state.currentWorkspace, { sticky });

  els.chatTitle.textContent = state.currentTitle;
  if (session.model) els.modelInput.value = session.model;

  // Очищаем фид
  els.feedItems.innerHTML = "";
  state.answerBlock = null;
  state.userTurn = 0; // нумерация запросов начинается заново
  // Снимаем режим приветствия: без этого у ленты остаётся flex:0 0 auto,
  // и загруженный чат нельзя прокрутить — он просто обрезается по высоте.
  // showWelcome() ниже вернёт режим обратно, если сессия пустая.
  setEmptyState(false);

  // Восстанавливаем ход работы. Лента сессии точнее, чем messages: по формату
  // модели не понять, какие инструменты вызывались и чем закончились.
  const timeline = session.timeline || [];
  if (timeline.length) {
    timeline.forEach(restoreTimelineEntry);
    finishStepGroup();
  } else {
    const messages = session.messages || [];
    if (messages.length === 0) {
      showWelcome();
    } else {
      messages.forEach((msg) => {
        if (msg.role === "user") {
          appendUserMessage(typeof msg.content === "string" ? msg.content : "(сообщение с вложением)");
        } else if (msg.role === "assistant" && msg.content) {
          const block = appendFeed("", "agent-response-block");
          block.innerHTML = renderMarkdown(msg.content);
          postProcessMarkdown(block);
        }
      });
    }
  }

  // Восстанавливаем план
  renderPlan(session.plan_steps || []);

  // Восстанавливаем артефакты. Кнопки отката не переносим: снимки живут на
  // диске по сессии, но в интерфейсе откат доступен для правок текущего сеанса.
  state.artifacts.clear();
  state.undoable.clear();
  (session.artifacts || []).forEach((art) => {
    state.artifacts.set(art.path, art);
  });
  renderArtifacts();

  loadSessionsList();
}

// ------------------------------------------------------------ WebSocket события

const HANDLERS = {
  "mode.updated"(msg) {
    setMode(msg.mode);
  },

  ready(msg) {
    if (msg.version) els.versionLabel.textContent = `версия ${msg.version}`;
    state.modes = msg.modes || [];
    setMode(msg.approval_mode || "manual");
    const saved = localStorage.getItem("local_ai_workspace");
    // saved — выбранная вручную папка проекта (липкая). Нет её → работаем в
    // авто-папке текущего чата, которую прислал сервер (не запоминаем как липкую).
    const activeWs = saved || msg.workspace || "";
    updateWorkspaceUI(activeWs, { sticky: Boolean(saved) });

    if (saved && saved !== msg.workspace) {
      send({ type: "set_workspace", workspace: saved });
    }

    if (!els.modelInput.value) els.modelInput.value = msg.model || "";
    if (msg.session) {
      restoreSessionUI(msg.session, activeWs, { sticky: Boolean(saved) });
    }
    logLine(`Готово. Инструментов: ${msg.tools.length}. Режим: ${msg.approval_mode}.`);
    (msg.warnings || []).forEach((w) => logLine(w, "warning"));
    loadSessionsList();
  },

  "session.loaded"(msg) {
    // Авто-папка чата (msg.auto_workspace) не становится липкой для новых чатов.
    restoreSessionUI(msg.session, msg.workspace, { sticky: !msg.auto_workspace });
    if (msg.mode) setMode(msg.mode);
    logLine(`Загружен чат: "${msg.session.title}" (папка: ${msg.workspace})`);
  },

  "workspace.error"(msg) {
    // Сервер отверг папку: показываем причину прямо под полем ввода,
    // а поле возвращаем к реально действующей папке.
    showWorkspaceError(msg.message);
    logLine(msg.message, "error");

  },

  "workspace.updated"(msg) {
    updateWorkspaceUI(msg.workspace);
    loadSessionsList();
  },

  state(msg) {
    setRunning(msg.state !== "idle");
    const label = { idle: "Готов", running: "Выполняет задачу", waiting_approval: "Ждёт подтверждения" };
    setStatus(label[msg.state] || msg.state, msg.state === "idle" ? "connected" : "working");
  },

  log(msg) {
    logLine(msg.text, msg.level);
  },

  "context.usage"(msg) {
    state.contextTokens = msg.tokens || 0;
    updateContextRing();
  },

  "plan.updated"(msg) {
    renderPlan(msg.steps);
    const completed = (msg.steps || []).filter((s) => s.status === "completed").length;
    logLine(`План: ${completed}/${(msg.steps || []).length} шагов.`, "debug");
  },

  "artifact.created"(msg) {
    state.artifacts.set(msg.path, msg);
    renderArtifacts();
    logLine(`Артефакт: ${msg.path} (${humanSize(msg.size_bytes)})`);
  },

  // Агент решил показать картинку прямо в ленте ответа (не только в «Превью»).
  show_image(msg) {
    state.answerBlock = null;
    clearWelcome();
    const abs = absolutePath(msg.path).replace(/\\/g, "/");
    const url = `/files/${abs}`;
    const caption = msg.caption
      ? `<figcaption>${escapeHTML(msg.caption)}</figcaption>`
      : "";
    const node = appendFeed(
      `<figure class="chat-image">
         <img src="${escapeHTML(url)}" alt="${escapeHTML(msg.name || msg.path)}" loading="lazy">
         ${caption}
       </figure>`,
      "chat-image-block",
    );
    // Клик по картинке открывает её в панели «Превью» на всю ширину.
    const img = node.querySelector("img");
    if (img) img.addEventListener("click", () => openPreview(null, msg.path));
    scrollFeed();
  },

  // Инлайн-виджет: SVG-графика или интерактивный HTML+JS. Рендерим в песочнице
  // (iframe srcdoc, sandbox без allow-same-origin) — скрипты виджета изолированы
  // от данных приложения, ровно как превью-панель артефактов.
  show_html(msg) {
    state.answerBlock = null;
    clearWelcome();
    const caption = msg.caption
      ? `<figcaption>${escapeHTML(msg.caption)}</figcaption>`
      : "";
    const cls = msg.kind === "graphic" ? "chat-graphic" : "chat-interactive";
    const node = appendFeed(
      `<figure class="chat-widget ${cls}">
         <iframe class="widget-frame" sandbox="allow-scripts allow-forms"
                 srcdoc="${escapeHTML(msg.html || "")}" loading="lazy"></iframe>
         ${caption}
       </figure>`,
      "chat-widget-block",
    );
    scrollFeed();
    return node;
  },

  // Инлайн-вложение: готовый файл прямо в ответе (превью для картинки, иначе чип
  // со скачиванием). Отличие от артефакта — файл виден в самой ленте.
  show_file(msg) {
    state.answerBlock = null;
    clearWelcome();
    const abs = absolutePath(msg.path).replace(/\\/g, "/");
    const url = `/files/${abs}`;
    const caption = msg.caption
      ? `<figcaption>${escapeHTML(msg.caption)}</figcaption>`
      : "";
    const name = escapeHTML(msg.name || msg.path);
    if (msg.kind === "image") {
      const node = appendFeed(
        `<figure class="chat-image">
           <img src="${escapeHTML(url)}" alt="${name}" loading="lazy">
           ${caption}
         </figure>`,
        "chat-image-block",
      );
      const img = node.querySelector("img");
      if (img) img.addEventListener("click", () => openPreview(null, msg.path));
      scrollFeed();
      return node;
    }
    const size = msg.size_bytes ? ` · ${humanSize(msg.size_bytes)}` : "";
    const node = appendFeed(
      `<figure class="chat-attachment">
         <a class="attach-chip" href="${escapeHTML(url)}" download target="_blank" rel="noopener noreferrer">
           <span class="attach-icon">📎</span>
           <span class="attach-name">${name}</span>
           <span class="attach-meta">${escapeHTML(msg.kind || "file")}${size}</span>
         </a>
         ${caption}
       </figure>`,
      "chat-attachment-block",
    );
    scrollFeed();
    return node;
  },

  // Сработало напоминание/условие — показываем пользователю прямо в ленте.
  // Модель получит контекст срабатывания из системного промпта на следующем ответе.
  "reminder.fired"(msg) {
    clearWelcome();
    appendFeed(
      `<div class="reminder-fired">
         <span class="reminder-bell">⏰</span>
         <span class="reminder-text">${escapeHTML(msg.text || msg.note || "Напоминание")}</span>
       </div>`,
      "reminder-fired-block",
    );
    logLine(`Напоминание: ${msg.note || msg.text || ""}`);
    scrollFeed(true);
  },

  // Агент попросил секрет — открываем панель ввода с подставленным именем.
  "secret.requested"(msg) {
    logLine(`Агент запросил секрет: ${msg.name}${msg.purpose ? " — " + msg.purpose : ""}`);
    openSecrets(msg.name);
  },

  // Перед изменением файла сделан снимок — на карточке артефакта появляется
  // кнопка отката. Показываем только для файлов, которые реально можно вернуть.
  "checkpoint.created"(msg) {
    if (msg.recoverable) {
      state.undoable.add(msg.path);
      renderArtifacts();
    }
  },

  // Живой счётчик трат в шапке: обновляется после каждого ответа модели.
  "usage.updated"(msg) {
    if (!els.usageMeter) return;
    const cost = msg.priced && msg.usd ? ` · ${formatCost(msg.usd)}` : "";
    let text = `${formatTokens(msg.tokens)} токенов${cost}`;
    // Если задан бюджет — показываем, сколько от него уже потрачено.
    if (msg.budget) {
      const ratio = msg.tokens / msg.budget;
      text = `${formatTokens(msg.tokens)} / ${formatTokens(msg.budget)}${cost}`;
      els.usageMeter.classList.toggle("is-warn", ratio >= 0.8);
    }
    els.usageMeter.textContent = text;
    els.usageMeter.hidden = false;
  },

  "checkpoint.restored"(msg) {
    logLine(msg.message, "info");
    // Если откаченный файл открыт в превью — перечитываем его (мог измениться
    // или исчезнуть, если отменяли создание).
    if (state.previewFile === msg.path) openArtifactPreview(msg.path);
  },

  "run.started"(msg) {
    state.answerBlock = null;
    state.answerText = "";
    state.toolBlocks.clear();
    state.thinking = null;
    state.stepGroup = null;
    clearWelcome();
    // Счётчик трат относится к текущей задаче: прячем прошлый до первых токенов.
    if (els.usageMeter) {
      els.usageMeter.hidden = true;
      els.usageMeter.classList.remove("is-warn");
    }
    setActivity("Обдумываю задачу");
    logLine(`Задача запущена (модель: ${msg.model}).`);
  },

  "tool.pending"(msg) {
    // Модель ещё диктует аргументы (например, содержимое файла на 500 строк).
    // Без этого экран замирал на десятки секунд без единого признака работы.
    const action = {
      write_file: "Пишу файл",
      apply_patch: "Готовлю правки",
      edit_file: "Правлю файл",
      create_skill: "Создаю навык",
      run_python: "Пишу код",
      execute_command: "Готовлю команду",
      update_plan: "Составляю план",
    }[msg.name] || `Готовлю ${msg.name}`;

    const size = msg.chars > 900 ? ` · ${(msg.chars / 1024).toFixed(1)} КБ` : "";
    setActivity(`${action}${size}`);
  },

  "step.started"(msg) {
    logLine(`Шаг ${msg.step}/${msg.max_steps}`, "debug");
    if (msg.step <= 1) return;
    // Предел показываем, только когда до него близко. Иначе «Шаг 2 из 25»
    // читается как план из 25 шагов, хотя 25 — это аварийный потолок цикла,
    // а не намерение агента.
    const left = msg.max_steps - msg.step;
    setActivity(left <= 5 ? `Шаг ${msg.step}, лимит ${msg.max_steps}` : `Шаг ${msg.step}`);
  },

  "reasoning.delta"(msg) {
    // Размышления идут в ленту рядом с шагами, а не в отдельную панель:
    // так видно, ЧТО именно агент обдумывал перед конкретным действием.
    if (!state.thinking) {
      clearWelcome();
      const node = appendFeed(
        `<button class="thinking-row" type="button">
           <span class="thinking-caret">▸</span>
           <span class="thinking-label">Размышляет…</span>
         </button>
         <div class="thinking-body" hidden></div>`,
        "thinking",
      );
      node.querySelector(".thinking-row").addEventListener("click", () => toggleBlock(node, ".thinking-body", ".thinking-caret"));
      state.thinking = { node, body: node.querySelector(".thinking-body"), text: "" };
    }
    state.thinking.text += msg.text;
    state.thinking.body.textContent = state.thinking.text;
    scrollFeed();
  },

  "text.delta"(msg) {
    if (!state.answerBlock) {
      closeThinking();
      finishStepGroup();
      clearActivity();
      clearReconnect();
      state.answerBlock = appendFeed("", "agent-response-block");
      state.answerText = "";
    }
    state.answerText += msg.text;
    scheduleAnswerRender();
  },

  "tool.started"(msg) {
    state.answerBlock = null;
    closeThinking();
    clearWelcome();
    clearReconnect();

    const node = renderStepRow(msg.name, msg.args, null);
    node.id = `tool-${msg.call_id}`;
    node.querySelector(".step-row").addEventListener("click", () =>
      toggleBlock(node, ".step-body", ".step-caret"),
    );
    state.toolBlocks.set(msg.call_id, node);
    setActivity(`Выполняю ${msg.name}`);
    logLine(`-> ${msg.name}`);
  },

  "tool.finished"(msg) {
    const node = state.toolBlocks.get(msg.call_id);
    logLine(`<- ${msg.name}: ${msg.ok ? "ok" : "ошибка"} (${msg.duration_ms} мс)`, msg.ok ? "info" : "error");
    if (!node) return;

    const status = node.querySelector(".step-status");
    status.textContent = msg.ok ? `${msg.duration_ms} мс` : "ошибка";
    status.className = `step-status ${msg.ok ? "success" : "failed"}`;
    node.querySelector(".tool-output").innerHTML = `<pre>${escapeHTML(msg.output)}</pre>`;
    // Ошибку раскрываем сразу: её нужно увидеть, не кликая.
    if (!msg.ok) {
      toggleBlock(node, ".step-body", ".step-caret", true);
      if (state.stepGroup) state.stepGroup.failed += 1;
    }
    state.toolBlocks.delete(msg.call_id);
    if (state.toolBlocks.size === 0) setActivity("Обрабатываю результат");
  },

  // Исследование идёт минутами и внутри одного вызова инструмента: без
  // отдельной строки прогресса окно неотличимо от зависшего.
  "research.progress"(msg) {
    const counter = msg.total ? ` ${msg.done}/${msg.total}` : "";
    setActivity(`${RESEARCH_PHASES[msg.phase] || "Исследую"}: ${msg.text}${counter}`);
    logLine(`[исследование] ${msg.text}`);
  },

  "question.asked"(msg) {
    finishStepGroup();
    clearActivity();
    renderQuestions(msg.request_id, msg.questions || []);
  },

  "approval.requested"(msg) {
    // Четыре варианта вместо «да/нет»: одноразовое разрешение, запомнить для
    // проекта, запомнить везде и отказ. Так не приходится подтверждать одно
    // и то же по десять раз за задачу.
    const scopes = [
      { scope: "once", label: "Разрешить", cls: "btn-approve" },
      { scope: "project", label: "Всегда в этом проекте", cls: "btn-approve-scope" },
      { scope: "global", label: "Всегда везде", cls: "btn-approve-scope" },
      { scope: "deny", label: "Отклонить", cls: "btn-deny" },
    ];

    finishStepGroup();
    const node = appendFeed(
      `<div class="approval-title">Разрешить действие: <b>${escapeHTML(msg.name)}</b></div>
       <div class="approval-reason">${escapeHTML(msg.reason)}</div>
       <pre class="approval-args">${escapeHTML(JSON.stringify(msg.args, null, 2))}</pre>
       <div class="approval-actions">
         ${scopes.map((s) => `<button type="button" class="${s.cls}" data-scope="${s.scope}">${s.label}</button>`).join("")}
       </div>`,
      "approval-block",
    );

    const answers = {
      once: "Разрешено один раз",
      project: "Разрешено в этом проекте",
      global: "Разрешено во всех проектах",
      deny: "Отклонено",
    };
    node.querySelectorAll("[data-scope]").forEach((button) => {
      button.addEventListener("click", () => {
        const scope = button.dataset.scope;
        send({ type: "approval", request_id: msg.request_id, scope, approved: scope !== "deny" });
        // После выбора карточка не нужна — сворачиваем её в одну компактную
        // строку, чтобы не занимала место в ленте.
        const icon = scope === "deny" ? "✗" : "✓";
        node.className = "approval-resolved";
        node.innerHTML =
          `<span class="approval-resolved__icon">${icon}</span> ` +
          `${escapeHTML(answers[scope])}: <b>${escapeHTML(msg.name)}</b>`;
      });
    });

    scrollFeed(true);
    logLine(`Запрошено подтверждение: ${msg.name}`, "warning");
  },

  "approval.resolved"(msg) {
    logLine(`Подтверждение ${msg.request_id}: ${msg.approved ? "разрешено" : "отклонено"}`, "warning");
  },

  // Связь с моделью оборвалась — идёт повтор. Показываем живой статус в ленте,
  // чтобы ожидание не выглядело зависанием (одна карточка, обновляется на месте).
  reconnecting(msg) {
    clearActivity();
    let node = state.reconnectNode;
    if (!node || !node.isConnected) {
      node = appendFeed("", "system-message reconnect-block");
      state.reconnectNode = node;
    }
    const reason = msg.reason ? ` — ${escapeHTML(msg.reason)}` : "";
    node.innerHTML =
      `<span class="reconnect-spinner" aria-hidden="true"></span>` +
      `<span class="reconnect-text">Переподключение к модели… попытка ${msg.attempt}/${msg.max_attempts}` +
      (msg.delay_s ? `, следующая через ${msg.delay_s} с` : "") +
      `<span class="reconnect-reason">${reason}</span></span>`;
    setStatus(`Переподключение ${msg.attempt}/${msg.max_attempts}…`, "working");
    scrollFeed();
  },

  "run.finished"(msg) {
    clearReconnect();
    const targetBlock = state.answerBlock || appendFeed("", "agent-response-block");
    targetBlock.innerHTML = renderMarkdown(msg.text || state.answerText);
    postProcessMarkdown(targetBlock);
    state.answerBlock = null;
    const tokens = msg.usage && msg.usage.total_tokens ? `, токенов: ${msg.usage.total_tokens}` : "";
    logLine(`Задача завершена за ${msg.duration_ms} мс (шагов: ${msg.steps}${tokens}).`);

    // Сводка о прогоне и кнопка копирования — в подвале под ответом.
    addAnswerFooter(targetBlock, {
      duration_ms: msg.duration_ms, steps: msg.steps, usage: msg.usage, cost_usd: msg.cost_usd,
    });

    finishStepGroup();
    clearActivity();
    setRunning(false);
    els.input.focus();
    loadSessionsList();
  },

  "run.failed"(msg) {
    state.answerBlock = null;
    finishStepGroup();
    clearActivity();
    clearReconnect();
    const node = appendFeed(
      `<div class="error-title">Задача прервана</div>
       <div class="error-text">${escapeHTML(msg.message)}</div>
       <button type="button" class="btn-retry">Повторить</button>`,
      "system-message error-block",
    );
    node.querySelector(".btn-retry").addEventListener("click", () => {
      if (!state.lastTask) return;
      els.input.value = state.lastTask;
      autoGrow();
      els.form.requestSubmit();
    });
    logLine(msg.message, "error");
    setRunning(false);
  },

  "run.cancelled"() {
    state.answerBlock = null;
    finishStepGroup();
    clearActivity();
    clearReconnect();
    appendFeed("Задача остановлена пользователем.", "system-message");
    setRunning(false);
  },
};


// ---------------------------------------------------------------- вопросы

/** Карточка вопросов агента: варианты в один клик вместо печати ответа. */
function renderQuestions(requestId, questions) {
  const node = appendFeed("", "question-block");
  const answers = new Map();

  questions.forEach((question, index) => {
    const card = document.createElement("div");
    card.className = "question";
    card.innerHTML =
      `<div class="question-text">${escapeHTML(question.question)}</div>
       <div class="question-hint">${questionHint(question.kind)}</div>
       <div class="question-options"></div>`;

    const list = card.querySelector(".question-options");
    const state = { kind: question.kind, chosen: [], order: question.options.map((o) => o.label) };
    answers.set(String(index), state);

    if (question.kind === "ranking") {
      buildRanking(list, question, state);
    } else {
      buildChoice(list, question, state);
    }

    node.appendChild(card);
  });

  const footer = document.createElement("div");
  footer.className = "question-actions";
  footer.innerHTML =
    `<button type="button" class="btn-answer">Ответить</button>
     <button type="button" class="btn-answer-skip">Решай сам</button>`;
  node.appendChild(footer);

  const reply = (skip) => {
    const payload = {};
    answers.forEach((state, key) => {
      payload[key] = skip ? [] : state.kind === "ranking" ? state.order : state.chosen;
    });
    send({ type: "answer", request_id: requestId, answers: payload });
    node.querySelectorAll("button, .option").forEach((el) => el.classList.add("is-locked"));
    footer.innerHTML = `<span class="question-result">${skip ? "Оставлено на усмотрение агента" : "Ответ отправлен"}</span>`;
  };

  footer.querySelector(".btn-answer").addEventListener("click", () => reply(false));
  footer.querySelector(".btn-answer-skip").addEventListener("click", () => reply(true));
  scrollFeed(true);
  logLine("Агент задал вопрос", "warning");
}

function questionHint(kind) {
  return {
    single: "Выберите один вариант",
    multiple: "Можно выбрать несколько",
    ranking: "Перетащите варианты: сверху — самое важное",
  }[kind] || "";
}

function optionMarkup(option) {
  const recommended = option.recommended
    ? `<span class="option-badge">рекомендую</span>`
    : "";
  const description = option.description
    ? `<span class="option-description">${escapeHTML(option.description)}</span>`
    : "";
  return `<span class="option-body">
            <span class="option-label">${escapeHTML(option.label)}${recommended}</span>
            ${description}
          </span>`;
}

/** Выбор одного или нескольких вариантов. */
function buildChoice(list, question, state) {
  question.options.forEach((option) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "option";
    item.innerHTML = optionMarkup(option);

    item.addEventListener("click", () => {
      if (item.classList.contains("is-locked")) return;
      if (question.kind === "single") {
        list.querySelectorAll(".option").forEach((el) => el.classList.remove("is-chosen"));
        state.chosen = [option.label];
        item.classList.add("is-chosen");
      } else {
        const index = state.chosen.indexOf(option.label);
        if (index >= 0) {
          state.chosen.splice(index, 1);
          item.classList.remove("is-chosen");
        } else {
          state.chosen.push(option.label);
          item.classList.add("is-chosen");
        }
      }
    });

    // Рекомендованный вариант выбран заранее: чаще всего его и подтверждают.
    if (option.recommended && question.kind === "single") {
      state.chosen = [option.label];
      item.classList.add("is-chosen");
    }
    list.appendChild(item);
  });
}

/** Ранжирование: перетаскиванием мышью или кнопками (для клавиатуры). */
function buildRanking(list, question, state) {
  list.classList.add("is-ranking");

  question.options.forEach((option) => {
    const item = document.createElement("div");
    item.className = "option option--rank";
    item.draggable = true;
    item.dataset.label = option.label;
    item.innerHTML =
      `<span class="option-rank"></span>
       ${optionMarkup(option)}
       <span class="option-move">
         <button type="button" class="btn-move" data-dir="-1" title="Выше">↑</button>
         <button type="button" class="btn-move" data-dir="1" title="Ниже">↓</button>
       </span>`;

    item.addEventListener("dragstart", () => item.classList.add("is-dragging"));
    item.addEventListener("dragend", () => {
      item.classList.remove("is-dragging");
      syncRanking(list, state);
    });

    item.querySelectorAll(".btn-move").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (item.classList.contains("is-locked")) return;
        const step = Number(button.dataset.dir);
        const sibling = step < 0 ? item.previousElementSibling : item.nextElementSibling;
        if (!sibling) return;
        if (step < 0) list.insertBefore(item, sibling);
        else list.insertBefore(sibling, item);
        syncRanking(list, state);
      });
    });

    list.appendChild(item);
  });

  list.addEventListener("dragover", (event) => {
    event.preventDefault();
    const dragging = list.querySelector(".is-dragging");
    if (!dragging) return;
    const after = itemAfterCursor(list, event.clientY);
    if (after) list.insertBefore(dragging, after);
    else list.appendChild(dragging);
  });

  syncRanking(list, state);
}

/** Куда вставить перетаскиваемый элемент: ищем ближайший снизу. */
function itemAfterCursor(list, cursorY) {
  const items = [...list.querySelectorAll(".option--rank:not(.is-dragging)")];
  return items.find((item) => {
    const box = item.getBoundingClientRect();
    return cursorY < box.top + box.height / 2;
  });
}

function syncRanking(list, state) {
  state.order = [...list.querySelectorAll(".option--rank")].map((item) => item.dataset.label);
  list.querySelectorAll(".option-rank").forEach((badge, index) => {
    badge.textContent = String(index + 1);
  });
}

// ---------------------------------------------------------------- WebSocket

function connect() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  setStatus("Подключение…", "disconnected");

  state.socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

  state.socket.onopen = () => {
    setStatus("Готов", "connected");
    setRunning(false);
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  };

  state.socket.onclose = () => {
    setStatus("Отключён", "disconnected");
    setRunning(false);
    logLine("Соединение разорвано. Переподключение через 2 с…", "error");
    if (!state.reconnectTimer) state.reconnectTimer = setTimeout(connect, 2000);
  };

  state.socket.onerror = () => logLine("Ошибка WebSocket.", "error");

  state.socket.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    const handler = HANDLERS[msg.type];
    if (handler) handler(msg);
  };
}

// --------------------------------------------------------------- память

const MEMORY_LABELS = {
  user: "О пользователе",
  preference: "Предпочтение",
  project: "О проекте",
  fact: "Факт",
};

async function openMemory() {
  if (!els.memoryModal) return;
  els.memoryModal.hidden = false;
  els.memoryList.innerHTML = `<p class="plus-menu__empty">Загружаю…</p>`;
  try {
    const data = await (await fetch("/api/memory")).json();
    renderMemory(data.facts || []);
  } catch (error) {
    els.memoryList.innerHTML = `<p class="plus-menu__empty">Не удалось загрузить: ${escapeHTML(String(error))}</p>`;
  }
}

function renderMemory(facts) {
  els.memoryCount.textContent = facts.length ? `фактов: ${facts.length}` : "";
  if (!facts.length) {
    els.memoryList.innerHTML =
      `<p class="plus-menu__empty">Пока пусто. Агент запоминает устойчивые факты о вас и проектах сам.</p>`;
    return;
  }
  els.memoryList.innerHTML = facts
    .map(
      (fact) =>
        `<div class="memory-item">
          <div class="memory-item__body">
            <span class="memory-item__cat">${escapeHTML(MEMORY_LABELS[fact.category] || fact.category)}</span>
            <span class="memory-item__text">${escapeHTML(fact.text)}</span>
          </div>
          <button class="memory-item__forget" data-id="${escapeHTML(fact.id)}" title="Забыть">×</button>
        </div>`,
    )
    .join("");

  els.memoryList.querySelectorAll(".memory-item__forget").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/memory/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
      openMemory(); // перечитываем
    });
  });
}

if (els.memoryBtn) {
  els.memoryBtn.addEventListener("click", openMemory);
  els.memoryClear.addEventListener("click", async () => {
    if (!confirm("Стереть всю память агента? Это необратимо.")) return;
    await fetch("/api/memory/all", { method: "DELETE" });
    openMemory();
  });
  els.memoryModal.querySelectorAll("[data-close-memory]").forEach((el) => {
    el.addEventListener("click", () => (els.memoryModal.hidden = true));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.memoryModal.hidden) els.memoryModal.hidden = true;
  });
}

// ------------------------------------------------------------ список моделей

/** Живой каталог OpenRouter вместо зашитого списка.
 *
 *  Моделей больше трёхсот, они появляются и исчезают каждую неделю — любой
 *  список в коде устареет. Поэтому он тянется с сервера и кэшируется там же;
 *  если сети нет, остаются варианты из разметки.
 */
async function loadModels() {
  let data;
  try {
    data = await (await fetch("/api/models")).json();
  } catch (error) {
    logLine(`Каталог моделей недоступен: ${error}`, "warning");
    return;
  }

  const models = data.models || [];
  if (!models.length) return;

  const options = models
    .map((model) => {
      // Цена и возможности прямо в подсказке: выбирать вслепую по одному
      // идентификатору неудобно, а описание datalist показывает рядом.
      const price = model.prompt_price > 0 ? `$${model.prompt_price}/млн` : "";
      const media = [model.vision && "фото", model.video && "видео"].filter(Boolean).join("+");
      const context = model.context ? `${Math.round(model.context / 1000)}k` : "";
      const hint = [context, price, media].filter(Boolean).join(" · ");
      return `<option value="${escapeHTML(model.id)}" label="${escapeHTML(hint)}"></option>`;
    })
    .join("");

  document.getElementById("model-options").innerHTML = options;
  state.models = models;
  logLine(`Моделей в списке: ${models.length} (${data.source})`, "debug");
}

loadModels();

// ------------------------------------------------------------- меню «плюс»

//: Значок вида вложения — по расширению, ещё до ответа сервера.
const ATTACH_KINDS = [
  { test: /\.(png|jpe?g|webp|gif|bmp)$/i, kind: "фото" },
  { test: /\.(mp4|mov|webm|mpe?g)$/i, kind: "видео" },
  { test: /\.(mp3|wav|ogg|m4a|flac)$/i, kind: "аудио" },
  { test: /\.(pdf|xlsx?|docx?|csv|tsv)$/i, kind: "документ" },
  { test: /\.zip$/i, kind: "архив" },
];

const WEB_MODE_LABELS = { auto: "", force: "поиск: всегда", off: "поиск: выкл" };

/** Что уходит на сервер вместе с задачей. */
function runOptions() {
  return {
    attachments: state.attachments.map((item) => item.path),
    web_mode: state.webMode,
    deep_research: state.deepResearch,
    skills: [...state.chosenSkills],
  };
}

function attachKind(path) {
  const name = path.split(/[\\/]/).pop() || path;
  if (!/\.[a-z0-9]+$/i.test(name)) return "папка";
  return (ATTACH_KINDS.find((entry) => entry.test.test(name)) || { kind: "файл" }).kind;
}

function addAttachments(paths) {
  const known = new Set(state.attachments.map((item) => item.path));
  paths.filter((path) => !known.has(path)).forEach((path) => {
    state.attachments.push({ path, name: path.split(/[\\/]/).pop() || path, kind: attachKind(path) });
  });
  renderAttachments();
}

function renderAttachments() {
  els.attachList.hidden = state.attachments.length === 0;
  els.attachList.innerHTML = state.attachments
    .map(
      (item, index) =>
        `<span class="attach-chip" title="${escapeHTML(item.path)}">` +
        `<span class="attach-chip__kind">${item.kind}</span>` +
        `<span class="attach-chip__name">${escapeHTML(item.name)}</span>` +
        `<button type="button" class="attach-chip__remove" data-index="${index}" ` +
        `title="Убрать">×</button></span>`,
    )
    .join("");

  els.attachList.querySelectorAll(".attach-chip__remove").forEach((button) => {
    button.addEventListener("click", () => {
      state.attachments.splice(Number(button.dataset.index), 1);
      renderAttachments();
    });
  });
}

/** Строка вложений в отправленном сообщении: что именно ушло агенту. */
function renderAttachmentsPreview() {
  if (!state.attachments.length) return "";
  const items = state.attachments
    .map((item) => `<span class="attach-chip"><span class="attach-chip__kind">${item.kind}</span>` +
      `<span class="attach-chip__name">${escapeHTML(item.name)}</span></span>`)
    .join("");
  return `<div class="attach-list">${items}</div>`;
}

/** Активные переключатели видны и при закрытом меню. */
function renderFlags() {
  const flags = [];
  if (WEB_MODE_LABELS[state.webMode]) flags.push(WEB_MODE_LABELS[state.webMode]);
  if (state.deepResearch) flags.push("исследование");
  if (state.chosenSkills.size) flags.push(`навыки: ${state.chosenSkills.size}`);

  els.composerFlags.innerHTML = flags
    .map((text) => `<span class="composer-flag">${escapeHTML(text)}</span>`)
    .join("");

  els.webSegments.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.web === state.webMode);
  });
  els.deepState.textContent = state.deepResearch ? "вкл" : "выкл";
  els.plusMenu
    .querySelector('[data-action="deep-research"]')
    .classList.toggle("is-on", state.deepResearch);
}

/** Папка как вложение: тот же системный диалог, что и у выбора рабочей папки,
 *  но выбранное сюда не меняет рабочую директорию агента. */
async function pickFolderAttachment() {
  const response = await fetch("/api/dialog/select-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initial_dir: state.currentWorkspace || "" }),
  }).then((r) => r.json());

  if (response.ok && response.path) return response.path;
  if (!response.cancelled) logLine(response.error || "Диалог выбора папки не открылся", "error");
  return null;
}

async function pickAttachments(kind) {
  const response = await fetch("/api/dialog/select-files", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, initial_dir: state.currentWorkspace || "" }),
  }).then((r) => r.json());

  if (response.ok) {
    addAttachments(response.paths || []);
    return;
  }
  // Отмена — не ошибка, а вот недоступный диалог нужно объяснить.
  if (!response.cancelled) logLine(response.error || "Диалог выбора файлов не открылся", "error");
}

/** Перетащенные файлы: браузер не даёт их путь, поэтому грузим содержимое на
 *  сервер, а он возвращает пути — и дальше это обычные вложения. */
async function uploadDroppedFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => f && f.size !== undefined);
  if (!files.length) return;

  const form = new FormData();
  files.forEach((file) => form.append("files", file, file.name));

  logLine(`Загружаю ${files.length} файл(ов)…`);
  try {
    const response = await fetch("/api/attachments/upload", { method: "POST", body: form }).then((r) => r.json());
    addAttachments(response.paths || []);
    (response.errors || []).forEach((err) => logLine(`Файл не приложен: ${err}`, "warning"));
    if ((response.paths || []).length) logLine(`Приложено файлов: ${response.paths.length}`);
  } catch (error) {
    logLine(`Не удалось загрузить файлы: ${error}`, "error");
  }
}

/** Перетаскивание файлов в чат. Браузер по умолчанию открыл бы файл вместо
 *  этого, поэтому событиям обязателен preventDefault. Оверлей показываем только
 *  когда тащат именно файлы, а не выделенный текст. */
function setupDragAndDrop() {
  const overlay = document.getElementById("drop-overlay");
  const zone = document.getElementById("chat-section");
  if (!overlay || !zone) return;

  let depth = 0; // счётчик вложенных dragenter/dragleave, иначе оверлей мигает

  const hasFiles = (event) =>
    Array.from(event.dataTransfer?.types || []).includes("Files");

  window.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth += 1;
    overlay.hidden = false;
  });

  window.addEventListener("dragover", (event) => {
    if (hasFiles(event)) event.preventDefault();
  });

  window.addEventListener("dragleave", (event) => {
    if (!hasFiles(event)) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0) overlay.hidden = true;
  });

  window.addEventListener("drop", (event) => {
    depth = 0;
    overlay.hidden = true;
    if (!hasFiles(event)) return;
    event.preventDefault();
    uploadDroppedFiles(event.dataTransfer.files);
  });
}

setupDragAndDrop();

async function loadSkills() {
  try {
    const data = await (await fetch("/api/skills")).json();
    state.skills = data.skills || [];
  } catch (error) {
    state.skills = [];
    logLine(`Не удалось загрузить навыки: ${error}`, "warning");
  }
  renderSkills();
}

function renderSkills() {
  if (!state.skills.length) {
    els.plusSkills.innerHTML =
      `<p class="plus-menu__empty">Навыков пока нет. Агент создаёт их сам ` +
      `или их можно положить в папку skills/.</p>`;
    return;
  }

  els.plusSkills.innerHTML = state.skills
    .map(
      (skill) =>
        `<button type="button" class="plus-item plus-item--toggle` +
        `${state.chosenSkills.has(skill.name) ? " is-on" : ""}" data-skill="${escapeHTML(skill.name)}">` +
        `<span class="plus-item__name">${escapeHTML(skill.name)}</span>` +
        `<span class="plus-item__hint">${escapeHTML(skill.description || "без описания")}</span>` +
        `<span class="plus-item__state">${state.chosenSkills.has(skill.name) ? "вкл" : ""}</span>` +
        `</button>`,
    )
    .join("");

  els.plusSkills.querySelectorAll("[data-skill]").forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.skill;
      if (state.chosenSkills.has(name)) state.chosenSkills.delete(name);
      else state.chosenSkills.add(name);
      renderSkills();
      renderFlags();
    });
  });
}

function togglePlusMenu(open) {
  const show = open === undefined ? els.plusMenu.hidden : open;
  els.plusMenu.hidden = !show;
  els.plusBtn.classList.toggle("is-open", show);
  els.plusBtn.setAttribute("aria-expanded", String(show));
  if (show && !state.skills.length) loadSkills();
}

els.plusBtn.addEventListener("click", (event) => {
  event.stopPropagation();
  togglePlusMenu();
});

els.plusMenu.addEventListener("click", (event) => event.stopPropagation());

document.addEventListener("click", () => togglePlusMenu(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !els.plusMenu.hidden) togglePlusMenu(false);
});

els.plusMenu.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.action;
    if (action === "attach-media") {
      togglePlusMenu(false);
      await pickAttachments("media");
    } else if (action === "attach-files") {
      togglePlusMenu(false);
      await pickAttachments("any");
    } else if (action === "attach-folder") {
      togglePlusMenu(false);
      const path = await pickFolderAttachment();
      if (path) addAttachments([path]);
    } else if (action === "deep-research") {
      state.deepResearch = !state.deepResearch;
      // Исследовать без интернета нельзя — включаем поиск вместе с ним.
      if (state.deepResearch && state.webMode === "off") setWebMode("auto");
      renderFlags();
    }
  });
});

function setWebMode(mode) {
  state.webMode = mode;
  localStorage.setItem("local_ai_web_mode", mode);
  if (mode === "off" && state.deepResearch) state.deepResearch = false;
  renderFlags();
}

els.webSegments.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () => setWebMode(button.dataset.web));
});

renderFlags();

// --------------------------------------------------------------- Слушатели событий

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const task = els.input.value.trim();
  if (state.running) {
    send({ type: "stop" });
    return;
  }
  if (!task) return;

  const currentWs = state.currentWorkspace;

  if (!send({
    type: "run",
    task,
    model: els.modelInput.value.trim() || undefined,
    workspace: currentWs || undefined,
    options: runOptions(),
  })) return;

  clearWelcome();
  state.lastTask = task;
  appendUserMessage(task, renderAttachmentsPreview());
  // Вложения одноразовые: приложить файл один раз и потом случайно отправить
  // его во все следующие запросы — не то, чего ждёт пользователь.
  state.attachments = [];
  renderAttachments();
  els.input.value = "";
  autoGrow();
  setRunning(true);
  setStatus("Выполняет задачу", "working");
});

function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 200)}px`;
}

els.input.addEventListener("input", autoGrow);

els.feed.addEventListener("scroll", updateStickiness);

els.newMessages.addEventListener("click", () => {
  state.stickToBottom = true;
  scrollFeed(true);
});

// Esc останавливает задачу: тянуться мышью к кнопке во время работы неудобно.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.running) {
    send({ type: "stop" });
  }
});

els.input.addEventListener("keydown", (event) => {
  // Пока открыт попап команд, стрелки и Enter управляют им, а не отправкой.
  if (handleCommandKey(event)) return;
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.form.requestSubmit();
  }
});

els.clearChat.addEventListener("click", () => {
  send({ type: "reset" });
  els.feedItems.innerHTML = "";
  state.answerBlock = null;
  state.thinking = null;
  state.stepGroup = null;
  showWelcome();
});

// ------------------------------------------------------------- экспорт диалога

/** Скачивает файл, отданный сервером как вложение. */
async function exportDialog(format) {
  if (!state.currentSessionId) {
    appendFeed("Сначала начните или откройте диалог.", "system-message");
    return;
  }
  const url = `/api/sessions/${encodeURIComponent(state.currentSessionId)}/export?format=${format}`;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      // Сервер присылает внятную причину (например, нет Edge/Chrome для PDF).
      let detail = "Не удалось экспортировать";
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        /* тело не JSON — оставляем общий текст */
      }
      appendFeed(escapeHTML(detail), "system-message error-block");
      return;
    }
    const blob = await res.blob();
    const name = filenameFromDisposition(res.headers.get("content-disposition"), format);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
  } catch (err) {
    appendFeed("Ошибка экспорта: " + escapeHTML(err.message), "system-message error-block");
  }
}

/** Достаёт имя файла из заголовка Content-Disposition (учитывает filename*). */
function filenameFromDisposition(header, format) {
  if (header) {
    const star = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (star) return decodeURIComponent(star[1]);
    const plain = header.match(/filename="?([^"]+)"?/i);
    if (plain) return plain[1];
  }
  return `dialog.${format}`;
}

if (els.exportBtn) {
  els.exportBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !els.exportMenu.hidden;
    els.exportMenu.hidden = open;
    els.exportBtn.setAttribute("aria-expanded", String(!open));
  });
  els.exportMenu.querySelectorAll("[data-format]").forEach((item) => {
    item.addEventListener("click", () => {
      els.exportMenu.hidden = true;
      els.exportBtn.setAttribute("aria-expanded", "false");
      exportDialog(item.dataset.format);
    });
  });
  // Клик мимо меню закрывает его.
  document.addEventListener("click", () => {
    if (!els.exportMenu.hidden) {
      els.exportMenu.hidden = true;
      els.exportBtn.setAttribute("aria-expanded", "false");
    }
  });
}

function setSidebarOpen(open) {
  els.sidebar.classList.toggle("collapsed", !open);
  if (els.sidebarOpen) {
    els.sidebarOpen.hidden = open;
    els.sidebarOpen.setAttribute("aria-expanded", String(open));
  }
  if (els.sidebarBackdrop) {
    els.sidebarBackdrop.hidden = !open;
    els.sidebarBackdrop.setAttribute("aria-hidden", String(!open));
  }
}

els.sidebarToggle.addEventListener("click", () => setSidebarOpen(false));
if (els.sidebarOpen) els.sidebarOpen.addEventListener("click", () => setSidebarOpen(true));
if (els.sidebarBackdrop) els.sidebarBackdrop.addEventListener("click", () => setSidebarOpen(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !els.sidebar.classList.contains("collapsed")) setSidebarOpen(false);
});

els.newChatBtn.addEventListener("click", () => {
  // Есть выбранная папка проекта — новый чат в ней; иначе сервер даст свою
  // авто-папку под этот чат (файлы чата не смешиваются между чатами).
  send({ type: "new_session", workspace: state.stickyWorkspace || "" });
});

// Кольцо контекста: смена модели пересчитывает заполнение (у моделей разное окно),
// клик — задать размер окна для текущей модели (поддержка 256k / 1M / числа).
if (els.modelInput) {
  els.modelInput.addEventListener("change", updateContextRing);
  els.modelInput.addEventListener("input", updateContextRing);
}
const contextRingEl = document.getElementById("context-ring");
if (contextRingEl) {
  contextRingEl.addEventListener("click", () => {
    const model = currentModelName();
    const current = getContextWindow(model);
    const answer = prompt(
      `Размер контекстного окна для модели «${model}» (например 256k, 1M, 200000):`,
      formatSize(current),
    );
    if (answer === null) return;
    const parsed = parseSize(answer);
    if (!parsed) { alert("Не понял размер. Примеры: 256k, 1M, 200000"); return; }
    setContextWindow(model, parsed);
    updateContextRing();
  });
  updateContextRing();
}

// ------------------------------------------------------- выбор рабочей папки
//
// Основной путь — встроенный обозреватель (работает всегда, в том числе в
// браузере). Системный проводник Windows доступен кнопкой внутри модалки:
// в десктоп-режиме это настоящий диалог, привязанный к окну приложения.

const folderBrowser = {
  current: "",
  selected: "",

  open() {
    els.folderModal.hidden = false;
    const start = state.currentWorkspace || "";
    this.loadQuick();
    this.navigate(start);
  },

  close() {
    els.folderModal.hidden = true;
  },

  setStatus(text, isError = false) {
    els.fmStatus.textContent = text || "";
    els.fmStatus.classList.toggle("is-error", Boolean(isError));
  },

  setSelected(path) {
    this.selected = path || "";
    els.fmSelected.textContent = this.selected ? `Выбрано: ${this.selected}` : "";
    els.fmChoose.disabled = !this.selected;
  },

  async loadQuick() {
    try {
      const data = await (await fetch("/api/workspace/recent")).json();
      const items = [];
      if (data.default) items.push({ label: "Папка агента", path: data.default });
      if (data.home) items.push({ label: "Домашняя", path: data.home });
      (data.recent || []).forEach((p) => {
        items.push({ label: p.split(/[\\/]/).filter(Boolean).pop() || p, path: p, recent: true });
      });

      els.fmQuick.innerHTML = "";
      items.forEach((item) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `fm-chip${item.recent ? " fm-chip--recent" : ""}`;
        chip.textContent = item.label;
        chip.title = item.path;
        chip.addEventListener("click", () => this.navigate(item.path));
        els.fmQuick.appendChild(chip);
      });
    } catch {
      els.fmQuick.innerHTML = "";
    }
  },

  async navigate(path) {
    this.setStatus("Загрузка…");
    try {
      const res = await fetch(`/api/browse?path=${encodeURIComponent(path || "")}`);
      const data = await res.json();

      this.current = data.current || "";
      els.fmPath.value = this.current;
      els.fmUp.disabled = !data.parent;
      els.fmUp.dataset.parent = data.parent || "";
      this.setSelected(this.current);

      els.fmList.innerHTML = "";
      if (!this.current) {
        this.setStatus("Выберите диск");
      } else if (data.error) {
        this.setStatus(data.error, true);
      } else if (!data.folders.length) {
        this.setStatus("Вложенных папок нет — можно выбрать эту.");
      } else {
        this.setStatus(
          data.writable === false ? "Внимание: папка доступна только для чтения." : "",
          data.writable === false,
        );
      }

      (data.folders || []).forEach((folder) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "fm-row";
        row.innerHTML = `<span class="fm-row__icon">📁</span><span class="fm-row__name"></span>`;
        row.querySelector(".fm-row__name").textContent = folder.name;
        row.addEventListener("click", () => this.setSelected(folder.path));
        row.addEventListener("dblclick", () => this.navigate(folder.path));
        els.fmList.appendChild(row);
      });
    } catch (err) {
      this.setStatus(`Не удалось прочитать папку: ${err}`, true);
    }
  },

  async openNative() {
    els.fmNative.disabled = true;
    const original = els.fmNative.textContent;
    els.fmNative.textContent = "Открываю…";
    try {
      const res = await fetch("/api/dialog/select-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial_dir: this.current || state.currentWorkspace }),
      });
      const data = await res.json();
      if (data.ok && data.path) {
        this.close();
        applyWorkspace(data.path);
      } else if (data.cancelled) {
        this.setStatus("Выбор отменён.");
      } else {
        // Именно здесь предыдущая версия молчала: теперь причина видна.
        this.setStatus(`${data.error} Используйте список папок ниже.`, true);
      }
    } catch (err) {
      this.setStatus(`Системный диалог недоступен: ${err}`, true);
    } finally {
      els.fmNative.disabled = false;
      els.fmNative.textContent = original;
    }
  },
};

function applyWorkspace(path) {
  const value = (path || "").trim();
  if (!value) return;
  updateWorkspaceUI(value);
  send({ type: "set_workspace", workspace: value });
  logLine(`Рабочая папка: ${value}`);
}

if (els.openFolderPickerBtn) {
  els.openFolderPickerBtn.addEventListener("click", () => folderBrowser.open());
}

document.querySelectorAll("[data-close-folder]").forEach((el) => {
  el.addEventListener("click", () => folderBrowser.close());
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && els.folderModal && !els.folderModal.hidden) {
    folderBrowser.close();
  }
});

els.fmUp.addEventListener("click", () => folderBrowser.navigate(els.fmUp.dataset.parent || ""));
els.fmGo.addEventListener("click", () => folderBrowser.navigate(els.fmPath.value.trim()));
els.fmPath.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    folderBrowser.navigate(els.fmPath.value.trim());
  }
});
els.fmNative.addEventListener("click", () => folderBrowser.openNative());
els.fmChoose.addEventListener("click", () => {
  const chosen = folderBrowser.selected || els.fmPath.value.trim();
  folderBrowser.close();
  applyWorkspace(chosen);
});


// ---------------------------------------------------------------- настройки

//: Поле ввода -> имя настройки на сервере.
const SETTING_FIELDS = {
  "set-api-key": "llm_api_key",
  "set-model": "default_model",
  "set-vision-model": "vision_model",
  "set-base-url": "llm_base_url",
  "set-language": "agent_language",
  "set-max-steps": "max_steps",
  "set-max-run-tokens": "max_run_tokens",
  "set-searxng-url": "searxng_url",
  "set-update-url": "update_url",
};

//: Поля-секреты (ключи): пустое значение = «не менять».
const SECRET_SETTING_FIELDS = {
  "set-brave-key": "brave_api_key",
  "set-tavily-key": "tavily_api_key",
};

async function openSettings() {
  els.settingsModal.hidden = false;
  els.settingsNote.hidden = true;

  try {
    const data = await (await fetch("/api/settings")).json();
    document.getElementById("set-model").value = data.default_model || "";
    document.getElementById("set-vision-model").value = data.vision_model || "";
    document.getElementById("set-base-url").value = data.llm_base_url || "";
    document.getElementById("set-language").value = data.agent_language || "";
    document.getElementById("set-max-steps").value = data.max_steps || 25;
    document.getElementById("set-max-run-tokens").value = data.max_run_tokens || 0;
    document.getElementById("set-allow-subagents").checked = Boolean(data.allow_subagents);
    document.getElementById("set-searxng-url").value = data.searxng_url || "";
    document.getElementById("set-update-url").value = data.update_url || "";

    // Ключи поиска: значение не показываем, только подсказку с хвостом.
    const brave = document.getElementById("set-brave-key");
    brave.value = "";
    brave.placeholder = data.brave_key_set ? `Сохранён ключ ${data.brave_key_hint}` : "необязательно";
    const tavily = document.getElementById("set-tavily-key");
    tavily.value = "";
    tavily.placeholder = data.tavily_key_set ? `Сохранён ключ ${data.tavily_key_hint}` : "необязательно";

    // Ключ не показываем: только подсказку с хвостом, чтобы узнать свой.
    const keyField = document.getElementById("set-api-key");
    keyField.value = "";
    keyField.placeholder = data.api_key_set ? `Сохранён ключ ${data.api_key_hint}` : "sk-or-v1-…";
    els.settingsPath.textContent = `Файл настроек: ${data.config_path}`;
  } catch (error) {
    showSettingsNote(`Не удалось загрузить настройки: ${error}`, true);
  }
}

function showSettingsNote(text, isError) {
  els.settingsNote.hidden = false;
  els.settingsNote.textContent = text;
  els.settingsNote.classList.toggle("is-error", Boolean(isError));
}

async function saveSettings() {
  const payload = {};
  Object.entries(SETTING_FIELDS).forEach(([id, field]) => {
    const value = document.getElementById(id).value.trim();
    // Пустое поле ключа означает «оставить прежний».
    if (field === "llm_api_key" && !value) return;
    payload[field] = value;
  });
  // Секретные ключи: пустое поле = «оставить прежний».
  Object.entries(SECRET_SETTING_FIELDS).forEach(([id, field]) => {
    const value = document.getElementById(id).value.trim();
    if (value) payload[field] = value;
  });
  // Переключатели — отдельно (у чекбокса нет .value).
  payload.allow_subagents = document.getElementById("set-allow-subagents").checked;

  els.saveSettingsBtn.disabled = true;
  els.saveSettingsBtn.textContent = "Сохраняю…";
  try {
    const result = await (await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })).json();

    if (!result.ok) {
      showSettingsNote(result.error || "Не удалось сохранить", true);
      return;
    }

    (result.warnings || []).forEach((warning) => logLine(warning, "warning"));
    updateSetupBanner(result.settings);
    showSettingsNote("Сохранено. Перезагружаю интерфейс…", false);
    // Настройки читает и сервер, и агент — перезагрузка гарантирует, что
    // новый ключ и модель применятся везде, а не только в новых чатах.
    setTimeout(() => window.location.reload(), 700);
  } catch (error) {
    showSettingsNote(`Ошибка сохранения: ${error}`, true);
  } finally {
    els.saveSettingsBtn.disabled = false;
    els.saveSettingsBtn.textContent = "Сохранить";
  }
}

/** Полоса первого запуска: без ключа агент бесполезен, и это надо сказать. */
function updateSetupBanner(settings) {
  if (!els.setupBanner) return;
  els.setupBanner.hidden = Boolean(settings && settings.api_key_set);
}

if (els.settingsBtn) {
  els.settingsBtn.addEventListener("click", openSettings);
  els.setupOpen.addEventListener("click", openSettings);
  els.saveSettingsBtn.addEventListener("click", saveSettings);
  document.querySelectorAll("[data-close-settings]").forEach((el) => {
    el.addEventListener("click", () => (els.settingsModal.hidden = true));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.settingsModal.hidden) els.settingsModal.hidden = true;
  });
  fetch("/api/settings")
    .then((response) => response.json())
    .then(updateSetupBanner)
    .catch(() => undefined);
}

// -------------------------------------------------------------- обновления

/** Проверка обновлений. Источник настраивается в UPDATE_URL (.env). */
async function checkUpdate(silent) {
  if (!els.updateBtn) return;
  if (!silent) els.updateNote.hidden = false, (els.updateNote.textContent = "Проверяю…");

  let info;
  try {
    info = await (await fetch("/api/update/check")).json();
  } catch (error) {
    if (!silent) showUpdateNote(`Не удалось проверить: ${error}`, false);
    return;
  }

  if (info.available) {
    showUpdateNote(`Есть версия ${info.version}`, true, info);
    els.updateBtn.classList.add("has-update");
  } else if (!silent) {
    showUpdateNote(info.error || "Установлена последняя версия", false);
  }
}

function showUpdateNote(text, offerInstall, info) {
  els.updateNote.hidden = false;
  els.updateNote.textContent = text;

  if (!offerInstall) return;
  if (info && info.notes) els.updateNote.title = info.notes;
  if (!info || !info.installable) {
    // Из исходников обновляться правильнее через git — так и говорим.
    els.updateNote.textContent = `${text} · обновите вручную`;
    return;
  }

  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn-install-update";
  button.textContent = "Установить и перезапустить";
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Устанавливаю…";
    try {
      const result = await (await fetch("/api/update/install", { method: "POST" })).json();
      button.textContent = result.ok ? "Приложение перезапустится" : `Ошибка: ${result.error}`;
    } catch (error) {
      button.textContent = `Ошибка: ${error}`;
    }
  });
  els.updateNote.appendChild(document.createElement("br"));
  els.updateNote.appendChild(button);
}

if (els.updateBtn) {
  els.updateBtn.addEventListener("click", () => checkUpdate(false));
  // Тихая проверка при запуске: если обновления нет, пользователь ничего не заметит.
  setTimeout(() => checkUpdate(true), 4000);
}

// ------------------------------------------------------ режим разрешений

function renderModeMenu() {
  els.modeMenu.innerHTML = state.modes
    .map(
      (mode) => `<button type="button" class="mode-option${mode.id === state.mode ? " is-active" : ""}"
           data-mode="${mode.id}">
        <span class="mode-option__title">${escapeHTML(mode.title)}</span>
        <span class="mode-option__hint">${escapeHTML(mode.hint)}</span>
      </button>`,
    )
    .join("");

  els.modeMenu.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      const selectedMode = button.dataset.mode;
      setMode(selectedMode);
      send({ type: "set_mode", mode: selectedMode });
      els.modeMenu.hidden = true;
    });
  });
}

function setMode(mode) {
  state.mode = mode;
  const found = state.modes.find((item) => item.id === mode);
  els.modeName.textContent = found ? found.title : mode;
  els.modeChip.title = found ? found.hint : "Режим разрешений";
  els.modeChip.classList.toggle("chip--warn", mode === "bypass");
  if (els.modeMenu && !els.modeMenu.hidden) {
    renderModeMenu();
  }
}

if (els.modeChip && els.modeMenu) {
  els.modeChip.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = els.modeMenu.hidden;
    if (willOpen) {
      renderModeMenu();
      const rect = els.modeChip.getBoundingClientRect();
      els.modeMenu.style.bottom = `${window.innerHeight - rect.top + 4}px`;
      els.modeMenu.style.left = `${Math.max(12, rect.left)}px`;
      els.modeMenu.style.top = 'auto';
    }
    els.modeMenu.hidden = !willOpen;
  });
  document.addEventListener("click", (event) => {
    if (els.modeMenu && !els.modeMenu.hidden && !els.modeMenu.contains(event.target) && !els.modeChip.contains(event.target)) {
      els.modeMenu.hidden = true;
    }
  });
}

// --------------------------------------------------------- быстрые команды

const CMD_PLACEHOLDER = "{{ввод}}";

async function loadCommands() {
  try {
    const data = await (await fetch("/api/commands")).json();
    state.commands = data.commands || [];
  } catch (error) {
    state.commands = [];
    logLine(`Не удалось загрузить команды: ${error}`, "warning");
  }
}

/** Разворачивает шаблон: аргумент идёт в {{ввод}} или дописывается снизу. */
function expandCommand(command, argument) {
  const arg = (argument || "").trim();
  if (command.template.includes(CMD_PLACEHOLDER)) {
    return command.template.split(CMD_PLACEHOLDER).join(arg);
  }
  return arg ? `${command.template}\n\n${arg}` : command.template;
}

/** Разбирает ввод «/имя остаток» в {name, rest} или null. */
function parseSlash(value) {
  const match = value.match(/^\/([\wа-яё\-]*)(?:\s([\s\S]*))?$/i);
  if (!match) return null;
  return { name: match[1], rest: match[2] || "", hasSpace: value.slice(1 + match[1].length, 2 + match[1].length) === " " };
}

let cmdActiveIndex = 0;

function updateCommandPopup() {
  if (!els.cmdPopup) return;
  const parsed = parseSlash(els.input.value);
  // Показываем автодополнение, только пока набирают ИМЯ (нет пробела после него).
  if (!parsed || parsed.hasSpace) {
    els.cmdPopup.hidden = true;
    return;
  }
  const query = parsed.name.toLowerCase();
  const matches = state.commands.filter((c) => c.name.toLowerCase().startsWith(query)).slice(0, 8);
  if (!matches.length) {
    els.cmdPopup.hidden = true;
    return;
  }
  cmdActiveIndex = Math.min(cmdActiveIndex, matches.length - 1);
  els.cmdPopup.innerHTML = matches
    .map(
      (c, i) =>
        `<button type="button" class="cmd-item${i === cmdActiveIndex ? " is-active" : ""}" data-name="${escapeHTML(c.name)}">
          <span class="cmd-item__name">/${escapeHTML(c.name)}</span>
          <span class="cmd-item__desc">${escapeHTML(c.description || "")}</span>
        </button>`,
    )
    .join("");
  els.cmdPopup.hidden = false;
  els.cmdPopup.querySelectorAll(".cmd-item").forEach((btn) => {
    btn.addEventListener("mousedown", (event) => {
      event.preventDefault(); // не терять фокус ввода
      applyCommand(btn.dataset.name);
    });
  });
}

function applyCommand(name) {
  const command = state.commands.find((c) => c.name === name);
  if (!command) return;
  const parsed = parseSlash(els.input.value);
  const argument = parsed ? parsed.rest : "";
  els.input.value = expandCommand(command, argument);
  els.cmdPopup.hidden = true;
  els.input.focus();
  // Ставим курсор в конец (или на место {{ввод}}, если пусто).
  const pos = els.input.value.length;
  els.input.setSelectionRange(pos, pos);
  autoGrow();
}

function commandPopupVisible() {
  return els.cmdPopup && !els.cmdPopup.hidden;
}

/** Навигация по попапу стрелками и Enter — вызывается из обработчика textarea. */
function handleCommandKey(event) {
  if (!commandPopupVisible()) return false;
  const items = els.cmdPopup.querySelectorAll(".cmd-item");
  if (!items.length) return false;

  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    cmdActiveIndex = (cmdActiveIndex + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    updateCommandPopup();
    return true;
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    applyCommand(items[cmdActiveIndex].dataset.name);
    return true;
  }
  if (event.key === "Escape") {
    els.cmdPopup.hidden = true;
    return true;
  }
  return false;
}

// -------- управление командами (модалка) --------

async function openCommands() {
  if (!els.commandsModal) return;
  els.commandsModal.hidden = false;
  await loadCommands();
  renderCommandsList();
}

function renderCommandsList() {
  els.commandsCount.textContent = `команд: ${state.commands.length}`;
  els.commandsList.innerHTML = state.commands
    .map(
      (c) =>
        `<div class="memory-item">
          <div class="memory-item__body">
            <span class="memory-item__cat">/${escapeHTML(c.name)}${c.builtin ? "" : " · своя"}</span>
            <span class="memory-item__text">${escapeHTML(c.description || c.template.slice(0, 80))}</span>
          </div>
          ${c.builtin ? "" : `<button class="memory-item__forget" data-del="${escapeHTML(c.name)}" title="Удалить">×</button>`}
        </div>`,
    )
    .join("");
  els.commandsList.querySelectorAll(".memory-item__forget").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/commands/${encodeURIComponent(btn.dataset.del)}`, { method: "DELETE" });
      await loadCommands();
      renderCommandsList();
    });
  });
}

async function saveCommand() {
  const name = els.cmdNewName.value.trim();
  const template = els.cmdNewTemplate.value.trim();
  if (!name || !template) {
    logLine("Команде нужны имя и шаблон", "warning");
    return;
  }
  try {
    const res = await fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, template, description: els.cmdNewDesc.value.trim() }),
    });
    if (!res.ok) {
      const err = await res.json();
      logLine(err.detail || "Не удалось сохранить команду", "error");
      return;
    }
    els.cmdNewName.value = els.cmdNewDesc.value = els.cmdNewTemplate.value = "";
    await loadCommands();
    renderCommandsList();
    logLine(`Команда /${name} сохранена`);
  } catch (error) {
    logLine(`Ошибка сохранения команды: ${error}`, "error");
  }
}

if (els.commandsBtn) {
  els.commandsBtn.addEventListener("click", openCommands);
  els.cmdSaveBtn.addEventListener("click", saveCommand);
  els.commandsModal.querySelectorAll("[data-close-commands]").forEach((el) => {
    el.addEventListener("click", () => (els.commandsModal.hidden = true));
  });
}

// ------------------------------------------------------------------- секреты

/** Открывает панель секретов; name — предзаполнить имя (по запросу агента). */
async function openSecrets(name) {
  if (!els.secretsModal) return;
  els.secretsModal.hidden = false;
  if (name && els.secretNewName) {
    els.secretNewName.value = name;
    setTimeout(() => els.secretNewValue && els.secretNewValue.focus(), 50);
  }
  await loadSecrets();
}

async function loadSecrets() {
  try {
    const ws = state.currentWorkspace || "";
    const res = await fetch(`/api/secrets?workspace=${encodeURIComponent(ws)}`);
    const data = await res.json();
    const secrets = data.secrets || [];
    els.secretsCount.textContent = `секретов: ${secrets.length}`;
    els.secretsList.innerHTML =
      secrets
        .map(
          (s) =>
            `<div class="memory-item">
              <div class="memory-item__body">
                <span class="memory-item__cat">${escapeHTML(s.name)}</span>
                <span class="memory-item__text">${escapeHTML(s.masked)}</span>
              </div>
              <button class="memory-item__forget" data-del="${escapeHTML(s.name)}" title="Удалить">×</button>
            </div>`,
        )
        .join("") || `<div class="empty-state">Секретов пока нет.</div>`;
    els.secretsList.querySelectorAll(".memory-item__forget").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const ws2 = encodeURIComponent(state.currentWorkspace || "");
        await fetch(`/api/secrets/${encodeURIComponent(btn.dataset.del)}?workspace=${ws2}`, {
          method: "DELETE",
        });
        await loadSecrets();
      });
    });
  } catch (error) {
    els.secretsList.innerHTML = `<div class="empty-state">Не удалось загрузить: ${escapeHTML(String(error))}</div>`;
  }
}

async function saveSecret() {
  const name = els.secretNewName.value.trim();
  const value = els.secretNewValue.value;
  if (!name || !value) {
    logLine("Секрету нужны имя и значение", "warning");
    return;
  }
  try {
    const res = await fetch("/api/secrets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, value, workspace: state.currentWorkspace || "" }),
    });
    if (!res.ok) {
      const err = await res.json();
      logLine(err.detail || "Не удалось сохранить секрет", "error");
      return;
    }
    // Значение из поля стираем сразу — не держим его в DOM дольше нужного.
    els.secretNewName.value = "";
    els.secretNewValue.value = "";
    await loadSecrets();
    logLine(`Секрет ${name} сохранён (в .env рабочей папки)`);
  } catch (error) {
    logLine(`Ошибка сохранения секрета: ${error}`, "error");
  }
}

if (els.secretsBtn) {
  els.secretsBtn.addEventListener("click", () => openSecrets());
  els.secretSaveBtn.addEventListener("click", saveSecret);
  els.secretsModal.querySelectorAll("[data-close-secrets]").forEach((el) => {
    el.addEventListener("click", () => (els.secretsModal.hidden = true));
  });
}

if (els.input && els.cmdPopup) {
  els.input.addEventListener("input", updateCommandPopup);
  els.input.addEventListener("blur", () => setTimeout(() => (els.cmdPopup.hidden = true), 150));
  loadCommands();
}

// --------------------------------------------------------------- пресеты

async function loadPresets() {
  try {
    const data = await (await fetch("/api/presets")).json();
    state.presets = data.presets || [];
  } catch (error) {
    state.presets = [];
    logLine(`Не удалось загрузить пресеты: ${error}`, "warning");
  }
  renderPresetMenu();
}

/** Применяет пресет: разом выставляет модель, режим, веб-поиск и навыки. */
function applyPreset(preset) {
  if (preset.model) els.modelInput.value = preset.model;
  if (preset.approval_mode) {
    send({ type: "set_mode", mode: preset.approval_mode });
    setMode(preset.approval_mode);
  }
  setWebMode(preset.web_mode || "auto");
  state.deepResearch = Boolean(preset.deep_research);
  state.chosenSkills = new Set(preset.skills || []);
  renderSkills();
  renderFlags();

  els.presetName.textContent = preset.name;
  els.presetChip.classList.add("chip--active");
  logLine(`Применён пресет «${preset.name}»`);
}

/** Собирает пресет из текущего состояния композера. */
function currentAsPreset(name) {
  return {
    name,
    model: els.modelInput.value.trim(),
    approval_mode: state.mode,
    web_mode: state.webMode,
    deep_research: state.deepResearch,
    skills: [...state.chosenSkills],
  };
}

async function savePreset() {
  const name = prompt("Название пресета (текущие настройки будут сохранены):");
  if (!name || !name.trim()) return;
  try {
    const res = await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentAsPreset(name.trim())),
    }).then((r) => r.json());
    if (res.ok) {
      logLine(`Пресет «${res.preset.name}» сохранён`);
      await loadPresets();
    } else {
      logLine("Не удалось сохранить пресет", "error");
    }
  } catch (error) {
    logLine(`Ошибка сохранения пресета: ${error}`, "error");
  }
}

function renderPresetMenu() {
  const items = state.presets
    .map(
      (preset) =>
        `<button type="button" class="mode-option preset-option" data-preset-name="${escapeHTML(preset.name)}">
          <span class="mode-option__title">${escapeHTML(preset.name)}</span>
          <span class="mode-option__hint">${escapeHTML(presetHint(preset))}</span>
          ${preset.builtin ? "" : `<span class="preset-del" data-del="${escapeHTML(preset.name)}" title="Удалить">×</span>`}
        </button>`,
    )
    .join("");
  els.presetMenu.innerHTML =
    items +
    `<button type="button" class="mode-option preset-save"><span class="mode-option__title">＋ Сохранить текущие</span></button>`;

  els.presetMenu.querySelectorAll(".preset-option").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (event.target.closest(".preset-del")) return;
      const preset = state.presets.find((p) => p.name === button.dataset.presetName);
      if (preset) {
        applyPreset(preset);
        els.presetMenu.hidden = true;
      }
    });
  });
  els.presetMenu.querySelectorAll(".preset-del").forEach((del) => {
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      await fetch(`/api/presets/${encodeURIComponent(del.dataset.del)}`, { method: "DELETE" });
      await loadPresets();
    });
  });
  els.presetMenu.querySelector(".preset-save").addEventListener("click", () => {
    els.presetMenu.hidden = true;
    savePreset();
  });
}

function presetHint(preset) {
  const modeName = (state.modes.find((m) => m.id === preset.approval_mode) || {}).title || preset.approval_mode;
  const web = { auto: "", force: "поиск: всегда", off: "поиск: выкл" }[preset.web_mode] || "";
  const bits = [modeName, web, preset.skills.length ? `навыки: ${preset.skills.length}` : ""].filter(Boolean);
  return bits.join(" · ");
}

if (els.presetChip && els.presetMenu) {
  els.presetChip.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = els.presetMenu.hidden;
    if (willOpen) {
      renderPresetMenu();
      const rect = els.presetChip.getBoundingClientRect();
      els.presetMenu.style.bottom = `${window.innerHeight - rect.top + 4}px`;
      els.presetMenu.style.left = `${Math.max(12, rect.left)}px`;
      els.presetMenu.style.top = 'auto';
    }
    els.presetMenu.hidden = !willOpen;
  });
  document.addEventListener("click", (event) => {
    if (els.presetMenu && !els.presetMenu.hidden && !els.presetMenu.contains(event.target) && !els.presetChip.contains(event.target)) {
      els.presetMenu.hidden = true;
    }
  });
  loadPresets();
}

// ----------------------------------------------------- тема и панель работы

const THEME_KEY = "local_ai_theme";
const WORK_KEY = "local_ai_work_panel";
const WORK_WIDTH_KEY = "local_ai_work_width";

function applyTheme(theme) {
  // Видимость иконок задана в CSS через [data-theme]: атрибут hidden на <svg>
  // не работает — это HTML-атрибут, а svg живёт в своём пространстве имён.
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  els.themeBtn.title = theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему";
  if (window.AgentTerminal) window.AgentTerminal.applyTheme();
}

function applyWorkPanel(state) {
  els.layout.dataset.work = state;
  localStorage.setItem(WORK_KEY, state);
  if (state !== "open") {
    // Панель закрыта — снимаем подсветку со всех кнопок в шапке.
    document.querySelectorAll(".work-toggle-btn.active").forEach((b) => b.classList.remove("active"));
  }
}

/** Открывает панель работы на нужной вкладке, а повторный клик по активной — закрывает. */
function toggleWorkTab(tabName) {
  const isOpen = els.layout.dataset.work === "open";
  const activeTab = document.querySelector(".work-toggle-btn.active")?.dataset.openTab;
  if (isOpen && activeTab === tabName) {
    applyWorkPanel("collapsed");
    return;
  }
  document.querySelectorAll(".work-toggle-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.openTab === tabName),
  );
  // Панель выезжает под шапкой — подгоняем верхнюю границу под её высоту.
  const header = document.querySelector(".chat-header");
  if (header) {
    els.layout.style.setProperty("--work-top", `${header.offsetHeight}px`);
  }
  switchTab(tabName);
  applyWorkPanel("open");
}

if (els.themeBtn) {
  // Приоритет: сохранённый выбор пользователя → системная тема → тёмная по умолчанию.
  const savedTheme = localStorage.getItem(THEME_KEY);
  const systemLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  applyTheme(savedTheme || (systemLight ? "light" : "dark"));
  els.themeBtn.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
}

if (els.layout) {
  // По умолчанию панель закрыта — чат во всю ширину, как в Claude.
  applyWorkPanel("collapsed");
  const closePanel = () => applyWorkPanel("collapsed");
  if (els.workClose) els.workClose.addEventListener("click", closePanel);
  if (els.workBackdrop) els.workBackdrop.addEventListener("click", closePanel);
  // Кнопки в шапке: открыть/переключить/закрыть панель на нужной вкладке.
  document.querySelectorAll(".work-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => toggleWorkTab(btn.dataset.openTab));
  });
  // Esc закрывает панель.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && els.layout.dataset.work === "open") closePanel();
  });
}

function applyWorkWidth(width) {
  const value = Math.max(280, Math.min(720, Number(width) || 400));
  document.documentElement.style.setProperty("--work-w", `${value}px`);
  localStorage.setItem(WORK_WIDTH_KEY, String(value));
}

if (els.workResize) {
  applyWorkWidth(localStorage.getItem(WORK_WIDTH_KEY) || 400);
  let resizing = false;
  els.workResize.addEventListener("pointerdown", (event) => {
    if (els.layout.dataset.work === "collapsed") return;
    resizing = true;
    els.workResize.setPointerCapture(event.pointerId);
    document.body.classList.add("is-resizing-work-panel");
  });
  els.workResize.addEventListener("pointermove", (event) => {
    if (!resizing) return;
    const drawer = document.getElementById("work-drawer");
    if (drawer) {
      const width = drawer.getBoundingClientRect().right - event.clientX;
      applyWorkWidth(width);
    }
  });
  const stopResize = () => {
    resizing = false;
    document.body.classList.remove("is-resizing-work-panel");
  };
  els.workResize.addEventListener("pointerup", stopResize);
  els.workResize.addEventListener("pointercancel", stopResize);
}

// Вкладки монитора
els.tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

const changesRefreshBtn = document.getElementById("btn-changes-refresh");
if (changesRefreshBtn) {
  changesRefreshBtn.addEventListener("click", () => loadChanges());
}

// План
function togglePlan() {
  state.planCollapsed = !state.planCollapsed;
  els.planStepsList.hidden = state.planCollapsed;
  els.planToggleBtn.textContent = state.planCollapsed ? "Показать" : "Скрыть";
}

els.planToggleBtn.addEventListener("click", (event) => {
  event.stopPropagation();
  togglePlan();
});

// Превью

window.addEventListener("DOMContentLoaded", () => {
  connect();
  loadSessionsList();
});
