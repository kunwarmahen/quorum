"use strict";

const $ = (sel) => document.querySelector(sel);
const api = (url, opts) => fetch(url, opts).then((r) => r.json());

let recordingActive = false;
let recStart = null;
let cache = {}; // id -> recording (for the drawer)
let drawerText = { minutes: "", transcript: "" }; // raw text for copy
let sortKey = "created_at"; // "created_at" | "name"
let sortDir = "desc"; // "asc" | "desc"

// --- helpers ---------------------------------------------------------------
function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function levelFor() {
  return $("#levelSelect").value;
}

function stageChip(label, status) {
  const cls = ["pending", "running", "done", "error"].includes(status) ? status : "pending";
  return `<span class="stage ${cls}" title="${status}"><span class="s-dot"></span>${label}</span>`;
}

function stagesHtml(r) {
  const sourceStatus = "done"; // file exists => source stage complete
  const parts = [
    stageChip("source", sourceStatus),
    `<span class="arrow">→</span>`,
    stageChip("mp3", r.kind === "audio" ? "done" : r.audio_status),
    `<span class="arrow">→</span>`,
    stageChip("transcript", r.transcript_status),
    `<span class="arrow">→</span>`,
    stageChip("minutes", r.minutes_status),
  ];
  return `<div class="stages">${parts.join("")}</div>`;
}

