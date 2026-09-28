/* SprintMind single-page UI. No build step: served by FastAPI at "/", talks to the same-origin API. */
"use strict";

// ============================================================== icons & utils
const ICONS = {
  home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  board: '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="11" rx="1"/><rect x="17" y="4" width="4" height="7" rx="1"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/>',
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  split: '<rect x="3" y="4" width="8" height="16" rx="1"/><rect x="13" y="4" width="8" height="16" rx="1"/>',
  settings: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  send: '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/>',
  up: '<path d="M7 10v11H3V10zM7 10l4-8a3 3 0 0 1 3 3v4h6a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.6 21H7"/>',
  down: '<g transform="rotate(180 12 12)"><path d="M7 10v11H3V10zM7 10l4-8a3 3 0 0 1 3 3v4h6a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.6 21H7"/></g>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  refresh: '<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  alert: '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  sparkle: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
};
const icon = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nice = (s) => String(s ?? "").replace(/_/g, " ");
const store = {
  get(k, d) { try { const v = localStorage.getItem("sm." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("sm." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, { method = "GET", json, form } = {}) {
  const init = { method, headers: {} };
  if (json !== undefined) { init.body = JSON.stringify(json); init.headers["Content-Type"] = "application/json"; }
  if (form) init.body = form;
  let r;
  try { r = await fetch(path, init); } catch { throw new Error("Can't reach the SprintMind API. Is the server running?"); }
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    const d = body && body.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `${r.status} ${r.statusText}`);
  }
  return body;
}

function toast(msg, isErr = false) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.innerHTML = `${icon(isErr ? "alert" : "check")}<span>${esc(msg)}</span>`;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), isErr ? 6000 : 3500);
}

async function busy(btn, label, fn) {
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span>${esc(label)}`;
  try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = old; }
}

const fmtDate = (s) => s ? new Date(s).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) : "";
const fmtDateTime = (s) => s ? new Date(s).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
function relTime(s) {
  const d = (Date.now() - new Date(s)) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  const days = Math.floor(d / 86400);
  return days === 1 ? "yesterday" : `${days} days ago`;
}
function dueInfo(due) {
  if (!due) return null;
  const d = new Date(due + "T23:59:59");
  if (isNaN(d)) return { label: `due ${due}`, late: false };
  const days = Math.floor((d - Date.now()) / 86400000);
  if (days < 0) return { label: `overdue ${-days}d`, late: true };
  if (days === 0) return { label: "due today", late: false };
  if (days === 1) return { label: "due tomorrow", late: false };
  return { label: `due ${fmtDate(due + "T12:00:00")}`, late: false };
}
const dueHtml = (due) => { const d = dueInfo(due); return d ? `<span class="due small ${d.late ? "late" : "muted"}">${esc(d.label)}</span>` : ""; };

// ---------------------------------------------------------------- markdown
function citeNums(g) {
  const out = [];
  for (const part of g.split(/\s*,\s*/)) {
    const m = part.match(/^(\d+)\s*[–-]\s*(\d+)$/);
    if (m && +m[2] - +m[1] < 20) for (let n = +m[1]; n <= +m[2]; n++) out.push(n);
    else if (/^\d+$/.test(part)) out.push(+part);
  }
  return out;
}
function inline(s) {
  let t = esc(s);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>");
  t = t.replace(/\[(\d+(?:\s*[,–-]\s*\d+)*)\]/g, (_, g) =>
    citeNums(g).map((n) => `<button class="cite" data-n="${n}" title="Show memory ${n}">${n}</button>`).join(""));
  return t.replace(/&lt;br\s*\/?&gt;/g, "<br>");
}
const LIST_RE = /^\s*([-*•]|\d+[.)])\s+/;
function md(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  const out = []; let para = []; let i = 0;
  const flush = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      flush(); const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++; out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`); continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush(); const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      out.push(`<table><thead><tr>${cells(rows[0]).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${
        rows.slice(2).map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const hm = l.match(/^(#{1,4})\s+(.*)/);
    if (hm) { flush(); out.push(`<h4>${inline(hm[2])}</h4>`); i++; continue; }
    if (LIST_RE.test(l)) {
      flush(); const ordered = /^\s*\d/.test(l); const items = [];
      while (i < lines.length && LIST_RE.test(lines[i])) {
        items.push(lines[i].replace(LIST_RE, "")); i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !LIST_RE.test(lines[i])) { items[items.length - 1] += " " + lines[i].trim(); i++; }
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((x) => `<li>${inline(x)}</li>`).join("")}</${tag}>`); continue;
    }
    if (/^\s*(---|\*\*\*|___)\s*$/.test(l) || !l.trim()) { flush(); i++; continue; }
    para.push(l); i++;
  }
  flush();
  return out.join("");
}

// ----------------------------------------------------------- shared pieces
const AV_COLORS = ["#3056e8", "#1d8048", "#a86a0c", "#7045c9", "#c93b3b", "#0f7c86", "#b3417e", "#4b5563"];
function avatar(name, id) {
  if (!name) return `<span class="avatar" style="background:var(--line);color:var(--muted)">?</span>`;
  let h = 0; for (const c of id || name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const ini = name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  return `<span class="avatar" style="background:${AV_COLORS[h % AV_COLORS.length]}" title="${esc(name)}">${esc(ini)}</span>`;
}
const who = (name, id) => `<span class="who">${avatar(name, id)}${esc(name || "Unassigned")}</span>`;

const STATUS_TONE = { todo: "", in_progress: "blue", in_review: "violet", blocked: "red", done: "green", dropped: "" };
const STATUS_LABEL = { todo: "To do", in_progress: "In progress", in_review: "In review", blocked: "Blocked", done: "Done", dropped: "Dropped" };
const statusPill = (s) => s ? `<span class="pill ${STATUS_TONE[s] ?? ""}">${esc(STATUS_LABEL[s] || nice(s))}</span>` : "";
const STAGE_TONE = { sprint_backlog: "blue", product_backlog: "violet", impediment: "red", sprint_review: "green", retro: "amber", follow_up: "teal" };
const stagePill = (s) => s ? `<span class="pill ${STAGE_TONE[s] || ""}">${esc(nice(s))}</span>` : "";
const TYPE_TONE = { standup: "blue", meeting: "violet", sop: "green", task_update: "teal", incident: "red", retro: "amber", note: "" };
const typePill = (t) => `<span class="pill ${TYPE_TONE[t] || ""}">${esc(nice(t))}</span>`;

function memList(facts, showScore = false) {
  return `<ol class="memlist">${facts.map((f, i) => `<li data-n="${i + 1}"><span class="n">${i + 1}</span><div>
    <div class="txt" title="Click to expand">${esc(f.text)}</div>
    <div class="meta">${f.occurred_start ? `<span class="pill">${esc(fmtDate(f.occurred_start))}</span>` : ""}
      ${f.type ? `<span class="pill ${f.type === "observation" ? "violet" : f.type === "experience" ? "teal" : ""}">${esc(f.type)}</span>` : ""}
      ${showScore && f.score != null ? `<span class="pill blue">score ${Number(f.score).toFixed(2)}</span>` : ""}
      ${(f.tags || []).filter((t) => !t.startsWith("sprint:")).slice(0, 6).map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div>
  </div></li>`).join("")}</ol>`;
}
const memBlock = (facts, label) => facts && facts.length
  ? `<details class="mem"><summary>${icon("database")} ${esc(label || `${facts.length} memories recalled from Hindsight`)}</summary>${memList(facts)}</details>`
  : `<span class="small muted">${icon("database")} No memories matched</span>`;
const cites = (nums) => (nums || []).map((n) => `<button class="cite" data-n="${n}">${n}</button>`).join("");
const skel = (n = 4) => Array.from({ length: n }, (_, i) => `<div class="skel" style="width:${90 - (i % 3) * 18}%"></div>`).join("");
const loadingNote = (t) => `<div class="loading-note"><span class="spin"></span>${esc(t)}</div>`;
const emptyBox = (ic, title, text, action = "") => `<div class="empty">${icon(ic)}<b>${esc(title)}</b>${esc(text)}${action ? `<div class="mt">${action}</div>` : ""}</div>`;
const errorBox = (e) => `<div class="empty">${icon("alert")}<b>Something went wrong</b>${esc(e.message || e)}</div>`;

// Citation chips jump to the numbered memory inside the same answer.
document.addEventListener("click", (e) => {
  const c = e.target.closest(".cite");
  if (!c) return;
  const scope = c.closest("[data-cites]");
  if (!scope) return;
  const det = scope.querySelector("details.mem");
  if (det) det.open = true;
  const li = scope.querySelector(`.memlist li[data-n="${c.dataset.n}"]`);
  if (li) { li.classList.add("full", "flash"); li.scrollIntoView({ block: "nearest", behavior: "smooth" }); setTimeout(() => li.classList.remove("flash"), 1400); }
});
document.addEventListener("click", (e) => { const t = e.target.closest(".memlist .txt"); if (t) t.parentElement.parentElement.classList.toggle("full"); });

function modal(html) {
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => { wrap.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => e.key === "Escape" && close();
  wrap.addEventListener("mousedown", (e) => e.target === wrap && close());
  document.addEventListener("keydown", onKey);
  document.body.append(wrap);
  $$("[data-close]", wrap).forEach((b) => b.onclick = close);
  return { el: wrap, close };
}

// ==================================================================== state
const S = {
  team: { name: "", members: [], customers: [] },
  options: { meeting_types: [], scrum_stages: {}, action_statuses: [] },
  health: null,
  person: store.get("person", "priya"),
  cache: { briefing: {}, radar: null, chats: {}, manager: [], demo: {} },
  board: { owner: "all", stage: "", meeting: "", q: "", dropped: false },
  draft: { title: "", meeting_type: "customer_sync", customer: "", participants: "", when: "", tab: "rec", pasted: "" },
  prefill: null,
  events: [], lastEventId: 0, stats: null, inflight: new Map(), listeners: new Set(),
  counts: { actions: 0, meetings: 0 },
};
const me = () => S.team.members.find((m) => m.id === S.person) || null;
const member = (id) => S.team.members.find((m) => m.id === id);

// =================================================================== router
const NAV = [
  { group: "Workspace", items: [
    { id: "today", label: "Today", icon: "home", title: "Today", sub: () => `${S.team.name} · Sprint ${S.health?.sprint ?? ""}`, render: viewToday },
    { id: "ask", label: "Ask SprintMind", icon: "chat", title: "Ask SprintMind", sub: "Answers recalled from team memory, with citations", render: viewAsk },
    { id: "actions", label: "Action board", icon: "board", title: "Action board", sub: "Action events from meetings, moving through the sprint", render: viewActions, count: "actions" },
    { id: "meetings", label: "Meetings", icon: "mic", title: "Meetings", sub: "Record, transcribe and turn meetings into action events", render: viewMeetings, count: "meetings" },
  ] },
  { group: "Team", items: [
    { id: "radar", label: "Sprint radar", icon: "radar", title: "Sprint radar", sub: "Sprint health synthesised by Hindsight reflect()", render: viewRadar },
    { id: "sops", label: "SOP vault", icon: "book", title: "SOP vault", sub: "The team's standard operating procedures", render: viewSops },
  ] },
  { group: "Memory", items: [
    { id: "sources", label: "Sources", icon: "file", title: "Sources", sub: "Everything SprintMind has been told", render: viewSources },
    { id: "inspector", label: "Memory inspector", icon: "activity", title: "Memory inspector", sub: "Every retain, recall and reflect, live", render: viewInspector },
    { id: "demo", label: "Before / after", icon: "split", title: "Before / after demo", sub: "The same question with and without memory", render: viewDemo },
  ] },
  { group: "System", items: [
    { id: "settings", label: "Settings", icon: "settings", title: "Settings", sub: "Memory bank, dataset and appearance", render: viewSettings },
  ] },
];
const ROUTES = Object.fromEntries(NAV.flatMap((g) => g.items).map((r) => [r.id, r]));

function renderNav() {
  $("#nav").innerHTML = NAV.map((g) => `<h6>${esc(g.group)}</h6>${g.items.map((r) =>
    `<a href="#/${r.id}" data-id="${r.id}">${icon(r.icon)}<span>${esc(r.label)}</span>${r.count ? `<span class="count" data-count="${r.count}"></span>` : ""}</a>`).join("")}`).join("");
  updateCounts();
}
function updateCounts() {
  $$("[data-count]").forEach((el) => { const n = S.counts[el.dataset.count]; el.textContent = n || ""; el.hidden = !n; });
}
async function refreshCounts() {
  try {
    const [a, m] = await Promise.all([api("/actions"), api("/meetings")]);
    S.counts.actions = a.actions.filter((x) => !["done", "dropped"].includes(x.status)).length;
    S.counts.meetings = m.length;
    updateCounts();
  } catch { /* shown elsewhere */ }
}

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  return { id: parts[0] || "today", args: parts.slice(1) };
}
async function route() {
  const { id, args } = parseHash();
  const r = ROUTES[id] || ROUTES.today;
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.id === r.id));
  $("#pageTitle").textContent = r.title;
  $("#pageSub").textContent = typeof r.sub === "function" ? r.sub() : r.sub || "";
  document.title = `${r.title} · SprintMind`;
  toggleSidebar(false);
  const el = document.createElement("div");
  $("#view").replaceChildren(el);
  window.scrollTo(0, 0);
  try { await r.render(el, args); } catch (e) { el.innerHTML = errorBox(e); }
}
const rerender = () => route();

