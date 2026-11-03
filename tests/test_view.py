"""The console view: served at the path the manifest declares, on the public prefix, and
four-rules-compliant (the artifact-plugin lesson: test the ACTUAL registered path)."""

from __future__ import annotations

import re
from pathlib import Path

import inventory_plugin
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


_HARNESS = (
    "globalThis.BASE = ''; globalThis.location = { pathname: '/plugins/inventory/view' };\n"
    "const el = () => new Proxy({}, { get: (t, k) => (k === 'addEventListener' || k === 'removeEventListener' || k === 'appendChild' || k === 'remove' || k === 'focus' ? () => {} : k === 'querySelectorAll' ? () => [] : k === 'querySelector' || k === 'closest' ? () => el() : k === 'classList' ? { toggle() {}, add() {}, remove() {} } : k === 'dataset' ? {} : k === 'hidden' ? true : ''), set: () => true });\n"
    "globalThis.document = { querySelector: () => el(), querySelectorAll: () => [], getElementById: () => el(), createElement: () => el(), addEventListener() {}, removeEventListener() {}, body: el() };\n"
    "globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => 0; globalThis.fetch = async () => ({ ok: false, status: 0, statusText: 'stub', text: async () => '' });\n"
)


def _run_page(probe: str) -> dict:
    """The real module under node with a stub DOM, then ``probe`` (which prints one JSON line)."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    module = re.search(r'<script type="module">(.*?)</script>', PAGE, re.S).group(1)
    r = subprocess.run(
        [node, "--input-type=module"], input=_HARNESS + module + probe, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr[:1200]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _app(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app)


def test_the_declared_view_path_is_served_publicly_and_not_under_api(registry):
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    (view,) = manifest["views"]
    assert view["path"].startswith("/plugins/inventory/")
    c = _app(registry)
    r = c.get(view["path"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>Inventory</title>" in r.text
    assert c.get("/api" + view["path"]).status_code == 404  # the page is not a data route


def test_the_page_follows_the_four_rules():
    """Tight enough that a page with a hand-rolled theme map, a message sniffer, a hardcoded
    host or an un-kitted fetch fails — the earlier presence-only version let all four through."""
    from inventory_plugin.view import PAGE, VIEW_PATH, build_view_router

    assert 'location.pathname.split("/plugins/")[0]' in PAGE  # rule 3: slug-aware base
    assert 'BASE+"/_ds/plugin-kit.css"' in PAGE and 'import(BASE + "/_ds/plugin-kit.js")' in PAGE  # rule 4
    assert 'const API = "/api/plugins/inventory"' in PAGE and "kit.initPluginView(boot)" in PAGE
    # rule 2: EVERY data call is kit.apiFetch(API + …), and there is no bare fetch( at all:
    # the old tokenless fallback only produced confusing 401s (protoAgent #2392)
    assert not re.search(r"kit\.apiFetch\((?!API \+)", PAGE)
    assert not re.findall(r"(?<![\w.])fetch\(", PAGE)
    style = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)
    assert not re.search(r"--pl-[\w-]+\s*:", style)  # tokens are consumed, never defined
    assert not re.search(r"\bonmessage\b|addEventListener\(\s*[\"']message", PAGE)  # the kit owns the handshake
    assert not re.search(r"(fetch|import)\(\s*[\"']https?://|(src|href)=[\"']https?://", PAGE)  # nothing hardcoded
    # the public router serves exactly the page, at exactly the manifest path, plus ONE more
    # GET: the bin-label hop, which takes only a bin id and reads no data (tests/test_labels.py)
    router = build_view_router({})
    assert [(r.path, set(r.methods)) for r in router.routes] == [(VIEW_PATH, {"GET"}), ("/b/{bin_id}", {"GET"})]
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert manifest["views"][0]["path"] == "/plugins/inventory" + VIEW_PATH


def test_every_text_the_page_renders_from_data_is_escaped():
    """A grep-level guard: data fields are only ever interpolated through esc()/fmt()."""
    from inventory_plugin.view import PAGE

    body = PAGE.split("// ── render", 1)[1]
    for raw in ("it.name +", "i.name +", "it.notes +", "it.price_basis +", "l.name +", "a.changes +", "s.channel +"):
        assert raw not in body, f"unescaped interpolation: {raw}"
    drawers = PAGE.split("// ── drawer", 1)[1].split("// ── loads", 1)[0]
    for raw in ("it.notes +", "it.price_basis +", "it.system +", "it.condition +", "l.notes +"):
        assert raw not in drawers, f"unescaped interpolation: {raw}"


def test_the_module_script_parses(tmp_path):
    """A single bad quote in a template string kills the whole module before any wiring
    runs — the page renders its chrome and nothing else, with no error banner. Node's
    parser is the only honest check; skipped where node is not installed."""
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    module = re.search(r'<script type="module">(.*?)</script>', PAGE, re.S).group(1)
    head = re.search(r"<script>(.*?)</script>", PAGE, re.S).group(1)
    for label, src, args in (("module", module, ["--input-type=module", "--check"]), ("head", head, ["--check", "-"])):
        r = subprocess.run([node, *args], input=src, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"{label} script does not parse:\n{r.stderr[:800]}"


def test_the_pure_helpers_under_node():
    """Run the real module under node with a stub DOM: money formatting (signs, nulls), the
    escaper, and the field builder against a hostile value. Skipped where node is absent."""
    probe = (
        "\nconsole.log(JSON.stringify({ neg: fmt(-12.5), pos: fmt(1234.5), nul: fmt(null), zero: fmt(0), sneg: signed(-3), spos: signed(3), snul: signed(null),"
        " esc: esc('<a href=\"x\">&\\'</a>'), field: field('name', 'Name', '\"><img src=x onerror=alert(1)>'), opt: field('lot_id', 'Lot', '', { type: 'select', options: [['\"><b>', 'x</option><script>']] }),"
        " doc: chatDoc([{ name: 'Wartrakk', condition: 'NoS', target: 20, system: 'Warhammer 40K', status: 'available' }, { name: 'Land Speeder', condition: 'nos', target: 40, system: 'Warhammer 40K', status: 'available' },"
        " { name: 'Intercessor Squad', condition: 'NoS', target: 30, system: 'Warhammer 40K', status: 'pending' }, { name: 'Helsmiths of Hashut Army Box', condition: 'NIB', target: 170, system: 'Warhammer AoS', status: 'available' },"
        " { name: 'Human  Team', condition: '', target: 57.69, system: 'Blood Bowl', status: 'listed' }, { name: 'Dice', condition: 'Sealed', target: null, system: 'Blood Bowl', status: 'available' }, { name: 'Loose bits', condition: '', target: 5, system: '', status: 'available' }]),"
        " price: [chatPrice(20), chatPrice(57.69), chatPrice(1234.5), chatPrice(null), chatPrice(0)] }));\n"
    )
    out = _run_page(probe)
    assert out["neg"] == "-$12.50" and out["pos"] == "$1,234.50" and out["nul"] == "—" and out["zero"] == "$0.00"
    assert out["sneg"] == "-$3.00" and out["spos"] == "+$3.00" and out["snul"] == "—"
    assert out["esc"] == "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;"
    assert "<img" not in out["field"] and "&quot;&gt;&lt;img" in out["field"]
    assert "<b>" not in out["opt"] and "<script>" not in out["opt"]


def test_copy_list_is_byte_identical_to_0_4_2():
    """Josh's hand-edited post is the spec. GOLDEN is the real output of chatDoc at bb48b3a
    (0.4.2) for this selection: names and conditions with stray whitespace, cents kept only
    when they matter, a blank system last as "Other"."""
    items = (
        "[{ name: 'Wartrakk', condition: 'NoS', target: 20, system: 'Warhammer 40K' },"
        " { name: 'Land  Speeder\\nMk II', condition: 'nos ', target: 40, system: ' Warhammer 40K' },"
        " { name: 'Helsmiths of Hashut Army Box', condition: 'New in box (NIB)', target: 169.99, system: 'Age of Sigmar' },"
        " { name: 'Human Team', condition: '', target: 57.69, system: 'Blood Bowl' },"
        " { name: 'Dice', condition: 'Sealed', target: null, system: 'Blood Bowl' },"
        " { name: 'Bundle', condition: 'NoS', target: 1234.5, system: 'Blood Bowl' },"
        " { name: 'Loose bits', condition: '', target: 5, system: '' }]"
    )
    golden = (
        "Age of Sigmar\nHelsmiths of Hashut Army Box New in box (NIB) — $169.99\n\n"
        "Blood Bowl\nHuman Team — $57.69\nDice Sealed\nBundle NoS — $1,234.50\n\n"
        "Warhammer 40K\nWartrakk NoS — $20\nLand Speeder Mk II nos — $40\n\n"
        "Other\nLoose bits — $5\n"
    )
    assert _run_page(f"\nconsole.log(JSON.stringify({{ doc: chatDoc({items}) }}));\n")["doc"] == golden


def test_the_stock_lifecycle_and_filters():
    out = _run_page(
        "\nconst its = [{ id: 'a', name: 'Boyz', status: 'available', target: 20, system: '40K', lot_id: 'L1', price_updated_on: '2020-01-01' },"
        " { id: 'b', name: 'Nobz', status: 'listed', target: 30, system: '40K', lot_id: 'L2', price_updated_on: new Date().toISOString().slice(0, 10) },"
        " { id: 'c', name: 'Terrain', status: 'planned', target: 10, system: 'AoS', lot_id: 'L1', notes: 'bulky', price_updated_on: '2026-07' },"
        " { id: 'd', name: 'Mek', status: 'sold', target: 9, system: '40K', lot_id: 'L1', price_updated_on: '2020-01-01' },"
        " { id: 'e', name: 'Kept box', status: 'kept', target: null, system: '', lot_id: 'L2' },"
        " { id: 'f', name: 'Unpriced', status: 'available', target: null, system: '40K', lot_id: 'L1' }];\n"
        "const ids = (f) => stockItems(its, { seg: 'sale', system: '', lot: '', q: '', preset: '', ...f }).map((i) => i.id).join('');\n"
        "console.log(JSON.stringify({ sale: ids({}), split: ids({ seg: 'split' }), sold: ids({ seg: 'sold' }), all: ids({ seg: 'all' }),"
        " sys: ids({ system: '40K' }), lot: ids({ seg: 'all', lot: 'L2' }), q: ids({ seg: 'all', q: 'BULKY' }), ready: ids({ preset: 'ready', seg: 'sold' }), stale: ids({ preset: 'stale' }),"
        " readyN: readyToList(its).length, groups: bySystem(its).map(([k, xs]) => k + ':' + xs.length).join('|') }));\n"
    )
    assert out["sale"] == "abf"  # kept and withdrawn only show under All
    assert out["split"] == "c" and out["sold"] == "d" and out["all"] == "abcdef"
    assert out["sys"] == "abf" and out["lot"] == "be" and out["q"] == "c"
    assert out["ready"] == "a"  # a preset overrides the lifecycle; unpriced is not ready to list
    assert out["stale"] == "ac"  # sold is never stale, and a month-only date parses
    assert out["readyN"] == 1
    assert out["groups"] == "40K:4|AoS:1|Other:1"  # Copy list's order: alphabetical, blank last


def test_history_reads_as_sentences_not_json():
    out = _run_page(
        "\nconst n = (id) => ({ X: 'Riverblades', L: 'Pyre and Flood' }[id] || '');\n"
        "const l = (a) => auditLine(a, n);\n"
        "console.log(JSON.stringify({ when: auditWhen('2026-11-02T17:03:45Z'), rows: ["
        " l({ entity: 'item', entity_id: 'X', action: 'update', changes: { target_cents: 5400, price_basis: 'eBay  sold comps' } }),"
        " l({ entity: 'item', entity_id: 'X', action: 'update', changes: { status: 'planned' } }),"
        " l({ entity: 'item', entity_id: 'X', action: 'update', changes: { condition: 'NoS', model_count: 10 } }),"
        " l({ entity: 'item', entity_id: 'X', action: 'create', changes: { name: 'Riverblades' } }),"
        " l({ entity: 'item', entity_id: 'GONE', action: 'delete', changes: { snapshot: { item: { name: 'Old thing' } } } }),"
        " l({ entity: 'lot', entity_id: 'L', action: 'update', changes: { acquisition_cost_cents: 7000 } }),"
        " l({ entity: 'lot', entity_id: 'GONE', action: 'delete', changes: { snapshot: { name: 'Old lot' } } }),"
        " l({ entity: 'sale', entity_id: '3', action: 'create', changes: { item_id: 'X', channel: 'eBay', price: 54, net: 45.5 } }),"
        " l({ entity: 'listing', entity_id: '4', action: 'create', changes: { item_id: 'X', channel: 'eBay' } }),"
        " l({ entity: 'listing', entity_id: '4', action: 'end', changes: { state: 'sold' } })] }));\n"
    )
    assert out["when"] == "2026-11-02 17:03"
    assert out["rows"] == [
        "Price set on Riverblades: $54, basis eBay sold comps",
        "Riverblades: now to split",
        "Updated Riverblades: condition, model count",
        "Added Riverblades",
        "Deleted Old thing",
        "Updated lot Pyre and Flood: cost",
        "Deleted lot Old lot",
        "Sold Riverblades for $54 on eBay, net $45.50",
        "Listed Riverblades on eBay",
        "Listing 4 ended (sold)",
    ]


def test_a_failed_kit_stops_the_page_with_a_loud_error():
    """No kit means no bearer: the page must name the file and start nothing."""
    from inventory_plugin.view import PAGE

    assert "/_ds/plugin-kit.js, so this view can't reach its data" in PAGE
    assert _run_page("\nconsole.log(JSON.stringify({ kit, booted }));\n") == {"kit": None, "booted": False}


def test_the_page_stays_on_the_design_system():
    """The audit's P0: no literal type or spacing, no restyling of kit classes, no hand-built eyebrow."""
    from inventory_plugin.view import PAGE

    style = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)
    assert not re.search(r"\.pl-[\w-]+[^{}]*\{", style)  # kit classes are used, never overridden
    rules = re.sub(r"@media[^{]*\{", "", style)
    assert not re.search(r"\d+px", rules)
    assert not re.search(r"font-size:\s*\d", rules)


