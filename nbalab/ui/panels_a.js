/* Panels: Fantasy Picks, Analytics Workbench, Natural-Language Research (+ shared result renderer) */
"use strict";

LAB.metricsCache = null;
async function getMetrics() { if (!LAB.metricsCache) LAB.metricsCache = (await api("/api/metrics")).metrics; return LAB.metricsCache; }
function synthBadge() { return LAB.status && LAB.status.synthetic ? badge("SYNTHETIC DATA", "b-synth", LAB.status.dataset_note) : null; }
function entityLink(type, id, name) { return el("a", {onclick: (e) => { e.stopPropagation(); openPanel("explorer", {type, id}); }}, name); }
function tokenOf(m) { return m.transform === "raw" ? m.id : m.id + "_" + m.transform; }
function ciText(ci) { return ci ? `[${fmt(ci[0])}, ${fmt(ci[1])}]` : ""; }

/* ---------- shared result renderer ---------- */
function renderResult(res, opts) {
  opts = opts || {}; const root = el("div"); const m = res.meta;
  root.append(el("div", {class: "row"}, synthBadge(), badge(m.dataset_label), badge(`${m.rows_matched} game-rows`), badge(`${m.entities_matched} entities`), badge(`${m.compute_ms} ms`), badge("formulas " + m.formula_version),
    m.last_game_date ? badge("data through " + m.last_game_date) : null));
  (m.warnings || []).forEach((w) => root.append(el("div", {class: "banner warn"}, w)));
  if (!res.rows.length) { root.append(el("div", {class: "banner"}, "No rows to show.")); return root; }
  const q = res.query; const cols = [{key: "rank", label: "#", num: true}, {key: "name", label: q.entity === "player" ? "Player" : q.entity === "team" ? "Team" : q.entity === "coach" ? "Coach" : "Lineup",
    render: (r) => (q.entity === "player" || q.entity === "team") ? entityLink(q.entity, r.id, r.name) : r.name, sort: (r) => r.name}];
  if (q.entity === "player") cols.push({key: "team", label: "Tm"}, {key: "position", label: "Pos"});
  cols.push({key: "games", label: "GP", num: true}, {key: "minutes", label: "Min", num: true});
  res.columns.forEach((c) => cols.push({key: "v:" + c.label, label: c.label, num: true, title: `${c.kind} | ${c.formula} (${c.formula_version})`, sort: (r) => r.values[c.label],
    render: (r) => { const v = r.values[c.label], ci = r.ci90 && r.ci90[c.label]; return el("span", null, fmt(v), ci ? el("span", {class: "ci"}, ciText(ci)) : null); }}));
  root.append(el("div", {class: "row small muted"}, "Header hover shows kind, formula and version.  ", res.columns.map((c) => el("span", null, kindBadge(c.kind), " "))));
  const t = dataTable(cols, res.rows, {onRow: opts.onRow, sortKey: "rank", desc: false}); root.append(t);
  if (res.compare) {
    const c = res.compare; const tb = el("table", {class: "t"}, el("thead", null, el("tr", null, el("th", null, "Metric"), c.metrics[0].entities.map((e) => el("th", {class: "num"}, e.name)))),
      el("tbody", null, c.metrics.map((m2) => el("tr", null, el("td", null, m2.label), m2.entities.map((e) => el("td", {class: "num"}, fmt(e.value), el("span", {class: "ci"}, (e.percentile != null ? `pctile ${fmt(e.percentile, 0)}` : "") + (e.delta_vs_baseline != null && e !== m2.entities[0] ? `  Δ ${fmt(e.delta_vs_baseline)}` : ""))))))));
    root.append(el("h4", null, "Comparison vs " + c.baseline), tb);
  }
  if (res.correlation) root.append(renderCorrelation(res.correlation));
  return root;
}
function renderCorrelation(c) {
  const t = el("table", {class: "t heat"}, el("thead", null, el("tr", null, el("th", null, ""), c.metrics.map((m) => el("th", null, m)))),
    el("tbody", null, c.metrics.map((m, i) => el("tr", null, el("td", null, m), c.r[i].map((r, j) => el("td", {title: `n=${c.n[i][j]}, p=${c.p_value[i][j] == null ? "–" : c.p_value[i][j].toExponential(1)}`, style: {background: heatColor(i === j ? null : r)}}, r == null ? "–" : r.toFixed(2)))))));
  return el("div", null, el("h4", null, `${c.method} correlation (${c.level} level, n≈${c.n[0][1]} ${c.unit})`), t, el("div", {class: "small muted"}, c.note));
}
function chartSection(res) {
  const root = el("div"); if (!res.rows.length || !res.columns.length) return root; const labs = res.columns.map((c) => c.label);
  const type = el("select", null, ["bar", "scatter", "histogram", "line (by rank)"].map((t) => el("option", null, t)));
  const xs = el("select", null, labs.map((l) => el("option", null, l))), ys = el("select", null, labs.map((l, i) => el("option", {selected: i === Math.min(1, labs.length - 1)}, l)));
  const out = el("div");
  function draw() {
    clear(out); const x = xs.value, y = ys.value, rows = res.rows, ent = res.query.entity;
    const open = (r) => () => { if (ent === "player" || ent === "team") openPanel("explorer", {type: ent, id: r.id}); };
    if (type.value === "bar") out.append(barChart(rows.map((r) => ({label: r.name, value: r.values[x], lo: r.ci90 && r.ci90[x] ? r.ci90[x][0] : null, hi: r.ci90 && r.ci90[x] ? r.ci90[x][1] : null, onclick: open(r)})), {lw: 170}));
    else if (type.value === "scatter") { const pts = rows.filter((r) => r.values[x] != null && r.values[y] != null).map((r) => ({x: r.values[x], y: r.values[y], label: r.name, group: r.position || r.team || "", onclick: open(r)}));
      out.append(scatterChart(pts, {xl: x, yl: y, fit: true, labelTop: pts.slice().sort((a, b) => b.y - a.y).slice(0, 5)})); }
    else if (type.value === "histogram") out.append(histChart(rows.map((r) => r.values[x]), {xl: x}));
    else out.append(lineChart(labs.map((l) => ({name: l, points: rows.map((r, i) => ({x: i + 1, y: r.values[l]})).filter((p) => p.y != null)})).slice(0, 4), {xl: "rank", yl: ""}));
  }
  [type, xs, ys].forEach((s) => s.addEventListener("change", draw));
  root.append(el("h4", null, "Chart"), el("div", {class: "row"}, "Type", type, "X / value", xs, "Y", ys), out); draw(); return root;
}
async function showSupport(query, row) {
  try {
    const s = await api("/api/support", {body: {query, id: row.id, limit: 120}});
    const cols = [{key: "date", label: "Date"}, {key: "opponent", label: "Opp"}, {key: "home", label: "H/A", render: (r) => r.home ? "H" : "A"}, {key: "result", label: "W/L"}, {key: "score", label: "Score"}];
    const keys = Object.keys(s.rows[0] ? s.rows[0].stats : {}); keys.forEach((k) => cols.push({key: "s:" + k, label: k, num: true, render: (r) => fmt(r.stats[k]), sort: (r) => r.stats[k]}));
    const totals = el("div", {class: "small muted"}, "Column totals over all contributing games: " + Object.entries(s.column_totals).map(([k, v]) => `${k}=${fmt(v)}`).join("  "));
    modal(`Supporting games: ${s.name} (${s.games_total} games, ${s.shown} shown)`, el("div", null, el("div", {class: "small muted"}, "These are the exact game rows aggregated into the number you clicked, under the same window and filters. Dataset: " + s.dataset_label), totals,
      dataTable(cols, s.rows, {sortKey: "date", desc: true, empty: "No games."})));
  } catch (e) { toast(e.message); }
}

