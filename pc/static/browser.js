"use strict";
/* Browser panel.
   In the Altair app (Tauri) tabs are native WebView2 webviews laid over #browser-view:
   the page renders natively — sharp, instant input, real scrolling and typing — and the
   agent drives the same tabs over DevTools (server: server/browser_ws.py). This module
   owns the tab strip, keeps the active tab placed over the panel and hidden whenever an
   app overlay (dialog, settings, palette) would otherwise be covered by it.
   Without the shell (UI opened in an ordinary browser) it falls back to a live
   screencast of a hidden Chrome, forwarding mouse, wheel and keys to it.
   Uses the globals of redesign.js: $, $$, esc, T, send, toast, confirmDialog, openPane,
   paneVisible, tauriInvoke, iconSvg. */
(function () {
  const B = {
    embedded: false, invoke: null, tabs: [], active: "", seq: 0,
    downloads: [], seenDownloads: new Set(), shelf: false,
    streaming: false, shot: null, fallbackTabs: [],
  };
  const viewEl = () => $("#browser-view");
  const urlEl = () => $("#br-url");

  // --- address bar rules (same as core/browser_session.normalize_target) -------------
  function looksLikeUrl(s) {
    s = (s || "").trim();
    if (!s) return false;
    if (/^(https?:\/\/|about:|file:)/i.test(s)) return true;
    if (/\s/.test(s)) return false;
    let host = s.split("/")[0];
    const m = /^(.*):(\d+)$/.exec(host); if (m && m[1]) host = m[1];
    if (host === "localhost") return true;
    return host.includes(".") && !host.startsWith(".") && !host.endsWith(".");
  }
  function normalizeTarget(s) {
    s = (s || "").trim().replace(/^"(.*)"$/, "$1");
    if (!s) return "";
    if (/^(https?:\/\/|about:|file:)/i.test(s)) return s;
    // A Windows path pasted from Explorer ("C:\\site\\index.html") opens as a local file.
    if (/^[a-z]:[\\/]/i.test(s)) return "file:///" + encodeURI(s.replace(/\\/g, "/")).replace(/#/g, "%23");
    if (looksLikeUrl(s)) return "https://" + s;
    return "https://www.google.com/search?q=" + encodeURIComponent(s);
  }

  // --- embedded host (Tauri) --------------------------------------------------------
  async function hostInit() {
    const invoke = tauriInvoke(); const events = window.__TAURI__?.event;
    if (!invoke || !events) return false;
    try { await invoke("browser_info"); } catch { return false; }  // an older shell without the browser
    B.invoke = invoke; B.embedded = true;
    await events.listen("altair-browser", (e) => onHostEvent(e.payload || {}));
    document.body.classList.add("br-native");
    return true;
  }
  const inv = (cmd, args) => B.invoke(cmd, args);
  const cur = () => B.tabs.find((t) => t.id === B.active);

  function overlayOpen() {
    return !!document.querySelector("#overlay-root .overlay, .onb-overlay");
  }
  function shouldShow() { return paneVisible("browser") && !overlayOpen(); }
  // Where the tab goes. With the panel closed its box is 0×0, and a tab opened (or kept)
  // at that size was a 1×1 page: the agent's screenshots came back as one pixel and sites
  // laid out for no width at all. A hidden tab keeps a real size: the panel's last one, or
  // a reasonable default until the panel has been shown.
  let lastShown = null;
  function bounds() {
    const r = viewEl().getBoundingClientRect();
    const b = { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
    if (b.w >= 200 && b.h >= 150) { lastShown = b; return b; }
    return lastShown || { x: 0, y: 0, w: Math.max(960, Math.round(innerWidth * 0.5)), h: Math.max(700, innerHeight - 80) };
  }
  let layoutQueued = false;
  function layout() {
    if (!B.embedded || layoutQueued) return;
    layoutQueued = true;
    requestAnimationFrame(() => {
      layoutQueued = false;
      const show = shouldShow(); const b = bounds();
      const where = `${b.x},${b.y},${b.w},${b.h}`;
      for (const t of B.tabs) {
        const visible = show && t.id === B.active;
        // Already there: no call. Each one runs on the window's main thread, and a stream of
        // them (every resize of the page, e.g. the input growing while typing) made keys like
        // the Alt+Shift layout switch get lost. A hidden tab is only resized (it keeps a real
        // page size for the agent), so it needs a call only when that size changes.
        const placed = visible ? "shown" : "hidden";
        if (t.placed === placed && t.where === where) continue;
        t.placed = placed;
        t.where = where;
        inv("browser_bounds", { tab: t.id, bounds: b, visible }).catch((e) => { t.placed = ""; t.where = ""; console.warn("browser_bounds", e); });
      }
      viewEl().classList.toggle("br-live", !!B.active);
    });
  }
  function sync() {
    if (!B.embedded) return;
    send({ type: "browser_host_tabs", active: B.active, tabs: B.tabs.map((t) => ({ id: t.id, url: t.url, title: t.title })) });
  }

  async function openTab(url, { activate = true, id = null } = {}) {
    id = id || ("u" + Date.now().toString(36) + (B.seq++));
    const tab = { id, url: url || "about:blank", title: "", loading: true, placed: "" };
    B.tabs.push(tab);
    await inv("browser_open", { tab: id, url: tab.url, bounds: bounds(), visible: false });
    tab.placed = "hidden";
    if (activate) selectTab(id); else { render(); sync(); }
    return id;
  }
  function selectTab(id) {
    if (!B.tabs.some((t) => t.id === id)) return;
    B.active = id;
    render(); sync(); layout();
    const t = cur(); if (t && !B.typing) urlEl().value = t.url === "about:blank" ? "" : t.url;
  }
  async function closeTab(id) {
    const i = B.tabs.findIndex((t) => t.id === id); if (i < 0) return;
    B.tabs.splice(i, 1);
    await inv("browser_close", { tab: id }).catch(() => {});
    if (B.active === id) B.active = (B.tabs[i] || B.tabs[i - 1] || {}).id || "";
    render(); sync(); layout();
    if (!B.active) urlEl().value = "";
  }

  function onHostEvent(p) {
    send({ type: "browser_host_event", ...p });  // target ids and downloads matter to the agent
    if (p.kind === "new_tab") { openTab(p.url); return; }
    const t = B.tabs.find((x) => x.id === p.tab); if (!t) return;
    if (p.kind === "title") t.title = p.title || "";
    else if (p.kind === "loading") { t.loading = true; if (p.url) t.url = p.url; }
    else if (p.kind === "loaded") { t.loading = false; if (p.url) t.url = p.url; }
    else return;
    if (t.id === B.active && !B.typing && t.url !== "about:blank") urlEl().value = t.url;
    render(); sync();
  }

  async function onHostCmd(m) {
    let ok = true, error = "";
    try {
      if (m.op === "open") {
        window.autoOpenBrowser?.();
        await openTab(m.url, { activate: m.activate !== false, id: m.tab });
      } else if (m.op === "select") selectTab(m.tab);
      else if (m.op === "close") await closeTab(m.tab);
    } catch (e) { ok = false; error = String(e?.message || e); }
    send({ type: "browser_host_ack", id: m.id, ok, error });
  }

  // --- tab strip ------------------------------------------------------------------
  function tabList() {
    return B.embedded ? B.tabs.map((t) => ({ id: t.id, title: t.title || (t.url === "about:blank" ? T("br.newTab") : t.url), active: t.id === B.active, loading: t.loading }))
      : B.fallbackTabs.filter((t) => !t.popup).map((t) => ({ id: t.id, title: t.title || t.url, active: t.active }));
  }
  // --- network: this site direct or through the VPN (core/browser_net.py) ---------------
  const NET_NEXT = { auto: "direct", direct: "vpn", vpn: "auto" };
  function currentHost() {
    const url = B.embedded ? (cur() && cur().url) : B.fallbackUrl;
    try { const h = new URL(url).hostname; return /^(localhost|127\.|\[::1\])/.test(h) ? "" : h; } catch { return ""; }
  }
  async function netRefresh(force) {
    const btn = $("#br-net"); if (!btn) return;
    const host = currentHost();
    if (!force && host === B.netHost && Date.now() - (B.netAt || 0) < 4000) return;
    B.netHost = host; B.netAt = Date.now();
    if (!host) { btn.hidden = true; return; }
    let d; try { d = await (await fetch(`/api/browser/net?host=${encodeURIComponent(host)}`)).json(); } catch { return; }
    if (!d.ok || !d.network || !d.network.vpn) { btn.hidden = true; return; }  // no VPN: nothing to choose
    const h = d.host || {};
    B.netRule = h.rule || "auto"; B.netSite = h.site || host;
    const last = h.last && h.last.route ? T(h.last.route === "vpn" ? "brnet.nowVpn" : h.last.route === "direct" ? "brnet.nowDirect" : "brnet.nowFailed") : "";
    btn.hidden = false;
    btn.dataset.mode = B.netRule;
    btn.textContent = T("brnet." + B.netRule);
    btn.dataset.tip = T("brnet.tip", { host, now: last || T("brnet.nowUnknown") });
  }
  async function netCycle() {
    const host = currentHost(); if (!host) return;
    const mode = NET_NEXT[B.netRule || "auto"];
    const site = B.netRule && B.netRule !== "auto" && B.netSite ? B.netSite : host;
    try {
      const r = await (await fetch("/api/browser/net", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ host: site, mode }) })).json();
      if (!r.ok) { toast(r.error || T("t.error"), "error"); return; }
      toast(T("brnet.set." + mode, { site: r.site }));
      history("reload");
      setTimeout(() => netRefresh(true), 1500);
    } catch { toast(T("t.error"), "error"); }
  }

  function render() {
    netRefresh(false);
    const strip = $("#br-tabs"); if (!strip) return;
    strip.hidden = false;
    strip.innerHTML = tabList().map((t) => `<div class="browser-tab${t.active ? " active" : ""}${t.loading ? " loading" : ""}" data-id="${esc(t.id)}" title="${esc(t.title)}"><span class="browser-tab-title">${esc(t.title)}</span><button class="browser-tab-x" data-close="${esc(t.id)}" aria-label="${esc(T("br.closeTab"))}">${iconSvg("x", "icon icon-sm")}</button></div>`).join("")
      + `<button class="browser-tab-new" id="br-newtab" data-tip="${esc(T("br.newTab"))}" aria-label="${esc(T("br.newTab"))}">${iconSvg("plus", "icon icon-sm")}</button>`;
    const empty = viewEl().querySelector(".browser-empty");
    if (empty) empty.hidden = B.embedded && !!B.active;
  }
  function tabAction(op, id) {
    if (B.embedded) {
      if (op === "select") selectTab(id); else if (op === "close") closeTab(id); else if (op === "new") { openTab("about:blank"); urlEl().focus(); }
    } else send({ type: "browser_tab_cmd", op, tab: id });
  }

  // --- navigation -------------------------------------------------------------------
  function navigate(text) {
    const url = normalizeTarget(text); if (!url) return;
    if (B.embedded) {
      if (!B.active) { openTab(url); return; }
      const t = cur(); t.url = url; t.loading = true; render();
      inv("browser_navigate", { tab: B.active, url }).catch((e) => toast(String(e), "error"));
    } else {
      send({ type: "browser_nav", url });
    }
  }
  function history(op) {
    if (B.embedded) { if (B.active) inv("browser_history", { tab: B.active, op }).catch(() => {}); }
    else send({ type: "browser_tab_cmd", op });
  }

  // --- downloads shelf --------------------------------------------------------------
  const STATUS = {
    downloading: "dl.st.downloading", checking: "dl.st.checking", clean: "dl.st.clean", risky: "dl.st.risky",
    suspicious: "dl.st.suspicious", threat: "dl.st.threat", unscanned: "dl.st.unscanned", failed: "dl.st.failed",
    moved: "dl.st.moved", deleted: "dl.st.deleted",
  };
  function sizeText(n) { if (!n) return ""; const u = ["B", "KB", "MB", "GB"]; let i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return `${n.toFixed(i ? 1 : 0)} ${u[i]}`; }
  function onDownloads(items) {
    B.downloads = (items || []).filter((d) => d.status !== "deleted");
    for (const d of B.downloads) {
      const key = d.id + d.status;
      if (B.seenDownloads.has(key)) continue;
      B.seenDownloads.add(key);
      if (d.status === "threat") toast(T("dl.toastThreat", { name: d.name }), "error");
      else if (["clean", "risky", "suspicious", "unscanned"].includes(d.status)) { toast(T("dl.toastReady", { name: d.name })); B.shelf = true; }
    }
    renderShelf();
  }
  function renderShelf() {
    const btn = $("#br-dl"), shelf = $("#br-dl-shelf"); if (!btn || !shelf) return;
    const waiting = B.downloads.filter((d) => ["clean", "risky", "suspicious", "unscanned", "checking", "downloading"].includes(d.status)).length;
    btn.hidden = !B.downloads.length;
    btn.dataset.badge = waiting ? String(waiting) : "";
    shelf.hidden = !B.shelf || !B.downloads.length;
    shelf.innerHTML = B.downloads.slice(0, 8).map((d) => {
      const actionable = ["clean", "risky", "suspicious", "unscanned"].includes(d.status);
      const tone = d.status === "clean" || d.status === "moved" ? "ok" : (["threat", "suspicious", "failed"].includes(d.status) ? "bad" : (d.status === "risky" || d.status === "unscanned" ? "warn" : ""));
      return `<div class="dl-item" data-id="${esc(d.id)}">
        <div class="dl-main"><div class="dl-name" title="${esc(d.name)}">${esc(d.name)}</div>
          <div class="dl-meta"><span class="dl-chip ${tone}" title="${esc(d.detail || "")}">${esc(T(STATUS[d.status] || d.status))}</span>${d.size ? `<span class="dim">${esc(sizeText(d.size))}</span>` : ""}</div></div>
        <div class="dl-acts">${actionable ? `<button class="btn btn-ghost btn-sm" data-dl="downloads">${esc(T("dl.toDownloads"))}</button><button class="btn btn-ghost btn-sm" data-dl="workspace">${esc(T("dl.toWorkspace"))}</button>` : ""}
          ${d.status !== "moved" ? `<button class="btn-icon small" data-dl="delete" data-tip="${esc(T("dl.delete"))}" aria-label="${esc(T("dl.delete"))}">${iconSvg("trash", "icon icon-sm")}</button>` : ""}</div>
      </div>`;
    }).join("");
    layout();
  }
  async function downloadAction(id, action) {
    const d = B.downloads.find((x) => x.id === id); if (!d) return;
    if (action === "delete") { send({ type: "browser_downloads", action: "delete", id }); return; }
    let confirmed = false;
    if (d.needs_confirmation) {
      confirmed = await confirmDialog({ title: T("dl.riskyTitle"), message: T("dl.riskyMsg", { name: d.name, status: T(STATUS[d.status] || d.status) }), confirmText: T("dl.moveAnyway"), danger: true });
      if (!confirmed) return;
    }
    send({ type: "browser_downloads", action: "move", id, destination: action, workspace: state.workspace || "", confirmed });
  }

  // --- page dialogs (alert/confirm/prompt) while the user browses ---------------------
  function promptDialog(message, value) {
    return new Promise((resolve) => {
      const overlay = el(`<div class="overlay confirm-overlay"><div class="confirm-box" role="dialog" aria-modal="true">
        <div class="confirm-title">${esc(T("br.dialogTitle"))}</div><div class="confirm-msg">${esc(message || "")}</div>
        <input class="field-input" style="width:100%;margin-top:10px" />
        <div class="confirm-actions"><button class="btn btn-outline" data-cancel>${esc(T("cf.cancel"))}</button><button class="btn btn-primary" data-ok>${esc(T("cf.ok"))}</button></div></div></div>`);
      const input = overlay.querySelector("input"); input.value = value || "";
      const finish = (v) => { overlay.remove(); layout(); resolve(v); };
      overlay.addEventListener("click", (e) => { if (e.target === overlay || e.target.closest("[data-cancel]")) finish(""); else if (e.target.closest("[data-ok]")) finish(input.value); });
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(input.value); else if (e.key === "Escape") finish(""); });
      els.overlayRoot.appendChild(overlay); setTimeout(() => input.focus(), 30);
    });
  }
  async function onDialog(m) {
    let answer = "";
    if (m.dialog === "prompt") answer = await promptDialog(m.message, m.default);
    else {
      const ok = await confirmDialog({ title: T("br.dialogTitle"), message: m.message || "", confirmText: m.dialog === "beforeunload" ? T("br.leave") : T("cf.ok") });
      answer = ok ? "ok" : "";
    }
    send({ type: "browser_dialog_reply", id: m.id, answer });
  }

  // --- fallback: screencast of a hidden Chrome ---------------------------------------
  function viewSize() { const v = viewEl(); const w = Math.round(v.clientWidth), h = Math.round(v.clientHeight); return w > 0 && h > 0 ? { w, h } : {}; }
  function startStream() { send({ type: "browser_stream", on: true, ...viewSize() }); }
  function stopStream() { B.streaming = false; send({ type: "browser_stream", on: false }); }
  function renderFrame(m) {
    const v = viewEl(); if (!v || !m.data) return;
    B.streaming = true; B.shot = { w: m.width || 1280, h: m.height || 860 };
    let img = v.querySelector("#br-shot");
    if (!img) { v.innerHTML = `<img id="br-shot" alt="" tabindex="0" />`; img = v.querySelector("#br-shot"); }
    img.src = "data:image/jpeg;base64," + m.data;
  }
  function renderState(m) {
    if (m.error) { if (!B.streaming && !B.embedded) viewEl().innerHTML = `<div class="browser-empty dim">${esc(m.error)}</div>`; else if (m.error) console.warn("browser:", m.error); return; }
    if (B.embedded) return;
    B.fallbackTabs = m.tabs || [];
    if (m.url) B.fallbackUrl = m.url;
    if (m.url && m.url !== "about:blank" && !B.typing) urlEl().value = m.url;
    render();
  }
  function pointer(e) {
    const img = e.target.closest?.("#br-shot"); if (!img || !B.shot) return null;
    const r = img.getBoundingClientRect();
    return { x: Math.round((e.clientX - r.left) / r.width * B.shot.w), y: Math.round((e.clientY - r.top) / r.height * B.shot.h) };
  }
  function keyName(e) {
    const map = { " ": "Space" };
    const mods = [e.ctrlKey && "Control", e.altKey && "Alt", e.shiftKey && e.key.length > 1 && "Shift", e.metaKey && "Meta"].filter(Boolean);
    return [...mods, map[e.key] || e.key].join("+");
  }
  function bindFallbackInput() {
    const v = viewEl();
    v.addEventListener("click", (e) => { const p = pointer(e); if (p) { e.target.focus(); send({ type: "browser_input", kind: "click", ...p, count: e.detail > 1 ? 2 : 1 }); } });
    let lastMove = 0;
    v.addEventListener("mousemove", (e) => { const now = Date.now(); if (now - lastMove < 80) return; lastMove = now; const p = pointer(e); if (p) send({ type: "browser_input", kind: "move", ...p }); });
    v.addEventListener("wheel", (e) => { const p = pointer(e); if (!p) return; e.preventDefault(); send({ type: "browser_input", kind: "wheel", ...p, dx: e.deltaX, dy: e.deltaY }); }, { passive: false });
    v.addEventListener("keydown", (e) => {
      if (!e.target.closest?.("#br-shot") || B.embedded) return;
      e.preventDefault();
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) send({ type: "browser_input", kind: "text", text: e.key });
      else if (!["Control", "Shift", "Alt", "Meta"].includes(e.key)) send({ type: "browser_input", kind: "key", key: keyName(e) });
    });
  }

  // --- wiring -------------------------------------------------------------------------
  function onPaneOpen() { if (B.embedded) { layout(); if (!B.tabs.length) urlEl()?.focus(); } else startStream(); }
  function onPaneClose() { if (B.embedded) layout(); else stopStream(); }
  function onReady() {
    if (!B.embedded) return;
    send({ type: "browser_host_hello" });
    sync();
  }
  function handle(m) {
    switch (m.type) {
      case "browser_host_cmd": onHostCmd(m); break;
      case "browser_dialog": onDialog(m); break;
      case "browser_downloads": onDownloads(m.items); break;
      case "browser_agent_active": window.autoOpenBrowser?.(); break;
      case "browser_frame": if (!B.embedded) renderFrame(m); break;
      case "browser_state": renderState(m); break;
    }
  }

  async function init() {
    const url = urlEl(); if (!url) return;
    // The address follows the page unless the user is typing a new one right now.
    url.addEventListener("input", () => { B.typing = true; });
    url.addEventListener("blur", () => { B.typing = false; });
    url.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); B.typing = false; navigate(url.value); url.blur(); }
      else if (e.key === "Escape") { B.typing = false; const t = cur(); url.value = t && t.url !== "about:blank" ? t.url : ""; url.blur(); }
    });
    url.addEventListener("focus", () => url.select());
    $("#br-back")?.addEventListener("click", () => history("back"));
    $("#br-forward")?.addEventListener("click", () => history("forward"));
    $("#br-reload")?.addEventListener("click", () => history("reload"));
    $("#br-ext")?.addEventListener("click", () => { const u = normalizeTarget(url.value); if (u) window.open(u, "_blank"); });
    $("#br-dl")?.addEventListener("click", () => { B.shelf = !B.shelf; renderShelf(); });
    $("#br-net")?.addEventListener("click", netCycle);
    $("#br-dl-shelf")?.addEventListener("click", (e) => { const b = e.target.closest("[data-dl]"); const item = e.target.closest(".dl-item"); if (b && item) downloadAction(item.dataset.id, b.dataset.dl); });
    $("#br-tabs")?.addEventListener("click", (e) => {
      const x = e.target.closest("[data-close]"); if (x) { e.stopPropagation(); tabAction("close", x.dataset.close); return; }
      if (e.target.closest("#br-newtab")) { tabAction("new"); return; }
      const t = e.target.closest(".browser-tab"); if (t) tabAction("select", t.dataset.id);
    });
    $("#br-tabs")?.addEventListener("auxclick", (e) => { const t = e.target.closest(".browser-tab"); if (t && e.button === 1) tabAction("close", t.dataset.id); });

    const embedded = await hostInit();
    if (embedded) {
      // Keep the native tab glued to the panel and out of the way of app overlays.
      new ResizeObserver(layout).observe(viewEl());
      window.addEventListener("resize", layout);
      new MutationObserver(layout).observe($("#overlay-root"), { childList: true });
      new MutationObserver(layout).observe(document.body, { childList: true });
      $("#pane-browser .pane-act[data-act='pop']")?.setAttribute("hidden", "");
      if (window.state?.ws?.readyState === 1) onReady();
    } else {
      bindFallbackInput();
      let rt = null, last = "";
      new ResizeObserver(() => {
        if (!paneVisible("browser")) return;
        const s = viewSize(); const key = `${s.w}x${s.h}`;
        if (!s.w || key === last) return; last = key;
        clearTimeout(rt); rt = setTimeout(startStream, 150);
      }).observe(viewEl());
    }
    render();
  }

  window.BrowserPanel = { init, handle, onReady, onPaneOpen, onPaneClose, relayout: layout, isEmbedded: () => B.embedded };
  document.addEventListener("DOMContentLoaded", () => { init(); });
})();
