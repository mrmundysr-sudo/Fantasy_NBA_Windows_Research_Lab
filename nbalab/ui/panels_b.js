/* Panels: Player/Team Explorer, Live Monitor, Model Tournament, Audit, Prediction Lab */
"use strict";

function valuesTable(block, cols) {
  if (!block) return el("div", {class: "muted"}, "No data.");
  const rows = (cols || []).map((c) => ({label: c.label, kind: c.kind, unit: c.unit, v: block.values[c.label], ci: block.ci90 && block.ci90[c.label]}));
  return dataTable([{key: "label", label: "Metric"}, {key: "kind", label: "Kind", render: (r) => kindBadge(r.kind)}, {key: "v", label: "Value", num: true, render: (r) => fmt(r.v)},
    {key: "ci", label: "90% CI", render: (r) => r.ci ? el("span", {class: "muted small"}, ciText(r.ci)) : "–"}, {key: "unit", label: "Unit"}], rows, {sortKey: null});
}

/* ---------- Explorer ---------- */
registerPanel("explorer", "Player & Team Explorer", (body) => {
  const box = el("div"); body.append(box);
  async function show(p) {
    clear(box); box.append(el("span", {class: "spin"}), " loading…");
    try { const d = await api(`/api/${p.type}/${p.id}`); clear(box); p.type === "player" ? player(d) : team(d); } catch (e) { clear(box); box.append(el("div", {class: "banner bad"}, e.message)); }
  }
  function player(d) {
    box.append(el("div", {class: "row"}, el("h3", {style: {margin: 0}}, d.name), badge(d.position), entityLink("team", d.team.id, d.team.name), badge(`age ${d.age}`), badge(`${d.height_in} in`), synthBadge()));
    const t = tabs([
      ["season", "Season stats", () => { const w = el("div"); Object.keys(d.seasons).forEach((s) => w.append(el("h4", null, `${s} (${d.seasons[s].games} GP)`), valuesTable(d.seasons[s], d.seasons[s].columns))); w.append(el("h4", null, "Career"), valuesTable(d.career, d.seasons[Object.keys(d.seasons)[0]].columns)); return w; }],
      ["log", "Game log", () => dataTable([{key: "date", label: "Date"}, {key: "opp", label: "Opp", render: (r) => (r.home ? "vs " : "@ ") + r.opp}, {key: "result", label: "W/L"}, {key: "min", label: "Min", num: true}, {key: "pts", label: "PTS", num: true}, {key: "reb", label: "REB", num: true},
        {key: "ast", label: "AST", num: true}, {key: "stl", label: "STL", num: true}, {key: "blk", label: "BLK", num: true}, {key: "tov", label: "TOV", num: true}, {key: "fg", label: "FG"}, {key: "started", label: "GS", render: (r) => r.started ? "●" : ""}], d.game_log, {sortKey: "date", desc: true})],
      ["min", "Minutes trend", () => el("div", null, lineChart([{name: "minutes", points: d.minutes_series.map((m, i) => ({x: i + 1, y: m.min}))}], {xl: "game #", yl: "min"}), d.missed.length ? el("div", {class: "small muted"}, "Games missed: " + d.missed.length) : null)],
      ["proj", "Next projection", () => d.projection ? projectionView(d.projection) : el("div", {class: "muted"}, "No upcoming projection stored. Open Fantasy Picks to generate.")],
      ["style", "Inferred style", () => el("div", null, el("div", {class: "banner small"}, d.kinds_note), barChart(d.style_inferred.map((s) => ({label: `${s.trait}`, value: s.z})), {lw: 200, w: 520}))],
      ["ev", "Reports", () => dataTable([{key: "published_at", label: "Published"}, {key: "source", label: "Source"}, {key: "status", label: "Status"}, {key: "detail", label: "Detail"}], d.events || [], {sortKey: "published_at", desc: true, empty: "No reports stored for this player."})]]);
    box.append(t.root);
  }
  function projectionView(p) {
    const rows = Object.entries(p.stats).map(([k, s]) => ({stat: k.toUpperCase(), mean: s.mean, sd: s.sd, p10: s.p10, p50: s.p50, p90: s.p90}));
    return el("div", null, el("div", {class: "banner small " + (LAB.status.synthetic ? "warn" : "")}, `${p.date} ${p.home ? "vs" : "@"} ${p.opponent} · P(play) ${fmt(p.p_play, 2)} · minutes mean ${fmt(p.min.mean, 1)} (p10 ${fmt(p.min.p10, 0)} – p90 ${fmt(p.min.p90, 0)}) · PROJECTED`),
      dataTable([{key: "stat", label: "Stat"}, {key: "mean", label: "Mean", num: true}, {key: "sd", label: "SD", num: true}, {key: "p10", label: "p10", num: true}, {key: "p50", label: "p50", num: true}, {key: "p90", label: "p90", num: true}], rows, {sortKey: null}));
  }
  function team(d) {
    box.append(el("div", {class: "row"}, el("h3", {style: {margin: 0}}, d.name), badge(d.abbr), badge(d.conference), synthBadge()));
    box.append(tabs([
      ["season", "Team stats", () => { const w = el("div"); Object.keys(d.seasons).forEach((s) => w.append(el("h4", null, `${s} (${d.seasons[s].games} GP)`), valuesTable(d.seasons[s], d.columns))); return w; }],
      ["roster", "Roster", () => dataTable([{key: "name", label: "Player", render: (r) => entityLink("player", r.id, r.name)}, {key: "position", label: "Pos"}, {key: "games", label: "GP", num: true}].concat(d.roster_columns.map((c) => ({key: "v" + c.label, label: c.label, num: true, sort: (r) => r.values[c.label], render: (r) => fmt(r.values[c.label])}))), d.roster, {sortKey: "games", desc: true})],
      ["coach", "Coaching eras", () => dataTable([{key: "name", label: "Coach"}, {key: "first", label: "First"}, {key: "last", label: "Last"}, {key: "games", label: "GP", num: true}, {key: "w", label: "Win%", num: true, sort: (r) => r.values["Win%"], render: (r) => fmt(r.values["Win%"])}, {key: "n", label: "Net Rtg", num: true, sort: (r) => r.values["Net Rating"], render: (r) => fmt(r.values["Net Rating"])}], d.coaching_eras, {sortKey: "first", desc: false})],
      ["lineups", "Starting lineups", () => el("div", null, el("div", {class: "banner small"}, d.lineup_note), dataTable([{key: "lineup", label: "Lineup"}, {key: "games", label: "GP", num: true}, {key: "n", label: "Net Rtg", num: true, sort: (r) => r.values["Net Rating"], render: (r) => fmt(r.values["Net Rating"])}], d.starting_lineups, {sortKey: "games", desc: true}))],
      ["up", "Upcoming", () => dataTable([{key: "date", label: "Date"}, {key: "opp", label: "Opp", render: (r) => (r.home ? "vs " : "@ ") + r.opp}, {key: "f", label: "Input freshness", render: (r) => r.freshness ? r.freshness.state + " – " + r.freshness.note : ""}], d.upcoming, {sortKey: "date", desc: false, empty: "No scheduled games."})]]).root);
  }
  return {open: (p) => { if (p && p.id) show(p); }};
}, {x: 70, y: 40, w: 900, h: 580});