def test_the_view_is_organised_around_questions_not_tables():
    from inventory_plugin.view import PAGE

    tablist = re.search(r'<div class="pl-tabs" role="tablist".*?</div>', PAGE, re.S).group(0)
    assert re.findall(r'data-tab="(\w+)"', tablist) == ["next", "stock", "bins", "lots"]
    for gone in (
        "mdLine",
        "mdList",
        "JSON.stringify(a.changes)",
        "data-edit=",
        "dblclick",
        'class="pl-select status"',
        "data-status",
    ):
        assert gone not in PAGE, gone
    # No control may show up with nothing behind it; the scan flow (slice 4) now has a backend.
    for absent in ("Flash Gitz", "window.print", "/b/", "getUserMedia"):
        assert absent not in PAGE, absent
    for present in ('capture="environment"', 'data-act="scan"', '"/scans"'):
        assert present in PAGE, present
    for kept in (
        "pl-drawer--",
        'data-act="price"',
        'b("sold", "Record sale"',
        'data-act="del"',
        "Basis (required)",
        "Un-sell ",
        "Prefer Withdraw or Keep",
    ):
        assert kept in PAGE, kept


def test_bin_filters_move_mode_and_the_pick_list_groups():
    out = _run_page(
        "\nconst its = [{ id: 'a', name: 'Boyz', status: 'available', bin_id: 'B01' }, { id: 'b', name: 'Nobz', status: 'planned', bin_id: '' },"
        " { id: 'c', name: 'Mek', status: 'sold', bin_id: '' }, { id: 'd', name: 'Gitz', status: 'listed', bin_id: 'B02' }, { id: 'e', name: 'Kept', status: 'kept', bin_id: '' }];\n"
        "const ids = (f) => stockItems(its, { seg: 'all', system: '', lot: '', bin: '', q: '', preset: '', ...f }).map((i) => i.id).join('');\n"
        "const g = packGroups([{ id: 1, bin_id: 'B02' }, { id: 2, bin_id: '' }, { id: 3, bin_id: 'B01' }, { id: 4, bin_id: 'B02' }]).map(([k, xs]) => (k || '-') + ':' + xs.map((x) => x.id).join('')).join('|');\n"
        "console.log(JSON.stringify({ b1: ids({ bin: 'B01' }), none: ids({ bin: '-' }), nobin: ids({ preset: 'nobin' }), q: ids({ q: 'b02' }),"
        " move: ids({ seg: 'sale', exclude: 'B01' }), g }));\n"
    )
    assert out["b1"] == "a" and out["none"] == "bce"
    assert out["nobin"] == "b"  # unsold and in no bin: what "Start binning" opens
    assert out["q"] == "d"  # search reads the bin
    assert out["move"] == "d"  # moving into B01: what is already in B01 is not offered
    assert out["g"] == "B01:3|B02:14|-:2"  # bin order, "no bin recorded" last