// ============================================================ live inspector
function handleEvent(e) {
  S.events.push(e);
  if (S.events.length > 600) S.events.splice(0, S.events.length - 600);
  if (e.status === "started") S.inflight.set(e.id, e);
  else S.inflight.delete(e.parent_id);
}
function updatePulse() {
  const now = Date.now();
  for (const [id, e] of S.inflight) if (now - new Date(e.ts) > 5 * 60000) S.inflight.delete(id);
  const live = [...S.inflight.values()].pop();
  $("#pulseBtn").classList.toggle("live", !!live);
  $("#pulseTxt").textContent = live ? `${live.op}${live.request?.label ? ": " + live.request.label : ""}…`.slice(0, 44) : "Memory idle";
}
let pollCount = 0;
async function pollEvents() {
  try {
    if (++pollCount % 10 === 0 && S.lastEventId) {  // detect server restart / cleared log
      const probe = await api("/inspector/events?limit=1");
      if (probe.last_id < S.lastEventId) { S.lastEventId = 0; S.events = []; S.inflight.clear(); }
    }
    const r = await api(`/inspector/events?after=${S.lastEventId}&limit=300`);
    r.events.forEach(handleEvent);
    S.lastEventId = r.last_id; S.stats = r.stats;
    if (r.events.length) S.listeners.forEach((fn) => fn(r.events));
    updatePulse();
  } catch { /* API down: health dot shows it */ }
  setTimeout(pollEvents, S.inflight.size ? 600 : 1500);
}
function evLabel(e) {
  const q = e.request || {};
  return q.label || q.query || q.model || q.action || q.document_id || (q.bank_id ? `bank ${q.bank_id}` : "");
}
function evRow(e) {
  const ts = new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const st = e.status === "ok" ? "✓" : e.status === "error" ? "✕" : "…";
  return `<div class="ev" data-id="${e.id}"><div class="line" title="Click for details">
    <span class="ts">${esc(ts)}</span><span class="op ${esc(e.op)}">${esc(e.op)}</span><span class="st ${esc(e.status)}">${st}</span>
    <span class="lbl">${esc(evLabel(e))}${e.error ? ` <span style="color:var(--red)">${esc(e.error)}</span>` : ""}</span>
    <span class="dur">${e.duration_ms != null ? e.duration_ms + " ms" : ""}</span></div></div>`;
}
document.addEventListener("click", (e) => {
  const line = e.target.closest(".ev > .line");
  if (!line) return;
  const box = line.parentElement;
  const open = $("pre", box);
  if (open) return open.remove();
  const ev = S.events.find((x) => x.id === +box.dataset.id);
  if (!ev) return;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify({ request: ev.request, response: ev.response, error: ev.error }, null, 2);
  box.append(pre);
});
function visibleEvents(op) {
  // Completed events plus anything still in flight; newest first.
  return S.events.filter((e) => (e.status !== "started" || S.inflight.has(e.id)) && (!op || e.op === op)).slice().reverse();
}
function renderDrawer() {
  if (!$("#drawer").classList.contains("open")) return;
  const evs = visibleEvents().slice(0, 80);
  $("#drawerBody").innerHTML = evs.length ? evs.map(evRow).join("")
    : emptyBox("activity", "No memory activity yet", "Ask a question, record a meeting or open the radar to see retain / recall / reflect here.");
}
S.listeners.add(renderDrawer);
function toggleDrawer(open) {
  $("#drawer").classList.toggle("open", open ?? !$("#drawer").classList.contains("open"));
  renderDrawer();
}
function toggleSidebar(open) {
  $("#sidebar").classList.toggle("open", !!open);
  $("#scrim").classList.toggle("on", !!open);
}

// ==================================================================== TODAY
function greeting() { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; }

async function viewToday(el) {
  const p = me();
  el.innerHTML = `
    <div class="hero"><div><h2>${greeting()}, ${esc(p ? p.name.split(" ")[0] : "team")}</h2>
      <p>${esc(new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }))} · Sprint ${esc(S.health?.sprint ?? "")} · ${esc(S.team.name)}</p></div></div>
    <div id="banner"></div>
    <div class="stats" id="stats">${Array(4).fill(`<div class="card stat">${skel(2)}</div>`).join("")}</div>
    <div class="tiles">
      <a class="card tile" href="#/ask"><span class="ic">${icon("chat")}</span><div><b>Ask SprintMind</b><span>${p ? "Your priorities, blockers and SOPs" : "Anything about the sprint"}</span></div></a>
      <a class="card tile" href="#/meetings/new"><span class="ic">${icon("mic")}</span><div><b>Record a meeting</b><span>Transcribe it into action events</span></div></a>
      <a class="card tile" href="#/radar"><span class="ic">${icon("radar")}</span><div><b>Sprint radar</b><span>Health, blockers, workload</span></div></a>
      <a class="card tile" href="#/actions"><span class="ic">${icon("board")}</span><div><b>Action board</b><span>Move work through the sprint</span></div></a>
    </div>
    <div class="cols">
      <section class="card"><div class="hd"><h2 class="grow">${p ? "My open actions" : "Blocked across the team"}</h2><a class="small" href="#/actions">Board →</a></div><div id="mine">${skel(4)}</div></section>
      <section class="card"><div class="hd"><h2 class="grow">Recent meetings</h2><a class="small" href="#/meetings">All →</a></div><div id="recent">${skel(4)}</div></section>
    </div>`;

  let actions, meetings, sources;
  try {
    [{ actions }, meetings, sources] = await Promise.all([api("/actions"), api("/meetings"), api("/sources")]);
  } catch (e) { $("#stats", el).innerHTML = ""; $("#mine", el).innerHTML = errorBox(e); $("#recent", el).innerHTML = ""; return; }

  if (!sources.length) {
    $("#banner", el).innerHTML = `<div class="banner">${icon("sparkle")}<div class="grow"><b>Memory is empty</b>
      <span class="small">Load the Sprint 14 dataset (SOPs, planning, standups, an incident) so SprintMind has history to recall, or record your first meeting.</span></div>
      <button class="btn primary" id="seedBtn">${icon("database")} Load Sprint 14 dataset</button><a class="btn" href="#/meetings/new">${icon("mic")} Record a meeting</a></div>`;
    $("#seedBtn", el).onclick = (e) => seedDataset(e.currentTarget);
  }

  const open = actions.filter((a) => !["done", "dropped"].includes(a.status));
  const blocked = open.filter((a) => a.status === "blocked");
  const mine = p ? open.filter((a) => a.owner_id === p.id) : blocked;
  const weekAgo = Date.now() - 7 * 86400000;
  const stat = (n, l, cls = "") => `<div class="card stat ${cls}"><div class="n">${n}</div><div class="l">${esc(l)}</div></div>`;
  $("#stats", el).innerHTML = [
    p ? stat(mine.length, "my open actions") : stat(open.length, "open actions"),
    stat(blocked.length, "blocked actions", blocked.length ? "red" : ""),
    stat(meetings.filter((m) => new Date(m.occurred_at) > weekAgo).length, "meetings this week"),
    stat(sources.length, "sources in memory"),
  ].join("");

  const byDue = (a, b) => (a.due || "9999").localeCompare(b.due || "9999");
  $("#mine", el).innerHTML = mine.length ? `<ul class="list">${mine.sort(byDue).slice(0, 8).map((a) => `
    <li class="click" data-action="${esc(a.id)}"><div class="grow"><div class="t">${a.ticket ? `<span class="ticket">${esc(a.ticket)}</span> ` : ""}${esc(a.title)}</div>
      <div class="s">${esc(a.meeting_title)}${p ? "" : ` · ${esc(a.owner || "unassigned")}`}</div></div>${dueHtml(a.due)}${statusPill(a.status)}</li>`).join("")}</ul>`
    : emptyBox("check", p ? "Nothing open for you" : "Nothing blocked", p ? "No action events are assigned to you yet." : "No blocked action events right now.");
  $$("[data-action]", el).forEach((li) => li.onclick = () => openAction(actions.find((a) => a.id === li.dataset.action), rerender));

  $("#recent", el).innerHTML = meetings.length ? `<ul class="list">${meetings.slice(0, 6).map((m) => `
    <li class="click" data-m="${esc(m.id)}"><div class="grow"><div class="t">${esc(m.title)}</div>
      <div class="s">${esc(fmtDateTime(m.occurred_at))} · ${m.actions.length} actions · ${m.actions.filter((a) => a.status === "done").length} done</div></div>
      ${typePill(m.source_type)}</li>`).join("")}</ul>`
    : emptyBox("mic", "No meetings yet", "Record, upload or paste one to get action events.", `<a class="btn primary" href="#/meetings/new">${icon("mic")} New meeting</a>`);
  $$("[data-m]", el).forEach((li) => li.onclick = () => location.hash = `#/meetings/${encodeURIComponent(li.dataset.m)}`);
}

async function seedDataset(btn) {
  if (!confirm("Load the Sprint 14 dataset into memory? This makes about 12 LLM extractions and 25 retain calls and takes a few minutes.")) return;
  toggleDrawer(true);
  await busy(btn, "Loading… watch the activity panel", async () => {
    try {
      const r = await api("/admin/seed", { method: "POST" });
      toast(`Loaded ${r.ingested} documents into memory`);
      S.cache.briefing = {}; S.cache.radar = null;
      rerender();
    } catch (e) { toast(e.message, true); }
  });
}

// ====================================================================== ASK
const SUGGEST_PERSON = ["What's my highest priority task today, and which SOP should I follow?", "What am I blocked on, and who can unblock it?",
  "What did Acme ask for in the latest sync?", "What changed on my tickets this week?"];