/* ---------- Live Monitor ---------- */
registerPanel("monitor", "Live Source Monitor", (body) => {
  const host = el("div"); body.append(host);
  async function load() {
    const [src, ev, cf] = await Promise.all([api("/api/sources"), api("/api/events?limit=60"), api("/api/conflicts")]); clear(host);
    host.append(el("div", {class: "banner warn small"}, `Lab clock: ${src.clock}. No source here is verified live. The bundled demo script is a SIMULATED feed of synthetic reports; it demonstrates the event pipeline, not real-time data.`));
    host.append(el("div", {class: "row"}, el("button", {class: "primary", onclick: deliver}, "Deliver next demo report"), el("button", {onclick: async () => { await api("/api/demo/reset", {body: {}}); toast("Demo script rewound", true); load(); }}, "Rewind demo script"),
      el("button", {onclick: upload}, "Upload events CSV…"), el("button", {onclick: load}, "Refresh")));
    host.append(el("h4", null, "Sources"), dataTable([{key: "name", label: "Source"}, {key: "kind", label: "Type"}, {key: "mode", label: "Mode"}, {key: "freshness_claim", label: "Freshness claim", render: (s) => badge(s.freshness_claim, {verified_live: "b-fresh", simulated: "b-warn", file: "b-info", stale: "b-stale"}[s.freshness_claim] || "")},
      {key: "events_stored", label: "Events", num: true}, {key: "latest_published_age_hours", label: "Latest age (h)", num: true}, {key: "last_error", label: "Last error"}, {key: "terms_status", label: "Terms"}, {key: "notes", label: "Notes", render: (s) => el("span", {class: "small muted", title: s.notes}, (s.notes || "").slice(0, 70))},
      {key: "poll", label: "", render: (s) => el("button", {onclick: async () => { try { const r = await api("/api/poll", {body: {source: s.name}}); toast(`${s.name}: ${r.ok ? "ok" : "not available"} (${r.inserted || 0} new)${r.error ? " – " + r.error : ""}`, !!r.ok); load(); } catch (e) { toast(e.message); } }}, "Poll")}], src.sources, {sortKey: "name", desc: false}));
    host.append(el("h4", null, "Provider adapters"), dataTable([{key: "name", label: "Adapter"}, {key: "mode", label: "Mode"}, {key: "configured", label: "Configured", render: (a) => a.configured ? "yes" : badge("no", "b-info")}, {key: "requires_key", label: "API key", render: (a) => a.requires_key ? "required (env var)" : "no"}, {key: "terms_status", label: "Terms review"}, {key: "notes", label: "Notes"}], src.adapters, {sortKey: "name", desc: false}));
    if (cf.conflicts.length) { host.append(el("h4", null, "Conflicts (latest published wins, flagged)"), dataTable([{key: "player", label: "Player"}, {key: "game_id", label: "Game"}, {key: "detail", label: "Detail", render: (c) => JSON.stringify(c.reports || c).slice(0, 160)}], cf.conflicts, {sortKey: null})); }
    host.append(el("h4", null, "Recent reports"), dataTable([{key: "published_at", label: "Published"}, {key: "age_hours", label: "Age h", num: true}, {key: "team", label: "Team"}, {key: "player_name", label: "Player"}, {key: "status", label: "Status", render: (e) => badge(e.status, e.status === "out" ? "b-fail" : "b-warn")},
      {key: "detail", label: "Detail"}, {key: "source", label: "Source"}, {key: "stale", label: "", render: (e) => e.stale ? badge("stale", "b-stale") : e.superseded_by ? badge("superseded", "b-info") : ""}, {key: "data_label", label: "Label"}], ev.events, {sortKey: "published_at", desc: true, empty: "No reports yet."}));
  }
  async function deliver() {
    try { const r = await api("/api/demo/deliver", {body: {}}); if (!r.ok) toast(r.error || "No more demo reports"); else {
      toast(`${r.inserted} report(s) ingested, ${r.recomputed.length} projections recomputed`, true);
      if (LAB.panels.picks && LAB.panels.picks.api) LAB.panels.picks.api.reload(); } load(); } catch (e) { toast(e.message); }
  }
  function upload() {
    const ta = el("textarea", {rows: 10, placeholder: "source,kind,team,player,game_date,status,detail,restriction_min,published_at"}); const m = modal("Upload events CSV (offline provider)", el("div", null, el("div", {class: "small muted"}, "Reports you paste are treated as file data; published_at decides which report wins."), ta,
      el("button", {class: "primary", onclick: async () => { try { const r = await api("/api/events/upload", {body: {csv: ta.value}}); toast(`${r.inserted || 0} new, ${(r.rejected || []).length} rejected`, !!r.ok); m.remove(); load(); } catch (e) { toast(e.message); } }}, "Ingest")));
  }
  load().catch((e) => host.append(el("div", {class: "banner bad"}, e.message)));
  return {reload: load};
}, {x: 90, y: 50, w: 980, h: 560});

