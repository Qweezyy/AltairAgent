// The body this window shows: this PC, or one of the servers Altair was installed on.
//
// A server's agent is reached through the PC's backend: /b/<server>/… is its own API, carried
// over the SSH tunnel (server/bodies.py). So the very same panels — chats, terminal, files,
// settings — show a server: every /api/… and /ws… the page asks for goes to /b/<server>/… while a
// server is chosen. The choice lives in the address (?body=<id>), so a reload keeps it and a
// switch is a clean start of the page.
(function () {
  const params = new URLSearchParams(location.search);
  const body = params.get("body") || "";
  // What belongs to this PC whatever body is shown: the bodies themselves and the servers' setup.
  const LOCAL = [/^\/api\/bodies(\/|$)/, /^\/api\/servers(\/|$)/, /^\/api\/body\/status$/];
  const rawFetch = window.fetch.bind(window);
  const RawSocket = window.WebSocket;

  function routed(path) {
    if (!body || !(path.startsWith("/api/") || path === "/ws" || path.startsWith("/ws/"))) return null;
    if (LOCAL.some((re) => re.test(path))) return null;
    return `/b/${encodeURIComponent(body)}${path}`;
  }
  function route(url) {
    let u;
    try { u = new URL(url, location.href); } catch { return url; }
    if (u.host !== location.host) return url;
    const path = routed(u.pathname);
    if (!path) return url;
    u.pathname = path;
    return u.toString();
  }

  window.fetch = function (input, init) {
    if (typeof input === "string" || input instanceof URL) return rawFetch(route(String(input)), init);
    if (input instanceof Request && body) {
      const target = route(input.url);
      if (target !== input.url) return rawFetch(new Request(target, input), init);
    }
    return rawFetch(input, init);
  };
  window.WebSocket = class extends RawSocket {
    constructor(url, protocols) { super(route(String(url)), protocols); }
  };

  // Pictures, files and frames of a server's chat point at /api/…: point them at the server too.
  function fixNode(node) {
    if (!body || node.nodeType !== 1) return;
    for (const attr of ["src", "href", "poster"]) {
      const v = node.getAttribute && node.getAttribute(attr);
      if (v && v.startsWith("/api/")) { const r = routed(v.split("?")[0]); if (r) node.setAttribute(attr, r + v.slice(v.split("?")[0].length)); }
    }
    node.querySelectorAll && node.querySelectorAll('[src^="/api/"],[href^="/api/"],[poster^="/api/"]').forEach(fixNode);
  }
  if (body) {
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "attributes") fixNode(r.target);
        else r.addedNodes.forEach(fixNode);
      }
    }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["src", "href", "poster"] });
  }

  const STATE_ORDER = { online: 0, connecting: 1, offline: 2 };
  const Body = {
    id: body,
    chat: params.get("chat") || "",
    list: [],
    route,
    rawFetch,
    // Opens a body (and a chat of it): the page starts over showing that body.
    open(id, chat) {
      const p = new URLSearchParams(location.search);
      p.delete("body"); p.delete("chat");
      if (id && !this.isSelf(id)) p.set("body", id);
      if (chat) p.set("chat", chat);
      const q = p.toString();
      location.href = location.pathname + (q ? `?${q}` : "");
    },
    isSelf(id) { const me = this.list.find((b) => b.self); return !id || (me && me.id === id); },
    current() { return this.list.find((b) => (body ? b.id === body : b.self)) || null; },
    others() { return this.list.filter((b) => (body ? b.id !== body : !b.self)); },
    async refresh() {
      try { this.list = (await (await rawFetch("/api/bodies")).json()).bodies || []; }
      catch { this.list = []; }
      this.list.sort((a, b) => (b.self - a.self) || (STATE_ORDER[a.state] - STATE_ORDER[b.state]));
      document.dispatchEvent(new CustomEvent("bodies:update", { detail: this.list }));
      return this.list;
    },
    // The chats of every other body that answers, for the rail.
    async otherChats() {
      const out = [];
      await Promise.all(this.others().filter((b) => b.state === "online").map(async (b) => {
        const url = b.self ? "/api/sessions" : `/b/${encodeURIComponent(b.id)}/api/sessions`;
        try {
          const r = await rawFetch(url);
          if (r.ok) out.push({ body: b, sessions: (await r.json()).sessions || [] });
        } catch { /* that body went away between the list and now: it is just not shown */ }
      }));
      return out.sort((a, b) => (b.body.self - a.body.self) || String(a.body.name).localeCompare(b.body.name));
    },
  };
  window.AltairBody = Body;

  document.addEventListener("DOMContentLoaded", () => {
    if (body) document.documentElement.dataset.body = body;
    Body.refresh();
    setInterval(() => { if (!document.hidden) Body.refresh(); }, 15000);
  });
})();