const SUGGEST_TEAM = ["What were the key deliverables agreed with Acme Corp in yesterday's Teams sync, and who owns each one?",
  "Who is working on SSO?", "Which customer commitments are due this week?", "What is blocked right now?"];

async function viewAsk(el) {
  const p = me();
  el.innerHTML = `<div class="cols side">
    <section class="card" id="briefCard"></section>
    <section class="card chat">
      <div class="hd">${icon("chat")}<h2 class="grow">Ask ${p ? `as ${esc(p.name)}` : "about the team"}</h2>
        <button class="btn sm ghost" id="clearChat">Clear</button></div>
      <div class="thread" id="thread"></div>
      <div class="composer">
        <div class="suggest" id="suggest"></div>
        <form class="box" id="askForm"><textarea id="q" rows="1" placeholder="Ask about tasks, blockers, customer commitments, SOPs…" aria-label="Question"></textarea>
          <button class="btn primary" id="sendBtn" aria-label="Send">${icon("send")}</button></form>
        <div class="small muted mt-s">Enter to send · Shift+Enter for a new line · answers cite the memories they came from</div>
      </div>
    </section></div>`;

  const key = S.person || "team";
  const thread = S.cache.chats[key] ||= [];
  const renderThread = () => {
    const t = $("#thread", el);
    t.innerHTML = thread.length ? thread.map(msgHtml).join("")
      : emptyBox("sparkle", "Ask anything about the sprint", "SprintMind recalls the relevant memories from Hindsight first, then answers only from them.");
    wireFeedback(t, thread);
    t.scrollTop = t.scrollHeight;
    $("#suggest", el).hidden = thread.length > 0;
  };
  $("#suggest", el).innerHTML = (p ? SUGGEST_PERSON : SUGGEST_TEAM).map((s) => `<button type="button">${esc(s)}</button>`).join("");
  $$("#suggest button", el).forEach((b) => b.onclick = () => send(b.textContent));
  $("#clearChat", el).onclick = () => { thread.length = 0; renderThread(); };

  const q = $("#q", el);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#askForm", el).requestSubmit(); } });
  q.addEventListener("input", () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 160) + "px"; });
  $("#askForm", el).onsubmit = (e) => { e.preventDefault(); if (q.value.trim()) send(q.value.trim()); };

  async function send(question) {
    q.value = ""; q.style.height = "";
    thread.push({ role: "user", text: question }, { role: "bot", pending: true });
    renderThread();
    $("#sendBtn", el).disabled = true;
    try {
      const res = await api("/employee/ask", { method: "POST", json: { question, person_id: S.person || null } });
      thread[thread.length - 1] = { role: "bot", res, question };
    } catch (e) {
      thread[thread.length - 1] = { role: "bot", error: e.message };
    }
    renderThread();
    $("#sendBtn", el).disabled = false;
  }
  renderThread();
  if (S.prefill) { q.value = S.prefill; S.prefill = null; q.focus(); }
  renderBriefingCard($("#briefCard", el));
}

function msgHtml(m, i) {
  if (m.role === "user") return `<div class="msg user">${esc(m.text)}</div>`;
  if (m.pending) return `<div class="msg bot"><div class="loading-note"><span class="spin"></span>Recalling memories and writing an answer…</div></div>`;
  if (m.error) return `<div class="msg bot err">${icon("alert")} ${esc(m.error)}</div>`;
  const r = m.res;
  return `<div class="msg bot" data-cites data-i="${i}"><div class="md">${md(r.answer)}</div>
    <div class="foot">${memBlock(r.memories)}<span class="grow"></span>
      <span class="small muted">${(r.latency_ms / 1000).toFixed(1)}s</span>
      <span class="fb">${m.feedback ? `<span class="small muted">${esc(m.feedback)}</span>` : `
        <button class="btn sm ghost icon" data-fb="up" title="Helpful" aria-label="Helpful">${icon("up")}</button>
        <button class="btn sm ghost icon" data-fb="down" title="Not helpful" aria-label="Not helpful">${icon("down")}</button>
        <button class="btn sm ghost" data-fb="fix">${icon("edit")} Correct</button>`}</span>
    </div></div>`;
}

function wireFeedback(root, thread) {
  $$(".msg.bot[data-i]", root).forEach((box) => {
    const m = thread[+box.dataset.i];
    const send = async (helpful, correction) => {
      try {
        await api("/feedback", { method: "POST", json: { interaction_id: m.res.interaction_id, helpful, correction: correction || null, person_id: S.person || null } });
        m.feedback = correction ? "Correction retained. Future answers will use it." : helpful ? "Thanks, noted." : "Noted as not helpful.";
        toast(correction ? "Correction retained in Hindsight" : "Feedback retained");
      } catch (e) { toast(e.message, true); }
      $(".fb", box).innerHTML = `<span class="small muted">${esc(m.feedback || "")}</span>`;
    };
    $$("[data-fb]", box).forEach((b) => b.onclick = () => {
      if (b.dataset.fb !== "fix") return send(b.dataset.fb === "up");
      $(".fb", box).innerHTML = `<form class="row" style="width:100%"><input class="grow" placeholder="What's the correct information?" required>
        <button class="btn sm primary">Save</button></form>`;
      const f = $(".fb form", box);
      $(".fb", box).style.width = "100%";
      $("input", f).focus();
      f.onsubmit = (e) => { e.preventDefault(); send(false, $("input", f).value.trim()); };
    });
  });
}

function renderBriefingCard(card, force = false) {
  const p = me();
  if (!p) {
    card.innerHTML = `<div class="hd">${icon("users")}<h2>Personal briefing</h2></div><div class="bd">
      ${emptyBox("users", "Viewing as the whole team", "Pick a person in “Viewing as” (top right) to get their personal “what's next” briefing.")}</div>`;
    return;
  }
  const cached = S.cache.briefing[p.id];
  card.innerHTML = `<div class="hd">${icon("sparkle")}<h2 class="grow">What's next for ${esc(p.name.split(" ")[0])}</h2>
    <button class="btn sm ghost" id="briefRefresh" title="Regenerate">${icon("refresh")}</button></div><div class="bd" id="briefBody"></div>`;
  $("#briefRefresh", card).onclick = () => renderBriefingCard(card, true);
  const body = $("#briefBody", card);
  if (cached && !force) return renderBriefing(body, cached);
  body.innerHTML = loadingNote("Recalling your tasks, blockers, commitments and SOPs…") + skel(8);
  api(`/employee/${encodeURIComponent(p.id)}/briefing`).then((r) => {
    S.cache.briefing[p.id] = { ...r, at: Date.now() };
    if (body.isConnected && S.person === p.id) renderBriefing(body, S.cache.briefing[p.id]);
  }).catch((e) => { if (body.isConnected) body.innerHTML = errorBox(e); });
}

function renderBriefing(body, r) {
  const b = r.briefing || {};
  const tp = b.top_priority || {};
  const sec = (title, items, fn) => items && items.length ? `<div class="bsec"><h4>${esc(title)}</h4><ul>${items.map(fn).join("")}</ul></div>` : "";
  body.dataset.cites = "";
  body.innerHTML = `
    <div class="headline">${esc(b.headline || "No briefing available")}</div>
    ${tp.task ? `<div class="priority"><div class="k">Top priority</div><div><strong>${tp.ticket ? `<span class="ticket">${esc(tp.ticket)}</span> ` : ""}${esc(tp.task)}</strong> ${cites(tp.sources)}</div>
      ${tp.why ? `<div class="small muted">${esc(tp.why)}</div>` : ""}</div>` : ""}
    ${sec("My tasks", b.my_tasks, (t) => `<li>${statusPill(t.status)}<span class="grow">${t.ticket ? `<span class="ticket">${esc(t.ticket)}</span> ` : ""}${esc(t.title)} ${cites(t.sources)}</span>${dueHtml(t.due)}</li>`)}
    ${sec("Blockers", b.blockers, (t) => `<li><span class="pill red">blocked</span><span class="grow">${esc(t.task)}. Waiting on ${esc(t.waiting_on)}${t.who_can_unblock ? `, ${esc(t.who_can_unblock)} can unblock` : ""} ${cites(t.sources)}</span></li>`)}
    ${sec("Customer commitments", b.customer_commitments, (t) => `<li><span class="pill violet">${esc(t.customer || "customer")}</span><span class="grow">${esc(t.commitment)} ${cites(t.sources)}</span>${dueHtml(t.due)}</li>`)}
    ${sec("SOPs to follow", b.sops_to_follow, (t) => `<li>${icon("book")}<span class="grow"><strong>${esc(t.sop)}</strong>${t.why ? `: ${esc(t.why)}` : ""} ${cites(t.sources)}</span></li>`)}
    <div class="mt">${memBlock(r.memories)}</div>
    <div class="small muted mt-s">Generated ${esc(relTime(r.at))}</div>`;
}

// ============================================================== ACTION BOARD
const LANES = ["todo", "in_progress", "blocked", "done", "dropped"];
const LANE_COLOR = { todo: "var(--muted)", in_progress: "var(--accent)", blocked: "var(--red)", done: "var(--green)", dropped: "var(--line)" };

