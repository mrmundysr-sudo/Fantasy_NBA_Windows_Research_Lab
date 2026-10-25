/* NBA Research Lab UI core: api client, DOM helpers, panel manager, tables and SVG charts. No dependencies. */
"use strict";
const TOKEN = document.querySelector('meta[name="lab-token"]').content;
const NS = "http://www.w3.org/2000/svg";
const LAB = {status: null, panels: {}, builders: {}, order: [], z: 10, toastTimer: null};

async function api(path, opts) {
  opts = opts || {};
  const res = await fetch(path, {method: opts.method || (opts.body ? "POST" : "GET"),
    headers: {"X-Lab-Token": TOKEN, "Content-Type": "application/json"}, body: opts.body ? JSON.stringify(opts.body) : undefined});
  let data = null;
  try { data = await res.json(); } catch (e) { data = {error: "bad response"}; }
  if (!res.ok) { const err = new Error(data.error || ("HTTP " + res.status)); err.data = data; throw err; }
  return data;
}

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    const v = attrs[k];
    if (v == null || v === false) continue;
    if (k === "class") e.className = v; else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k === "value") e.value = v; else if (v === true) e.setAttribute(k, ""); else e.setAttribute(k, v);
  }
  for (const kid of kids.flat(3)) { if (kid == null || kid === false) continue; e.append(kid.nodeType ? kid : document.createTextNode(String(kid))); }
  return e;
}
function svg(tag, attrs, ...kids) {
  const e = document.createElementNS(NS, tag);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  for (const kid of kids.flat()) if (kid != null) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return e;
}
const $ = (s, r) => (r || document).querySelector(s);
function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
function fmt(v, d) {
  if (v == null || (typeof v === "number" && !isFinite(v))) return "–";
  if (typeof v !== "number") return String(v);
  d = d == null ? (Math.abs(v) >= 100 ? 1 : 2) : d;
  return v.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: d});
}
function badge(text, cls, title) { return el("span", {class: "badge " + (cls || ""), title: title || ""}, text); }
function kindBadge(k) { return badge(k, "b-" + k, {observed: "Directly recorded", derived: "Computed from observed values by a formula", inferred: "Inferred, not directly observed",
  projected: "Model projection", simulated: "Simulated", unavailable: "Not available in this dataset"}[k] || k); }
function toast(msg, ok) {
  const t = el("div", {class: "toast" + (ok ? " ok" : "")}, msg);
  document.body.append(t); clearTimeout(LAB.toastTimer);
  setTimeout(() => t.remove(), ok ? 3000 : 7000);
}
function busy(node, on) { if (on) node.dataset.old = node.innerHTML, node.innerHTML = '<span class="spin"></span> working…', node.disabled = true; else node.innerHTML = node.dataset.old || node.innerHTML, node.disabled = false; }
async function guarded(node, fn) {
  if (node) busy(node, true);
  try { return await fn(); } catch (e) { toast(e.message || String(e)); return null; } finally { if (node) busy(node, false); }
}
function modal(title, content) {
  const bg = el("div", {class: "modal-bg", onclick: (e) => { if (e.target === bg) bg.remove(); }});
  const m = el("div", {class: "modal"}, el("div", {class: "row"}, el("b", null, title), el("span", {style: {flex: 1}}), el("button", {onclick: () => bg.remove()}, "Close")), content);
  bg.append(m); document.body.append(bg); return bg;
}
function downloadText(name, text, type) {
  const a = el("a", {href: URL.createObjectURL(new Blob([text], {type: type || "text/plain"})), download: name}); document.body.append(a); a.click(); a.remove();
}

