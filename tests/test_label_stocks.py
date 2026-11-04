"""0.15.0: label stocks. Item and bin sheets print on Avery 5160 or 5163 sheets or on a thermal
roll (one label per page). The QR stays at least 0.8″ where the label allows, never under 0.6″.
The view remembers the last stock per kind in this browser."""

from __future__ import annotations

import re

import pytest

from tests.test_item_labels import API, HUB, _boxes, _client, _with_items, host  # noqa: F401  (host is a fixture)


def _page_size(html):
    m = re.search(r"@page\{size:([\d.]+)in ([\d.]+)in;margin:0\}", html)
    return (float(m.group(1)), float(m.group(2))) if m else None


def _qr_in(html):
    return float(re.search(r"\.qr\{flex:0 0 ([\d.]+)in", html).group(1))


@pytest.mark.parametrize("stock", ["avery5160", "avery5163", "dymo30252", "roll225x125"])
def test_every_stock_keeps_the_qr_readable(stock):
    from inventory_plugin.labels import QR_MIN_IN, QR_PREF_IN, stock_layout

    lay = stock_layout(stock)
    assert lay["qr"] >= QR_PREF_IN and lay["qr"] >= QR_MIN_IN


def test_a_label_too_small_for_a_readable_qr_is_refused(monkeypatch):
    from inventory_plugin import labels

    monkeypatch.setitem(labels.LABEL_STOCKS, "tiny", {"name": "tiny", "sheet": False, "w": 0.75, "h": 0.5, "qr": 0.4})
    with pytest.raises(ValueError, match="minimum"):
        labels.stock_layout("tiny")


def test_avery_5163_is_ten_4_by_2_labels_closing_exactly_on_letter():
    from inventory_plugin.labels import LABEL_STOCKS, items_on_stock

    g = LABEL_STOCKS["avery5163"]
    assert g["side"] * 2 + g["w"] * g["cols"] + (g["pitch_x"] - g["w"]) * (g["cols"] - 1) == pytest.approx(8.5)
    assert g["top"] * 2 + g["h"] * g["rows"] == pytest.approx(11.0)
    html = items_on_stock([{"id": f"I{k}", "name": "n"} for k in range(11)], HUB, "avery5163", start_at=3)
    assert _page_size(html) == (8.5, 11.0) and html.count('class="page"') == 2
    b = _boxes(html)
    assert b[:4] == [(0.15625, 0.5), (4.34375, 0.5), (0.15625, 2.5), (4.34375, 2.5)]  # 2 blanks, then labels
    assert html.count('class="label blank"') == 2 and _qr_in(html) == 1.6


@pytest.mark.parametrize(("stock", "size"), [("dymo30252", (3.5, 1.125)), ("roll225x125", (2.25, 1.25))])
def test_a_roll_prints_one_label_per_page_the_size_of_the_label(stock, size):
    from inventory_plugin.labels import bins_on_stock, items_on_stock

    html = items_on_stock(
        [{"id": "A", "name": "Alpha", "condition": "NoS", "bin_id": "B01"}, {"id": "B", "name": "Beta"}], HUB, stock
    )
    assert _page_size(html) == size and html.count('class="page"') == 2 and _boxes(html) == [(0.0, 0.0), (0.0, 0.0)]
    assert "/plugins/inventory/i/" in html.split("<body", 1)[1] and "NoS · B01" in html
    bins = bins_on_stock([{"id": "B01", "label": "Orks"}], HUB, stock)
    assert _page_size(bins) == size and 'class="bid">B01<' in bins and "Orks" in bins


def test_the_defaults_are_the_sheets_from_before():
    from inventory_plugin.labels import bins_on_stock, item_sheet_html, items_on_stock, sheet_html

    items = [{"id": "A", "name": "Alpha"}]
    assert items_on_stock(items, HUB, "avery5160") == item_sheet_html(items, HUB)
    bins = [{"id": "B01", "label": ""}]
    assert bins_on_stock(bins, HUB, "bins12") == sheet_html(bins, HUB)


def test_the_routes_take_a_stock_and_check_start_at_against_it(host, registry, monkeypatch):  # noqa: F811
    c = _with_items(registry, monkeypatch, 3)
    base = c.get(API + "/labels/base").json()
    assert [s["id"] for s in base["stocks"]["items"]] == ["avery5160", "avery5163", "dymo30252", "roll225x125"]
    assert base["stocks"]["bins"][0]["id"] == "bins12" and base["default_stock"] == {
        "bins": "bins12",
        "items": "avery5160",
    }
    r = c.post(API + "/labels/sheet", json={"items": ["IT-00"], "stock": "dymo30252"}).json()
    assert r["stock"] == "dymo30252" and "3½″ × 1⅛″" in r["stock_name"] and r["link"]
    bad = c.post(API + "/labels/sheet", json={"items": ["IT-00"], "stock": "bins12"})
    assert bad.status_code == 400 and "isn't one for items" in bad.json()["detail"]
    assert (
        "1 to 10"
        in c.post(API + "/labels/sheet", json={"items": ["IT-00"], "stock": "avery5163", "start_at": 11}).json()[
            "detail"
        ]
    )
    assert (
        c.post(API + "/labels/sheet", json={"items": ["IT-00"], "stock": "avery5163", "start_at": 10}).status_code
        == 200
    )
    assert (
        "1 to 1"
        in c.post(API + "/labels/sheet", json={"items": ["IT-00"], "stock": "roll225x125", "start_at": 2}).json()[
            "detail"
        ]
    )
    b = c.post(API + "/labels/sheet", json={"all": True, "stock": "avery5160"}).json()
    assert b["stock"] == "avery5160" and _page_size(b["html"]) == (8.5, 11.0)


def test_the_view_offers_the_stock_and_remembers_it_per_kind():
    from inventory_plugin.view import PAGE

    assert '"inventory.labelstock." + kind' in PAGE and "localStorage.setItem(STOCK_KEY(kind), v)" in PAGE
    bins = PAGE[
        PAGE.index("  async function labelsDialog(only) {") : PAGE.index(
            "  async function itemLabelsDialog(ids, title) {"
        )
    ]
    items = PAGE[
        PAGE.index("  async function itemLabelsDialog(ids, title) {") : PAGE.index("  async function newBinsDialog() {")
    ]
    for d, kind in ((bins, "bins"), (items, "items")):
        assert f'stockField("{kind}", info)' in d and f'keepStock("{kind}"' in d and f'wireStock("{kind}", info)' in d
        assert d.index("const shown = dialog(") < d.index("wireStock(")  # wired while the dialog is open
