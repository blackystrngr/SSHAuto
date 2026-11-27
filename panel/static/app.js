/* sshauto panel: vanilla JS, no build step. All dynamic text goes through textContent (never innerHTML). */
(() => {
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const SVG_TAGS = new Set(["svg", "path", "circle", "line", "rect", "g", "text", "polyline"]);

/* ---------------------------------------------------------------- DOM helpers */
function h(tag, props, ...kids) {
  const svg = SVG_TAGS.has(tag);
  const el = svg ? document.createElementNS(SVGNS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(el.style, v);
    else if (k === "for") el.htmlFor = v;
    else if (!svg && (k === "value" || k === "checked" || k === "disabled" || k === "hidden" || k === "readOnly"
             || k === "textContent" || k === "selected")) el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const clear = (el) => { el.replaceChildren(); return el; };

const ICONS = {
  overview: "M3 12h4l2-6 4 12 2-6h6",
  users: "M16 19v-1.5a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4V19M10 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6M20 19v-1a3.5 3.5 0 0 0-2.5-3.3M16.5 5.2a3 3 0 0 1 0 5.6",
  network: "M12 3v5M12 16v5M3 12h5M16 12h5M12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6",
  shield: "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6zM9 12l2 2 4-4",
  system: "M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01",
  logs: "M5 4h14v16H5zM8 9h8M8 13h8M8 17h5",
  settings: "M4 7h10M18 7h2M4 17h2M10 17h10M16 5v4M8 15v4",
  moon: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  out: "M9 4H5v16h4M16 8l4 4-4 4M20 12H9",
  copy: "M9 9h10v11H9zM5 15V4h10",
  check: "M5 12.5l4.5 4.5L19 7",
  x: "M6 6l12 12M18 6L6 18",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
  unlock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 6.6-1.6",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 0 0-14.5-3.5M4 4v4h4M4 13a8 8 0 0 0 14.5 3.5M20 20v-4h-4",
  cal: "M4 6h16v14H4zM4 10h16M9 3v4M15 3v4",
  phone: "M8 3h8v18H8zM11 18h2",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
  edge: "M4 8h16v8H4zM8 12h.01M12 12h.01M16 12h.01",
  relay: "M4 12h4M16 12h4M8 7l4-3 4 3M8 17l4 3 4-3M12 4v16",
  key: "M14 10a4 4 0 1 1-3.4 6.1L3 16v-3l2-1 1 1 1-1 1 1 2.6-2.6A4 4 0 0 1 14 10M17 9h.01",
  warn: "M12 4l9 16H3zM12 10v4M12 17h.01",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5",
};
function icon(name) {
  return h("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, h("path", { d: ICONS[name] }));
}

/* ---------------------------------------------------------------- formatting */
const fmt = {
  bytes(n) {
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`;
  },
  rate(kbps) { return kbps >= 1000 ? `${(kbps / 1000).toFixed(kbps >= 10000 ? 0 : 1)} Mb/s` : `${Math.round(kbps)} kb/s`; },
  dur(s) {
    const d = Math.floor(s / 86400), hr = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${hr}h` : hr ? `${hr}h ${m}m` : `${m}m`;
  },
  clock(t) { return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); },
  int(n) { return Number(n || 0).toLocaleString(); },
};

/* ---------------------------------------------------------------- api */
class ApiError extends Error {
  constructor(message, hint, status) { super(message); this.hint = hint; this.status = status; }
}
async function api(method, path, body) {
  let res;
  try {
    res = await fetch("api" + path, {
      method, credentials: "same-origin",
      headers: Object.assign({ "X-Requested-With": "sshauto-panel" }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Cannot reach the server.", "Check your connection and try again.", 0);
  }
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && path !== "/login") { showLogin(); throw new ApiError("Signed out", null, 401); }
  if (!res.ok) throw new ApiError(data.error || res.statusText, data.hint, res.status);
  return data;
}
const GET = (p) => api("GET", p);
const POST = (p, b) => api("POST", p, b || {});
const DEL = (p) => api("DELETE", p);

/* ---------------------------------------------------------------- ui plumbing */
function toast(message, opts = {}) {
  const el = h("div", { class: "toast" + (opts.bad ? " bad" : "") }, message, opts.hint && h("small", {}, opts.hint));
  $("#toasts").append(el);
  setTimeout(() => el.remove(), opts.bad ? 7000 : 3200);
}
const $ = (s, r = document) => r.querySelector(s);
const fail = (err) => { if (err.status !== 401) toast(err.message, { bad: true, hint: err.hint }); };

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = h("textarea", { value: text, style: { position: "fixed", opacity: "0" } });
    document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Copied");
}

function dialog(build) {
  const dlg = h("dialog", {});
  const close = (value) => { dlg.close(value); };
  add(dlg, [build(close)]);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}
function confirmBox({ title, body, action, danger }) {
  return new Promise((resolve) => {
    const dlg = dialog((close) => h("div", { class: "dlg" },
      h("h2", {}, title), h("p", { class: "dim", style: { margin: "0" } }, body),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => close("no") }, "Cancel"),
        h("button", { class: "btn " + (danger ? "primary" : "primary"), onclick: () => close("yes") }, action))));
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "yes"));
  });
}

async function runJob(startFn, title) {
  const sheet = h("div", { class: "sheet", role: "status" });
  const spin = h("div", { class: "spin" });
  const status = h("b", {}, title);
  const log = h("pre", {});
  const closeBtn = h("button", { class: "btn small ghost", onclick: () => sheet.remove(), hidden: true }, "Close");
  add(sheet, [h("div", { class: "head" }, spin, status, h("span", { class: "grow" }), closeBtn), log]);
  document.body.append(sheet);
  try {
    const { job } = await startFn();
    for (;;) {
      const j = await GET("/jobs/" + job);
      log.textContent = j.log.slice(-40).join("\n");
      log.scrollTop = log.scrollHeight;
      if (j.status !== "running") {
        spin.remove();
        closeBtn.hidden = false;
        if (j.status === "done") { status.textContent = title + ": done"; setTimeout(() => sheet.remove(), 2200); return j; }
        status.textContent = title + ": failed";
        throw new ApiError("That did not finish.", "Details are in the log below.", 500);
      }
      await new Promise((r) => setTimeout(r, 800));
    }
  } catch (e) {
    spin.remove(); closeBtn.hidden = false;
    if (!(e instanceof ApiError && e.hint === "Details are in the log below.")) { sheet.remove(); }
    throw e;
  }
}

