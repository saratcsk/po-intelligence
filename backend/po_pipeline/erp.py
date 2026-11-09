"""Step B5: turn a READY PO into a sales order and post it to a mock ERP.

The ERP is a stand-in: a small class that behaves like a typical ERP sales-order API
(customer account, plant, order lines, duplicate check). A real connector for any ERP
would replace MockErp without changing the rest of the pipeline. All data here is made up.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

PLANT = "P100"   # the single plant / business unit in this demo
REMARK_MAX = 30  # many ERPs limit the order remark field, so we trim to a safe length

# Made-up customer master: customer name -> ERP customer account number.
CUSTOMERS = {
    "ACME SUPPLY CO": 70101,
    "NORTHWIND HVAC DISTRIBUTORS": 70102,
    "CONTOSO MECHANICAL LLC": 70103,
    "FABRIKAM BUILDING SERVICES": 70104,
}

# Made-up item cross-reference: customer's part number -> our ERP item number.
ITEM_XREF = {
    "MR-2001": "ITM-10201", "MR-3150": "ITM-10315", "MR-4410": "ITM-10441",
    "MR-5022": "ITM-10502", "MR-7311": "ITM-10731", "MR-8120": "ITM-10812",
    "MR-9001": "ITM-10900",
}


class NotReadyError(ValueError):
    """Raised when a PO cannot be turned into an ERP sales order."""


def build_sales_order(validation: dict) -> dict:
    """Map a validated PO (output of validate_po) to an ERP sales-order request."""
    if validation["status"] != "READY":
        raise NotReadyError("Only READY purchase orders can be sent to the ERP")

    po = validation["po"]
    account = CUSTOMERS.get((po.get("customer_name") or "").strip().upper())
    if account is None:
        raise NotReadyError(f"Customer '{po.get('customer_name')}' is not in the customer master")

    lines = []
    for line in po["line_items"]:
        item = ITEM_XREF.get(line["part_number"])
        if item is None:
            raise NotReadyError(f"No ERP item cross-reference for {line['part_number']}")
        lines.append({
            "line_number": line["line_number"],
            "item_number": item,
            "customer_part": line["part_number"],
            "quantity": line["quantity"],
            "uom": line["uom"],
            "unit_price": line["unit_price"],
        })

    return {
        "plant": PLANT,
        "customer_account": account,
        "ship_to_account": account,    # demo: ship-to account = sold-to account
        "customer_po": po["po_number"],
        "order_date": po["po_date"],
        "requested_date": po.get("requested_ship_date"),
        "remark": (po.get("notes") or "")[:REMARK_MAX],
        "lines": lines,
    }


class MockErp:
    """Pretends to be an ERP sales-order API. Remembers orders so duplicates are caught."""

    def __init__(self, first_order_number: int = 5100001):
        self.next_number = first_order_number
        self.orders = {}  # (customer_account, customer_po) -> order number

    def post_sales_order(self, payload: dict) -> dict:
        required = ["plant", "customer_account", "customer_po", "order_date", "lines"]
        missing = [f for f in required if not payload.get(f)]
        if missing:
            return {"status": "ERROR", "message": f"Missing fields: {missing}"}

        key = (payload["customer_account"], payload["customer_po"])
        if key in self.orders:  # same customer PO sent twice: do not create a second order
            return {"status": "DUPLICATE", "order_number": self.orders[key], "order_type": "SO"}

        number = self.next_number
        self.next_number += 1
        self.orders[key] = number
        return {"status": "SUCCESS", "order_number": number, "order_type": "SO",
                "lines_created": len(payload["lines"]),
                "completed_at": datetime.now(timezone.utc).isoformat()}


def save_json(data: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return path


if __name__ == "__main__":
    import sys

    from po_pipeline.agent import run_po_agent
    from po_pipeline.text_extract import extract_text
    from po_pipeline.validate import validate_po

    source = Path(sys.argv[1])
    validation = validate_po(run_po_agent(extract_text(source).text)["po"])
    print(f"{source.name}: {validation['status']}  score={validation['score']}")

    if validation["status"] != "READY":
        for issue in validation["issues"]:
            print(f"  [{issue['severity']}] {issue['field']}: {issue['message']}")
        print("  -> sent to CSR review queue, not posted to the ERP")
        sys.exit(0)

    order = build_sales_order(validation)
    out = save_json(order, Path("out") / f"{source.stem}.erp.json")
    erp = MockErp()
    print(f"  ERP payload saved to {out}")
    print(f"  ERP response: {erp.post_sales_order(order)}")
    print(f"  Posted again: {erp.post_sales_order(order)}")
