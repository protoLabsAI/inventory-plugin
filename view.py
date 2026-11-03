"""The console view — a single vanilla-JS page served on the PUBLIC ``/plugins/inventory``
prefix (an iframe navigation carries no bearer), pulling everything through the gated
``/api/plugins/inventory`` API via the design-system kit's authed fetch.

The four rules (docs/guides/plugin-views.md): serve the declared path; gate data by default;
derive the base from the page's own URL (fleet-proxy slug aware); link the DS kit and let
it own theme tokens + the init handshake.
"""

from __future__ import annotations

from .labels import LABEL_PREFIX, console_root, norm_bin_id, older_host_page, view_target

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
  .row-acts{display:flex;gap:var(--pl-space-2)}
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
  .rowact{flex:none;margin:0 var(--pl-space-1)}
  .bin-id{font-family:var(--pl-font-mono)}
  .note{margin-bottom:var(--pl-space-3)}
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
  .form.one{grid-template-columns:1fr}
  .block{width:100%;justify-content:center;min-height:var(--pl-space-12)}
  .step-k{margin-bottom:var(--pl-space-2)}
  .gap-t{margin-top:var(--pl-space-3)}
  .thumb{display:block;width:100%;max-height:calc(var(--pl-space-12) * 5);object-fit:contain;margin:var(--pl-space-2) 0;background:var(--pl-color-bg-inset);border-radius:var(--pl-radius)}
  .thumb.ph{display:flex;align-items:center;justify-content:center;height:calc(var(--pl-space-12) * 2);border:var(--pl-border-width) dashed var(--pl-color-border-strong);color:var(--pl-color-fg-muted);font-size:var(--pl-font-size-xs)}
  .thumb.sm{flex:none;width:var(--pl-space-12);height:var(--pl-space-12);margin:0;object-fit:cover}
  .skel-line{display:block;height:var(--pl-space-3);margin:var(--pl-space-1) 0}
  .w60{width:60%}.w40{width:40%}
  .unit{flex-wrap:wrap;gap:var(--pl-space-2);padding:var(--pl-space-2) 0}
  .ubody{display:flex;align-items:center;gap:var(--pl-space-2);flex:1 1 14rem;min-width:0}
  .uact{display:flex;align-items:center;gap:var(--pl-space-1);margin-left:auto}
  .binsel{width:auto;max-width:12rem}
  .tall{min-height:var(--pl-space-12);min-width:var(--pl-space-12)}
  .cprow{display:flex;align-items:flex-start;gap:var(--pl-space-2);padding:var(--pl-space-2) 0;border-top:var(--pl-border-width) solid var(--pl-color-border)}
  .cprow .irow-main{min-width:0;flex:1}
  .cptext{display:block;margin-top:var(--pl-space-0_5);overflow-wrap:anywhere}
  .pre{white-space:pre-wrap;font-size:var(--pl-font-size-xs);color:var(--pl-color-fg-muted)}
  .cnt{font-size:var(--pl-font-size-2xs);margin-bottom:var(--pl-space-1)}
  .big{font-size:var(--pl-font-size-lg);margin-top:var(--pl-space-2)}
  .sc-sum{margin:var(--pl-space-3) 0 0}
  .rgroup{display:grid;gap:var(--pl-space-2)}
  .sc-foot{flex-direction:column}
  .frow{display:flex;gap:var(--pl-space-2);width:100%}
  .frow > *{flex:1 1 0}
  .scanbar{display:none}
  @media (max-width:639px){.form{grid-template-columns:1fr}.foot > *,.rowact{min-height:var(--pl-space-12)}.view .scan-top{display:none}.binsel{min-height:var(--pl-space-12)}.scanbar{display:block;padding:var(--pl-space-2) var(--pl-space-4) calc(var(--pl-space-2) + env(safe-area-inset-bottom));border-top:var(--pl-border-width) solid var(--pl-color-border);background:var(--pl-color-bg)}}
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
      <button class="pl-btn pl-btn--sm pl-btn--primary scan-top" data-act="scan">Scan</button>
      <button class="pl-btn pl-btn--sm pl-btn--ghost" id="more" aria-haspopup="menu" aria-expanded="false">More</button>
      <div class="pop" id="more-menu" hidden></div>
    </div>
  </div>
  <div class="view-tabs"><div class="pl-tabs" role="tablist" id="tabs" aria-label="Inventory">
    <button class="pl-tab" role="tab" data-tab="next">Next</button>
    <button class="pl-tab" role="tab" data-tab="stock">Stock</button>
    <button class="pl-tab" role="tab" data-tab="bins">Bins</button>
    <button class="pl-tab" role="tab" data-tab="lots">Lots</button>
  </div></div>
  <div id="err" class="pl-callout pl-callout--error" role="alert" hidden></div>
  <main class="main" id="main" role="tabpanel"></main>
  <div class="scanbar"><button class="pl-btn pl-btn--primary block" data-act="scan">Scan</button></div>