/* ---------------------------------------------------------------- theme */
const theme = {
  get() { try { return localStorage.getItem("panel-theme") || "system"; } catch { return "system"; } },
  set(v) {
    try { localStorage.setItem("panel-theme", v); } catch { /* private mode */ }
    if (v === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = v;
  },
  isDark() {
    const t = this.get();
    return t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  },
  toggle() { this.set(this.isDark() ? "light" : "dark"); },
};
theme.set(theme.get());

/* ---------------------------------------------------------------- shell + router */
const PAGES = [
  { id: "overview", label: "Overview", icon: "overview", render: pageOverview },
  { id: "users", label: "Users", icon: "users", render: pageUsers },
  { id: "network", label: "Network", icon: "network", render: pageNetwork },
  { id: "protection", label: "Protection", icon: "shield", render: pageProtection },
  { id: "system", label: "System", icon: "system", render: pageSystem },
  { id: "logs", label: "Logs", icon: "logs", render: pageLogs },
  { id: "settings", label: "Settings", icon: "settings", render: pageSettings },
];
let ctx = null;          // current page context (timers)
let host = "";

function newCtx() {
  const timers = [];
  return {
    alive: true,
    every(fn, ms) { timers.push(setInterval(() => { if (this.alive && !document.hidden) fn(); }, ms)); },
    stop() { this.alive = false; timers.forEach(clearInterval); },
  };
}

function buildShell() {
  const root = clear($("#root"));
  const nav = h("nav", { class: "nav", "aria-label": "Sections" },
    PAGES.map((p) => h("a", { href: "#/" + p.id, "data-id": p.id }, icon(p.icon), h("span", {}, p.label))));
  const themeBtn = () => h("button", { onclick: () => { theme.toggle(); buildThemeIcons(); }, "aria-label": "Switch light or dark theme" },
    icon(theme.isDark() ? "sun" : "moon"), h("span", {}, theme.isDark() ? "Light theme" : "Dark theme"));
  const rail = h("aside", { class: "rail" },
    h("div", { class: "brand" }, h("b", {}, "sshauto"), h("span", { id: "hostname" }, host || "panel")),
    nav,
    h("div", { class: "rail-foot", id: "railfoot" }, themeBtn(),
      h("button", { onclick: signOut }, icon("out"), h("span", {}, "Sign out"))));
  const main = h("main", { id: "main" });
  const top = h("div", { class: "topbar" }, h("b", {}, "sshauto"),
    h("div", { class: "row" },
      h("button", { class: "btn ghost iconbtn", id: "themebtn-m", "aria-label": "Switch light or dark theme", onclick: () => { theme.toggle(); buildThemeIcons(); } }, icon(theme.isDark() ? "sun" : "moon")),
      h("button", { class: "btn ghost iconbtn", "aria-label": "Sign out", onclick: signOut }, icon("out"))));
  main.append(top, h("div", { id: "page" }));
  root.append(h("div", { class: "app" }, rail, main));
  function buildThemeIcons() {
    const foot = $("#railfoot"); foot.firstChild.replaceWith(themeBtn());
    const m = $("#themebtn-m"); m.replaceChildren(icon(theme.isDark() ? "sun" : "moon"));
  }
}

async function route() {
  if (!$("#page")) return;
  const id = (location.hash.match(/^#\/([a-z]+)/) || [])[1] || "overview";
  const page = PAGES.find((p) => p.id === id) || PAGES[0];
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.id === page.id) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (ctx) ctx.stop();
  ctx = newCtx();
  const el = clear($("#page"));
  document.title = `${page.label} · sshauto`;
  try { await page.render(el, ctx); } catch (e) { if (e.status !== 401) { add(el, [h("p", { class: "empty" }, h("b", {}, "This page could not load"), e.message)]); } }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

async function signOut() {
  try { await POST("/logout"); } catch { /* already out */ }
  showLogin();
}

function head(title, sub, ...actions) {
  return h("div", { class: "page-head" }, h("div", {}, h("h1", {}, title), sub && h("p", { class: "sub" }, sub)),
    actions.length ? h("div", { class: "row" }, actions) : null);
}
function block(title, sub, body, ...actions) {
  return h("section", { class: "block" },
    h("div", { class: "block-head" }, h("div", {}, h("h2", {}, title), sub && h("p", { class: "sub", style: { margin: "0" } }, sub)),
      actions.length ? h("div", { class: "row" }, actions) : null), body);
}

/* ---------------------------------------------------------------- login */
async function showLogin(message) {
  if (ctx) ctx.stop();
  let configured = true;
  try { configured = (await GET("/session")).configured; } catch { /* server unreachable: show form anyway */ }
  const err = h("div", { class: "err", role: "alert" }, message || "");
  const user = h("input", { type: "text", id: "u", autocomplete: "username", required: true, value: "admin" });
  const pass = h("input", { type: "password", id: "p", autocomplete: "current-password", required: true });
  const btn = h("button", { class: "btn primary", type: "submit", style: { width: "100%" } }, "Sign in");
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault(); btn.disabled = true; err.textContent = "";
      try { await POST("/login", { username: user.value, password: pass.value }); await boot(); }
      catch (ex) { err.textContent = ex.message; btn.disabled = false; pass.select(); }
    },
  },
    h("h1", { class: "mark" }, "sshauto"),
    h("p", {}, configured ? "Sign in to manage this server." : "No admin account yet. Run python3 main.py panel --reset-password on the server."),
    h("label", { class: "field", for: "u" }, h("span", {}, "Username"), user),
    h("label", { class: "field", for: "p" }, h("span", {}, "Password"), pass),
    err, btn);
  clear($("#root")).append(h("div", { class: "login" }, form));
  (pass.value ? pass : user).focus();
  pass.focus();
}