/* ---------- panel manager ---------- */
const LAYOUT_KEY = "nbalab.layout.v1";
function loadLayout() { try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}"); } catch (e) { return {}; } }
function saveLayout() {
  const out = {};
  for (const id in LAB.panels) { const p = LAB.panels[id]; out[id] = {x: p.x, y: p.y, w: p.w, h: p.h, min: p.min, open: true, z: p.z}; }
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(out)); } catch (e) {}
  return out;
}
function registerPanel(id, title, builder, def) { LAB.builders[id] = {title, builder, def: def || {x: 60, y: 40, w: 760, h: 520}}; }
function openPanel(id, props) {
  if (LAB.panels[id]) { focusPanel(id); if (props && LAB.panels[id].api && LAB.panels[id].api.open) LAB.panels[id].api.open(props); return LAB.panels[id]; }
  const b = LAB.builders[id]; if (!b) return;
  const saved = loadLayout()[id] || {};
  const d = Object.assign({}, b.def, saved);
  const desk = $("#desk");
  const W = desk.clientWidth, H = desk.clientHeight;
  d.w = Math.min(d.w, W - 10); d.h = Math.min(d.h, H - 6);
  d.x = Math.max(0, Math.min(d.x, W - 120)); d.y = Math.max(0, Math.min(d.y, H - 40));
  const body = el("div", {class: "body"});
  const root = el("section", {class: "panel", id: "panel-" + id});
  const hdr = el("header", null, el("span", {class: "title"}, b.title),
    el("button", {title: "Minimize", onclick: () => { p.min = !p.min; root.classList.toggle("min", p.min); saveLayout(); }}, "–"),
    el("button", {title: "Maximize / restore", onclick: () => toggleMax(id)}, "▢"),
    el("button", {title: "Close", onclick: () => closePanel(id)}, "✕"));
  const grip = el("div", {class: "grip"});
  root.append(hdr, body, grip);
  const p = {id, root, body, x: d.x, y: d.y, w: d.w, h: d.h, min: !!d.min, z: ++LAB.z, api: null, max: false};
  LAB.panels[id] = p; desk.append(root); place(p);
  if (p.min) root.classList.add("min");
  root.addEventListener("pointerdown", () => focusPanel(id));
  drag(hdr, p); resize(grip, p);
  p.api = b.builder(body, p) || {};
  if (props && p.api.open) p.api.open(props);
  syncLaunchers(); saveLayout();
  return p;
}
function place(p) { const s = p.root.style; s.left = p.x + "px"; s.top = p.y + "px"; s.width = p.w + "px"; s.height = p.h + "px"; s.zIndex = p.z; }
function focusPanel(id) { const p = LAB.panels[id]; if (!p) return; p.z = ++LAB.z; p.root.style.zIndex = p.z; for (const k in LAB.panels) LAB.panels[k].root.classList.toggle("focus", k === id); }
function closePanel(id) { const p = LAB.panels[id]; if (!p) return; if (p.api && p.api.close) p.api.close(); p.root.remove(); delete LAB.panels[id]; syncLaunchers(); saveLayout(); }
function toggleMax(id) {
  const p = LAB.panels[id], desk = $("#desk");
  if (!p.max) { p.prev = {x: p.x, y: p.y, w: p.w, h: p.h}; p.x = 0; p.y = 0; p.w = desk.clientWidth - 2; p.h = desk.clientHeight - 2; p.max = true; p.min = false; p.root.classList.remove("min"); }
  else { Object.assign(p, p.prev); p.max = false; }
  place(p); saveLayout();
}
function drag(h, p) {
  h.addEventListener("pointerdown", (e) => {
    if (e.target.tagName === "BUTTON" || p.max) return;
    const sx = e.clientX, sy = e.clientY, ox = p.x, oy = p.y; h.setPointerCapture(e.pointerId);
    const mv = (ev) => { const desk = $("#desk"); p.x = Math.max(-p.w + 80, Math.min(desk.clientWidth - 80, ox + ev.clientX - sx)); p.y = Math.max(0, Math.min(desk.clientHeight - 30, oy + ev.clientY - sy)); place(p); };
    const up = () => { h.removeEventListener("pointermove", mv); h.removeEventListener("pointerup", up); saveLayout(); };
    h.addEventListener("pointermove", mv); h.addEventListener("pointerup", up);
  });
  h.addEventListener("dblclick", () => toggleMax(p.id));
}
function resize(g, p) {
  g.addEventListener("pointerdown", (e) => {
    e.stopPropagation(); const sx = e.clientX, sy = e.clientY, ow = p.w, oh = p.h; g.setPointerCapture(e.pointerId);
    const mv = (ev) => { p.w = Math.max(300, ow + ev.clientX - sx); p.h = Math.max(140, oh + ev.clientY - sy); place(p); if (p.api && p.api.resized) p.api.resized(); };
    const up = () => { g.removeEventListener("pointermove", mv); g.removeEventListener("pointerup", up); saveLayout(); };
    g.addEventListener("pointermove", mv); g.addEventListener("pointerup", up);
  });
}
function openIds() { return Object.keys(LAB.panels).sort((a, b) => LAB.panels[a].z - LAB.panels[b].z); }
function tile() {
  const ids = openIds(); if (!ids.length) return; const desk = $("#desk"); const W = desk.clientWidth, H = desk.clientHeight;
  const cols = ids.length <= 1 ? 1 : (ids.length <= 4 ? 2 : 3), rows = Math.ceil(ids.length / cols);
  ids.forEach((id, i) => { const p = LAB.panels[id]; p.max = false; p.min = false; p.root.classList.remove("min"); const c = i % cols, r = Math.floor(i / cols);
    p.w = Math.floor(W / cols) - 6; p.h = Math.floor(H / rows) - 6; p.x = c * Math.floor(W / cols) + 3; p.y = r * Math.floor(H / rows) + 3; place(p); if (p.api && p.api.resized) p.api.resized(); });
  saveLayout();
}
function cascade() { openIds().forEach((id, i) => { const p = LAB.panels[id]; p.max = false; p.x = 20 + i * 28; p.y = 10 + i * 28; p.w = Math.min(820, $("#desk").clientWidth - p.x - 20); p.h = Math.min(560, $("#desk").clientHeight - p.y - 10); place(p); }); saveLayout(); }
function syncLaunchers() { document.querySelectorAll("#launchers button").forEach((b) => b.classList.toggle("primary", !!LAB.panels[b.dataset.id])); }

