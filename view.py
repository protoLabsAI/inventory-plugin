"""The console view — a single vanilla-JS page served on the PUBLIC ``/plugins/inventory``
prefix (an iframe navigation carries no bearer), pulling everything through the gated
``/api/plugins/inventory`` API via the design-system kit's authed fetch.

The four rules (docs/guides/plugin-views.md): serve the declared path; gate data by default;
derive the base from the page's own URL (fleet-proxy slug aware); link the DS kit and let
it own theme tokens + the init handshake.
"""

from __future__ import annotations

VIEW_PATH = "/view"

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inventory</title>
<style>
  html,body{margin:0;background:var(--pl-color-bg);color:var(--pl-color-fg);font-family:var(--pl-font-sans);font-size:13px}
  .wrap{display:flex;flex-direction:column;min-height:100vh}
  .bar{display:flex;flex-wrap:wrap;gap:var(--pl-space-2);align-items:center;padding:var(--pl-space-2) var(--pl-space-4);border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .bar .grow{flex:1}
  .bar .pl-input,.bar .pl-select{width:auto;min-width:140px;padding:6px 9px;font-size:13px}
  .stats{padding:0 var(--pl-space-4)}
  .stats .pl-stats{padding:var(--pl-space-3) 0;border-top:none}
  .tabs{padding:0 var(--pl-space-4)}
  .body{flex:1;overflow:auto;padding:0 var(--pl-space-4) var(--pl-space-6)}
  .pl-table td,.pl-table th{white-space:nowrap;vertical-align:middle}
  .pl-table td.wrap-cell{white-space:normal;max-width:34ch}
  .num{text-align:right;font-family:var(--pl-font-mono)}
  .muted{color:var(--pl-color-fg-muted)}
  .sub{display:block;font-size:11px;color:var(--pl-color-fg-muted)}
  .edit{cursor:text}
  .edit:hover{background:var(--pl-color-bg-hover);border-radius:4px}
  .price{cursor:pointer}
  .price:hover{text-decoration:underline}
  .actions{display:flex;gap:4px}
  .pl-select.status{padding:2px 6px;font-size:12px;width:auto}
  .form{display:grid;grid-template-columns:1fr 1fr;gap:var(--pl-space-3)}
  .form .span2{grid-column:1 / -1}
  .pl-field__input,.pl-textarea{font-size:13px}
  textarea.pl-field__input{min-height:120px;font-family:var(--pl-font-mono);font-size:12px}
  .result{margin-top:var(--pl-space-3);font-family:var(--pl-font-mono);font-size:12px;white-space:pre-wrap}
  .basis{max-width:28ch;overflow:hidden;text-overflow:ellipsis}
  .pl-toast-stack{bottom:var(--pl-space-4);right:var(--pl-space-4)}
  .kicker{font-family:var(--pl-font-mono);font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--pl-color-fg-muted)}
  @media (max-width:640px){.form{grid-template-columns:1fr}.bar .pl-input,.bar .pl-select{min-width:100px}}
</style>
<script>
  // RULE 3 — slug-aware base: "" on the host window, "/agents/<slug>" through the fleet proxy.
  var BASE = location.pathname.split("/plugins/")[0];
  // RULE 4 — the DS kit's CSS off BASE (it re-themes live with the operator's theme).
  (function(){ var l=document.createElement("link"); l.rel="stylesheet"; l.href=BASE+"/_ds/plugin-kit.css"; document.head.appendChild(l); })();
