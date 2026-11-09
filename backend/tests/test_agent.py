import pytest

from po_pipeline import agent
from po_pipeline.agent import run_po_agent
from po_pipeline.catalog import lookup_part


def tool_use(tool_id, name, args):
    return {"toolUse": {"toolUseId": tool_id, "name": name, "input": args}}


def reply(*blocks):
    return {"output": {"message": {"role": "assistant", "content": list(blocks)}},
            "usage": {"inputTokens": 1000, "outputTokens": 100}, "stopReason": "tool_use"}


FINAL_PO = {"customer_name": "Fabrikam", "po_number": None, "po_date": "2026-10-28",
            "line_items": [{"line_number": 1, "part_number": "MR-6008", "description": "Hydraulic Oil",
                            "quantity": 4, "uom": "GAL", "unit_price": 211.41, "confidence": 0.9,
                            "catalog_status": "not_found"}],
            "field_confidence": {}, "missing_fields": ["po_number"]}


class ScriptedBedrock:
    """Plays back a fixed list of Claude replies and records what the agent sent each turn."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def converse(self, **request):
        self.requests.append(request)
        return self.replies.pop(0)


def test_catalog_finds_known_part_and_suggests_for_unknown():
    assert lookup_part("mr-9001")["found"] is True
    missing = lookup_part("MR-6008")
    assert missing["found"] is False and "MR-2001" in missing["similar_parts"]


def test_agent_runs_tool_then_finishes():
    fake = ScriptedBedrock([
        reply(tool_use("a", "lookup_part", {"part_number": "MR-6008"})),   # turn 1: check a part
        reply(tool_use("b", "record_purchase_order", FINAL_PO)),             # turn 2: final answer
    ])
    result = run_po_agent("PO text", bedrock=fake)

    assert result["turns"] == 2
    assert result["po"]["line_items"][0]["catalog_status"] == "not_found"
    assert result["trace"][0]["output"]["found"] is False           # audit trail kept
    tool_result = fake.requests[1]["messages"][2]["content"][0]["toolResult"]  # user, Claude, tool result
    assert tool_result["toolUseId"] == "a"                           # result sent back to Claude
    assert result["usage"] == {"inputTokens": 2000, "outputTokens": 200}


def test_agent_stops_after_max_turns(monkeypatch):
    monkeypatch.setattr(agent, "MAX_TURNS", 2)
    fake = ScriptedBedrock([reply(tool_use(str(i), "lookup_part", {"part_number": "MR-9001"})) for i in range(2)])
    with pytest.raises(RuntimeError):
        run_po_agent("PO text", bedrock=fake)