/* ---------- Model Tournament ---------- */
registerPanel("models", "Model Tournament & Registry", (body) => {
  const host = el("div"); body.append(host); let timer = null;
  async function load() {
    const m = await api("/api/models"); clear(host);
    host.append(el("div", {class: "cards"}, ...[["Champion", m.champion], ["Trained through", m.trained_through], ["Stack", m.stack.join(" → ")]].map(([l, v]) => el("div", {class: "card"}, el("div", {class: "v"}, v || "–"), el("div", {class: "l"}, l)))));
    host.append(el("div", {class: "row"}, "Folds", el("input", {id: "t-folds", type: "number", value: 3, min: 2, max: 6, style: {width: "50px"}}), "MC draws", el("input", {id: "t-draws", type: "number", value: 80, min: 20, max: 300, style: {width: "60px"}}), "Max games/fold",
      el("input", {id: "t-maxg", type: "number", value: 60, min: 10, style: {width: "60px"}}), el("button", {class: "primary", onclick: start}, "Run tournament"), el("button", {class: "danger", onclick: rollback}, "Roll back champion")));
    host.append(el("div", {id: "t-prog"}));
    if (m.last_tournament) host.append(renderTournament(m.last_tournament));
    host.append(el("h4", null, "Registry history (append-only)"), dataTable([{key: "seq", label: "#", num: true}, {key: "at", label: "At"}, {key: "action", label: "Action"}, {key: "model_id", label: "Model"}, {key: "version", label: "Version"}, {key: "note", label: "Note"}], m.history, {sortKey: "seq", desc: true}));
    host.append(el("h4", null, "Fitted model info"), el("pre", {class: "mono small"}, JSON.stringify(m.train_info, null, 1).slice(0, 3000)));
  }
  async function start() {
    const q = (id) => +host.querySelector(id).value; const prog = $("#t-prog", host); clear(prog);
    try { const r = await api("/api/tournament/start", {body: {folds: q("#t-folds"), draws: q("#t-draws"), max_games_per_fold: q("#t-maxg")}}); poll(r.job_id); } catch (e) { toast(e.message); }
  }
  function poll(id) {
    clearInterval(timer); timer = setInterval(async () => { try { const j = await api("/api/tournament/" + id); const prog = $("#t-prog", host); if (!prog) return clearInterval(timer);
      clear(prog); prog.append(el("div", {class: "banner small"}, j.state === "running" ? el("span", {class: "spin"}) : "", " " + j.state + " · " + (j.progress || []).slice(-3).join(" | ")));
      if (j.state !== "running") { clearInterval(timer); if (j.state === "error") toast(j.error || "tournament failed"); else { toast(j.promoted ? "Challenger promoted: " + j.promoted : "Tournament finished; champion unchanged", true); load(); } } } catch (e) { clearInterval(timer); toast(e.message); } }, 1500);
  }
  async function rollback() { try { const r = await api("/api/models/rollback", {body: {note: "manual rollback from UI"}}); toast("Champion is now " + r.champion, true); load(); } catch (e) { toast(e.message); } }
  function renderTournament(t) {
    const names = Object.keys(t.models), root = el("div"); root.append(el("h4", null, "Chronological out-of-sample evaluation"), el("div", {class: "small muted"}, `${t.n_rows} player-game rows over ${t.folds.length} walk-forward fold(s) · eval scoring ${t.eval_scoring} · finished ${t.finished}. Synthetic-data results do not prove real-world skill.`));
    const rows = names.map((n) => { const x = t.models[n].metrics; return {name: n, version: t.models[n].version, min: x.min.mae, minb: x.min.bias, f: x.fpts.mae, fr: x.fpts.rmse, fb: x.fpts.bias, pts: x.pts.mae, reb: x.reb.mae, ast: x.ast.mae, brier: x.p_play_brier, c80: x.calibration && x.calibration.fpts_cover_80, mc: x.calibration && x.calibration.min_cover_80, champ: n === t.champion}; });
    root.append(dataTable([{key: "name", label: "Model", render: (r) => el("span", null, r.name, r.champ ? badge("champion", "b-pass") : "")}, {key: "min", label: "Min MAE", num: true}, {key: "f", label: "Fantasy MAE", num: true}, {key: "fr", label: "Fantasy RMSE", num: true}, {key: "fb", label: "Bias", num: true}, {key: "pts", label: "PTS MAE", num: true}, {key: "reb", label: "REB MAE", num: true}, {key: "ast", label: "AST MAE", num: true},
      {key: "brier", label: "P(play) Brier", num: true}, {key: "c80", label: "Fantasy 80% cov", num: true}, {key: "mc", label: "Min 80% cov", num: true}], rows, {sortKey: "f", desc: false}));
    root.append(el("h4", null, "Fantasy MAE by model"), barChart(rows.map((r) => ({label: r.name, value: r.f})), {lw: 180, w: 560}));
    const comp = t.comparisons || {}; root.append(el("h4", null, "Promotion test vs baselines (paired bootstrap)"), dataTable([{key: "n", label: "Challenger vs champion"}, {key: "fm", label: "Fantasy MAE gain (95% CI)", render: (r) => `${fmt(r.c.fpts.mean_improvement, 3)} ${ciText(r.c.fpts.ci95)}`}, {key: "mm", label: "Minutes MAE gain (95% CI)", render: (r) => `${fmt(r.c.min.mean_improvement, 3)} ${ciText(r.c.min.ci95)}`},
      {key: "p", label: "Promote?", render: (r) => r.c.promote ? badge("yes", "b-pass") : badge("no", "b-fail")}, {key: "why", label: "Reasons", render: (r) => (r.c.reasons || []).join("; ")}], Object.keys(comp).map((n) => ({n, c: comp[n]})), {sortKey: null}));
    const best = t.models[t.champion] || t.models.lab_v1; const rel = best.metrics.calibration && best.metrics.calibration.fpts_reliability;
    if (rel) root.append(el("h4", null, `Reliability (${t.champion})`), scatterChart(rel.map((r) => ({x: r.nominal, y: r.observed, label: "", group: ""})), {xl: "nominal quantile", yl: "observed frequency", diag: true}));
    const sl = best.slices || {}; Object.keys(sl).filter((k) => k !== "team" && k !== "coach").forEach((k) => root.append(el("h4", null, `Slice: ${k}`), dataTable([{key: "k", label: k}, {key: "n", label: "n", num: true}, {key: "min_mae", label: "Min MAE", num: true}, {key: "fpts_mae", label: "Fantasy MAE", num: true}], Object.keys(sl[k]).map((v) => Object.assign({k: v}, sl[k][v])), {sortKey: "k", desc: false})));
    return root;
  }
  load();
  return {close: () => clearInterval(timer), reload: load};
}, {x: 110, y: 60, w: 980, h: 600});

