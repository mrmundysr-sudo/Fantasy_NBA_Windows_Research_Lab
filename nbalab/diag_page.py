PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NBA Source Diagnostics</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202a;--mut:#5b6672;--line:#d9dee4;--acc:#1f5fbf;--accink:#fff;--ok:#0b7a43;--okbg:#e2f4ea;--warn:#8a5a00;--warnbg:#fdf0d5;--bad:#a1261c;--badbg:#fbe4e1;--idle:#4a5560;--idlebg:#eceff2}
@media (prefers-color-scheme:dark){:root{--bg:#12161b;--card:#1b2128;--ink:#e8edf2;--mut:#9aa6b2;--line:#303942;--acc:#5b9bff;--accink:#08111f;--ok:#5fd394;--okbg:#12301f;--warn:#f0c15c;--warnbg:#33290f;--bad:#ff8a80;--badbg:#3a1a17;--idle:#aab4be;--idlebg:#262e36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 8px}
.sub{color:var(--mut);margin:0 0 18px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 14px}
.src{border-top:1px solid var(--line);padding:12px 0}.src:first-of-type{border-top:0}.src h3{font-size:15px;margin:0}
.terms{background:var(--idlebg);border-radius:8px;padding:10px 12px;margin:8px 0;font-size:13.5px}.terms a{color:var(--acc)}
label.ck{display:flex;gap:8px;align-items:flex-start;margin:6px 0}label.ck input{margin-top:4px;width:16px;height:16px}
button{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:9px 16px;cursor:pointer}
button.primary{background:var(--acc);color:var(--accink);border-color:var(--acc);font-weight:600}button:disabled{opacity:.45;cursor:not-allowed}
button:focus-visible,input:focus-visible,a:focus-visible{outline:3px solid var(--acc);outline-offset:2px}
.bar{height:12px;border-radius:6px;background:var(--idlebg);overflow:hidden}.bar i{display:block;height:100%;background:var(--acc);width:0;transition:width .3s}
.row{display:grid;grid-template-columns:minmax(150px,1.2fr) auto 80px;gap:10px;align-items:center;padding:6px 0;border-top:1px solid var(--line);font-size:14px}
.row:first-of-type{border-top:0}.chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;font-weight:600;white-space:nowrap}
.c-ok{background:var(--okbg);color:var(--ok)}.c-warn{background:var(--warnbg);color:var(--warn)}.c-bad{background:var(--badbg);color:var(--bad)}.c-idle{background:var(--idlebg);color:var(--idle)}
.det{grid-column:1/-1;color:var(--mut);font-size:12.5px;overflow-wrap:anywhere;margin:-2px 0 2px}.ms{text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}
.sum{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0}.pill{padding:6px 12px;border-radius:8px;font-weight:600}
.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.path{font-family:Consolas,monospace;font-size:13px;overflow-wrap:anywhere}
.spin{display:inline-block;width:10px;height:10px;border:2px solid var(--acc);border-right-color:transparent;border-radius:50%;animation:s 0.8s linear infinite;margin-right:6px;vertical-align:-1px}@keyframes s{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spin{animation:none}.bar i{transition:none}}
.hint{color:var(--mut);font-size:13px}
</style></head><body><main>
<h1>NBA Source Diagnostics</h1>
<p class="sub">Checks which NBA data sources answer from <b>this</b> computer. It makes a few small, spaced-out requests per source and saves a report you can send back. Nothing is installed or changed.</p>

<section class="card" id="setup" aria-labelledby="h-setup"><h2 id="h-setup">1. Choose sources and confirm terms</h2>
<p class="hint">Each source is run by someone else, with its own terms of use. Before testing, read the linked terms and tick the box to confirm. That confirmation is yours: you are the one responsible for how you use each source.</p>
<div id="srcs"></div>
<div class="actions" style="margin-top:10px"><button class="primary" id="run" disabled>Run Tests</button><span class="hint" id="runhint">Tick the confirmation for each source you want to test.</span></div></section>

<section class="card" id="prog" hidden aria-labelledby="h-prog"><h2 id="h-prog">2. Progress</h2>
<div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="barw"><i id="bar"></i></div>
<p class="hint" id="ptext" aria-live="polite"></p><div id="steps"></div></section>

<section class="card" id="done" hidden aria-labelledby="h-done"><h2 id="h-done">3. Results and report</h2>
<div class="sum" id="sum"></div><p class="hint" id="explain"></p>
<div class="actions"><button class="primary" id="exp">Export Report</button><button id="reveal" hidden>Show in folder</button><button id="again">Run again</button></div>
<p class="path" id="where" aria-live="polite"></p></section>
<p class="hint">You can close this tab when you are done; the program also closes itself shortly after. <button id="quit" style="padding:3px 10px;font-size:13px">Quit now</button></p>
</main>
<script>
"use strict";
const T = new URLSearchParams(location.search).get("t") || "__TOKEN__";
const $ = (s) => document.querySelector(s);
const el = (tag, attrs, ...kids) => { const e = document.createElement(tag); for (const k in (attrs || {})) { if (k === "class") e.className = attrs[k]; else if (k.startsWith("on")) e[k] = attrs[k]; else e.setAttribute(k, attrs[k]); } kids.flat().forEach((c) => e.append(c)); return e; };
async function api(path, body) { const r = await fetch("/api/diag/" + path, {method: body ? "POST" : "GET", headers: {"X-Token": T, "Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.statusText); return j; }
const CH = {ok: "c-ok", empty: "c-ok", timeout: "c-warn", blocked: "c-bad", unreachable: "c-bad", http: "c-bad", auth: "c-bad", parse: "c-bad", not_configured: "c-warn", skipped: "c-idle", pending: "c-idle", running: "c-idle", not_enabled: "c-idle", unsupported: "c-idle"};
const ICON = {ok: "✓ ", empty: "✓ ", timeout: "⏱ ", running: "", pending: "… ", skipped: "– "};
const WHAT = {NBA_STATS: "stats.nba.com: player and team statistics, game logs, minutes, advanced metrics, lineups, shot locations (the deep history)",
  NBA_LIVE: "cdn.nba.com: today's scoreboard, live box scores, starters and inactives", NBA_INJURY_REPORT: "official NBA injury report (PDF): injury status with publication times"};
let state = null, timer = null, runStart = 0;
function tickState() { const picked = [...document.querySelectorAll(".inc:checked")].map((c) => c.value); const ok = picked.length && picked.every((n) => $("#ack-" + n).checked);
  $("#run").disabled = !ok; $("#runhint").textContent = ok ? "Ready. This takes about a minute; a source that does not answer may take longer." : "Tick the confirmation for each source you want to test."; }
async function init() {
  state = await api("state"); const host = $("#srcs");
  state.sources.forEach((s) => { host.append(el("div", {class: "src"},
    el("label", {class: "ck"}, el("input", {type: "checkbox", class: "inc", value: s.name, checked: s.default ? "" : null, onchange: tickState}), el("span", null, el("h3", null, s.name), el("span", {class: "hint"}, WHAT[s.name] || s.kind))),
    el("div", {class: "terms"}, el("div", null, "Terms: ", el("a", {href: s.terms_url, target: "_blank", rel: "noopener"}, s.terms_url)), el("div", null, s.terms_summary || "No terms summary recorded.")),
    el("label", {class: "ck"}, el("input", {type: "checkbox", id: "ack-" + s.name, onchange: tickState}), el("span", null, "I have read the terms for " + s.name + " and accept responsibility for how I use this source.")))); });
  if (!state.environment.pypdf_available) $("#srcs").append(el("p", {class: "hint"}, "Note: PDF reading is not available in this build, so the injury report can only be checked for download, not parsed."));
  document.querySelectorAll(".inc").forEach((c) => { if (!c.checked) c.removeAttribute("checked"); }); tickState();
  if (state.progress.status !== "idle") watch();
  setInterval(() => api("ping").catch(() => {}), 5000);
}
function render(p) {
  $("#prog").hidden = false; const pct = p.total ? Math.round(100 * p.done / p.total) : 0; $("#bar").style.width = pct + "%"; $("#barw").setAttribute("aria-valuenow", pct);
  $("#ptext").textContent = p.status === "running" ? `Testing... ${p.done} of ${p.total} checks finished` : `Finished: ${p.done} of ${p.total} checks`;
  const host = $("#steps"); host.replaceChildren(); let cur = null;
  p.steps.forEach((s) => { if (s.source !== cur) { cur = s.source; const ps = p.per_source[cur] || {}; host.append(el("h3", {style: "margin:12px 0 4px"}, cur + "  ", el("span", {class: "hint"}, `${ps.ok || 0} of ${ps.total || 0} connected`))); }
    const running = s.state === "running";
    host.append(el("div", {class: "row"}, el("span", null, s.capability), el("span", {class: "chip " + (CH[s.state] || "c-bad")}, running ? el("span", {class: "spin"}) : "", (ICON[s.state] || (s.state === "pending" ? "" : "✗ ")), s.label), el("span", {class: "ms"}, s.elapsed_ms ? (s.elapsed_ms / 1000).toFixed(1) + " s" : ""),
      s.detail && s.state !== "pending" ? el("div", {class: "det"}, s.detail) : ""));
  });
}
function finish(p) {
  $("#done").hidden = false; const sum = $("#sum"); sum.replaceChildren();
  Object.entries(p.per_source).forEach(([n, v]) => { const good = v.ok === v.total; const none = v.ok === 0;
    sum.append(el("span", {class: "pill " + (good ? "c-ok" : none ? "c-bad" : "c-warn")}, `${n}: ${v.ok}/${v.total} connected` + (v.timeout ? `, ${v.timeout} timed out` : ""))); });
  const to = Object.values(p.per_source).some((v) => v.timeout);
  $("#explain").textContent = (to ? "A timeout means the server did not answer in time from this network. That is useful to know and is exactly what the report records. " : "") + "Click Export Report and send me the file. It contains results and small public-data samples only: no IP address, name or file paths.";
  if (p.error) $("#explain").textContent += " Internal error: " + p.error;
  if (p.last_export) $("#where").textContent = "Saved: " + p.last_export;
}
async function watch() { clearInterval(timer); const tick = async () => { const p = await api("progress"); render(p); if (p.status === "done") { clearInterval(timer); finish(p); $("#run").disabled = false; } }; await tick(); timer = setInterval(() => tick().catch(() => {}), 700); }
$("#run").onclick = async () => { const picked = [...document.querySelectorAll(".inc:checked")].map((c) => c.value); $("#run").disabled = true; $("#done").hidden = true; $("#where").textContent = "";
  try { await api("start", {sources: picked, acknowledged: picked.filter((n) => $("#ack-" + n).checked)}); $("#prog").scrollIntoView({behavior: "smooth"}); watch(); } catch (e) { $("#runhint").textContent = "Could not start: " + e.message; tickState(); } };
$("#exp").onclick = async () => { try { const r = await api("export", {}); $("#where").textContent = "Saved: " + r.path; $("#reveal").hidden = false; } catch (e) { $("#where").textContent = "Export failed: " + e.message; } };
$("#reveal").onclick = () => api("reveal", {}).catch((e) => { $("#where").textContent = e.message; });
$("#again").onclick = () => { $("#done").hidden = true; $("#prog").hidden = true; tickState(); window.scrollTo(0, 0); };
$("#quit").onclick = async () => { try { await api("quit", {}); } catch (e) {} document.body.innerHTML = "<main><p>Closed. You can close this tab.</p></main>"; };
init().catch((e) => { document.body.prepend(el("p", {style: "padding:16px;color:#a1261c"}, "Startup problem: " + e.message)); });
</script></body></html>'''
