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
  html,body{margin:0;background:var(--pl-color-bg);color:var(--pl-color-fg);font-family:var(--pl-font-sans);font-size:var(--pl-font-size-sm)}
  .view{display:flex;flex-direction:column;height:100vh;height:100dvh}
  .view-tabs{padding:0 var(--pl-space-4)}
  .main{flex:1;overflow:auto;padding:var(--pl-space-3) var(--pl-space-4) var(--pl-space-8)}
  .sec{margin-bottom:var(--pl-space-6)}
  .sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:var(--pl-space-2);margin-bottom:var(--pl-space-1_5)}
  .sec-title{margin:0;font-size:var(--pl-font-size-base);font-weight:var(--pl-font-weight-medium)}
  .list{list-style:none;margin:0;padding:0;border-top:var(--pl-border-width) solid var(--pl-color-border)}
  .list li{display:flex;align-items:center;border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .irow{display:flex;align-items:center;gap:var(--pl-space-2_5);flex:1;min-width:0;min-height:var(--pl-space-12);padding:var(--pl-space-1_5) var(--pl-space-1);background:none;border:0;color:inherit;font:inherit;text-align:left;cursor:pointer}
  button.irow:hover{background:var(--pl-color-bg-hover)}
  .irow:focus-visible,.trow:focus-visible{outline:var(--pl-space-0_5) solid var(--pl-color-focus);outline-offset:calc(-1 * var(--pl-space-0_5))}
  .static{cursor:default}
  .irow-main{flex:1;min-width:0}
  .irow-name{display:block}
  .irow-sub{display:block;font-size:var(--pl-font-size-xs);color:var(--pl-color-fg-muted)}
  .money{font-family:var(--pl-font-mono);white-space:nowrap;text-align:right}
  .neg{color:var(--pl-color-status-error)}
  .muted{color:var(--pl-color-fg-muted)}
  .sub{display:block;font-size:var(--pl-font-size-2xs);color:var(--pl-color-fg-muted)}
  .hit{display:flex;align-items:center;align-self:stretch;padding:0 var(--pl-space-2_5) 0 var(--pl-space-1);cursor:pointer}
  .toolbar{display:flex;flex-wrap:wrap;gap:var(--pl-space-2);align-items:center;margin-bottom:var(--pl-space-2)}
  .grow{flex:1 1 10rem;min-width:0}
  .chips{display:flex;flex-wrap:wrap;gap:var(--pl-space-1_5);margin-bottom:var(--pl-space-2)}
  .count{display:flex;align-items:center;gap:var(--pl-space-2);margin-bottom:var(--pl-space-1);color:var(--pl-color-fg-muted);font-size:var(--pl-font-size-xs)}
  .group{margin-top:var(--pl-space-4)}
  .group:first-child{margin-top:0}
  .group-label{margin-bottom:var(--pl-space-1)}
  .selbar{position:sticky;bottom:calc(-1 * var(--pl-space-8));display:flex;flex-wrap:wrap;align-items:center;gap:var(--pl-space-2);padding:var(--pl-space-2) 0;background:var(--pl-color-bg);border-top:var(--pl-border-width) solid var(--pl-color-border-strong)}
  .selbar-count{flex:1}
  .anchor{position:relative}
  .pop{position:absolute;right:0;top:100%;z-index:20;margin-top:var(--pl-space-1)}
  .pop-form{display:grid;gap:var(--pl-space-2)}
  .col-num{width:1%}
  .sr-only{position:absolute;width:var(--pl-border-width);height:var(--pl-border-width);overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
  .dsec{padding:var(--pl-space-3) 0;border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .dsec:last-child{border-bottom:0}
  .dh{margin:0 0 var(--pl-space-2);font-size:var(--pl-font-size-sm);font-weight:var(--pl-font-weight-medium)}
  .drow{display:flex;flex-wrap:wrap;gap:var(--pl-space-1_5);align-items:center;margin-bottom:var(--pl-space-2)}
  .kv{display:grid;grid-template-columns:6rem 1fr;gap:var(--pl-space-2);align-items:center;min-height:var(--pl-space-8)}
  .mono{font-family:var(--pl-font-mono);font-size:var(--pl-font-size-xs);overflow-wrap:anywhere}
  .bigprice{font-size:var(--pl-font-size-xl);text-align:left}
  .basis{margin:var(--pl-space-2) 0;line-height:var(--pl-font-line-height-body);overflow-wrap:anywhere}
  .notes{white-space:pre-wrap;overflow-wrap:anywhere}
  .hist{list-style:none;margin:0;padding:0}
  .hist li{padding:var(--pl-space-1_5) 0;border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .foot{display:flex;flex-wrap:wrap;gap:var(--pl-space-2);width:100%}
  .sheet{max-height:92%}
  .form{display:grid;grid-template-columns:1fr 1fr;gap:var(--pl-space-3)}
  .form .span2{grid-column:1 / -1}
  .form textarea{min-height:calc(var(--pl-space-12) * 2.5);font-family:var(--pl-font-mono);font-size:var(--pl-font-size-xs)}
  .result{margin-top:var(--pl-space-3);font-family:var(--pl-font-mono);font-size:var(--pl-font-size-xs);white-space:pre-wrap}
  textarea.copytext{width:100%;min-height:calc(var(--pl-space-12) * 3.5);font-family:var(--pl-font-mono);font-size:var(--pl-font-size-xs)}
  @media (max-width:639px){.form{grid-template-columns:1fr}.foot > *{min-height:var(--pl-space-12)}}
  @media (max-width:999px){.col-lot{display:none}}
  @media (prefers-reduced-motion:reduce){*{animation:none;transition:none}}
</style>
<script>
  // RULE 3 — slug-aware base: "" on the host window, "/agents/<slug>" through the fleet proxy.
  var BASE = location.pathname.split("/plugins/")[0];
  // RULE 4 — the DS kit's CSS off BASE (it re-themes live with the operator's theme).
  (function(){ var l=document.createElement("link"); l.rel="stylesheet"; l.href=BASE+"/_ds/plugin-kit.css"; document.head.appendChild(l); })();
</script>
</head><body>
<div class="view">
  <div class="pl-panel-header pl-panel-header--compact">
    <h1 class="pl-panel-header__title">Inventory</h1>
    <div class="pl-panel-header__actions anchor">
      <button class="pl-btn pl-btn--sm pl-btn--ghost" id="more" aria-haspopup="menu" aria-expanded="false">More</button>
      <div class="pop" id="more-menu" hidden></div>
    </div>
  </div>
  <div class="view-tabs"><div class="pl-tabs" role="tablist" id="tabs" aria-label="Inventory">
    <button class="pl-tab" role="tab" data-tab="next">Next</button>
    <button class="pl-tab" role="tab" data-tab="stock">Stock</button>
    <button class="pl-tab" role="tab" data-tab="lots">Lots</button>
  </div></div>
  <div id="err" class="pl-callout pl-callout--error" role="alert" hidden></div>
  <main class="main" id="main" role="tabpanel"></main>
</div>
<div id="overlay-root"></div>
<div id="dialog-root"></div>
<div class="pl-toast-stack" id="toasts" data-position="top-center" aria-live="polite"></div>
<script type="module">
  const $ = (s, r = document) => r.querySelector(s);
  // RULE 4 — plugin-kit.js is an ES module → dynamic import. With no kit there is no bearer, so
  // every data call would only 401: say what is wrong and stop (protoAgent #2392).
  function kitFailed(e) {
    const el = $("#err"); el.hidden = false;
    el.textContent = "Couldn't load the design kit from /_ds/plugin-kit.js, so this view can't reach its data. Reload, or update protoAgent. (" + String((e && e.message) || e) + ")";
    return null;
  }
  const kit = await import(BASE + "/_ds/plugin-kit.js").catch(kitFailed);

  const API = "/api/plugins/inventory";
  const STATUSES = ["planned","available","listed","pending","kept","withdrawn"];
  const STATUS_LABEL = { planned: ["to split", ""], available: ["available", ""], listed: ["listed", "info"], pending: ["pending", "warning"], sold: ["sold", "success"], kept: ["kept", ""], withdrawn: ["withdrawn", ""] };
  // Where a piece is in its life. Kept and withdrawn only show under All.
  const LIFECYCLE = [["sale", "For sale", ["available", "listed", "pending"]], ["split", "To split", ["planned"]], ["sold", "Sold", ["sold"]], ["all", "All", null]];
  const UNSOLD = ["planned", "available", "listed", "pending"];
  const STALE_DAYS = 45;
  const ROW_CAP = 5;
  // The usual shorthand for a miniature's state, offered as suggestions next to whatever is already in use.
  const CONDITION_CODES = ["NoS", "NIB", "Sealed", "Assembled", "Primed", "Painted", "Used"];
  const state = { tab: "next", lots: [], items: [], systems: [], conditions: [], selected: new Set(), summary: null, drawer: null, f: { seg: "sale", system: "", lot: "", q: "", preset: "" } };
  const mq = globalThis.matchMedia ? globalThis.matchMedia("(max-width:639px)") : null;
  const narrow = () => !!(mq && mq.matches);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = (v) => (v === null || v === undefined || v === "" ? "—" : (Number(v) < 0 ? "-" : "") + "$" + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const signed = (v) => (v === null || v === undefined || v === "" ? "—" : (Number(v) >= 0 ? "+" : "") + fmt(v));
  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
  // Chat-friendly price: whole dollars without cents ($20), cents only when they matter ($57.69).
  const chatPrice = (v) => (v === null || v === undefined || v === "" ? "" : (Number(v) < 0 ? "-" : "") + "$" + Math.abs(Number(v)).toLocaleString(undefined, Number.isInteger(Number(v)) ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  // One line per item, the way it reads in a group chat: "Name NoS — $20" — the condition
  // inline after the name, then the price. Josh's own post is the spec.
  const chatLine = (it) => {
    const cond = clean(it.condition) ? " " + clean(it.condition) : "";
    const price = chatPrice(it.target);
    return clean(it.name) + cond + (price ? " — " + price : "");
  };
  // The copied document, shaped like a for-sale post: the game system as a plain header
  // (blank → "Other", last), then one line per item in the grid's order. No bullets, no lots,
  // no categories, no status sections.
  function chatDoc(items) {
    const groups = new Map();
    for (const it of items) { const k = clean(it.system); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(it); }
    const order = (keys) => keys.filter((k) => k).sort((a, b) => a.localeCompare(b)).concat(keys.includes("") ? [""] : []);
    return order([...groups.keys()]).map((sys) => [sys || "Other", ...groups.get(sys).map(chatLine)].join("\n")).join("\n\n") + "\n";
  }
  const whole = (v) => chatPrice(v) || "—";
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* denied in this frame, or no secure context */ }
    try {
      const ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select(); const ok = document.execCommand("copy"); ta.remove(); return ok;
    } catch (e) { return false; }
  }


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

  // ── pure view logic ──────────────────────────────────────────────────────────
  const daysOld = (d, now = Date.now()) => (d ? (now - Date.parse(d.length === 7 ? d + "-01" : d)) / 864e5 : Infinity);
  const isStale = (i, now) => UNSOLD.includes(i.status) && i.target != null && daysOld(i.price_updated_on, now) > STALE_DAYS;
  const readyToList = (items) => items.filter((i) => i.status === "available" && i.target != null);
  function stockItems(items, f, now) {
    const seg = LIFECYCLE.find(([k]) => k === f.seg) || LIFECYCLE[0];
    const q = clean(f.q).toLowerCase();
    return items.filter((i) => {
      if (f.preset === "ready" && !(i.status === "available" && i.target != null)) return false;
      if (f.preset === "stale" && !isStale(i, now)) return false;
      if (!f.preset && seg[2] && !seg[2].includes(i.status)) return false;
      if (f.system && clean(i.system) !== f.system) return false;
      if (f.lot && i.lot_id !== f.lot) return false;
      return !q || [i.name, i.condition, i.category, i.notes, i.id].some((v) => String(v || "").toLowerCase().includes(q));
    });
  }
  // The same grouping and order Copy list uses, so what is on screen is what gets copied.
  function bySystem(items) {
    const groups = new Map();
    for (const it of items) { const k = clean(it.system); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(it); }
    const keys = [...groups.keys()];
    return keys.filter((k) => k).sort((a, b) => a.localeCompare(b)).concat(keys.includes("") ? [""] : []).map((k) => [k || "Other", groups.get(k)]);
  }
  const FIELD_LABEL = { name: "name", lot_id: "lot", category: "category", system: "system", condition: "condition", quantity: "quantity", model_count: "model count", notes: "notes", retail_cents: "retail", cost_basis_cents: "cost basis", description: "description", source: "source", acquired_on: "date", acquisition_cost_cents: "cost" };
  // One audit row as a sentence. `nameOf` turns an item id into the name the operator knows.
  function auditLine(a, nameOf = () => "") {
    const c = (a.changes && typeof a.changes === "object") ? a.changes : {};
    const snap = c.snapshot || {};
    const who = nameOf(a.entity_id) || c.name || snap.name || (snap.item && snap.item.name) || a.entity_id;
    if (a.entity === "sale") return "Sold " + (nameOf(c.item_id) || c.item_id || "an item") + " for " + whole(c.price) + (c.channel ? " on " + c.channel : "") + ", net " + whole(c.net);
    if (a.entity === "listing") return a.action === "end" ? "Listing " + a.entity_id + " ended" + (c.state ? " (" + c.state + ")" : "") : "Listed " + (nameOf(c.item_id) || c.item_id || "an item") + (c.channel ? " on " + c.channel : "");
    const kind = a.entity === "lot" ? "lot " : "";
    if (a.action === "create") return "Added " + kind + who;
    if (a.action === "delete") return "Deleted " + kind + who;
    if (a.entity === "item" && ("target_cents" in c || "target" in c || "price_basis" in c)) {
      const t = "target_cents" in c ? (c.target_cents === null ? null : c.target_cents / 100) : c.target;
      return "Price set on " + who + (t === undefined ? "" : ": " + whole(t)) + (c.price_basis ? ", basis " + clean(c.price_basis) : "");
    }
    if (a.entity === "item" && "status" in c) return who + ": now " + (STATUS_LABEL[c.status] || [c.status])[0];
    const fields = Object.keys(c).map((k) => FIELD_LABEL[k] || k.replace(/_/g, " "));
    return "Updated " + kind + who + (fields.length ? ": " + fields.join(", ") : "");
  }
  const auditWhen = (ts) => String(ts || "").replace("T", " ").replace(/:\d\dZ$/, "");

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
        (opts.step ? ' step="' + opts.step + '"' : "") + (opts.placeholder ? ' placeholder="' + esc(opts.placeholder) + '"' : "") + (opts.list ? ' list="' + esc(opts.list) + '"' : "") + (opts.required ? " required" : "") + (opts.readonly ? " readonly" : "") + ">";
    }
    return '<label class="pl-field' + (opts.span2 ? " span2" : "") + '"><span class="pl-field__label">' + esc(label) + "</span>" + input +
      (opts.hint ? '<span class="pl-field__hint">' + esc(opts.hint) + "</span>" : "") + "</label>";
  }
  function dialog({ title, body, submit = "Save", danger = false, onSubmit }) {
    return new Promise((resolve) => {
      const root = $("#dialog-root");
      root.innerHTML = '<div class="pl-overlay"><div class="pl-dialog" role="dialog" aria-modal="true" tabindex="-1">' +
        '<div class="pl-dialog__head"><div class="pl-dialog__title">' + esc(title) + "</div></div>" +
        '<form class="pl-dialog__body" id="dlg-form"><div class="form">' + body + '</div><div class="result" id="dlg-result"></div></form>' +
        '<div class="pl-dialog__foot"><button class="pl-btn" data-x type="button">' + (onSubmit ? "Cancel" : "Close") + "</button>" +
        (onSubmit ? '<button class="pl-btn ' + (danger ? "pl-btn--danger" : "pl-btn--primary") + '" id="dlg-ok" type="submit" form="dlg-form">' + esc(submit) + "</button>" : "") + "</div></div></div>";
      const onKey = (e) => { if (e.key === "Escape") close(false); };
      const close = (v) => { document.removeEventListener("keydown", onKey); root.innerHTML = ""; resolve(v); };
      document.addEventListener("keydown", onKey);
      root.querySelectorAll("[data-x]").forEach((b) => b.addEventListener("click", () => close(false)));
      root.querySelector(".pl-overlay").addEventListener("click", (e) => { if (e.target === e.currentTarget) close(false); });
      const form = $("#dlg-form");
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!onSubmit) return;
        const values = Object.fromEntries(new FormData(form).entries());
        const ok = $("#dlg-ok"); if (ok) { ok.disabled = true; ok.classList.add("pl-btn--loading"); }
        try { const r = await onSubmit(values, form); if (r !== false) close(true); }
        catch (err) { toast(err.message || String(err), "error"); }
        finally { if (ok) { ok.disabled = false; ok.classList.remove("pl-btn--loading"); } }
      });
      const first = form.querySelector("input:not([readonly]),select,textarea"); (first || root.querySelector(".pl-dialog")).focus();
      if (first && first.tagName === "TEXTAREA" && first.readOnly) first.select();
    });
  }
  const confirm = (title, text, submit = "Confirm") => dialog({ title, body: '<div class="span2">' + esc(text) + "</div>", submit, danger: true, onSubmit: async () => true });

  // ── drawer: the one surface for an item, a lot, and the history ──────────────
  let lastFocus = null;
  function closeDrawer() {
    state.drawer = null; $("#overlay-root").innerHTML = "";
    const again = lastFocus && lastFocus.dataset && lastFocus.dataset.item ? document.querySelector('[data-item="' + CSS.escape(lastFocus.dataset.item) + '"]') : lastFocus;
    if (again && again.isConnected) again.focus();
    lastFocus = null;
  }
  function paintDrawer(title, body, foot = "") {
    if (!$("#overlay-root").innerHTML) lastFocus = document.activeElement;
    const side = narrow() ? "bottom" : "right";
    const scroll = $("#overlay-root .pl-drawer__body"); const top = scroll ? scroll.scrollTop : 0;
    $("#overlay-root").innerHTML = '<div class="pl-overlay pl-overlay--drawer" data-close-bg><div class="pl-drawer pl-drawer--' + side + (side === "bottom" ? " sheet" : "") + '" role="dialog" aria-modal="true" aria-labelledby="dr-title" tabindex="-1">' +
      '<div class="pl-drawer__head"><div class="pl-drawer__title" id="dr-title">' + title + '</div><button class="pl-btn pl-btn--ghost pl-btn--sm" data-act="close">Close</button></div>' +
      '<div class="pl-drawer__body">' + body + "</div>" + (foot ? '<div class="pl-drawer__foot foot">' + foot + "</div>" : "") + "</div></div>";
    $("#overlay-root .pl-drawer__body").scrollTop = top;
    if (!top) $("#overlay-root .pl-drawer").focus();
  }
  let drawerSeq = 0;
  async function openItem(id) {
    const mine = ++drawerSeq; state.drawer = { kind: "item", id };
    try {
      const [{ item }, { audit }] = await Promise.all([api("/items/" + encodeURIComponent(id)), api("/audit?limit=20&entity_id=" + encodeURIComponent(id))]);
      if (mine !== drawerSeq) return;
      paintDrawer(esc(item.name), itemBody(item, audit), itemFoot(item));
    } catch (e) { if (mine === drawerSeq) { closeDrawer(); toast(e.message, "error"); } }
  }
  function openLot(id) {
    state.drawer = { kind: "lot", id }; ++drawerSeq;
    return paintLot(id);
  }
  async function paintLot(id) {
    const mine = drawerSeq;
    try {
      const { sales } = await api("/sales?lot_id=" + encodeURIComponent(id));
      if (mine !== drawerSeq) return;
      const l = state.summary && state.summary.lots.find((x) => x.id === id);
      if (!l) return closeDrawer();
      paintDrawer(esc(l.name || l.id), lotBody(l, sales), lotFoot(l));
    } catch (e) { if (mine === drawerSeq) { closeDrawer(); toast(e.message, "error"); } }
  }
  async function openHistory() {
    const mine = ++drawerSeq; state.drawer = { kind: "history" };
    try {
      const { audit } = await api("/audit?limit=200");
      if (mine !== drawerSeq) return;
      paintDrawer("History", audit.length ? '<ul class="hist">' + audit.map(histLine).join("") + "</ul>" : '<div class="pl-empty">Nothing yet.</div>');
    } catch (e) { if (mine === drawerSeq) { closeDrawer(); toast(e.message, "error"); } }
  }
  const nameOf = (id) => (state.items.find((i) => i.id === id) || {}).name || (state.lots.find((l) => l.id === id) || {}).name || "";
  const histLine = (a) => "<li>" + esc(auditLine(a, nameOf)) + '<span class="sub">' + esc(auditWhen(a.ts)) + " · " + esc(a.actor) + "</span></li>";
  const kv = (k, v) => '<div class="kv"><span class="muted">' + esc(k) + "</span><span>" + v + "</span></div>";
  const badge = (st) => { const [t, k] = STATUS_LABEL[st] || [st, ""]; return '<span class="pl-badge' + (k ? " pl-badge--" + k : "") + '">' + esc(t) + "</span>"; };
  const staleBadge = '<span class="pl-badge pl-badge--warning">stale</span>';
  const lotName = (id) => (state.lots.find((l) => l.id === id) || {}).name || id;

  function itemBody(it, audit) {
    const live = (it.listings || []).filter((l) => l.state === "active");
    const obs = (it.observations || [])[0];
    let h = '<div class="dsec"><div class="drow">' + badge(it.status) + (it.quantity > 1 ? '<span class="muted">×' + esc(it.quantity) + "</span>" : "") + (isStale(it) ? staleBadge : "") + "</div>";
    if (it.status === "sold") {
      h += (it.sales || []).map((s) => '<div class="kv"><span class="muted">' + esc(s.sold_on) + "</span><span>" + esc(s.channel) + " · sold " + whole(s.price) + ", net " + whole(s.net) + "</span></div>").join("");
    }
    h += "</div>";
    h += '<div class="dsec"><h3 class="dh">Price</h3><div class="money bigprice">' + esc(whole(it.target)) + "</div>" +
      (it.target != null ? '<div class="muted">' + esc(whole(it.target_low)) + " – " + esc(whole(it.target_high)) + "</div>" : "") +
      '<p class="basis">' + (it.price_basis ? esc(it.price_basis) : '<span class="muted">No basis recorded.</span>') + "</p>" +
      '<div class="muted">' + esc([it.price_updated_on, obs && obs.n ? obs.n + " comps" : ""].filter(Boolean).join(" · ")) + "</div>" +
      '<div class="drow"></div><button class="pl-btn pl-btn--sm" data-act="price" data-id="' + esc(it.id) + '">Set price</button></div>';
    h += '<div class="dsec"><h3 class="dh">Listing</h3>' +
      (live.length ? live.map((l) => '<div class="kv"><span class="muted">' + esc(l.channel) + "</span><span>" + esc(whole(l.price)) + (l.url ? ' · <a href="' + esc(/^https?:\/\//.test(l.url) ? l.url : "#") + '" target="_blank" rel="noopener">Open</a>' : "") + "</span></div>").join("") : '<p class="muted">Not listed.</p>') +
      (["sold", "kept", "withdrawn"].includes(it.status) ? "" : '<button class="pl-btn pl-btn--sm" data-act="list" data-id="' + esc(it.id) + '">Add listing</button>') + "</div>";
    h += '<div class="dsec"><h3 class="dh">Details</h3>' +
      kv("System", esc(it.system) || '<span class="muted">—</span>') +
      kv("Condition", esc(it.condition) || '<span class="muted">—</span>') +
      kv("Category", esc(it.category) || '<span class="muted">—</span>') +
      kv("Lot", it.lot_id ? '<button class="pl-btn pl-btn--ghost pl-btn--sm" data-lot="' + esc(it.lot_id) + '">' + esc(lotName(it.lot_id)) + "</button>" : '<span class="muted">—</span>') +
      kv("ID", '<span class="mono">' + esc(it.id) + "</span>") +
      (it.notes ? '<p class="basis notes">' + esc(it.notes) + "</p>" : "") + "</div>";
    if (audit && audit.length) h += '<div class="dsec"><h3 class="dh">History</h3><ul class="hist">' + audit.map(histLine).join("") + "</ul></div>";
    h += '<div class="dsec"><button class="pl-btn pl-btn--sm pl-btn--danger" data-act="del" data-id="' + esc(it.id) + '">Delete item</button></div>';
    return h;
  }
  function itemFoot(it) {
    const b = (act, label, primary = false) => '<button class="pl-btn' + (primary ? " pl-btn--primary" : "") + '" data-act="' + act + '" data-id="' + esc(it.id) + '">' + label + "</button>";
    const out = [];
    if (it.status === "sold" || it.status === "kept" || it.status === "withdrawn") out.push(b("return", "Return to stock"));
    else {
      out.push(b("sold", "Record sale", true));
      if (it.status === "planned") out.push(b("available", "Mark available"));
      out.push(b("keep", "Keep"), b("withdraw", "Withdraw"));
    }
    out.push(b("edit", "Edit"));
    return out.join("");
  }
  const stats = (cells, cols = narrow() ? 2 : cells.length) => '<div class="pl-stats" style="--pl-stats-cols:' + cols + '">' + cells.map(([n, l, s, neg]) => '<div class="pl-stat"><div class="pl-stat__num' + (neg ? " neg" : "") + '">' + esc(n) + '</div><div class="pl-stat__label">' + esc(l) + (s ? '<span class="sub">' + esc(s) + "</span>" : "") + "</div></div>").join("") + "</div>";
  const plCells = (cost, rem, net, gross, proj) => [[fmt(cost), "Lot cost"], [fmt(rem.target), "Remaining at target", fmt(rem.low) + " – " + fmt(rem.high)], [fmt(net), "Realized net", "gross " + fmt(gross)], [signed(proj), "Projected net", "if the rest sells at target", proj < 0]];
  function lotBody(l, sales) {
    const units = state.items.filter((i) => i.lot_id === l.id);
    const groups = [["Keep", ["kept"]], ["To split", ["planned"]], ["For sale", ["available", "listed", "pending"]], ["Sold", ["sold"]], ["Withdrawn", ["withdrawn"]]];
    let h = '<div class="dsec">' + stats(plCells(l.acquisition_cost, l.remaining, l.realized.net, l.realized.gross, l.projected_net_at_target), 2) + "</div>";
    for (const [label, sts] of groups) {
      const xs = units.filter((i) => sts.includes(i.status)); if (!xs.length) continue;
      h += '<div class="dsec"><h3 class="dh">' + esc(label) + ' <span class="muted">' + xs.length + '</span></h3><ul class="list">' + xs.map((i) => "<li>" + itemButton(i, clean(i.condition)) + "</li>").join("") + "</ul></div>";
    }
    if (sales.length) h += '<div class="dsec"><h3 class="dh">Sales <span class="muted">' + sales.length + '</span></h3><ul class="list">' + sales.map((s) => '<li><div class="irow static"><span class="irow-main"><span class="irow-name">' + esc(nameOf(s.item_id) || s.item_id) + '</span><span class="irow-sub">' + esc([s.sold_on, s.channel, "sold " + fmt(s.price), "ship in " + fmt(s.shipping_charged), "fees " + fmt(s.fees), "ship out " + fmt(s.shipping_cost)].join(" · ")) + '</span></span><span class="money">' + esc(fmt(s.net)) + "</span></div></li>").join("") + "</ul></div>";
    if (l.acquired_on || l.source || l.notes) h += '<div class="dsec"><h3 class="dh">Details</h3>' + (l.acquired_on ? kv("Acquired", esc(l.acquired_on)) : "") + (l.source ? kv("Source", esc(l.source)) : "") + (l.notes ? '<p class="basis notes">' + esc(l.notes) + "</p>" : "") + "</div>";
    return h;
  }
  function lotFoot(l) {
    return '<button class="pl-btn" data-act="editlot" data-id="' + esc(l.id) + '">Edit lot</button>' + (l.counts.total ? "" : '<button class="pl-btn pl-btn--danger" data-act="dellot" data-id="' + esc(l.id) + '">Delete lot</button>');
  }

  // ── loads ────────────────────────────────────────────────────────────────────
  async function loadCore() {
    const [lots, summary, items, systems, conditions] = await Promise.all([api("/lots"), api("/summary"), api("/items?limit=5000"), api("/systems").catch(() => ({ systems: [] })), api("/conditions").catch(() => ({ conditions: [] }))]);
    state.lots = lots.lots; state.summary = summary; state.items = items.items; state.systems = systems.systems || []; state.conditions = conditions.conditions || [];
  }
  let inflight = 0;
  async function refresh() {
    const mine = ++inflight;  // a slower earlier load must not paint over a newer one
    clearErr();
    try {
      await loadCore();
      if (mine !== inflight) return;
      render();
      const d = state.drawer;
      if (d && d.kind === "item") openItem(d.id);
      else if (d && d.kind === "lot") openLot(d.id);
      else if (d && d.kind === "history") openHistory();
    } catch (e) { if (mine === inflight) { showErr(e); render(); } }
  }

  // ── render ───────────────────────────────────────────────────────────────────
  function render() {
    if (state.f.lot && !state.lots.some((l) => l.id === state.f.lot)) state.f.lot = "";  // the lot is gone
    let dl = document.getElementById("systems-dl"); if (!dl) { dl = document.createElement("datalist"); dl.id = "systems-dl"; document.body.appendChild(dl); }
    dl.innerHTML = state.systems.map((s) => '<option value="' + esc(s) + '">').join("");
    let cdl = document.getElementById("conditions-dl"); if (!cdl) { cdl = document.createElement("datalist"); cdl.id = "conditions-dl"; document.body.appendChild(cdl); }
    cdl.innerHTML = [...new Set([...CONDITION_CODES, ...state.conditions])].map((c) => '<option value="' + esc(c) + '">').join("");
    document.querySelectorAll("#tabs .pl-tab").forEach((b) => { const on = b.dataset.tab === state.tab; b.classList.toggle("pl-tab--active", on); b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    const main = $("#main");
    if (state.tab === "next") main.innerHTML = renderNext();
    else if (state.tab === "stock") { main.innerHTML = renderStock(); renderResults(); }
    else main.innerHTML = renderLots();
  }
  function go(tab) { state.tab = tab; render(); $("#main").scrollTop = 0; }
  const qtyTag = (i) => (i.quantity > 1 ? ' <span class="muted">×' + esc(i.quantity) + "</span>" : "");
  const itemButton = (i, subline) => '<button class="irow" data-item="' + esc(i.id) + '"><span class="irow-main"><span class="irow-name">' + esc(i.name) + qtyTag(i) + '</span><span class="irow-sub">' + esc(subline) + "</span></span>" + (isStale(i) ? staleBadge : "") + '<span class="money">' + esc(whole(i.target)) + "</span></button>";
  function sec(title, n, rows, see) {
    return '<section class="sec"><div class="sec-head"><h2 class="sec-title">' + esc(title) + ' <span class="muted">' + n + "</span></h2>" + (see ? '<button class="pl-btn pl-btn--ghost pl-btn--xs" data-see="' + see + '">See all ' + n + "</button>" : "") + '</div><ul class="list">' + rows + "</ul></section>";
  }
  function renderNext() {
    const items = state.items;
    let h = "";
    const ready = readyToList(items);
    if (ready.length) h += sec("Ready to list", ready.length, ready.slice(0, ROW_CAP).map((i) => "<li>" + itemButton(i, clean(i.condition) || clean(i.system)) + "</li>").join(""), "ready");
    const split = items.filter((i) => i.status === "planned");
    if (split.length) {
      const byLot = new Map();
      for (const i of split) { if (!byLot.has(i.lot_id)) byLot.set(i.lot_id, []); byLot.get(i.lot_id).push(i); }
      const rows = [...byLot].slice(0, ROW_CAP).map(([lot, xs]) => {
        const l = state.lots.find((x) => x.id === lot);
        const value = xs.reduce((a, i) => a + (i.target || 0) * (i.quantity || 1), 0);
        const label = esc(l ? l.name : "No lot");
        const sub = xs.length + " to split" + (l && l.acquisition_cost ? " · " + whole(l.acquisition_cost) + " cost" : "");
        return "<li><button class=\"irow\" " + (lot ? 'data-lot="' + esc(lot) + '"' : 'data-see="split"') + '><span class="irow-main"><span class="irow-name">' + label + '</span><span class="irow-sub">' + esc(sub) + '</span></span><span class="money">' + esc(whole(Math.round(value * 100) / 100)) + "</span></button></li>";
      }).join("");
      h += sec("To split", split.length, rows, "split");
    }
    const stale = items.filter((i) => isStale(i)).length;
    if (stale) h += '<section class="sec"><div class="sec-head"><h2 class="sec-title">Housekeeping</h2></div><ul class="list"><li><button class="irow" data-see="stale"><span class="irow-main">' + stale + " price" + (stale === 1 ? " is" : "s are") + " over " + STALE_DAYS + ' days old</span><span class="muted">See them</span></button></li></ul></section>';
    return h || '<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">Nothing waiting</div><div class="pl-empty__desc">No priced stock to list and nothing to split.</div></div>';
  }
  function chipsHtml() {
    const f = state.f, chips = [];
    if (f.preset === "ready") chips.push(["preset", "Ready to list"]);
    if (f.preset === "stale") chips.push(["preset", "Stale price"]);
    if (f.system) chips.push(["system", f.system]);
    if (f.lot) chips.push(["lot", lotName(f.lot)]);
    return chips.length ? '<div class="chips">' + chips.map(([k, l]) => '<span class="pl-tag">' + esc(l) + '<button class="pl-tag__remove" data-unchip="' + k + '" aria-label="Remove filter ' + esc(l) + '">×</button></span>').join("") + "</div>" : "";
  }
  function renderStock() {
    const f = state.f;
    const segs = LIFECYCLE.map(([k, l]) => { const on = !f.preset && f.seg === k; return '<button class="pl-segmented__btn' + (on ? " pl-segmented__btn--active" : "") + '" data-seg="' + k + '" aria-pressed="' + on + '">' + esc(l) + "</button>"; }).join("");
    const opt = (v, l, cur) => '<option value="' + esc(v) + '"' + (v === cur ? " selected" : "") + ">" + esc(l) + "</option>";
    return '<div class="toolbar"><div class="pl-segmented" role="group" aria-label="Lifecycle">' + segs + "</div>" +
      '<input class="pl-input grow" id="f-q" type="search" placeholder="Search name, condition, id" aria-label="Search" value="' + esc(f.q) + '">' +
      '<div class="anchor"><button class="pl-btn pl-btn--sm" id="filter-btn" aria-haspopup="true" aria-expanded="false">Filter</button>' +
      '<div class="pop pl-popover" id="filter-pop" hidden><div class="pop-form">' +
      '<label class="pl-field"><span class="pl-field__label">System</span><select class="pl-field__input" id="f-system">' + opt("", "Any", f.system) + state.systems.map((s) => opt(s, s, f.system)).join("") + "</select></label>" +
      '<label class="pl-field"><span class="pl-field__label">Lot</span><select class="pl-field__input" id="f-lot">' + opt("", "Any", f.lot) + state.lots.map((l) => opt(l.id, l.name || l.id, f.lot)).join("") + "</select></label>" +
      "</div></div></div></div>" +
      '<div id="chips">' + chipsHtml() + '</div><div id="results"></div><div id="selbar"></div>';
  }
  const checkbox = (id, label, on) => '<label class="hit pl-checkbox"><input type="checkbox" class="pl-checkbox__input" ' + id + (on ? " checked" : "") + '><span class="pl-checkbox__box"></span><span class="sr-only">' + esc(label) + "</span></label>";
  function renderResults() {
    const root = $("#results"); if (!root) return;
    const xs = stockItems(state.items, state.f);
    $("#chips").innerHTML = chipsHtml();
    if (!xs.length) { root.innerHTML = '<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">Nothing matches</div><div class="pl-empty__desc">Clear a filter or change the lifecycle.</div></div>'; return renderSelbar(xs); }
    const allOn = xs.every((i) => state.selected.has(i.id));
    const row = (i) => {
      const on = state.selected.has(i.id);
      const sel = checkbox('data-sel="' + esc(i.id) + '"', "Select " + i.name, on);
      if (narrow()) return "<li" + (on ? ' class="pl-tr--selected"' : "") + ">" + sel + itemButton(i, [clean(i.condition), STATUS_LABEL[i.status] ? STATUS_LABEL[i.status][0] : i.status].filter(Boolean).join(" · ")) + "</li>";
      return '<tr class="pl-tr--interactive trow' + (on ? " pl-tr--selected" : "") + '" data-item="' + esc(i.id) + '" tabindex="0"><td>' + sel + "</td>" +
        "<td>" + esc(i.name) + qtyTag(i) + (clean(i.condition) ? '<span class="sub">' + esc(i.condition) + "</span>" : "") + "</td>" +
        "<td>" + badge(i.status) + (isStale(i) ? " " + staleBadge : "") + '</td><td class="money col-num">' + esc(whole(i.target)) + '</td><td class="col-lot muted">' + esc(i.lot_id ? lotName(i.lot_id) : "—") + "</td></tr>";
    };
    const groups = bySystem(xs);
    const count = xs.length + " item" + (xs.length === 1 ? "" : "s");
    if (narrow()) {
      root.innerHTML = '<div class="count">' + checkbox('id="sel-all"', "Select all visible", allOn) + "<span>" + count + "</span></div>" +
        groups.map(([sys, its]) => '<div class="group"><div class="pl-eyebrow group-label">' + esc(sys) + " · " + its.length + '</div><ul class="list">' + its.map(row).join("") + "</ul></div>").join("");
    } else {
      root.innerHTML = '<table class="pl-table"><thead><tr><th>' + checkbox('id="sel-all"', "Select all visible", allOn) + '</th><th>Item</th><th>Status</th><th class="money">Target</th><th class="col-lot">Lot</th></tr></thead>' +
        groups.map(([sys, its]) => '<tbody><tr><th colspan="5" class="pl-eyebrow" scope="colgroup">' + esc(sys) + " · " + its.length + "</th></tr>" + its.map(row).join("") + "</tbody>").join("") + "</table>";
    }
    renderSelbar(xs);
  }
  function renderSelbar(xs) {
    const bar = $("#selbar"); if (!bar) return;
    const n = state.selected.size;
    bar.className = n ? "selbar" : "";
    if (!n) { bar.innerHTML = ""; return; }
    const visible = xs.filter((it) => state.selected.has(it.id)).length;
    bar.innerHTML = '<span class="selbar-count">' + n + " selected" + (visible !== n ? " (" + visible + " visible)" : "") + "</span>" +
      '<button class="pl-btn pl-btn--sm pl-btn--primary" id="copy-md" title="System header, then one line per item: Name NoS — $20">Copy list</button>' +
      '<button class="pl-btn pl-btn--sm pl-btn--ghost" id="sel-clear">Clear</button>' +
      (narrow() ? "" : '<span class="pl-kbd">⌘C</span>');
  }
  async function copySelected() {
    if (!state.selected.size) return;
    // Re-read from the server rather than trusting the cache: an item deleted or re-priced
    // by the agent since the grid loaded must not leak stale into the copied text.
    let live; try { live = (await api("/items?limit=5000")).items; } catch (e) { toast(e.message, "error"); return; }
    const byId = Object.fromEntries(live.map((it) => [it.id, it]));
    const dropped = [...state.selected].filter((id) => !byId[id]);
    for (const id of dropped) state.selected.delete(id);
    const items = live.filter((it) => state.selected.has(it.id));
    if (!items.length) { toast("Nothing left to copy — the selected items no longer exist", "warning"); await refresh(); return; }
    const text = chatDoc(items);
    const note = dropped.length ? " (" + dropped.length + " no longer exist and were skipped)" : "";
    if (await copyText(text)) { toast("Copied " + items.length + " item" + (items.length === 1 ? "" : "s") + " for chat" + note, dropped.length ? "warning" : "success"); if (dropped.length) await refresh(); return; }
    await dialog({ title: "Copy " + items.length + " items", body: '<label class="pl-field span2"><span class="pl-field__label">The clipboard is blocked in this frame — select all and copy</span><textarea class="pl-field__input copytext" id="copytext" readonly>' + esc(text) + "</textarea></label>", onSubmit: null });
  }
  function renderLots() {
    const s = state.summary; if (!s || !s.lots.length) return '<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">No lots yet</div><div class="pl-empty__desc">Add one from More.</div></div>';
    const t = s.totals;
    const rows = s.lots.map((l) => {
      const sub = [l.acquired_on || "no date", l.counts.total + " item" + (l.counts.total === 1 ? "" : "s") + (l.counts.sold ? " · " + l.counts.sold + " sold" : "")].join(" · ");
      const proj = l.projected_net_at_target;
      return '<li><button class="irow" data-lot="' + esc(l.id) + '"><span class="irow-main"><span class="irow-name">' + esc(l.name || l.id) + '</span><span class="irow-sub">' + esc(sub) + '</span></span><span class="money' + (proj < 0 ? " neg" : "") + '">' + esc(signed(proj)) + "</span></button></li>";
    }).join("");
    return '<section class="sec">' + stats(plCells(t.acquisition_cost, t.remaining, t.realized_net, t.realized_gross, t.projected_net_at_target)) + "</section>" +
      '<section class="sec"><div class="sec-head"><h2 class="sec-title">Lots <span class="muted">' + s.lots.length + '</span></h2><span class="muted">Projected net</span></div><ul class="list">' + rows + "</ul></section>";
  }

  // ── actions ──────────────────────────────────────────────────────────────────
  const itemById = (id) => state.items.find((i) => i.id === id);
  const lotOptions = (current = "") => {
    const opts = [["", "— none —"]].concat(state.lots.map((l) => [l.id, l.name || l.id]));
    if (current && !state.lots.some((l) => l.id === current)) opts.push([current, "(unknown lot " + current + ")"]);
    return opts;
  };

  async function itemDialog(item) {
    const it = item || { lot_id: state.f.lot, status: "available", quantity: 1 };
    const sold = it.status === "sold";  // a sold item changes state through Return to stock, never here
    await dialog({
      title: item ? "Edit " + item.name : "New item", submit: item ? "Save" : "Add",
      body: field("name", "Name", it.name, { required: true, span2: true }) + field("lot_id", "Lot", it.lot_id, { type: "select", options: lotOptions(it.lot_id) }) +
        field("system", "Game system", it.system || "", { placeholder: "Warhammer 40K, Blood Bowl…", list: "systems-dl", hint: "Optional. Lists and Copy list group by it." }) +
        field("category", "Category", it.category) + field("condition", "Condition", it.condition, { placeholder: "NoS, NIB, Sealed, Assembled, Painted…", list: "conditions-dl", hint: "Printed inline after the name in a copied list." }) +
        (sold ? "" : field("status", "Status", it.status, { type: "select", options: STATUSES })) +
        field("quantity", "Quantity", it.quantity ?? 1, { type: "number", step: "1" }) + field("model_count", "Model count", it.model_count ?? "", { type: "number", step: "1" }) +
        field("retail", "Retail ($)", it.retail ?? "", { type: "number", step: "0.01", hint: "The anchor price, not a target." }) + field("cost_basis", "Cost basis ($)", it.cost_basis ?? "", { type: "number", step: "0.01" }) +
        field("notes", "Notes", it.notes, { type: "textarea", span2: true }),
      onSubmit: async (v) => {
        if (!v.name.trim()) throw new Error("A name is required");
        const body = { name: v.name, lot_id: v.lot_id, category: v.category, system: v.system, condition: v.condition, quantity: num(v.quantity), model_count: num(v.model_count), retail: num(v.retail), cost_basis: num(v.cost_basis), notes: v.notes };
        if (!sold && (!item || v.status !== item.status)) body.status = v.status;
        if (item) await api("/items/" + encodeURIComponent(item.id), "PUT", body); else await api("/items", "POST", body);
        toast(item ? "Saved" : "Item added", "success"); await refresh();
      },
    });
  }
  async function lotDialog(lot) {
    const l = lot || {};
    await dialog({
      title: lot ? "Edit lot " + (lot.name || lot.id) : "New lot", submit: lot ? "Save" : "Add",
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
        '<div class="span2 pl-eyebrow">Evidence (optional — recorded as a price observation)</div>' +
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
    await dialog({
      title: "Listing · " + it.name, submit: "Add listing",
      body: field("channel", "Channel", "eBay", { required: true }) + field("price", "Listed price ($)", it.target ?? "", { type: "number", step: "0.01" }) +
        field("url", "URL", "", { span2: true, placeholder: "https://www.ebay.com/itm/…" }) + field("notes", "Notes", "", { span2: true }),
      onSubmit: async (v) => { await api("/items/" + encodeURIComponent(id) + "/listings", "POST", { channel: v.channel, url: v.url, price: num(v.price), notes: v.notes }); toast("Listing added", "success"); await refresh(); },
    });
  }
  async function setStatus(id, status) {
    const it = itemById(id); if (!it) return;
    if (it.status === "sold" && !(await confirm(`Un-sell ${it.name}?`, "This item has a recorded sale, which stays on the books. Only do this if the sale fell through — and consider force-recording a refund instead."))) return;
    try { await api("/items/" + encodeURIComponent(id), "PUT", { status }); toast("Moved to " + STATUS_LABEL[status][0], "success"); } catch (e) { toast(e.message, "error"); }
    await refresh();
  }
  async function deleteItem(id) {
    const it = itemById(id);
    if (!(await confirm("Delete " + id + "?", "\"" + (it && it.name) + "\" and its listings, sales and price history are removed permanently. Prefer Withdraw or Keep to keep the record.", "Delete"))) return;
    try { await api("/items/" + encodeURIComponent(id), "DELETE"); state.selected.delete(id); state.drawer = null; closeDrawer(); toast("Deleted", "success"); } catch (e) { toast(e.message, "error"); }
    await refresh();
  }
  async function deleteLot(id) {
    if (!(await confirm("Delete lot " + id + "?", "Only an empty lot can be deleted. Items keep their history.", "Delete"))) return;
    try { await api("/lots/" + encodeURIComponent(id), "DELETE"); if (state.f.lot === id) state.f.lot = ""; state.drawer = null; closeDrawer(); toast("Lot deleted", "success"); } catch (e) { toast(e.message, "error"); }
    await refresh();
  }
  async function importDialog() {
    await dialog({
      title: "Import CSV", submit: "Import",
      body: '<label class="pl-field span2"><span class="pl-field__label">CSV file</span><input class="pl-field__input" type="file" id="csv-file" accept=".csv,text/csv"></label>' +
        field("csv", "…or paste CSV", "", { type: "textarea", span2: true, placeholder: "inventory_id,lot_id,item,target_price_usd,status\n…" }) +
        field("kind", "Kind", "auto", { type: "select", options: [["auto", "auto-detect"], ["items", "items"], ["lots", "lots"]] }) + field("default_lot", "Default lot", state.f.lot, { type: "select", options: lotOptions(), hint: "Used when a row has no lot_id." }),
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
      const qs = new URLSearchParams({ kind: state.tab === "lots" ? "lots" : "items" }); if (state.f.lot) qs.set("lot_id", state.f.lot);
      const r = await kit.apiFetch(API + "/export?" + qs.toString()); const text = await r.text(); if (!r.ok) throw new Error(text);
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = "inventory-" + (qs.get("kind")) + "-" + new Date().toISOString().slice(0, 10) + ".csv"; document.body.appendChild(a); a.click(); a.remove();
      toast("Exported", "success");
    } catch (e) { toast(e.message || String(e), "error"); }
  }

  // ── wiring ───────────────────────────────────────────────────────────────────
  const MENU = [["Add item by hand", () => itemDialog(null)], ["New lot", () => lotDialog(null)], ["Import CSV", importDialog], ["Export CSV", exportCsv], ["History", openHistory]];
  const ACTS = {
    close: closeDrawer,
    price: (id) => priceDialog(id), sold: (id) => soldDialog(id), list: (id) => listingDialog(id),
    edit: async (id) => itemDialog((await api("/items/" + encodeURIComponent(id))).item),
    del: deleteItem, keep: (id) => setStatus(id, "kept"), withdraw: (id) => setStatus(id, "withdrawn"),
    available: (id) => setStatus(id, "available"), return: (id) => setStatus(id, "available"),
    editlot: (id) => lotDialog(state.lots.find((l) => l.id === id)), dellot: deleteLot,
  };
  function seeAll(what) {
    if (what === "split") Object.assign(state.f, { seg: "split", preset: "" });
    else state.f.preset = what;
    go("stock");
  }
  function setPop(id, btnId, open) {
    const pop = $(id), btn = $(btnId); if (!pop || !btn) return;
    pop.hidden = !open; btn.setAttribute("aria-expanded", String(open));
  }
  function wire() {
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#more,#more-menu")) setPop("#more-menu", "#more", false);
      if (!e.target.closest("#filter-btn,#filter-pop")) setPop("#filter-pop", "#filter-btn", false);
      if (e.target.closest("#more")) { const m = $("#more-menu"); m.innerHTML = '<div class="pl-menu" role="menu">' + MENU.map(([l], i) => '<button class="pl-menu__item" role="menuitem" data-menu="' + i + '">' + esc(l) + "</button>").join("") + "</div>"; return setPop("#more-menu", "#more", m.hidden); }
      if (e.target.closest("#filter-btn")) return setPop("#filter-pop", "#filter-btn", $("#filter-pop").hidden);
      if (e.target.closest("#copy-md")) return copySelected();
      if (e.target.closest("#sel-clear")) { state.selected.clear(); return renderResults(); }
      if (e.target.matches("[data-close-bg]")) return closeDrawer();
      if (e.target.closest(".hit")) return;
      const t = e.target.closest("[data-tab],[data-item],[data-lot],[data-see],[data-seg],[data-unchip],[data-act],[data-menu]");
      if (!t) return;
      const d = t.dataset;
      if (d.tab) { closeDrawer(); return go(d.tab); }
      if (d.see) return seeAll(d.see);
      if (d.item) return openItem(d.item);
      if (d.lot) return openLot(d.lot);
      if (d.seg) { Object.assign(state.f, { seg: d.seg, preset: "" }); return render(); }
      if (d.unchip) { state.f[d.unchip] = ""; return render(); }
      if (d.menu) { setPop("#more-menu", "#more", false); return MENU[+d.menu][1](); }
      if (d.act) return ACTS[d.act](d.id);
    });
    document.addEventListener("change", (e) => {
      const t = e.target;
      if (t.dataset.sel) { if (t.checked) state.selected.add(t.dataset.sel); else state.selected.delete(t.dataset.sel); return renderResults(); }
      if (t.id === "sel-all") { for (const it of stockItems(state.items, state.f)) { if (t.checked) state.selected.add(it.id); else state.selected.delete(it.id); } return renderResults(); }
      if (t.id === "f-system") { state.f.system = t.value; return renderResults(); }
      if (t.id === "f-lot") { state.f.lot = t.value; return renderResults(); }
    });
    document.addEventListener("input", (e) => { if (e.target.id === "f-q") { state.f.q = e.target.value; renderResults(); } });
    document.addEventListener("keydown", (e) => {
      const dialogOpen = !!$("#dialog-root").innerHTML;
      if (e.key === "Escape" && state.drawer && !dialogOpen) return closeDrawer();
      if (e.key === "Tab" && state.drawer && !dialogOpen) {
        const box = $("#overlay-root .pl-drawer"); if (!box) return;
        const f = box.querySelectorAll("button:not([disabled]),a[href],input,select,textarea");
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        return;
      }
      if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("tr[data-item]")) { e.preventDefault(); return openItem(e.target.dataset.item); }
      if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && e.target.closest && e.target.closest("#tabs")) {
        const tabs = [...document.querySelectorAll("#tabs .pl-tab")], i = tabs.indexOf(e.target.closest(".pl-tab"));
        const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        go(next.dataset.tab); return next.focus();
      }
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "c" || !state.selected.size) return;
      const el = document.activeElement; if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && !el.closest(".hit")) return;
      if (String(window.getSelection && window.getSelection()).length) return;  // a real text selection wins
      e.preventDefault(); copySelected();
    });
    if (mq) mq.addEventListener("change", () => { render(); const d = state.drawer; if (d) refresh(); });
  }

  // Boot once, on whichever fires first: the kit's protoagent:init handshake (bearer + theme) or a short timer.
  let booted = false;
  function boot() { if (booted) return; booted = true; refresh(); }
  if (kit) { wire(); kit.initPluginView(boot); setTimeout(boot, 800); }
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