/* ---------- Fantasy Picks ---------- */
registerPanel("picks", "Fantasy Picks", (body) => {
  const state = {rows: [], scoring: "classic_points"};
  const scoringSel = el("select"), catSel = el("select"), q = el("input", {placeholder: "player / team", style: {width: "140px"}}), teamSel = el("select", null, el("option", {value: ""}, "All teams")),
    posSel = el("select", null, el("option", {value: ""}, "All pos"), ["PG", "SG", "SF", "PF", "C"].map((p) => el("option", null, p))), pp = el("input", {type: "number", value: 0, min: 0, max: 1, step: 0.1, style: {width: "56px"}, title: "Minimum probability of playing"}),
    days = el("select", null, [1, 2, 3, 4].map((d) => el("option", {value: d}, d + (d === 1 ? " game day" : " game days"))));
  const cats = {overall: "Overall expected", upside: "Upside (p90)", consistency: "Consistency", minutes: "Minutes opportunity", matchup: "Matchup & pace edge", defense: "Defensive specialists", injury_opportunity: "Injury opportunities", value: "Value (needs cost data)"};
  Object.entries(cats).forEach(([k, v]) => catSel.append(el("option", {value: k}, v)));
  const info = el("div"), tableHost = el("div"), detail = el("div");
  const bar = el("div", {class: "row"}, "Scoring", scoringSel, el("button", {onclick: editScoring}, "Edit rules"), "Rank by", catSel, q, teamSel, posSel, "P(play) ≥", pp, days,
    el("button", {class: "primary", onclick: load}, "Refresh"), el("button", {onclick: importCosts}, "Import costs…"), el("button", {onclick: exportCsv}, "CSV"));
  body.append(bar, info, tableHost, detail);
  [scoringSel, catSel, teamSel, posSel, days].forEach((s) => s.addEventListener("change", load)); q.addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
  async function init() {
    const sc = await api("/api/scoring"); clear(scoringSel); sc.systems.forEach((s) => scoringSel.append(el("option", {value: s.id}, `${s.name} v${s.version}`))); scoringSel.value = state.scoring;
    const t = await api("/api/entities?type=team"); t.entities.forEach((e) => teamSel.append(el("option", {value: e.id}, e.abbr + " " + e.name)));
    load();
  }
  async function load() {
    state.scoring = scoringSel.value || "classic_points"; clear(tableHost); tableHost.append(el("span", {class: "spin"}), " projecting…");
    try {
      const r = await api(`/api/picks?scoring=${state.scoring}&category=${catSel.value}&q=${encodeURIComponent(q.value)}&days=${days.value}&team=${teamSel.value}&position=${posSel.value}&min_p_play=${pp.value}&limit=80`);
      state.rows = r.rows; clear(info); clear(tableHost); clear(detail);
      info.append(el("div", {class: "banner " + (LAB.status.synthetic ? "warn" : "")}, r.disclaimer || "", el("span", {class: "small"}, `   ${r.category_label || ""} · scoring ${r.scoring ? r.scoring.name + " v" + r.scoring.version : ""} · games ${r.game_dates ? r.game_dates.join(", ") : ""} · as of ${r.as_of || ""}`)));
      if (r.message) info.append(el("div", {class: "banner"}, r.message));
      const cols = [{key: "rank", label: "#", num: true}, {key: "player", label: "Player", render: (x) => entityLink("player", x.player_id, x.player)}, {key: "team", label: "Tm", render: (x) => `${x.team} ${x.home ? "vs" : "@"} ${x.opponent}`},
        {key: "p_play", label: "P(play)", num: true, render: (x) => fmt(x.p_play, 2)}, {key: "min_mean", label: "Min (p10–p90)", num: true, render: (x) => `${fmt(x.min_mean, 1)} (${fmt(x.min_p10, 0)}–${fmt(x.min_p90, 0)})`},
        {key: "f", label: "Fantasy", num: true, sort: (x) => x.fantasy.mean, render: (x) => el("b", null, fmt(x.fantasy.mean, 1))}, {key: "fr", label: "p10–p90", num: true, sort: (x) => x.fantasy.p90, render: (x) => `${fmt(x.fantasy.p10, 0)}–${fmt(x.fantasy.p90, 0)}`},
        {key: "cons", label: "Consist.", num: true, sort: (x) => x.fantasy.consistency, render: (x) => fmt(x.fantasy.consistency, 2)}];
      ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"].forEach((k) => cols.push({key: "s" + k, label: k.toUpperCase(), num: true, sort: (x) => x.stats[k], render: (x) => fmt(x.stats[k], 1)}));
      if (catSel.value === "value") cols.push({key: "cost", label: "Cost", num: true});
      cols.push({key: "score", label: "Rank value", num: true, render: (x) => fmt(x.score, 2)}, {key: "flags", label: "Flags", render: (x) => flagBadges(x), sort: (x) => x.flags.length});
      tableHost.append(dataTable(cols, r.rows, {sortKey: "rank", desc: false, onRow: showDetail, empty: "No candidates."}));
    } catch (e) { clear(tableHost); tableHost.append(el("div", {class: "banner bad"}, e.message)); }
  }
  function flagBadges(x) {
    const out = []; if (x.status) out.push(badge(x.status, x.status === "out" ? "b-fail" : "b-warn", x.status_detail || ""));
    if (x.flags.some((f) => f.startsWith("STALE") || f.includes("STALE INPUT"))) out.push(badge("stale input", "b-stale"));
    if (x.flags.some((f) => f.startsWith("Conflicting"))) out.push(badge("conflict", "b-warn"));
    if (x.flags.some((f) => f.startsWith("Low sample"))) out.push(badge("low sample", "b-info"));
    if (x.input_freshness && x.input_freshness.state === "no_report") out.push(badge("no report", "b-info", "No injury/lineup report published for this team and game"));
    if (x.trigger_event_id) out.push(badge("event-updated", "b-fresh", "Recomputed after a new report arrived")); return el("span", null, out);
  }
  function showDetail(x) {
    clear(detail); const d = el("div", {class: "banner"});
    d.append(el("div", {class: "row"}, el("b", null, `${x.player} — ${x.team} ${x.home ? "vs" : "@"} ${x.opponent}, ${x.game_date}`), synthBadge(), badge(x.model_version), badge("as of " + x.as_of)));
    d.append(el("div", {class: "row"}, el("div", null, el("div", {class: "small muted"}, "Fantasy points distribution (mean ●, box p25–p75, whiskers p10–p90)"), boxPlot(x.fantasy)),
      el("div", null, el("div", {class: "small muted"}, "Minutes (unconditional, includes chance of not playing)"), boxPlot({p10: x.min_p10, p25: x.min_p10, p50: x.min_mean, p75: x.min_p90, p90: x.min_p90, mean: x.min_mean}, {lo: 0, hi: 48}))));
    d.append(kv({"P(play)": fmt(x.p_play, 3), "Double-double prob": fmt(x.dd_prob, 3), "History games": x.history_games, "Vacated teammate minutes (feature)": fmt(x.vacated_min, 2),
      "Input freshness": x.input_freshness ? (x.input_freshness.state + (x.input_freshness.latest_report_age_hours != null ? `, latest report ${x.input_freshness.latest_report_age_hours}h old from ${x.input_freshness.source}` : "")) : "–"}));
    if (x.drivers.length) { d.append(el("h4", null, "Why these minutes (ridge contributions, in minutes vs league-average player)"), barChart(x.drivers.map((v) => ({label: `${v.label} (${fmt(v.value, 1)})`, value: v.minutes_effect})), {lw: 250, w: 560})); }
    x.flags.forEach((f) => d.append(el("div", {class: "banner warn small"}, f)));
    d.append(el("div", {class: "small muted"}, "Projections are model output with uncertainty, not facts. Re-run after new reports arrive in the Monitor panel."));
    detail.append(d); detail.scrollIntoView({block: "nearest"});
  }
  async function editScoring() {
    const cur = (await api("/api/scoring")).systems.find((s) => s.id === scoringSel.value); const keys = ["pts", "fg3m", "fgm", "fga", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "dd", "td"];
    const inputs = {}; const g = el("div", {class: "kv"}); keys.forEach((k) => { inputs[k] = el("input", {type: "number", step: 0.05, value: cur.rules[k] != null ? cur.rules[k] : "", style: {width: "80px"}}); g.append(el("div", null, k), inputs[k]); });
    const name = el("input", {value: cur.name, style: {width: "260px"}});
    const m = modal("Edit scoring rules (creates a NEW immutable version)", el("div", null, el("div", {class: "small muted"}, `Current: ${cur.id} v${cur.version}. Existing versions and past predictions are never rewritten.`), el("div", {class: "row"}, "Name", name), g,
      el("div", {class: "row"}, el("button", {class: "primary", onclick: async () => { const rules = {}; keys.forEach((k) => { if (inputs[k].value !== "") rules[k] = parseFloat(inputs[k].value); });
        try { const r = await api("/api/scoring", {body: {id: cur.id, name: name.value, rules}}); toast(`Saved ${r.id} v${r.version}`, true); m.remove(); init(); } catch (e) { toast(e.message); } }}, "Save new version"))));
  }
  function importCosts() {
    const ta = el("textarea", {rows: 10, placeholder: "player,cost\nFull Player Name,7500"}); const src = el("input", {placeholder: "source label (e.g. my salary sheet)", style: {width: "300px"}});
    const m = modal("Import player costs (for Value ranking)", el("div", null, el("div", {class: "small muted"}, "Paste a CSV with columns player,cost. Nothing is fabricated: Value ranking stays unavailable until costs are loaded."), ta, el("div", {class: "row"}, src,
      el("button", {class: "primary", onclick: async () => { const lines = ta.value.trim().split(/\r?\n/).slice(1).map((l) => { const i = l.lastIndexOf(","); return {player: l.slice(0, i).replace(/^"|"$/g, ""), cost: l.slice(i + 1)}; });
        try { const r = await api("/api/costs/import", {body: {rows: lines, source: src.value || "user import"}}); toast(`Imported ${r.imported}, rejected ${r.rejected}`, true); m.remove(); load(); } catch (e) { toast(e.message); } }}, "Import"))));
  }
  function exportCsv() { downloadText("picks.csv", toCsv(["rank", "player", "team", "opp", "p_play", "min", "fantasy_mean", "p10", "p90", "score"], state.rows.map((x) => [x.rank, x.player, x.team, x.opponent, x.p_play, x.min_mean, x.fantasy.mean, x.fantasy.p10, x.fantasy.p90, x.score])), "text/csv"); }
  init();
  return {open: (p) => { if (p && p.q) { q.value = p.q; load(); } }, reload: load};
}, {x: 8, y: 6, w: 1120, h: 560});

