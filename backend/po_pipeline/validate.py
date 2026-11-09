"""Steps B4 + B8: business rules, our own confidence score, and the READY / NEEDS_REVIEW decision.

The model's self-reported confidence is only one input: in B3 it said 0.95 even on a broken PO.
Deterministic rule checks decide whether a person must look at the PO.
All thresholds and rule severities come from the admin config (po_pipeline/config.py).
"""

from datetime import date

from po_pipeline.catalog import CATALOG
from po_pipeline.config import DEFAULT_CONFIG


def normalize_po(po: dict) -> dict:
    """Tidy values so rules compare like with like (e.g. OCR's 'mr-9001 ' -> 'MR-9001')."""
    po = {k: (v.strip() if isinstance(v, str) else v) for k, v in po.items()}
    for line in po.get("line_items", []):
        for key in ("part_number", "uom"):
            if isinstance(line.get(key), str):
                line[key] = line[key].strip().upper()
    return po


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def validate_po(po: dict, config: dict | None = None) -> dict:
    cfg = config or DEFAULT_CONFIG
    acc, rules, fields = cfg["accuracy"], cfg["rules"], cfg["fields"]
    po = normalize_po(po)
    issues = []

    def add(severity, field, message):
        if severity in ("warning", "error"):          # "off" / "optional" add nothing
            issues.append({"severity": severity, "field": field, "message": message})

    def missing_level(field):
        return {"required": "error", "important": "warning"}.get(fields.get(field, "optional"), "off")

    # Header rules: which fields must be present is set by the admin (required / important / optional)
    labels = {"po_number": "PO number", "customer_name": "Customer name",
              "bill_to": "Bill-to address", "buyer_contact": "Buyer contact"}
    for field, label in labels.items():
        if not po.get(field):
            add(missing_level(field), field, f"{label} is missing")
    po_date = _parse_date(po.get("po_date"))
    ship_date = _parse_date(po.get("requested_ship_date"))
    if not po_date:
        add(missing_level("po_date"), "po_date", "PO date is missing or not a valid date")
    if not ship_date:
        add(missing_level("requested_ship_date"), "requested_ship_date", "Requested ship date is missing")
    elif po_date and ship_date < po_date:
        add(rules["ship_date_before_po_date"], "requested_ship_date",
            f"Ship date {ship_date} is before PO date {po_date}")
    if not po.get("ship_to"):
        add(missing_level("ship_to"), "ship_to", "Ship-to address is missing")

    # Line rules
    lines = po.get("line_items") or []
    if not lines:
        add("error", "line_items", "PO has no line items")        # never optional
    for line in lines:
        ref = f"line {line.get('line_number')}"
        part = line.get("part_number")
        qty = line.get("quantity")
        price = line.get("unit_price")
        if not part:
            add("error", ref, "Part number is missing")           # never optional
        elif line.get("catalog_status") == "not_found" or part not in CATALOG:
            add(rules["unknown_part"], ref, f"Part {part} is not in the catalog")
        if qty is None or qty <= 0:
            add(rules["invalid_quantity"], ref, f"Quantity must be greater than 0 (got {qty})")
        item = CATALOG.get(part)
        if item:
            if line.get("uom") and line["uom"] != item["uom"]:
                add(rules["uom_mismatch"], ref, f"UOM {line['uom']} differs from catalog UOM {item['uom']}")
            if price is not None and rules["price_check"] != "off":
                gap = abs(price - item["list_price"]) / item["list_price"]
                if gap > acc["price_block_pct"]:
                    add("error", ref, f"Unit price {price} is {gap:.0%} from list price {item['list_price']}")
                elif gap > acc["price_warn_pct"]:
                    add(rules["price_check"], ref, f"Unit price {price} is {gap:.0%} from list price {item['list_price']}")
        if line.get("confidence", 1) < acc["low_model_confidence"]:
            add(rules["low_ai_confidence"], ref, f"AI was unsure about this line (confidence {line['confidence']})")

    for field, conf in (po.get("field_confidence") or {}).items():
        if conf < acc["low_model_confidence"] and field not in (po.get("missing_fields") or []):
            add(rules["low_ai_confidence"], field, f"AI was unsure about {field} (confidence {conf})")

    # Score: start from the AI's average confidence, then apply rule penalties
    model_scores = [l.get("confidence", 1) for l in lines] or [0]
    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = len(issues) - errors
    score = (sum(model_scores) / len(model_scores)) * (1 - acc["error_penalty"] * errors - acc["warning_penalty"] * warnings)
    score = round(max(0.0, min(1.0, score)), 2)

    status = "READY" if errors == 0 and score >= acc["auto_approve_threshold"] else "NEEDS_REVIEW"
    return {"status": status, "score": score, "errors": errors, "warnings": warnings,
            "issues": issues, "po": po}


if __name__ == "__main__":
    import sys

    from po_pipeline.agent import run_po_agent
    from po_pipeline.text_extract import extract_text

    agent_result = run_po_agent(extract_text(sys.argv[1]).text)
    result = validate_po(agent_result["po"])
    print(f"{sys.argv[1].split('/')[-1]}:  {result['status']}  score={result['score']}  "
          f"errors={result['errors']}  warnings={result['warnings']}  cost~${agent_result['estimated_cost_usd']}")
    for issue in result["issues"]:
        print(f"  [{issue['severity']}] {issue['field']}: {issue['message']}")