/* ---------- tables ---------- */
/* columns: [{key,label,num?,render?(row)->Node|string,sort?(row)->value,title?}] */
function dataTable(columns, rows, opts) {
  opts = opts || {}; const state = {sortKey: opts.sortKey, desc: opts.desc !== false, shown: opts.pageSize || 150}; const wrap = el("div");
  function draw() {
    clear(wrap);
    let data = rows.slice();
    if (state.sortKey != null) { const col = columns.find((c) => c.key === state.sortKey); if (col) {
      const val = (r) => col.sort ? col.sort(r) : r[col.key]; data.sort((a, b) => { const x = val(a), y = val(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return (x < y ? -1 : x > y ? 1 : 0) * (state.desc ? -1 : 1); }); } }
    const thead = el("tr", null, columns.map((c) => el("th", {class: c.num ? "num" : "", title: c.title || "", onclick: () => { if (state.sortKey === c.key) state.desc = !state.desc; else { state.sortKey = c.key; state.desc = !!c.num; } draw(); }},
      c.label, state.sortKey === c.key ? (state.desc ? " ▾" : " ▴") : "")));
    const body = data.slice(0, state.shown).map((r) => { const tr = el("tr", {class: opts.onRow ? "click" : ""}, columns.map((c) => { const v = c.render ? c.render(r) : (c.num ? fmt(r[c.key]) : (r[c.key] == null ? "–" : r[c.key])); return el("td", {class: c.num ? "num" : ""}, v); }));
      if (opts.onRow) tr.addEventListener("click", () => { wrap.querySelectorAll("tr.sel").forEach((x) => x.classList.remove("sel")); tr.classList.add("sel"); opts.onRow(r); }); return tr; });
    wrap.append(el("table", {class: "t"}, el("thead", null, thead), el("tbody", null, body)));
    if (data.length > state.shown) wrap.append(el("div", {class: "row"}, el("button", {onclick: () => { state.shown += 300; draw(); }}, `Show more (${data.length - state.shown} hidden)`),
      el("button", {onclick: () => { state.shown = data.length; draw(); }}, "Show all")));
    if (!data.length) wrap.append(el("div", {class: "muted", style: {padding: "6px"}}, opts.empty || "No rows."));
  }
  draw(); return wrap;
}
function toCsv(headers, rows) { const q = (v) => { v = v == null ? "" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }; return [headers.map(q).join(",")].concat(rows.map((r) => r.map(q).join(","))).join("\n"); }

/* ---------- SVG charts ---------- */
function scale(d0, d1, r0, r1) { const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0); return (v) => r0 + (v - d0) * k; }
function niceTicks(lo, hi, n) { if (lo === hi) { lo -= 1; hi += 1; } const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0))); const f = step0 / mag; const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10)); return out; }
function chartFrame(w, h, m, xd, yd, xl, yl) {
  const s = svg("svg", {width: w, height: h, viewBox: `0 0 ${w} ${h}`}); const X = scale(xd[0], xd[1], m.l, w - m.r), Y = scale(yd[0], yd[1], h - m.b, m.t);
  for (const t of niceTicks(yd[0], yd[1], 5)) { s.append(svg("line", {class: "grid", x1: m.l, x2: w - m.r, y1: Y(t), y2: Y(t)}), svg("text", {x: m.l - 4, y: Y(t) + 3, "text-anchor": "end"}, fmt(t))); }
  for (const t of niceTicks(xd[0], xd[1], 6)) { s.append(svg("line", {class: "grid", y1: m.t, y2: h - m.b, x1: X(t), x2: X(t)}), svg("text", {x: X(t), y: h - m.b + 12, "text-anchor": "middle"}, fmt(t))); }
  s.append(svg("line", {class: "ax", x1: m.l, x2: w - m.r, y1: h - m.b, y2: h - m.b}), svg("line", {class: "ax", x1: m.l, x2: m.l, y1: m.t, y2: h - m.b}));
  if (xl) s.append(svg("text", {x: (m.l + w - m.r) / 2, y: h - 4, "text-anchor": "middle"}, xl)); if (yl) s.append(svg("text", {x: 10, y: m.t - 2, "text-anchor": "start"}, yl));
  return {s, X, Y};
}
const PALETTE = ["#58a6ff", "#3ecf8e", "#f0b429", "#ff6b6b", "#b392f0", "#4cc9c0", "#ff9f43", "#9aa7bd"];
function scatterChart(points, o) { /* points:[{x,y,label,group,onclick}] */
  o = o || {}; const w = o.w || 640, h = o.h || 360, m = {l: 46, r: 14, t: 16, b: 34};
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y); const pad = (a) => { const lo = Math.min(...a), hi = Math.max(...a), d = (hi - lo) * 0.05 || 1; return [lo - d, hi + d]; };
  const xd = pad(xs), yd = pad(ys); const {s, X, Y} = chartFrame(w, h, m, xd, yd, o.xl, o.yl);
  const groups = [...new Set(points.map((p) => p.group))]; const col = (g) => PALETTE[groups.indexOf(g) % PALETTE.length];
  points.forEach((p) => { const c = svg("circle", {cx: X(p.x), cy: Y(p.y), r: 3.6, fill: col(p.group), "fill-opacity": 0.75, stroke: "#0d1117", "stroke-width": 0.6}); c.append(svg("title", null, `${p.label}\n${o.xl || "x"}: ${fmt(p.x)}\n${o.yl || "y"}: ${fmt(p.y)}`));
    if (p.onclick) { c.style.cursor = "pointer"; c.addEventListener("click", p.onclick); } s.append(c); });
  if (o.fit && points.length > 3) { const n = points.length, mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n; let sxx = 0, sxy = 0; points.forEach((p) => { sxx += (p.x - mx) ** 2; sxy += (p.x - mx) * (p.y - my); }); if (sxx > 0) { const b = sxy / sxx, a = my - b * mx; s.append(svg("line", {x1: X(xd[0]), x2: X(xd[1]), y1: Y(a + b * xd[0]), y2: Y(a + b * xd[1]), stroke: "#f0b429", "stroke-dasharray": "4 3", "stroke-width": 1.3})); } }
  (o.labelTop || []).forEach((p) => s.append(svg("text", {x: X(p.x) + 5, y: Y(p.y) - 4}, p.label)));
  const legend = el("div", {class: "legend small"}, groups.filter((g) => g != null).map((g) => el("span", null, el("i", {class: "dot", style: {background: col(g)}}), String(g)))); return el("div", null, s, legend);
}
function barChart(items, o) { /* items:[{label,value,lo,hi,onclick}] horizontal bars */
  o = o || {}; const rowH = 18, w = o.w || 640, m = {l: o.lw || 150, r: 50, t: 6, b: 18}; const h = m.t + m.b + items.length * rowH;
  const vals = items.flatMap((i) => [i.value, i.lo, i.hi].filter((v) => v != null)); const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals); const X = scale(lo, hi, m.l, w - m.r);
  const s = svg("svg", {width: w, height: h, viewBox: `0 0 ${w} ${h}`}); niceTicks(lo, hi, 5).forEach((t) => { s.append(svg("line", {class: "grid", x1: X(t), x2: X(t), y1: m.t, y2: h - m.b}), svg("text", {x: X(t), y: h - 5, "text-anchor": "middle"}, fmt(t))); });
  items.forEach((it, i) => { const y = m.t + i * rowH; if (it.value == null) return; const x0 = X(0), x1 = X(it.value);
    const r = svg("rect", {x: Math.min(x0, x1), y: y + 3, width: Math.abs(x1 - x0), height: rowH - 6, class: it.value >= 0 ? "bar-pos" : "bar-neg", "fill-opacity": 0.8}); r.append(svg("title", null, `${it.label}: ${fmt(it.value)}`)); if (it.onclick) { r.style.cursor = "pointer"; r.addEventListener("click", it.onclick); } s.append(r);
    if (it.lo != null && it.hi != null) s.append(svg("line", {x1: X(it.lo), x2: X(it.hi), y1: y + rowH / 2, y2: y + rowH / 2, stroke: "#dbe2ee", "stroke-width": 1}), svg("line", {x1: X(it.lo), x2: X(it.lo), y1: y + 5, y2: y + rowH - 5, stroke: "#dbe2ee"}), svg("line", {x1: X(it.hi), x2: X(it.hi), y1: y + 5, y2: y + rowH - 5, stroke: "#dbe2ee"}));
    s.append(svg("text", {x: m.l - 4, y: y + rowH / 2 + 3, "text-anchor": "end", style: "fill:#dbe2ee"}, it.label.length > 24 ? it.label.slice(0, 23) + "…" : it.label), svg("text", {x: x1 + (it.value >= 0 ? 4 : -4), y: y + rowH / 2 + 3, "text-anchor": it.value >= 0 ? "start" : "end"}, fmt(it.value))); });
  s.append(svg("line", {class: "ax", x1: X(0), x2: X(0), y1: m.t, y2: h - m.b})); return s;
}
function histChart(values, o) {
  o = o || {}; const v = values.filter((x) => x != null && isFinite(x)); if (!v.length) return el("div", {class: "muted"}, "No values."); const w = o.w || 640, h = o.h || 240, m = {l: 40, r: 10, t: 12, b: 30};
  const lo = Math.min(...v), hi = Math.max(...v), nb = Math.min(30, Math.max(6, Math.round(Math.sqrt(v.length) * 1.6))), bw = (hi - lo) / nb || 1, bins = new Array(nb).fill(0);
  v.forEach((x) => { bins[Math.min(nb - 1, Math.floor((x - lo) / bw))]++; }); const {s, X, Y} = chartFrame(w, h, m, [lo, hi || lo + 1], [0, Math.max(...bins)], o.xl, "count");
  bins.forEach((c, i) => s.append(svg("rect", {x: X(lo + i * bw) + 1, y: Y(c), width: Math.max(1, X(lo + (i + 1) * bw) - X(lo + i * bw) - 2), height: Y(0) - Y(c), fill: "#58a6ff", "fill-opacity": 0.75}))); return s;
}
function lineChart(series, o) { /* series:[{name,points:[{x,y}]}] */
  o = o || {}; const w = o.w || 640, h = o.h || 220, m = {l: 40, r: 10, t: 12, b: 28}; const all = series.flatMap((s) => s.points); if (!all.length) return el("div", {class: "muted"}, "No data.");
  const xd = [Math.min(...all.map((p) => p.x)), Math.max(...all.map((p) => p.x))], yd = [Math.min(...all.map((p) => p.y)), Math.max(...all.map((p) => p.y))]; const {s, X, Y} = chartFrame(w, h, m, xd, yd, o.xl, o.yl);
  series.forEach((ser, i) => s.append(svg("polyline", {points: ser.points.map((p) => `${X(p.x)},${Y(p.y)}`).join(" "), fill: "none", stroke: PALETTE[i % PALETTE.length], "stroke-width": 1.6}))); return s;
}
function sparkline(vals, w, h) { w = w || 160; h = h || 34; if (!vals.length) return el("span"); const lo = Math.min(...vals), hi = Math.max(...vals), X = scale(0, Math.max(vals.length - 1, 1), 2, w - 2), Y = scale(lo, hi === lo ? lo + 1 : hi, h - 3, 3);
  return svg("svg", {width: w, height: h}, svg("polyline", {points: vals.map((v, i) => `${X(i)},${Y(v)}`).join(" "), fill: "none", stroke: "#58a6ff", "stroke-width": 1.5})); }