/* ---------- Analytics Workbench ---------- */
registerPanel("workbench", "Advanced Analytics Workbench", (body) => {
  const S = {entity: "player", metrics: [{id: "pts", transform: "per36"}, {id: "ts_pct", transform: "raw"}, {id: "usg_pct", transform: "raw"}], ids: [], team_ids: [], seasons: [], date_from: "", date_to: "", last_n: "",
    home: "", result: "", started: "", b2b: "", min_game_min: "", rest_min: "", ot: "", opponents: [], teammates_out: [], teammates_in: [], coach_ids: [], position: "", min_games: 5, min_minutes: 200, min_mpg: 0,
    sort_by: "", desc: true, limit: 25, custom: [], comp_name: "Composite", comp: [], compare: false, corr_on: false, corr_level: "entity", corr_method: "pearson", uncertainty: true};
  let metrics = [], lastRes = null; const left = el("div", {class: "left"}), right = el("div", {class: "right"}); body.append(el("div", {class: "split"}, left, right)); body.style.padding = "6px";
  function buildQuery() {
    const q = {entity: S.entity, metrics: S.metrics.map((m) => ({id: m.id, transform: m.transform})), window: {}, filters: {}, ids: S.ids.map((x) => x.id), team_ids: S.team_ids.map((x) => x.id), min_games: +S.min_games || 1, min_minutes: +S.min_minutes || 0,
      min_mpg: +S.min_mpg || 0, limit: +S.limit || 25, uncertainty: S.uncertainty, compare: S.compare};
    if (S.seasons.length) q.window.seasons = S.seasons; if (S.date_from) q.window.date_from = S.date_from; if (S.date_to) q.window.date_to = S.date_to; if (S.last_n) q.window.last_n = +S.last_n;
    const f = q.filters; if (S.home !== "") f.home = S.home === "1"; if (S.result) f.result = S.result; if (S.started !== "") f.started = S.started === "1"; if (S.b2b !== "") f.b2b = S.b2b === "1"; if (S.ot !== "") f.ot = S.ot === "1";
    if (S.min_game_min !== "") f.min_minutes_game = +S.min_game_min; if (S.rest_min !== "") f.rest_min = +S.rest_min; if (S.opponents.length) f.opponents = S.opponents.map((x) => x.id);
    if (S.teammates_out.length) f.teammates_out = S.teammates_out.map((x) => x.id); if (S.teammates_in.length) f.teammates_in = S.teammates_in.map((x) => x.id); if (S.coach_ids.length) f.coach_ids = S.coach_ids.map((x) => x.id);
    if (S.position) q.position = S.position; if (S.sort_by) q.sort = {by: S.sort_by, desc: S.desc};
    const cu = S.custom.filter((c) => c.name && c.formula); if (cu.length) q.custom = cu; const cw = S.comp.filter((c) => c.token); if (cw.length) q.composite = {name: S.comp_name || "Composite", weights: Object.fromEntries(cw.map((c) => [c.token, +c.weight || 0]))};
    if (S.corr_on) q.correlate = {metrics: S.metrics.map(tokenOf), level: S.corr_level, method: S.corr_method};
    return q;
  }
  function entityPicker(type, list, label) {
    const dl = "dl-" + Math.random().toString(36).slice(2), inp = el("input", {list: dl, placeholder: label, style: {width: "180px"}}), dlist = el("datalist", {id: dl}), chips = el("div"), cache = {};
    function drawChips() { clear(chips); list.forEach((x, i) => chips.append(el("span", {class: "chip"}, x.name, el("button", {onclick: () => { list.splice(i, 1); drawChips(); }}, "✕")))); }
    inp.addEventListener("input", async () => { if (inp.value.length < 2) return; const r = await api(`/api/entities?type=${type()}&q=${encodeURIComponent(inp.value)}&limit=30`); clear(dlist); r.entities.forEach((e) => { cache[e.name] = e; dlist.append(el("option", {value: e.name})); }); });
    const add = () => { const e = cache[inp.value]; if (e && !list.some((x) => x.id === e.id)) { list.push({id: e.id, name: e.name}); inp.value = ""; drawChips(); } };
    inp.addEventListener("change", add); inp.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); }); drawChips(); return el("div", null, inp, dlist, chips);
  }
  const num = (k, w) => el("input", {type: "number", value: S[k], style: {width: (w || 64) + "px"}, onchange: (e) => { S[k] = e.target.value; }});
  const sel = (k, opts) => el("select", {onchange: (e) => { S[k] = e.target.value; }}, opts.map(([v, t]) => el("option", {value: v, selected: String(S[k]) === v}, t)));
  function validTransforms(m) { const meta = metrics.find((x) => x.id === m.id); if (!meta) return ["raw"]; return meta.transforms.filter((t) => !(S.entity !== "player" && t === "per36")); }
  function renderLeft() {
    clear(left);
    left.append(el("div", {class: "row"}, el("b", null, "Entity"), el("select", {onchange: (e) => { S.entity = e.target.value; S.ids = []; S.team_ids = []; S.metrics = S.metrics.filter((m) => { const mm = metrics.find((x) => x.id === m.id); return mm && mm.scope.includes(S.entity) && mm.status === "available"; }); renderLeft(); }},
      ["player", "team", "coach", "lineup"].map((t) => el("option", {value: t, selected: S.entity === t}, t))), el("span", {class: "small muted"}, S.entity === "lineup" ? "starting lineups" : "")));
    left.append(el("details", {open: true}, el("summary", null, `Metrics (${S.metrics.length} selected, no cap)`), el("div", null, selectedMetrics(), metricPicker())));
    left.append(el("details", {open: true}, el("summary", null, "Entities & window"), el("div", null, el("div", {class: "small muted"}, "Leave empty for all entities."), entityPicker(() => S.entity, S.ids, "add " + S.entity + "…"),
      S.entity === "player" ? el("div", null, el("div", {class: "small muted"}, "Restrict to team(s)"), entityPicker(() => "team", S.team_ids, "add team…"), el("div", {class: "row"}, "Position", sel("position", [["", "any"], ["PG", "PG"], ["SG", "SG"], ["SF", "SF"], ["PF", "PF"], ["C", "C"]]))) : null,
      el("div", {class: "row"}, "Seasons", ...(LAB.seasons || []).map((s) => el("label", null, el("input", {type: "checkbox", checked: S.seasons.includes(s), onchange: (e) => { S.seasons = e.target.checked ? [...S.seasons, s] : S.seasons.filter((x) => x !== s); }}), " " + s))),
      el("div", {class: "row"}, "From", el("input", {type: "date", value: S.date_from, onchange: (e) => { S.date_from = e.target.value; }}), "to", el("input", {type: "date", value: S.date_to, onchange: (e) => { S.date_to = e.target.value; }})),
      el("div", {class: "row"}, "Last N games per entity", num("last_n")))));
    left.append(el("details", null, el("summary", null, "Filters"), el("div", null,
      el("div", {class: "row"}, "Venue", sel("home", [["", "any"], ["1", "home"], ["0", "away"]]), "Result", sel("result", [["", "any"], ["W", "wins"], ["L", "losses"]])),
      el("div", {class: "row"}, "Back-to-back", sel("b2b", [["", "any"], ["1", "yes"], ["0", "no"]]), "OT", sel("ot", [["", "any"], ["1", "yes"], ["0", "no"]]), "Min rest days", num("rest_min", 50)),
      S.entity === "player" ? el("div", {class: "row"}, "Started", sel("started", [["", "any"], ["1", "yes"], ["0", "bench"]]), "Game minutes ≥", num("min_game_min", 54)) : null,
      el("div", {class: "small muted"}, "Opponent(s)"), entityPicker(() => "team", S.opponents, "opponent team…"), el("div", {class: "small muted"}, "Coach(es)"), entityPicker(() => "coach", S.coach_ids, "coach…"),
      el("div", {class: "small muted"}, "Teammate(s) OUT (did not play)"), entityPicker(() => "player", S.teammates_out, "player out…"), el("div", {class: "small muted"}, "Teammate(s) IN (played)"), entityPicker(() => "player", S.teammates_in, "player in…"))));
    left.append(el("details", null, el("summary", null, "Sample guards, sort, limit"), el("div", null, el("div", {class: "row"}, "Min games", num("min_games", 54), "Min total min", num("min_minutes", 64), "Min mpg", num("min_mpg", 54)),
      el("div", {class: "row"}, "Sort by", el("select", {onchange: (e) => { S.sort_by = e.target.value; }}, el("option", {value: ""}, "first metric"), S.metrics.map((m) => el("option", {value: tokenOf(m), selected: S.sort_by === tokenOf(m)}, tokenOf(m))), S.custom.filter((c) => c.name).map((c) => el("option", {value: c.name, selected: S.sort_by === c.name}, c.name)),
        S.comp.length ? el("option", {value: S.comp_name, selected: S.sort_by === S.comp_name}, S.comp_name) : null), sel("desc", [["true", "desc"], ["false", "asc"]]), "Limit", num("limit", 60)),
      el("label", null, el("input", {type: "checkbox", checked: S.uncertainty, onchange: (e) => { S.uncertainty = e.target.checked; }}), " bootstrap 90% intervals (≤120 rows)"))));
    left.append(el("details", null, el("summary", null, "Custom formulas & weighted composite"), el("div", null, customUi())));
    left.append(el("details", null, el("summary", null, "Compare / correlate"), el("div", null, el("label", null, el("input", {type: "checkbox", checked: S.compare, onchange: (e) => { S.compare = e.target.checked; }}), " comparison view (percentiles, deltas vs first row)"),
      el("div", {class: "row"}, el("label", null, el("input", {type: "checkbox", checked: S.corr_on, onchange: (e) => { S.corr_on = e.target.checked; }}), " correlation matrix of selected metrics"), sel("corr_level", [["entity", "entity level"], ["game", "game level"]]), sel("corr_method", [["pearson", "Pearson"], ["spearman", "Spearman"]])),
      el("div", {class: "small muted"}, "Counting stats correlate through minutes; use per-36 transforms for entity-level questions."))));
    left.append(el("div", {class: "row"}, el("button", {class: "primary", id: "wb-run", onclick: (e) => run(e.target)}, "Run query"), el("button", {onclick: () => showQuery()}, "View query JSON"), el("button", {onclick: exportCsv}, "CSV")));
    left.append(wsUi());
  }
  function selectedMetrics() {
    const box = el("div"); S.metrics.forEach((m, i) => { const meta = metrics.find((x) => x.id === m.id) || {name: m.id, kind: "observed"}; const tr = el("select", {onchange: (e) => { m.transform = e.target.value; }}, validTransforms(m).map((t) => el("option", {value: t, selected: t === m.transform}, t.replace("_", " "))));
      box.append(el("div", {class: "row"}, kindBadge(meta.kind), el("span", {title: meta.description}, meta.name), tr, el("button", {onclick: () => { S.metrics.splice(i, 1); renderLeft(); }}, "✕"))); });
    return box;
  }
  function metricPicker() {
    const q = el("input", {placeholder: "search metric registry…", style: {width: "170px"}}), cat = el("select", null, el("option", {value: ""}, "all categories"), [...new Set(metrics.map((m) => m.category))].map((c) => el("option", null, c))), list = el("div", {class: "mlist"});
    function draw() { clear(list); const ql = q.value.toLowerCase(); metrics.filter((m) => m.scope.includes(S.entity) && (!cat.value || m.category === cat.value) && (!ql || (m.id + " " + m.name + " " + m.aliases.join(" ") + " " + m.description).toLowerCase().includes(ql))).slice(0, 150).forEach((m) => {
      const on = S.metrics.some((x) => x.id === m.id); const off = m.status !== "available";
      list.append(el("div", {class: "mitem" + (off ? " off" : ""), title: off ? m.unavailable_reason : m.description + (m.expr ? "\nformula: " + m.expr : "")}, el("input", {type: "checkbox", checked: on, disabled: off, onchange: (e) => { if (e.target.checked) S.metrics.push({id: m.id, transform: m.transforms.includes("per_game") && m.unit === "count" && false ? "per_game" : "raw"}); else S.metrics = S.metrics.filter((x) => x.id !== m.id); renderLeft(); }}), m.name, el("span", {class: "grow", style: {flex: 1}}), kindBadge(m.kind), el("span", {class: "small muted"}, m.unit))); }); }
    q.addEventListener("input", draw); cat.addEventListener("change", draw); draw(); return el("div", null, el("div", {class: "row"}, q, cat), list);
  }
  function customUi() {
    const box = el("div"); const tokens = S.metrics.map(tokenOf).join(", ") || "(select metrics first)";
    box.append(el("div", {class: "small muted"}, "Formulas combine metric values per entity. Allowed: + - * / **n, abs sqrt log exp min max if_pos. Tokens: " + tokens + ". Any registry metric id with _per36/_per_game/_per100 suffix also works."));
    S.custom.forEach((c, i) => box.append(el("div", {class: "row"}, el("input", {value: c.name, placeholder: "name", style: {width: "90px"}, onchange: (e) => { c.name = e.target.value; }}), el("input", {value: c.formula, placeholder: "pts_per36*0.7 + ast_per36*0.3", style: {width: "170px"}, onchange: (e) => { c.formula = e.target.value; }}), el("button", {onclick: () => { S.custom.splice(i, 1); renderLeft(); }}, "✕"))));
    box.append(el("button", {onclick: () => { S.custom.push({name: "Custom " + (S.custom.length + 1), formula: ""}); renderLeft(); }}, "+ formula"));
    box.append(el("hr"), el("div", {class: "row"}, el("b", null, "Weighted z-score composite"), el("input", {value: S.comp_name, style: {width: "100px"}, onchange: (e) => { S.comp_name = e.target.value; }})));
    S.comp.forEach((c, i) => box.append(el("div", {class: "row"}, el("input", {value: c.token, list: "tokens", placeholder: "metric token", style: {width: "130px"}, onchange: (e) => { c.token = e.target.value; }}), "weight", el("input", {type: "number", step: 0.1, value: c.weight, style: {width: "64px"}, onchange: (e) => { c.weight = e.target.value; }}), el("button", {onclick: () => { S.comp.splice(i, 1); renderLeft(); }}, "✕"))));
    box.append(el("datalist", {id: "tokens"}, S.metrics.map((m) => el("option", {value: tokenOf(m)}))), el("button", {onclick: () => { S.comp.push({token: "", weight: 1}); renderLeft(); }}, "+ weighted metric"));
    return box;
  }
  function wsUi() {
    const name = el("input", {placeholder: "workspace name", style: {width: "130px"}}), sel2 = el("select", null, el("option", {value: ""}, "load saved query…"));
    api("/api/workspaces").then((r) => r.workspaces.filter((w) => w.spec && w.spec.kind === "workbench").forEach((w) => sel2.append(el("option", {value: w.name}, w.name))));
    sel2.addEventListener("change", async () => { const r = await api("/api/workspaces"); const w = r.workspaces.find((x) => x.name === sel2.value); if (w) { Object.assign(S, w.spec.state); name.value = w.name; renderLeft(); toast("Loaded " + w.name, true); } });
    return el("details", null, el("summary", null, "Saved workspaces"), el("div", {class: "row"}, name, el("button", {onclick: async () => { if (!name.value) return toast("Name it first"); await api("/api/workspaces", {body: {name: name.value, spec: {kind: "workbench", state: S}}}); toast("Saved", true); renderLeft(); }}, "Save"), sel2,
      el("button", {class: "danger", onclick: async () => { if (sel2.value) { await api("/api/workspaces", {method: "DELETE", body: {name: sel2.value}}); toast("Deleted", true); renderLeft(); } }}, "Delete")));
  }
  function showQuery() { modal("Structured query (what the engine runs)", el("pre", {class: "mono small"}, JSON.stringify(buildQuery(), null, 2))); }
  function exportCsv() { if (!lastRes) return toast("Run a query first"); const cols = lastRes.columns.map((c) => c.label); downloadText("workbench.csv", toCsv(["rank", "name", "gp", "min"].concat(cols), lastRes.rows.map((r) => [r.rank, r.name, r.games, r.minutes].concat(cols.map((c) => r.values[c])))), "text/csv"); }
  async function run(btn) {
    if (!S.metrics.length && !S.custom.length && !S.comp.length) return toast("Select at least one metric");
    clear(right); right.append(el("span", {class: "spin"}), " computing…"); const q = buildQuery();
    try { const res = await api("/api/workbench", {body: {query: q}}); lastRes = res; clear(right);
      right.append(renderResult(res, {onRow: (row) => showSupport(res.query, row)}), el("div", {class: "small muted"}, "Click a row to inspect the exact supporting games."), chartSection(res));
    } catch (e) { clear(right); right.append(el("div", {class: "banner bad"}, e.message)); }
  }
  async function init() { metrics = await getMetrics(); LAB.seasons = (await api("/api/entities?type=team&limit=1")).seasons; renderLeft(); right.append(el("div", {class: "muted"}, "Choose metrics, entities, windows and filters, then Run query. Every number is computed from stored game rows; hover headers for formula and version.")); }
  init();
  return {open: (p) => { if (p && p.state) { Object.assign(S, p.state); renderLeft(); if (p.run) run(); } }, S, queryToState: null};
}, {x: 20, y: 20, w: 1100, h: 600});

