"use strict";
/* Altair — мастер первого запуска. Показывается один раз (флаг agent_onboarded).
   Шаги: приветствие (маскот) → язык → провайдер+ключ → рабочая папка → готово.
   По умолчанию всё на English. Сохраняет через POST /api/settings и WS set_workspace.
   Чистый JS, зависит только от window.I18N, window.Mascot и глобального send/toast. */
(function () {
  var LS_DONE = "agent_onboarded";
  var t = function (k, v) { return window.I18N ? window.I18N.t(k, v) : k; };

  // Провайдеры (OpenAI-совместимые — работают уже сейчас). Нативный Anthropic —
  // отдельный слой, появится позже; тогда сюда добавится пресет «Anthropic».
  var PRESETS = [
    { id: "openrouter", name: "OpenRouter", base: "https://openrouter.ai/api/v1", ex: "anthropic/claude-sonnet-4.5", keys: "https://openrouter.ai/keys", hint: "Anthropic, OpenAI, Google, Meta — one key" },
    { id: "anthropic", name: "Anthropic", base: "https://api.anthropic.com/v1", ex: "claude-sonnet-4-5", keys: "https://console.anthropic.com/settings/keys", hint: "Claude, native API" },
    { id: "openai", name: "OpenAI", base: "https://api.openai.com/v1", ex: "gpt-4o", keys: "https://platform.openai.com/api-keys", hint: "GPT-4o / o-series" },
    { id: "groq", name: "Groq", base: "https://api.groq.com/openai/v1", ex: "llama-3.3-70b-versatile", keys: "https://console.groq.com/keys", hint: "Fast open models" },
    { id: "together", name: "Together", base: "https://api.together.xyz/v1", ex: "meta-llama/Llama-3.3-70B-Instruct-Turbo", keys: "https://api.together.ai/settings/api-keys", hint: "Open-source models" },
    { id: "ollama", name: "Ollama", base: "http://localhost:11434/v1", ex: "llama3.1", keys: "", noKey: true, hint: "Local, offline" },
    { id: "custom", name: "Custom", base: "", ex: "", keys: "", hint: "Any OpenAI-compatible endpoint" },
  ];

  var STEPS = ["welcome", "lang", "provider", "workspace", "done"];
  var st = { i: 0, provider: "openrouter", apiKey: "", model: "", baseUrl: "", workspace: "", root: null };

  function toast(msg, kind) { if (window.toast) window.toast(msg, kind); }

  function preset(id) { return PRESETS.filter(function (p) { return p.id === id; })[0] || PRESETS[0]; }

  // --- отправка рабочей папки по WS (может быть ещё не подключён) ---
  function setWorkspace(ws) {
    if (!ws) return;
    var tries = 0;
    (function attempt() {
      if (window.send && window.state && window.state.ws && window.state.ws.readyState === 1) {
        window.send({ type: "set_workspace", workspace: ws });
      } else if (tries++ < 20) {
        setTimeout(attempt, 250);
      }
    })();
  }

  async function saveSettings() {
    var p = preset(st.provider);
    var body = {
      llm_base_url: (st.baseUrl || p.base || "").trim(),
      default_model: (st.model || p.ex || "").trim(),
      agent_language: window.I18N && window.I18N.lang() === "ru" ? "русский" : "English",
      approval_mode: "manual",
    };
    var key = (st.apiKey || "").trim();
    if (key) body.llm_api_key = key;
    try {
      var res = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      var data = await res.json();
      if (!data.ok) { toast(t("onb.saveError"), "error"); return false; }
      if (st.workspace) setWorkspace(st.workspace);
      return true;
    } catch (e) {
      toast(t("onb.saveError"), "error");
      return false;
    }
  }

  // ------------------------------------------------------------- рендер
  function el(html) { var tpl = document.createElement("template"); tpl.innerHTML = html.trim(); return tpl.content.firstElementChild; }

  function mascot(mood, size) { return window.Mascot ? window.Mascot.svg({ mood: mood || "idle", size: size || 128, satellites: true }) : ""; }

  function stepWelcome() {
    return `<div class="onb-hero">${mascot("idle", 140)}</div>
      <h2 class="onb-title">${t("onb.welcome.title")}</h2>
      <p class="onb-body">${t("onb.welcome.body")}</p>`;
  }

  function stepLang() {
    var cur = window.I18N ? window.I18N.lang() : "en";
    var card = function (id, label, sub) {
      return `<button class="onb-card${cur === id ? " sel" : ""}" data-lang="${id}">
        <span class="onb-card-main">${label}</span><span class="onb-card-sub">${sub}</span>
        <span class="onb-check">✓</span></button>`;
    };
    return `<h2 class="onb-title">${t("onb.lang.title")}</h2>
      <p class="onb-body">${t("onb.lang.body")}</p>
      <div class="onb-cards">
        ${card("en", "English", "Default")}
        ${card("ru", "Русский", "Russian")}
      </div>`;
  }

  function stepProvider() {
    var p = preset(st.provider);
    var opts = PRESETS.map(function (x) { return `<button class="onb-prov${x.id === st.provider ? " sel" : ""}" data-prov="${x.id}"><b>${x.name}</b><small>${x.hint}</small></button>`; }).join("");
    var needKey = !p.noKey;
    var keysLink = p.keys ? ` <a href="${p.keys}" target="_blank" rel="noopener" class="onb-link">${p.keys.replace(/^https?:\/\//, "")}</a>` : "";
    return `<h2 class="onb-title">${t("onb.provider.title")}</h2>
      <p class="onb-body">${t("onb.provider.body")}</p>
      <div class="onb-provgrid">${opts}</div>
      <div class="onb-fields">
        ${needKey ? `<label class="onb-field"><span>${t("onb.provider.key")}${keysLink}</span>
          <input type="password" id="onb-key" placeholder="${t("onb.provider.keyPh")}" value="${st.apiKey ? String(st.apiKey).replace(/"/g, "&quot;") : ""}" autocomplete="off" spellcheck="false"></label>` : ""}
        <label class="onb-field"><span>${t("onb.provider.model")}</span>
          <input type="text" id="onb-model" placeholder="${t("onb.provider.modelPh", { example: p.ex || "model-name" })}" value="${st.model ? String(st.model).replace(/"/g, "&quot;") : ""}" spellcheck="false"></label>
        <details class="onb-adv"${st.baseUrl ? " open" : ""}><summary>${t("onb.provider.advanced")}</summary>
          <label class="onb-field"><span>${t("onb.provider.baseUrl")}</span>
            <input type="text" id="onb-base" placeholder="${p.base || "https://…/v1"}" value="${st.baseUrl ? String(st.baseUrl).replace(/"/g, "&quot;") : ""}" spellcheck="false"></label>
        </details>
      </div>
      <p class="onb-note">${t("onb.provider.skipNote")}</p>`;
  }

  function stepWorkspace() {
    var chosen = st.workspace ? `<div class="onb-ws-path">${st.workspace}</div>` : `<div class="onb-ws-path dim">${t("onb.workspace.default")}</div>`;
    return `<h2 class="onb-title">${t("onb.workspace.title")}</h2>
      <p class="onb-body">${t("onb.workspace.body")}</p>
      <button class="onb-btn onb-btn-outline" id="onb-choose-ws">${t("onb.workspace.choose")}</button>
      ${chosen}`;
  }

  function stepDone() {
    return `<div class="onb-hero">${mascot("happy", 140)}</div>
      <h2 class="onb-title">${t("onb.done.title")}</h2>
      <p class="onb-body">${t("onb.done.body")}</p>`;
  }

  var RENDER = { welcome: stepWelcome, lang: stepLang, provider: stepProvider, workspace: stepWorkspace, done: stepDone };

  function ctaLabel() {
    var s = STEPS[st.i];
    if (s === "welcome") return t("onb.welcome.cta");
    if (s === "done") return t("onb.done.cta");
    return t("onb.next");
  }

  function draw(root) {
    var s = STEPS[st.i];
    var dots = STEPS.map(function (_, idx) { return `<span class="onb-dot${idx === st.i ? " on" : idx < st.i ? " done" : ""}"></span>`; }).join("");
    root.innerHTML = `<div class="onb-card-wrap">
      <div class="onb-panel">
        <button class="onb-skip" id="onb-skip">${t("onb.skip")}</button>
        <div class="onb-content" id="onb-content">${RENDER[s]()}</div>
      </div>
      <div class="onb-foot">
        <button class="onb-back" id="onb-back"${st.i === 0 ? " hidden" : ""}>${t("onb.back")}</button>
        <div class="onb-dots">${dots}</div>
        <button class="onb-next onb-btn-primary" id="onb-next">${ctaLabel()}</button>
      </div>
    </div>`;
    wire(root);
  }

  function wire(root) {
    var content = root.querySelector("#onb-content");
    root.querySelector("#onb-skip").onclick = function () { finish(root, true); };
    root.querySelector("#onb-back").onclick = function () { if (st.i > 0) { st.i--; draw(root); } };
    root.querySelector("#onb-next").onclick = function () { next(root); };

    content.querySelectorAll("[data-lang]").forEach(function (b) {
      b.onclick = function () { if (window.I18N) window.I18N.setLang(b.getAttribute("data-lang")); draw(root); };
    });
    content.querySelectorAll("[data-prov]").forEach(function (b) {
      b.onclick = function () {
        st.provider = b.getAttribute("data-prov");
        var p = preset(st.provider);
        if (!st.baseUrl) st.baseUrl = "";
        st.model = st.model || "";
        draw(root);
      };
    });
    var key = content.querySelector("#onb-key"); if (key) key.oninput = function () { st.apiKey = key.value; };
    var model = content.querySelector("#onb-model"); if (model) model.oninput = function () { st.model = model.value; };
    var base = content.querySelector("#onb-base"); if (base) base.oninput = function () { st.baseUrl = base.value; };
    var chooseWs = content.querySelector("#onb-choose-ws");
    if (chooseWs) chooseWs.onclick = async function () {
      try {
        var res = await fetch("/api/dialog/select-folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initial_dir: st.workspace || "" }) });
        var data = await res.json();
        if (data.ok && data.path) { st.workspace = data.path; draw(root); }
      } catch (e) {}
    };
  }

  async function next(root) {
    if (STEPS[st.i] === "done") { finish(root, false); return; }
    st.i++;
    draw(root);
  }

  async function finish(root, skipped) {
    if (!skipped) { await saveSettings(); toast(t("onb.saved")); }
    try { localStorage.setItem(LS_DONE, "1"); } catch (e) {}
    root.classList.remove("show");
    setTimeout(function () { root.remove(); }, 260);
  }

  function open() {
    var root = document.createElement("div");
    root.className = "onb-overlay";
    document.body.appendChild(root);
    st.i = 0;
    draw(root);
    requestAnimationFrame(function () { root.classList.add("show"); });
  }

  function maybe() {
    var done = false;
    try { done = localStorage.getItem(LS_DONE) === "1"; } catch (e) {}
    if (!done) open();
  }

  window.Onboarding = { maybe: maybe, open: open };
})();