def test_an_unreadable_price_date_counts_as_stale():
    out = _run_page(
        "\nconst base = { status: 'available', target: 10 };\n"
        "console.log(JSON.stringify({ bad: isStale({ ...base, price_updated_on: 'sometime in 2025' }), blank: isStale({ ...base, price_updated_on: '' }),"
        " fresh: isStale({ ...base, price_updated_on: new Date().toISOString().slice(0, 10) }), d: daysOld('not a date') }));\n"
    )
    assert out == {"bad": True, "blank": True, "fresh": False, "d": None}  # Infinity serialises as null


def test_history_reads_bin_moves_and_packing():
    out = _run_page(
        "\nconst n = (id) => ({ X: 'Riverblades' }[id] || '');\n"
        "console.log(JSON.stringify([auditLine({ entity: 'item', entity_id: 'X', action: 'move', changes: { bin: ['', 'B07'] } }, n),"
        " auditLine({ entity: 'item', entity_id: 'X', action: 'move', changes: { bin: ['B03', 'B07'] } }, n),"
        " auditLine({ entity: 'item', entity_id: 'X', action: 'move', changes: { bin: ['B07', ''], reason: 'packed' } }, n),"
        " auditLine({ entity: 'sale', entity_id: '9', action: 'packed', changes: { item_id: 'X' } }, n),"
        " auditLine({ entity: 'bin', entity_id: 'B07', action: 'create', changes: { label: 'Terrain' } }, n),"
        " auditLine({ entity: 'bin', entity_id: 'B07', action: 'delete', changes: {} }, n)]));\n"
    )
    assert out == [
        "Moved Riverblades into B07",
        "Moved Riverblades B03 → B07",
        "Took Riverblades out of B07 (packed)",
        "Packed Riverblades",
        "Made bin B07 (Terrain)",
        "Removed bin B07",
    ]


