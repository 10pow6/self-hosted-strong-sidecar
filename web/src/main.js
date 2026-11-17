import Chart from "chart.js/auto";
import "./style.css";

const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const api = (p, o) => fetch(p, o).then(async r => { if (!r.ok) throw new Error(await r.text()); return r.json(); });
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// kg values converted from lb carry 2 decimals (e.g. 90.72); lb needs at most 1
const fmt = n => n == null ? "—" : (+n).toLocaleString(undefined, {maximumFractionDigits: U() === "kg" ? 2 : 1});
const toast = m => { const t = $("#toast"); t.textContent = m; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2200); };
// Review tag shown on Progress (session table + chart tooltip).
const statusTag = p => ({
  pending: "⚠ needs review",
  ok: "✓ marked OK",
  bar_included: `− ${fmt(p.bar_weight)} ${U()} bar`,
  bar_excluded: `+ ${fmt(p.bar_weight)} ${U()} bar`,
  exclude: "⊘ excluded",
}[p.status] || "");
const statusTitle = p => ({
  pending: "Unreviewed jump: " + (p.reasons || []).join("; "),
  ok: "Flagged as a jump; you marked it real. Used as logged.",
  bar_included: `Logged including the bar; ${fmt(p.bar_weight)} ${U()} subtracted to match your usual logging (logged top ${fmt(p.logged_top_weight)} ${U()}).`,
  bar_excluded: `Logged without the bar; ${fmt(p.bar_weight)} ${U()} added to match your usual logging (logged top ${fmt(p.logged_top_weight)} ${U()}).`,
  exclude: "Excluded from charts, trends and records.",
}[p.status] || "");
const BAR_SIGN = { bar_included: -1, bar_excluded: 1 };
const DECISIONS = [
  ["pending", "— review —"], ["ok", "OK (real)"],
  ["bar_included", "Bar included in log → subtract"], ["bar_excluded", "Bar left out of log → add"],
  ["exclude", "Exclude"]];