/* ---------- Audit ---------- */
registerPanel("audit", "Audit & Data Quality", (body) => {
  const host = el("div"); body.append(host);
  async function load(deep) {
    clear(host); host.append(el("span", {class: "spin"}), " auditing…");
    try { const a = await api("/api/audit?deep=" + (deep ? 1 : 0)); clear(host);
      host.append(el("div", {class: "cards"}, ...["pass", "fail", "warn", "info"].map((k) => el("div", {class: "card"}, el("div", {class: "v"}, a.summary[k]), el("div", {class: "l"}, k)))),
        el("div", {class: "row"}, el("button", {onclick: () => load(true)}, "Run deep audit (leakage + reproducibility)"), el("button", {onclick: () => load(false)}, "Quick audit")));
      host.append(kv({Dataset: a.dataset_label, "Dataset SHA-256": a.dataset_sha256, "App / formulas": `${a.app_version} / ${a.formula_version}`, Seed: a.seed, Champion: a.champion, "Model versions": JSON.stringify(a.model_versions), "Scoring versions": JSON.stringify(a.scoring_versions), "Lab clock": a.generated_for_clock}));
      const sec = (title, items) => [el("h4", null, title), dataTable([{key: "status", label: "Status", render: (r) => statusBadge(r.status)}, {key: "title", label: "Check"}, {key: "detail", label: "Detail", render: (r) => el("span", {class: "small"}, r.detail)}], items, {sortKey: null})];
      host.append(...sec("Data quality", a.data_quality), ...sec("Append-only / immutability", a.immutability));
      if (a.leakage) host.append(...sec("Leakage tests", [a.leakage])); if (a.reproducibility) host.append(...sec("Reproducibility", [a.reproducibility]));
      if (!deep) host.append(el("div", {class: "small muted"}, "Quick mode skips the slower leakage and reproducibility checks."));
    } catch (e) { clear(host); host.append(el("div", {class: "banner bad"}, e.message)); }
  }
  load(false); return {reload: () => load(false)};
}, {x: 130, y: 70, w: 900, h: 560});

