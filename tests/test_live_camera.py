"""0.13.0: a live viewfinder in Put away. Through the hub the console and this frame share an
origin, so getUserMedia works in the frame; the photo path stays as the fallback. The pure
helpers run under node; a fake video frame goes through the same crop, jsQR and parser."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _page():
    from inventory_plugin.view import PAGE

    return PAGE


def _helpers() -> str:
    page = _page()
    return page[page.index("  const CAM_FPS") : page.index("  const CAM = {")]


def _node(script: str, *args: str, stdin: str = "") -> object:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    out = subprocess.run(
        [node, "-e", script, *args], input=stdin, capture_output=True, text=True, timeout=120, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_crop_is_the_centre_square_of_the_framing_box_scaled_for_jsqr():
    got = _node(
        _helpers()
        + "console.log(JSON.stringify([camCrop(1920,1080), camCrop(1080,1920), camCrop(640,480), camCrop(10,10,0.7,720)]));"
    )
    assert got == [
        {"sx": 582, "sy": 162, "s": 756, "out": 720},  # landscape 1080p: 70% of 1080, capped at 720
        {"sx": 162, "sy": 582, "s": 756, "out": 720},  # portrait (a phone held upright)
        {"sx": 152, "sy": 72, "s": 336, "out": 336},  # small stream: never upscaled
        {"sx": 1, "sy": 1, "s": 7, "out": 7},
    ]


def test_frames_are_throttled_and_a_label_in_view_is_read_once_per_window():
    got = _node(
        _helpers()
        + "const seen = new Map();"
        + "console.log(JSON.stringify({"
        + " due: [camDue(1000, 900), camDue(1000, 857), camDue(1000, 0)],"
        + " fresh: [camFresh(seen,'A',0), camFresh(seen,'A',1000), camFresh(seen,'B',1200), camFresh(seen,'A',2600), camFresh(seen,'A',3000)],"
        + " inv: [0,1,2,3,4,8].map(camInversion) }));"
    )
    assert got["due"] == [False, True, True]  # ~7 fps: 143 ms between decodes
    assert got["fresh"] == [True, False, True, True, False]  # 2.5 s per code, codes independent
    assert got["inv"] == ["attemptBoth", "dontInvert", "dontInvert", "dontInvert", "attemptBoth", "attemptBoth"]


def test_support_needs_getusermedia_and_respects_a_policy_that_says_no():
    page = _page()
    fn = page[page.index("  function camSupported() {") : page.index("  const CAM = {")]
    cases = {
        "none": "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {} }); globalThis.document = {};",
        "safari": "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia() {} } } }); globalThis.document = {};",
        "allowed": "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia() {} } } }); globalThis.document = { featurePolicy: { allowsFeature: () => true } };",
        "blocked": "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia() {} } } }); globalThis.document = { featurePolicy: { allowsFeature: () => false } };",
    }
    out = {}
    for k, env in cases.items():
        out[k] = _node(env + fn + "console.log(JSON.stringify(camSupported()));")
    assert out == {"none": False, "safari": True, "allowed": True, "blocked": False}


def _qr_rgba(text: str, scale: int):
    from inventory_plugin.vendor.qrcodegen import QrCode

    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n, border = qr.get_size(), 4
    side = (n + 2 * border) * scale
    rows = []
    for y in range(side):
        row = bytearray()
        for x in range(side):
            mx, my = x // scale - border, y // scale - border
            v = 0 if (0 <= mx < n and 0 <= my < n and qr.get_module(mx, my)) else 255
            row += bytes((v, v, v, 255))
        rows.append(bytes(row))
    return side, rows


def _frame(text: str, w: int, h: int, at: str) -> str:
    """A grey 'video frame' with the label's QR pasted in the centre or a corner, as hex RGBA."""
    side, rows = _qr_rgba(text, 7)
    x0, y0 = ((w - side) // 2, (h - side) // 2) if at == "centre" else (4, 4)
    frame = bytearray(bytes((128, 128, 128, 255)) * (w * h))
    for y, row in enumerate(rows):
        o = ((y0 + y) * w + x0) * 4
        frame[o : o + len(row)] = row
    return frame.hex()


def test_a_fake_video_frame_goes_through_the_crop_jsqr_and_the_label_parser():
    from inventory_plugin.labels import item_label_url, label_url

    page = _page()
    parse = page[page.index("  function parseLabel(text) {") : page.index("  function parseTyped(text) {")]
    hub = "https://hub.example.ts.net/agents/merchantAgent-6604"
    w, h = 1280, 720
    frames = [
        _frame(item_label_url(hub, "NH-SH-Harrows"), w, h, "centre"),
        _frame(label_url(hub, "B07"), w, h, "centre"),
        _frame(item_label_url(hub, "NH-SH-Harrows"), w, h, "corner"),  # outside the framing square
    ]
    # The view draws the crop with canvas.drawImage; here the same rectangle is copied (nearest
    # neighbour) into a cropped buffer of `out` px, which is all the canvas step does.
    script = (
        "const jsQR = require(process.argv[1]);\n"
        + _helpers()
        + parse
        + f"\nconst W = {w}, H = {h}; const frames = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "console.log(JSON.stringify(frames.map((hex) => { const src = Buffer.from(hex, 'hex'); const c = camCrop(W, H);"
        + " const out = new Uint8ClampedArray(c.out * c.out * 4);"
        + " for (let y = 0; y < c.out; y++) for (let x = 0; x < c.out; x++) {"
        + "  const sx = c.sx + Math.floor(x * c.s / c.out), sy = c.sy + Math.floor(y * c.s / c.out);"
        + "  const i = (sy * W + sx) * 4, o = (y * c.out + x) * 4; out[o] = src[i]; out[o+1] = src[i+1]; out[o+2] = src[i+2]; out[o+3] = 255; }"
        + " const r = jsQR(out, c.out, c.out, { inversionAttempts: camInversion(1) }); return r ? parseLabel(r.data) : null; })));"
    )
    got = _node(script, str(ROOT / "vendor" / "jsqr" / "jsQR.js"), stdin=json.dumps(frames))
    assert got == [{"kind": "item", "id": "NH-SH-Harrows"}, {"kind": "bin", "id": "B07"}, None]


def test_the_stream_is_started_on_a_tap_and_always_stopped():
    page = _page()
    cam = page[page.index("  async function camStart() {") : page.index("  function camTick(now) {")]
    assert 'facingMode: { ideal: "environment" }' in cam and "audio: false" in cam
    assert "new AC()" in cam  # the AudioContext is made inside the starting tap (iOS)
    assert "getTracks().forEach((t) => t.stop())" in cam
    assert "CAM_IDLE_MS" in cam and "Paused — tap to resume." in page
    build = page[page.index("  function camBuild() {") : page.index("  function camMount() {")]
    assert 'setAttribute("playsinline", "")' in build and "v.muted = true" in build  # iOS needs both
    assert '"pa-cam": () => camStart()' in page  # only a tap starts it
    close = page[page.index("  function closeDrawer() {") : page.index("  function paintDrawer(")]
    assert "camStop()" in close
    assert "document.hidden && CAM.on" in page and 'addEventListener("pagehide"' in page


def test_refusal_and_unavailability_fall_back_to_the_photo_path():
    page = _page()
    assert "Camera permission was refused. Allow it in Settings ▸ Safari ▸ Camera, or take a photo instead." in page
    assert "The live camera isn't available here, so take a photo of the label instead." in page
    paint = page[page.index("  function paPaint() {") : page.index("  const paSay")]
    assert 'capBtn("pa"' in paint  # the photo button is always there
    assert "live && !CAM.on ?" in paint and 'data-act="pa-cam"' in paint  # the camera button only when it can work
    hit = page[page.index("  async function camHit(text) {") : page.index("  async function camTorch() {")]
    assert "parseLabel(text)" in hit and "location" not in hit and "window.open" not in hit  # read, never opened
    assert "CAM.paused = true" in hit and "CAM.paused = false" in hit  # a bin's confirm pauses reading


def test_a_finished_batch_empties_the_list_on_screen_and_isnt_read_straight_back():
    page = _page()
    move = page[page.index("  async function paMove(ids, bin) {") : page.index("  const PA_ACTS = {")]
    assert "camMoved(ids, bin)" in move and "paPaint();" in move.split("await refresh();", 1)[1]
    got = _node(
        _helpers()
        + page[page.index("  function parseLabel(text) {") : page.index("  function parseTyped(text) {")]
        + "const CAM = { seen: new Map() }; let now = 0; Date.now = () => now;"
        + page[page.index("  const CAM_MOVED_MS") : page.index("  async function camTorch() {")]
        + "const code = 'https://hub.example.ts.net/plugins/inventory/i/P1';"
        + "const bin = 'https://hub.example.ts.net/plugins/inventory/b/B01';"
        + "const other = 'https://hub.example.ts.net/plugins/inventory/b/B02';"
        + "camFresh(CAM.seen, code, 0); camFresh(CAM.seen, bin, 0); camFresh(CAM.seen, other, 0); camMoved(['P1'], 'B01');"
        + "const r = [3000, 14000, 15100].map((t) => [camFresh(CAM.seen, code, t), camFresh(CAM.seen, bin, t)]);"
        + "console.log(JSON.stringify({ r, other: camFresh(CAM.seen, other, 3000) }));"
    )
    # the moved piece and the bin it went into are ignored for 15 s, then readable again; any other bin is not held back
    assert got == {"r": [[False, False], [False, False], [True, True]], "other": True}


def test_every_third_tick_reads_the_whole_frame_so_an_off_centre_label_still_reads():
    # Live test 11-04: a close-up whose QR sat left of the framing square never read.
    from inventory_plugin.labels import item_label_url

    page = _page()
    parse = page[page.index("  function parseLabel(text) {") : page.index("  function parseTyped(text) {")]
    hub = "https://hub.example.ts.net/agents/merchantAgent-6604"
    w, h = 1280, 720
    frame = _frame(item_label_url(hub, "NH-SH-Harrows"), w, h, "corner")
    plans = _node(_helpers() + "\nconsole.log(JSON.stringify([0,1,2,3,4,5].map((n) => camPlan(1920, 1080, n))));")
    assert [p["full"] for p in plans] == [False, False, True, False, False, True]
    assert (plans[2]["ow"], plans[2]["oh"], plans[2]["sw"]) == (960, 540, 1920)
    script = (
        "const jsQR = require(process.argv[1]);\n"
        + _helpers()
        + parse
        + f"\nconst W = {w}, H = {h}; const src = Buffer.from(JSON.parse(require('fs').readFileSync(0, 'utf8')), 'hex');\n"
        + "console.log(JSON.stringify([0, 2].map((n) => { const c = camPlan(W, H, n);"
        + " const out = new Uint8ClampedArray(c.ow * c.oh * 4);"
        + " for (let y = 0; y < c.oh; y++) for (let x = 0; x < c.ow; x++) {"
        + "  const sx = c.sx + Math.floor(x * c.sw / c.ow), sy = c.sy + Math.floor(y * c.sh / c.oh);"
        + "  const i = (sy * W + sx) * 4, o = (y * c.ow + x) * 4; out[o] = src[i]; out[o+1] = src[i+1]; out[o+2] = src[i+2]; out[o+3] = 255; }"
        + " const r = jsQR(out, c.ow, c.oh, { inversionAttempts: 'dontInvert' }); return r ? parseLabel(r.data) : null; })));"
    )
    got = _node(script, str(ROOT / "vendor" / "jsqr" / "jsQR.js"), stdin=json.dumps(frame))
    assert got == [None, {"kind": "item", "id": "NH-SH-Harrows"}]  # the centre crop misses it; the full frame reads it


# ── 0.17.0: the viewfinder shows what it read ────────────────────────────────────────────


def _feed_fn() -> str:
    page = _page()
    return page[page.index("  const CAM_FEED_ROWS") : page.index("  function camFeed(head, names)")]


def test_the_feed_lists_the_newest_reads_first_capped_with_a_more_line_and_escaped():
    esc = "const esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');"
    got = _node(
        esc
        + _feed_fn()
        + "console.log(JSON.stringify({"
        + " empty: camFeedHtml('Scanned · 0', [], []),"
        + " one: camFeedHtml('Scanned · 1', ['Ork <Boyz>'], []),"
        + " six: camFeedHtml('Scanned · 6', ['a','b','c','d','e','f'], ['b','c','d']),"
        + " again: camFeedHtml('Scanned · 6', ['a','b','c','d','e','f'], ['d','e','f']) }));"
    )
    assert got["empty"] == ""
    assert "Ork &lt;Boyz>" in got["one"] and '<li class="cf-row new">' in got["one"]
    six = got["six"]
    order = [six.index(x) for x in ("<span>f<", "<span>e<", "<span>d<")]
    assert order == sorted(order)  # newest first
    assert "<span>c<" not in six and "+ 3 more below" in six  # three rows, then a count
    assert six.count(" new") == 1 and six.count(" old") == 2  # only the fresh read slides in
    assert " new" not in got["again"]  # nothing new: no row animates


def test_a_read_never_borrows_the_stock_lists_hit_class_and_every_outcome_shows():
    page = _page()
    cam = page[page.index("  const CAM = {") : page.index("  function openPutAway()")]
    assert '"hit"' not in cam and ".cam.hit" not in page  # .hit is the Stock row's tap target
    hit = page[page.index("async function camHit") :][:2200]
    for kind in ('camSignal("ok"', 'camSignal("dup"', 'camSignal("bad"'):
        assert kind in hit
    assert "void el.offsetWidth" in page  # back-to-back reads each replay the animation
    # The running list is painted onto the video by every drawer that owns the camera.
    assert page.count("camMount(); camFeed(") == 3
    style = page[page.index("<style>") : page.index("</style>")]
    assert "@keyframes cam-shutter" in style and "prefers-reduced-motion" in style


def test_a_repaint_updates_the_open_drawer_in_place_instead_of_replaying_its_slide_in():
    page = _page()
    fn = page[page.index("  function paintDrawer(") : page.index("  let drawerSeq = 0;")]
    in_place, rebuild = fn.index('open.classList.contains("pl-drawer--" + side)'), fn.index('$("#overlay-root").innerHTML =')
    assert in_place < rebuild and "return;" in fn[in_place:rebuild]