function boxPlot(q, o) { /* q:{p10,p25,p50,p75,p90,mean} */
  o = o || {}; const w = o.w || 360, h = 44; const lo = o.lo != null ? o.lo : Math.min(0, q.p10), hi = o.hi != null ? o.hi : q.p90 * 1.15 + 1, X = scale(lo, hi, 8, w - 8); const s = svg("svg", {width: w, height: h});
  s.append(svg("line", {x1: X(q.p10), x2: X(q.p90), y1: 18, y2: 18, stroke: "#8a96aa"}), svg("rect", {x: X(q.p25), y: 8, width: Math.max(1, X(q.p75) - X(q.p25)), height: 20, fill: "#1f4f8a", stroke: "#58a6ff"}),
    svg("line", {x1: X(q.p50), x2: X(q.p50), y1: 8, y2: 28, stroke: "#fff", "stroke-width": 2}), svg("circle", {cx: X(q.mean), cy: 18, r: 3.2, fill: "#f0b429"}));
  [["p10", q.p10], ["p50", q.p50], ["p90", q.p90]].forEach(([n, v]) => s.append(svg("text", {x: X(v), y: 41, "text-anchor": "middle"}, n + " " + fmt(v, 1)))); return s; }
function heatColor(r) { if (r == null) return ""; const a = Math.min(1, Math.abs(r)); return r >= 0 ? `rgba(62,207,142,${0.12 + 0.55 * a})` : `rgba(255,107,107,${0.12 + 0.55 * a})`; }
function kv(obj) { const g = el("div", {class: "kv"}); for (const k in obj) g.append(el("div", null, k), el("div", null, obj[k] == null ? "–" : obj[k])); return g; }
function tabs(defs, start) {
  const bar = el("div", {class: "tabs"}), area = el("div"); let cur = start || defs[0][0];
  function show(id) { cur = id; [...bar.children].forEach((b) => b.classList.toggle("on", b.dataset.id === id)); clear(area); const d = defs.find((x) => x[0] === id); const c = d[2](); area.append(c && c.nodeType ? c : document.createTextNode("")); }
  defs.forEach((d) => bar.append(el("button", {"data-id": d[0], onclick: () => show(d[0])}, d[1]))); show(cur); return {root: el("div", null, bar, area), show};
}
function statusBadge(s) { return badge(s.toUpperCase(), "b-" + s); }
