import pytest

from po_pipeline.erp import REMARK_MAX, MockErp, NotReadyError, build_sales_order

READY = {
    "status": "READY",
    "po": {
        "customer_name": "Acme Supply Co", "po_number": "4500012345",
        "po_date": "2026-10-27", "requested_ship_date": "2026-11-10",
        "notes": "Deliver to dock 4. Call 24h before delivery.",
        "line_items": [{"line_number": 1, "part_number": "MR-4410", "quantity": 37,
                        "uom": "BX", "unit_price": 764.98}],
    },
}


def test_ready_po_maps_to_erp_order():
    order = build_sales_order(READY)
    assert order["customer_account"] == 70101            # from the customer master
    assert order["customer_po"] == "4500012345"
    assert order["lines"][0]["item_number"] == "ITM-10441"  # from the item cross-reference
    assert len(order["remark"]) <= REMARK_MAX


def test_needs_review_po_is_never_sent():
    with pytest.raises(NotReadyError):
        build_sales_order({**READY, "status": "NEEDS_REVIEW"})


def test_unknown_customer_is_rejected():
    po = {**READY["po"], "customer_name": "Unknown Corp"}
    with pytest.raises(NotReadyError, match="customer master"):
        build_sales_order({**READY, "po": po})


def test_mock_erp_creates_order_then_blocks_duplicate():
    erp = MockErp()
    order = build_sales_order(READY)
    first = erp.post_sales_order(order)
    second = erp.post_sales_order(order)
    assert first["status"] == "SUCCESS" and first["order_number"] == 5100001
    assert second["status"] == "DUPLICATE" and second["order_number"] == 5100001


def test_mock_erp_rejects_incomplete_payload():
    assert MockErp().post_sales_order({"customer_account": 70101})["status"] == "ERROR"
