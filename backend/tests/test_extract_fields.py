from po_pipeline.extract_fields import PO_TOOL, extract_po_fields

SAMPLE_PO = {
    "customer_name": "Acme Supply Co",
    "po_number": "4500012345",
    "po_date": "2026-10-27",
    "line_items": [{"line_number": 1, "part_number": "MR-4410", "description": "Stainless Bolt Kit 1000pc",
                    "quantity": 37, "uom": "BX", "unit_price": 764.98, "confidence": 0.98}],
    "field_confidence": {"po_number": 0.99},
    "missing_fields": [],
}


class FakeBedrock:
    """Stands in for Bedrock: records the request and returns a canned tool call. No AWS, no cost."""

    def __init__(self):
        self.request = None

    def converse(self, **request):
        self.request = request
        return {
            "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "t1", "name": "record_purchase_order", "input": SAMPLE_PO}}]}},
            "usage": {"inputTokens": 2000, "outputTokens": 400, "totalTokens": 2400},
            "stopReason": "tool_use",
        }


def test_returns_the_tool_input_as_the_po():
    result = extract_po_fields("PO text", bedrock=FakeBedrock())
    assert result["po"]["po_number"] == "4500012345"
    assert result["po"]["line_items"][0]["quantity"] == 37


def test_claude_is_forced_to_use_the_po_tool():
    fake = FakeBedrock()
    extract_po_fields("PO text", bedrock=fake)
    assert fake.request["toolConfig"]["toolChoice"] == {"tool": {"name": "record_purchase_order"}}
    assert fake.request["inferenceConfig"]["temperature"] == 0


def test_cost_estimate_uses_token_counts():
    result = extract_po_fields("PO text", bedrock=FakeBedrock())
    assert result["estimated_cost_usd"] == 0.004  # 2000*$1/M + 400*$5/M


def test_schema_requires_confidence_on_every_line():
    line_schema = PO_TOOL["inputSchema"]["json"]["properties"]["line_items"]["items"]
    assert "confidence" in line_schema["required"]