def test_the_edit_dialog_never_sets_status():
    """State moves only through the drawer's actions (Record sale, Mark available, Keep, Withdraw,
    Return to stock) and a draft's "I listed it", each with its own guard. Edit has no status field
    and never sends one."""
    from inventory_plugin.view import PAGE

    dialog = PAGE.split("async function itemDialog", 1)[1].split("async function lotDialog", 1)[0]
    assert 'field("status"' not in dialog and "body.status" not in dialog and "STATUSES" not in PAGE
    for act in ("sold:", "keep:", "withdraw:", "available:", "return:"):
        assert act in PAGE.split("const ACTS = {", 1)[1].split("};", 1)[0]


def test_the_page_has_multiselect_and_copy():
    from inventory_plugin.view import PAGE

    for needle in (
        'id="sel-all"',
        "data-sel=",
        'id="copy-md"',
        "navigator.clipboard.writeText",
        'execCommand("copy")',
        "copySelected()",
        'data-act="movesel"',
        'btn("movehere", "Move items here"',
        'btn("copybin", "Copy list")',
        'data-act="packed"',
        'id="f-bin"',
    ):
        assert needle in PAGE, needle


def test_label_printing_opens_its_tab_before_any_await_and_boots_into_a_scanned_bin():
    """A new tab must be opened inside the click itself: anything after an await is a popup
    the browser blocks. The frame has no allow-modals, so printing never happens in the frame."""
    from inventory_plugin.view import PAGE

    body = PAGE.split("async function labelsDialog", 1)[1].split("async function newBinsDialog", 1)[0]
    submit = body.split("onSubmit: async (v) => {", 1)[1]
    assert submit.lstrip().startswith('const w = window.open("", "_blank");')
    assert ".print(" not in PAGE  # printing happens in the sheet's own tab
    assert 'URLSearchParams(location.search || "").get("bin")' in PAGE and "applyStartBin()" in PAGE


