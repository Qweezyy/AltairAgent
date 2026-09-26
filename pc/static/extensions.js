// Settings → Extensions: skills (added from files) and MCP servers (added, switched, reconnected
// live). Uses the helpers redesign.js defines at top level ($, $$, T, esc, escAttr, toast,
// iconSvg, confirmDialog, state).
(function () {
  "use strict";

  const api = async (url, opts = {}) => {
    const init = { ...opts };
    if (opts.json !== undefined) {
      init.method = init.method || "POST";
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.json);
      delete init.json;
    }
    const res = await fetch(url, init);
    let data = {};
    try { data = await res.json(); } catch { data = {}; }
    if (!res.ok && !data.error) data.error = data.detail || `HTTP ${res.status}`;
    if (!res.ok) data.ok = false;
    return data;
  };

  // ------------------------------------------------------------------ skills

  async function renderSkills(main) {
    main.innerHTML = `<div class="settings-section ext-section"><h2>${esc(T("sk.title"))}</h2>
      <p class="sr-desc" style="margin-bottom:14px">${esc(T("sk.desc"))}</p>
      <div class="ext-actions">
        <button class="btn btn-primary" id="sk-file">${iconSvg("plus", "icon icon-sm")} ${esc(T("sk.addFile"))}</button>
        <button class="btn btn-outline" id="sk-folder">${iconSvg("folder", "icon icon-sm")} ${esc(T("sk.addFolder"))}</button>
        <label class="ext-scope"><input type="checkbox" id="sk-project"/> ${esc(T("sk.projectOnly"))}</label>
        <input type="file" id="sk-input" accept=".md,.zip" multiple hidden />
      </div>
      <div class="ext-drop dim" id="sk-drop">${esc(T("sk.drop"))}</div>
      <div id="sk-list" class="dim">${esc(T("mem.loading"))}</div></div>`;

    const scope = () => ($("#sk-project", main).checked ? "project" : "global");
    const list = $("#sk-list", main);

    const refresh = async () => {
      const d = await api("/api/skills");
      state.skills = d.skills || [];  // the composer's "+" menu shows the same list
      list.classList.remove("dim");
      if (!state.skills.length) {
        list.innerHTML = `<div class="empty">${iconSvg("spark", "icon")}<div>${esc(T("sk.empty"))}</div></div>`;
        return;
      }
      list.innerHTML = state.skills.map((s) => `<div class="list-row">
          <div class="grow"><div class="lr-title">${esc(s.name)} <span class="chip">${esc(T(s.scope === "project" ? "sk.project" : "sk.global"))}</span>${s.files ? ` <span class="chip">${esc(T("sk.files", { n: s.files }))}</span>` : ""}</div>
          <div class="lr-sub">${esc(s.description || "")}</div>
          <div class="lr-sub mono" style="overflow-wrap:anywhere">${esc(s.path || "")}</div></div>
          <button class="btn-icon small" data-del="${escAttr(s.name)}" data-scope="${escAttr(s.scope)}" data-tip="${escAttr(T("side.delete"))}">${iconSvg("trash", "icon icon-sm")}</button>
        </div>`).join("");
      $$("[data-del]", list).forEach((b) => b.addEventListener("click", async () => {
        if (!(await confirmDialog({ message: T("sk.deleteConfirm", { name: b.dataset.del }), danger: true }))) return;
        const r = await api(`/api/skills/${encodeURIComponent(b.dataset.del)}?scope=${b.dataset.scope}`, { method: "DELETE" });
        if (!r.ok) toast(r.error || T("t.error"), "error");
        refresh();
      }));
    };

    // Skills may carry scripts the agent will run: say so once before installing.
    const done = (d) => { toast(T("sk.added", { names: d.installed.join(", ") })); refresh(); };
    const retryIfExists = async (d, again) => {
      if (d.ok) return done(d);
      if (d.exists && (await confirmDialog({ message: T("sk.replace", { names: d.exists }), confirmText: T("sk.replaceBtn") }))) {
        const r = await again();
        if (r.ok) return done(r);
        d = r;
      }
      if (!d.exists) toast(d.error || T("t.error"), "error");
    };
    const importPaths = async (paths) => {
      const send = (overwrite) => api("/api/skills/import", { json: { paths, scope: scope(), overwrite } });
      retryIfExists(await send(false), () => send(true));
    };
    const uploadFiles = async (files) => {
      if (!files.length) return;
      const send = (overwrite) => {
        const form = new FormData();
        [...files].forEach((f) => form.append("files", f, f.name));
        return api(`/api/skills/upload?scope=${scope()}&overwrite=${overwrite}`, { method: "POST", body: form });
      };
      retryIfExists(await send(false), () => send(true));
    };

    $("#sk-file", main).addEventListener("click", async () => {
      const d = await api("/api/dialog/select-files", { json: { initial_dir: "", kind: "any" } });
      if (d.ok && d.paths?.length) importPaths(d.paths);
      else if (!d.cancelled) $("#sk-input", main).click();  // no system dialog: the browser one
    });
    $("#sk-folder", main).addEventListener("click", async () => {
      const d = await api("/api/dialog/select-folder", { json: { initial_dir: "" } });
      if (d.ok && d.path) importPaths([d.path]);
      else if (!d.cancelled && d.error) toast(d.error, "error");
    });
    $("#sk-input", main).addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
    const drop = $("#sk-drop", main);
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
    drop.addEventListener("drop", (e) => { e.preventDefault(); uploadFiles(e.dataTransfer.files); });
    refresh();
  }

  // ------------------------------------------------------------------ MCP servers

  const STATE_CLASS = { connected: "running", error: "error", disabled: "", stopped: "" };

  function serverRow(s) {
    const where = s.kind === "remote" ? s.url : [s.command, ...(s.args || [])].join(" ");
    const tools = s.tools?.length ? T("mcp.tools", { n: s.tools.length }) : "";
    const toolList = s.tools?.length ? `<details class="mcp-tools"><summary>${esc(tools)}</summary><div class="mono">${s.tools.map(esc).join(", ")}</div></details>` : "";
    return `<div class="list-row mcp-row" data-name="${escAttr(s.name)}">
      <span class="status-dot ${STATE_CLASS[s.state] || ""}"></span>
      <div class="grow">
        <div class="lr-title">${esc(s.name)} <span class="chip">${esc(T(s.kind === "remote" ? "mcp.remote" : "mcp.local"))}</span> <span class="lr-sub">${esc(T("mcp.st." + s.state))}</span></div>
        <div class="lr-sub mono" style="overflow-wrap:anywhere">${esc(where)}</div>
        ${s.error ? `<div class="lr-sub mcp-err">${esc(s.error)}</div>` : ""}
        ${toolList}
      </div>
      <label class="ext-switch" data-tip="${escAttr(T("mcp.enabled"))}"><input type="checkbox" data-toggle ${s.state !== "disabled" ? "checked" : ""}/></label>
      <button class="btn-icon small" data-restart data-tip="${escAttr(T("mcp.reconnect"))}">${iconSvg("refresh", "icon icon-sm")}</button>
      <button class="btn-icon small" data-remove data-tip="${escAttr(T("side.delete"))}">${iconSvg("trash", "icon icon-sm")}</button>
    </div>`;
  }

  async function renderMcp(main) {
    main.innerHTML = `<div class="settings-section ext-section"><h2>${esc(T("mcp.title"))}</h2>
      <p class="sr-desc" style="margin-bottom:14px">${esc(T("mcp.desc"))}</p>
      <div id="mcp-list" class="dim">${esc(T("mem.loading"))}</div>
      <div class="divider" style="margin:16px 0"></div>
      <div class="ext-tabs" role="tablist">
        <button class="btn btn-outline small active" data-form="remote">${esc(T("mcp.addRemote"))}</button>
        <button class="btn btn-outline small" data-form="local">${esc(T("mcp.addLocal"))}</button>
        <button class="btn btn-outline small" data-form="json">${esc(T("mcp.addJson"))}</button>
      </div>
      <div class="form-row" data-pane="remote">
        <input class="field" id="mcp-r-name" placeholder="${escAttr(T("mcp.namePh"))}" />
        <input class="field" id="mcp-r-url" placeholder="https://example.com/mcp" />
        <input class="field" id="mcp-r-token" type="password" placeholder="${escAttr(T("mcp.tokenPh"))}" />
        <label class="ext-scope"><input type="checkbox" id="mcp-r-sse"/> ${esc(T("mcp.legacySse"))}</label>
      </div>
      <div class="form-row" data-pane="local" hidden>
        <input class="field" id="mcp-l-name" placeholder="${escAttr(T("mcp.namePh"))}" />
        <input class="field mono" id="mcp-l-cmd" placeholder="npx -y @modelcontextprotocol/server-memory" />
        <textarea class="field mono" id="mcp-l-env" rows="2" placeholder="${escAttr(T("mcp.envPh"))}"></textarea>
      </div>
      <div class="form-row" data-pane="json" hidden>
        <textarea class="field mono" id="mcp-json" rows="7" placeholder='{"mcpServers": {"name": {"command": "npx", "args": ["-y", "…"]}}}'></textarea>
        <span class="form-help">${esc(T("mcp.jsonHelp"))}</span>
      </div>
      <button class="btn btn-primary" id="mcp-add">${esc(T("mcp.connect"))}</button>
      <p class="form-help" style="margin-top:10px">${esc(T("mcp.configAt"))} <span class="mono" id="mcp-path"></span></p>
    </div>`;

    const list = $("#mcp-list", main);
    let pane = "remote";
    $$("[data-form]", main).forEach((b) => b.addEventListener("click", () => {
      pane = b.dataset.form;
      $$("[data-form]", main).forEach((x) => x.classList.toggle("active", x === b));
      $$("[data-pane]", main).forEach((p) => { p.hidden = p.dataset.pane !== pane; });
    }));

    const refresh = async () => {
      const d = await api("/api/mcp");
      $("#mcp-path", main).textContent = d.config_path || "";
      list.classList.remove("dim");
      const servers = d.servers || [];
      list.innerHTML = (d.config_error ? `<div class="lr-sub mcp-err">${esc(d.config_error)}</div>` : "") +
        (servers.length ? servers.map(serverRow).join("") : `<div class="empty">${iconSvg("wrench", "icon")}<div>${esc(T("mcp.empty"))}</div></div>`);
      $$(".mcp-row", list).forEach((row) => {
        const name = row.dataset.name;
        const busy = (on) => row.classList.toggle("busy", on);
        $("[data-toggle]", row).addEventListener("change", async (e) => {
          busy(true);
          const r = await api(`/api/mcp/servers/${encodeURIComponent(name)}/toggle`, { json: { enabled: e.target.checked } });
          if (!r.ok) toast(r.error || T("t.error"), "error");
          refresh(); reloadTools();
        });
        $("[data-restart]", row).addEventListener("click", async () => {
          busy(true);
          const r = await api(`/api/mcp/servers/${encodeURIComponent(name)}/restart`, { method: "POST" });
          if (r.server?.state === "error") toast(r.server.error, "error");
          refresh(); reloadTools();
        });
        $("[data-remove]", row).addEventListener("click", async () => {
          if (!(await confirmDialog({ message: T("mcp.removeConfirm", { name }), danger: true }))) return;
          const r = await api(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
          if (!r.ok) toast(r.error || T("t.error"), "error");
          refresh(); reloadTools();
        });
      });
    };

    const parseEnv = (text) => Object.fromEntries(text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && l.includes("="))
      .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]));
    // "npx -y pkg --opt 'a b'" → command + args, honouring quotes.
    const splitCmd = (text) => (text.match(/"[^"]*"|'[^']*'|\S+/g) || []).map((p) => p.replace(/^(["'])(.*)\1$/, "$2"));

    $("#mcp-add", main).addEventListener("click", async () => {
      let body;
      if (pane === "json") {
        const json = $("#mcp-json", main).value.trim();
        if (!json) return toast(T("mcp.needJson"), "error");
        body = { json };
      } else if (pane === "remote") {
        const name = $("#mcp-r-name", main).value.trim(); const url = $("#mcp-r-url", main).value.trim();
        if (!name || !url) return toast(T("mcp.needNameUrl"), "error");
        const config = { url, transport: $("#mcp-r-sse", main).checked ? "sse" : "http" };
        const token = $("#mcp-r-token", main).value.trim();
        if (token) config.token = token;
        body = { name, config };
      } else {
        const name = $("#mcp-l-name", main).value.trim(); const parts = splitCmd($("#mcp-l-cmd", main).value.trim());
        if (!name || !parts.length) return toast(T("mcp.needNameCmd"), "error");
        body = { name, config: { command: parts[0], args: parts.slice(1), env: parseEnv($("#mcp-l-env", main).value) } };
      }
      const btn = $("#mcp-add", main);
      btn.disabled = true; btn.textContent = T("mcp.connecting");
      try {
        const r = await api("/api/mcp/servers", { json: body });
        if (!r.ok) { toast(r.error || T("t.error"), "error"); return; }
        const failed = (r.servers || []).filter((s) => s.state === "error");
        if (failed.length) toast(T("mcp.addedWithError", { name: failed[0].name, error: failed[0].error }), "error");
        else toast(T("mcp.added", { n: (r.servers || []).reduce((n, s) => n + (s.tools?.length || 0), 0) }));
        $$("input.field, textarea.field", main).forEach((f) => { f.value = ""; });
        refresh(); reloadTools();
      } finally { btn.disabled = false; btn.textContent = T("mcp.connect"); }
    });
    refresh();
  }

  // The "About" counter and tool pickers read state.tools: keep it in step with MCP changes.
  async function reloadTools() {
    try {
      const d = await api("/api/tools");
      if (Array.isArray(d.tools)) state.tools = d.tools;
    } catch { /* the counter just stays stale until the next reload */ }
  }

  window.ExtensionsSettings = { renderSkills, renderMcp };
})();
