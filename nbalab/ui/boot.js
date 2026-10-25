/* Boot: status bar, launchers, global search, layouts */
"use strict";
(async function boot() {
  const names = [["picks", "Picks"], ["workbench", "Workbench"], ["research", "Ask"], ["explorer", "Explorer"], ["monitor", "Monitor"], ["models", "Models"], ["audit", "Audit"], ["predlab", "Pred Lab"]];
  const L = $("#launchers"); names.forEach(([id, t]) => L.append(el("button", {"data-id": id, title: LAB.builders[id].title, onclick: () => LAB.panels[id] ? closePanel(id) : openPanel(id)}, t)));
  $("#tile").onclick = tile; $("#cascade").onclick = cascade;
  async function refresh() {
    try { const s = await api("/api/status"); LAB.status = s; $("#synth").style.display = s.synthetic ? "" : "none";
      $("#st-data").textContent = `${s.dataset_label} · ${s.players} players · ${s.games_final} games · through ${s.last_game_date}`; $("#st-clock").textContent = "lab clock " + s.lab_clock + (s.synthetic ? " (simulated)" : "");
      $("#st-champ").textContent = `champion ${s.champion} · ${s.projections_stored} projections · ${s.events_stored} reports`; $("#st-msg").textContent = ""; return s;
    } catch (e) { $("#st-msg").textContent = "API unreachable: " + e.message; throw e; }
  }
  await refresh(); setInterval(() => refresh().catch(() => {}), 20000);
  // global search
  const gs = $("#gsearch"), sr = $("#sresults"); let t = null;
  gs.addEventListener("input", () => { clearTimeout(t); t = setTimeout(async () => {
    if (gs.value.length < 2) { sr.style.display = "none"; return; }
    const r = await api("/api/search?q=" + encodeURIComponent(gs.value)); clear(sr);
    const sec = (title, items, fn) => { if (items.length) { sr.append(el("h4", null, title)); items.forEach((i) => sr.append(fn(i))); } };
    const done = () => { sr.style.display = "none"; gs.value = ""; };
    sec("Players", r.players, (p) => el("div", {class: "it", onclick: () => { openPanel("explorer", {type: "player", id: p.id}); done(); }}, p.name, el("span", {class: "muted"}, p.team + " " + p.position)));
    sec("Teams", r.teams, (p) => el("div", {class: "it", onclick: () => { openPanel("explorer", {type: "team", id: p.id}); done(); }}, p.name, el("span", {class: "muted"}, p.abbr)));
    sec("Coaches", r.coaches, (p) => el("div", {class: "it", onclick: () => { openPanel("research", {text: `${p.name} win rate`}); done(); }}, p.name, el("span", {class: "muted"}, "coach")));
    sec("Metrics", r.metrics, (p) => el("div", {class: "it", title: p.category, onclick: () => { openPanel("workbench"); toast(`Add "${p.name}" in the Workbench metric list`, true); done(); }}, p.name, kindBadge(p.kind)));
    sr.style.display = sr.children.length ? "block" : "none"; }, 160); });
  document.addEventListener("click", (e) => { if (!sr.contains(e.target) && e.target !== gs) sr.style.display = "none"; });
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); gs.focus(); } });
  // workspaces (window layouts)
  async function loadWs() {
    const sel = $("#wload"); while (sel.options.length > 1) sel.remove(1);
    (await api("/api/workspaces")).workspaces.filter((w) => w.spec.kind === "layout").forEach((w) => sel.append(el("option", {value: w.name}, w.name)));
  }
  $("#wsave").onclick = async () => { const m = modal("Save layout", (() => { const i = el("input", {placeholder: "layout name"}); return el("div", null, i, el("button", {class: "primary", onclick: async () => { if (!i.value) return;
    const lay = {}; for (const id in LAB.panels) { const p = LAB.panels[id]; lay[id] = {x: p.x, y: p.y, w: p.w, h: p.h, min: p.min}; }
    await api("/api/workspaces", {body: {name: i.value, spec: {kind: "layout", layout: lay}}}); toast("Layout saved", true); m.remove(); loadWs(); }}, "Save")); })()); };
  $("#wload").onchange = async (e) => { const r = await api("/api/workspaces"); const w = r.workspaces.find((x) => x.name === e.target.value); if (!w) return;
    Object.keys(LAB.panels).forEach(closePanel); Object.entries(w.spec.layout).forEach(([id, d]) => { if (LAB.builders[id]) { LAB.builders[id].def = Object.assign({}, LAB.builders[id].def, d); openPanel(id); } }); e.target.value = ""; };
  loadWs();
  // initial layout: restore or default
  const saved = (typeof loadLayout === "function") ? loadLayout() : {}; const ids = Object.keys(saved).filter((id) => LAB.builders[id]);
  if (ids.length) ids.forEach((id) => { LAB.builders[id].def = Object.assign({}, LAB.builders[id].def, saved[id]); openPanel(id); });
  else { openPanel("picks"); }
  syncLaunchers();
})().catch((e) => { const m = document.getElementById("st-msg"); if (m) m.textContent = "Startup error: " + e.message; console.error(e); });