def test_bins_sort_in_number_order_under_node():
    probe = (
        "\nconsole.log(JSON.stringify(sortBins([{id:'B10'},{id:'SHELF'},{id:'B2'},{id:'B01'}]).map((b) => b.id)));\n"
    )
    assert _run_page(probe) == ["B01", "B2", "B10", "SHELF"]


def test_the_scan_breakdown_logic_under_node():
    """The view's scan bookkeeping, run for real: the agent's contents merge once; a piece
    photographed into the box absorbs the listed row with the same name (no duplicate); a
    low-confidence piece needs a Check; Needs a look says what to do next."""
    probe = (
        "\nstate.threshold = 0.7; state.lots = [{ id: 'L', name: 'Box scanned' }];"
        " SC.kind = 'box'; SC.data = { id: 'S1', kind: 'box', state: 'proposed', lot_id: 'L', photos: [], item_ids: [],"
        " proposal: { name: 'Pyre and Flood', confidence: 0.9, contents: [{ name: 'Riverblades', quantity: 1 }, { name: 'Idol', quantity: 1 }] },"
        " units: [{ id: 'S2', state: 'proposed', photos: [], proposal: { name: 'riverblades', confidence: 0.95, alternatives: [] } },"
        " { id: 'S3', state: 'proposed', photos: [], proposal: { name: 'Wardens', confidence: 0.2, alternatives: [{ name: 'Sentinels' }] } }] };"
        " mergeUnits(); mergeUnits();"
        " const rows = SC.units.map((u) => [u.name, u.state, u.scan_id || '']);"
        " const subs = [scanSub({ state: 'identifying' }), scanSub({ state: 'failed' }), scanSub({ state: 'proposed', kind: 'box', proposal: { confidence: 0.3 } }),"
        " scanSub({ state: 'proposed', kind: 'box', proposal: { confidence: 0.9 } }), scanSub({ state: 'confirmed', pricing: { state: 'queued' } }), scanSub({ state: 'confirmed', pricing: null })];"
        " console.log(JSON.stringify({ rows, subs, check: [scanNeedsCheck({ state: 'failed' }), scanNeedsCheck({ state: 'proposed', proposal: { confidence: 0.9 } })] }));\n"
    )
    out = _run_page(probe)
    assert out["rows"] == [["Idol", "ok", ""], ["riverblades", "ok", "S2"], ["Wardens", "check", "S3"]]
    assert out["subs"] == [
        "Identifying…",
        "Couldn't identify it. Type the name or retry",
        "Not sure. Check it",
        "Identified. Break it down",
        "Pricing…",
        "Bin the pieces",
    ]
    assert out["check"] == [True, False]