let state = { exercises: [], ex: localGet("ex"), range: 365, defaultBar: 45, anomalies: [], charts: {}, units: "lb" };
const U = () => state.units;   // display unit, from /api/v1/settings
function localGet(k) { try { return localStorage.getItem("strong." + k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem("strong." + k, v); } catch {} }

// ---- tabs
$$("nav button").forEach(b => b.onclick = () => {
  $$("nav button").forEach(x => x.classList.toggle("active", x === b));
  $$("main > section").forEach(s => s.classList.toggle("hidden", s.id !== "tab-" + b.dataset.tab));
  history.replaceState(null, "", "#" + b.dataset.tab);
  if (b.dataset.tab === "review") loadReview();
  if (b.dataset.tab === "data") loadImports();
  if (b.dataset.tab === "api") loadApiDocs();
  if (b.dataset.tab === "programs") loadPrograms();
  if (b.dataset.tab === "plans") loadPlans();
  if (b.dataset.tab === "settings") loadSettings();
});
const goTab = t => $(`nav button[data-tab="${t}"]`).click();
addEventListener("hashchange", () => {
  const t = location.hash.slice(1) || "progress";
  if (!$(`nav button[data-tab="${t}"]`)?.classList.contains("active")) goTab(t);
});

// ---- progress
async function loadExercises() {
  const [exs, settings] = await Promise.all([api("/api/v1/exercises"), api("/api/v1/settings")]);
  state.exercises = exs;
  state.units = settings.units;
  $$(".unit").forEach(el => el.textContent = U());
  const active = $("#activeOnly").checked;
  const list = state.exercises.filter(e => !active || e.days_since_last <= 120);
  $("#exSelect").innerHTML = list.map(e =>
    `<option value="${esc(e.slug)}">${esc(e.exercise)} · ${e.sessions_total}${e.pending_reviews ? " ⚠" + e.pending_reviews : ""}</option>`).join("");
  if (!list.some(e => e.slug === state.ex)) state.ex = list[0]?.slug;
  $("#exSelect").value = state.ex;
  const pend = state.exercises.reduce((a, e) => a + e.pending_reviews, 0);
  $("#pendingBadge").textContent = pend; $("#pendingBadge").classList.toggle("hidden", !pend);
  await loadProgress();
}
$("#activeOnly").onchange = loadExercises;
$("#exSelect").onchange = e => { state.ex = e.target.value; localSet("ex", state.ex); loadProgress(); };
$$("#range button").forEach(b => b.onclick = () => {
  $$("#range button").forEach(x => x.classList.toggle("active", x === b)); state.range = +b.dataset.r; loadProgress();
});

async function loadProgress() {
  if (!state.ex) return;
  const since = state.range ? new Date(Date.now() - state.range * 864e5).toISOString().slice(0, 10) : "";
  const [series, ctx] = await Promise.all([
    api(`/api/v1/exercises/${state.ex}/series` + (since ? `?since=${since}` : "")),
    api(`/api/v1/exercises/${state.ex}/coach-context?recent=5`)]);
  renderTiles(ctx);
  renderCharts(series);
  renderSessions(series);
}

function renderTiles(c) {
  const s = c.summary, p = c.progression || {}, r = c.records || {};
  const trend = p.e1rm_trend_per_4wk_90d;
  const tiles = s.bodyweight ? [
    ["Best reps (last session)", fmt(s.current_max_reps), "bodyweight"],
    ["Sessions, last 30 d", s.sessions_last_30d, `${s.sessions_last_90d} in 90 d`],
    ["Last trained", s.last_date, `${s.days_since_last} days ago`],
  ] : [
    ["Current top set", fmt(p.current_top_weight) + " " + U(), `all-time ${fmt(p.all_time_top_weight)} ${U()}`],
    ["Best e1RM", fmt(r.best_e1rm?.e1rm) + " " + U(), `${r.best_e1rm?.from_set} on ${r.best_e1rm?.date}`],
    ["e1RM trend (90 d)", trend == null ? "—" : (trend > 0 ? "+" : "") + fmt(trend), "lb per 4 weeks"],
    ["Sessions, last 30 d", s.sessions_last_30d, `${s.sessions_last_90d} in 90 d · last ${s.days_since_last} d ago`],
  ];
  if (s.pending_reviews) tiles.push(["Needs review", "⚠ " + s.pending_reviews, "flagged sessions excluded from stats"]);
  $("#tiles").innerHTML = tiles.map(([k, v, sub]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${esc(sub)}</div></div>`).join("");
}

function baseOpts(yTitle) {
  const grid = css("--grid"), muted = css("--text-muted");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false }, tooltip: {
      backgroundColor: css("--surface-1"), titleColor: css("--text-primary"), bodyColor: css("--text-secondary"),
      borderColor: css("--axis"), borderWidth: 1, padding: 10, boxPadding: 4,
      callbacks: { title: items => { const p = items[0].raw.p; return `${p.date} · ${p.workout}`; } } } },
    scales: {
      x: { type: "linear", grid: { display: false }, border: { color: css("--axis") },
           ticks: { color: muted, maxTicksLimit: 7, callback: v => new Date(v).toLocaleDateString(undefined, { month: "short", year: "2-digit" }) } },
      y: { grid: { color: grid }, border: { display: false }, ticks: { color: muted }, title: { display: !!yTitle, text: yTitle, color: muted } } },
  };
}

function renderCharts(series) {
  Object.values(state.charts).forEach(c => c.destroy());
  const pts = series.points.filter(p => p.status !== "exclude");
  const t = p => new Date(p.started_at.replace(" ", "T")).getTime();
  const s1 = css("--series-1"), s2 = css("--series-2"), warn = css("--warning"), surf = css("--surface-1");
  const bw = series.bodyweight;
  const flagged = p => p.status === "pending";

  const clean = pts.filter(p => !flagged(p)), review = pts.filter(flagged);
  const val = p => bw ? p.max_reps : p.top_weight;
  const sets = (bw ? [
    { label: "Max reps", data: clean.map(p => ({ x: t(p), y: p.max_reps, p })), borderColor: s1 },
  ] : [
    { label: "Top set", data: clean.map(p => ({ x: t(p), y: p.top_weight, p })), borderColor: s1 },
    { label: "Estimated 1RM", data: clean.map(p => ({ x: t(p), y: p.e1rm, p })), borderColor: s2 },
  ]).map(d => Object.assign(d, { borderWidth: 2, tension: 0.15, backgroundColor: d.borderColor,
    pointRadius: 0, pointHoverRadius: 5, pointBorderColor: surf, pointBorderWidth: 2 }));
  if (review.length) sets.push({ label: "Needs review (as logged)", type: "scatter",
    data: review.map(p => ({ x: t(p), y: val(p), p })), pointRadius: 5, pointHoverRadius: 7,
    pointBorderColor: warn, pointBorderWidth: 2, pointBackgroundColor: surf, borderColor: warn, showLine: false });
  $("#mainTitle").textContent = bw ? "Max reps per session" : `Top set & estimated 1RM (${U()})`;
  $("#mainLegend").innerHTML = sets.filter(d => d.type !== "scatter").map(d => `<span><i style="background:${d.borderColor}"></i>${d.label}</span>`).join("") +
    (review.length ? `<span><i class="dot"></i>needs review, as logged (left out of the trend)</span>` : "");

  const o = baseOpts("");
  const xs = pts.map(t); o.scales.x.min = Math.min(...xs); o.scales.x.max = Math.max(...xs);
  o.plugins.tooltip.callbacks.afterBody = items => {
    const p = items[0].raw.p, lines = [p.sets];
    if (p.status) lines.push(statusTag(p) + " · " + statusTitle(p));
    return lines;
  };
  o.plugins.tooltip.callbacks.label = i => ` ${i.dataset.label}: ${fmt(i.parsed.y)}${bw ? " reps" : " " + U()}` +
    (i.dataset.label === "Top set" ? ` × ${i.raw.p.top_set_reps}` : "");
  state.charts.main = new Chart($("#mainChart"), { type: "line", data: { datasets: sets }, options: o });

  const vo = baseOpts("");
  Object.assign(vo.scales.x, { min: o.scales.x.min, max: o.scales.x.max, offset: false });
  vo.plugins.tooltip.callbacks.label = i => ` ${bw ? "Total reps" : "Volume"}: ${fmt(i.parsed.y)}`;
  $("#volTitle").textContent = bw ? "Total reps per session" : `Volume per session (${U()} × reps)`;
  state.charts.vol = new Chart($("#volChart"), { type: "bar", data: { datasets: [{
    data: pts.map(p => ({ x: t(p), y: bw ? p.total_reps : p.volume, p })),
    backgroundColor: pts.map(p => flagged(p) ? warn : s1), borderRadius: 2, barThickness: "flex", maxBarThickness: 8 }] }, options: vo });
}

function renderSessions(series) {
  const tagged = series.points.filter(p => p.status).length;
  $("#sessCount").textContent = `· ${series.points.length} in range` + (tagged ? ` · ${tagged} with review tags` : "");
  $("#sessTable").innerHTML = series.points.slice().reverse().map(p => `<tr>
    <td>${p.date}</td><td>${esc(p.workout)}</td>
    <td>${p.top_weight == null ? fmt(p.max_reps) + " reps" : fmt(p.top_weight) + " × " + p.top_set_reps}</td>
    <td>${fmt(p.e1rm)}</td><td class="sets">${esc(p.sets)}</td>
    <td>${p.status ? `<span class="pill ${p.status}" title="${esc(statusTitle(p))}">${esc(statusTag(p))}</span>` : ""}</td></tr>`).join("");
}

// ---- review
let revStatus = "pending";
$$("#revStatus button").forEach(b => b.onclick = () => {
  $$("#revStatus button").forEach(x => x.classList.toggle("active", x === b)); revStatus = b.dataset.s; loadReview();
});
$("#revExercise").onchange = loadReview;

async function loadReview() {
  // Fetch everything for the current status tab, build the exercise filter from it, filter client-side.
  const res = await api(`/api/v1/anomalies?status=${revStatus}`);
  const counts = new Map();
  res.items.forEach(a => counts.set(a.slug, { name: a.exercise, n: (counts.get(a.slug)?.n || 0) + 1 }));
  const sel = $("#revExercise"), prev = sel.value;
  sel.innerHTML = `<option value="">All exercises (${res.items.length})</option>` +
    [...counts].sort((a, b) => b[1].n - a[1].n)
      .map(([slug, c]) => `<option value="${esc(slug)}">${esc(c.name)} (${c.n})</option>`).join("");
  sel.value = counts.has(prev) ? prev : "";
  const ex = sel.value;
  res.items = ex ? res.items.filter(a => a.slug === ex) : res.items;
  state.defaultBar = res.default_bar_weight; state.anomalies = res.items;
  if (!$("#bulkBar_lb").value) $("#bulkBar_lb").value = state.defaultBar;
  $("#selAll").checked = false;
  $("#revTable").innerHTML = res.items.length ? res.items.map((a, i) => `<tr data-i="${i}">
    <td><input type="checkbox" class="sel"></td>
    <td>${a.date}<div class="muted" style="font-size:12px">${esc(a.workout_name)}</div></td>
    <td>${esc(a.exercise)}</td>
    <td class="sets">${esc(a.logged.sets.join(", "))}${a.baseline ? `<div class="muted">usual top ≈ ${fmt(a.baseline.top_weight)} ${U()}</div>` : ""}</td>
    <td class="reason">${a.reasons.map(esc).join("<br>") || "<span class='muted'>no longer flagged</span>"}</td>
    <td style="white-space:nowrap">
      <select class="dec">
        ${DECISIONS.map(([v, l]) => `<option value="${v}" ${a.status === v ? "selected" : ""}>${l}</option>`).join("")}
      </select>
      <span class="barwrap ${a.status in BAR_SIGN ? "" : "hidden"}"><input type="number" class="bar" step="0.5" style="width:70px" value="${a.bar_weight ?? state.defaultBar}"> ${U()}</span>
      <div class="muted preview" style="font-size:12px"></div>
    </td></tr>`).join("") : `<tr><td colspan="6" class="muted">Nothing to review 🎉</td></tr>`;
  $$("#revTable tr[data-i]").forEach(tr => {
    const a = state.anomalies[+tr.dataset.i], dec = tr.querySelector(".dec"), bar = tr.querySelector(".bar");
    const preview = () => {
      const top = a.logged.top_weight;
      const sign = BAR_SIGN[dec.value];
      tr.querySelector(".preview").textContent = sign && top ? `top → ${fmt(top + sign * (+bar.value || 0))} ${U()}` : "";
    };
    preview();
    dec.onchange = () => { tr.querySelector(".barwrap").classList.toggle("hidden", !(dec.value in BAR_SIGN)); preview(); save([{ a, status: dec.value, bar: +bar.value }]); };
    bar.onchange = () => { preview(); save([{ a, status: dec.value, bar: +bar.value }]); };
    tr.querySelector(".sel").onchange = updateSel;
  });
  updateSel();
}
function updateSel() { $("#selCount").textContent = $$("#revTable .sel:checked").length; }
$("#selAll").onchange = e => { $$("#revTable .sel").forEach(c => c.checked = e.target.checked); updateSel(); };
$("#bulkApply").onclick = () => {
  const rows = $$("#revTable tr[data-i]").filter(tr => tr.querySelector(".sel").checked);
  if (!rows.length) return toast("Select rows first");
  save(rows.map(tr => ({ a: state.anomalies[+tr.dataset.i], status: $("#bulkStatus").value, bar: +$("#bulkBar_lb").value })), true);
};
async function save(items, reload = false) {
  await api("/api/v1/anomalies", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(items.map(({ a, status, bar }) => ({ started_at: a.started_at, exercise: a.exercise, status,
      bar_weight: status in BAR_SIGN ? bar : null }))) });
  toast(`Saved ${items.length} decision${items.length > 1 ? "s" : ""}`);
  // decisions change the baseline, so other flags may appear/disappear
  await loadExercises();
  if (reload) loadReview();
}

// ---- data
async function loadImports() {
  const rows = await api("/api/v1/imports");
  $("#impTable").innerHTML = rows.map(r => `<tr><td>${r.id}</td><td>${esc(r.filename)}</td><td>${r.imported_at}</td>
    <td>${r.workouts_total}</td><td>${r.workouts_new}</td><td>${r.workouts_changed}</td><td>${r.workouts_missing}</td></tr>`).join("");
}
async function showImport(p) {
  try { const r = await p; $("#importResult").textContent = JSON.stringify(r, null, 2); await loadExercises(); loadImports(); }
  catch (e) { $("#importResult").textContent = "Error: " + e.message; }
}
$("#uploadBtn").onclick = () => {
  const f = $("#file").files[0]; if (!f) return toast("Choose a CSV first");
  const fd = new FormData(); fd.append("file", f);
  showImport(api("/api/v1/import", { method: "POST", body: fd }));
};
$("#scanBtn").onclick = () => showImport(api("/api/v1/import/scan", { method: "POST" }));

// ---- API & skill (rendered from /api/v1/catalog, the same source as the skill's api.md)
let apiLoaded = false;
$("#toApi").onclick = e => { e.preventDefault(); $('nav button[data-tab="api"]').click(); };
$("#skillView").onclick = async () => {
  const pre = $("#skillText");
  if (pre.classList.toggle("hidden")) return;
  if (!pre.textContent) pre.textContent = await (await fetch("/api/v1/skill.md")).text();
};

function curlFor(e, base) {
  if (e.method === "GET") return `curl -s '${base}${e.example || e.path}'`;
  if (e.path.endsWith("/import")) return `curl -s -F 'file=@strong.csv' '${base}${e.path}'`;
  if (e.method === "PUT") return `curl -s -X PUT '${base}${e.path}' -H 'Content-Type: application/json' ` +
    `-d '[{"started_at":"2026-10-15 04:50:15","exercise":"bench-press-barbell","status":"bar_included","bar_weight":45}]'`;
  return `curl -s -X ${e.method} '${base}${e.path}'`;
}

async function loadApiDocs() {
  if (apiLoaded) return;
  const cat = await api("/api/v1/catalog");
  apiLoaded = true;
  const base = cat.base_url;
  $("#skillBase").textContent = base;
  const md = s => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  $("#conventions").innerHTML = cat.conventions.map(c => `<li>${md(c)}</li>`).join("");
  let group = null, html = "";
  cat.endpoints.forEach((e, i) => {
    if (e.group !== group) { group = e.group; html += `<h2 class="group">${esc(group)}</h2>`; }
    const curl = curlFor(e, base);
    html += `<div class="card endpoint">
      <div class="ep-head"><span class="method ${e.method}">${e.method}</span><code class="path">${esc(e.path)}</code></div>
      <p class="ep-sum">${esc(e.summary)}</p>
      <p class="muted"><b>Use for:</b> ${esc(e.use_for)}</p>
      ${e.params.length ? `<table class="params"><thead><tr><th>Param</th><th>Type</th><th>Description</th></tr></thead><tbody>
        ${e.params.map(([n, t, d]) => `<tr><td><code>${esc(n)}</code></td><td class="muted">${esc(t)}</td><td>${esc(d)}</td></tr>`).join("")}
        </tbody></table>` : ""}
      <div class="returns"><b>Returns</b><ul>${e.returns.map(r => `<li>${esc(r)}</li>`).join("")}</ul></div>
      <pre class="code">${esc(curl)}</pre>
      <div class="row" style="margin:6px 0 0">
        <button class="btn copy" data-curl="${esc(curl)}">Copy curl</button>
        ${e.method === "GET" && e.example ? `<button class="btn try" data-url="${esc(e.example)}" data-i="${i}">Try it</button>` : ""}
      </div>
      <pre class="code out hidden" id="out${i}"></pre>
    </div>`;
  });
  $("#endpoints").innerHTML = html;
  $$("#endpoints .copy").forEach(b => b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.curl); toast("Copied"); }
    catch { toast("Copy failed; select the text instead"); }
  });
  $$("#endpoints .try").forEach(b => b.onclick = async () => {
    const out = $("#out" + b.dataset.i);
    if (!out.classList.contains("hidden")) { out.classList.add("hidden"); b.textContent = "Try it"; return; }
    out.textContent = "Loading…"; out.classList.remove("hidden"); b.textContent = "Hide";
    const r = await fetch(b.dataset.url), text = await r.text();
    let body = text;
    try { body = JSON.stringify(JSON.parse(text), null, 2); } catch { /* CSV or text */ }
    const lines = body.split("\n");
    out.textContent = `${r.status} · ${(text.length / 1024).toFixed(1)} KB\n\n` + lines.slice(0, 80).join("\n") +
      (lines.length > 80 ? `\n… ${lines.length - 80} more lines` : "");
  });
}

// ---- settings
$$(".toSettings").forEach(a => a.onclick = e => { e.preventDefault(); goTab("settings"); });
const KG_PER_LB = 0.45359237;
const formUnit = () => $('input[name="units"]:checked')?.value || U();
function settingsHint() {
  const u = formUnit(), inc = $('input[name="barTracking"]:checked')?.value === "included";
  const w = +$("#barWeight").value || 0, step = u === "kg" ? 2.5 : 5;
  const [total, logged] = u === "kg" ? [100, 90] : [225, 205];
  const r = x => Math.round(x / step) * step;
  $$(".formUnit").forEach(el => el.textContent = u);
  $("#barExample").textContent = u === "kg" ? "A 20 kg bar with 20 kg on each side → " + (inc ? "60" : "40") + "."
                                            : "A 45 lb bar with a 45 on each side → " + (inc ? "135" : "90") + ".";
  $("#settingsHint").textContent = inc
    ? `Program weights are percentages of your logged numbers. Example: 80% of a logged ${total} → ${fmt(r(0.8 * total))} ${u}.`
    : `Program weights are percentages of the actual load (logged + ${fmt(w)} ${u} bar). Example: 80% of a logged ${logged} → `
      + `${fmt(r(0.8 * (logged + w) - w))} ${u} (not ${fmt(r(0.8 * logged))}).`;
}
async function loadSettings() {
  const s = await api("/api/v1/settings");
  $$('input[name="units"]').forEach(r => r.checked = r.value === s.units);
  $$('input[name="barTracking"]').forEach(r => r.checked = r.value === s.bar_tracking);
  $("#barWeight").value = s.bar_weight;
  settingsHint();
}
$$('input[name="units"]').forEach(r => r.onchange = () => {
  // convert the bar weight shown so switching units doesn't silently change it
  const w = +$("#barWeight").value || 0;
  $("#barWeight").value = r.value === "kg" ? +(w * KG_PER_LB).toFixed(2) : +(w / KG_PER_LB).toFixed(1);
  settingsHint();
});
$$('input[name="barTracking"]').forEach(r => r.onchange = settingsHint);
$("#barWeight").oninput = settingsHint;
$("#saveSettings").onclick = async () => {
  const body = { units: formUnit(), bar_tracking: $('input[name="barTracking"]:checked').value,
                 bar_weight: +$("#barWeight").value };
  const unitsChanged = body.units !== U();
  try {
    await api("/api/v1/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast("Settings saved"); apiLoaded = false; $("#skillText").textContent = "";
    if (unitsChanged) { P.overrides = {}; await loadExercises(); }   // every number changes
  } catch (e) { toast("Could not save: " + e.message.slice(0, 120)); }
};

// ---- programs
const P = { list: [], current: null, editingNew: false };
const jsonPut = (url, body, method = "PUT") =>
  api(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

async function loadPrograms() {
  P.list = await api("/api/v1/programs");
  const opts = P.list.map(p => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join("");
  const keep = $("#progSelect").value;
  $("#progSelect").innerHTML = opts || `<option value="">No programs yet</option>`;
  if (P.list.some(p => p.slug === keep)) $("#progSelect").value = keep;
  $("#afProgram").innerHTML = opts;
  $("#afExercise").innerHTML = state.exercises.map(e => `<option value="${esc(e.slug)}">${esc(e.exercise)}</option>`).join("");
  if (!$("#afStart").value) $("#afStart").value = new Date().toISOString().slice(0, 10);
  syncAssignExercise();
  await Promise.all([loadAssignments(), showProgram()]);
}

function syncAssignExercise() {
  const p = P.list.find(x => x.slug === $("#afProgram").value);
  const mapped = p && state.exercises.find(e => e.exercise === p.default_lift_mapping[p.primary_lift]);
  if (mapped) $("#afExercise").value = mapped.slug;
}
$("#afProgram").onchange = syncAssignExercise;

async function loadAssignments() {
  const list = await api("/api/v1/assignments");
  if (!list.length) { $("#assignList").innerHTML = `<p class="muted">You're not following a program yet. Use “Start following a program” below.</p>`; return; }
  const cards = await Promise.all(list.map(async a => {
    const prog = P.list.find(p => p.slug === a.program);
    const ex = prog ? a.exercise_map[prog.primary_lift] : Object.values(a.exercise_map)[0];
    const exSlug = state.exercises.find(e => e.exercise === ex)?.slug;
    let ad = null;
    if (exSlug && !a.end_date) {
      const ctx = await api(`/api/v1/exercises/${exSlug}/program-context?recent=1`);
      ad = ctx.programs.find(x => x.assignment.id === a.id)?.adherence;
    }
    const e1 = ad?.e1rm || {};
    return `<div class="assign">
      <div class="ep-head"><b>${esc(prog?.name || a.program)}</b><span class="muted">${esc(ex || "")} · since ${a.start_date}${a.end_date ? ` · ended ${a.end_date}` : ""}</span></div>
      ${ad ? `<div class="tiles small">
        <div class="tile"><div class="k">Sessions / week</div><div class="v">${fmt(ad.actual_sessions_per_week)}</div><div class="s">program: ${fmt(ad.expected_sessions_per_week)}</div></div>
        <div class="tile"><div class="k">As prescribed</div><div class="v">${ad.adherence_pct ?? "—"}%</div><div class="s">${Object.entries(ad.verdict_counts).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(" · ")}</div></div>
        <div class="tile"><div class="k">e1RM since start</div><div class="v">${e1.change_since_start == null ? "—" : (e1.change_since_start > 0 ? "+" : "") + fmt(e1.change_since_start)}</div><div class="s">${fmt(e1.best_in_30d_before_start)} → ${fmt(e1.best_since_start)} ${U()}</div></div>
        <div class="tile"><div class="k">Re-test</div><div class="v">${ad.retest_due ? "⚠ due" : "not yet"}</div><div class="s">${fmt(ad.weeks_on_program)} weeks in</div></div>
      </div>
      <div class="muted" style="font-size:13px"><b>Next:</b> ${esc(ad.upcoming[0]?.position)}: <code>${esc(ad.upcoming[0]?.compact)}</code></div>` : ""}
      <div class="row" style="margin:8px 0 0">
        ${a.end_date ? "" : `<button class="btn" data-end="${a.id}">End today</button>`}
        <button class="btn" data-del="${a.id}">Delete</button>
      </div></div>`;
  }));
  $("#assignList").innerHTML = cards.join("");
  $$("#assignList [data-end]").forEach(b => b.onclick = async () => {
    await jsonPut(`/api/v1/assignments/${b.dataset.end}`, { end_date: new Date().toISOString().slice(0, 10) }, "PATCH");
    toast("Ended"); loadAssignments();
  });
  $$("#assignList [data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Delete this assignment? (The program itself is kept.)")) return;
    await api(`/api/v1/assignments/${b.dataset.del}`, { method: "DELETE" }); toast("Deleted"); loadPrograms();
  });
}

