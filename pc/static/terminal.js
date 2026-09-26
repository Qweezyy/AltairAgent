/**
 * Интерактивный терминал на xterm.js.
 *
 * Подключается к /ws/terminal лениво — только когда пользователь впервые
 * открывает вкладку «Терминал», чтобы не держать оболочку зря. Байты вывода
 * приходят текстовыми кадрами и пишутся в xterm как есть; нажатия и смена
 * размера уходят на сервер JSON-командами.
 */
(function () {
  "use strict";

  const state = {
    term: null,
    fit: null,
    socket: null,
    started: false,
    resizeObserver: null,
  };

  function els() {
    return {
      host: document.getElementById("terminal-host"),
      status: document.getElementById("terminal-status"),
      restart: document.getElementById("btn-terminal-restart"),
    };
  }

  function setStatus(text, kind) {
    const { status } = els();
    if (!status) return;
    status.textContent = text;
    status.dataset.kind = kind || "";
  }

  /** Тема xterm под текущую тему приложения (светлую/тёмную). */
  function themeColors() {
    const dark = document.documentElement.dataset.theme !== "light";
    return dark
      ? { background: "#141318", foreground: "#e6e1d8", cursor: "#e6a23c",
          selectionBackground: "#3a3730" }
      : { background: "#fbf9f5", foreground: "#2a2620", cursor: "#c8791a",
          selectionBackground: "#e7ddc9" };
  }

  function buildTerminal() {
    const term = new window.Terminal({
      fontFamily: "var(--font-mono, Consolas, monospace)",
      fontSize: 13,
      cursorBlink: true,
      convertEol: false,
      theme: themeColors(),
    });
    const FitAddon = window.FitAddon && window.FitAddon.FitAddon;
    const fit = FitAddon ? new FitAddon() : null;
    if (fit) term.loadAddon(fit);
    return { term, fit };
  }

  function socketUrl() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const dims = state.fit ? state.fit.proposeDimensions() : null;
    const cols = (dims && dims.cols) || 80;
    const rows = (dims && dims.rows) || 24;
    // Терминал открываем в рабочей папке текущего чата, если она известна.
    const ws = localStorage.getItem("local_ai_workspace") || "";
    const params = new URLSearchParams({ cols, rows });
    if (ws) params.set("cwd", ws);
    return `${proto}://${location.host}/ws/terminal?${params.toString()}`;
  }

  function connect() {
    const socket = new WebSocket(socketUrl());
    state.socket = socket;
    setStatus("Подключение…", "pending");

    socket.addEventListener("open", () => {
      setStatus("Подключён", "ok");
      sendResize();
    });
    socket.addEventListener("message", (event) => {
      state.term.write(event.data);
    });
    socket.addEventListener("close", () => {
      setStatus("Сессия завершена — ↻ для перезапуска", "closed");
      state.socket = null;
    });
    socket.addEventListener("error", () => {
      setStatus("Ошибка соединения", "closed");
    });
  }

  function sendResize() {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
    if (state.fit) {
      try {
        state.fit.fit();
      } catch {
        /* контейнер ещё скрыт — размер применим позже */
      }
    }
    const cols = state.term.cols;
    const rows = state.term.rows;
    state.socket.send(JSON.stringify({ type: "resize", cols, rows }));
  }

  /** Первый запуск: создаёт xterm, вешает обработчики, открывает сокет. */
  function start() {
    if (state.started) return;
    const { host, restart } = els();
    if (!host || !window.Terminal) return;
    // Opening xterm into a 0×0 host (pane just shown, layout not flushed yet)
    // leaves it unrendered until a later resize — that was the "terminal only
    // works after a restart" bug. Wait for the host to have a real size first.
    if (host.clientWidth === 0 || host.clientHeight === 0) {
      if ((state._waitFrames = (state._waitFrames || 0) + 1) < 120) {
        requestAnimationFrame(start);
      }
      return;
    }
    state.started = true;

    const built = buildTerminal();
    state.term = built.term;
    state.fit = built.fit;
    state.term.open(host);
    // Несколько повторных fit после первой отрисовки: холодный старт (особенно в
    // упакованном Edge App) рисует не сразу, а без fit первый экран остаётся
    // пустым до ручного ресайза — это и был баг «работает только после ↻».
    setTimeout(() => sendResize(), 0);
    setTimeout(() => sendResize(), 200);
    setTimeout(() => { sendResize(); state.term && state.term.refresh(0, state.term.rows - 1); }, 600);

    // Нажатия пользователя → на сервер.
    state.term.onData((data) => {
      if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify({ type: "input", data }));
      }
    });

    // Терминал перерисовывается под размер панели.
    if (window.ResizeObserver) {
      state.resizeObserver = new ResizeObserver(() => sendResize());
      state.resizeObserver.observe(host);
    }

    if (restart) {
      restart.addEventListener("click", () => restartSession());
    }

    connect();
  }

  function restartSession() {
    if (state.socket) {
      try {
        state.socket.close();
      } catch {
        /* уже закрыт */
      }
      state.socket = null;
    }
    if (state.term) state.term.reset();
    connect();
  }

  /** Вызывается из app.js при переключении на вкладку «Терминал». */
  function onTabShown() {
    state._waitFrames = 0;           // свежий бюджет ожидания размера на каждое открытие
    start();
    // Уже стартовали, но сокет отвалился (сервер перезапускался и т.п.) — тихо
    // переподключаемся, чтобы не пришлось жать ↻ вручную.
    if (state.started && (!state.socket || state.socket.readyState > WebSocket.OPEN)) {
      connect();
    }
    // Панель могла быть скрыта при первом open() — подгоняем размер сейчас.
    setTimeout(() => sendResize(), 30);
    setTimeout(() => sendResize(), 250);
    if (state.term) state.term.focus();
  }

  function applyTheme() {
    if (state.term) state.term.options.theme = themeColors();
  }

  /** Отправляет строку в терминал как ввод (для «открыть папку в терминале»).
   *  Если сокет ещё не готов — ждёт и повторяет. */
  function sendText(text, tries) {
    tries = tries || 0;
    onTabShown(); // гарантируем старт/переподключение
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
      state.socket.send(JSON.stringify({ type: "input", data: text }));
      if (state.term) state.term.focus();
    } else if (tries < 40) {
      setTimeout(() => sendText(text, tries + 1), 100);
    }
  }

  window.AgentTerminal = { onTabShown, applyTheme, sendText };
})();