function actionsHtml(r) {
  const busy = [r.audio_status, r.transcript_status, r.minutes_status].includes("running");
  const btns = [];
  btns.push(`<button class="btn btn-sm btn-primary" data-act="run" data-id="${r.id}" ${busy ? "disabled" : ""}>▶ Run all</button>`);
  if (r.kind !== "audio")
    btns.push(`<button class="btn btn-sm" data-act="convert" data-id="${r.id}" ${busy ? "disabled" : ""}>mp3</button>`);
  btns.push(`<button class="btn btn-sm" data-act="transcribe" data-id="${r.id}" ${busy ? "disabled" : ""}>transcribe</button>`);
  btns.push(`<button class="btn btn-sm" data-act="minutes" data-id="${r.id}" ${busy ? "disabled" : ""}>minutes</button>`);
  if (r.transcript_text || r.minutes_text)
    btns.push(`<button class="btn btn-sm" data-act="view" data-id="${r.id}">view</button>`);
  btns.push(`<button class="btn btn-sm btn-danger-ghost" data-act="delete" data-id="${r.id}" title="Delete meeting and its files">🗑</button>`);
  return `<div class="row-actions">${btns.join("")}</div>`;
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtBytes(n) {
  if (n == null) return null;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 || v >= 100 ? 0 : 1)} ${u[i]}`;
}

function fmtDuration(sec) {
  if (sec == null) return null;
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}

function rowHtml(r) {
  const lvl = r.minutes_level ? ` · minutes: ${r.minutes_level}` : "";
  const tip = `${fmtDate(r.file_mtime || r.created_at)} · ${r.kind}${lvl}`;
  const err = r.error && r.status === "error" ? `<div class="ferr">${esc(r.error)}</div>` : "";
  // Prefer the file's own timestamp; fall back to when it was added to the app.
  const fileDate = r.file_mtime || r.created_at;
  const meta = [fmtDuration(r.duration_seconds), fmtBytes(r.size_bytes),
                fileDate ? fmtDate(fileDate) : null]
    .filter(Boolean).join(" · ");
  const metaHtml = meta ? `<div class="fmeta">${esc(meta)}</div>` : "";
  return `<tr>
    <td>
      <div class="fcell">
        <span class="fname">${esc(r.name)}</span>
        <span class="info" tabindex="0" title="${esc(tip)}" aria-label="${esc(tip)}">&#9432;</span>
      </div>
      ${metaHtml}
      ${err}
    </td>
    <td>${stagesHtml(r)}</td>
    <td>${actionsHtml(r)}</td>
  </tr>`;
}

// --- rendering -------------------------------------------------------------
function renderObs(obs) {
  const light = $("#obsLight");
  const text = $("#obsText");
  light.className = "obs-light";
  if (!obs || !obs.connected) {
    light.classList.add("bad");
    text.textContent = "OBS not connected";
    setRecording(false);
    return;
  }
  if (obs.recording) {
    light.classList.add("rec");
    text.textContent = "OBS recording" + (obs.timecode ? " · " + obs.timecode : "");
    setRecording(true);
  } else {
    light.classList.add("ok");
    text.textContent = "OBS connected";
    setRecording(false);
  }
}

// Render a pill per external service (Whisper STT, Ollama). OBS keeps its own
// pill above because it also drives the record buttons + timer.
function renderHealth(health) {
  const strip = $("#healthStrip");
  const services = (health && health.services) || [];
  for (const s of services) {
    if (s.name === "OBS") continue; // OBS pill is managed by renderObs
    const id = "health-" + s.name.replace(/\W+/g, "");
    let pill = document.getElementById(id);
    if (!pill) {
      pill = document.createElement("span");
      pill.className = "health-pill";
      pill.id = id;
      pill.innerHTML = `<span class="obs-light"></span><span class="ht"></span>`;
      strip.appendChild(pill);
    }
    const light = pill.querySelector(".obs-light");
    const text = pill.querySelector(".ht");
    light.className = "obs-light " + (s.ok ? "ok" : "bad");
    let label = s.name + (s.ok ? " ok" : " down");
    if (s.ok && s.model_ready === false) {
      light.className = "obs-light warn";
      label = s.name + " · model missing";
    }
    text.textContent = label;
    pill.title = s.error ? `${s.name}: ${s.error}` : `${s.name} — ${s.url}`;
  }
}

async function refreshHealth() {
  try {
    renderHealth(await api("/api/health"));
  } catch (e) {
    /* transient */
  }
}

// --- config panel (model selection) ----------------------------------------
function fillModelSelect(sel, noteEl, info) {
  const models = info.models || [];
  const current = info.current || "";
  const opts = models.slice();
  // Always keep the current selection visible, even if the server is down or
  // the model isn't in the returned list.
  if (current && !opts.includes(current)) opts.unshift(current);
  sel.innerHTML = opts
    .map((m) => `<option value="${esc(m)}" ${m === current ? "selected" : ""}>${esc(m)}</option>`)
    .join("") || `<option value="">(no models)</option>`;
  if (info.ok === false) {
    noteEl.textContent = "server unreachable — showing saved value";
    noteEl.className = "cfg-note bad";
  } else {
    noteEl.textContent = `${models.length} available`;
    noteEl.className = "cfg-note";
  }
}

async function loadModels() {
  let data;
  try {
    data = await api("/api/models");
  } catch (e) {
    return;
  }
  fillModelSelect($("#sttModelSelect"), $("#sttNote"), data.stt || {});
  fillModelSelect($("#ollamaModelSelect"), $("#ollamaNote"), data.ollama || {});
}

async function saveConfig() {
  await api("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      stt_model: $("#sttModelSelect").value,
      ollama_model: $("#ollamaModelSelect").value,
    }),
  });
  refreshHealth(); // reflect the new model's readiness in the health pills
}

$("#btnConfig").addEventListener("click", () => {
  const p = $("#configPanel");
  p.hidden = !p.hidden;
  if (!p.hidden) loadModels();
});
$("#configClose").addEventListener("click", () => ($("#configPanel").hidden = true));
$("#sttModelSelect").addEventListener("change", saveConfig);
$("#ollamaModelSelect").addEventListener("change", saveConfig);

function setRecording(on) {
  recordingActive = on;
  $("#btnStart").disabled = on;
  $("#btnStop").disabled = !on;
  if (on && !recStart) recStart = Date.now();
  if (!on) { recStart = null; $("#recTimer").textContent = ""; }
}

function sortRecs(recs) {
  const dir = sortDir === "asc" ? 1 : -1;
  return recs.slice().sort((a, b) => {
    let av, bv;
    if (sortKey === "name") {
      av = (a.name || "").toLowerCase();
      bv = (b.name || "").toLowerCase();
    } else {
      av = a.file_mtime || a.created_at || "";
      bv = b.file_mtime || b.created_at || "";
    }
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });
}

function renderTable(recs) {
  const body = $("#recBody");
  if (!recs.length) {
    body.innerHTML = `<tr><td colspan="3" class="empty">No files yet. Record a meeting or drop a .mkv/.mp3 into ~/MeetingMinutes/recordings and hit “Scan folder”.</td></tr>`;
    return;
  }
  cache = {};
  recs.forEach((r) => (cache[r.id] = r));
  body.innerHTML = sortRecs(recs).map(rowHtml).join("");
}

$("#sortSelect").addEventListener("change", (e) => {
  const [key, dir] = e.target.value.split("_");
  sortKey = key === "name" ? "name" : "created_at";
  sortDir = dir === "asc" ? "asc" : "desc";
  refresh();
});

// --- polling ---------------------------------------------------------------
async function refresh() {
  try {
    const data = await api("/api/recordings");
    renderObs(data.obs);
    renderTable(data.recordings);
  } catch (e) {
    /* transient */
  }
}

setInterval(refresh, 2500);
// Health probes hit external servers, so poll them less aggressively.
setInterval(refreshHealth, 15000);
setInterval(() => {
  if (recStart) {
    const s = Math.floor((Date.now() - recStart) / 1000);
    const m = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    $("#recTimer").textContent = `${m}:${ss}`;
  }
}, 1000);

// --- actions ---------------------------------------------------------------
$("#btnStart").addEventListener("click", async () => {
  $("#btnStart").disabled = true;
  const r = await api("/api/obs/start", { method: "POST" });
  if (r.detail) alert(r.detail);
  refresh();
});

$("#btnStop").addEventListener("click", async () => {
  $("#btnStop").disabled = true;
  const r = await api("/api/obs/stop", { method: "POST" });
  if (r.warning) alert(r.warning);
  await refresh();
});

$("#btnScan").addEventListener("click", async () => {
  const r = await api("/api/scan", { method: "POST" });
  refresh();
});

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  if (act === "view") return openDrawer(id);
  if (act === "delete") return openConfirm(id);

  btn.disabled = true;
  const map = {
    convert: ["/api/recordings/" + id + "/convert", {}],
    transcribe: ["/api/recordings/" + id + "/transcribe", {}],
    minutes: ["/api/recordings/" + id + "/minutes", { level: levelFor() }],
    run: ["/api/recordings/" + id + "/run", { level: levelFor() }],
  };
  const [url, payload] = map[act];
  await api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  refresh();
});

// --- delete confirmation ---------------------------------------------------
let pendingDeleteId = null;

async function openConfirm(id) {
  // Ask the server exactly what would be removed, then show it for review.
  let plan;
  try {
    plan = await api("/api/recordings/" + id + "/delete-preview");
  } catch (e) {
    return alert("Could not load delete preview.");
  }
  pendingDeleteId = id;
  $("#confirmName").textContent = plan.name || "";
  const items = [];
  if (plan.whole_folder && plan.folder)
    items.push(`<li class="del-folder">📁 ${esc(plan.folder)}/</li>`);
  (plan.files || []).forEach((f) =>
    items.push(`<li>${esc(f.split("/").pop())}</li>`)
  );
  if (!items.length) items.push(`<li class="muted">(no files on disk; only the database entry will be removed)</li>`);
  $("#confirmList").innerHTML = items.join("");
  const input = $("#confirmInput");
  input.value = "";
  $("#confirmDelete").disabled = true;
  $("#confirmModal").classList.add("open");
  $("#confirmBackdrop").classList.add("open");
  setTimeout(() => input.focus(), 50);
}

function closeConfirm() {
  pendingDeleteId = null;
  $("#confirmInput").value = "";
  $("#confirmDelete").disabled = true;
  $("#confirmModal").classList.remove("open");
  $("#confirmBackdrop").classList.remove("open");
}

// Enable the Delete button only once the user types the confirmation word.
$("#confirmInput").addEventListener("input", (e) => {
  $("#confirmDelete").disabled = e.target.value.trim().toLowerCase() !== "delete";
});
$("#confirmInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !$("#confirmDelete").disabled) $("#confirmDelete").click();
});

$("#confirmCancel").addEventListener("click", closeConfirm);
$("#confirmBackdrop").addEventListener("click", closeConfirm);
$("#confirmDelete").addEventListener("click", async () => {
  if (pendingDeleteId == null || $("#confirmDelete").disabled) return;
  const btn = $("#confirmDelete");
  btn.disabled = true;
  btn.textContent = "Deleting…";
  try {
    const res = await fetch("/api/recordings/" + pendingDeleteId, { method: "DELETE" });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      alert(j.detail || "Delete failed.");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Delete permanently";
    closeConfirm();
    refresh();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeConfirm();
});

// --- drawer ----------------------------------------------------------------
function inline(s) {
  // Inline spans: bold then italic. Input is already HTML-escaped.
  return s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

// Block-level Markdown -> HTML. Good enough for minutes, and the resulting
// HTML is what we put on the clipboard so paste into Docs keeps formatting.
function mdToHtml(md) {
  const lines = esc(md).split(/\r?\n/);
  const out = [];
  let list = null; // "ul" | "ol" | null

  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      closeList();
      const lvl = Math.min(m[1].length, 6);
      out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
    } else if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      if (list !== "ul") { closeList(); list = "ul"; out.push("<ul>"); }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") { closeList(); list = "ol"; out.push("<ol>"); }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("\n");
}

async function openDrawer(id) {
  const r = cache[id] || (await api("/api/recordings/" + id));
  $("#drawerTitle").textContent = r.name;
  drawerText.minutes = r.minutes_text || "";
  drawerText.transcript = r.transcript_text || "";
  $("#pane-minutes").innerHTML = r.minutes_text
    ? mdToHtml(r.minutes_text)
    : `<div class="placeholder">No minutes generated yet.</div>`;
  $("#pane-transcript").innerHTML = r.transcript_text
    ? esc(r.transcript_text)
    : `<div class="placeholder">No transcript yet.</div>`;
  $("#drawer").classList.add("open");
  $("#drawerBackdrop").classList.add("open");
}

function activeTab() {
  const t = document.querySelector(".tab.active");
  return t ? t.dataset.tab : "minutes";
}

function flashCopy(label) {
  const btn = $("#copyBtn");
  btn.textContent = label;
  setTimeout(() => (btn.innerHTML = "⧉ Copy"), 1200);
}

$("#copyBtn").addEventListener("click", async () => {
  const tab = activeTab();
  const text = drawerText[tab] || "";
  if (!text) return flashCopy("Nothing to copy");

  // Minutes are Markdown: also offer rich HTML so pasting into Google Docs /
  // Word keeps the formatting instead of showing raw Markdown.
  const html = tab === "minutes" ? mdToHtml(text) : null;

  try {
    if (html && window.ClipboardItem && navigator.clipboard?.write) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([text], { type: "text/plain" }),
        }),
      ]);
    } else {
      await navigator.clipboard.writeText(text);
    }
  } catch (e) {
    // Fallback for non-secure contexts (plain http on a LAN host): use an
    // offscreen element so we can still carry rich HTML for the minutes.
    const sel = document.createElement(html ? "div" : "textarea");
    if (html) { sel.innerHTML = html; sel.contentEditable = "true"; }
    else { sel.value = text; }
    sel.style.position = "fixed";
    sel.style.opacity = "0";
    document.body.appendChild(sel);
    const range = document.createRange();
    range.selectNodeContents(sel);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(range);
    if (!html) sel.select();
    document.execCommand("copy");
    s.removeAllRanges();
    document.body.removeChild(sel);
  }
  flashCopy("✓ Copied");
});

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawerBackdrop").classList.remove("open");
}
$("#drawerClose").addEventListener("click", closeDrawer);
$("#drawerBackdrop").addEventListener("click", closeDrawer);
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("#pane-" + t.dataset.tab).classList.add("active");
  })
);

refresh();
refreshHealth();