$("#afSave").onclick = async () => {
  const p = P.list.find(x => x.slug === $("#afProgram").value);
  if (!p) return toast("Pick a program");
  const ex = state.exercises.find(e => e.slug === $("#afExercise").value);
  const basis = $("#afBasis").value;
  try {
    await jsonPut("/api/v1/assignments", {
      program: p.slug, start_date: $("#afStart").value,
      exercise_map: { [p.primary_lift]: ex.exercise },
      basis: basis ? { [p.primary_lift]: +basis } : {}, notes: $("#afNotes").value || null }, "POST");
    toast("Started"); $("#assignForm").open = false; loadPrograms();
  } catch (e) { toast("Could not save: " + e.message.slice(0, 120)); }
};

async function showProgram() {
  const slug = $("#progSelect").value;
  if (!slug) { $("#progView").innerHTML = `<p class="muted">No programs yet. Click “New program”.</p>`; return; }
  const ov = P.overrides?.[slug] || {};
  const q = new URLSearchParams();
  if (ov.exercise) q.set("exercise", ov.exercise);
  if (ov.basis) q.set("basis", ov.basis);
  const [prog, plan] = await Promise.all([api(`/api/v1/programs/${slug}`), api(`/api/v1/programs/${slug}/plan?${q}`)]);
  P.current = prog;
  const lifts = plan.program.lifts;
  const exOpts = sel => `<option value="">(not mapped)</option>` + state.exercises.map(e =>
    `<option value="${esc(e.slug)}" ${e.exercise === sel ? "selected" : ""}>${esc(e.exercise)}</option>`).join("");
  $("#progView").innerHTML = `
    <h2>${esc(prog.name)}</h2>
    <p>${esc(prog.description)}</p>
    ${prog.source ? `<p class="muted" style="font-size:12px">Source: ${esc(prog.source)}</p>` : ""}
    ${prog.progression ? `<p><b>Progression:</b> ${esc(prog.progression)}</p>` : ""}
    ${prog.notes ? `<p class="muted" style="font-size:13px">${esc(prog.notes)}</p>` : ""}
    <h3>Weights for</h3>
    <table class="params"><thead><tr><th>Lift</th><th>Exercise</th><th>${prog.basis === "training_max" ? "Training max" : "1RM"} (${U()})</th><th class="muted">How</th></tr></thead><tbody>
      ${lifts.map(l => { const b = plan.basis[l]; return `<tr><td><code>${esc(l)}</code></td>
        <td><select data-lift="${esc(l)}" class="mapEx">${exOpts(b.exercise)}</select></td>
        <td><input type="number" step="0.5" data-lift="${esc(l)}" class="basisIn" style="width:90px" value="${b.lb ?? ""}" placeholder="—"></td>
        <td class="muted" style="font-size:12px">${esc(b.how)}</td></tr>`; }).join("")}
    </tbody></table>
    <p class="muted" style="font-size:12px">${esc(plan.convention)}</p>
    ${plan.weeks.map(w => w.days.map(d => `
      <div class="day"><h3>Week ${w.week}${w.label ? ` · ${esc(w.label)}` : ""} · Day ${d.day}${d.label ? ` · ${esc(d.label)}` : ""}</h3>
      ${d.exercises.map(e => `<div class="dayex"><span class="lift">${esc(e.exercise || e.lift)}${e.note ? ` <span class="muted">(${esc(e.note)})</span>` : ""}</span>
        <span class="chips">${e.sets.map(s => `<span class="chip${s.warmup ? " wu" : ""}" title="${s.pct != null ? s.pct + "%" : ""}${s.actual_load ? ` · ${s.actual_load} ${U()} on the bar` : ""}">${s.weight != null ? fmt(s.weight) : "BW"}×${esc(s.reps)}${s.pct != null ? `<small>${fmt(s.pct)}%</small>` : ""}</span>`).join("")}</span></div>`).join("")}
      </div>`).join("")).join("")}`;
  const update = () => {
    const pairs = cls => $$(`#progView .${cls}`).filter(i => i.value).map(i => `${i.dataset.lift}:${i.value}`).join(",");
    P.overrides = { ...(P.overrides || {}), [slug]: { exercise: pairs("mapEx"), basis: pairs("basisIn") } };
    showProgram();
  };
  $$("#progView .mapEx, #progView .basisIn").forEach(i => i.onchange = update);
}
$("#progSelect").onchange = showProgram;