/* ---------- Natural-Language Research ---------- */
registerPanel("research", "Natural-Language Research + Query Inspector", (body) => {
  const input = el("textarea", {rows: 2, placeholder: "Ask about the data… e.g. “Top 10 players by points per 36 minutes this season with at least 300 minutes”"}), chips = el("div", {class: "row"}), out = el("div");
  const ask = el("button", {class: "primary", onclick: () => go()}, "Ask"); body.append(input, el("div", {class: "row"}, ask, el("span", {class: "small muted"}, "Questions become a structured query that a deterministic engine runs on stored game rows. Nothing is invented; unknowns are refused.")), chips, out);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) go(); });
  api("/api/nlq/examples").then((r) => r.examples.forEach((t) => chips.append(el("a", {class: "chip", onclick: () => { input.value = t; go(); }}, t))));
  async function go() { const text = input.value.trim(); if (!text) return; clear(out); out.append(el("span", {class: "spin"}), " thinking…"); try { show(await api("/api/nlq", {body: {text}})); } catch (e) { clear(out); out.append(el("div", {class: "banner bad"}, e.message)); } }
  function show(r) {
    clear(out);
    if (!r.ok) { out.append(el("div", {class: "banner bad"}, r.error || r.message), r.ambiguities && r.ambiguities.length ? el("div", null, r.ambiguities.map((a) => el("div", null, `“${a.token}” could be: `, a.candidates.map((c) => el("a", {class: "chip", onclick: () => { input.value = input.value.replace(new RegExp(a.token, "i"), c.name); go(); }}, c.name + (c.team ? " (" + c.team + ")" : "")))))) : null,
      r.suggestions ? el("div", {class: "small muted"}, "Try: ", r.suggestions.map((s) => el("div", null, "• " + s))) : null, r.note ? el("div", {class: "small muted"}, r.note) : null); return; }
    out.append(el("div", {class: "banner good"}, el("b", null, r.headline)));
    const ins = r.inspector; out.append(renderResult(r.result, {onRow: (row) => showSupport(r.query, row)}));
    out.append(el("h4", null, "Query Inspector"));
    const det = (title, content, open) => el("details", {open: !!open}, el("summary", null, title), el("div", null, content));
    out.append(det("Interpretation", el("div", null, kv({"Your words": ins.original_text, "Entity type": r.query.entity, "Mode": ins.mode}), (ins.interpretation.entities || []).map((e) => el("div", null, `${e.kind}: ${e.name} (id ${e.id})`)),
      (ins.interpretation.metrics || []).map((m) => el("div", null, `metric: ${m.name} · ${m.transform}`)), (ins.notes || []).map((n) => el("div", {class: "banner small"}, n)),
      ins.unresolved_words && ins.unresolved_words.length ? el("div", {class: "banner warn small"}, "Words not used by the interpretation: " + ins.unresolved_words.join(", ")) : null), true));
    out.append(det("Window & filters", el("div", null, ins.window.map((w) => el("div", null, "• " + w)), (ins.filters.length ? ins.filters : ["no additional filters"]).map((f) => el("div", null, "• " + f))), true));
    out.append(det("Formulas used", dataTable([{key: "label", label: "Column"}, {key: "kind", label: "Kind", render: (f) => kindBadge(f.kind)}, {key: "formula", label: "Formula", render: (f) => el("span", {class: "mono small"}, f.formula)}, {key: "version", label: "Ver"}, {key: "unit", label: "Unit"}, {key: "source", label: "Source"}], ins.formulas), true));
    out.append(det("Joins & sources", el("div", null, el("div", null, "Logical joins (executed as in-memory columnar aggregation, no generated SQL):"), ins.joins.map((j) => el("div", {class: "small"}, "• " + j)),
      ins.sources.map((s) => el("div", {class: "banner small " + (s.synthetic ? "warn" : "")}, `Dataset: ${s.dataset}${s.synthetic ? " (SYNTHETIC)" : ""} · data through ${s.last_game_date}. ${s.note}`)), el("div", {class: "small muted"}, ins.possession_basis))));
    out.append(det("Sample size & uncertainty", el("div", null, kv({"Game rows matched": ins.sample_size.rows_matched, "Entities matched": ins.sample_size.entities_matched, "Rows shown": ins.sample_size.shown, "Games (top row)": ins.sample_size.games_by_top_row}), el("div", {class: "small"}, ins.uncertainty.method),
      !ins.uncertainty.available ? el("div", {class: "small muted"}, "No intervals for this query type (e.g. correlation).") : null, ins.warnings.map((w) => el("div", {class: "banner warn small"}, w))), true));
    if (ins.supporting_games && ins.supporting_games.rows.length) { const s = ins.supporting_games; const cols = [{key: "date", label: "Date"}, {key: "opponent", label: "Opp"}, {key: "result", label: "W/L"}, {key: "score", label: "Score"}];
      Object.keys(s.rows[0].stats).forEach((k) => cols.push({key: "s" + k, label: k, num: true, render: (r) => fmt(r.stats[k]), sort: (r) => r.stats[k]}));
      out.append(det(`Supporting games for ${s.name} (${s.games_total} games)`, dataTable(cols, s.rows, {sortKey: "date", desc: true}), true)); }
    const ta = el("textarea", {rows: 12, class: "mono"}); ta.value = JSON.stringify(ins.structured_query, null, 2);
    out.append(det("Editable structured query (edit and re-run)", el("div", null, ta, el("div", {class: "row"}, el("button", {class: "primary", onclick: async () => { try { show(await api("/api/query/rerun", {body: {query: JSON.parse(ta.value)}})); } catch (e) { toast(e.message); } }}, "Re-run edited query"),
      el("button", {onclick: () => { openPanel("workbench"); toast("Use the Workbench controls to rebuild; JSON is in the query box above.", true); }}, "Open Workbench")))));
  }
  return {open: (p) => { if (p && p.text) { input.value = p.text; go(); } }};
}, {x: 40, y: 30, w: 980, h: 620});