/* ---------- Prediction Lab (compare saved projections across scoring systems) ---------- */
registerPanel("predlab", "Prediction Lab", (body) => {
  const host = el("div"); body.append(host); const id = el("input", {placeholder: "player name", list: "pl-dl", style: {width: "200px"}}), dl = el("datalist", {id: "pl-dl"}), cache = {};
  id.addEventListener("input", async () => { if (id.value.length < 2) return; const r = await api("/api/entities?type=player&q=" + encodeURIComponent(id.value) + "&limit=20"); clear(dl); r.entities.forEach((e) => { cache[e.name] = e; dl.append(el("option", {value: e.name})); }); });
  const out = el("div"); host.append(el("div", {class: "small muted"}, "Compare one player's next projection under every scoring system, and see the distribution."), el("div", {class: "row"}, id, dl, el("button", {class: "primary", onclick: go}, "Compare")), out);
  async function go() {
    const e = cache[id.value]; if (!e) return toast("Pick a player from the list"); clear(out);
    try { const [d, sc] = await Promise.all([api("/api/player/" + e.id), api("/api/scoring")]); if (!d.projection) return out.append(el("div", {class: "banner"}, "No stored projection."));
      const p = d.projection; out.append(el("div", {class: "banner small warn"}, `${d.name} ${p.date} ${p.home ? "vs" : "@"} ${p.opponent} · PROJECTED · P(play) ${fmt(p.p_play, 2)}`));
      const mean = (k) => p.stats[k] ? p.stats[k].mean : 0; const rows = sc.systems.map((s) => { let v = 0; Object.entries(s.rules).forEach(([k, w]) => { if (p.stats[k]) v += w * p.stats[k].mean; }); return {name: `${s.name} v${s.version}`, v, note: "linear rules on mean stats; dd/td terms need the full simulation (see Picks)"}; });
      out.append(dataTable([{key: "name", label: "Scoring"}, {key: "v", label: "Mean (linear)", num: true}, {key: "note", label: "Note"}], rows, {sortKey: "v", desc: true}));
      out.append(el("h4", null, "Stat distributions"), dataTable([{key: "k", label: "Stat"}, {key: "m", label: "Mean", num: true}, {key: "p10", label: "p10", num: true}, {key: "p50", label: "p50", num: true}, {key: "p90", label: "p90", num: true}], Object.entries(p.stats).map(([k, s]) => ({k, m: s.mean, p10: s.p10, p50: s.p50, p90: s.p90})), {sortKey: null}));
    } catch (er) { toast(er.message); }
  }
  return {};
}, {x: 150, y: 80, w: 760, h: 460});