const NEW_PROGRAM = {
  slug: "my-program", name: "My program", description: "", source: "",
  lifts: { main: "Bench Press (Barbell)" }, basis: "1rm", training_max_pct: 100, rounding_lb: 5,
  min_weeks: 4, progression: "", notes: "",
  weeks: [{ week: 1, days: [{ day: 1, label: "Day 1", exercises: [{ lift: "main", sets: [
    { sets: 1, reps: 10, pct: 50, warmup: true }, { sets: 3, reps: 5, pct: 75 }, { sets: 1, reps: 5, pct: 80, amrap: true }] }] }] }],
};
function openEditor(obj, isNew) {
  P.editingNew = isNew;
  const { prescription, ...clean } = obj;
  $("#progEditorTitle").textContent = isNew ? "New program" : `Edit: ${obj.name}`;
  $("#progJson").value = JSON.stringify(clean, null, 2);
  $("#progErr").classList.add("hidden");
  $("#progDelete").classList.toggle("hidden", isNew);
  $("#progEditor").classList.remove("hidden");
  $("#progEditor").scrollIntoView({ behavior: "smooth" });
}
$("#progNewBtn").onclick = () => openEditor(NEW_PROGRAM, true);
$("#progEditBtn").onclick = () => P.current && openEditor(P.current, false);
$("#progCancel").onclick = () => $("#progEditor").classList.add("hidden");
$("#progSave").onclick = async () => {
  let obj;
  try { obj = JSON.parse($("#progJson").value); }
  catch (e) { $("#progErr").textContent = "Invalid JSON: " + e.message; $("#progErr").classList.remove("hidden"); return; }
  if (P.editingNew && P.list.some(p => p.slug === obj.slug) && !confirm(`A program "${obj.slug}" exists. Replace it?`)) return;
  try {
    await jsonPut(`/api/v1/programs/${encodeURIComponent(obj.slug)}`, obj);
    toast("Program saved"); $("#progEditor").classList.add("hidden");
    await loadPrograms(); $("#progSelect").value = obj.slug; showProgram();
  } catch (e) {
    let msg = e.message;
    try { msg = JSON.parse(msg).detail.map?.(d => `${d.loc.join(".")}: ${d.msg}`).join("\n") ?? JSON.parse(msg).detail; } catch {}
    $("#progErr").textContent = msg; $("#progErr").classList.remove("hidden");
  }
};
$("#progDelete").onclick = async () => {
  const slug = P.current?.slug;
  if (!slug || !confirm(`Delete program "${slug}"? This removes programs/${slug}.json.`)) return;
  try { await api(`/api/v1/programs/${slug}`, { method: "DELETE" }); toast("Deleted"); $("#progEditor").classList.add("hidden"); $("#progSelect").value = ""; loadPrograms(); }
  catch (e) { toast(e.message.includes("409") || e.message.includes("assignment") ? "In use by an assignment; end/delete that first" : "Delete failed"); }
};