</div>
<div id="overlay-root"></div>
<div id="dialog-root"></div>
<div class="pl-toast-stack" id="toasts" data-position="top-center" aria-live="polite"></div>
<div class="sr-only" id="sr-live" aria-live="polite"></div>
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
  const STATUS_LABEL = { planned: ["to split", ""], available: ["available", ""], listed: ["listed", "info"], pending: ["pending", "warning"], sold: ["sold", "success"], kept: ["kept", ""], withdrawn: ["withdrawn", ""] };
  // Where a piece is in its life. Kept and withdrawn only show under All.
  const LIFECYCLE = [["sale", "For sale", ["available", "listed", "pending"]], ["split", "To split", ["planned"]], ["sold", "Sold", ["sold"]], ["all", "All", null]];
  const UNSOLD = ["planned", "available", "listed", "pending"];
  let STALE_DAYS = 30;  // the Stale price setting, read from /view-config on boot (same number the weekly review uses)
  const ROW_CAP = 5;
  // The usual shorthand for a miniature's state, offered as suggestions next to whatever is already in use.
  const CONDITION_CODES = ["NoS", "NIB", "Sealed", "Assembled", "Primed", "Painted", "Used"];
  const state = { tab: "next", lots: [], items: [], systems: [], conditions: [], bins: [], unbinned: 0, toPack: [], scans: [], threshold: 0.7, drafts: {}, maxBulk: 10, selected: new Set(), summary: null, drawer: null, moveTo: "", f: { seg: "sale", system: "", lot: "", bin: "", q: "", preset: "" } };
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
  // An unreadable date is no evidence of freshness: treat it like a missing one (stale).
  const daysOld = (d, now = Date.now()) => { if (!d) return Infinity; const t = Date.parse(d.length === 7 ? d + "-01" : d); return Number.isNaN(t) ? Infinity : (now - t) / 864e5; };
  const isStale = (i, now) => UNSOLD.includes(i.status) && i.target != null && daysOld(i.price_updated_on, now) > STALE_DAYS;
  const readyToList = (items) => items.filter((i) => i.status === "available" && i.target != null);
  function stockItems(items, f, now) {
    const seg = LIFECYCLE.find(([k]) => k === f.seg) || LIFECYCLE[0];
    const q = clean(f.q).toLowerCase();
    return items.filter((i) => {
      if (f.preset === "ready" && !(i.status === "available" && i.target != null)) return false;
      if (f.preset === "stale" && !isStale(i, now)) return false;
      if (f.preset === "nobin" && !(UNSOLD.includes(i.status) && !i.bin_id)) return false;
      if (!f.preset && seg[2] && !seg[2].includes(i.status)) return false;
      if (f.system && clean(i.system) !== f.system) return false;
      if (f.lot && i.lot_id !== f.lot) return false;
      if (f.bin === "-" ? !!i.bin_id : f.bin && i.bin_id !== f.bin) return false;
      if (f.exclude && i.bin_id === f.exclude) return false;  // moving into a bin: what is already there is not a choice
      return !q || [i.name, i.condition, i.category, i.notes, i.id, i.bin_id].some((v) => String(v || "").toLowerCase().includes(q));
    });
  }
  // The same grouping and order Copy list uses, so what is on screen is what gets copied.
  function bySystem(items) {
    const groups = new Map();
    for (const it of items) { const k = clean(it.system); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(it); }
    const keys = [...groups.keys()];
    return keys.filter((k) => k).sort((a, b) => a.localeCompare(b)).concat(keys.includes("") ? [""] : []).map((k) => [k || "Other", groups.get(k)]);
  }
  // The pick list, one group per bin in bin order, "no bin recorded" last.
  function packGroups(sales) {
    const groups = new Map();
    for (const s of sales) { const k = s.bin_id || ""; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(s); }
    const keys = [...groups.keys()];
    return keys.filter((k) => k).sort().concat(keys.includes("") ? [""] : []).map((k) => [k, groups.get(k)]);
  }
  const FIELD_LABEL = { name: "name", lot_id: "lot", category: "category", system: "system", condition: "condition", quantity: "quantity", model_count: "model count", notes: "notes", retail_cents: "retail", cost_basis_cents: "cost basis", description: "description", source: "source", acquired_on: "date", acquisition_cost_cents: "cost" };
  // One audit row as a sentence. `nameOf` turns an item id into the name the operator knows.
  function auditLine(a, nameOf = () => "") {
    const c = (a.changes && typeof a.changes === "object") ? a.changes : {};
    const snap = c.snapshot || {};
    const who = nameOf(a.entity_id) || c.name || snap.name || (snap.item && snap.item.name) || a.entity_id;
    if (a.entity === "sale" && a.action !== "packed") return "Sold " + (nameOf(c.item_id) || c.item_id || "an item") + " for " + whole(c.price) + (c.channel ? " on " + c.channel : "") + ", net " + whole(c.net);
    if (a.entity === "listing") return a.action === "end" ? "Listing " + a.entity_id + " ended" + (c.state ? " (" + c.state + ")" : "") : "Listed " + (nameOf(c.item_id) || c.item_id || "an item") + (c.channel ? " on " + c.channel : "");
    if (a.entity === "draft") return ({ request: "Asked for a listing draft for ", write: "Listing draft written for ", fail: "Listing draft failed for ", copy: "Copied the listing draft for ", live: "Marked live on eBay: " }[a.action] || "Listing draft updated: ") + who + (a.action === "fail" && c.reason ? " (" + clean(c.reason) + ")" : "");
    if (a.entity === "bin" && a.action === "labels") return "Printed the label for bin " + a.entity_id;
    if (a.entity === "bin") return (a.action === "create" ? "Made bin " : a.action === "delete" ? "Removed bin " : "Updated bin ") + a.entity_id + (c.label ? " (" + clean(c.label) + ")" : "");
    if (a.entity === "sale" && a.action === "packed") return "Packed " + (nameOf(c.item_id) || c.item_id || "an item");
    if (a.action === "move" && Array.isArray(c.bin)) {
      const [from, to] = c.bin;
      return (to ? "Moved " + who + (from ? " " + from + " → " + to : " into " + to) : "Took " + who + " out of " + from) + (c.reason === "packed" ? " (packed)" : "");
    }
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
        (opts.step ? ' step="' + opts.step + '"' : "") + (opts.placeholder ? ' placeholder="' + esc(opts.placeholder) + '"' : "") + (opts.list ? ' list="' + esc(opts.list) + '"' : "") + (opts.inputmode ? ' inputmode="' + esc(opts.inputmode) + '"' : "") + (opts.required ? " required" : "") + (opts.readonly ? " readonly" : "") + ">";
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
    if (state.drawer && state.drawer.kind === "scan") {
      stopPoll();
      if (SC.id && !(SC.data && SC.data.finished_at)) { toast("Saved. Pick it up from Next, under Needs a look"); setTimeout(refresh, 0); }
    }
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
      const [{ item }, { audit }, dr] = await Promise.all([api("/items/" + encodeURIComponent(id)), api("/audit?limit=20&entity_id=" + encodeURIComponent(id)), api("/items/" + encodeURIComponent(id) + "/draft").catch(() => null)]);
      if (mine !== drawerSeq) return;
      state.openDraft = dr ? dr.draft : null;
      paintDrawer(esc(item.name), itemBody(item, audit, state.openDraft), itemFoot(item));
      draftPoll(id, mine);
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
  function openBin(id) {
    ++drawerSeq; state.drawer = { kind: "bin", id };
    const b = binById(id);
    if (!b) { closeDrawer(); return toast("Bin " + id + " no longer exists", "warning"); }
    paintDrawer('<span class="bin-id">' + esc(b.id) + "</span>" + (b.label ? " · " + esc(b.label) : ""), binBody(b), binFoot(b));
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
  const binById = (id) => state.bins.find((b) => b.id === id);
  const binText = (id) => { const b = binById(id); return id ? id + (b && b.label ? " · " + b.label : "") : ""; };
  const forSale = (i) => ["available", "listed", "pending"].includes(i.status);
  function binBody(b) {
    const xs = state.items.filter((i) => i.bin_id === b.id);
    let h = '<div class="dsec"><div class="drow"><span>' + xs.length + " item" + (xs.length === 1 ? "" : "s") + '</span><span class="muted">· ' + esc(whole(b.value_at_target)) + " unsold at target</span></div>" + (b.note ? '<p class="basis notes">' + esc(b.note) + "</p>" : "") + '<div class="drow muted">' + (b.labels_printed_on ? "Label printed " + esc(b.labels_printed_on) : "No label printed yet") + "</div></div>";
    if (!xs.length) return h + '<div class="dsec"><div class="pl-empty"><div class="pl-empty__title">Nothing in ' + esc(b.id) + ' yet</div><div class="pl-empty__desc">Move items here to record that they live in this bin.</div></div></div>';
    for (const [sys, its] of bySystem(xs)) h += '<div class="dsec"><h3 class="dh">' + esc(sys) + ' <span class="muted">' + its.length + '</span></h3><ul class="list">' + its.map((i) => "<li>" + itemButton(i, [clean(i.condition), (STATUS_LABEL[i.status] || [i.status])[0]].filter(Boolean).join(" · ")) + "</li>").join("") + "</ul></div>";
    return h;
  }
  function binFoot(b) {
    const xs = state.items.filter((i) => i.bin_id === b.id);
    const btn = (act, label, primary = false) => '<button class="pl-btn' + (primary ? " pl-btn--primary" : "") + '" data-act="' + act + '" data-id="' + esc(b.id) + '">' + label + "</button>";
    return btn("movehere", "Move items here", true) + (xs.some(forSale) ? btn("copybin", "Copy list") : "") + (xs.length ? btn("binstock", "Select in Stock") : "") + btn("printlabel", "Print label") + btn("editbin", "Edit bin") + (xs.length ? "" : '<button class="pl-btn pl-btn--danger" data-act="delbin" data-id="' + esc(b.id) + '">Delete bin</button>');
  }

  // ── listing drafts: written by an agent turn, copied by you, listed by you ─────────
  const LSTATE = { drafting: ["drafting…", "warning"], failed: ["draft failed", "error"], draft: ["draft ready", ""], copied: ["copied, not live yet", "warning"], live: ["live on eBay", "info"], ended: ["ended", ""], sold: ["sold", "success"] };
  const draftBadge = (st) => { const [t, k] = LSTATE[st] || [st, ""]; return '<span class="pl-badge' + (k ? " pl-badge--" + k : "") + '">' + esc(t) + "</span>"; };
  const TITLE_MAX = 80;
  const clip = (t, n) => { const x = clean(t); return x.length > n ? x.slice(0, n - 1).trimEnd() + "…" : x; };
  const draftLabel = (d) => (!d || d.listing_state === "none" ? "no draft yet" : (LSTATE[d.listing_state] || [d.listing_state])[0]);
  const draftable = (it) => ["planned", "available"].includes(it.status);
  const cpRow = (k, label, text, extra = "") => '<div class="cprow"><div class="irow-main"><span class="pl-field__label">' + esc(label) + '</span><span class="cptext' + (k === "description" ? " pre" : "") + '">' + esc(text) + "</span>" + extra + '</div><button class="pl-btn pl-btn--sm tall" data-act="dcopy" data-k="' + k + '">Copy ' + esc(label.toLowerCase()) + "</button></div>";
  function draftBody(it, d) {
    // Kept, withdrawn or sold stock gets no new draft; a live one still shows its listing.
    if (!draftable(it) && !(d && d.state === "live")) return "";
    const st = d ? d.state : "none";
    const ask = (label, act = "draft") => '<button class="pl-btn pl-btn--sm' + (act === "draft" ? " pl-btn--primary" : "") + '" data-act="' + act + '" data-id="' + esc(it.id) + '">' + esc(label) + "</button>";
    if (st === "none") return it.target == null
      ? '<p class="muted">Set a price first: a draft lists at the item\'s target.</p>'
      : '<p class="muted">No draft yet. The agent writes a title and description from the record and photos.</p>' + ask("Draft listing");
    if (st === "drafting") return '<div class="pl-skel-lines" aria-hidden="true"><span class="pl-skel skel-line w60"></span><span class="pl-skel skel-line w40"></span></div><p class="muted" role="status">Drafting… usually under a minute. You can close this.</p>';
    if (st === "failed") return '<div class="pl-callout pl-callout--warning"><div class="pl-callout__body">' + esc(d.reason || "The draft didn't finish.") + "</div></div>" + '<div class="drow gap-t">' + ask("Try again") + "</div>";
    if (st === "live") return "";  // the live listing (with its Open link) shows above
    const n = d.title.length;
    const nPhotos = (d.photos || []).length;
    return cpRow("title", "Title", d.title, '<span class="muted cnt' + (n > TITLE_MAX ? " neg" : "") + '">' + n + " / " + TITLE_MAX + " characters</span>") +
      '<div class="cprow"><div class="irow-main"><span class="pl-field__label">Price</span><span class="money">' + esc(fmt(it.target)) + '</span><span class="irow-sub">' + esc(it.price_basis ? "Basis: " + clip(it.price_basis, 140) : "No basis recorded") + '</span><span class="irow-sub">Change it with Set price.</span></div>' + '<button class="pl-btn pl-btn--sm tall" data-act="dcopy" data-k="price">Copy price</button></div>' +
      cpRow("description", "Description", d.description) +
      '<div class="cprow"><div class="irow-main"><span class="pl-field__label">Photos</span><span class="irow-sub">' + (nPhotos ? nPhotos + " photo" + (nPhotos === 1 ? "" : "s") + ", metadata stripped, cover first" : "No photos from a scan") + "</span></div>" + (nPhotos ? '<button class="pl-btn pl-btn--sm tall" data-act="dphotos" data-id="' + esc(it.id) + '">Download photos</button>' : "") + "</div>" +
      (d.notes ? '<p class="basis notes muted">' + esc(d.notes) + "</p>" : "") +
      '<div class="drow gap-t">' + '<button class="pl-btn pl-btn--sm pl-btn--primary" data-act="dlive" data-id="' + esc(it.id) + '">I listed it</button>' + ask("Regenerate", "redraft") + "</div>" +
      '<p class="muted">Nothing posts for you. List it on eBay yourself, then paste its address here.</p>';
  }
  let draftTimer = 0;
  function draftPoll(id, seq) {
    if (draftTimer) clearTimeout(draftTimer); draftTimer = 0;
    if (!state.openDraft || state.openDraft.state !== "drafting") return;
    draftTimer = setTimeout(() => {
      draftTimer = 0;
      if (seq !== drawerSeq || !state.drawer || state.drawer.kind !== "item" || state.drawer.id !== id) return;
      if (document.hidden) return draftPoll(id, seq);
      api("/items/" + encodeURIComponent(id) + "/draft").then(async ({ draft }) => {
        if (seq !== drawerSeq) return;
        if (draft.state === "drafting") return draftPoll(id, seq);
        const live = $("#sr-live"); if (live) live.textContent = draft.state === "draft" ? "Draft ready" : "Draft failed";
        await refresh();
      }).catch(() => draftPoll(id, seq));
    }, POLL_MS);
  }
  async function requestDraft(id) {
    try { await api("/items/" + encodeURIComponent(id) + "/draft", "POST"); } catch (e) { return toast(e.message, "error"); }
    await refresh();
  }
  async function copyDraft(btn) {
    const d = state.openDraft; const it = d && state.items.find((i) => i.id === d.item_id); if (!d || !it) return;
    const k = btn.dataset.k;
    const text = k === "price" ? (Math.round(it.target * 100) / 100).toFixed(2).replace(/\.00$/, "") : k === "title" ? d.title : d.description;
    const ok = await copyText(text);
    if (!ok) return toast("Couldn't copy here. Select the text and copy it.", "warning");
    const was = btn.textContent; btn.textContent = "Copied"; setTimeout(() => { if (btn.isConnected) btn.textContent = was; }, 2000);
    if (d.state === "draft") {
      try { const { draft } = await api("/items/" + encodeURIComponent(d.item_id) + "/draft/copied", "POST", { what: k }); state.openDraft = draft; state.drafts[d.item_id] = draft; const b = document.querySelector("#lsec .dh .pl-badge"); if (b) b.outerHTML = draftBadge(draft.listing_state); }
      catch (e) { /* the copy worked; the state catches up on the next load */ }
    }
  }
  async function downloadPhotos(id) {
    try {
      const r = await kit.apiFetch(API + "/items/" + encodeURIComponent(id) + "/photos.zip");
      if (!r.ok) throw new Error((await r.text()) || r.status + " " + r.statusText);
      const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = id + "-photos.zip";
      document.body.appendChild(a); a.click(); a.remove();
    } catch (e) { toast("Couldn't download the photos: " + (e.message || e), "error"); }
  }
  async function liveDialog(id) {
    const it = state.items.find((i) => i.id === id); if (!it) return;
    await dialog({
      title: "Listed on eBay · " + it.name, submit: "Save",
      body: field("url", "The eBay listing's address", "", { span2: true, placeholder: "https://www.ebay.com/itm/…", required: true }) + '<div class="span2 muted">Open the listing, then Share ▸ Copy link. This records an eBay listing at ' + esc(fmt(it.target)) + " and marks the item listed.</div>",
      onSubmit: async (v) => { await api("/items/" + encodeURIComponent(id) + "/draft/live", "POST", { url: v.url }); toast("Recorded. " + it.name + " is listed", "success"); await refresh(); },
    });
  }
  async function bulkDraft() {
    const ids = readyToList(state.items).filter((i) => { const d = state.drafts[i.id]; return !d || d.state === "failed"; }).slice(0, state.maxBulk).map((i) => i.id);
    if (!ids.length) return toast("Everything ready to list already has a draft", "info");
    const ok = await dialog({
      title: "Draft " + ids.length + " listing" + (ids.length === 1 ? "" : "s") + "?", submit: "Draft " + ids.length,
      body: '<div class="span2">The agent writes ' + (ids.length === 1 ? "one draft" : "them one after another") + '. Each is one agent turn. Nothing is posted.</div>',
      onSubmit: async () => { const r = await api("/drafts/bulk", "POST", { item_ids: ids }); toast("Drafting " + r.started.length + (r.refused.length ? ". " + r.refused.length + " skipped" : ""), r.refused.length ? "warning" : "success"); },
    });
    if (ok) await refresh();
  }

  function itemBody(it, audit, draft = null) {
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
    h += '<div class="dsec" id="lsec"><h3 class="dh">Listing' + (draft && LSTATE[draft.listing_state] ? " " + draftBadge(draft.listing_state) : "") + "</h3>" +
      (live.length ? live.map((l) => '<div class="kv"><span class="muted">' + esc(l.channel) + "</span><span>" + esc(whole(l.price)) + (l.url ? ' · <a href="' + esc(/^https?:\/\//.test(l.url) ? l.url : "#") + '" target="_blank" rel="noopener">Open</a>' : "") + "</span></div>").join("") : "") +
      draftBody(it, draft) +
      (["sold", "kept", "withdrawn"].includes(it.status) ? "" : '<div class="drow gap-t"><button class="pl-btn pl-btn--sm pl-btn--ghost" data-act="list" data-id="' + esc(it.id) + '">Add a listing by hand</button></div>') + "</div>";
    h += '<div class="dsec"><h3 class="dh">Details</h3>' +
      kv("Bin", (it.bin_id ? '<button class="pl-btn pl-btn--ghost pl-btn--sm bin-id" data-bin="' + esc(it.bin_id) + '">' + esc(binText(it.bin_id)) + "</button>" : '<span class="muted">Not in a bin</span>') +
        (it.status === "sold" && !it.bin_id ? "" : ' <button class="pl-btn pl-btn--sm" data-act="movebin" data-id="' + esc(it.id) + '">' + (it.bin_id ? "Move" : "Put in a bin") + "</button>")) +
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
    const [lots, summary, items, systems, conditions, bins, toPack, scans, drafts, vcfg] = await Promise.all([api("/lots"), api("/summary"), api("/items?limit=5000"), api("/systems").catch(() => ({ systems: [] })), api("/conditions").catch(() => ({ conditions: [] })), api("/bins"), api("/to-pack"), api("/scans").catch(() => ({ scans: [], threshold: 0.7 })), api("/drafts").catch(() => ({ drafts: [], max_bulk: 10 })), api("/view-config").catch(() => ({}))]);
    state.lots = lots.lots; state.summary = summary; state.items = items.items; state.systems = systems.systems || []; state.conditions = conditions.conditions || [];
    state.bins = bins.bins; state.unbinned = bins.unbinned_unsold; state.toPack = toPack.sales;
    state.scans = scans.scans || []; if (typeof scans.threshold === "number") state.threshold = scans.threshold;
    if (vcfg && Number(vcfg.stale_price_days) > 0) STALE_DAYS = Number(vcfg.stale_price_days);
    state.drafts = Object.fromEntries((drafts.drafts || []).map((d) => [d.item_id, d])); state.maxBulk = drafts.max_bulk || 10;
  }
  // A scanned bin label lands here as ?bin=B07 (the console forwards it to this frame).
  const START_BIN = (() => { try { return (new URLSearchParams(location.search || "").get("bin") || "").trim().toUpperCase(); } catch { return ""; } })();
  let startDone = !START_BIN;
  function applyStartBin() {
    startDone = true; state.tab = "bins"; render();
    if (binById(START_BIN)) return openBin(START_BIN);
    toast("There's no bin " + START_BIN + " here. It may have been deleted, or the label belongs to another agent.", "warning");
  }
  let inflight = 0;
  async function refresh() {
    const mine = ++inflight;  // a slower earlier load must not paint over a newer one
    clearErr();
    try {
      await loadCore();
      if (mine !== inflight) return;
      render();
      if (!startDone) return applyStartBin();
      const d = state.drawer;
      if (d && d.kind === "item") openItem(d.id);
      else if (d && d.kind === "lot") openLot(d.id);
      else if (d && d.kind === "bin") openBin(d.id);
      else if (d && d.kind === "history") openHistory();
      else if (d && d.kind === "scan") scPaint();
    } catch (e) { if (mine === inflight) { showErr(e); render(); } }
  }

  // ── render ───────────────────────────────────────────────────────────────────
  function render() {
    if (state.f.lot && !state.lots.some((l) => l.id === state.f.lot)) state.f.lot = "";  // the lot is gone
    if (state.f.bin && state.f.bin !== "-" && !binById(state.f.bin)) state.f.bin = "";  // the bin is gone
    if (state.moveTo && !binById(state.moveTo)) endMove();
    let dl = document.getElementById("systems-dl"); if (!dl) { dl = document.createElement("datalist"); dl.id = "systems-dl"; document.body.appendChild(dl); }
    dl.innerHTML = state.systems.map((s) => '<option value="' + esc(s) + '">').join("");
    let cdl = document.getElementById("conditions-dl"); if (!cdl) { cdl = document.createElement("datalist"); cdl.id = "conditions-dl"; document.body.appendChild(cdl); }
    cdl.innerHTML = [...new Set([...CONDITION_CODES, ...state.conditions])].map((c) => '<option value="' + esc(c) + '">').join("");
    let ndl = document.getElementById("names-dl"); if (!ndl) { ndl = document.createElement("datalist"); ndl.id = "names-dl"; document.body.appendChild(ndl); }
    ndl.innerHTML = [...new Set(state.items.map((i) => i.name))].slice(0, 400).map((n) => '<option value="' + esc(n) + '">').join("");
    let sdl = document.getElementById("sources-dl"); if (!sdl) { sdl = document.createElement("datalist"); sdl.id = "sources-dl"; document.body.appendChild(sdl); }
    sdl.innerHTML = [...new Set(state.lots.map((l) => clean(l.source)).filter(Boolean))].map((n) => '<option value="' + esc(n) + '">').join("");
    document.querySelectorAll("#tabs .pl-tab").forEach((b) => { const on = b.dataset.tab === state.tab; b.classList.toggle("pl-tab--active", on); b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    const main = $("#main");
    if (state.tab === "next") main.innerHTML = renderNext();
    else if (state.tab === "stock") { main.innerHTML = renderStock(); renderResults(); }
    else if (state.tab === "bins") main.innerHTML = renderBins();
    else main.innerHTML = renderLots();
  }
  function go(tab) { state.tab = tab; render(); $("#main").scrollTop = 0; }
  const qtyTag = (i) => (i.quantity > 1 ? ' <span class="muted">×' + esc(i.quantity) + "</span>" : "");
  const itemButton = (i, subline) => '<button class="irow" data-item="' + esc(i.id) + '"><span class="irow-main"><span class="irow-name">' + esc(i.name) + qtyTag(i) + '</span><span class="irow-sub">' + esc(subline) + "</span></span>" + (isStale(i) ? staleBadge : "") + '<span class="money">' + esc(whole(i.target)) + "</span></button>";
  function sec(title, n, rows, see, extra = "") {
    return '<section class="sec"><div class="sec-head"><h2 class="sec-title">' + esc(title) + ' <span class="muted">' + n + "</span></h2>" + (see || extra ? '<span class="row-acts">' + extra + (see ? '<button class="pl-btn pl-btn--ghost pl-btn--xs" data-see="' + see + '">See all ' + n + "</button>" : "") + "</span>" : "") + '</div><ul class="list">' + rows + "</ul></section>";
  }
  function renderNext() {
    const items = state.items;
    let h = "";
    if (state.toPack.length) {
      const rows = packGroups(state.toPack).map(([bin, sales]) => '<li class="group-label"><span class="pl-eyebrow">' + esc(bin ? binText(bin) : "No bin recorded") + "</span></li>" +
        sales.map((sl) => '<li><button class="irow" data-item="' + esc(sl.item_id) + '"><span class="irow-main"><span class="irow-name">' + esc(sl.item_name) + (sl.quantity > 1 ? ' <span class="muted">×' + esc(sl.quantity) + "</span>" : "") + '</span><span class="irow-sub">' + esc(["sold " + sl.sold_on, sl.channel].filter(Boolean).join(" · ")) + '</span></span></button><button class="pl-btn pl-btn--sm rowact" data-act="packed" data-id="' + esc(sl.id) + '" aria-label="Packed: ' + esc(sl.item_name) + '">Packed</button></li>').join("")).join("");
      h += sec("To pack", state.toPack.length, rows, "");
    }
    if (state.scans.length) {
      const rows = state.scans.slice(0, ROW_CAP).map((s) => '<li><button class="irow" data-act="resumescan" data-id="' + esc(s.id) + '"><span class="irow-main"><span class="irow-name">' + esc(scanTitle(s)) + '</span><span class="irow-sub">' + esc(scanSub(s)) + "</span></span>" + (scanNeedsCheck(s) ? '<span class="pl-badge pl-badge--warning">check</span>' : "") + "</button></li>").join("");
      h += sec("Needs a look", state.scans.length, rows, "");
    }
    const stuck = items.filter((i) => i.status === "available" && state.drafts[i.id] && state.drafts[i.id].copied_stale);
    if (stuck.length) h += sec("Copied, not live yet", stuck.length, stuck.slice(0, ROW_CAP).map((i) => "<li>" + itemButton(i, "copied " + String(state.drafts[i.id].copied_at).slice(0, 10) + " · did it go live?") + "</li>").join(""), "");
    const ready = readyToList(items);
    const undrafted = ready.filter((i) => { const d = state.drafts[i.id]; return !d || d.state === "failed"; }).length;
    const bulk = undrafted ? '<button class="pl-btn pl-btn--sm" data-act="draftbulk">Draft ' + Math.min(undrafted, state.maxBulk) + "</button>" : "";
    if (ready.length) h += sec("Ready to list", ready.length, ready.slice(0, ROW_CAP).map((i) => "<li>" + itemButton(i, [draftLabel(state.drafts[i.id]), i.bin_id || ""].filter(Boolean).join(" · ")) + "</li>").join(""), "ready", bulk);
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
    const chores = [];
    if (state.unbinned) chores.push(state.bins.length
      ? '<li><button class="irow" data-see="nobin"><span class="irow-main">' + state.unbinned + " unsold item" + (state.unbinned === 1 ? " is" : "s are") + ' not in a bin</span><span class="muted">Start binning</span></button></li>'
      : '<li><button class="irow" data-tab="bins"><span class="irow-main">' + state.unbinned + " unsold item" + (state.unbinned === 1 ? " has" : "s have") + ' no location yet</span><span class="muted">Make bins</span></button></li>');
    if (stale) chores.push('<li class="chore"><button class="irow" data-see="stale"><span class="irow-main">' + stale + " price" + (stale === 1 ? " is" : "s are") + " over " + STALE_DAYS + ' days old</span><span class="muted">See them</span></button>' + (composeTarget() ? '<button class="pl-btn pl-btn--sm" data-act="ask-reprice">Ask for a re-price</button>' : "") + "</li>");
    if (chores.length) h += '<section class="sec"><div class="sec-head"><h2 class="sec-title">Housekeeping</h2></div><ul class="list">' + chores.join("") + "</ul></section>";
    return h || '<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">Nothing waiting</div><div class="pl-empty__desc">Scan a box to start.</div><div class="pl-empty__actions"><button class="pl-btn pl-btn--primary" data-act="scan">Scan</button></div></div>';
  }
  function chipsHtml() {
    const f = state.f, chips = [];
    if (f.preset === "ready") chips.push(["preset", "Ready to list"]);
    if (f.preset === "stale") chips.push(["preset", "Stale price"]);
    if (f.preset === "nobin") chips.push(["preset", "Not in a bin"]);
    if (f.bin) chips.push(["bin", f.bin === "-" ? "No bin" : "Bin " + binText(f.bin)]);
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
      '<label class="pl-field"><span class="pl-field__label">Bin</span><select class="pl-field__input" id="f-bin">' + opt("", "Any", f.bin) + opt("-", "Not in a bin", f.bin) + state.bins.map((b) => opt(b.id, binText(b.id), f.bin)).join("") + "</select></label>" +
      "</div></div></div></div>" +
      (state.moveTo ? '<div class="pl-callout pl-callout--info note" role="status">Pick what goes into <span class="bin-id">' + esc(binText(state.moveTo)) + '</span>, then Move. <button class="pl-btn pl-btn--sm pl-btn--ghost" data-act="endmove">Cancel</button></div>' : "") +
      '<div id="chips">' + chipsHtml() + '</div><div id="results"></div><div id="selbar"></div>';
  }
  const checkbox = (id, label, on) => '<label class="hit pl-checkbox"><input type="checkbox" class="pl-checkbox__input" ' + id + (on ? " checked" : "") + '><span class="pl-checkbox__box"></span><span class="sr-only">' + esc(label) + "</span></label>";
  function renderResults() {
    const root = $("#results"); if (!root) return;
    const xs = stockItems(state.items, stockFilter());
    $("#chips").innerHTML = chipsHtml();
    if (!xs.length) { root.innerHTML = '<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">Nothing matches</div><div class="pl-empty__desc">Clear a filter or change the lifecycle.</div></div>'; return renderSelbar(xs); }
    const allOn = xs.every((i) => state.selected.has(i.id));
    const row = (i) => {
      const on = state.selected.has(i.id);
      const sel = checkbox('data-sel="' + esc(i.id) + '"', "Select " + i.name, on);
      if (narrow()) return "<li" + (on ? ' class="pl-tr--selected"' : "") + ">" + sel + itemButton(i, [clean(i.condition), i.bin_id, STATUS_LABEL[i.status] ? STATUS_LABEL[i.status][0] : i.status].filter(Boolean).join(" · ")) + "</li>";
      return '<tr class="pl-tr--interactive trow' + (on ? " pl-tr--selected" : "") + '" data-item="' + esc(i.id) + '" tabindex="0"><td>' + sel + "</td>" +
        "<td>" + esc(i.name) + qtyTag(i) + (clean(i.condition) ? '<span class="sub">' + esc(i.condition) + "</span>" : "") + "</td>" +
        '<td class="bin-id">' + (i.bin_id ? esc(i.bin_id) : '<span class="muted">—</span>') + "</td>" +
        "<td>" + badge(i.status) + (isStale(i) ? " " + staleBadge : "") + '</td><td class="money col-num">' + esc(whole(i.target)) + '</td><td class="col-lot muted">' + esc(i.lot_id ? lotName(i.lot_id) : "—") + "</td></tr>";
    };
    const groups = bySystem(xs);
    const count = xs.length + " item" + (xs.length === 1 ? "" : "s");
    if (narrow()) {
      root.innerHTML = '<div class="count">' + checkbox('id="sel-all"', "Select all visible", allOn) + "<span>" + count + "</span></div>" +
        groups.map(([sys, its]) => '<div class="group"><div class="pl-eyebrow group-label">' + esc(sys) + " · " + its.length + '</div><ul class="list">' + its.map(row).join("") + "</ul></div>").join("");
    } else {
      root.innerHTML = '<table class="pl-table"><thead><tr><th>' + checkbox('id="sel-all"', "Select all visible", allOn) + '</th><th>Item</th><th>Bin</th><th>Status</th><th class="money">Target</th><th class="col-lot">Lot</th></tr></thead>' +
        groups.map(([sys, its]) => '<tbody><tr><th colspan="6" class="pl-eyebrow" scope="colgroup">' + esc(sys) + " · " + its.length + "</th></tr>" + its.map(row).join("") + "</tbody>").join("") + "</table>";
    }
    renderSelbar(xs);
  }
  function renderSelbar(xs) {
    const bar = $("#selbar"); if (!bar) return;
    const n = state.selected.size;
    bar.className = n ? "selbar" : "";
    if (!n) { bar.innerHTML = ""; return; }
    const visible = xs.filter((it) => state.selected.has(it.id)).length;
    const count = '<span class="selbar-count">' + n + " selected" + (visible !== n ? " (" + visible + " visible)" : "") + "</span>";
    if (state.moveTo) { bar.innerHTML = count + '<button class="pl-btn pl-btn--sm pl-btn--primary" data-act="domove">Move ' + n + " to " + esc(state.moveTo) + '</button><button class="pl-btn pl-btn--sm pl-btn--ghost" id="sel-clear">Clear</button>'; return; }
    bar.innerHTML = count +
      '<button class="pl-btn pl-btn--sm pl-btn--primary" id="copy-md" title="System header, then one line per item: Name NoS — $20">Copy list</button>' +
      '<button class="pl-btn pl-btn--sm" data-act="movesel">Move to bin…</button>' +
      '<button class="pl-btn pl-btn--sm pl-btn--ghost" id="sel-clear">Clear</button>' +
      (narrow() ? "" : '<span class="pl-kbd">⌘C</span>');
  }
  async function copySelected() {
    if (!state.selected.size) return;
    return copyItems(state.selected);
  }
  // Copy a for-sale post for these ids (a Set is pruned of ids that no longer exist).
  async function copyItems(ids) {
    // Re-read from the server rather than trusting the cache: an item deleted or re-priced
    // by the agent since the grid loaded must not leak stale into the copied text.
    let live; try { live = (await api("/items?limit=5000")).items; } catch (e) { toast(e.message, "error"); return; }
    const byId = Object.fromEntries(live.map((it) => [it.id, it]));
    const want = new Set(ids);
    const dropped = [...want].filter((id) => !byId[id]);
    for (const id of dropped) { state.selected.delete(id); want.delete(id); }
    const items = live.filter((it) => want.has(it.id));
    if (!items.length) { toast("Nothing left to copy — the selected items no longer exist", "warning"); await refresh(); return; }
    const text = chatDoc(items);
    const note = dropped.length ? " (" + dropped.length + " no longer exist and were skipped)" : "";
    if (await copyText(text)) { toast("Copied " + items.length + " item" + (items.length === 1 ? "" : "s") + " for chat" + note, dropped.length ? "warning" : "success"); if (dropped.length) await refresh(); return; }
    await dialog({ title: "Copy " + items.length + " items", body: '<label class="pl-field span2"><span class="pl-field__label">The clipboard is blocked in this frame — select all and copy</span><textarea class="pl-field__input copytext" id="copytext" readonly>' + esc(text) + "</textarea></label>", onSubmit: null });
  }
  function renderBins() {
    if (!state.bins.length) return '<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">No bins yet</div><div class="pl-empty__desc">Number your boxes or shelves B01, B02 … and record what lives where. ' + (state.unbinned ? state.unbinned + " unsold item" + (state.unbinned === 1 ? " has" : "s have") + " no location yet." : "") + '</div><div class="pl-empty__action"><button class="pl-btn pl-btn--primary" data-act="newbins">Create bins</button></div></div>';
    const rows = state.bins.map((b) => '<li><button class="irow" data-bin="' + esc(b.id) + '"><span class="irow-main"><span class="irow-name"><span class="bin-id">' + esc(b.id) + "</span>" + (b.label ? " · " + esc(b.label) : "") + '</span><span class="irow-sub">' + b.items + " item" + (b.items === 1 ? "" : "s") + '</span></span><span class="money">' + esc(whole(b.value_at_target)) + "</span></button></li>").join("");
    const loose = state.unbinned ? '<ul class="list note"><li><button class="irow" data-see="nobin"><span class="irow-main"><span class="irow-name">' + state.unbinned + " unsold item" + (state.unbinned === 1 ? "" : "s") + ' not in a bin</span><span class="irow-sub">Open them in Stock and move them</span></span></button></li></ul>' : "";
    return '<section class="sec"><div class="sec-head"><h2 class="sec-title">Bins <span class="muted">' + state.bins.length + '</span></h2><span class="row-acts"><button class="pl-btn pl-btn--sm" data-act="printlabels">Print labels</button><button class="pl-btn pl-btn--sm" data-act="newbins">New bins</button></span></div>' + loose + '<ul class="list">' + rows + "</ul></section>";
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

  // ── chat compose (protoAgent plugin-view bridge, protoagent:chat:compose) ───────────────
  // DRAFTS a message in the console's chat composer; the operator reviews it and sends. Never a
  // send. Aimed by composeTarget(): an http(s) console's exact origin, else "*" (see below); no
  // button at all when the page isn't embedded.
  function hostOrigin() {
    try {
      if (window.parent === window) return "";
      const a = location.ancestorOrigins && location.ancestorOrigins.length ? location.ancestorOrigins[0] : "";
      const o = a || (document.referrer ? new URL(document.referrer).origin : "");
      return o && o !== "null" ? o : "";
    } catch (_) { return ""; }
  }
  // The target for a compose message. An http(s) console gets its exact origin. The desktop
  // app's console is tauri://localhost, which a WebView can treat as an opaque origin, and a
  // message aimed at that origin is dropped silently, so there we send to "*" the way the kit's
  // own ready message does. The host still checks the message comes from THIS frame, and the
  // text is item names this view fetched with the console's own token, sent only on a click.
  function composeTarget() {
    if (window.parent === window) return "";
    const o = hostOrigin();
    return /^https?:\/\//.test(o) ? o : "*";
  }
  const COMPOSE_MAX = 4000;  // the bridge takes plain text up to 4 KB
  function repriceText(items) {
    const head = "Please re-price these " + items.length + " item" + (items.length === 1 ? "" : "s") + " from eBay sold comps. Their price evidence is over " + STALE_DAYS + " days old. For each, run ebay_price_check (sold), then inventory_set_price with the basis and the comps; where the sold comps are thin, keep the price and say so.\n";
    let body = "", shown = 0;
    for (const i of items) {
      // Plain text for the chat composer, never HTML: the bridge drafts it as text.
      const line = ["- ", i.name, " (", i.id, ")", i.target != null ? ", now " + whole(i.target) : "", "\n"].join("");
      if ((head + body + line).length > COMPOSE_MAX - 80) break;
      body += line; shown++;
    }
    const more = items.length - shown;
    return head + body + (more ? "…and " + more + " more: use inventory_reprice_plan for the full list.\n" : "");
  }
  function composeToChat(text) {
    const origin = composeTarget(); if (!origin) { toast("Open this view inside the console to draft a chat message.", "warning"); return; }
    window.parent.postMessage({ type: "protoagent:chat:compose", text: String(text).slice(0, COMPOSE_MAX) }, origin);
  }

  async function itemDialog(item) {
    const it = item || { lot_id: state.f.lot, status: "available", quantity: 1 };
    // No status here: state changes only through the drawer's own actions (Record sale, Mark available,
    // Keep, Withdraw, Return to stock) and a draft's "I listed it", each with its own guard.
    await dialog({
      title: item ? "Edit " + item.name : "New item", submit: item ? "Save" : "Add",
      body: field("name", "Name", it.name, { required: true, span2: true }) + field("lot_id", "Lot", it.lot_id, { type: "select", options: lotOptions(it.lot_id) }) +
        field("system", "Game system", it.system || "", { placeholder: "Warhammer 40K, Blood Bowl…", list: "systems-dl", hint: "Optional. Lists and Copy list group by it." }) +
        field("category", "Category", it.category) + field("condition", "Condition", it.condition, { placeholder: "NoS, NIB, Sealed, Assembled, Painted…", list: "conditions-dl", hint: "Printed inline after the name in a copied list." }) +
        field("quantity", "Quantity", it.quantity ?? 1, { type: "number", step: "1" }) + field("model_count", "Model count", it.model_count ?? "", { type: "number", step: "1" }) +
        field("retail", "Retail ($)", it.retail ?? "", { type: "number", step: "0.01", hint: "The anchor price, not a target." }) + field("cost_basis", "Cost basis ($)", it.cost_basis ?? "", { type: "number", step: "0.01" }) +
        field("notes", "Notes", it.notes, { type: "textarea", span2: true }),
      onSubmit: async (v) => {
        if (!v.name.trim()) throw new Error("A name is required");
        const body = { name: v.name, lot_id: v.lot_id, category: v.category, system: v.system, condition: v.condition, quantity: num(v.quantity), model_count: num(v.model_count), retail: num(v.retail), cost_basis: num(v.cost_basis), notes: v.notes };
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
  const binOrder = (id) => { const m = /^B(\d+)$/.exec(id); return m ? [0, +m[1], ""] : [1, 0, id]; };
  const sortBins = (bins) => bins.slice().sort((a, b) => { const x = binOrder(a.id), y = binOrder(b.id); return x[0] - y[0] || x[1] - y[1] || (x[2] < y[2] ? -1 : x[2] > y[2] ? 1 : 0); });
  async function labelsDialog(only) {
    if (!state.bins.length) return toast("Make some bins first. Then print their labels.", "warning");
    let info;
    try { info = await api("/labels/base"); } catch (e) { return toast(e.message, "error"); }
    if (!info.base) return toast(info.problem, "error");
    const ids = sortBins(state.bins).map((b) => b.id);
    const unprinted = state.bins.filter((b) => !b.labels_printed_on).length;
    const where = '<div class="span2 muted">Each label opens ' + esc(info.base + info.path) + "&lt;bin&gt;." +
      (info.phone_reachable ? "" : " That address only works on this computer, so a phone can't open it. Set Bin label address in Settings.") + "</div>";
    const body = only ? '<div class="span2">One label for bin ' + esc(only) + ".</div>" + where :
      field("mode", "Which labels", unprinted ? "unprinted" : "all", { type: "select", options: [["unprinted", "Bins without a printed label (" + unprinted + ")"], ["range", "A range of bins"], ["all", "All bins (" + ids.length + ")"]] }) +
      field("from", "From", ids[0], { type: "select", options: ids, hint: "Only used for a range." }) + field("to", "To", ids[ids.length - 1], { type: "select", options: ids }) + where;
    await dialog({
      title: only ? "Print the label for " + only : "Print bin labels", submit: "Open the sheet", body,
      onSubmit: async (v) => {
        const w = window.open("", "_blank");  // before any await: a new tab needs the click itself
        const req = only ? { ids: [only] } : v.mode === "range" ? { from: v.from, to: v.to } : v.mode === "all" ? { all: true } : { unprinted: true };
        let r;
        try { r = await api("/labels/sheet", "POST", req); } catch (e) { if (w) w.close(); throw e; }
        const n = r.ids.length, what = n + " label" + (n === 1 ? "" : "s");
        if (w) {
          w.document.open(); w.document.write(r.html); w.document.close();
          toast("Opened " + what + " in a new tab. Print from there.", "success"); await refresh(); return true;
        }
        const url = URL.createObjectURL(new Blob([r.html], { type: "text/html" }));
        $("#dlg-result").innerHTML = '<div class="pl-callout pl-callout--warning span2">The browser blocked the new tab. <a href="' + url + '" target="_blank" rel="noopener">Open the sheet of ' + esc(what) + "</a> and print from there.</div>";
        await refresh(); return false;
      },
    });
  }
  async function newBinsDialog() {
    const first = !state.bins.length;
    await dialog({
      title: "New bins", submit: "Create",
      body: field("count", "How many", first ? 6 : 1, { type: "number", step: "1", hint: "Numbered on from the last bin: B01, B02 …" }) +
        field("label", "Label (optional)", "", { placeholder: "Blood Bowl overflow", hint: "Applied to each new bin. Change it later from the bin." }),
      onSubmit: async (v) => {
        const r = await api("/bins", "POST", { count: Number(v.count || 1), label: v.label });
        const ids = r.bins.map((b) => b.id);
        toast("Made " + (ids.length > 1 ? ids[0] + "–" + ids[ids.length - 1] : ids[0]), "success"); await refresh();
      },
    });
  }
  async function editBinDialog(id) {
    const b = binById(id); if (!b) return;
    await dialog({
      title: "Edit bin " + b.id, submit: "Save",
      body: field("label", "Label", b.label, { span2: true, placeholder: "Blood Bowl overflow" }) + field("note", "Note", b.note, { type: "textarea", span2: true }),
      onSubmit: async (v) => { await api("/bins/" + encodeURIComponent(b.id), "PUT", { label: v.label, note: v.note }); toast("Saved", "success"); await refresh(); },
    });
  }
  async function deleteBin(id) {
    if (!(await confirm("Delete bin " + id + "?", "The bin is empty. Its history stays in the activity log.", "Delete"))) return;
    try { await api("/bins/" + encodeURIComponent(id), "DELETE"); state.drawer = null; closeDrawer(); toast("Bin deleted", "success"); } catch (e) { toast(e.message, "error"); }
    await refresh();
  }
  async function moveDialog(ids) {
    ids = [...ids]; if (!ids.length) return;
    const one = ids.length === 1 ? itemById(ids[0]) : null;
    const opts = [["", "— no bin —"]].concat(state.bins.map((b) => [b.id, binText(b.id)])).concat([["__new", "A new bin (next number)"]]);
    const current = one ? one.bin_id : "";
    await dialog({
      title: one ? "Move " + one.name : "Move " + ids.length + " items", submit: "Move",
      body: field("bin", "To bin", current || (state.bins.length ? state.bins[0].id : "__new"), { type: "select", options: opts, span2: true }),
      onSubmit: async (v) => {
        let to = v.bin;
        if (to === "__new") to = (await api("/bins", "POST", { count: 1 })).bins[0].id;
        const r = await api("/bins/move", "POST", { item_ids: ids, bin_id: to });
        toast(r.moved.length ? "Moved " + r.moved.length + (to ? " to " + to : " out of their bins") : "Already there", "success");
        await refresh();
      },
    });
  }
  function startMove(id) {
    state.moveTo = id; state.selected.clear();
    Object.assign(state.f, { seg: "sale", preset: "", system: "", lot: "", bin: "", q: "" });
    closeDrawer(); go("stock");
  }
  function endMove() { state.moveTo = ""; state.selected.clear(); }
  async function doMove() {
    const to = state.moveTo, ids = [...state.selected]; if (!to || !ids.length) return;
    try {
      const r = await api("/bins/move", "POST", { item_ids: ids, bin_id: to });
      toast("Moved " + r.moved.length + " to " + to, "success");
      endMove(); await refresh(); go("bins"); openBin(to);
    } catch (e) { toast(e.message, "error"); }
  }
  async function packed(saleId) {
    try { await api("/sales/" + encodeURIComponent(saleId) + "/packed", "POST"); toast("Packed", "success"); } catch (e) { toast(e.message, "error"); }
    await refresh();
  }
  const stockFilter = () => (state.moveTo ? { ...state.f, exclude: state.moveTo } : state.f);
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

  // ── scan: photo intake (spec §3). The agent identifies in the background; the flow polls. ──
  const POLL_MS = 2500;
  const SC = { id: "", kind: "", step: 1, file: null, fileUrl: "", data: null, units: [], merged: false, boxName: "", boxHow: "", boxSystem: "", boxCondition: "", lotId: "", lastBin: "", bins: {}, pick: false, timer: 0, busy: false, sig: "" };
  const thumbs = new Map();  // photo id → object URL (null while loading)
  function stopPoll() { if (SC.timer) clearTimeout(SC.timer); SC.timer = 0; }
  function scReset() {
    stopPoll();
    if (SC.fileUrl) URL.revokeObjectURL(SC.fileUrl);
    Object.assign(SC, { id: "", kind: "", step: 1, file: null, fileUrl: "", data: null, units: [], merged: false, boxName: "", boxHow: "", boxSystem: "", boxCondition: "", lotId: "", lastBin: "", bins: {}, pick: false, busy: false, sig: "" });
  }
  const scanOpen = () => !!(state.drawer && state.drawer.kind === "scan");
  const scanPending = (s) => !!s && (s.state === "identifying" || (s.units || []).some((u) => u.state === "identifying") || !!(s.pricing && s.pricing.state === "queued"));
  const confident = (p) => !!p && Number(p.confidence) >= state.threshold;
  async function upload(scanId, file) {
    const r = await kit.apiFetch(API + "/scans/" + encodeURIComponent(scanId) + "/photos", { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
    const text = await r.text();
    let data = null; try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    if (!r.ok) throw new Error((data && (data.detail || data.error)) || (r.status + " " + r.statusText));
    return data;
  }
  function photoSrc(scanId, photoId) {
    if (thumbs.has(photoId)) return thumbs.get(photoId) || "";
    thumbs.set(photoId, null);
    kit.apiFetch(API + "/scans/" + encodeURIComponent(scanId) + "/photos/" + encodeURIComponent(photoId))
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => { if (b) { thumbs.set(photoId, URL.createObjectURL(b)); if (scanOpen()) scPaint(); } else thumbs.delete(photoId); })
      .catch(() => thumbs.delete(photoId));
    return "";
  }
  const thumbImg = (s, cls = "thumb") => {
    const p = s && s.photos && s.photos[s.photos.length - 1];
    const src = p ? photoSrc(s.id, p.id) : "";
    return src ? '<img class="' + cls + '" src="' + esc(src) + '" alt="">' : '<span class="' + cls + ' ph" aria-hidden="true"></span>';
  };
  // The breakdown rows: the agent's proposed contents (once), every piece photographed into the
  // box, and anything added by name. Local choices (Keep/Sell, a corrected name) survive polls.
  function mergeUnits() {
    const s = SC.data; if (!s) return;
    if (s.kind === "unit") return;
    const p = s.proposal;
    if (!SC.merged && p && (p.contents || []).length) {
      SC.merged = true;
      for (const [k, c] of p.contents.entries()) SC.units.push({ key: "c" + k, name: c.name, quantity: c.quantity || 1, category: c.category, system: c.system, condition: c.condition, model_count: c.model_count || 0, keep: false, state: "ok" });
    }
    for (const u of s.units || []) {
      let row = SC.units.find((r) => r.scan_id === u.id);
      if (!row) { row = { key: u.id, scan_id: u.id, name: "", quantity: 1, keep: false, state: "identifying" }; SC.units.push(row); }
      row.photo = u;
      if (row.named) { row.state = "ok"; continue; }
      const up = u.proposal;
      if (u.state === "identifying" || u.state === "new") row.state = "identifying";
      else if (u.state === "failed") row.state = "failed";
      else if (up) { row.name = up.name; row.system = up.system; row.category = up.category; row.condition = up.condition; row.alts = up.alternatives || []; row.state = confident(up) ? "ok" : "check"; if (row.state === "ok") absorb(row); }
    }
  }
  // A piece photographed into the box is often one the agent already listed: once its name
  // matches a listed row, the photographed row absorbs it (keeping that row's Keep/Sell and count).
  function absorb(row) {
    const key = clean(row.name).toLowerCase(); if (!key) return;
    const twin = SC.units.find((r) => r !== row && !r.scan_id && clean(r.name).toLowerCase() === key);
    if (!twin) return;
    row.keep = twin.keep; row.quantity = twin.quantity || row.quantity; row.category = row.category || twin.category; row.model_count = row.model_count || twin.model_count;
    SC.units = SC.units.filter((r) => r !== twin);
  }
  async function loadScan() {
    const { scan } = await api("/scans/" + encodeURIComponent(SC.id));
    SC.data = scan; SC.kind = scan.kind; SC.lotId = scan.lot_id || SC.lotId;
    mergeUnits();
  }
  function poll() {
    stopPoll();
    if (!SC.id || !scanOpen() || !scanPending(SC.data)) return;
    SC.timer = setTimeout(async () => {
      SC.timer = 0;
      if (document.hidden || !scanOpen()) return;  // visibilitychange resumes it
      try {
        const before = JSON.stringify(SC.data);
        await loadScan();
        if (SC.data && SC.data.pricing) { const [it, sm] = await Promise.all([api("/items?limit=5000"), api("/summary")]); state.items = it.items; state.summary = sm; }
        if (JSON.stringify(SC.data) !== before || SC.step === 5) scPaint();
        announce();
      } catch (e) { toast(e.message, "error"); }
      poll();
    }, POLL_MS);
  }
  let lastSaid = "";
  function announce() {
    const s = SC.data; if (!s) return;
    const said = s.state === "proposed" && s.proposal ? "Identified: " + s.proposal.name : s.state === "failed" ? "Couldn't identify it" : "";
    if (said && said !== lastSaid) { lastSaid = said; const live = $("#sc-live"); if (live) live.textContent = said; }
  }
  const rc = (name, v, title, blurb, on) => '<label class="pl-radiocard' + (on ? " pl-radiocard--selected" : "") + '"><input class="pl-radiocard__input" type="radio" name="' + esc(name) + '" value="' + esc(v) + '"' + (on ? " checked" : "") + '><span class="pl-radiocard__title">' + esc(title) + "</span>" + (blurb ? '<span class="pl-radiocard__blurb">' + esc(blurb) + "</span>" : "") + "</label>";
  const capBtn = (what, label, primary = true) => '<label class="pl-btn' + (primary ? " pl-btn--primary" : "") + ' block"><input type="file" accept="image/*" capture="environment" class="sr-only" data-cap="' + what + '">' + esc(label) + "</label>";
  const btnP = (act, label, disabled = false) => '<button class="pl-btn pl-btn--primary block" data-act="' + act + '"' + (disabled ? " disabled" : "") + ">" + esc(label) + "</button>";
  const btn = (act, label) => '<button class="pl-btn block" data-act="' + act + '">' + esc(label) + "</button>";
  const discard = () => (SC.id && !(SC.data && SC.data.state === "confirmed") ? '<button class="pl-btn pl-btn--ghost pl-btn--sm" data-act="sc-discard">Discard this scan</button>' : "");
  const lotLine = () => { const l = state.lots.find((x) => x.id === SC.lotId); return l ? '<p class="muted">' + esc(l.name) + (l.acquisition_cost ? " · " + esc(whole(l.acquisition_cost)) + " cost" : "") + "</p>" : ""; };
  const skel = '<div class="pl-skel-lines" aria-hidden="true"><span class="pl-skel skel-line w60"></span><span class="pl-skel skel-line w40"></span></div>';
  const scanItems = () => ((SC.data && SC.data.item_ids) || []).map(itemById).filter(Boolean);
  const unitLabel = (u) => (u.state === "identifying" && !u.name ? "Identifying…" : u.state === "failed" && !u.named ? "Couldn't identify" : u.name + (u.state === "check" ? "?" : ""));
  function unitRow(u) {
    const name = u.state === "identifying" && !u.name ? skel + '<span class="sr-only">Identifying</span>' : '<span class="irow-name">' + esc(unitLabel(u)) + (u.quantity > 1 ? ' <span class="muted">×' + esc(u.quantity) + "</span>" : "") + "</span>";
    const sub = u.state === "identifying" ? "Identifying…" : u.state === "check" ? "Not sure. Tap Check" : u.state === "failed" ? "Type the name or retake" : clean(u.condition) || clean(u.category);
    const fix = u.state === "check" || u.state === "failed" ? '<button class="pl-btn pl-btn--sm tall" data-act="sc-fix" data-id="' + esc(u.key) + '"><span class="pl-badge pl-badge--warning">check</span></button>' : "";
    const seg = '<div class="pl-segmented" role="group" aria-label="Keep or sell ' + esc(u.name || "this piece") + '"><button class="pl-segmented__btn tall' + (u.keep ? " pl-segmented__btn--active" : "") + '" data-act="sc-keep" data-id="' + esc(u.key) + '" aria-pressed="' + u.keep + '">Keep</button><button class="pl-segmented__btn tall' + (!u.keep ? " pl-segmented__btn--active" : "") + '" data-act="sc-sell" data-id="' + esc(u.key) + '" aria-pressed="' + !u.keep + '">Sell</button></div>';
    return '<li class="unit"><div class="ubody">' + (u.photo ? thumbImg(u.photo, "thumb sm") : "") + '<div class="irow-main">' + name + '<span class="irow-sub">' + esc(sub) + "</span></div>" + fix + '</div><div class="uact">' + seg +
      '<button class="pl-btn pl-btn--ghost pl-btn--sm tall" data-act="sc-remove" data-id="' + esc(u.key) + '" aria-label="Remove ' + esc(u.name || "this piece") + '">Remove</button></div></li>';
  }
  const SCREENS = {
    1: () => ['<h3 class="dh">What are you scanning?</h3><div class="pl-radiocard-group rgroup" role="radiogroup" aria-label="What are you scanning">' +
      rc("sc-kind", "box", "New box I bought", "Starts a lot: photo and price, then break it down.", SC.kind === "box") +
      rc("sc-kind", "unit", "Single piece", "One unit, into a lot or none.", SC.kind === "unit") + "</div>" +
      '<p class="muted gap-t">Bin labels open from the iPhone Camera app: point it at a label.</p>', btnP("sc-next", "Continue", !SC.kind)],
    2: () => {
      const box = SC.kind === "box";
      const have = SC.data && SC.data.photos && SC.data.photos.length;
      const shot = SC.fileUrl ? '<img class="thumb" src="' + esc(SC.fileUrl) + '" alt="The photo to identify">' : have ? thumbImg(SC.data) : "";
      let h = '<h3 class="dh">' + (box ? "The box" : "The piece") + "</h3>" + shot + capBtn("sc-photo", shot ? "Retake" : box ? "Take a photo of the box" : "Take a photo of the piece", !shot);
      if (!SC.id) h += '<div class="form one gap-t">' + (box
        ? field("sc_paid", "What did you pay? ($)", "", { required: true, inputmode: "decimal", placeholder: "45.00" }) + field("sc_source", "Where from? (optional)", "", { list: "sources-dl", placeholder: "LGS, eBay lot, GW webstore" })
        : field("sc_lot", "Lot (optional)", SC.lotId, { type: "select", options: lotOptions(SC.lotId) })) + "</div>";
      else h += lotLine();
      return [h, btnP("sc-identify", "Identify") + discard()];
    },
    3: () => {
      const s = SC.data, p = s && s.proposal, box = SC.kind === "box";
      let h = lotLine() + thumbImg(s);
      let foot = "";
      if (!s || s.state === "identifying" || s.state === "new") {
        h += skel + '<p class="muted">Identifying… usually under a minute.' + (box ? " You can break it down meanwhile." : " You can close this; it finishes in the background.") + "</p>";
        foot = box ? btnP("sc-break", "Break it down") : btn("close", "Close");
      } else if (s.state === "failed") {
        h += '<div class="pl-callout pl-callout--error"><div class="pl-callout__body">' + esc(s.reason || "Couldn't identify it.") + '</div></div><div class="form one gap-t">' + field("sc_name", "Name", "", { list: "names-dl", placeholder: "Type what it is" }) + "</div>";
        foot = btnP("sc-typed", "Use this name") + '<div class="frow">' + capBtn("sc-photo", "Retake photo", false) + btn("sc-retry", "Retry") + "</div>";
      } else if (s.state === "proposed" && confident(p) && !SC.pick) {
        h += '<h3 class="dh big">' + esc(p.name) + "</h3>" + (p.system ? '<span class="pl-tag">' + esc(p.system) + "</span>" : "") +
          '<p class="muted">' + esc(p.notes || "Confident match") + "</p>" + (box && (p.contents || []).length ? '<p class="muted">' + esc(p.contents.length) + " pieces proposed</p>" : "");
        foot = btnP("sc-accept", "Looks right") + btn("sc-notit", "Not it");
      } else {
        const names = [p.name, ...(p.alternatives || []).map((a) => a.name)].filter(Boolean);
        h += '<div class="pl-callout pl-callout--warning"><div class="pl-callout__body">Not sure. Pick one or type it.</div></div><div class="pl-radiocard-group rgroup gap-t" role="radiogroup" aria-label="Which is it">' +
          names.map((n, k) => rc("sc-alt", n, n, k === 0 ? "The agent's best guess" : "", false)).join("") + '</div><div class="form one gap-t">' + field("sc_name", "Or type it", "", { list: "names-dl" }) + "</div>";
        foot = btnP("sc-pick", "Confirm") + capBtn("sc-photo", "Retake photo", false);
      }
      return [h + '<div class="sr-only" id="sc-live" aria-live="polite"></div>', foot + discard()];
    },
    4: () => {
      const box = SC.kind === "box";
      const sell = SC.units.filter((u) => !u.keep).length, kept = SC.units.length - sell;
      let h = '<h3 class="dh">' + (box ? "Keep this, sell that" : "Keep or sell?") + "</h3>" + (box && SC.boxName ? '<p class="muted">' + esc(SC.boxName) + "</p>" : lotLine()) +
        (box && !SC.boxName ? '<p class="muted">The box is still being named. You can confirm its name at the end.</p>' : "");
      h += SC.units.length ? '<ul class="list gap-t">' + SC.units.map(unitRow).join("") + "</ul>" : '<p class="muted gap-t">' + (SC.data && SC.data.state === "identifying" ? "Waiting for the agent's list of pieces. Or photograph them yourself." : "Add each piece you split out of the box.") + "</p>";
      if (box) h += '<div class="drow gap-t">' + capBtn("sc-piece", "+ Photo of a piece", false) + '<button class="pl-btn block" data-act="sc-addname">+ Add by name</button></div>';
      h += '<p class="sc-sum"><strong>' + sell + " to sell · " + kept + " kept</strong></p>";
      return [h, btnP("sc-confirm", sell ? "Price the sell pile" : "Save") + discard()];
    },
    5: () => {
      const pr = (SC.data && SC.data.pricing) || {};
      const unpriced = new Map((pr.unpriced || []).map((u) => [u.item_id, u.reason]));
      const items = scanItems().filter((i) => UNSOLD.includes(i.status));
      const l = state.summary && state.summary.lots.find((x) => x.id === SC.lotId);
      let h = l ? stats([[fmt(l.acquisition_cost), "Lot cost"], [fmt(l.remaining.target), "At target"], [signed(l.projected_net_at_target), "Projected net", "", l.projected_net_at_target < 0]], 3) : "";
      if (pr.state === "failed") h += '<div class="pl-callout pl-callout--warning gap-t"><div class="pl-callout__body">' + esc(pr.reason || "Pricing didn't finish.") + '</div></div><button class="pl-btn pl-btn--sm gap-t" data-act="sc-reprice">Retry pricing</button>';
      if (pr.state === "queued") h += '<p class="muted gap-t" aria-live="polite">Checking eBay sold comps. This can take a few minutes; you can bin the pieces meanwhile.</p>';
      h += '<ul class="list gap-t">' + items.map((i) => {
        const sub = i.target != null ? clean(i.price_basis) : pr.state === "queued" ? "Pricing…" : unpriced.has(i.id) ? "No comps yet · " + unpriced.get(i.id) : "No comps yet";
        const right = i.target != null ? '<button class="pl-btn pl-btn--ghost pl-btn--sm money" data-act="price" data-id="' + esc(i.id) + '">' + esc(whole(i.target)) + "</button>" : pr.state === "queued" ? "" : '<button class="pl-btn pl-btn--sm" data-act="price" data-id="' + esc(i.id) + '">Set price</button>';
        return '<li class="unit"><div class="irow-main"><span class="irow-name">' + esc(i.name) + '</span><span class="irow-sub">' + esc(sub) + "</span></div>" + right + "</li>";
      }).join("") + '</ul><p class="muted">Tap a price to change it. A price always keeps its basis.</p>';
      return [h, btnP("sc-tobins", "Bin them")];
    },
    6: () => {
      const items = scanItems();
      if (!state.bins.length) return ['<div class="pl-empty pl-empty--slotted"><div class="pl-empty__title">No bins yet</div><div class="pl-empty__desc">Make a few, then put each piece in one.</div></div>', btnP("newbins", "New bins") + btn("sc-done", "Finish without bins")];
      const sel = (i) => {
        const sellable = UNSOLD.includes(i.status);
        const cur = SC.bins[i.id] !== undefined ? SC.bins[i.id] : i.bin_id || (sellable ? SC.lastBin : "");
        return '<select class="pl-select binsel" data-binsel="' + esc(i.id) + '" data-sell="' + (sellable ? 1 : 0) + '" aria-label="Bin for ' + esc(i.name) + '"><option value="">' + (sellable ? "Pick a bin" : "Not binned") + "</option>" +
          sortBins(state.bins).map((b) => '<option value="' + esc(b.id) + '"' + (cur === b.id ? " selected" : "") + ">" + esc(binText(b.id)) + "</option>").join("") + "</select>";
      };
      const row = (i) => '<li class="unit"><div class="irow-main"><span class="irow-name">' + esc(i.name) + '</span><span class="irow-sub">' + (UNSOLD.includes(i.status) ? "to sell" : "kept") + "</span></div>" + sel(i) + "</li>";
      const sells = items.filter((i) => UNSOLD.includes(i.status)), kept = items.filter((i) => !UNSOLD.includes(i.status));
      return ['<h3 class="dh">Put each piece in a bin</h3><p class="muted">After the first, each piece defaults to the last bin you picked.</p>' +
        (sells.length ? '<ul class="list">' + sells.map(row).join("") + "</ul>" : "") + (kept.length ? '<div class="pl-eyebrow gap-t">Kept (optional)</div><ul class="list">' + kept.map(row).join("") + "</ul>" : ""), btnP("sc-done", "Done") + (sells.length ? btn("sc-toprices", "Back to prices") : "")];
    },
  };
  function scPaint() {
    if (!scanOpen()) return;
    const keep = {}; document.querySelectorAll("#scan-flow input[id^='f_sc_'], #scan-flow select[id^='f_sc_']").forEach((el) => (keep[el.id] = el.value));
    const radio = document.querySelector("#scan-flow input[name='sc-alt']:checked");
    const focus = document.activeElement && document.activeElement.id;
    const [body, foot] = SCREENS[SC.step]();
    paintDrawer("Scan", '<div id="scan-flow"><div class="pl-eyebrow step-k">Step ' + SC.step + " of 6</div>" + body + "</div>", '<div class="foot sc-foot">' + foot + "</div>");
    for (const [id, v] of Object.entries(keep)) { const el = document.getElementById(id); if (el) el.value = v; }
    if (radio) { const r = document.querySelector("#scan-flow input[name='sc-alt'][value='" + CSS.escape(radio.value) + "']"); if (r) r.checked = true; }
    if (focus) { const el = document.getElementById(focus); if (el && el.closest("#overlay-root")) el.focus(); }
  }
  function openScanFlow() { ++drawerSeq; state.drawer = { kind: "scan" }; scPaint(); poll(); }
  function startScan() { scReset(); lastSaid = ""; openScanFlow(); }
  function stepFor(s) {
    const p = s.proposal;
    if (s.state === "new") return 2;
    if (s.state === "confirmed") { const sell = scanItems().filter((i) => UNSOLD.includes(i.status)); return sell.length && ((s.pricing && s.pricing.state === "queued") || sell.some((i) => i.target == null)) ? 5 : 6; }
    const sure = s.kind === "box" && s.state === "proposed" && confident(p);
    if (sure) { SC.boxName = p.name; SC.boxHow = "accepted"; SC.boxSystem = p.system; SC.boxCondition = p.condition; }
    return s.kind === "box" && (sure || (s.units || []).length) ? 4 : 3;
  }
  async function resumeScan(id) {
    scReset(); lastSaid = "";
    try { SC.id = id; await loadScan(); SC.step = stepFor(SC.data); openScanFlow(); } catch (e) { scReset(); toast(e.message, "error"); }
  }
  function named(name, how) {
    const p = SC.data && SC.data.proposal;
    SC.boxName = name; SC.boxHow = how; SC.boxSystem = (p && p.system) || ""; SC.boxCondition = (p && p.condition) || "";
    if (SC.kind === "unit") SC.units = [{ key: "self", name, quantity: 1, keep: false, state: "ok", system: SC.boxSystem, condition: SC.boxCondition, category: (p && p.category) || "", named: true }];
    SC.step = 4; SC.pick = false; scPaint(); poll();
  }
  const unitByKey = (k) => SC.units.find((u) => u.key === k);
  async function fixUnit(key) {
    const u = unitByKey(key); if (!u) return;
    const names = [u.state === "check" ? u.name : "", ...(u.alts || []).map((a) => a.name)].filter(Boolean);
    await dialog({
      title: u.state === "failed" ? "Couldn't identify this piece" : "Not sure. Pick one or type it", submit: "Use this",
      body: (names.length ? '<div class="pl-radiocard-group rgroup span2" role="radiogroup" aria-label="Which is it">' + names.map((n, k) => rc("alt", n, n, k === 0 ? "The agent's best guess" : "", false)).join("") + "</div>" : "") +
        field("typed", names.length ? "Or type it" : "Name", "", { span2: true, list: "names-dl" }),
      onSubmit: async (v) => { const n = clean(v.typed || v.alt); if (!n) throw new Error("Pick one or type the name"); u.name = n; u.named = true; u.state = "ok"; absorb(u); scPaint(); },
    });
  }
  async function confirmScan() {
    if (SC.busy) return;
    if (!SC.units.length) return toast("Add at least one piece. The box itself counts if you sell it whole", "warning");
    if (SC.units.some((u) => !u.named && (u.state === "identifying" || u.state === "failed" || u.state === "check"))) return toast("Some pieces still need a name: wait for them, or tap Check", "warning");
    let name = SC.kind === "box" ? SC.boxName : SC.units[0].name;
    if (SC.kind === "box" && !name) {
      const p = SC.data && SC.data.proposal;
      const ok = await dialog({ title: "What is the box?", submit: "Use this name", body: field("n", "Name", (p && p.name) || "", { required: true, span2: true, list: "names-dl" }),
        onSubmit: async (v) => { if (!clean(v.n)) throw new Error("Type the box's name"); name = clean(v.n); SC.boxName = name; SC.boxHow = p && p.name === name ? "accepted" : "typed"; } });
      if (!ok || !name) return;
    }
    const units = SC.units.map((u) => ({ name: u.name, quantity: u.quantity || 1, keep: !!u.keep, scan_id: u.scan_id || "", category: u.category || "", system: u.system || "", condition: u.condition || "", model_count: u.model_count || 0 }));
    const body = SC.kind === "box" ? { name, how: SC.boxHow, system: SC.boxSystem, condition: SC.boxCondition, units } : { ...units[0], name, how: SC.boxHow, lot_id: SC.lotId };
    SC.busy = true;
    try {
      SC.data = (await api("/scans/" + encodeURIComponent(SC.id) + "/confirm", "POST", body)).scan;
      await loadCore();
      if (units.some((u) => !u.keep)) { SC.data = (await api("/scans/" + encodeURIComponent(SC.id) + "/price", "POST")).scan; SC.step = 5; } else SC.step = 6;
    } catch (e) { toast(e.message, "error"); } finally { SC.busy = false; }
    scPaint(); poll();
  }
  async function finishScan() {
    const placements = {};
    document.querySelectorAll("#scan-flow [data-binsel]").forEach((el) => { if (el.value) placements[el.dataset.binsel] = el.value; });
    try {
      const { scan } = await api("/scans/" + encodeURIComponent(SC.id) + "/finish", "POST", { placements });
      const items = scan.item_ids.map(itemById).filter(Boolean), sold = items.filter((i) => UNSOLD.includes(i.status)).length, kept = items.length - sold;
      const binned = Object.keys(placements).length, binsUsed = [...new Set(Object.values(placements))];
      toast(clean(lotName(SC.lotId) || SC.boxName) + ": " + binned + " binned" + (binsUsed.length === 1 ? " in " + binsUsed[0] : "") + (kept ? ", " + kept + " kept" : ""), "success");
      scReset(); closeDrawer(); await refresh();
    } catch (e) { toast(e.message, "error"); }
  }
  async function scanPhoto(file) {
    if (!file) return;
    if (!SC.id) { if (SC.fileUrl) URL.revokeObjectURL(SC.fileUrl); SC.file = file; SC.fileUrl = URL.createObjectURL(file); return scPaint(); }
    try { SC.data = (await upload(SC.id, file)).scan; mergeUnits(); if (SC.step === 2) SC.step = 3; SC.pick = false; } catch (e) { toast(e.message, "error"); }
    scPaint(); poll();
  }
  async function identify() {
    if (SC.busy) return;
    if (SC.id) return SC.data && SC.data.photos.length ? (SC.step = 3, scPaint(), poll()) : toast("Take the photo first", "warning");
    if (!SC.file) return toast("Take the photo first", "warning");
    const paid = SC.kind === "box" ? ($("#f_sc_paid") || {}).value || "" : "";
    if (SC.kind === "box" && !(Number(String(paid).replace(/[$,\s]/g, "")) > 0)) return toast("Say what you paid. It becomes the lot cost", "warning");
    const d = new Date(), stamp = (d.getMonth() + 1 + "").padStart(2, "0") + "-" + (d.getDate() + "").padStart(2, "0") + " " + (d.getHours() + "").padStart(2, "0") + ":" + (d.getMinutes() + "").padStart(2, "0");
    SC.busy = true;
    try {
      const body = SC.kind === "box" ? { kind: "box", paid, source: ($("#f_sc_source") || {}).value || "", name: "Box scanned " + stamp } : { kind: "unit", lot_id: ($("#f_sc_lot") || {}).value || "" };
      const { scan } = await api("/scans", "POST", body);
      SC.id = scan.id; SC.data = scan; SC.lotId = scan.lot_id;
      SC.data = (await upload(SC.id, SC.file)).scan;
      SC.step = 3;
      loadCore().then(() => scanOpen() && scPaint()).catch(() => {});
    } catch (e) { toast(e.message, "error"); } finally { SC.busy = false; }
    scPaint(); poll();
  }
  async function addPiece(file) {
    if (!file) return;
    try {
      const { scan } = await api("/scans", "POST", { kind: "unit", parent_id: SC.id });
      await upload(scan.id, file);
      await loadScan();
    } catch (e) { toast(e.message, "error"); }
    scPaint(); poll();
  }
  async function discardScan() {
    if (!(await confirm("Discard this scan?", "Its photos go, and so does the box's lot if nothing else is in it. Nothing you already confirmed is touched.", "Discard"))) return;
    try { await api("/scans/" + encodeURIComponent(SC.id), "DELETE"); scReset(); closeDrawer(); toast("Scan discarded", "success"); } catch (e) { toast(e.message, "error"); }
    await refresh();
  }
  const SCAN_ACTS = {
    scan: () => startScan(),
    resumescan: (id) => resumeScan(id),
    "sc-next": () => { if (!SC.kind) return; SC.step = 2; scPaint(); },
    "sc-identify": () => identify(),
    "sc-break": () => { SC.step = 4; scPaint(); poll(); },
    "sc-accept": () => named(SC.data.proposal.name, "accepted"),
    "sc-notit": () => { SC.pick = true; scPaint(); },
    "sc-pick": () => {
      const typed = clean(($("#f_sc_name") || {}).value), r = document.querySelector("#scan-flow input[name='sc-alt']:checked");
      const n = typed || (r && r.value); if (!n) return toast("Pick one or type the name", "warning");
      named(n, typed ? "typed" : n === (SC.data.proposal || {}).name ? "accepted" : "alternative");
    },
    "sc-typed": () => { const n = clean(($("#f_sc_name") || {}).value); if (!n) return toast("Type the name first", "warning"); named(n, "typed"); },
    "sc-retry": async () => { try { SC.data = (await api("/scans/" + encodeURIComponent(SC.id) + "/retry", "POST")).scan; } catch (e) { toast(e.message, "error"); } scPaint(); poll(); },
    "sc-keep": (k) => { const u = unitByKey(k); if (u) { u.keep = true; scPaint(); } },
    "sc-sell": (k) => { const u = unitByKey(k); if (u) { u.keep = false; scPaint(); } },
    "sc-remove": (k) => { SC.units = SC.units.filter((u) => u.key !== k); scPaint(); },
    "sc-fix": (k) => fixUnit(k),
    "sc-addname": async () => {
      await dialog({ title: "Add a piece", submit: "Add", body: field("n", "Name", "", { required: true, span2: true, list: "names-dl" }) + field("q", "Quantity", 1, { type: "number", step: "1" }),
        onSubmit: async (v) => { const n = clean(v.n); if (!n) throw new Error("Type the piece's name"); SC.units.push({ key: "n" + Date.now(), name: n, quantity: Math.max(1, Number(v.q) || 1), keep: false, state: "ok", named: true }); scPaint(); } });
    },
    "sc-confirm": () => confirmScan(),
    "sc-reprice": async () => { try { SC.data = (await api("/scans/" + encodeURIComponent(SC.id) + "/price", "POST")).scan; } catch (e) { toast(e.message, "error"); } scPaint(); poll(); },
    "sc-tobins": () => { SC.step = 6; scPaint(); },
    "sc-toprices": () => { SC.step = 5; scPaint(); poll(); },
    "sc-done": () => finishScan(),
    "sc-discard": () => discardScan(),
  };
  // Next ▸ Needs a look: every scan not finished, newest first.
  function scanTitle(s) { const p = s.proposal; return (p && p.name) || (s.kind === "box" ? lotName(s.lot_id) : "A piece"); }
  function scanSub(s) {
    if (s.state === "new") return "No photo yet";
    if (s.state === "identifying") return "Identifying…";
    if (s.state === "failed") return "Couldn't identify it. Type the name or retry";
    if (s.state === "proposed") return confident(s.proposal) ? (s.kind === "box" ? "Identified. Break it down" : "Identified. Confirm it") : "Not sure. Check it";
    if (s.pricing && s.pricing.state === "queued") return "Pricing…";
    return "Bin the pieces";
  }
  const scanNeedsCheck = (s) => s.state === "failed" || (s.state === "proposed" && !confident(s.proposal));

  // ── wiring ───────────────────────────────────────────────────────────────────
  const MENU = [["Add item by hand", () => itemDialog(null)], ["New lot", () => lotDialog(null)], ["New bins", () => newBinsDialog()], ["Print bin labels", () => labelsDialog()], ["Import CSV", importDialog], ["Export CSV", exportCsv], ["History", openHistory]];
  const ACTS = {
    close: closeDrawer,
    price: (id) => priceDialog(id), sold: (id) => soldDialog(id), list: (id) => listingDialog(id),
    edit: async (id) => itemDialog((await api("/items/" + encodeURIComponent(id))).item),
    del: deleteItem, keep: (id) => setStatus(id, "kept"), withdraw: (id) => setStatus(id, "withdrawn"),
    available: (id) => setStatus(id, "available"), return: (id) => setStatus(id, "available"),
    editlot: (id) => lotDialog(state.lots.find((l) => l.id === id)), dellot: deleteLot,
    newbins: () => newBinsDialog(), printlabels: () => labelsDialog(), printlabel: (id) => labelsDialog(id), editbin: (id) => editBinDialog(id), delbin: deleteBin,
    movebin: (id) => moveDialog([id]), movesel: () => moveDialog(state.selected),
    movehere: (id) => startMove(id), endmove: () => { endMove(); render(); }, domove: () => doMove(),
    copybin: (id) => copyItems(state.items.filter((i) => i.bin_id === id && forSale(i)).map((i) => i.id)),
    binstock: (id) => { endMove(); Object.assign(state.f, { seg: "all", preset: "", system: "", lot: "", q: "", bin: id }); closeDrawer(); go("stock"); },
    packed: (id) => packed(id),
    "ask-reprice": () => composeToChat(repriceText(state.items.filter((i) => isStale(i)).sort((a, b) => daysOld(b.price_updated_on) - daysOld(a.price_updated_on)))),
    draft: (id) => requestDraft(id), redraft: (id) => requestDraft(id), dlive: (id) => liveDialog(id), dphotos: (id) => downloadPhotos(id), draftbulk: () => bulkDraft(),
  };
  Object.assign(ACTS, SCAN_ACTS);
  function seeAll(what) {
    if (what === "split") Object.assign(state.f, { seg: "split", preset: "" });
    else if (what === "nobin") Object.assign(state.f, { preset: "nobin", bin: "" });
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
      const t = e.target.closest("[data-tab],[data-item],[data-lot],[data-bin],[data-see],[data-seg],[data-unchip],[data-act],[data-menu]");
      if (!t) return;
      const d = t.dataset;
      if (d.tab) { closeDrawer(); return go(d.tab); }
      if (d.see) return seeAll(d.see);
      if (d.item) return openItem(d.item);
      if (d.lot) return openLot(d.lot);
      if (d.bin) return openBin(d.bin);
      if (d.seg) { Object.assign(state.f, { seg: d.seg, preset: "" }); return render(); }
      if (d.unchip) { state.f[d.unchip] = ""; return render(); }
      if (d.menu) { setPop("#more-menu", "#more", false); return MENU[+d.menu][1](); }
      if (d.act === "dcopy") return copyDraft(t);
      if (d.act) return ACTS[d.act](d.id);
    });
    document.addEventListener("change", (e) => {
      const t = e.target;
      if (t.dataset.sel) { if (t.checked) state.selected.add(t.dataset.sel); else state.selected.delete(t.dataset.sel); return renderResults(); }
      if (t.id === "sel-all") { for (const it of stockItems(state.items, stockFilter())) { if (t.checked) state.selected.add(it.id); else state.selected.delete(it.id); } return renderResults(); }
      if (t.id === "f-system") { state.f.system = t.value; return renderResults(); }
      if (t.id === "f-lot") { state.f.lot = t.value; return renderResults(); }
      if (t.id === "f-bin") { state.f.bin = t.value; return renderResults(); }
      if (t.dataset.cap) { const f = t.files && t.files[0]; t.value = ""; return t.dataset.cap === "sc-piece" ? addPiece(f) : scanPhoto(f); }
      if (t.name === "sc-kind") { SC.kind = t.value; return scPaint(); }
      if (t.name === "sc-alt" || t.name === "alt") { t.closest(".pl-radiocard-group").querySelectorAll(".pl-radiocard").forEach((c) => c.classList.toggle("pl-radiocard--selected", c.contains(t))); return; }
      if (t.dataset.binsel) { SC.bins[t.dataset.binsel] = t.value; if (t.value && t.dataset.sell === "1") SC.lastBin = t.value; return scPaint(); }
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
    document.addEventListener("visibilitychange", () => { if (!document.hidden && scanOpen()) poll(); });
    document.addEventListener("error", (e) => {
      const img = e.target;
      if (!(img && img.matches && img.matches("#scan-flow img.thumb"))) return;
      const ph = document.createElement("span"); ph.className = img.className + " ph"; ph.textContent = "Photo ready";
      img.replaceWith(ph);
    }, true);
  }

  // Boot once, on whichever fires first: the kit's protoagent:init handshake (bearer + theme) or a short timer.
  let booted = false;
  function boot() { if (booted) return; booted = true; refresh(); }
  if (kit) { wire(); kit.initPluginView(boot); setTimeout(boot, 800); }
</script>
</body></html>
"""


def build_view_router(cfg: dict):
    """The PUBLIC router — mounted at ``/plugins/inventory``: the page, and the bin-label hop."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    r = APIRouter()

    @r.get(VIEW_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def _view():
        return HTMLResponse(PAGE)

    # The one other public route: the address printed on a bin label. It takes nothing but a
    # bin id, reads no data and echoes only that id, then hands off to the console (or says
    # the console needs an update). no-store, so a label starts opening its bin the day the
    # host learns how, with nothing cached in between.
    @r.get(LABEL_PREFIX + "/{bin_id}", include_in_schema=False)
    async def _label(bin_id: str):
        from fastapi.responses import PlainTextResponse, RedirectResponse

        bid = norm_bin_id(bin_id)
        if bid is None:
            return PlainTextResponse("Not a bin label.", status_code=404, headers={"Cache-Control": "no-store"})
        target = view_target(bid)
        if target:
            return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})
        return HTMLResponse(older_host_page(bid, console_root()), headers={"Cache-Control": "no-store"})

    return r
