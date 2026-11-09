import copy

from po_pipeline.validate import validate_po

GOOD_PO = {
    "customer_name": "Acme Supply Co", "po_number": "4500012345",
    "po_date": "2026-10-27", "requested_ship_date": "2026-11-10",
    "ship_to": "Acme Supply Co - Warehouse 2",
    "line_items": [
        {"line_number": 1, "part_number": "MR-4410", "quantity": 37, "uom": "BX",
         "unit_price": 764.98, "confidence": 0.95, "catalog_status": "found"},
        {"line_number": 2, "part_number": "MR-3150", "quantity": 17, "uom": "EA",
         "unit_price": 119.36, "confidence": 0.95, "catalog_status": "found"},
    ],
    "field_confidence": {"po_number": 0.95}, "missing_fields": [],
}


def broken(**changes):
    po = copy.deepcopy(GOOD_PO)
    po.update(changes)
    return po


def messages(result):
    return " | ".join(i["message"] for i in result["issues"])


def test_clean_po_is_ready():
    result = validate_po(GOOD_PO)
    assert result["status"] == "READY" and result["issues"] == []


def test_missing_po_number_needs_review():
    result = validate_po(broken(po_number=None))
    assert result["status"] == "NEEDS_REVIEW"
    assert "PO number is missing" in messages(result)


def test_ship_date_before_po_date_is_an_error():
    result = validate_po(broken(requested_ship_date="2026-10-01"))
    assert result["status"] == "NEEDS_REVIEW"
    assert "before PO date" in messages(result)


def test_zero_quantity_and_unknown_part_are_errors():
    po = copy.deepcopy(GOOD_PO)
    po["line_items"][0]["quantity"] = 0
    po["line_items"][1].update(part_number="MR-6008", catalog_status="not_found")
    result = validate_po(po)
    assert result["errors"] == 2
    assert "greater than 0" in messages(result) and "not in the catalog" in messages(result)


def test_price_20_to_50_percent_off_is_a_warning():
    po = copy.deepcopy(GOOD_PO)
    po["line_items"][1]["unit_price"] = 84.00   # list 120.00 -> 30% off
    result = validate_po(po)
    assert result["errors"] == 0 and result["warnings"] == 1
    assert result["status"] == "READY"          # one warning: 0.95 * 0.9 = 0.86, still above 0.85


def test_price_over_50_percent_off_blocks():
    po = copy.deepcopy(GOOD_PO)
    po["line_items"][1]["unit_price"] = 26.65   # list 120.00 -> 78% off (the PO-1003 case)
    result = validate_po(po)
    assert result["errors"] == 1 and result["status"] == "NEEDS_REVIEW"
    assert "78% from list price" in messages(result)


def test_low_ai_confidence_lowers_score():
    po = copy.deepcopy(GOOD_PO)
    for line in po["line_items"]:
        line["confidence"] = 0.6
    result = validate_po(po)
    assert result["status"] == "NEEDS_REVIEW" and result["warnings"] == 2


def test_ocr_style_values_are_normalized():
    po = copy.deepcopy(GOOD_PO)
    po["line_items"][0].update(part_number=" mr-4410 ", uom="bx")
    result = validate_po(po)
    assert result["status"] == "READY"
    assert result["po"]["line_items"][0]["part_number"] == "MR-4410"