</script>
</head><body>
<div class="wrap">
  <div class="pl-panel-header pl-panel-header--compact">
    <div><div class="pl-panel-header__kicker">Source of truth</div><h1 class="pl-panel-header__title">Inventory</h1></div>
    <div class="pl-panel-header__actions">
      <button class="pl-btn pl-btn--sm" id="btn-import">Import CSV</button>
      <button class="pl-btn pl-btn--sm" id="btn-export">Export CSV</button>
      <button class="pl-btn pl-btn--sm" id="btn-lot">+ Lot</button>
      <button class="pl-btn pl-btn--sm pl-btn--primary" id="btn-item">+ Item</button>
    </div>
  </div>
  <div class="bar">
    <select class="pl-select" id="f-lot"><option value="">All lots</option></select>
    <select class="pl-select" id="f-status">
      <option value="">Any status</option>
      <option value="planned,available,listed,pending">Unsold</option>
      <option value="planned">Planned</option><option value="available">Available</option>
      <option value="listed">Listed</option><option value="pending">Pending</option>
      <option value="sold">Sold</option><option value="kept">Kept</option><option value="withdrawn">Withdrawn</option>
    </select>
    <input class="pl-input grow" id="f-q" placeholder="Search name, notes, id…">
    <span class="muted" id="count"></span>
  </div>
  <div class="stats"><div class="pl-stats" id="stats" style="--pl-stats-cols:5"></div></div>
  <div class="tabs"><div class="pl-tabs" id="tabs">
    <button class="pl-tab pl-tab--active" data-tab="items">Items</button>
    <button class="pl-tab" data-tab="lots">Lots</button>
    <button class="pl-tab" data-tab="sales">Sales</button>
    <button class="pl-tab" data-tab="activity">Activity</button>
  </div></div>
  <div class="body">
    <div id="err" class="pl-callout pl-callout--error" hidden></div>
    <div id="out"></div>
  </div>