async function viewActions(el) {
  el.innerHTML = `<div class="filters" id="filters"></div><div id="boardWrap">${skel(6)}</div>`;
  let data, meetings;
  try { [data, meetings] = await Promise.all([api("/actions"), api("/meetings")]); }
  catch (e) { $("#boardWrap", el).innerHTML = errorBox(e); return; }
  const all = data.actions;
  const f = S.board;
  if (f.owner === "me" && !S.person) f.owner = "all";

  $("#filters", el).innerHTML = `
    <div class="tabs" role="tablist">
      <button data-owner="all" class="${f.owner === "all" ? "on" : ""}">Everyone</button>
      ${S.person ? `<button data-owner="me" class="${f.owner === "me" ? "on" : ""}">Mine</button>` : ""}
    </div>
    <select id="fOwner" aria-label="Owner"><option value="">Any owner</option>${S.team.members.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("")}<option value="__none">Unassigned</option></select>
    <select id="fStage" aria-label="Scrum stage"><option value="">Any stage</option>${Object.keys(S.options.scrum_stages).map((s) => `<option value="${s}">${esc(nice(s))}</option>`).join("")}</select>
    <select id="fMeeting" aria-label="Meeting"><option value="">Any meeting</option>${meetings.map((m) => `<option value="${esc(m.id)}">${esc(m.title)} · ${esc(fmtDate(m.occurred_at))}</option>`).join("")}</select>
    <input id="fQ" type="search" placeholder="Search actions" aria-label="Search" value="${esc(f.q)}">
    <label class="check" style="margin:0"><input type="checkbox" id="fDropped" ${f.dropped ? "checked" : ""}> Show dropped</label>`;
  if (!["all", "me"].includes(f.owner)) $("#fOwner", el).value = f.owner;
  $("#fStage", el).value = f.stage; $("#fMeeting", el).value = f.meeting;
  $$("[data-owner]", el).forEach((b) => b.onclick = () => { f.owner = b.dataset.owner; viewActions(el); });
  $("#fOwner", el).onchange = (e) => { f.owner = e.target.value || "all"; viewActions(el); };
  $("#fStage", el).onchange = (e) => { f.stage = e.target.value; draw(); };
  $("#fMeeting", el).onchange = (e) => { f.meeting = e.target.value; draw(); };
  $("#fQ", el).oninput = (e) => { f.q = e.target.value; draw(); };
  $("#fDropped", el).onchange = (e) => { f.dropped = e.target.checked; draw(); };

  function filtered() {
    const q = f.q.trim().toLowerCase();
    return all.filter((a) =>
      (f.owner === "all" || (f.owner === "me" ? a.owner_id === S.person : f.owner === "__none" ? !a.owner_id : a.owner_id === f.owner)) &&
      (!f.stage || a.scrum_stage === f.stage) && (!f.meeting || a.meeting_id === f.meeting) &&
      (!q || `${a.title} ${a.detail || ""} ${a.ticket || ""} ${a.owner || ""}`.toLowerCase().includes(q)));
  }
  function draw() {
    const wrap = $("#boardWrap", el);
    if (!all.length) {
      wrap.innerHTML = `<div class="card">${emptyBox("board", "No action events yet", "Record, upload or paste a meeting and SprintMind extracts its action events into this board.",
        `<a class="btn primary" href="#/meetings/new">${icon("mic")} Record a meeting</a>`)}</div>`;
      return;
    }
    const lanes = LANES.filter((l) => l !== "dropped" || f.dropped);
    const items = filtered();
    const order = (a, b) => (dueInfo(b.due)?.late ? 1 : 0) - (dueInfo(a.due)?.late ? 1 : 0) || (a.due || "9999").localeCompare(b.due || "9999");
    wrap.innerHTML = `<div class="board" style="--cols:${lanes.length}">${lanes.map((l) => {
      const list = items.filter((a) => a.status === l).sort(order);
      return `<div class="lane" data-lane="${l}"><h3><span class="bar" style="background:${LANE_COLOR[l]}"></span>${esc(STATUS_LABEL[l])} <span class="n">${list.length}</span></h3>
        ${list.map(cardHtml).join("") || `<div class="small muted" style="padding:6px 4px">Drop actions here</div>`}</div>`;
    }).join("")}</div>
    <p class="small muted">Drag a card to change its status, or click it to edit. Every change is retained in Hindsight, so the radar and briefings pick it up.</p>`;
    wireBoard(wrap);
  }
  const cardHtml = (a) => `<div class="acard" draggable="true" data-id="${esc(a.id)}" tabindex="0">
    <div class="t">${a.ticket ? `<span class="ticket">${esc(a.ticket)}</span> ` : ""}${esc(a.title)}</div>
    <div class="m">${avatar(a.owner, a.owner_id)}${stagePill(a.scrum_stage)}${a.customer ? `<span class="pill">${esc(a.customer)}</span>` : ""}<span class="grow"></span>${dueHtml(a.due)}</div>
    <div class="from">${icon("mic")} ${esc(a.meeting_title)}</div></div>`;

  function wireBoard(wrap) {
    $$(".acard", wrap).forEach((c) => {
      const a = all.find((x) => x.id === c.dataset.id);
      c.ondragstart = (e) => { e.dataTransfer.setData("text/plain", a.id); e.dataTransfer.effectAllowed = "move"; c.classList.add("dragging"); };
      c.ondragend = () => c.classList.remove("dragging");
      c.onclick = () => openAction(a, () => viewActions(el));
      c.onkeydown = (e) => e.key === "Enter" && c.click();
    });
    $$(".lane", wrap).forEach((lane) => {
      lane.ondragover = (e) => { e.preventDefault(); lane.classList.add("over"); };
      lane.ondragleave = () => lane.classList.remove("over");
      lane.ondrop = async (e) => {
        e.preventDefault(); lane.classList.remove("over");
        const a = all.find((x) => x.id === e.dataTransfer.getData("text/plain"));
        const status = lane.dataset.lane;
        if (!a || a.status === status) return;
        const prev = a.status;
        a.status = status; draw();
        try {
          await api(`/actions/${encodeURIComponent(a.id)}`, { method: "PATCH", json: { status, person_id: S.person || null } });
          toast(`“${a.title.slice(0, 40)}” → ${STATUS_LABEL[status]}. Retained in memory.`);
          refreshCounts();
        } catch (err) { a.status = prev; draw(); toast(err.message, true); }
      };
    });
  }
  draw();
}