// ---- improvement plans
const PL = { list: [], id: +localGet("plan") || null, plan: null, prog: null };
const VERDICT = { as_prescribed: "as prescribed", heavier: "heavier", lighter: "lighter", fewer_reps: "fewer reps",
                  extra: "extra session", outside_phases: "between phases", logged: "logged" };
// Tiny markdown: paragraphs, "- " bullets, **bold**, `code`.
function mdLite(src) {
  const inline = t => esc(t).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", list = false;
  for (const line of String(src || "").split("\n")) {
    const li = line.match(/^\s*[-*] (.*)/);
    if (li) { if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(li[1])}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (list ? "</ul>" : "");
}
const chipsFor = sets => `<span class="chips">${sets.map(s => `<span class="chip${s.warmup ? " wu" : ""}" title="${s.pct != null ? s.pct + "%" : ""}${s.actual_load ? ` · ${s.actual_load} ${U()} on the bar` : ""}${s.note ? " · " + esc(s.note) : ""}">${s.weight != null ? fmt(s.weight) + "×" : ""}${esc(s.reps)}${s.weight == null ? " reps" : ""}${s.pct != null ? `<small>${fmt(s.pct)}%</small>` : ""}</span>`).join("")}</span>`;
const dayHtml = (d, doneLabel) => `<div class="day"><h3>${esc(d.label || "Day " + d.day)}${doneLabel ? `<span class="done">✓ ${esc(doneLabel)}</span>` : ""}</h3>
  ${d.note ? `<p class="muted" style="font-size:12px;margin:0 0 4px">${esc(d.note)}</p>` : ""}
  ${d.exercises.map(e => `<div class="dayex"><span class="lift">${esc(e.exercise)}</span>${chipsFor(e.sets)}
    ${e.note ? `<span class="exnote">${esc(e.note)}</span>` : ""}</div>`).join("")}</div>`;

async function loadPlans() {
  PL.list = await api("/api/v1/plans");
  const has = PL.list.length > 0;
  $("#planEmpty").classList.toggle("hidden", has);
  $("#planBody").classList.toggle("hidden", !has);
  $$("#planComplete, #planAbandon, #planDelete, #planSelect, #planStatus").forEach(el => el.classList.toggle("hidden", !has));
  if (!has) return;
  if (!PL.list.some(p => p.id === PL.id)) PL.id = PL.list[0].id;
  $("#planSelect").innerHTML = PL.list.map(p => `<option value="${p.id}">${esc(p.name)} · ${p.status}</option>`).join("");
  $("#planSelect").value = PL.id;
  await showPlan();
}
$("#planSelect").onchange = e => { PL.id = +e.target.value; localSet("plan", PL.id); showPlan(); };

async function showPlan() {
  const [plan, prog] = await Promise.all([api(`/api/v1/plans/${PL.id}`), api(`/api/v1/plans/${PL.id}/progress`)]);
  PL.plan = plan; PL.prog = prog;
  $$(".unit").forEach(el => el.textContent = U());
  $("#planStatus").textContent = plan.status; $("#planStatus").className = "pill " + plan.status;
  $("#planComplete").classList.toggle("hidden", plan.status === "completed");
  $("#planAbandon").classList.toggle("hidden", plan.status === "abandoned");
  $("#planName").textContent = plan.name;
  $("#planGoal").textContent = plan.goal;
  $("#planMeta").textContent = `${plan.start_date} → ${plan.end_date} · ${plan.exercises.join(", ")}` +
    (plan.author ? ` · by ${plan.author}` : "") + ` · updated ${plan.updated_at}`;
  renderPlanTiles(plan, prog);
  renderTimeline(plan, prog);
  renderPlanWeek(prog);
  $("#planDiagnosis").innerHTML = mdLite(plan.diagnosis) || `<p class="muted">—</p>`;
  $("#planGuardrails").innerHTML = plan.guardrails.map(g => `<li>${esc(g)}</li>`).join("") || `<li class="muted">—</li>`;
  $("#planMaxes").innerHTML = plan.maxes.map(m => `<tr><td>${m.date}</td><td>${esc(m.exercise)}</td><td>${fmt(m.value)}</td><td class="muted">${esc(m.note || "")}</td></tr>`).join("");
  const maxEx = [...new Set(plan.maxes.map(m => m.exercise))];
  $("#pmExercise").innerHTML = maxEx.map(e => `<option>${esc(e)}</option>`).join("");
  if (!$("#pmDate").value) $("#pmDate").value = new Date().toISOString().slice(0, 10);
  const logEx = Object.keys(prog.exercises), keep = $("#planLogEx").value;
  $("#planLogEx").innerHTML = logEx.map(e => `<option>${esc(e)}</option>`).join("");
  $("#planLogEx").value = logEx.includes(keep) ? keep : logEx[0];
  renderPlanLog();
  $("#planMatchNote").textContent = prog.matching_note;
  $("#planConvention").textContent = prog.convention + " Hover a set for % and the load on the bar.";
  $("#planPhases").innerHTML = plan.phases.map((ph, i) => `<details class="phase" ${prog.current?.phase_index === i ? "open" : ""}>
    <summary>${esc(ph.name)} <span class="muted" style="font-weight:400">· ${ph.kind} · ${ph.start_date} → ${ph.end_date}</span></summary>
    ${ph.note ? `<p class="muted" style="font-size:13px">${esc(ph.note)}</p>` : ""}
    ${ph.progression ? `<p style="font-size:13px"><b>Progression:</b> ${esc(ph.progression)}</p>` : ""}
    ${ph.weeks.map(w => `<h3>${esc(w.label || "Week " + w.week)} <span class="muted" style="font-weight:400">· from ${w.starts}</span></h3>
      ${w.days.map(d => dayHtml(d)).join("")}`).join("")}</details>`).join("");
}

function renderPlanTiles(plan, prog) {
  const g = prog.goals[0], tiles = [];
  if (g) {
    const pct = Math.max(0, Math.min(100, g.progress_pct ?? 0));
    tiles.push([`Goal · ${g.exercise}`, `${fmt(g.current_max)} → ${fmt(g.target)} ${U()}`,
      `started at e1RM ${fmt(g.baseline_e1rm_90d)} · ${g.progress_pct ?? 0}% of the way`, `<div class="goalbar"><i style="width:${pct}%"></i></div>`]);
    tiles.push(["Best since start", g.best_e1rm_since_start == null ? "—" : fmt(g.best_e1rm_since_start) + " " + U(),
      g.best_e1rm_from ? "e1RM from " + g.best_e1rm_from : "no sessions yet"]);
  }
  const c = prog.current;
  tiles.push(["Now", c ? `${c.phase} · wk ${c.week}/${c.weeks_in_phase}` : (prog.days_in < 0 ? "Not started" : "Between phases"),
    c ? `ends ${c.phase_ends}` + (prog.next_phase ? ` · next: ${prog.next_phase.name}` : "") :
        prog.next_phase ? `next: ${prog.next_phase.name} on ${prog.next_phase.starts}` : ""]);
  const main = prog.exercises[plan.exercises[0]];
  if (main) {
    tiles.push(["This week", `${main.this_week.done} / ${main.this_week.planned}`, `${plan.exercises[0]} sessions done / planned`]);
    tiles.push(["As prescribed", main.as_prescribed_pct == null ? "—" : main.as_prescribed_pct + "%",
      Object.entries(main.verdict_counts).map(([k, v]) => `${VERDICT[k] || k} ${v}`).join(" · ") || "no sessions yet"]);
  }
  $("#planTiles").innerHTML = tiles.map(([k, v, sub, extra]) =>
    `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(sub)}</div>${extra || ""}</div>`).join("");
}

function renderTimeline(plan, prog) {
  const day = s => new Date(s + "T00:00:00").getTime();
  const t0 = day(plan.start_date), t1 = day(plan.end_date) + 864e5, span = t1 - t0;
  $("#planTimeline").innerHTML = plan.phases.map(ph => {
    const w = (day(ph.end_date) + 864e5 - day(ph.start_date)) / span * 100;
    return `<div class="seg ${ph.kind}" style="flex:0 0 ${w}%" title="${esc(ph.name)}: ${ph.start_date} → ${ph.end_date}">${esc(ph.name)}</div>`;
  }).join("") + (() => {
    const now = day(prog.as_of);
    return now >= t0 && now < t1 ? `<div class="today" style="left:${(now - t0) / span * 100}%" title="Today ${prog.as_of}"></div>` : "";
  })();
  $("#planTimelineLegend").innerHTML = `<span>${plan.start_date}</span><span class="spacer"></span><span>today: ${prog.as_of}</span><span class="spacer"></span><span>${plan.end_date}</span>`;
}

function renderPlanWeek(prog) {
  const c = prog.current;
  if (!c) { $("#planWeekTitle").textContent = "This week"; $("#planWeekNote").textContent = prog.next_phase ? `Next: ${prog.next_phase.name} starts ${prog.next_phase.starts}.` : "The plan has ended."; $("#planWeek").innerHTML = ""; return; }
  $("#planWeekTitle").textContent = `This week · ${c.phase}, week ${c.week} (from ${c.week_starts})`;
  $("#planWeekNote").textContent = [c.note, c.progression && "Progression: " + c.progression].filter(Boolean).join(" ");
  // A day is done when a session of the plan's main exercise this week was matched to it.
  const ws = c.week_starts, we = new Date(new Date(ws + "T00:00:00").getTime() + 6 * 864e5).toISOString().slice(0, 10);
  const done = {};
  (prog.exercises[PL.plan.exercises[0]]?.sessions || []).forEach(s => {
    if (s.date >= ws && s.date <= we && s.day && !done[s.day]) done[s.day] = s.date;
  });
  $("#planWeek").innerHTML = c.days.map(d => dayHtml(d, done[d.label || "Day " + d.day] && "done " + done[d.label || "Day " + d.day])).join("");
}

function renderPlanLog() {
  const ex = PL.prog?.exercises[$("#planLogEx").value];
  const rows = (ex?.sessions || []).slice().reverse();
  $("#planLog").innerHTML = rows.length ? rows.map(r => `<tr><td>${r.date}</td>
    <td>${r.phase ? esc(`${r.phase} · wk ${r.week}${r.day ? " · " + r.day : ""}`) : "—"}</td>
    <td class="sets">${esc(r.prescribed || "")}</td><td class="sets">${esc(r.actual)}</td>
    <td><span class="pill ${r.verdict}" title="${r.top_vs_prescribed_pct ? `top ${r.top_vs_prescribed_pct}% of prescribed · ${r.actual_working_reps}/${r.prescribed_working_reps} working reps` : ""}">${VERDICT[r.verdict] || r.verdict}</span></td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">No sessions logged in the plan yet. Import a new Strong export to see them here.</td></tr>`;
}
$("#planLogEx").onchange = renderPlanLog;

async function setPlanStatus(status) {
  await jsonPut(`/api/v1/plans/${PL.id}`, { status }, "PATCH"); toast("Plan " + status); loadPlans();
}
$("#planComplete").onclick = () => setPlanStatus("completed");
$("#planAbandon").onclick = () => confirm("Mark this plan abandoned? (It's kept for history.)") && setPlanStatus("abandoned");
$("#planDelete").onclick = async () => {
  if (!confirm(`Delete “${PL.plan?.name}” permanently? Consider “Abandon” to keep its history.`)) return;
  await api(`/api/v1/plans/${PL.id}`, { method: "DELETE" }); toast("Deleted"); PL.id = null; loadPlans();
};
$("#pmSave").onclick = async () => {
  const value = +$("#pmValue").value;
  if (!value) return toast("Enter the max");
  try {
    await jsonPut(`/api/v1/plans/${PL.id}/maxes`, { exercise: $("#pmExercise").value, value, date: $("#pmDate").value }, "POST");
    toast("Max recorded; later weeks recalculated"); $("#pmValue").value = ""; $("#planMaxForm").open = false; showPlan();
  } catch (e) { toast("Could not save: " + e.message.slice(0, 120)); }
};

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", loadProgress);
loadExercises().then(() => { const t = location.hash.slice(1); if (t && t !== "progress") $(`nav button[data-tab="${t}"]`)?.click(); });
