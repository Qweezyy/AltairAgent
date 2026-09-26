/* Групповой чат агентов — клиент эксперимента.
   Логика: загрузить конфигурацию, дать удобно собрать команду, запустить прогон
   через /ws/swarm и живо показывать чат, ходы и действия участников. */

(function () {
  "use strict";

  var PALETTE = ["#d97757", "#7fa88b", "#6d9dc5", "#c98bb9", "#d8a657", "#8f8ce0", "#5fb3a1", "#cf6b52"];

  var cfg = null;                 // конфигурация с сервера
  var members = [];               // редактируемая команда
  var colorByName = {};           // имя -> цвет (на время прогона)
  var roleByName = {};
  var running = false;
  var ws = null;

  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------------- WebSocket

  function connect() {
    var proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(proto + "://" + location.host + "/ws/swarm");
    ws.onopen = function () { $("conn").textContent = "подключено"; };
    ws.onclose = function () {
      $("conn").textContent = "нет связи";
      setRunning(false);
      setTimeout(connect, 1500);
    };
    ws.onerror = function () { $("conn").textContent = "ошибка связи"; };
    ws.onmessage = function (e) {
      var msg;
      try { msg = JSON.parse(e.data); } catch (_) { return; }
      handle(msg);
    };
  }

  function send(obj) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  }

  function handle(msg) {
    switch (msg.type) {
      case "swarm.ready": applyConfig(msg.config); break;
      case "swarm.started": onStarted(msg); break;
      case "swarm.turn": onTurn(msg); break;
      case "swarm.message": onMessage(msg); break;
      case "swarm.activity": onActivity(msg); break;
      case "swarm.finished": onFinished(msg); break;
      case "swarm.error": onError(msg.message); break;
      case "log":
        if (msg.level === "warning" || msg.level === "error") logActivity(msg.text, msg.level === "error");
        break;
    }
  }

  // ------------------------------------------------------------- конфиг

  function applyConfig(c) {
    if (cfg) return;                 // одна инициализация на страницу
    cfg = c;
    if (!$("model").value) $("model").placeholder = c.model || "по умолчанию";
    if (!$("workspace").value) $("workspace").value = c.workspace || "";
    if (c.max_rounds) $("rounds").max = String(c.max_rounds);
    members = (c.team || []).map(cloneMember);
    renderMembers();
  }

  function cloneMember(m) {
    return {
      name: m.name || "",
      role: m.role || "участник",
      charter: m.charter || "",
      tools: (m.tools || []).slice(),
      _open: false,
    };
  }

  // ------------------------------------------------------- редактор команды

  function renderMembers() {
    var box = $("members");
    box.innerHTML = "";
    members.forEach(function (m, i) {
      box.appendChild(memberCard(m, i));
    });
    validateTeam();
  }

  function memberCard(m, idx) {
    var color = PALETTE[idx % PALETTE.length];
    var el = document.createElement("div");
    el.className = "member";
    el.style.setProperty("--m-color", color);

    var top = document.createElement("div");
    top.className = "m-top";
    top.innerHTML = '<span class="m-dot"></span>';

    var name = inputEl("text", m.name, "имя");
    name.className = "m-name-input";
    name.style.flex = "1";
    name.oninput = function () { m.name = name.value; validateTeam(); };

    var role = inputEl("text", m.role, "роль");
    role.className = "m-role";
    role.oninput = function () { m.role = role.value; };

    top.appendChild(name);
    top.appendChild(role);
    el.appendChild(top);

    var charter = document.createElement("textarea");
    charter.rows = 3;
    charter.value = m.charter;
    charter.placeholder = "Зона ответственности: что МОЖНО и что НЕЛЬЗЯ (это делают коллеги).";
    charter.oninput = function () { m.charter = charter.value; };
    el.appendChild(charter);

    // Инструменты: сводка + разворачиваемый пикер
    var summary = document.createElement("div");
    summary.className = "tools-summary";
    renderToolSummary(summary, m);
    el.appendChild(summary);

    var toggle = document.createElement("button");
    toggle.className = "ghost btn-sm tools-toggle";
    toggle.textContent = m._open ? "Скрыть инструменты" : "Настроить инструменты (" + m.tools.length + ")";
    toggle.onclick = function () {
      m._open = !m._open;
      renderMembers();
    };
    el.appendChild(toggle);

    if (m._open) el.appendChild(toolsPicker(m, summary));

    var actions = document.createElement("div");
    actions.className = "member-actions";
    actions.appendChild(smallBtn("Дублировать", function () {
      var copy = cloneMember(m);
      copy.name = uniqueName(m.name);
      members.splice(idx + 1, 0, copy);
      renderMembers();
    }));
    var del = smallBtn("Удалить", function () {
      members.splice(idx, 1);
      renderMembers();
    });
    del.className += " danger";
    actions.appendChild(del);
    el.appendChild(actions);

    return el;
  }

  function renderToolSummary(box, m) {
    box.innerHTML = "";
    if (!m.tools.length) {
      var none = document.createElement("span");
      none.className = "tool-chip";
      none.textContent = "только чат";
      box.appendChild(none);
      return;
    }
    var danger = dangerousSet();
    m.tools.forEach(function (t) {
      var chip = document.createElement("span");
      chip.className = "tool-chip" + (danger[t] ? " danger" : "");
      chip.textContent = t;
      box.appendChild(chip);
    });
  }

  function toolsPicker(m, summaryBox) {
    var picker = document.createElement("div");
    picker.className = "tools-picker";
    var byCat = {};
    (cfg.tools || []).forEach(function (t) {
      (byCat[t.category] = byCat[t.category] || []).push(t);
    });
    var CAT_RU = { read: "чтение", edit: "правка файлов", execute: "запуск кода/команд", network: "сеть" };
    Object.keys(byCat).sort().forEach(function (cat) {
      var head = document.createElement("div");
      head.className = "cat";
      head.textContent = CAT_RU[cat] || cat;
      picker.appendChild(head);
      byCat[cat].forEach(function (t) {
        var row = document.createElement("label");
        row.className = "tool-opt";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = m.tools.indexOf(t.name) !== -1;
        cb.onchange = function () {
          var i = m.tools.indexOf(t.name);
          if (cb.checked && i === -1) m.tools.push(t.name);
          else if (!cb.checked && i !== -1) m.tools.splice(i, 1);
          renderToolSummary(summaryBox, m);
        };
        var text = document.createElement("span");
        text.innerHTML = "<code>" + t.name + "</code> <span class='d'>— " + escapeHtml(t.description).slice(0, 90) + "</span>";
        row.appendChild(cb);
        row.appendChild(text);
        picker.appendChild(row);
      });
    });
    return picker;
  }

  var _dangerCache = null;
  function dangerousSet() {
    if (_dangerCache) return _dangerCache;
    _dangerCache = {};
    (cfg.tools || []).forEach(function (t) { if (t.dangerous) _dangerCache[t.name] = true; });
    return _dangerCache;
  }

  function uniqueName(base) {
    var names = members.map(function (m) { return m.name; });
    var n = base + " (копия)";
    var i = 2;
    while (names.indexOf(n) !== -1) { n = base + " (копия " + i + ")"; i++; }
    return n;
  }

  function validateTeam() {
    var warn = $("team-warn");
    var problems = [];
    if (members.length < 2) problems.push("Нужно минимум двое участников.");
    var names = members.map(function (m) { return (m.name || "").trim(); });
    if (names.some(function (n) { return !n; })) problems.push("У всех участников должно быть имя.");
    var dup = names.filter(function (n, i) { return n && names.indexOf(n) !== i; });
    if (dup.length) problems.push("Имена должны быть уникальными: " + dup.join(", ") + ".");
    if (members.some(function (m) { return !(m.charter || "").trim(); }))
      problems.push("У каждого участника опишите зону ответственности.");

    if (cfg && cfg.max_members && members.length > cfg.max_members)
      problems.push("Слишком много участников (максимум " + cfg.max_members + ").");

    warn.textContent = problems.length
      ? "⚠ " + problems.join(" ")
      : "Совет: раздайте участникам ВЗАИМОДОПОЛНЯЮЩИЕ права — так, чтобы никто не мог закрыть задачу в одиночку.";
    warn.style.color = problems.length ? "var(--clay)" : "var(--text-3)";
    $("run").disabled = running || problems.length > 0;
    return problems.length === 0;
  }

  // ----------------------------------------------------------- запуск

  function run() {
    if (!validateTeam()) return;
    var task = $("task").value.trim();
    if (!task) { toast("Опишите общую задачу команды."); return; }

    clearFeed();
    colorByName = {};
    roleByName = {};
    members.forEach(function (m, i) {
      colorByName[m.name] = PALETTE[i % PALETTE.length];
      roleByName[m.name] = m.role;
    });

    send({
      type: "run",
      task: task,
      rounds: parseInt($("rounds").value, 10) || 6,
      model: $("model").value.trim(),
      workspace: $("workspace").value.trim(),
      members: members.map(function (m) {
        return { name: m.name.trim(), role: m.role.trim(), charter: m.charter.trim(), tools: m.tools };
      }),
    });
    setStatus("running", "запуск…");
    setRunning(true);
  }

  function setRunning(on) {
    running = on;
    $("stop").disabled = !on;
    validateTeam();
    ["task", "rounds", "model", "workspace", "add-member", "reset-team", "import-team"].forEach(function (id) {
      var el = $(id); if (el) el.disabled = on;
    });
  }

  // ----------------------------------------------------------- события прогона

  var msgCount = 0;

  function onStarted(msg) {
    setStatus("running", "работают одновременно");
    msgCount = 0;
    buildStrip(msg.members || []);
    updateCounters();
    logActivity("Старт: задача «" + msg.task + "», папка " + msg.workspace);
  }

  function onTurn(msg) {
    addActive(msg.member);
    setStripActivity(msg.member, "думает…");
    updateCounters();
  }

  function onMessage(msg) {
    appendMessage(msg);
    msgCount++;
    setStripActivity(msg.author, "написал в чат");
    updateCounters();
  }

  function onActivity(msg) {
    if (msg.tool === "ждёт коллег") {
      removeActive(msg.member);
      setStripActivity(msg.member, "ждёт коллег");
    } else {
      addActive(msg.member);
      setStripActivity(msg.member, msg.tool + (msg.ok ? "" : " ✗"));
    }
    updateCounters();
    if (!msg.ok || msg.tool !== "ждёт коллег") {
      logActivity("[" + msg.member + "] " + msg.tool + (msg.ok ? " — ok" : " — ошибка" + (msg.detail ? ": " + msg.detail : "")), !msg.ok);
    }
  }

  function onFinished(msg) {
    setStatus("done", "завершено: " + msg.reason);
    clearActive();
    Array.prototype.forEach.call($("strip").children, function (c) {
      var a = c.querySelector(".act"); if (a) a.textContent = "готово";
    });
    setRunning(false);
    updateCounters();
  }

  function updateCounters() {
    var active = $("strip").querySelectorAll(".mstrip.active").length;
    var parts = ["сообщений: " + msgCount];
    if (running) parts.push(active ? ("работают: " + active) : "все ждут");
    $("round").textContent = parts.join(" · ");
  }

  function onError(text) {
    setStatus("error", "ошибка");
    toast(text || "Ошибка эксперимента");
    logActivity(text, true);
    setRunning(false);
  }

  // ----------------------------------------------------------- рендер ленты

  function clearFeed() {
    $("feed").innerHTML = "";
    $("activity").innerHTML = "";
    $("activity").hidden = true;
    $("strip").innerHTML = "";
  }

  function appendMessage(msg) {
    var feed = $("feed");
    var isClient = !!msg.role && msg.role === "заказчик";
    var el = document.createElement("div");
    el.className = "msg" + (isClient ? " client" : "");
    var color = colorByName[msg.author] || "var(--text-2)";
    el.style.setProperty("--m-color", color);

    var who = document.createElement("div");
    who.className = "who";
    var roleTxt = roleByName[msg.author] || msg.role || "";
    who.innerHTML = escapeHtml(msg.author) + (roleTxt ? " <span class='role'>" + escapeHtml(roleTxt) + "</span>" : "");
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = msg.text;

    el.appendChild(who);
    el.appendChild(bubble);
    feed.appendChild(el);
    feed.scrollTop = feed.scrollHeight;
  }

  function buildStrip(list) {
    var strip = $("strip");
    strip.innerHTML = "";
    list.forEach(function (m) {
      var el = document.createElement("div");
      el.className = "mstrip";
      el.dataset.name = m.name;
      el.style.setProperty("--m-color", colorByName[m.name] || "var(--ochre)");
      el.innerHTML = '<span class="dot"></span><b>' + escapeHtml(m.name) + "</b><span class='act'></span>";
      strip.appendChild(el);
    });
  }

  function stripEl(name) {
    return $("strip").querySelector('[data-name="' + cssEscape(name) + '"]');
  }
  function addActive(name) {
    var el = stripEl(name);
    if (el) el.classList.add("active");
  }
  function removeActive(name) {
    var el = stripEl(name);
    if (el) el.classList.remove("active");
  }
  function clearActive() {
    Array.prototype.forEach.call($("strip").children, function (c) { c.classList.remove("active"); });
  }
  function setStripActivity(name, text) {
    var el = stripEl(name);
    if (el) { var a = el.querySelector(".act"); if (a) a.textContent = text; }
  }

  function logActivity(text, err) {
    var box = $("activity");
    box.hidden = false;
    var line = document.createElement("div");
    line.className = "a-line" + (err ? " err" : "");
    line.textContent = text;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  // ----------------------------------------------------------- утилиты UI

  function setStatus(kind, text) {
    var el = $("status");
    el.className = "status-pill " + kind;
    el.textContent = text;
  }

  function inputEl(type, value, ph) {
    var i = document.createElement("input");
    i.type = type; i.value = value || ""; if (ph) i.placeholder = ph;
    return i;
  }
  function smallBtn(text, fn) {
    var b = document.createElement("button");
    b.className = "ghost btn-sm";
    b.textContent = text; b.onclick = fn;
    return b;
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function cssEscape(s) { return String(s).replace(/["\\]/g, "\\$&"); }

  var toastTimer = null;
  function toast(text) {
    var t = $("toast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3500);
  }

  // ----------------------------------------------------------- импорт/экспорт

  function exportTeam() {
    var data = members.map(function (m) {
      return { name: m.name, role: m.role, charter: m.charter, tools: m.tools };
    });
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "swarm_team.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function importTeam(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var data = JSON.parse(reader.result);
        if (!Array.isArray(data)) throw new Error("ожидался список участников");
        members = data.map(cloneMember);
        renderMembers();
        toast("Команда загружена: " + members.length + " участников.");
      } catch (e) {
        toast("Не удалось прочитать файл: " + e.message);
      }
    };
    reader.readAsText(file);
  }

  async function browseWorkspace() {
    try {
      var res = await fetch("/api/dialog/select-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial_dir: $("workspace").value || "" }),
      });
      var data = await res.json();
      if (data.ok && data.path) $("workspace").value = data.path;
      else if (data.error) toast(data.error);
    } catch (_) {
      toast("Диалог выбора папки недоступен — впишите путь вручную.");
    }
  }

  // ----------------------------------------------------------- события DOM

  function bind() {
    $("run").onclick = run;
    $("stop").onclick = function () { send({ type: "stop" }); setStatus("running", "останавливаю…"); };
    $("add-member").onclick = function () {
      members.push(cloneMember({ name: "Участник " + (members.length + 1), role: "участник", charter: "", tools: [] }));
      renderMembers();
    };
    $("reset-team").onclick = function () {
      if (cfg) { members = (cfg.team || []).map(cloneMember); renderMembers(); toast("Команда сброшена к образцу."); }
    };
    $("export-team").onclick = exportTeam;
    $("import-team").onclick = function () { $("import-file").click(); };
    $("import-file").onchange = function (e) { if (e.target.files[0]) importTeam(e.target.files[0]); e.target.value = ""; };
    $("browse-ws").onclick = browseWorkspace;
  }

  bind();
  connect();
})();