function openAction(a, onSaved) {
  if (!a) return;
  const m = modal(`
    <div class="hd"><div class="grow"><div class="chips mb" style="margin-bottom:8px">${stagePill(a.scrum_stage)}<span class="pill">${esc(nice(a.kind))}</span>
      ${a.customer ? `<span class="pill violet">${esc(a.customer)}</span>` : ""}${a.priority ? `<span class="pill amber">${esc(a.priority)}</span>` : ""}</div>
      <h3>${a.ticket ? `<span class="ticket">${esc(a.ticket)}</span> ` : ""}${esc(a.title)}</h3>
      ${a.detail ? `<p class="muted small" style="margin:6px 0 0">${esc(a.detail)}</p>` : ""}
      ${a.blocked_on ? `<p class="small" style="margin:6px 0 0;color:var(--red)">Waiting on: ${esc(a.blocked_on)}</p>` : ""}</div>
      <button class="btn icon ghost" data-close aria-label="Close">${icon("x")}</button></div>
    <form class="bd">
      <div class="grid2">
        <div><label class="f">Status</label><select name="status">${S.options.action_statuses.map((s) => `<option value="${s}" ${s === a.status ? "selected" : ""}>${esc(STATUS_LABEL[s] || s)}</option>`).join("")}</select></div>
        <div><label class="f">Owner</label><select name="owner"><option value="">${a.owner && !a.owner_id ? esc(a.owner) + " (not on roster)" : "Unassigned"}</option>
          ${S.team.members.map((p) => `<option value="${p.id}" ${p.id === a.owner_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>
      </div>
      <label class="f">Due date</label><input type="date" name="due" value="${esc(a.due || "")}">
      <label class="f">Update note <span class="muted" style="font-weight:400">(optional, retained with the change)</span></label>
      <textarea name="note" rows="2" placeholder="e.g. Sandbox live, Acme confirmed they can test"></textarea>
      <div class="row mt"><a class="small" href="#/meetings/${encodeURIComponent(a.meeting_id)}" data-close>${icon("mic")} ${esc(a.meeting_title || "Source meeting")}</a>
        <span class="grow"></span><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div>
      <h4 class="mt small muted" style="text-transform:uppercase;letter-spacing:.05em">History</h4>
      <ul class="timeline mt-s">${(a.history || []).slice().reverse().map((h) => `<li>${statusPill(h.status)} <span class="muted">${esc(relTime(h.at))} · ${esc(h.by || "")}</span>
        ${h.note ? `<div>${esc(h.note)}</div>` : ""}</li>`).join("")}</ul>
    </form>`);
  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", m.close));
  const form = $("form", m.el);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const patch = { person_id: S.person || null };
    if (fd.get("status") !== a.status) patch.status = fd.get("status");
    if (fd.get("owner") && fd.get("owner") !== a.owner_id) patch.owner = fd.get("owner");
    if (fd.get("due") && fd.get("due") !== a.due) patch.due = fd.get("due");
    if (fd.get("note").trim()) patch.note = fd.get("note").trim();
    if (Object.keys(patch).length === 1) return m.close();
    await busy($("button.primary", form), "Saving", async () => {
      try {
        await api(`/actions/${encodeURIComponent(a.id)}`, { method: "PATCH", json: patch });
        toast("Saved and retained in Hindsight");
        m.close(); refreshCounts(); onSaved && onSaved();
      } catch (err) { toast(err.message, true); }
    });
  };
}

// ================================================================== MEETINGS
async function viewMeetings(el, args) {
  el.innerHTML = `<div class="cols side">
    <section class="card mlist"><div class="hd"><h2 class="grow">Meetings</h2><a class="btn sm primary" href="#/meetings/new">${icon("plus")} New</a></div>
      <div style="padding:10px 12px 0"><input type="search" id="mSearch" placeholder="Search meetings" aria-label="Search meetings"></div>
      <div id="mList">${skel(5)}</div></section>
    <section id="mMain"></section></div>`;
  let meetings = [];
  try { meetings = await api("/meetings"); } catch (e) { $("#mList", el).innerHTML = errorBox(e); }
  const sel = args[0] || (meetings.length ? meetings[0].id : "new");
  const drawList = (q = "") => {
    const list = meetings.filter((m) => !q || m.title.toLowerCase().includes(q.toLowerCase()));
    $("#mList", el).innerHTML = list.length ? `<ul class="list mt-s">${list.map((m) => `
      <li class="click ${m.id === sel ? "active" : ""}" data-m="${esc(m.id)}"><div class="grow"><div class="t">${esc(m.title)}</div>
        <div class="s">${esc(fmtDateTime(m.occurred_at))} · ${m.actions.length} actions</div></div>${typePill(m.source_type)}</li>`).join("")}</ul>`
      : `<div class="empty small">${meetings.length ? "No matches" : "No meetings yet"}</div>`;
    $$("[data-m]", el).forEach((li) => li.onclick = () => location.hash = `#/meetings/${encodeURIComponent(li.dataset.m)}`);
  };
  drawList();
  $("#mSearch", el).oninput = (e) => drawList(e.target.value);

  const main = $("#mMain", el);
  if (sel === "new") return renderRecorder(main);
  main.innerHTML = `<div class="card pad">${skel(8)}</div>`;
  try { renderMeeting(main, await api(`/meetings/${encodeURIComponent(sel)}`)); }
  catch (e) { main.innerHTML = `<div class="card">${errorBox(e)}</div>`; }
}

function renderMeeting(main, m) {
  const byStage = {};
  for (const a of m.actions) (byStage[a.scrum_stage] ||= []).push(a);
  const t = m.transcription;
  const people = (m.people || []).map((id) => member(id)).filter(Boolean);
  main.innerHTML = `
    <div class="card"><div class="bd">
      <div class="row"><h2 class="grow" style="font-size:19px">${esc(m.title)}</h2>
        <button class="btn sm" id="askAbout">${icon("chat")} Ask about this meeting</button></div>
      <div class="row small muted mt-s">${typePill(m.source_type)}<span>${esc(fmtDateTime(m.occurred_at))}</span><span>· ${esc(nice(m.meeting_type))}</span>
        ${m.customer ? `<span>· ${esc(S.team.customers.find((c) => c.id === m.customer)?.name || m.customer)}</span>` : ""}${t ? `<span>· ${esc(t.engine)}${t.duration_s ? `, ${Math.max(1, Math.round(t.duration_s / 60))} min` : ""}</span>` : ""}
        <span>· ${m.facts_retained} facts retained in Hindsight</span></div>
      ${people.length ? `<div class="row mt-s">${people.map((p) => avatar(p.name, p.id)).join("")}</div>` : ""}
      ${m.extraction_error ? `<p class="small" style="color:var(--red)">${icon("alert")} Summarising failed; the transcript was still saved to memory. ${esc(m.extraction_error)}</p>` : ""}
      <p style="font-size:15px;margin:14px 0 0">${esc(m.summary)}</p>
    </div></div>
    ${m.decisions.length || m.open_questions.length ? `<div class="cols mt">
      <section class="card"><div class="hd">${icon("check")}<h3>Decisions</h3></div><div class="bd md">${m.decisions.length ? `<ul>${m.decisions.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : `<span class="muted small">None recorded</span>`}</div></section>
      <section class="card"><div class="hd">${icon("alert")}<h3>Open questions</h3></div><div class="bd md">${m.open_questions.length ? `<ul>${m.open_questions.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : `<span class="muted small">None</span>`}</div></section>
    </div>` : ""}
    <section class="card mt"><div class="hd">${icon("board")}<h3 class="grow">Action events (${m.actions.length})</h3><a class="small" href="#/actions">Open board →</a></div><div class="bd" id="acts">
      ${m.actions.length ? Object.keys(S.options.scrum_stages).filter((s) => byStage[s]).map((s) => `
        <div class="stage-group"><h4>${stagePill(s)} <small>${esc(S.options.scrum_stages[s])}</small></h4>
        ${byStage[s].map((a) => `<div class="arow" data-id="${esc(a.id)}">
          <div><div class="t">${a.ticket ? `<span class="ticket">${esc(a.ticket)}</span> ` : ""}${esc(a.title)}</div>
            ${a.detail ? `<div class="d">${esc(a.detail)}</div>` : ""}
            <div class="chips mt-s">${dueHtml(a.due)}${a.customer ? `<span class="pill">${esc(a.customer)}</span>` : ""}${a.owner && !a.owner_id ? `<span class="pill">owner: ${esc(a.owner)}</span>` : ""}
              ${a.blocked_on ? `<span class="pill red">waiting on ${esc(a.blocked_on)}</span>` : ""}</div></div>
          <select data-f="owner" aria-label="Owner"><option value="">Unassigned</option>${S.team.members.map((p) => `<option value="${p.id}" ${p.id === a.owner_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
          <select data-f="status" aria-label="Status">${S.options.action_statuses.map((s2) => `<option value="${s2}" ${s2 === a.status ? "selected" : ""}>${esc(STATUS_LABEL[s2])}</option>`).join("")}</select>
        </div>`).join("")}</div>`).join("") : emptyBox("board", "No action events", "Nothing actionable was found in this meeting.")}
    </div></section>
    <section class="card mt"><details><summary class="hd" style="cursor:pointer">${icon("file")}<h3 class="grow">Transcript</h3><span class="small muted">${m.transcript.split("\n").length} lines</span></summary>
      <div class="bd"><div class="transcript">${esc(m.transcript)}</div></div></details></section>`;
  $("#askAbout", main).onclick = () => { S.prefill = `What was agreed in "${m.title}" and who owns each action?`; location.hash = "#/ask"; };
  $$(".arow", main).forEach((row) => $$("select", row).forEach((s) => s.onchange = async () => {
    if (s.dataset.f === "owner" && !s.value) return;
    try {
      await api(`/actions/${encodeURIComponent(row.dataset.id)}`, { method: "PATCH", json: { [s.dataset.f]: s.value, person_id: S.person || null } });
      toast("Updated and retained in Hindsight");
      refreshCounts();
      renderMeeting(main, await api(`/meetings/${encodeURIComponent(m.id)}`));
    } catch (e) { toast(e.message, true); }
  }));
}

// ---------- recorder (state lives outside the view so navigating away doesn't lose a recording)
const REC = { recorder: null, chunks: [], streams: [], ctx: null, analyser: null, startedAt: 0, elapsed: 0, tick: null, blob: null, blobName: null, file: null };
window.addEventListener("beforeunload", (e) => { if (REC.recorder) { e.preventDefault(); e.returnValue = ""; } });

function renderRecorder(main) {
  const d = S.draft;
  if (!d.when) { const n = new Date(); n.setMinutes(n.getMinutes() - n.getTimezoneOffset()); d.when = n.toISOString().slice(0, 16); }
  main.innerHTML = `
    <section class="card"><div class="hd">${icon("mic")}<h2 class="grow">New meeting</h2>
      <span class="small muted" id="engine"></span></div>
    <div class="bd">
      <label class="f" for="rTitle">Title</label><input id="rTitle" placeholder="e.g. Acme Corp weekly sync" value="${esc(d.title)}">
      <div class="grid2">
        <div><label class="f" for="rType">Meeting type</label><select id="rType">${S.options.meeting_types.map((t) => `<option value="${t}">${esc(nice(t))}</option>`).join("")}</select></div>
        <div><label class="f" for="rCust">Customer</label><select id="rCust"><option value="">None</option>${S.team.customers.map((c) => `<option>${esc(c.name)}</option>`).join("")}</select></div>
      </div>
      <div class="grid2">
        <div><label class="f" for="rWhen">When</label><input id="rWhen" type="datetime-local" value="${esc(d.when)}"></div>
        <div><label class="f" for="rPeople">Participants <span class="muted" style="font-weight:400">(optional)</span></label><input id="rPeople" placeholder="Neha, Priya, Arjun" value="${esc(d.participants)}"></div>
      </div>

      <div class="row mt" style="margin-top:20px"><div class="tabs" id="rTabs">
        <button data-tab="rec">${icon("mic")} Record</button><button data-tab="file">${icon("upload")} Upload</button><button data-tab="paste">${icon("file")} Paste</button></div></div>

      <div data-pane="rec" class="mt">
        <div class="recorder">
          <button class="recbtn" id="recBtn" aria-label="Start recording"><span></span></button>
          <div class="grow"><div class="timer" id="recTimer">0:00:00</div>
            <div class="row small muted" id="recState">Click the red button to start recording</div></div>
          <button class="btn sm" id="pauseBtn" disabled>Pause</button>
        </div>
        <canvas class="meter" id="meter" width="800" height="88"></canvas>
        <label class="check"><input type="checkbox" id="tabAudio"> <span>Also capture a browser tab's audio (Teams / Meet / Zoom web). Chrome asks which tab to share: tick <b>Share tab audio</b>.</span></label>
        <audio id="preview" controls hidden style="width:100%;margin-top:12px"></audio>
      </div>
      <div data-pane="file" class="mt" hidden>
        <label class="drop" id="drop"><input type="file" id="file" accept="audio/*,video/mp4,video/webm" hidden>
          ${icon("upload")}<div id="dropTxt"><b>Drop a recording here</b> or click to choose<br><span class="small">mp3, m4a, wav, webm, ogg, mp4, up to 25 MB on Groq's free tier</span></div></label>
      </div>
      <div data-pane="paste" class="mt" hidden>
        <textarea id="pasted" class="mono" rows="10" placeholder="Paste a Teams / Zoom transcript or meeting notes">${esc(d.pasted)}</textarea>
        <button class="btn sm mt-s" id="demoBtn">${icon("sparkle")} Load the Acme demo transcript</button>
      </div>

      <div class="row mt" style="margin-top:20px"><button class="btn primary" id="processBtn" disabled>${icon("sparkle")} Transcribe &amp; extract actions</button>
        <span class="small muted" id="hint"></span></div>
      <div id="pipe" class="mt" hidden><div class="small muted" style="margin-bottom:6px">Live memory pipeline</div><div class="card" id="pipeLog" style="padding:6px;max-height:240px;overflow:auto"></div></div>
    </div></section>`;

  $("#rType", main).value = d.meeting_type;
  $("#rCust", main).value = d.customer;
  const t = S.health?.transcriber;
  $("#engine", main).innerHTML = t?.engine ? `<span class="dot ok"></span>${esc(t.engine)}` : `<span class="dot bad"></span>no transcriber: ${esc(t?.error || "off")}`;

  const bind = (id, key) => $(id, main).addEventListener("input", (e) => { d[key] = e.target.value; ready(); });
  bind("#rTitle", "title"); bind("#rType", "meeting_type"); bind("#rCust", "customer"); bind("#rWhen", "when"); bind("#rPeople", "participants"); bind("#pasted", "pasted");
  $("#rType", main).onchange = (e) => d.meeting_type = e.target.value;
  $("#rCust", main).onchange = (e) => d.customer = e.target.value;

  const setTab = (tab) => {
    d.tab = tab;
    $$("#rTabs button", main).forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    $$("[data-pane]", main).forEach((p) => p.hidden = p.dataset.pane !== tab);
    ready();
  };
  $$("#rTabs button", main).forEach((b) => b.onclick = () => setTab(b.dataset.tab));

  function ready() {
    const has = d.tab === "rec" ? !!REC.blob : d.tab === "file" ? !!REC.file : !!d.pasted.trim();
    const btn = $("#processBtn", main);
    if (!btn) return;
    btn.disabled = !has || !d.title.trim() || !!REC.recorder;
    $("#hint", main).textContent = !d.title.trim() ? "Add a title first" : REC.recorder ? "Stop the recording first"
      : !has ? (d.tab === "rec" ? "Record something first" : d.tab === "file" ? "Choose a file first" : "Paste a transcript first") : "";
  }
  window.__recReady = ready;

  // file
  const fileIn = $("#file", main), drop = $("#drop", main);
  const setFile = (f) => { REC.file = f; $("#dropTxt", main).innerHTML = f ? `<b>${esc(f.name)}</b><br><span class="small">${(f.size / 1048576).toFixed(1)} MB · click to change</span>` : $("#dropTxt", main).innerHTML; ready(); };
  fileIn.onchange = () => setFile(fileIn.files[0]);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); };
  if (REC.file) setFile(REC.file);

  // recording
  $("#recBtn", main).onclick = () => REC.recorder ? stopRecording() : startRecording();
  $("#pauseBtn", main).onclick = togglePause;
  syncRecUi();

  $("#demoBtn", main).onclick = async (e) => {
    e.preventDefault();
    try {
      const x = await api("/demo/transcript");
      Object.assign(d, { pasted: x.content, title: x.title, meeting_type: "customer_sync", customer: x.customer || "", participants: (x.participants || []).join(", ") });
      const w = new Date(x.occurred_at); w.setMinutes(w.getMinutes() - w.getTimezoneOffset()); d.when = w.toISOString().slice(0, 16);
      renderRecorder(main);
    } catch (err) { toast(err.message, true); }
  };

  $("#processBtn", main).onclick = async () => {
    const btn = $("#processBtn", main);
    const meta = { title: d.title.trim(), meeting_type: $("#rType", main).value, occurred_at: new Date(d.when).toISOString(),
                   customer: $("#rCust", main).value || null, participants: d.participants.split(",").map((s) => s.trim()).filter(Boolean) };
    const since = S.lastEventId;
    $("#pipe", main).hidden = false;
    const log = () => { const box = $("#pipeLog", main); if (box) box.innerHTML = visibleEvents().filter((e) => e.id > since).slice(0, 40).map(evRow).join("") || loadingNote("Starting…"); };
    S.listeners.add(log); log();
    await busy(btn, d.tab === "paste" ? "Summarising…" : "Transcribing & summarising…", async () => {
      try {
        let m;
        if (d.tab === "paste") m = await api("/meetings", { method: "POST", json: { ...meta, transcript: d.pasted } });
        else {
          const fd = new FormData();
          fd.append("audio", d.tab === "rec" ? new File([REC.blob], REC.blobName, { type: REC.blob.type }) : REC.file);
          for (const [k, v] of Object.entries(meta)) if (v && v.length !== 0) fd.append(k, Array.isArray(v) ? v.join(", ") : v);
          m = await api("/meetings/record", { method: "POST", form: fd });
        }
        toast(`Saved “${m.title}”: ${m.actions.length} action events, ${m.facts_retained} facts retained`);
        Object.assign(d, { title: "", participants: "", pasted: "", when: "" });
        REC.blob = null; REC.file = null;
        S.cache.briefing = {}; S.cache.radar = null;
        refreshCounts();
        location.hash = `#/meetings/${encodeURIComponent(m.id)}`;
      } catch (e) { toast(e.message, true); }
    });
    setTimeout(() => S.listeners.delete(log), 3000);
  };
  setTab(d.tab);
}

