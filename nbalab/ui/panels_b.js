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
    const synth = LAB.status && LAB.status.synthetic;
    host.append(el("div", {class: "banner warn small"}, synth ? `Lab clock: ${src.clock}. No source here is verified live. The bundled demo script is a SIMULATED feed of synthetic reports; it demonstrates the event pipeline, not real-time data.`
      : `Lab clock: ${src.clock} (real time). A source counts as live only after a successful fetch; enable sources and acknowledge their terms in the Sources panel.`));
    host.append(el("div", {class: "row"}, synth ? el("button", {class: "primary", onclick: deliver}, "Deliver next demo report") : null, synth ? el("button", {onclick: async () => { await api("/api/demo/reset", {body: {}}); toast("Demo script rewound", true); load(); }}, "Rewind demo script") : null,
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

/* ---------- Data Sources: capability matrix, terms gate, probe, real-data import ---------- */
registerPanel("sources", "Data Sources & Providers", (body) => {
  const host = el("div"); body.append(host); let timer = null;
  const EVID = {probe_verified: ["verified live", "b-pass", "A live request from a real environment returned data (see Probe tab)."], library_catalog: ["in nba_api catalog", "b-info", "Endpoint and columns exist in the nba_api source code. NOT proof it works today."],
    documented: ["vendor docs", "b-aging", "Stated in the vendor documentation; not yet confirmed by a live request."], unverified: ["unverified", "b-warn", "Inferred; response shape not confirmed."]};
  const OUT = {ok: "b-pass", empty: "b-warn", skipped: "b-info", not_enabled: "b-info", not_configured: "b-info", blocked: "b-fail", timeout: "b-fail", unreachable: "b-fail", http: "b-fail", auth: "b-warn", parse: "b-fail"};
  async function load(tab) {
    const d = await api("/api/datasources"); clear(host);
    host.append(el("div", {class: "banner small " + (d.synthetic ? "warn" : "")}, `Current dataset: ${d.dataset_label}. Sources are OFF until you enable them and acknowledge their terms. Nothing here buys anything; keys are read from environment variables and never stored.`));
    const t = tabs([
      ["matrix", "Capability matrix", () => matrix(d)], ["src", "Sources & terms", () => sources(d)], ["probe", "Probe & log", () => probe(d)], ["imp", "Real-data import", () => importer(d)]], tab || "matrix");
    host.append(t.root);
  }
  function matrix(d) {
    const w = el("div"); w.append(el("div", {class: "small muted"}, "Rows are research capabilities; chips are the sources that can serve each, in routing priority order. Evidence colours: green = verified by a live probe, blue = endpoint exists in the nba_api catalog, grey = vendor docs only, yellow = unverified."));
    const rows = d.matrix.map((m) => ({cap: m.capability, desc: m.description, m}));
    w.append(dataTable([{key: "cap", label: "Capability"}, {key: "desc", label: "What it gives"}, {key: "m", label: "Sources (priority order)", sort: (r) => r.m.sources.length, render: (r) => r.m.sources.length ? el("span", null, r.m.sources.map((c) => {
      const ev = EVID[c.evidence] || EVID.unverified; const tip = `${c.endpoint}\ntier: ${c.tier}\n${ev[2]}\n${c.notes}${c.probe ? "\nlast probe: " + c.probe.outcome + " " + c.probe.at : ""}`;
      return el("span", {class: "chip", title: tip, style: {paddingRight: "9px"}}, (c.priority ? c.priority + ". " : "") + c.source + (c.enabled ? "" : " (off)"), " ", badge(ev[0], ev[1]), c.tier.startsWith("paid") || c.tier === "quote" ? badge(c.tier, "b-warn") : null);
    })) : badge("no source", "b-fail")}], rows, {sortKey: null, pageSize: 60}));
    const gaps = d.matrix.filter((m) => !m.covered).map((m) => m.capability);
    if (gaps.length) w.append(el("div", {class: "banner warn small"}, "No source declared for: " + gaps.join(", ")));
    return w;
  }
  function sources(d) {
    const w = el("div");
    d.sources.forEach((s) => {
      const caps = s.capabilities.map((c) => c.capability).join(", ");
      w.append(el("div", {class: "banner"}, el("div", {class: "row"}, el("b", null, s.name), badge(s.enabled ? "ENABLED" : "off", s.enabled ? "b-pass" : "b-info"), badge("terms: " + s.terms_status, s.terms_status === "restriction_found" ? "b-fail" : s.terms_status === "unreviewed" ? "b-warn" : "b-info"),
        s.requires_key ? badge(s.configured ? "key present" : "needs " + s.key_env, s.configured ? "b-pass" : "b-warn") : badge("no key", "b-info")),
        el("div", {class: "small"}, s.terms_summary || "No terms summary recorded."), el("div", {class: "small muted"}, "Terms page: " + (s.terms_url || "n/a")), el("div", {class: "small muted"}, "Capabilities: " + caps),
        s.ack ? el("div", {class: "small muted"}, `Acknowledged ${s.ack.at}`) : null,
        el("div", {class: "row"}, s.enabled ? el("button", {class: "danger", onclick: async () => { await api("/api/sources/disable", {body: {name: s.name}}); load("src"); }}, "Disable") : el("button", {class: "primary", onclick: () => enable(s)}, "Enable…"))));
    });
    return w;
  }
  function enable(s) {
    const need = `I have read ${s.terms_url} and accept responsibility for how I use this source`; const inp = el("input", {style: {width: "100%"}, placeholder: "type the sentence above exactly"});
    const m = modal("Enable " + s.name, el("div", null, el("div", {class: "banner warn small"}, s.terms_summary), el("p", null, "To enable this source, read its terms and then type this sentence exactly:"), el("pre", {class: "mono small"}, need), inp,
      el("div", {class: "row"}, el("button", {class: "primary", onclick: async () => { try { await api("/api/sources/enable", {body: {name: s.name, acknowledgement: inp.value}}); m.remove(); toast(s.name + " enabled", true); load("src"); } catch (e) { toast(e.message); } }}, "Enable source"))));
  }
  function probe(d) {
    const w = el("div"); const prog = el("div"); const out = el("div");
    w.append(el("div", {class: "small muted"}, "A probe makes a handful of small live requests per ENABLED source and records the honest outcome (ok, empty, blocked, timeout, unreachable, auth, http, parse). stats.nba.com is known to ignore many cloud/hosting IP ranges, so results from a datacenter say little about your home connection; run this on the machine you actually use."),
      el("div", {class: "row"}, el("button", {class: "primary", onclick: run}, "Run probe on enabled sources"), el("button", {onclick: () => load("probe")}, "Refresh")), prog);
    async function run() { clear(prog); try { const r = await api("/api/sources/probe", {body: {}}); poll(r.job_id); } catch (e) { toast(e.message); } }
    function poll(id) { clearInterval(timer); timer = setInterval(async () => { const j = await api("/api/jobs/" + id); clear(prog); prog.append(el("div", {class: "banner small"}, j.state === "running" ? el("span", {class: "spin"}) : "", " " + j.state + (j.error ? ": " + j.error : "")));
      if (j.state !== "running") { clearInterval(timer); load("probe"); } }, 1500); }
    w.append(el("h4", null, "Latest probe results"), dataTable([{key: "at", label: "When"}, {key: "source", label: "Source"}, {key: "capability", label: "Capability"}, {key: "outcome", label: "Outcome", render: (r) => badge(r.outcome, OUT[r.outcome] || "")}, {key: "rows", label: "Rows", num: true}, {key: "elapsed_ms", label: "ms", num: true}, {key: "detail", label: "Detail"}], d.probes, {sortKey: "at", desc: true, empty: "No probe has been run yet."}));
    w.append(el("h4", null, "Request log"), dataTable([{key: "at", label: "When"}, {key: "source", label: "Source"}, {key: "capability", label: "Capability"}, {key: "ok", label: "OK", render: (r) => r.ok ? badge("ok", "b-pass") : badge(r.error_kind, "b-fail")}, {key: "rows", label: "Rows", num: true}, {key: "detail", label: "Detail"}], d.log, {sortKey: "at", desc: true, empty: "No requests yet."}));
    return w;
  }
  function importer(d) {
    const w = el("div"); const seasons = el("input", {value: "", placeholder: "e.g. 2025-26,2026-27 (blank = current season)", style: {width: "260px"}}); const prog = el("div"); const dbs = el("div");
    w.append(el("div", {class: "banner warn small"}, "Imports players, schedule and player game logs (including minutes) through the enabled sources into a SEPARATE database, so synthetic and real data are never mixed. Tracking columns are stored as missing, starters are INFERRED from minutes until a box-score backfill exists, and coaches are unknown. Review the source terms (Sources tab) before importing."),
      el("div", {class: "row"}, "Seasons", seasons, el("button", {class: "primary", onclick: go}, "Import into new database")), prog, el("h4", null, "Databases"), dbs);
    async function go() { clear(prog); const list = seasons.value.split(",").map((x) => x.trim()).filter(Boolean);
      try { const r = await api("/api/data/import", {body: {seasons: list.length ? list : undefined, overwrite: true}}); poll(r.job_id); } catch (e) { toast(e.message); } }
    function poll(id) { clearInterval(timer); timer = setInterval(async () => { const j = await api("/api/jobs/" + id); clear(prog);
      prog.append(el("div", {class: "banner small " + (j.state === "error" ? "bad" : "")}, j.state === "running" ? el("span", {class: "spin"}) : "", " " + j.state + " · " + (j.error || (j.progress || []).slice(-2).join(" | "))));
      if (j.state !== "running") { clearInterval(timer); listDbs(); if (j.state === "done") toast(`Imported ${j.result.games} games, ${j.result.box_rows} player-game rows`, true); } }, 1500); }
    async function listDbs() { const r = await api("/api/data/databases"); clear(dbs);
      dbs.append(dataTable([{key: "name", label: "File"}, {key: "label", label: "Dataset label"}, {key: "size_mb", label: "MB", num: true}, {key: "cur", label: "", render: (x) => x.path === r.current ? badge("in use", "b-pass") :
        el("button", {onclick: async () => { try { await api("/api/data/switch", {body: {path: x.path}}); toast("Switched. Reloading…", true); setTimeout(() => location.reload(), 800); } catch (e) { toast(e.message); } }}, "Switch to this")}], r.databases, {sortKey: null})); }
    listDbs(); return w;
  }
  load().catch((e) => host.append(el("div", {class: "banner bad"}, e.message)));
  return {close: () => clearInterval(timer), reload: () => load()};
}, {x: 60, y: 30, w: 1040, h: 620});


/* ---------- Data Source Diagnostics: test provider connections from THIS computer, with progress and an exportable report ---------- */
registerPanel("diag", "Data Source Diagnostics", (body) => {
  const host = el("div"); body.append(host); let timer = null, st = null;
  const CHIP = {ok: "b-pass", empty: "b-pass", timeout: "b-warn", blocked: "b-fail", unreachable: "b-fail", http: "b-fail", auth: "b-fail", parse: "b-fail", not_configured: "b-warn", skipped: "b-info", pending: "b-info", running: "b-info"};
  async function init() {
    st = await api("/api/diagnostics/state"); clear(host);
    host.append(el("div", {class: "banner small"}, "Tests which data sources answer from this computer. Makes a few small, spaced-out requests per source. Each source has its own terms: read the link, then confirm. Use it for any future provider added to the Sources layer."));
    const rows = st.sources.map((s) => { const inc = el("input", {type: "checkbox", checked: s.default}), ack = el("input", {type: "checkbox"}); return {s, inc, ack}; });
    rows.forEach((r) => host.append(el("div", {class: "banner"}, el("label", {class: "row"}, r.inc, el("b", null, r.s.name), el("span", {class: "small muted"}, r.s.kind)),
      el("div", {class: "small"}, r.s.terms_summary || "No terms summary recorded."), el("div", {class: "small muted"}, "Terms: " + r.s.terms_url),
      el("label", {class: "row small"}, r.ack, "I have read the terms for " + r.s.name + " and accept responsibility for how I use this source."))));
    const out = el("div"), msg = el("span", {class: "small muted"});
    const run = el("button", {class: "primary", onclick: async () => { const pick = rows.filter((r) => r.inc.checked).map((r) => r.s.name), ak = rows.filter((r) => r.ack.checked).map((r) => r.s.name);
      try { await api("/api/diagnostics/start", {body: {sources: pick, acknowledged: ak}}); msg.textContent = ""; poll(out); } catch (e) { msg.textContent = e.message; } }}, "Run Tests");
    host.append(el("div", {class: "row"}, run, msg), out); if (st.progress.status !== "idle") poll(out);
  }
  function poll(out) { clearInterval(timer); const tick = async () => { const p = await api("/api/diagnostics/progress"); draw(out, p); if (p.status === "done") clearInterval(timer); }; tick(); timer = setInterval(() => tick().catch(() => {}), 800); }
  function draw(out, p) {
    clear(out); out.append(el("div", {class: "small"}, p.status === "running" ? el("span", {class: "spin"}) : "", ` ${p.done} of ${p.total} checks finished`));
    out.append(dataTable([{key: "source", label: "Source"}, {key: "capability", label: "Check"}, {key: "label", label: "Result", render: (r) => badge(r.label, CHIP[r.state] || "b-fail")}, {key: "rows", label: "Rows", num: true}, {key: "elapsed_ms", label: "ms", num: true}, {key: "detail", label: "Detail"}], p.steps, {sortKey: null, pageSize: 60}));
    if (p.status === "done") { const where = el("div", {class: "small mono"}, p.last_export ? "Saved: " + p.last_export : "");
      out.append(el("div", {class: "row"}, el("button", {class: "primary", onclick: async () => { try { const r = await api("/api/diagnostics/export", {body: {}}); where.textContent = "Saved: " + r.path; toast("Report saved", true); } catch (e) { toast(e.message); } }}, "Export Report"),
        el("button", {onclick: () => api("/api/diagnostics/reveal", {body: {}}).catch((e) => toast(e.message))}, "Show in folder")), where); }
  }
  init().catch((e) => host.append(el("div", {class: "banner warn"}, e.message)));
  return {destroy: () => clearInterval(timer)};
}, {x: 120, y: 60, w: 820, h: 560});