/* ---------------------------------------------------------------- overview */
const NICE = [1, 2, 5, 10];
function niceMax(v) {
  if (v <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const n of NICE) if (n * p >= v) return n * p;
  return 10 * p;
}

function drawThroughput(box, pts, tip) {
  clear(box);
  if (pts.length < 2) { box.append(h("p", { class: "dim" }, "Collecting samples…")); return; }
  const W = box.clientWidth || 640, H = box.clientHeight || 232;
  const wide = W > 560;
  const m = { l: 52, r: wide ? 118 : 12, t: 10, b: 26 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const top = niceMax(Math.max(...pts.map((p) => Math.max(p.rx, p.tx))) * 1.05);
  const x = (i) => m.l + (i / (pts.length - 1)) * iw;
  const y = (v) => m.t + ih - (v / top) * ih;
  const line = (key) => pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p[key]).toFixed(1)}`).join("");
  const svg = h("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Download and upload throughput for the last 20 minutes" });
  for (const f of [0, 0.5, 1]) {
    const v = top * f, yy = y(v);
    svg.append(h("line", { class: f === 0 ? "axis" : "grid", x1: m.l, x2: W - m.r, y1: yy, y2: yy }),
      h("text", { x: m.l - 8, y: yy + 4, "text-anchor": "end" }, fmt.rate(v)));
  }
  svg.append(h("text", { x: m.l, y: H - 6 }, fmt.clock(pts[0].t)),
    h("text", { x: W - m.r, y: H - 6, "text-anchor": "end" }, "now"));
  svg.append(h("path", { class: "ln up", d: line("tx") }), h("path", { class: "ln down", d: line("rx") }));
  const last = pts[pts.length - 1], yd = y(last.rx), yu = y(last.tx);
  for (const [cls, yy] of [["up", yu], ["down", yd]]) svg.append(h("circle", { class: "pt " + cls, cx: x(pts.length - 1), cy: yy, r: 5 }));
  if (wide && Math.abs(yd - yu) >= 20) {     // direct labels only when the ends don't collide
    svg.append(h("text", { class: "endlabel", x: W - m.r + 12, y: yd + 4 }, `Down ${fmt.rate(last.rx)}`),
      h("text", { class: "endlabel", x: W - m.r + 12, y: yu + 4 }, `Up ${fmt.rate(last.tx)}`));
  }
  const cross = h("line", { class: "cross", y1: m.t, y2: m.t + ih, visibility: "hidden" });
  const cd = h("circle", { class: "pt down", r: 5, visibility: "hidden" });
  const cu = h("circle", { class: "pt up", r: 5, visibility: "hidden" });
  const hit = h("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih });
  svg.append(cross, cd, cu, hit);
  const move = (e) => {
    const r = svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(pts.length - 1, Math.round(((px - m.l) / iw) * (pts.length - 1))));
    const p = pts[i], cx = x(i);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    cd.setAttribute("cx", cx); cd.setAttribute("cy", y(p.rx)); cd.setAttribute("visibility", "visible");
    cu.setAttribute("cx", cx); cu.setAttribute("cy", y(p.tx)); cu.setAttribute("visibility", "visible");
    clear(tip).append(h("div", { class: "t" }, fmt.clock(p.t)),
      h("div", { class: "r" }, h("span", {}, h("i", { class: "down" }), "Down"), h("b", {}, fmt.rate(p.rx))),
      h("div", { class: "r" }, h("span", {}, h("i", { class: "up" }), "Up"), h("b", {}, fmt.rate(p.tx))));
    tip.hidden = false;
    const left = (cx / W) * r.width;
    tip.style.left = Math.min(Math.max(left + 14, 0), r.width - 170) + "px";
    tip.style.top = "8px";
  };
  const leave = () => { tip.hidden = true; for (const n of [cross, cd, cu]) n.setAttribute("visibility", "hidden"); };
  hit.addEventListener("pointermove", move); hit.addEventListener("pointerleave", leave);
  box.append(svg);
}

function meterRow(label, pct, detail) {
  const level = pct >= 95 ? "bad" : pct >= 85 ? "warn" : "";
  const bar = h("div", { class: "meter-bar", role: "meter", "aria-valuenow": Math.round(pct), "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": label },
    h("i", { class: level, style: { width: Math.min(100, pct) + "%" } }));
  return h("div", { class: "meter" }, h("b", {}, label), bar,
    h("div", { class: "v" }, `${Math.round(pct)}%`, h("small", {}, detail + (level ? " · high" : ""))));
}

function pathNodes(o) {
  const svc = Object.fromEntries(o.services.map((s) => [s.unit, s.state]));
  const up = (u) => svc[u] === "active";
  const tunnels = o.now.tunnels || 0;
  const ports = o.ports || {};
  const nodes = [
    { icon: "phone", name: "Clients", place: "SSH over WebSocket apps", ok: null,
      state: tunnels ? "connected" : "idle", metric: [fmt.int(tunnels), tunnels === 1 ? "tunnel" : "tunnels"] },
    { icon: "edge", name: "nginx", place: "public ports, TLS", ok: up("nginx"), state: svc.nginx },
    { icon: "relay", name: "WS proxy", place: `127.0.0.1:${ports.proxy ?? "?"}`, ok: up("ws-ssh-proxy"), state: svc["ws-ssh-proxy"] },
    { icon: "key", name: "Dropbear", place: `127.0.0.1:${ports.dropbear ?? "?"}`, ok: up("dropbear-tunnel"), state: svc["dropbear-tunnel"] },
    { icon: "globe", name: "Internet", place: "this server's uplink", ok: null, state: "open",
      metric2: [`${fmt.rate(o.now.rx || 0)}`, "down", `${fmt.rate(o.now.tx || 0)}`, "up"] },
  ];
  return nodes.map((n, i) => {
    const next = nodes[i + 1];
    let wire = "";
    if (next) {
      if (n.ok === false || next.ok === false) wire = "down";
      else if ((n.ok !== false && next.ok !== false) && (i > 0 || tunnels > 0)) wire = "live";
    }
    const stateClass = n.ok === true ? "good" : n.ok === false ? "bad" : "";
    const label = n.ok === true ? "running" : n.ok === false ? (n.state === "missing" ? "not installed" : n.state || "down") : n.state;
    return h("div", { class: `node ${n.ok === true ? "live" : n.ok === false ? "down" : "edge"} ${wire === "down" ? "down" : wire === "live" ? "live" : ""}`, style: { "--i": i } },
      h("div", { class: "node-badge" }, icon(n.icon)),
      h("div", { class: "col" },
        h("div", { class: "node-name" }, n.name),
        h("div", { class: "node-state " + stateClass }, n.ok === false && icon("warn"), label),
        n.metric && h("div", { class: "node-metric num" }, n.metric[0], h("small", {}, n.metric[1])),
        n.metric2 && h("div", { class: "node-metric num" }, n.metric2[0], h("small", {}, n.metric2[1])),
        n.metric2 && h("div", { class: "node-state num" }, `${n.metric2[2]} ${n.metric2[3]}`),
        !n.metric && !n.metric2 && h("div", { class: "node-place" }, n.place)));
  });
}

async function pageOverview(main, c) {
  const pathEl = h("div", { class: "path" });
  const figures = h("div", { class: "figures" });
  const tip = h("div", { class: "tip", hidden: true });
  const box = h("div", { class: "chart" });
  const tableBody = h("tbody", {});
  const meters = h("div", { class: "meters" });
  const svcList = h("div", { class: "pills" });
  let series = [], overview = null;

  const plate = h("div", { class: "plate" },
    h("div", { class: "plate-head" }, h("div", {}, h("h2", {}, "Throughput"), h("span", { class: "dim" }, "Network interface, last 20 minutes")),
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "down" }), "Down"), h("span", {}, h("i", { class: "up" }), "Up"))),
    h("div", { style: { position: "relative" } }, box, tip),
    h("details", { class: "dataview" }, h("summary", {}, "Show values"),
      h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Down"), h("th", {}, "Up"), h("th", {}, "Tunnels"))), tableBody)));

  add(main, [
    head("Overview", "Live state of the relay on this server."),
    pathEl, figures,
    h("div", { style: { height: "24px" } }), plate,
    block("This server", null, meters),
    block("Services", null, svcList, h("a", { class: "btn small", href: "#/system" }, "Manage")),
  ]);

  const paintChart = () => drawThroughput(box, series, tip);
  const paintTable = () => {
    clear(tableBody);
    for (const p of series.slice(-8).reverse()) {
      tableBody.append(h("tr", {}, h("td", { class: "num" }, fmt.clock(p.t)), h("td", { class: "num" }, fmt.rate(p.rx)),
        h("td", { class: "num" }, fmt.rate(p.tx)), h("td", { class: "num" }, p.tunnels)));
    }
  };
  const paintOverview = () => {
    const o = overview, s = o.system;
    clear(pathEl).append(h("div", { class: "path-head" }, h("strong", {}, "Tunnel path"), h("span", { class: "dim" }, o.domain || "")),
      h("div", { class: "path-track" }, pathNodes(o)));
    const hn = $("#hostname"); if (hn) hn.textContent = s.hostname;
    clear(figures).append(
      h("div", { class: "figure" }, h("b", { class: "num" }, fmt.int(o.now.tunnels || 0)), h("span", {}, "Tunnels open now")),
      h("div", { class: "figure" }, h("b", { class: "num" }, fmt.int(o.users)), h("span", {}, "Accounts")),
      h("div", { class: "figure" }, h("b", { class: "num" }, fmt.dur(s.uptime)), h("span", {}, "Server uptime")));
    clear(meters).append(
      meterRow("CPU", o.now.cpu || 0, `${s.cores} cores · load ${s.load[0]}`),
      meterRow("Memory", (s.mem_used / s.mem_total) * 100, `${fmt.bytes(s.mem_used)} of ${fmt.bytes(s.mem_total)}`),
      meterRow("Disk", (s.disk_used / s.disk_total) * 100, `${fmt.bytes(s.disk_used)} of ${fmt.bytes(s.disk_total)}`));
    clear(svcList).append(...o.services.filter((x) => !(x.optional && x.state === "missing")).map((x) =>
      h("span", { class: "pill" }, h("span", { class: "dot " + (x.state === "active" ? "good" : "bad") }), x.label)));
  };
  const loadOverview = async () => { overview = await GET("/overview"); if (c.alive) paintOverview(); };
  const loadSeries = async () => { series = (await GET("/metrics")).series; if (c.alive) { paintChart(); paintTable(); } };
  await Promise.all([loadOverview(), loadSeries()]);
  c.every(() => loadOverview().catch(() => {}), 3000);
  c.every(() => loadSeries().catch(() => {}), 6000);
  new ResizeObserver(() => { if (c.alive) paintChart(); }).observe(box);
}

/* ---------------------------------------------------------------- users */
function genPassword() {
  const abc = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const buf = new Uint32Array(12); crypto.getRandomValues(buf);
  return Array.from(buf, (n) => abc[n % abc.length]).join("");
}

function userStatus(u) {
  if (u.locked) return h("span", { class: "chip warn" }, icon("lock"), "Locked");
  if (u.expired) return h("span", { class: "chip bad" }, icon("x"), "Expired");
  if (u.days_left != null && u.days_left <= 7) return h("span", { class: "chip warn" }, icon("cal"), u.days_left === 0 ? "Expires today" : `${u.days_left}d left`);
  return h("span", { class: "chip good" }, icon("check"), "Active");
}

function secretRow(label, value) {
  return h("div", { class: "secret" }, h("small", {}, label), h("code", {}, value),
    h("button", { class: "btn small iconbtn", "aria-label": `Copy ${label.toLowerCase()}`, onclick: () => copyText(value) }, icon("copy")));
}

function showCredentials(res) {
  const c = res.connection;
  const ports = `HTTP ${c.http_ports.slice(0, 3).join(", ")} · TLS ${c.https_ports.slice(0, 3).join(", ")}`;
  const all = [`Server: ${c.domain}`, `Username: ${res.username}`, `Password: ${res.password}`, `Payload: ${c.payload}`, ports].join("\n");
  dialog((close) => h("div", { class: "dlg" }, h("h2", {}, "Account ready"),
    h("p", { class: "dim", style: { marginTop: "0" } }, "The password is shown once. Copy it now."),
    secretRow("Server", c.domain), secretRow("Username", res.username), secretRow("Password", res.password), secretRow("Payload", c.payload),
    h("p", { class: "dim", style: { fontSize: "14px" } }, ports),
    h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => copyText(all) }, icon("copy"), "Copy all"),
      h("button", { class: "btn primary", onclick: () => close("ok") }, "Done"))));
}

function addUserDialog(reload) {
  dialog((close) => {
    const name = h("input", { type: "text", id: "nu", autocomplete: "off", required: true, maxlength: 32, placeholder: "e.g. alex_phone" });
    const pass = h("input", { type: "text", id: "np", autocomplete: "off", placeholder: "Leave empty to generate", maxlength: 64 });
    const days = h("input", { type: "number", id: "nd", min: 0, max: 3650, value: 30 });
    const err = h("p", { class: "chip bad", hidden: true, role: "alert" });
    const go = h("button", { class: "btn primary", type: "submit" }, "Create user");
    return h("form", {
      onsubmit: async (e) => {
        e.preventDefault(); go.disabled = true; err.hidden = true;
        try {
          const res = await POST("/users", { username: name.value.trim(), password: pass.value, days: Number(days.value) });
          close("ok"); showCredentials(res); reload();
        } catch (ex) { err.textContent = ex.message + (ex.hint ? ` (${ex.hint})` : ""); err.hidden = false; go.disabled = false; }
      },
    },
      h("h2", {}, "Add user"),
      h("label", { class: "field", for: "nu" }, h("span", {}, "Username"), name, h("small", {}, "Lowercase letters, digits, _ and -")),
      h("label", { class: "field", for: "np" }, h("span", {}, "Password"),
        h("div", { class: "row" }, h("div", { class: "grow" }, pass),
          h("button", { class: "btn", type: "button", onclick: () => { pass.value = genPassword(); } }, "Generate"))),
      h("label", { class: "field", for: "nd" }, h("span", {}, "Valid for (days)"), days, h("small", {}, "0 means the account never expires")),
      err,
      h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: () => close("no") }, "Cancel"), go));
  });
}

function expiryDialog(user, reload) {
  dialog((close) => {
    const days = h("input", { type: "number", id: "ed", min: 0, max: 3650, value: 30 });
    const apply = async (n) => {
      try { await POST(`/users/${user.username}/expiry`, { days: n }); toast(n ? `${user.username} now expires in ${n} days` : `${user.username} never expires`); close("ok"); reload(); }
      catch (e) { fail(e); }
    };
    return h("form", { onsubmit: (e) => { e.preventDefault(); apply(Number(days.value)); } },
      h("h2", {}, `Change expiry for ${user.username}`),
      h("p", { class: "dim", style: { marginTop: "0" } }, `Currently: ${user.expires}`),
      h("div", { class: "row", style: { marginBottom: "14px" } },
        [7, 30, 90, 365].map((n) => h("button", { class: "btn small", type: "button", onclick: () => apply(n) }, `${n} days`)),
        h("button", { class: "btn small", type: "button", onclick: () => apply(0) }, "Never")),
      h("label", { class: "field", for: "ed" }, h("span", {}, "Or a number of days from today"), days),
      h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: () => close("no") }, "Cancel"),
        h("button", { class: "btn primary", type: "submit" }, "Save expiry")));
  });
}

async function pageUsers(main, c) {
  let users = [];
  const list = h("div", {});
  const search = h("input", { type: "text", placeholder: "Search users", "aria-label": "Search users", style: { maxWidth: "280px" } });
  const count = h("span", { class: "dim" });
  const reload = async () => { users = (await GET("/users")).users; if (c.alive) paint(); };
  const act = (fn) => async () => { try { await fn(); await reload(); } catch (e) { fail(e); } };

  function paint() {
    const q = search.value.trim().toLowerCase();
    const shown = users.filter((u) => u.username.includes(q));
    count.textContent = `${shown.length} of ${users.length}`;
    clear(list);
    if (!users.length) {
      list.append(h("div", { class: "empty" }, h("b", {}, "No users yet"), "Add the first account, then give its username, password and payload to the app on the phone."));
      return;
    }
    const body = shown.map((u) => h("tr", {},
      h("td", {}, h("div", { class: "name" }, u.username)),
      h("td", {}, userStatus(u)),
      h("td", { class: "dim num" }, u.expires === "never" ? "Never expires" : u.expires),
      h("td", { class: "r" }, h("div", { class: "row end", style: { gap: "4px" } },
        h("button", { class: "btn small ghost iconbtn", "aria-label": `Change expiry for ${u.username}`, title: "Change expiry", onclick: () => expiryDialog(u, reload) }, icon("cal")),
        h("button", { class: "btn small ghost iconbtn", "aria-label": `${u.locked ? "Unlock" : "Lock"} ${u.username}`, title: u.locked ? "Unlock" : "Lock",
          onclick: act(async () => { await POST(`/users/${u.username}/${u.locked ? "unlock" : "lock"}`); toast(u.locked ? `${u.username} unlocked` : `${u.username} locked and signed out`); }) }, icon(u.locked ? "unlock" : "lock")),
        h("button", { class: "btn small ghost iconbtn danger", "aria-label": `Delete ${u.username}`, title: "Delete",
          onclick: async () => {
            if (await confirmBox({ title: `Delete ${u.username}?`, body: "The account and its home folder are removed and any open tunnel is dropped.", action: "Delete user", danger: true })) {
              await act(async () => { await DEL(`/users/${u.username}`); toast(`${u.username} deleted`); })();
            }
          } }, icon("trash")))))
    );
    list.append(h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", {}, "Status"), h("th", {}, "Expires"), h("th", { class: "r" }, ""))),
      h("tbody", {}, body)));
    if (!shown.length) list.append(h("div", { class: "empty" }, `Nobody matches "${search.value}".`));
  }
  search.addEventListener("input", paint);
  add(main, [head("Users", "Accounts that can connect through the relay.",
    h("button", { class: "btn primary", onclick: () => addUserDialog(reload) }, icon("plus"), "Add user")),
    h("div", { class: "row", style: { marginBottom: "6px" } }, search, count), list]);
  await reload();
}

/* ---------------------------------------------------------------- network */
function portPill(kind, port, custom, reload) {
  return h("span", { class: "pill" + (custom ? " custom" : "") }, port,
    custom && h("button", { "aria-label": `Remove port ${port}`, title: "Remove", onclick: async () => {
      try { await DEL(`/ports/${kind}/${port}`); toast(`Port ${port} removed`); reload(); } catch (e) { fail(e); } } }, icon("x")));
}

async function pageNetwork(main, c) {
  const portsBox = h("div", {}), connBox = h("div", {}), squidBox = h("div", {});
  const kind = h("select", { "aria-label": "Port type", style: { width: "auto" } }, h("option", { value: "https" }, "HTTPS"), h("option", { value: "http" }, "HTTP"));
  const num = h("input", { type: "number", min: 1, max: 65535, placeholder: "Port", "aria-label": "Port number", style: { width: "120px" } });

  async function loadPorts() {
    const p = await GET("/ports"); if (!c.alive) return;
    const cu = { http: new Set(p.custom_http), https: new Set(p.custom_https) };
    clear(portsBox).append(
      h("p", { class: "dim", style: { margin: "0 0 8px" } }, "HTTP"), h("div", { class: "pills" }, p.http.map((n) => portPill("http", n, cu.http.has(n), loadPorts))),
      h("p", { class: "dim", style: { margin: "18px 0 8px" } }, "HTTPS"), h("div", { class: "pills" }, p.https.map((n) => portPill("https", n, cu.https.has(n), loadPorts))),
      h("p", { class: "dim", style: { fontSize: "14px" } }, "Blue ports were added by you. Ports from Cloudflare's supported list work behind the proxy."));
  }
  async function loadConn() {
    const k = await GET("/connection"); if (!c.alive) return;
    clear(connBox).append(
      h("dl", { class: "kv" },
        h("dt", {}, "Server"), h("dd", {}, k.domain), h("button", { class: "btn small iconbtn", "aria-label": "Copy server", onclick: () => copyText(k.domain) }, icon("copy")),
        h("dt", {}, "Backend"), h("dd", {}, `Dropbear on 127.0.0.1:${k.dropbear_port}`), h("span", {}),
        h("dt", {}, "Admin SSH"), h("dd", {}, `port ${k.ssh_port}`), h("span", {})),
      h("p", { class: "dim", style: { margin: "16px 0 6px" } }, "Payload for apps like NetMod"),
      h("div", { class: "row" }, h("code", { class: "block grow" }, k.payload),
        h("button", { class: "btn small iconbtn", "aria-label": "Copy payload", onclick: () => copyText(k.payload) }, icon("copy"))));
  }
  async function loadSquid() {
    const s = await GET("/squid"); if (!c.alive) return;
    const user = h("input", { type: "text", id: "su", value: s.username || "", autocomplete: "off", maxlength: 32 });
    const pass = h("input", { type: "password", id: "sp", value: s.password || "", autocomplete: "off", maxlength: 64 });
    const port = h("input", { type: "number", id: "sport", value: s.port || 3128, min: 1, max: 65535 });
    const reveal = h("button", { class: "btn small iconbtn", type: "button", "aria-label": "Show password", onclick: () => { pass.type = pass.type === "password" ? "text" : "password"; } }, icon("eye"));
    const go = h("button", { class: "btn primary", type: "submit" }, s.installed ? "Apply changes" : "Turn on");
    clear(squidBox).append(h("form", {
      onsubmit: async (e) => {
        e.preventDefault(); go.disabled = true;
        try { await runJob(() => POST("/squid", { username: user.value, password: pass.value, port: Number(port.value) }), "Squid proxy"); toast("Squid proxy is on"); loadSquid(); }
        catch (ex) { fail(ex); go.disabled = false; }
      } },
      h("div", { class: "row", style: { marginBottom: "14px" } }, h("span", { class: "chip " + (s.installed ? "good" : "") }, s.installed ? icon("check") : null, s.installed ? "Running" : "Off"),
        h("span", { class: "dim" }, "HTTP proxy that always asks for a username and password.")),
      h("div", { class: "row", style: { alignItems: "flex-start" } },
        h("label", { class: "field grow", for: "su" }, h("span", {}, "Username"), user),
        h("label", { class: "field grow", for: "sp" }, h("span", {}, "Password"), h("div", { class: "row" }, h("div", { class: "grow" }, pass), reveal)),
        h("label", { class: "field", for: "sport", style: { width: "110px" } }, h("span", {}, "Port"), port)),
      h("div", { class: "row" }, go, s.installed && h("button", { class: "btn danger", type: "button", onclick: async () => {
        if (!(await confirmBox({ title: "Turn off Squid?", body: "The proxy stops and its port is closed.", action: "Turn off" }))) return;
        try { await runJob(() => DEL("/squid"), "Remove Squid"); loadSquid(); } catch (ex) { fail(ex); } } }, "Turn off"))));
  }
  add(main, [head("Network", "Public ports, how apps connect, and the optional Squid proxy."),
    block("Relay ports", "nginx listens on these and forwards to the WebSocket proxy.", portsBox,
      h("form", { class: "row", onsubmit: async (e) => {
        e.preventDefault();
        try { await POST("/ports", { kind: kind.value, port: Number(num.value) }); toast(`Port ${num.value} added`); num.value = ""; loadPorts(); } catch (ex) { fail(ex); } } },
        kind, num, h("button", { class: "btn", type: "submit" }, icon("plus"), "Add port"))),
    block("Connection details", null, connBox),
    block("Squid proxy", "Optional.", squidBox)]);
  await Promise.all([loadPorts(), loadConn(), loadSquid()]);
}

/* ---------------------------------------------------------------- protection */
async function pageProtection(main, c) {
  const body = h("div", {});
  async function load() {
    const f = await GET("/filter"); if (!c.alive) return;
    const sw = {};
    const rows = Object.keys(f.descriptions).map((cat) => {
      sw[cat] = h("input", { type: "checkbox", id: "c-" + cat, checked: f.installed ? f.categories.includes(cat) : true });
      return h("li", {}, h("div", { class: "main" }, h("b", {}, cat[0].toUpperCase() + cat.slice(1)),
        h("span", {}, f.descriptions[cat] + (f.counts[cat] ? ` · ${fmt.int(f.counts[cat])} domains` : ""))),
        h("label", { class: "switch", for: "c-" + cat }, sw[cat], h("i", {}), h("span", { class: "sr" }, cat)));
    });
    const priv = h("input", { type: "checkbox", id: "priv", checked: f.private });
    const allow = h("textarea", { id: "allow", placeholder: "example.com", "aria-label": "Never block these domains", value: (f.allow || []).join("\n") });
    const apply = h("button", { class: "btn primary", type: "submit" }, f.installed ? "Apply" : "Turn on filter");
    clear(body).append(h("form", {
      onsubmit: async (e) => {
        e.preventDefault(); apply.disabled = true;
        try {
          await runJob(() => POST("/filter", { categories: Object.keys(sw).filter((k) => sw[k].checked),
            allow: allow.value.split(/[\s,]+/).filter(Boolean), block_private: priv.checked }), "Content filter");
          toast("Filter updated");
        } catch (ex) { fail(ex); }
        load();
      } },
      h("div", { class: "row", style: { marginBottom: "10px" } },
        h("span", { class: "chip " + (f.installed ? "good" : "") }, f.installed ? icon("check") : null, f.installed ? "Filter is on" : "Filter is off"),
        h("span", { class: "dim" }, "Blocks domains for traffic that goes through the tunnel. System DNS is not changed.")),
      h("ul", { class: "rowlist" }, rows,
        h("li", {}, h("div", { class: "main" }, h("b", {}, "Block private networks"),
          h("span", {}, "Stops tunnel users reaching 10.x, 172.16.x, 192.168.x and the cloud metadata address.")),
          h("label", { class: "switch", for: "priv" }, priv, h("i", {}), h("span", { class: "sr" }, "Block private networks")))),
      h("label", { class: "field", for: "allow", style: { marginTop: "16px" } }, h("span", {}, "Never block"), allow,
        h("small", {}, "One domain per line. Subdomains are included.")),
      h("div", { class: "row" }, apply,
        f.installed && h("button", { class: "btn", type: "button", onclick: async () => {
          try { await runJob(() => POST("/filter/update"), "Update blocklists"); toast("Blocklists updated"); } catch (ex) { fail(ex); } load(); } }, icon("refresh"), "Update lists"),
        f.installed && h("button", { class: "btn danger", type: "button", onclick: async () => {
          if (!(await confirmBox({ title: "Remove the content filter?", body: "Tunnel services restart and go back to the system's DNS.", action: "Remove filter" }))) return;
          try { await runJob(() => DEL("/filter"), "Remove filter"); } catch (ex) { fail(ex); } load(); } }, "Remove"))));
  }
  add(main, [head("Protection", "Block ads, malware, phishing and cryptominers for everyone who uses the tunnel."), body,
    h("p", { class: "notice", style: { marginTop: "26px" } }, "This works on domain names. An app that connects to a raw IP address, or uses its own encrypted DNS, is not filtered. Lists are updated when you press Update lists.")]);
  await load();
}

/* ---------------------------------------------------------------- system */
async function pageSystem(main, c) {
  const svcBox = h("div", {}), healthBox = h("div", {}), speedBox = h("div", {});
  async function loadServices() {
    const s = (await GET("/services")).services; if (!c.alive) return;
    clear(svcBox).append(h("ul", { class: "rowlist" }, s.map((x) => {
      const ok = x.state === "active", missing = x.state === "missing";
      return h("li", {}, h("span", { class: "dot " + (ok ? "good" : missing ? "" : "bad") }),
        h("div", { class: "main" }, h("b", {}, x.label), h("span", {}, `${x.unit} · ${missing ? (x.optional ? "not installed" : "missing") : x.state}`)),
        x.restartable && !missing && h("button", { class: "btn small", onclick: async () => {
          if (["nginx", "ws-ssh-proxy", "dropbear-tunnel"].includes(x.unit) &&
              !(await confirmBox({ title: `Restart ${x.label}?`, body: "Open tunnels are dropped and clients reconnect.", action: "Restart" }))) return;
          try { await POST(`/services/${x.unit}/restart`); toast(`${x.label} restarted`); loadServices(); } catch (e) { fail(e); } } }, icon("refresh"), "Restart"));
    })));
  }
  async function runChecks() {
    clear(healthBox).append(h("div", { class: "row" }, h("div", { class: "spin" }), h("span", { class: "dim" }, "Testing the tunnel path…")));
    try {
      const d = await GET("/doctor"); if (!c.alive) return;
      clear(healthBox).append(
        h("div", { class: "row", style: { marginBottom: "6px" } }, h("span", { class: "chip " + (d.ok ? "good" : "bad") }, icon(d.ok ? "check" : "warn"), d.ok ? "Everything works" : "Something is wrong"),
          !d.ok && h("button", { class: "btn small primary", onclick: async () => {
            try { await runJob(() => POST("/doctor/repair"), "Repair"); toast("Repair finished"); } catch (e) { fail(e); } runChecks(); loadServices(); } }, "Repair now")),
        h("ul", { class: "rowlist checks" }, d.checks.map((k) => h("li", {}, icon(k.ok ? "check" : "warn"),
          h("div", { class: "main" }, h("b", { style: { color: k.ok ? "" : "var(--bad-text)" } }, k.name), k.detail && h("span", {}, k.detail))))));
    } catch (e) { clear(healthBox); fail(e); }
  }
  const speed = async () => {
    try { const j = await runJob(() => POST("/speedtest"), "Speed test");
      clear(speedBox).append(h("div", { class: "figure" }, h("b", { class: "num" }, `${j.result.mbps}`), h("span", {}, "Mb/s download from this server"))); }
    catch (e) { fail(e); }
  };
  add(main, [head("System", "Services, health checks and tools."),
    block("Services", null, svcBox),
    block("Health check", "Sends a test connection through nginx, the proxy and Dropbear.", healthBox, h("button", { class: "btn", onclick: runChecks }, icon("refresh"), "Run again")),
    block("Speed test", "Measures this server's download speed.", speedBox, h("button", { class: "btn", onclick: speed }, "Run speed test"))]);
  await loadServices(); runChecks();
  c.every(() => loadServices().catch(() => {}), 8000);
}

/* ---------------------------------------------------------------- logs */
async function pageLogs(main, c) {
  const src = h("select", { "aria-label": "Log source", style: { width: "auto" } });
  const lines = h("select", { "aria-label": "Number of lines", style: { width: "auto" } }, [100, 200, 500].map((n) => h("option", { value: n }, `${n} lines`)));
  const live = h("input", { type: "checkbox", id: "live" });
  const pre = h("pre", { class: "log", tabindex: 0, "aria-label": "Log output" });
  const names = { proxy: "WebSocket proxy", nginx: "nginx", dropbear: "Dropbear", fail2ban: "fail2ban", dnsfilter: "DNS filter", panel: "This panel" };
  let ready = false;
  async function load() {
    const d = await GET(`/logs?source=${src.value || "proxy"}&lines=${lines.value}`); if (!c.alive) return;
    if (!ready) { d.sources.forEach((s) => src.append(h("option", { value: s }, names[s] || s))); src.value = d.source; ready = true; }
    const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    pre.textContent = d.lines.length ? d.lines.join("\n") : "Nothing logged yet for this source.";
    if (stick) pre.scrollTop = pre.scrollHeight;
  }
  src.addEventListener("change", load); lines.addEventListener("change", load);
  add(main, [head("Logs", "Recent output from each service."),
    h("div", { class: "row", style: { marginBottom: "12px" } }, src, lines,
      h("button", { class: "btn", onclick: () => load().catch(fail) }, icon("refresh"), "Refresh"),
      h("label", { class: "row", for: "live", style: { gap: "8px" } }, h("span", { class: "switch" }, live, h("i", {})), "Follow")), pre]);
  await load(); pre.scrollTop = pre.scrollHeight;
  c.every(() => { if (live.checked) load().catch(() => {}); }, 4000);
}

/* ---------------------------------------------------------------- settings */
async function pageSettings(main) {
  const cur = h("input", { type: "password", id: "cp", autocomplete: "current-password", required: true });
  const nw = h("input", { type: "password", id: "np1", autocomplete: "new-password", required: true, minlength: 10 });
  const nw2 = h("input", { type: "password", id: "np2", autocomplete: "new-password", required: true });
  const go = h("button", { class: "btn primary", type: "submit" }, "Change password");
  const mode = h("select", { id: "theme", style: { maxWidth: "240px" }, onchange: (e) => theme.set(e.target.value) },
    [["system", "Match my device"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => h("option", { value: v, selected: theme.get() === v }, l)));
  add(main, [head("Settings"),
    block("Password", "Changing it signs out every other browser.", h("form", { style: { maxWidth: "420px" }, onsubmit: async (e) => {
      e.preventDefault();
      if (nw.value !== nw2.value) return toast("The two new passwords do not match.", { bad: true });
      go.disabled = true;
      try { await POST("/settings/password", { current: cur.value, new: nw.value }); toast("Password changed"); cur.value = nw.value = nw2.value = ""; }
      catch (ex) { fail(ex); }
      go.disabled = false; } },
      h("label", { class: "field", for: "cp" }, h("span", {}, "Current password"), cur),
      h("label", { class: "field", for: "np1" }, h("span", {}, "New password"), nw, h("small", {}, "At least 10 characters")),
      h("label", { class: "field", for: "np2" }, h("span", {}, "New password again"), nw2), go)),
    block("Appearance", null, h("label", { class: "field", for: "theme", style: { maxWidth: "240px" } }, h("span", {}, "Theme"), mode)),
    block("Session", "You stay signed in for 12 hours.", h("button", { class: "btn", onclick: signOut }, icon("out"), "Sign out"))]);
}

/* ---------------------------------------------------------------- boot */
async function boot() {
  let s;
  try { s = await GET("/session"); } catch (e) { if (e.status !== 401) return showLogin(e.message); return; }
  if (!s.authenticated) return showLogin();
  try { host = (await GET("/overview")).system.hostname; } catch { /* shown later */ }
  buildShell();
  route();
}
boot();
})();
