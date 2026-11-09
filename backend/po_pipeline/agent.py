"""Step B3 (part 2): an AI agent that checks part numbers before recording the PO.

The loop:  Claude thinks -> calls a tool -> we run it and send back the result -> repeat,
until Claude calls record_purchase_order (the final answer) or we hit MAX_TURNS.
"""

import copy
import json

import boto3

from po_pipeline.catalog import lookup_part
from po_pipeline.extract_fields import MODEL_ID, PO_TOOL, PRICE_PER_MILLION, SYSTEM_PROMPT

MAX_TURNS = 6  # safety limit: stops a confused agent from looping (and spending) forever

LOOKUP_TOOL = {
    "name": "lookup_part",
    "description": "Look up one part number in the product catalog. Returns found=true with the "
                   "catalog description, UOM and list price, or found=false with similar part numbers.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"part_number": {"type": "string"}},
        "required": ["part_number"],
    }},
}

# The final-answer tool: same PO format as part 1, plus a catalog check result on every line.
AGENT_PO_TOOL = copy.deepcopy(PO_TOOL)
_line = AGENT_PO_TOOL["inputSchema"]["json"]["properties"]["line_items"]["items"]
_line["properties"]["catalog_status"] = {
    "type": "string", "enum": ["found", "not_found"],
    "description": "Result of lookup_part for this line's part number",
}
_line["required"].append("catalog_status")

AGENT_PROMPT = SYSTEM_PROMPT + """
You are a PO-processing agent with tools.
1. Call lookup_part once for every distinct part number on the PO (you may call it several times in one turn).
2. Never change a part number to match the catalog; report catalog_status = not_found instead.
3. When every part has been checked, call record_purchase_order exactly once with the final result."""


def _run_tool(name: str, args: dict) -> dict:
    if name == "lookup_part":
        return lookup_part(args.get("part_number", ""))
    return {"error": f"unknown tool {name}"}


def run_po_agent(po_text: str, bedrock=None, model_id: str = MODEL_ID, max_turns: int | None = None) -> dict:
    limit = max_turns or MAX_TURNS   # the admin config can lower or raise the turn limit
    bedrock = bedrock or boto3.client("bedrock-runtime")
    messages = [{"role": "user", "content": [{"text": f"Process this purchase order:\n\n{po_text}"}]}]
    trace = []                      # audit trail: every tool call the agent made
    tokens = {"inputTokens": 0, "outputTokens": 0}

    for turn in range(1, limit + 1):
        response = bedrock.converse(
            modelId=model_id,
            system=[{"text": AGENT_PROMPT}],
            messages=messages,
            toolConfig={"tools": [{"toolSpec": LOOKUP_TOOL}, {"toolSpec": AGENT_PO_TOOL}],
                        "toolChoice": {"any": {}}},  # must call a tool each turn, but Claude chooses which
            inferenceConfig={"maxTokens": 4096, "temperature": 0},
        )
        for k in tokens:
            tokens[k] += response["usage"][k]

        message = response["output"]["message"]
        messages.append(message)
        tool_calls = [block["toolUse"] for block in message["content"] if "toolUse" in block]

        for call in tool_calls:
            if call["name"] == "record_purchase_order":   # final answer: stop the loop
                trace.append({"turn": turn, "tool": call["name"]})
                cost = tokens["inputTokens"] / 1e6 * PRICE_PER_MILLION[0] + tokens["outputTokens"] / 1e6 * PRICE_PER_MILLION[1]
                return {"po": call["input"], "trace": trace, "turns": turn, "usage": tokens,
                        "estimated_cost_usd": round(cost, 6), "model_id": model_id}

        results = []
        for call in tool_calls:
            output = _run_tool(call["name"], call["input"])
            trace.append({"turn": turn, "tool": call["name"], "input": call["input"], "output": output})
            results.append({"toolResult": {"toolUseId": call["toolUseId"], "content": [{"json": output}]}})
        messages.append({"role": "user", "content": results})

    raise RuntimeError(f"Agent did not finish within {limit} turns")


if __name__ == "__main__":
    import sys

    from po_pipeline.text_extract import extract_text

    extracted = extract_text(sys.argv[1])
    result = run_po_agent(extracted.text)
    print("Agent trace:")
    for step in result["trace"]:
        detail = f"{step['input']['part_number']} -> found={step['output']['found']}" if step["tool"] == "lookup_part" else "final answer"
        print(f"  turn {step['turn']}: {step['tool']}  {detail}")
    print("\nLines:")
    for line in result["po"]["line_items"]:
        print(f"  {line['line_number']}: {line['part_number']}  qty={line['quantity']}  catalog={line['catalog_status']}")
    print(f"\nPO number={result['po']['po_number']}  turns={result['turns']}  "
          f"tokens in/out={result['usage']['inputTokens']}/{result['usage']['outputTokens']}  cost~${result['estimated_cost_usd']}")