def test_the_listing_draft_section_renders_each_state_safely_under_node():
    """The drawer's Listing section from real draft payloads: a hostile title stays inert, the
    title counter, the controls only where they act (no draft without a price, no new draft for
    kept stock), and the Next sub-line."""
    probe = (
        "\nconst it = { id: 'A', name: 'Item', status: 'available', target: 45, price_basis: 'eBay sold comps (5 sold)' };"
        "\nconst d = { item_id: 'A', state: 'draft', listing_state: 'draft', title: '<img src=x onerror=alert(1)> Big Mek NoS', description: 'NoS\\n<script>x</script>', notes: '', photos: [{ scan_id: 'S1', photo_id: 'p' }] };"
        "\nconsole.log(JSON.stringify({"
        " ready: draftBody(it, d), none: draftBody(it, null), noprice: draftBody({ ...it, target: null }, null),"
        " kept: draftBody({ ...it, status: 'kept' }, null), drafting: draftBody(it, { ...d, state: 'drafting' }),"
        " failed: draftBody(it, { ...d, state: 'failed', reason: '<b>no photos</b>' }),"
        " live: draftBody({ ...it, status: 'listed' }, { ...d, state: 'live', listing: { url: 'javascript:alert(1)' } }),"
        " labels: [draftLabel(null), draftLabel({ listing_state: 'none' }), draftLabel({ listing_state: 'copied' }), draftLabel({ listing_state: 'live' })] }));\n"
    )
    out = _run_page(probe)
    r = out["ready"]
    assert "<img" not in r and "<script>" not in r and "&lt;img" in r
    assert (
        "40 / 80 characters" in r
        and 'data-act="dlive"' in r
        and 'data-act="redraft"' in r
        and 'data-act="dphotos"' in r
    )
    assert r.count('data-act="dcopy"') == 3 and 'data-k="price"' in r  # title, price and description
    assert "Change it with Set price" in r and ".." not in r  # the price isn't edited in the draft
    assert (
        'data-act="draft"' in out["none"]
        and 'data-act="draft"' not in out["noprice"]
        and "Set a price first" in out["noprice"]
    )
    assert out["kept"] == ""
    assert "Drafting" in out["drafting"] and 'data-act="dcopy"' not in out["drafting"]
    assert "&lt;b&gt;no photos" in out["failed"] and "Try again" in out["failed"]
    assert out["live"] == ""  # the live listing row above carries the link
    assert out["labels"] == ["no draft yet", "no draft yet", "copied, not live yet", "live on eBay"]
