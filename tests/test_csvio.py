"""CSV import/export against the operator's REAL sheet shapes (fixtures are the first rows
of the merchantAgent workspace files, headers verbatim)."""

from __future__ import annotations

from pathlib import Path

from inventory_plugin.csvio import detect_kind, export_csv, import_csv, parse_csv

FIX = Path(__file__).parent / "fixtures"


class TestImport:
    def test_the_lots_sheet_is_detected_and_loaded(self, store):
        out = import_csv(store, (FIX / "lots-sheet.csv").read_text(), actor="t")
        assert out["kind"] == "lots" and out["created"] == 2 and out["warnings"] == []
        lot = store.get_lot("DEMOLOT-2026-09")
        assert lot["acquisition_cost"] == 400.0 and lot["acquired_on"] == "2026-09"
        assert "Demo" in lot["name"]

    def test_the_items_sheet_maps_headers_statuses_and_sales(self, store):
        import_csv(store, (FIX / "lots-sheet.csv").read_text(), actor="t")
        out = import_csv(store, (FIX / "items-sheet.csv").read_text(), actor="t")
        assert out["kind"] == "items"
        assert out["created"] == out["rows"]  # every row landed, including the odd statuses
        assert out["warnings"] == []  # "Planned Split" is a real status now
        assert store.list_items(status="planned"), "the sheet's 'Planned Split' rows import as planned"
        assert {
            "inventory_id": "id",
            "item": "name",
            "target_price_usd": "target",
            "price_source": "price_basis",
        }.items() <= out["mapped_columns"].items()
        assert "discount_estimate" not in out["ignored_columns"]  # known-and-deliberately-dropped, not "unknown"
        cap = store.get_item("DEMO-SM-001")
        assert cap["target"] == 20.0 and cap["retail"] == 40.0 and cap["status"] == "available"
        assert cap["price_basis"] == "Sample market analysis"
        sold = [i for i in store.list_items(status="sold")]
        assert sold, "the 'Sold ($5)' / 'Sold ($25)' rows must import as sales"
        for item in sold:
            full = store.get_item(item["id"])
            assert full["sales"] and full["sales"][0]["channel"] == "import"
        assert out["sales_recorded"] == len(sold)

    def test_reimport_updates_instead_of_duplicating(self, store):
        import_csv(store, (FIX / "lots-sheet.csv").read_text(), actor="t")
        text = (FIX / "items-sheet.csv").read_text()
        first = import_csv(store, text, actor="t")
        second = import_csv(store, text, actor="t")
        assert second["created"] == 0 and second["updated"] == first["created"]
        assert second["sales_recorded"] == 0  # a sale is recorded once
        assert len(store.list_items()) == first["created"]

    def test_a_bad_row_is_reported_not_fatal(self, store):
        text = "id,name,status\nA,Good,available\nB,,available\nC,Also good,banana\n"
        out = import_csv(store, text, actor="t")
        assert out["created"] == 2  # A, and C kept with a warning; B (no name) is the one that cannot land
        assert len(out["warnings"]) == 2
        assert "line 3" in out["warnings"][0] and "needs a name" in out["warnings"][0]
        assert "line 4" in out["warnings"][1] and "kept as available" in out["warnings"][1]
        c = store.get_item("C")
        assert c["status"] == "available" and "status as imported: banana" in c["notes"]

    def test_default_lot_applies_when_the_sheet_has_none(self, store):
        out = import_csv(store, "name,target\nThing,10\n", actor="t", default_lot="LOT-9")
        assert out["created"] == 1
        assert store.list_items()[0]["lot_id"] == "LOT-9"

    def test_empty_csv(self, store):
        assert import_csv(store, "", actor="t")["ok"] is False

    def test_kind_detection(self):
        assert detect_kind(["lot_id", "lot_name", "acquisition_cost_usd"]) == "lots"
        assert detect_kind(["id", "name", "cost", "date", "source"]) == "lots"  # alias-only lots sheet
        assert detect_kind(["inventory_id", "lot_id", "item"]) == "items"
        assert detect_kind(["name", "target"]) == "items"
        assert detect_kind(["id", "name", "cost", "status"]) == "items"  # `status` marks items


class TestExport:
    def test_round_trip(self, seeded):
        text = export_csv(seeded, kind="items")
        headers, rows = parse_csv(text)
        assert "price_basis" in headers and len(rows) == 4
        by_id = {r["id"]: r for r in rows}
        assert by_id["A"]["target"] == "30.0" and by_id["C"]["status"] == "sold"
        lots = export_csv(seeded, kind="lots")
        assert "LOT-1" in lots and "100.0" in lots

    def test_export_filters(self, seeded):
        _, rows = parse_csv(export_csv(seeded, kind="items", status="sold"))
        assert [r["id"] for r in rows] == ["C"]