function syncRecUi() {
  const btn = $("#recBtn"); if (!btn) return;
  const live = !!REC.recorder;
  btn.classList.toggle("live", live);
  btn.setAttribute("aria-label", live ? "Stop recording" : "Start recording");
  $("#pauseBtn").disabled = !live;
  $("#pauseBtn").textContent = REC.recorder?.state === "paused" ? "Resume" : "Pause";
  $("#recState").textContent = live ? (REC.recorder.state === "paused" ? "Paused" : "Recording… click the square to stop")
    : REC.blob ? "Recording ready. Play it back below, or record again." : "Click the red button to start recording";
  const pv = $("#preview");
  if (pv) { pv.hidden = !REC.blob || live; if (REC.blob && !live && !pv.src) pv.src = URL.createObjectURL(REC.blob); }
  updateTimer();
  window.__recReady && window.__recReady();
}
function pickMime() {
  for (const m of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"])
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
  return "";
}
async function startRecording() {
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser can't record audio here. Use Chrome/Edge/Safari on http://localhost.");
    const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    REC.streams = [mic];
    REC.ctx = new AudioContext();
    const dest = REC.ctx.createMediaStreamDestination();
    REC.analyser = REC.ctx.createAnalyser(); REC.analyser.fftSize = 512;
    const add = (s) => { const src = REC.ctx.createMediaStreamSource(s); src.connect(dest); src.connect(REC.analyser); };
    add(mic);
    if ($("#tabAudio")?.checked) {
      const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      if (!display.getAudioTracks().length) throw new Error("The shared tab has no audio. Share a tab and tick “Share tab audio”.");
      REC.streams.push(display);
      add(new MediaStream(display.getAudioTracks()));
    }
    const mimeType = pickMime();
    REC.recorder = new MediaRecorder(dest.stream, { ...(mimeType ? { mimeType } : {}), audioBitsPerSecond: 32000 }); // ≈14 MB/hour
    REC.chunks = [];
    REC.recorder.ondataavailable = (e) => e.data.size && REC.chunks.push(e.data);
    REC.recorder.onstop = finishRecording;
    REC.recorder.start(1000);
    REC.startedAt = Date.now(); REC.elapsed = 0; REC.blob = null;
    const pv = $("#preview"); if (pv) pv.removeAttribute("src");
    REC.tick = setInterval(updateTimer, 250);
    drawMeter();
    syncRecUi();
  } catch (e) { cleanupStreams(); REC.recorder = null; syncRecUi(); toast(e.message || String(e), true); }
}
function stopRecording() { if (REC.recorder && REC.recorder.state !== "inactive") REC.recorder.stop(); }
function togglePause() {
  const r = REC.recorder; if (!r) return;
  if (r.state === "recording") { r.pause(); REC.elapsed += Date.now() - REC.startedAt; }
  else { r.resume(); REC.startedAt = Date.now(); }
  syncRecUi();
}
function updateTimer() {
  const el = $("#recTimer"); if (!el) return;
  const ms = REC.elapsed + (REC.recorder?.state === "recording" ? Date.now() - REC.startedAt : 0);
  const s = Math.floor(ms / 1000);
  el.textContent = `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
function finishRecording() {
  const type = REC.recorder.mimeType || "audio/webm";
  if (REC.recorder.state !== "paused") REC.elapsed += Date.now() - REC.startedAt;
  REC.blob = new Blob(REC.chunks, { type });
  REC.blobName = "meeting." + (type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm");
  clearInterval(REC.tick); cleanupStreams(); REC.recorder = null;
  syncRecUi();
}
function cleanupStreams() {
  REC.streams.forEach((s) => s.getTracks().forEach((t) => t.stop())); REC.streams = [];
  if (REC.ctx) { REC.ctx.close(); REC.ctx = null; }
}
function drawMeter() {
  const data = new Uint8Array(REC.analyser.frequencyBinCount);
  (function frame() {
    const c = $("#meter");
    if (!REC.recorder) { if (c) c.getContext("2d").clearRect(0, 0, c.width, c.height); return; }
    if (c) {
      const g = c.getContext("2d");
      g.clearRect(0, 0, c.width, c.height);
      REC.analyser.getByteFrequencyData(data);
      g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent");
      const bars = 72, w = c.width / bars;
      for (let i = 0; i < bars; i++) {
        const v = data[Math.floor((i * data.length) / bars / 2)] / 255;
        const hgt = Math.max(3, v * c.height);
        g.fillRect(i * w + 1.5, (c.height - hgt) / 2, w - 3, hgt);
      }
    }
    requestAnimationFrame(frame);
  })();
}

// ===================================================================== RADAR
async function viewRadar(el) {
  el.innerHTML = `<div class="row mb"><p class="muted grow" style="margin:0">Hindsight <code>reflect()</code> reasons over every standup, meeting and ticket update, then returns a structured report. Nobody writes a status update.</p>
      <span class="small muted" id="ranAt"></span><button class="btn" id="runRadar">${icon("refresh")} Refresh</button></div>
    <div id="radar"></div>
    <section class="card mt" data-cites id="mgr"><div class="hd">${icon("chat")}<h2 class="grow">Ask about the team</h2><span class="small muted">answered with reflect()</span></div>
      <div class="bd"><form class="row" id="mgrForm"><input class="grow" id="mgrQ" placeholder="e.g. Who is overloaded, and what could move to Sprint 15?" aria-label="Manager question">
        <button class="btn primary">${icon("send")} Ask</button></form><div id="mgrAnswers"></div></div></section>`;

  const box = $("#radar", el);
  const run = async () => {
    box.innerHTML = `<div class="card pad">${loadingNote("Hindsight is reflecting over the sprint's memory… this can take 10 to 30 seconds")}${skel(10)}</div>`;
    try { S.cache.radar = { ...(await api("/manager/radar")), at: Date.now() }; }
    catch (e) { box.innerHTML = `<div class="card">${errorBox(e)}</div>`; return; }
    if (box.isConnected) renderRadar(box, S.cache.radar, $("#ranAt", el));
  };
  $("#runRadar", el).onclick = (e) => busy(e.currentTarget, "Reflecting", run);
  if (S.cache.radar) renderRadar(box, S.cache.radar, $("#ranAt", el)); else busy($("#runRadar", el), "Reflecting", run);

  const answers = $("#mgrAnswers", el);
  const drawAnswers = () => {
    answers.innerHTML = S.cache.manager.slice().reverse().map((a) => `<div class="mt" data-cites style="border-top:1px solid var(--line-2);padding-top:14px">
      <div class="small muted" style="margin-bottom:6px"><strong>Q:</strong> ${esc(a.q)}</div>
      ${a.pending ? loadingNote("Reflecting…") : a.error ? `<div style="color:var(--red)">${esc(a.error)}</div>` : `<div class="md">${md(a.res.answer)}</div>
      <div class="mt-s">${memBlock(a.res.based_on, `Based on ${a.res.based_on.length} memories`)}</div>`}</div>`).join("");
  };
  drawAnswers();
  $("#mgrForm", el).onsubmit = async (e) => {
    e.preventDefault();
    const q = $("#mgrQ", el).value.trim(); if (!q) return;
    $("#mgrQ", el).value = "";
    const item = { q, pending: true }; S.cache.manager.push(item); drawAnswers();
    try { item.res = await api("/manager/ask", { method: "POST", json: { question: q } }); } catch (err) { item.error = err.message; }
    item.pending = false; if (answers.isConnected) drawAnswers();
  };
}

function renderRadar(box, r, ranAt) {
  if (ranAt) ranAt.textContent = `Updated ${relTime(r.at)} · ${(r.latency_ms / 1000).toFixed(1)}s`;
  const rep = r.report;
  if (!rep) {
    box.innerHTML = `<div class="card" data-cites><div class="bd md">${md(r.narrative || "No report")}</div><div class="bd">${memBlock(r.based_on, `Based on ${r.based_on.length} memories`)}</div></div>`;
    return;
  }
  const H = { on_track: "On track", at_risk: "At risk", off_track: "Off track" };
  const col = (title, tone, items, fn) => `<section class="card"><div class="hd"><span class="pill ${tone}">${items.length}</span><h3>${esc(title)}</h3></div>
    ${items.length ? `<ul class="list">${items.map((x) => `<li><div class="grow">${fn(x)}</div></li>`).join("")}</ul>` : `<div class="empty small">None</div>`}</section>`;
  const tk = (x) => `${x.ticket ? `<span class="ticket">${esc(x.ticket)}</span> ` : ""}<span class="t">${esc(x.title || "")}</span>`;
  const maxLoad = Math.max(1, ...(rep.workload || []).map((w) => w.active_items || 0));
  box.dataset.cites = "";
  box.innerHTML = `
    <div class="health ${esc(rep.health || "at_risk")}"><div class="big">${esc(H[rep.health] || nice(rep.health))}</div><div>${esc(rep.summary || "")}</div></div>
    <div class="cols three">
      ${col("Blocked", "red", rep.blocked || [], (x) => `${tk(x)}<div class="s">${esc(x.owner || "")}${x.waiting_on ? ` · waiting on ${esc(x.waiting_on)}` : ""}${x.since ? ` · since ${esc(x.since)}` : ""}</div>`)}
      ${col("In progress", "blue", rep.in_progress || [], (x) => `${tk(x)}<div class="s">${esc(x.owner || "")}${x.status ? ` · ${esc(nice(x.status))}` : ""}${x.due ? ` · due ${esc(x.due)}` : ""}</div>`)}
      ${col("Done", "green", rep.completed || [], (x) => `${tk(x)}<div class="s">${esc(x.owner || "")}</div>`)}
    </div>
    <div class="cols mt">
      <section class="card"><div class="hd">${icon("users")}<h3>Customer commitments</h3></div>
        ${(rep.customer_commitments || []).length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Commitment</th><th>Owner</th><th>Due</th><th>Risk</th></tr></thead><tbody>
        ${rep.customer_commitments.map((c) => `<tr><td><span class="muted small">${esc(c.customer || "")}</span><div>${esc(c.commitment)}</div></td><td>${esc(c.owner || "")}</td>
          <td class="small">${esc(c.due || "")}</td><td><span class="pill ${c.risk === "high" ? "red" : c.risk === "medium" ? "amber" : "green"}">${esc(c.risk || "")}</span></td></tr>`).join("")}
        </tbody></table></div>` : `<div class="empty small">No open commitments</div>`}</section>
      <section class="card"><div class="hd">${icon("activity")}<h3>Workload</h3></div><div class="bd">
        ${(rep.workload || []).map((w) => `<div class="wl"><span>${esc(w.person)}</span>
          <div><div class="track"><div class="fill ${esc(w.load || "")}" style="width:${((w.active_items || 0) / maxLoad) * 100}%"></div></div>
          ${w.note ? `<div class="small muted" style="margin-top:3px">${esc(w.note)}</div>` : ""}</div><b>${w.active_items ?? ""}</b></div>`).join("") || `<span class="muted small">No workload data</span>`}
      </div></section>
    </div>
    ${(rep.recommendations || []).length ? `<section class="card mt"><div class="hd">${icon("sparkle")}<h3>Recommendations</h3></div><div class="bd md"><ul>${rep.recommendations.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></section>` : ""}
    <section class="card mt"><div class="bd">${memBlock(r.based_on, `Evidence: based on ${r.based_on.length} memories`)}
      ${(r.mental_models_used || []).length ? `<div class="chips mt-s"><span class="small muted">Mental models:</span>${r.mental_models_used.map((m) => `<span class="pill violet">${esc(m)}</span>`).join("")}</div>` : ""}</div></section>`;
}

// ====================================================================== SOPS
async function viewSops(el) {
  el.innerHTML = `<div class="cols side">
    <section class="card"><div class="hd">${icon("book")}<h2>Procedures</h2></div><div id="sopList">${skel(4)}</div></section>
    <div class="stack">
      <section class="card" data-cites><div class="hd">${icon("chat")}<h2>Ask the SOP vault</h2></div><div class="bd">
        <form class="row" id="sopForm"><input class="grow" id="sopQ" placeholder="How do we ship a hotfix?" aria-label="SOP question"><button class="btn primary">${icon("send")} Ask</button></form>
        <div class="suggest mt-s">${["How do we ship a hotfix?", "What approvals do I need for a production DB migration?", "How many reviewers does a PR need?"].map((s) => `<button type="button">${esc(s)}</button>`).join("")}</div>
        <div id="sopAns"></div></div></section>
      <section class="card" id="sopDoc"></section>
    </div></div>`;
  let sops = [];
  try { sops = await api("/sops"); } catch (e) { $("#sopList", el).innerHTML = errorBox(e); }
  const show = (s) => {
    $$("[data-sop]", el).forEach((li) => li.classList.toggle("active", li.dataset.sop === s.document_id));
    $("#sopDoc", el).innerHTML = `<div class="hd"><h3 class="grow">${esc(s.title)}</h3><span class="small muted">${esc(fmtDate(s.occurred_at))}</span></div><div class="bd md">${md(s.content || s.summary)}</div>`;
  };
  $("#sopList", el).innerHTML = sops.length ? `<ul class="list mlist">${sops.map((s) => `<li class="click" data-sop="${esc(s.document_id)}"><div class="grow"><div class="t">${esc(s.title)}</div><div class="s">${esc(s.summary || "")}</div></div></li>`).join("")}</ul>`
    : emptyBox("book", "No SOPs yet", "Load the dataset in Settings, or add one on the Sources page.");
  $$("[data-sop]", el).forEach((li) => li.onclick = () => show(sops.find((s) => s.document_id === li.dataset.sop)));
  if (sops.length) show(sops[0]); else $("#sopDoc", el).hidden = true;

  const ask = async (q) => {
    $("#sopQ", el).value = q;
    $("#sopAns", el).innerHTML = `<div class="mt">${loadingNote("Recalling SOP steps…")}</div>`;
    try {
      const r = await api("/sops/ask", { method: "POST", json: { question: q } });
      $("#sopAns", el).innerHTML = `<div class="md mt">${md(r.answer)}</div><div class="mt-s">${memBlock(r.memories)}</div>`;
    } catch (e) { $("#sopAns", el).innerHTML = `<p style="color:var(--red)">${esc(e.message)}</p>`; }
  };
  $("#sopForm", el).onsubmit = (e) => { e.preventDefault(); const q = $("#sopQ", el).value.trim(); if (q) ask(q); };
  $$(".suggest button", el).forEach((b) => b.onclick = () => ask(b.textContent));
}

// =================================================================== SOURCES
const SOURCE_TYPES = ["standup", "meeting", "task_update", "sop", "incident", "retro", "note"];
async function viewSources(el) {
  el.innerHTML = `
    <section class="card mb"><details id="addBox"><summary class="hd" style="cursor:pointer">${icon("plus")}<h2 class="grow">Add to memory</h2>
      <span class="small muted">Paste a standup, ticket update, SOP or notes. SprintMind extracts tagged facts and retains them.</span></summary>
      <form class="bd" id="ingForm">
        <div class="grid2"><div><label class="f">Type</label><select name="source_type">${SOURCE_TYPES.map((t) => `<option value="${t}">${esc(nice(t))}</option>`).join("")}</select></div>
          <div><label class="f">When</label><input type="datetime-local" name="occurred_at"></div></div>
        <div class="grid2"><div><label class="f">Title</label><input name="title" required placeholder="Daily standup"></div>
          <div><label class="f">Customer</label><select name="customer"><option value="">None</option>${S.team.customers.map((c) => `<option>${esc(c.name)}</option>`).join("")}</select></div></div>
        <label class="f">Content</label><textarea name="content" class="mono" rows="8" required placeholder="Priya: NW-231 in review, needs a second reviewer…"></textarea>
        <div class="row mt"><button class="btn primary">${icon("database")} Retain in memory</button><span class="grow"></span></div>
        <div id="ingOut"></div>
      </form></details></section>
    <div class="filters" id="typeFilter"></div>
    <section class="card"><div id="srcTable">${skel(6)}</div></section>`;

  const now = new Date(); now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  $("[name=occurred_at]", el).value = now.toISOString().slice(0, 16);
  let sources = [], filter = "";
  const draw = () => {
    const list = sources.filter((s) => !filter || s.source_type === filter).slice().reverse();
    $("#typeFilter", el).innerHTML = `<div class="tabs">${["", ...new Set(sources.map((s) => s.source_type))].map((t) =>
      `<button data-t="${t}" class="${t === filter ? "on" : ""}">${t ? esc(nice(t)) : "All"} <span class="muted">${t ? sources.filter((s) => s.source_type === t).length : sources.length}</span></button>`).join("")}</div>`;
    $$("[data-t]", el).forEach((b) => b.onclick = () => { filter = b.dataset.t; draw(); });
    $("#srcTable", el).innerHTML = list.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>When</th><th>Type</th><th>Title &amp; summary</th><th>People</th><th>Facts</th></tr></thead><tbody>
      ${list.map((s) => `<tr><td class="small" style="white-space:nowrap">${esc(fmtDateTime(s.occurred_at))}</td><td>${typePill(s.source_type)}</td>
        <td><div class="t" style="font-weight:600">${esc(s.title)}</div><div class="small muted">${esc(s.summary || "")}</div>
          ${s.extraction_error ? `<span class="pill amber" title="${esc(s.extraction_error)}">raw text only</span>` : ""}</td>
        <td><div class="row" style="gap:2px">${(s.people || []).map((id) => { const p = member(id); return p ? avatar(p.name, p.id) : ""; }).join("")}</div></td>
        <td><b>${s.facts_retained}</b></td></tr>`).join("")}</tbody></table></div>`
      : emptyBox("file", "Nothing in memory yet", "Load the Sprint 14 dataset in Settings, record a meeting, or add something above.");
  };
  const load = async () => { try { sources = await api("/sources"); draw(); } catch (e) { $("#srcTable", el).innerHTML = errorBox(e); } };
  await load();

  $("#ingForm", el).onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd);
    body.occurred_at = body.occurred_at ? new Date(body.occurred_at).toISOString() : null;
    body.customer = body.customer || null;
    await busy($("button.primary", e.target), "Extracting & retaining", async () => {
      try {
        const r = await api("/ingest", { method: "POST", json: body });
        $("#ingOut", el).innerHTML = `<div class="mt"><p><b>${r.facts_retained} facts retained</b> from “${esc(r.title)}”${r.extraction_error ? ` <span class="pill amber">raw text only: ${esc(r.extraction_error)}</span>` : ""}</p>
          <ul class="list card">${r.items.map((i) => `<li><span class="pill">${esc(nice(i.kind))}</span><div class="grow">${i.ticket ? `<span class="ticket">${esc(i.ticket)}</span> ` : ""}${esc(i.title || i.detail)}
            <div class="s">${esc(i.owner || "")}${i.status ? ` · ${esc(nice(i.status))}` : ""}</div></div>${dueHtml(i.due)}</li>`).join("")}</ul></div>`;
        e.target.content.value = "";
        toast("Retained in Hindsight");
        S.cache.briefing = {}; S.cache.radar = null;
        load();
      } catch (err) { toast(err.message, true); }
    });
  };
}

// ================================================================= INSPECTOR
async function viewInspector(el) {
  el.innerHTML = `<div class="row mb"><div class="tabs" id="iTabs"><button data-t="live" class="on">Live activity</button><button data-t="browse">Browse memories</button><button data-t="recall">Recall tester</button></div></div>
    <div data-p="live"></div><div data-p="browse" hidden></div><div data-p="recall" hidden></div>`;
  const tabs = (t) => { $$("#iTabs button", el).forEach((b) => b.classList.toggle("on", b.dataset.t === t)); $$("[data-p]", el).forEach((p) => p.hidden = p.dataset.p !== t); };
  $$("#iTabs button", el).forEach((b) => b.onclick = () => tabs(b.dataset.t));

  // live
  const live = $("[data-p=live]", el);
  let op = "";
  live.innerHTML = `<section class="card"><div class="hd"><div class="opcounts grow" id="opCounts"></div>
      <select id="opFilter" style="width:auto" aria-label="Filter by operation"><option value="">All operations</option>${["retain", "recall", "reflect", "llm", "transcribe", "setup"].map((o) => `<option>${o}</option>`).join("")}</select>
      <button class="btn sm" id="clearEv">Clear</button></div><div style="padding:8px" id="evList"></div></section>
    <p class="small muted">Click any row to see the exact request Hindsight received and the memories it returned. This is the proof that answers come from stored facts.</p>`;
  const drawLive = () => {
    if (!live.isConnected) return S.listeners.delete(drawLive);
    const counts = S.stats?.completed_by_op || {};
    $("#opCounts", live).innerHTML = Object.keys(counts).length ? Object.entries(counts).map(([k, v]) => `<span class="op ${esc(k)}">${esc(k)} ${v}</span>`).join("") : `<span class="small muted">No operations yet</span>`;
    const evs = visibleEvents(op).slice(0, 300);
    $("#evList", live).innerHTML = evs.length ? evs.map(evRow).join("") : emptyBox("activity", "Waiting for memory activity", "Use any page and operations appear here in real time.");
  };
  $("#opFilter", live).onchange = (e) => { op = e.target.value; drawLive(); };
  $("#clearEv", live).onclick = async () => { await api("/inspector/events", { method: "DELETE" }).catch(() => {}); S.events = []; S.inflight.clear(); S.stats = null; drawLive(); };
  S.listeners.add(drawLive); drawLive();

  // browse
  const browse = $("[data-p=browse]", el);
  browse.innerHTML = `<form class="filters" id="bForm"><select name="type" aria-label="Memory type"><option value="">All types</option><option>world</option><option>experience</option><option>observation</option></select>
    <input name="q" type="search" placeholder="Search memory text" aria-label="Search"><button class="btn primary">${icon("search")} Search</button></form><div id="bOut"></div>`;
  const doBrowse = async () => {
    const fd = new FormData($("#bForm", browse)); const p = new URLSearchParams({ limit: "100" });
    if (fd.get("type")) p.set("type", fd.get("type")); if (fd.get("q")) p.set("q", fd.get("q"));
    $("#bOut", browse).innerHTML = skel(6);
    try { const r = await api(`/memory/list?${p}`); $("#bOut", browse).innerHTML = r.count ? `<p class="small muted">${r.count} memories</p>${memList(r.memories)}` : emptyBox("database", "No memories", "Nothing matches, or the bank is empty."); }
    catch (e) { $("#bOut", browse).innerHTML = errorBox(e); }
  };
  $("#bForm", browse).onsubmit = (e) => { e.preventDefault(); doBrowse(); };
  $("[data-t=browse]", el).addEventListener("click", () => !$("#bOut", browse).innerHTML && doBrowse());

  // recall tester
  const rc = $("[data-p=recall]", el);
  rc.innerHTML = `<section class="card"><form class="bd" id="rForm">
      <label class="f">Query</label><input name="query" required placeholder="What is blocking NW-240?">
      <div class="grid2"><div><label class="f">Tags <span class="muted" style="font-weight:400">(comma-separated, e.g. person:priya, status:blocked)</span></label><input name="tags"></div>
        <div class="grid2"><div><label class="f">Tag match</label><select name="tags_match">${["any", "all", "any_strict", "all_strict"].map((x) => `<option>${x}</option>`).join("")}</select></div>
          <div><label class="f">Budget</label><select name="budget"><option>low</option><option selected>mid</option><option>high</option></select></div></div></div>
      <div class="row mt"><button class="btn primary">${icon("search")} Recall</button></div></form></section><div id="rOut" class="mt"></div>`;
  $("#rForm", rc).onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const tags = fd.get("tags").split(",").map((t) => t.trim()).filter(Boolean);
    $("#rOut", rc).innerHTML = skel(6);
    try {
      const r = await api("/memory/recall", { method: "POST", json: { query: fd.get("query"), tags: tags.length ? tags : null, tags_match: fd.get("tags_match"), budget: fd.get("budget") } });
      $("#rOut", rc).innerHTML = r.count ? `<p class="small muted">${r.count} memories recalled</p>${memList(r.memories, true)}` : emptyBox("database", "Nothing recalled", "Try a broader query or remove tags.");
    } catch (err) { $("#rOut", rc).innerHTML = errorBox(err); }
  };
}

// ====================================================================== DEMO
const DEMO_Q = "What were the key deliverables agreed with Acme Corp in yesterday's Teams sync, and who owns each one?";
async function viewDemo(el) {
  const st = S.cache.demo;
  st.q ||= DEMO_Q;
  el.innerHTML = `
    <div class="steps">
      <div class="card step ${st.before ? "done" : ""}"><div class="k">Step 1 · Without memory</div><b>A plain LLM gets the question</b><div class="small muted">No context, so it can only guess.</div></div>
      <div class="card step ${st.retained ? "done" : ""}"><div class="k">Step 2 · retain()</div><b>SprintMind learns the meeting</b><div class="small muted">The Acme Teams sync becomes summary, facts and action events.</div></div>
      <div class="card step ${st.after ? "done" : ""}"><div class="k">Step 3 · recall()</div><b>Same question, with memory</b><div class="small muted">Answer is grounded in cited memories.</div></div>
    </div>
    <section class="card mb"><div class="bd">
      <label class="f" for="dq">Question</label><textarea id="dq" rows="2">${esc(st.q)}</textarea>
      <div class="row mt">
        <button class="btn" id="d1">1 · Ask without memory</button>
        <button class="btn" id="d2">${icon("database")} 2 · Retain the Acme sync</button>
        <button class="btn primary" id="d3">3 · Ask with memory</button>
        <span class="grow"></span><button class="btn ghost" id="dBoth">${icon("split")} Run 1 &amp; 3 side by side</button>
      </div>
      <div id="dRet" class="small mt-s"></div>
    </div></section>
    <div class="cols vs">
      <section class="card"><div class="hd bad">${icon("x")}<h3>Without memory</h3></div><div class="bd" id="dBefore"></div></section>
      <section class="card" data-cites><div class="hd good">${icon("check")}<h3>With Hindsight memory ${S.person && me() ? `· as ${esc(me().name)}` : ""}</h3></div><div class="bd" id="dAfter"></div></section>
    </div>`;
  const q = () => (st.q = $("#dq", el).value.trim());
  const drawBefore = () => $("#dBefore", el).innerHTML = st.before ? `<div class="md">${md(st.before.answer)}</div>` : `<span class="muted small">Run step 1 to see what a stateless model says.</span>`;
  const drawAfter = () => $("#dAfter", el).innerHTML = st.after ? `<div class="md">${md(st.after.answer)}</div><div class="mt">${memBlock(st.after.memories)}</div>` : `<span class="muted small">Run step 3 after step 2.</span>`;
  const drawRet = () => $("#dRet", el).innerHTML = st.retained ? `${icon("check")} Retained <b>${esc(st.retained.title)}</b>: ${st.retained.facts_retained} facts, ${st.retained.actions.length} action events. <a href="#/meetings/${encodeURIComponent(st.retained.id)}">Open meeting →</a>` : "";
  drawBefore(); drawAfter(); drawRet();
  const loadInto = (id, t) => $(id, el).innerHTML = loadingNote(t);

  $("#d1", el).onclick = (e) => busy(e.currentTarget, "Asking", async () => {
    loadInto("#dBefore", "Asking the LLM with no memory…");
    try { st.before = await api("/demo/baseline", { method: "POST", json: { question: q() } }); } catch (err) { toast(err.message, true); }
    if (el.isConnected) viewDemo(el);
  });
  $("#d2", el).onclick = (e) => busy(e.currentTarget, "Retaining", async () => {
    toggleDrawer(true);
    try {
      const t = await api("/demo/transcript");
      st.retained = await api("/meetings", { method: "POST", json: { transcript: t.content, title: t.title, meeting_type: "customer_sync", customer: t.customer, participants: t.participants, occurred_at: t.occurred_at } });
      S.cache.briefing = {}; S.cache.radar = null; refreshCounts();
      toast(`Retained: ${st.retained.facts_retained} facts, ${st.retained.actions.length} action events`);
    } catch (err) { toast(err.message, true); }
    if (el.isConnected) viewDemo(el);
  });
  $("#d3", el).onclick = (e) => busy(e.currentTarget, "Recalling", async () => {
    loadInto("#dAfter", "Recalling memories from Hindsight…");
    try { st.after = await api("/employee/ask", { method: "POST", json: { question: q(), person_id: S.person || null } }); } catch (err) { toast(err.message, true); }
    if (el.isConnected) viewDemo(el);
  });
  $("#dBoth", el).onclick = (e) => busy(e.currentTarget, "Comparing", async () => {
    loadInto("#dBefore", "Asking without memory…"); loadInto("#dAfter", "Recalling memories…");
    try { const r = await api("/demo/compare", { method: "POST", json: { question: q(), person_id: S.person || null } }); st.before = r.without_memory; st.after = r.with_memory; }
    catch (err) { toast(err.message, true); }
    if (el.isConnected) viewDemo(el);
  });
}

// ================================================================== SETTINGS
async function viewSettings(el) {
  await loadHealth();
  const h = S.health || {};
  const mem = h.memory || {};
  const row = (k, v, ok) => `<li><span class="grow">${esc(k)}</span><span class="small">${ok === undefined ? "" : `<span class="dot ${ok ? "ok" : "bad"}"></span>`}${esc(v)}</span></li>`;
  el.innerHTML = `<div class="cols">
    <section class="card"><div class="hd">${icon("activity")}<h2 class="grow">System status</h2><button class="btn sm ghost" id="reH">${icon("refresh")}</button></div>
      <ul class="list">
        ${row("Memory backend", mem.error ? mem.error : mem.backend + (mem.api_version ? ` · API ${mem.api_version}` : ""), !mem.error)}
        ${row("Memory bank", h.bank_id || "")}
        ${row("LLM", h.llm?.configured ? h.llm.model : "not configured (set GROQ_API_KEY)", !!h.llm?.configured)}
        ${row("Transcriber", h.transcriber?.engine || h.transcriber?.error || "off", !!h.transcriber?.engine)}
        ${row("Current sprint", h.sprint || "")}
        ${row("Team", `${S.team.name} · ${S.team.members.length} people`)}
      </ul></section>
    <section class="card"><div class="hd">${icon("database")}<h2>Memory bank</h2></div><div class="bd">
      <p class="small muted" style="margin-top:0">Set up the bank's mission, directives and mental models, load the synthetic Sprint 14 history, or start over.</p>
      <div class="stack">
        <div class="row"><button class="btn" id="setupBtn">${icon("settings")} Configure bank</button><span class="small muted grow">Safe to repeat</span></div>
        <div class="row"><button class="btn primary" id="seedBtn2">${icon("database")} Load Sprint 14 dataset</button><span class="small muted grow">About 12 documents, a few minutes</span></div>
        <div class="row"><button class="btn danger" id="resetBtn">${icon("alert")} Reset memory</button><span class="small muted grow">Deletes the bank, sources and saved meetings</span></div>
      </div></div></section>
    <section class="card"><div class="hd">${icon("sparkle")}<h2>Appearance</h2></div><div class="bd">
      <label class="f">Theme</label><select id="theme"><option value="system">Match system</option><option value="light">Light</option><option value="dark">Dark</option></select></div></section>
    <section class="card"><div class="hd">${icon("book")}<h2>API</h2></div><div class="bd small">
      <p style="margin-top:0">Everything in this UI is a call to the SprintMind API. Explore it at <a href="/docs" target="_blank" rel="noopener">/docs</a>.</p></div></section>
  </div>`;
  $("#reH", el).onclick = () => viewSettings(el);
  $("#theme", el).value = store.get("theme", "system");
  $("#theme", el).onchange = (e) => { store.set("theme", e.target.value); applyTheme(); };
  $("#setupBtn", el).onclick = (e) => busy(e.currentTarget, "Configuring", async () => {
    try { const r = await api("/admin/setup", { method: "POST" }); toast(`Bank ready: ${(r.directives || []).length} directives, ${(r.mental_models || []).length} mental models`); }
    catch (err) { toast(err.message, true); }
  });
  $("#seedBtn2", el).onclick = (e) => seedDataset(e.currentTarget);
  $("#resetBtn", el).onclick = async (e) => {
    const typed = prompt(`This permanently deletes every memory in "${h.bank_id}", the source list and saved meetings.\n\nType the bank id to confirm:`);
    if (typed !== h.bank_id) { if (typed !== null) toast("Bank id didn't match. Nothing was deleted.", true); return; }
    await busy(e.currentTarget, "Resetting", async () => {
      try { await api("/admin/reset", { method: "POST" }); S.cache = { briefing: {}, radar: null, chats: {}, manager: [], demo: {} }; refreshCounts(); toast("Memory reset"); }
      catch (err) { toast(err.message, true); }
    });
  };
}

// ====================================================================== boot
function applyTheme() {
  const t = store.get("theme", "system");
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
}
async function loadHealth() {
  try {
    S.health = await api("/health");
    const mem = S.health.memory || {};
    $("#memDot").className = "dot " + (mem.error ? "bad" : "ok");
    $("#memTxt").textContent = mem.error ? "Memory unreachable" : `Memory: ${mem.backend}${mem.backend === "local" ? " (offline)" : ""}`;
    $("#llmDot").className = "dot " + (S.health.llm?.configured ? "ok" : "bad");
    $("#llmTxt").textContent = S.health.llm?.configured ? S.health.llm.model : "LLM not configured";
  } catch {
    $("#memDot").className = "dot bad"; $("#memTxt").textContent = "API unreachable";
    $("#llmDot").className = "dot"; $("#llmTxt").textContent = "";
  }
}
function renderPersona() {
  const sel = $("#persona");
  sel.innerHTML = `${S.team.members.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("")}<option value="">Whole team</option>`;
  if (S.person && !member(S.person)) S.person = S.team.members[0]?.id || "";
  sel.value = S.person;
  sel.onchange = () => { S.person = sel.value; store.set("person", S.person); rerender(); };
}

async function boot() {
  applyTheme();
  $("#menuBtn").innerHTML = icon("menu");
  $("#drawerClose").innerHTML = icon("x");
  $("#menuBtn").onclick = () => toggleSidebar(!$("#sidebar").classList.contains("open"));
  $("#scrim").onclick = () => toggleSidebar(false);
  $("#pulseBtn").onclick = () => toggleDrawer();
  $("#drawerClose").onclick = () => toggleDrawer(false);
  document.addEventListener("keydown", (e) => e.key === "Escape" && $("#drawer").classList.contains("open") && !$(".modal-wrap") && toggleDrawer(false));
  renderNav();
  try {
    const [team, options] = await Promise.all([api("/team"), api("/meetings/options"), loadHealth()]);
    S.team = { ...team, name: team.team }; S.options = options;
    $("#teamName").textContent = team.team;
  } catch (e) {
    $("#view").innerHTML = `<div class="card">${errorBox(e)}</div>`;
    return;
  }
  renderPersona();
  window.addEventListener("hashchange", route);
  route();
  refreshCounts();
  pollEvents();
  setInterval(loadHealth, 30000);
}
boot();