</div>
<div id="dialog-root"></div>
<div class="pl-toast-stack" id="toasts" data-position="bottom-right"></div>
<script type="module">
  // RULE 4 — plugin-kit.js is an ES module → dynamic import; a tokenless shim keeps the page alive on an older host.
  let kit;
  try { kit = await import(BASE + "/_ds/plugin-kit.js"); }
  catch (e) { kit = { initPluginView(){}, apiFetch: (p, i) => fetch(BASE + p, i) }; }

  const API = "/api/plugins/inventory";
  const STATUSES = ["planned","available","listed","pending","sold","kept","withdrawn"];
  const state = { tab: "items", lots: [], items: [], summary: null, sales: [], audit: [], filters: { lot: "", status: "", q: "" } };
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = (v) => (v === null || v === undefined || v === "" ? "—" : (Number(v) < 0 ? "-" : "") + "$" + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const signed = (v) => (v === null || v === undefined || v === "" ? "—" : (Number(v) >= 0 ? "+" : "") + fmt(v));
  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

  // RULES 2+3 — every data call goes through the kit's slug-aware, bearer-carrying fetch with a bare path.
  async function api(path, method = "GET", body) {
    const init = { method, headers: {} };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    const r = await kit.apiFetch(API + path, init);
    const text = await r.text();
    let data = null; try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    if (!r.ok) throw new Error((data && (data.detail || data.error)) || (r.status + " " + r.statusText));
    return data;
  }

  function toast(msg, kind = "info") {
    const el = document.createElement("div");
    el.className = "pl-toast pl-toast--" + kind;
    el.innerHTML = '<div class="pl-toast__body"><div class="pl-toast__msg">' + esc(msg) + "</div></div>";
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
  }
  function showErr(e) { const el = $("#err"); el.hidden = false; el.textContent = String(e && e.message || e); }
  function clearErr() { $("#err").hidden = true; }

  // ── dialogs ──────────────────────────────────────────────────────────────────
  function field(name, label, value = "", opts = {}) {
    const id = "f_" + name;
    let input;
    if (opts.type === "select") {
      input = '<select class="pl-field__input" id="' + id + '" name="' + name + '">' +
        opts.options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return '<option value="' + esc(v) + '"' + (String(v) === String(value) ? " selected" : "") + ">" + esc(l) + "</option>"; }).join("") + "</select>";
    } else if (opts.type === "textarea") {
      input = '<textarea class="pl-field__input" id="' + id + '" name="' + name + '" placeholder="' + esc(opts.placeholder || "") + '">' + esc(value) + "</textarea>";
    } else {
      input = '<input class="pl-field__input" id="' + id + '" name="' + name + '" type="' + (opts.type || "text") + '" value="' + esc(value) + '"' +
        (opts.step ? ' step="' + opts.step + '"' : "") + (opts.placeholder ? ' placeholder="' + esc(opts.placeholder) + '"' : "") + (opts.required ? " required" : "") + (opts.readonly ? " readonly" : "") + ">";
    }
    return '<label class="pl-field' + (opts.span2 ? " span2" : "") + '"><span class="pl-field__label">' + esc(label) + "</span>" + input +
      (opts.hint ? '<span class="pl-field__hint">' + esc(opts.hint) + "</span>" : "") + "</label>";
  }
  function dialog({ title, body, submit = "Save", danger = false, onSubmit }) {
    return new Promise((resolve) => {
      const root = $("#dialog-root");
      root.innerHTML = '<div class="pl-overlay"><div class="pl-dialog" role="dialog" aria-modal="true" tabindex="-1">' +
        '<div class="pl-dialog__head"><div class="pl-dialog__title">' + esc(title) + '</div><button class="pl-btn pl-btn--ghost pl-btn--icon pl-dialog__close" data-x aria-label="Close">×</button></div>' +
        '<form class="pl-dialog__body" id="dlg-form"><div class="form">' + body + '</div><div class="result" id="dlg-result"></div></form>' +
        '<div class="pl-dialog__foot"><button class="pl-btn" data-x type="button">Cancel</button>' +
        (onSubmit ? '<button class="pl-btn ' + (danger ? "pl-btn--danger" : "pl-btn--primary") + '" id="dlg-ok" type="submit" form="dlg-form">' + esc(submit) + "</button>" : "") + "</div></div></div>";
      const onKey = (e) => { if (e.key === "Escape") close(false); };
      const close = (v) => { document.removeEventListener("keydown", onKey); root.innerHTML = ""; resolve(v); };
      document.addEventListener("keydown", onKey);
      root.querySelectorAll("[data-x]").forEach((b) => b.addEventListener("click", () => close(false)));
      root.querySelector(".pl-overlay").addEventListener("click", (e) => { if (e.target === e.currentTarget) close(false); });
      const form = $("#dlg-form");
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const values = Object.fromEntries(new FormData(form).entries());
        const ok = $("#dlg-ok"); if (ok) { ok.disabled = true; ok.classList.add("pl-btn--loading"); }
        try { const r = await onSubmit(values, form); if (r !== false) close(true); }
        catch (err) { toast(err.message || String(err), "error"); }
        finally { if (ok) { ok.disabled = false; ok.classList.remove("pl-btn--loading"); } }
      });
      const first = form.querySelector("input:not([readonly]),select,textarea"); (first || root.querySelector(".pl-dialog")).focus();
    });
  }
  const confirm = (title, text, submit = "Confirm") => dialog({ title, body: '<div class="span2">' + esc(text) + "</div>", submit, danger: true, onSubmit: async () => true });

  // ── loads ────────────────────────────────────────────────────────────────────
  async function loadCore() {
    const f = state.filters;
    const qs = new URLSearchParams(); if (f.lot) qs.set("lot_id", f.lot); if (f.status) qs.set("status", f.status); if (f.q) qs.set("q", f.q);
    const [lots, summary, items] = await Promise.all([api("/lots"), api("/summary"), api("/items?" + qs.toString())]);
    state.lots = lots.lots; state.summary = summary; state.items = items.items;
  }
  let inflight = 0;
  async function refresh() {
    const mine = ++inflight;  // a slower earlier load must not paint over a newer one
    clearErr();
    try {
      await loadCore();
      if (state.tab === "sales") state.sales = (await api("/sales" + (state.filters.lot ? "?lot_id=" + encodeURIComponent(state.filters.lot) : ""))).sales;
      if (state.tab === "activity") state.audit = (await api("/audit?limit=200")).audit;
      if (mine !== inflight) return;
      render();
    } catch (e) { if (mine === inflight) { showErr(e); render(); } }
  }

  // ── render ───────────────────────────────────────────────────────────────────
  function render() {
    if (state.filters.lot && !state.lots.some((l) => l.id === state.filters.lot)) state.filters.lot = "";  // the lot is gone
    const sel = $("#f-lot"); const cur = state.filters.lot;
    sel.innerHTML = '<option value="">All lots</option>' + state.lots.map((l) => '<option value="' + esc(l.id) + '"' + (l.id === cur ? " selected" : "") + ">" + esc(l.name || l.id) + "</option>").join("");
    renderStats();
    document.querySelectorAll("#tabs .pl-tab").forEach((b) => b.classList.toggle("pl-tab--active", b.dataset.tab === state.tab));
    const out = $("#out");
    if (state.tab === "items") out.innerHTML = renderItems();
    else if (state.tab === "lots") out.innerHTML = renderLots();
    else if (state.tab === "sales") out.innerHTML = renderSales();
    else out.innerHTML = renderAudit();
    $("#count").textContent = state.tab === "items" ? state.items.length + " item" + (state.items.length === 1 ? "" : "s") : "";
    wireTable();
  }
  function renderStats() {
    const s = state.summary; if (!s) return;
    const lot = state.filters.lot ? s.lots.find((l) => l.id === state.filters.lot) : null;
    const t = lot ? { acquisition_cost: lot.acquisition_cost, remaining: lot.remaining, realized_net: lot.realized.net, projected_net_at_target: lot.projected_net_at_target, items: lot.counts.total, unpriced_items: lot.remaining.unpriced_items } : s.totals;
    const tile = (n, l, sub = "") => '<div class="pl-stat"><div class="pl-stat__num">' + n + '</div><div class="pl-stat__label">' + esc(l) + (sub ? '<span class="sub">' + esc(sub) + "</span>" : "") + "</div></div>";
    $("#stats").innerHTML =
      tile(fmt(t.acquisition_cost), "Lot cost", lot ? lot.acquired_on : (s.lots.length + " lots")) +
      tile(fmt(t.remaining.target), "Remaining at target", fmt(t.remaining.low) + " – " + fmt(t.remaining.high)) +
      tile(fmt(t.realized_net), "Realized net", lot ? "gross " + fmt(lot.realized.gross) : "gross " + fmt(s.totals.realized_gross)) +
      tile(signed(t.projected_net_at_target), "Projected net at target", "if the rest sells at target") +
      tile(String(t.items), "Items", t.unpriced_items ? t.unpriced_items + " unpriced" : "all priced");
  }
  function renderItems() {
    if (!state.items.length) return '<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">No items</div><div class="pl-empty__desc">Add one, or import a CSV — headers like inventory_id, item, lot_id, target_price_usd, status are recognised.</div></div>';
    const rows = state.items.map((it) => {
      const ed = (f, v, cls = "") => '<span class="edit ' + cls + '" data-edit="' + f + '" data-id="' + esc(it.id) + '" title="Double-click to edit">' + (v === "" || v === null ? '<span class="muted">—</span>' : esc(v)) + "</span>";
      const pr = (f, v) => '<td class="num price" data-price="' + esc(it.id) + '" title="Click to set the price band">' + fmt(v) + "</td>";
      return "<tr>" +
        '<td class="muted">' + esc(it.id) + "</td>" +
        '<td class="wrap-cell">' + ed("name", it.name) + '<span class="sub">' + ed("category", it.category) + " · " + ed("condition", it.condition) + "</span></td>" +
        '<td class="num">' + ed("quantity", it.quantity) + "</td>" +
        '<td><select class="pl-select status" data-status="' + esc(it.id) + '">' + STATUSES.map((s) => '<option' + (s === it.status ? " selected" : "") + ">" + s + "</option>").join("") + "</select></td>" +
        pr("target_low", it.target_low) + pr("target", it.target) + pr("target_high", it.target_high) +
        '<td class="basis" title="' + esc(it.price_basis) + '">' + (it.price_basis ? esc(it.price_basis) : '<span class="muted">no basis</span>') + '<span class="sub">' + esc(it.price_updated_on || "") + "</span></td>" +
        '<td class="actions">' +
          '<button class="pl-btn pl-btn--xs" data-act="price" data-id="' + esc(it.id) + '">Price</button>' +
          '<button class="pl-btn pl-btn--xs" data-act="sold" data-id="' + esc(it.id) + '"' + (it.status === "sold" ? " disabled" : "") + ">Sold</button>" +
          '<button class="pl-btn pl-btn--xs" data-act="list" data-id="' + esc(it.id) + '">List</button>' +
          '<button class="pl-btn pl-btn--xs pl-btn--ghost" data-act="edit" data-id="' + esc(it.id) + '">Edit</button>' +
          '<button class="pl-btn pl-btn--xs pl-btn--ghost" data-act="del" data-id="' + esc(it.id) + '" aria-label="Delete">✕</button>' +
        "</td></tr>";
    }).join("");
    return '<table class="pl-table"><thead><tr><th>ID</th><th>Item</th><th class="num">Qty</th><th>Status</th><th class="num">Low</th><th class="num">Target</th><th class="num">High</th><th>Basis</th><th></th></tr></thead><tbody>' + rows + "</tbody></table>";
  }
  function renderLots() {
    const s = state.summary; if (!s || !s.lots.length) return '<div class="pl-empty">No lots yet.</div>';
    const rows = s.lots.map((l) => "<tr>" +
      '<td class="muted">' + esc(l.id) + "</td><td>" + esc(l.name) + "</td><td>" + esc(l.acquired_on) + "</td>" +
      '<td class="num">' + fmt(l.acquisition_cost) + '</td><td class="num">' + l.counts.total + ' <span class="muted">(' + l.counts.sold + " sold)</span></td>" +
      '<td class="num">' + fmt(l.remaining.target) + '<span class="sub">' + fmt(l.remaining.low) + " – " + fmt(l.remaining.high) + "</span></td>" +
      '<td class="num">' + fmt(l.realized.net) + '</td><td class="num">' + signed(l.projected_net_at_target) + "</td>" +
      '<td class="actions"><button class="pl-btn pl-btn--xs pl-btn--ghost" data-act="editlot" data-id="' + esc(l.id) + '">Edit</button><button class="pl-btn pl-btn--xs pl-btn--ghost" data-act="dellot" data-id="' + esc(l.id) + '" aria-label="Delete">✕</button></td></tr>').join("");
    return '<table class="pl-table"><thead><tr><th>ID</th><th>Lot</th><th>Acquired</th><th class="num">Cost</th><th class="num">Items</th><th class="num">Remaining @ target</th><th class="num">Realized net</th><th class="num">Projected net</th><th></th></tr></thead><tbody>' + rows + "</tbody></table>";
  }
  function renderSales() {
    if (!state.sales.length) return '<div class="pl-empty">No sales recorded yet.</div>';
    const names = Object.fromEntries(state.items.map((i) => [i.id, i.name]));
    const rows = state.sales.map((s) => "<tr><td>" + esc(s.sold_on) + '</td><td class="wrap-cell">' + esc(names[s.item_id] || s.item_id) + '<span class="sub">' + esc(s.item_id) + "</span></td><td>" + esc(s.channel) + "</td>" +
      '<td class="num">' + fmt(s.price) + '</td><td class="num">' + fmt(s.shipping_charged) + '</td><td class="num">' + fmt(s.fees) + '</td><td class="num">' + fmt(s.shipping_cost) + '</td><td class="num"><b>' + fmt(s.net) + "</b></td></tr>").join("");
    return '<table class="pl-table"><thead><tr><th>Date</th><th>Item</th><th>Channel</th><th class="num">Price</th><th class="num">Ship in</th><th class="num">Fees</th><th class="num">Ship out</th><th class="num">Net</th></tr></thead><tbody>' + rows + "</tbody></table>";
  }
  function renderAudit() {
    if (!state.audit.length) return '<div class="pl-empty">No activity yet.</div>';
    const rows = state.audit.map((a) => "<tr><td class=\"muted\">" + esc(a.ts) + "</td><td>" + esc(a.entity) + "</td><td>" + esc(a.entity_id) + "</td><td>" + esc(a.action) + "</td><td>" + esc(a.actor) + '</td><td class="wrap-cell muted">' + esc(JSON.stringify(a.changes)) + "</td></tr>").join("");
    return '<table class="pl-table"><thead><tr><th>When</th><th>Entity</th><th>ID</th><th>Action</th><th>By</th><th>Changes</th></tr></thead><tbody>' + rows + "</tbody></table>";
  }

  // ── actions ──────────────────────────────────────────────────────────────────
  const itemById = (id) => state.items.find((i) => i.id === id);
  const lotOptions = (current = "") => {
    const opts = [["", "— none —"]].concat(state.lots.map((l) => [l.id, l.name || l.id]));
    if (current && !state.lots.some((l) => l.id === current)) opts.push([current, "(unknown lot " + current + ")"]);
    return opts;
  };

  async function itemDialog(item) {
    const it = item || { lot_id: state.filters.lot, status: "available", quantity: 1 };
    await dialog({
      title: item ? "Edit " + item.id : "New item", submit: item ? "Save" : "Add",
      body: field("name", "Name", it.name, { required: true, span2: true }) + field("lot_id", "Lot", it.lot_id, { type: "select", options: lotOptions(it.lot_id) }) +
        field("category", "Category", it.category) + field("condition", "Condition", it.condition, { placeholder: "new on sprue, sealed, painted…" }) +
        field("status", "Status", it.status, { type: "select", options: STATUSES.filter((s) => s !== "sold" || it.status === "sold") }) +
        field("quantity", "Quantity", it.quantity ?? 1, { type: "number", step: "1" }) + field("model_count", "Model count", it.model_count ?? "", { type: "number", step: "1" }) +
        field("retail", "Retail ($)", it.retail ?? "", { type: "number", step: "0.01", hint: "The anchor price, not a target." }) + field("cost_basis", "Cost basis ($)", it.cost_basis ?? "", { type: "number", step: "0.01" }) +
        field("notes", "Notes", it.notes, { type: "textarea", span2: true }),
      onSubmit: async (v) => {
        if (!v.name.trim()) throw new Error("A name is required");
        const body = { name: v.name, lot_id: v.lot_id, category: v.category, condition: v.condition, quantity: num(v.quantity), model_count: num(v.model_count), retail: num(v.retail), cost_basis: num(v.cost_basis), notes: v.notes };
        if (!item || v.status !== item.status) body.status = v.status;  // an unchanged "sold" would be refused by the API
        if (item) await api("/items/" + encodeURIComponent(item.id), "PUT", body); else await api("/items", "POST", body);
        toast(item ? "Saved" : "Item added", "success"); await refresh();
      },
    });
  }
  async function lotDialog(lot) {
    const l = lot || {};
    await dialog({
      title: lot ? "Edit lot " + lot.id : "New lot", submit: lot ? "Save" : "Add",
      body: field("id", "Lot ID", l.id || "", { required: true, readonly: !!lot, placeholder: "BLOODBOWL-2026-09", hint: lot ? "" : "Short, stable, unique." }) + field("name", "Name", l.name || "", { required: true }) +
        field("acquisition_cost", "Acquisition cost ($)", l.acquisition_cost ?? "", { type: "number", step: "0.01" }) + field("acquired_on", "Acquired on", l.acquired_on || "", { placeholder: "2026-09" }) +
        field("source", "Source", l.source || "", { placeholder: "eBay lot, LGS, estate…" }) + field("notes", "Notes", l.notes || "", { type: "textarea", span2: true }),
      onSubmit: async (v) => { await api("/lots/" + encodeURIComponent(v.id), "PUT", { name: v.name, acquisition_cost: num(v.acquisition_cost), acquired_on: v.acquired_on, source: v.source, notes: v.notes, description: l.description || "" }); toast("Lot saved", "success"); await refresh(); },
    });
  }
  async function priceDialog(id) {
    const it = itemById(id); if (!it) return;
    await dialog({
      title: "Price · " + it.name, submit: "Set price",
      body: field("low", "Low ($)", it.target_low ?? "", { type: "number", step: "0.01" }) + field("target", "Target ($)", it.target ?? "", { type: "number", step: "0.01" }) + field("high", "High ($)", it.target_high ?? "", { type: "number", step: "0.01" }) +
        field("observed_on", "As of", new Date().toISOString().slice(0, 10)) +
        field("basis", "Basis (required)", it.price_basis || "", { required: true, span2: true, placeholder: "eBay sold comps (22 sold, incl. shipping) · retail anchor, no sold comps", hint: "What the numbers rest on. A bare number is not a target." }) +
        '<div class="span2 kicker">Evidence (optional — recorded as a price observation)</div>' +
        field("source", "Source", "", { type: "select", options: [["", "—"], "ebay_sold", "ebay_active", "amazon", "retail", "manual", "other"] }) + field("n", "Comps (n)", "", { type: "number", step: "1" }) +
        field("p25", "p25 ($)", "", { type: "number", step: "0.01" }) + field("median", "Median ($)", "", { type: "number", step: "0.01" }) + field("p75", "p75 ($)", "", { type: "number", step: "0.01" }) + field("query", "Query used", ""),
      onSubmit: async (v) => {
        const body = { low: num(v.low), target: num(v.target), high: num(v.high), basis: v.basis, observed_on: v.observed_on };
        if (v.source) body.observation = { source: v.source, n: Number(v.n || 0), p25: num(v.p25), median: num(v.median), p75: num(v.p75), query: v.query, basis: v.basis };
        await api("/items/" + encodeURIComponent(id) + "/price", "POST", body); toast("Price set", "success"); await refresh();
      },
    });
  }
  async function soldDialog(id) {
    const it = itemById(id); if (!it) return;
    await dialog({
      title: "Mark sold · " + it.name, submit: "Record sale",
      body: field("price", "Sold for ($)", it.target ?? "", { type: "number", step: "0.01", required: true }) + field("channel", "Channel", "eBay", { required: true, placeholder: "eBay, Facebook, local, r/miniswap" }) +
        field("sold_on", "Sold on", new Date().toISOString().slice(0, 10)) + field("quantity", "Quantity", 1, { type: "number", step: "1" }) +
        field("shipping_charged", "Shipping charged ($)", "", { type: "number", step: "0.01" }) + field("fees", "Fees ($)", "", { type: "number", step: "0.01", hint: "Marketplace + payment fees." }) +
        field("shipping_cost", "Shipping cost ($)", "", { type: "number", step: "0.01" }) + field("notes", "Notes", "") +
        '<div class="span2 muted">net = price + shipping charged − fees − shipping cost. Live listings are closed.</div>',
      onSubmit: async (v) => {
        const r = await api("/items/" + encodeURIComponent(id) + "/sold", "POST", { price: num(v.price), channel: v.channel, sold_on: v.sold_on, quantity: Number(v.quantity || 1), shipping_charged: num(v.shipping_charged) || 0, fees: num(v.fees) || 0, shipping_cost: num(v.shipping_cost) || 0, notes: v.notes });
        toast("Sold — net " + fmt(r.sale.net), "success"); await refresh();
      },
    });
  }
  async function listingDialog(id) {
    const it = (await api("/items/" + encodeURIComponent(id))).item; if (!it) return;
    const live = (it.listings || []).filter((l) => l.state === "active");
    await dialog({
      title: "Listing · " + it.name, submit: "Add listing",
      body: field("channel", "Channel", "eBay", { required: true }) + field("price", "Listed price ($)", it.target ?? "", { type: "number", step: "0.01" }) +
        field("url", "URL", "", { span2: true, placeholder: "https://www.ebay.com/itm/…" }) + field("notes", "Notes", "", { span2: true }) +
        (live.length ? '<div class="span2 muted">Live: ' + live.map((l) => esc(l.channel) + " " + fmt(l.price)).join(", ") + "</div>" : ""),
      onSubmit: async (v) => { await api("/items/" + encodeURIComponent(id) + "/listings", "POST", { channel: v.channel, url: v.url, price: num(v.price), notes: v.notes }); toast("Listing added", "success"); await refresh(); },
    });
  }
  async function importDialog() {
    await dialog({
      title: "Import CSV", submit: "Import",
      body: '<label class="pl-field span2"><span class="pl-field__label">CSV file</span><input class="pl-field__input" type="file" id="csv-file" accept=".csv,text/csv"></label>' +
        field("csv", "…or paste CSV", "", { type: "textarea", span2: true, placeholder: "inventory_id,lot_id,item,target_price_usd,status\n…" }) +
        field("kind", "Kind", "auto", { type: "select", options: [["auto", "auto-detect"], ["items", "items"], ["lots", "lots"]] }) + field("default_lot", "Default lot", state.filters.lot, { type: "select", options: lotOptions(), hint: "Used when a row has no lot_id." }),
      onSubmit: async (v, form) => {
        let text = v.csv; const f = $("#csv-file", form); if (f && f.files && f.files[0]) text = await f.files[0].text();
        if (!text.trim()) throw new Error("Paste CSV or choose a file");
        const r = await api("/import", "POST", { csv: text, kind: v.kind, default_lot: v.default_lot });
        const res = $("#dlg-result", form);
        res.textContent = "kind: " + r.kind + " · rows: " + r.rows + " · created: " + r.created + " · updated: " + r.updated + " · sales: " + r.sales_recorded +
          (r.ignored_columns.length ? "\nignored columns: " + r.ignored_columns.join(", ") : "") + (r.warnings.length ? "\n\n" + r.warnings.join("\n") : "");
        toast("Imported " + (r.created + r.updated) + " rows", r.warnings.length ? "warning" : "success"); await refresh();
        return false; // keep the dialog open so the report is readable
      },
    });
  }
  async function exportCsv() {
    try {
      const qs = new URLSearchParams({ kind: state.tab === "lots" ? "lots" : "items" }); if (state.filters.lot) qs.set("lot_id", state.filters.lot); if (state.filters.status && state.tab !== "lots") qs.set("status", state.filters.status);
      const r = await kit.apiFetch(API + "/export?" + qs.toString()); const text = await r.text(); if (!r.ok) throw new Error(text);
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = "inventory-" + (qs.get("kind")) + "-" + new Date().toISOString().slice(0, 10) + ".csv"; document.body.appendChild(a); a.click(); a.remove();
      toast("Exported", "success");
    } catch (e) { toast(e.message || String(e), "error"); }
  }
  async function inlineEdit(span) {
    const id = span.dataset.id, f = span.dataset.edit, it = itemById(id); if (!it) return;
    const input = document.createElement("input"); input.className = "pl-editable__input"; input.value = it[f] ?? ""; if (f === "quantity") { input.type = "number"; input.step = "1"; input.style.width = "5ch"; }
    span.replaceWith(input); input.focus(); input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      if (save && f === "name" && !input.value.trim()) { toast("A name is required", "error"); save = false; }
      if (save && String(input.value) !== String(it[f] ?? "")) {
        try { await api("/items/" + encodeURIComponent(id), "PUT", { [f]: f === "quantity" ? num(input.value) : input.value }); toast("Saved", "success"); } catch (e) { toast(e.message, "error"); }
      }
      await refresh();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
    input.addEventListener("blur", () => finish(true));
  }
  function wireTable() {
    const out = $("#out");
    out.querySelectorAll("[data-edit]").forEach((s) => s.addEventListener("dblclick", () => inlineEdit(s)));
    out.querySelectorAll("[data-price]").forEach((td) => td.addEventListener("click", () => priceDialog(td.dataset.price)));
    out.querySelectorAll("[data-status]").forEach((sel) => sel.addEventListener("change", async () => {
      const id = sel.dataset.status, it = itemById(id);
      if (sel.value === "sold") { sel.value = it.status; return soldDialog(id); }
      if (it.status === "sold" && !(await confirm("Un-sell " + id + "?", "This item has a recorded sale, which stays on the books. Only do this if the sale fell through — and consider force-recording a refund instead."))) { sel.value = it.status; return; }
      try { await api("/items/" + encodeURIComponent(id), "PUT", { status: sel.value }); toast("Status → " + sel.value, "success"); } catch (e) { toast(e.message, "error"); }
      await refresh();
    }));
    out.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.id, act = b.dataset.act;
      if (act === "price") return priceDialog(id);
      if (act === "sold") return soldDialog(id);
      if (act === "list") return listingDialog(id);
      if (act === "edit") { const full = (await api("/items/" + encodeURIComponent(id))).item; return itemDialog(full); }
      if (act === "del") { const it = itemById(id); if (await confirm("Delete " + id + "?", "\"" + (it && it.name) + "\" and its listings, sales and price history are removed permanently. Prefer status = withdrawn or kept to keep the record.", "Delete")) { try { await api("/items/" + encodeURIComponent(id), "DELETE"); toast("Deleted", "success"); } catch (e) { toast(e.message, "error"); } await refresh(); } }
      if (act === "editlot") { const lot = state.lots.find((l) => l.id === id); return lotDialog(lot); }
      if (act === "dellot") { if (await confirm("Delete lot " + id + "?", "Only an empty lot can be deleted. Items keep their history.", "Delete")) { try { await api("/lots/" + encodeURIComponent(id), "DELETE"); if (state.filters.lot === id) state.filters.lot = ""; toast("Lot deleted", "success"); } catch (e) { toast(e.message, "error"); } await refresh(); } }
    }));
  }

  // ── wiring ───────────────────────────────────────────────────────────────────
  $("#btn-item").addEventListener("click", () => itemDialog(null));
  $("#btn-lot").addEventListener("click", () => lotDialog(null));
  $("#btn-import").addEventListener("click", importDialog);
  $("#btn-export").addEventListener("click", exportCsv);
  $("#f-lot").addEventListener("change", (e) => { state.filters.lot = e.target.value; refresh(); });
  $("#f-status").addEventListener("change", (e) => { state.filters.status = e.target.value; refresh(); });
  let qt; $("#f-q").addEventListener("input", (e) => { clearTimeout(qt); qt = setTimeout(() => { state.filters.q = e.target.value.trim(); refresh(); }, 250); });
  $("#tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (!b) return; state.tab = b.dataset.tab; refresh(); });

  // Boot once, on whichever fires first: the kit's protoagent:init handshake (bearer + theme) or a short timer.
  let booted = false;
  function boot() { if (booted) return; booted = true; refresh(); }
  kit.initPluginView(boot);
  setTimeout(boot, 800);
</script>
</body></html>
"""


def build_view_router(cfg: dict):
    """The PUBLIC page router — mounted at ``/plugins/inventory``; serves only the page."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    r = APIRouter()

    @r.get(VIEW_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def _view():
        return HTMLResponse(PAGE)

    return r
