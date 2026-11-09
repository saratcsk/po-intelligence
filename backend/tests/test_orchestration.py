import copy

from po_pipeline import handler
from po_pipeline.config import DEFAULT_CONFIG, save_config
from tests.test_handler import ACME_PO, FakeS3, FakeTable, s3_event, saved_pos


class CountingBedrock:
    """Returns the final PO straight away and remembers every request."""

    def __init__(self):
        self.requests = []

    def converse(self, **request):
        self.requests.append(request)
        return {"output": {"message": {"role": "assistant", "content": [
                    {"toolUse": {"toolUseId": "t", "name": "record_purchase_order", "input": ACME_PO}}]}},
                "usage": {"inputTokens": 1000, "outputTokens": 200}}


def setup(monkeypatch, **orchestration):
    table, bedrock = FakeTable(), CountingBedrock()
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["orchestration"].update(orchestration)
    save_config(table, cfg, "admin", "test")
    monkeypatch.setattr(handler, "_clients", lambda: {
        "s3": FakeS3(), "textract": None, "bedrock": bedrock, "table": table})
    return table, bedrock


def test_auto_post_off_leaves_ready_po_waiting_for_a_person(monkeypatch):
    table, _ = setup(monkeypatch, auto_post_enabled=False)
    result = handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))
    assert result["processed"][0]["status"] == "READY"
    assert not any(k.startswith("ERP#") for k in table.items)       # nothing sent to the ERP
    assert saved_pos(table)[0]["config_version"] == 1               # which settings were used


def test_ocr_off_sends_scans_to_review_without_calling_ai(monkeypatch):
    table, bedrock = setup(monkeypatch, ocr_enabled=False)
    result = handler.lambda_handler(s3_event("incoming/PO-1003_contoso_scanned.pdf"))
    assert result["processed"][0]["status"] == "NEEDS_REVIEW"
    assert bedrock.requests == []                                    # no AI cost
    assert "OCR" in saved_pos(table)[0]["issues"][0]["message"]


def test_catalog_tool_off_uses_single_extraction_call(monkeypatch):
    _, bedrock = setup(monkeypatch, catalog_lookup_enabled=False)
    handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))
    tools = [t["toolSpec"]["name"] for t in bedrock.requests[0]["toolConfig"]["tools"]]
    assert tools == ["record_purchase_order"]                       # no lookup_part tool offered


def test_turn_limit_comes_from_config(monkeypatch):
    seen = {}
    monkeypatch.setattr(handler, "run_po_agent",
                        lambda text, **kw: seen.update(kw) or {"po": ACME_PO, "trace": [], "turns": 1,
                                                               "estimated_cost_usd": 0, "model_id": kw["model_id"]})
    setup(monkeypatch, max_agent_turns=3)
    handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))
    assert seen["max_turns"] == 3
