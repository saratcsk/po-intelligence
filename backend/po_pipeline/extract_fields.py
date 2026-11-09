"""Step B3 (part 1): Claude turns PO text into structured fields.

We give Claude a "tool" whose input schema IS our PO format, and force Claude to call it.
Claude's tool-call arguments are then guaranteed JSON in exactly that shape.
"""

import json
import os

import boto3

# One setting decides the model. Switch to global.anthropic.claude-haiku-5-5 once its quota is approved.
MODEL_ID = os.environ.get("PO_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

# Haiku 4.5 prices in USD per 1 million tokens (input, output); used only to print an estimate.
PRICE_PER_MILLION = (1.00, 5.00)

SYSTEM_PROMPT = """You extract data from customer purchase orders (POs) sent to a manufacturer.
Rules:
- Copy values exactly as written in the document. Never invent or guess a value.
- If a field is missing or unreadable, use null and add the field name to missing_fields.
- Dates: convert to YYYY-MM-DD.
- Quantities and prices: numbers only (no $ or commas).
- confidence is 0.0-1.0: how sure you are the value is correct and complete.
  Use below 0.7 when text is blurry, ambiguous, or you had to interpret it.
- Pages are separated by "--- page N ---" markers; line items may continue across pages."""

PO_TOOL = {
    "name": "record_purchase_order",
    "description": "Record the fields extracted from one purchase order.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "customer_name": {"type": ["string", "null"], "description": "Company that sent the PO (the buyer / bill-to company)"},
            "po_number": {"type": ["string", "null"]},
            "po_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "requested_ship_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "buyer_contact": {"type": ["string", "null"]},
            "bill_to": {"type": ["string", "null"]},
            "ship_to": {"type": ["string", "null"]},
            "notes": {"type": ["string", "null"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line_number": {"type": ["integer", "null"]},
                        "part_number": {"type": ["string", "null"]},
                        "description": {"type": ["string", "null"]},
                        "quantity": {"type": ["number", "null"]},
                        "uom": {"type": ["string", "null"], "description": "Unit of measure, e.g. EA"},
                        "unit_price": {"type": ["number", "null"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["line_number", "part_number", "description", "quantity", "uom", "unit_price", "confidence"],
                },
            },
            "field_confidence": {
                "type": "object",
                "description": "Confidence 0.0-1.0 for each header field above",
                "additionalProperties": {"type": "number"},
            },
            "missing_fields": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["customer_name", "po_number", "po_date", "line_items", "field_confidence", "missing_fields"],
    }},
}


def extract_po_fields(po_text: str, bedrock=None, model_id: str = MODEL_ID) -> dict:
    """Send the PO text to Claude and return the structured PO plus token usage."""
    bedrock = bedrock or boto3.client("bedrock-runtime")
    response = bedrock.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": f"Extract this purchase order:\n\n{po_text}"}]}],
        toolConfig={"tools": [{"toolSpec": PO_TOOL}],
                    "toolChoice": {"tool": {"name": "record_purchase_order"}}},  # force the tool
        inferenceConfig={"maxTokens": 4096, "temperature": 0},  # 0 = same input, same answer
    )

    tool_call = next(block["toolUse"] for block in response["output"]["message"]["content"] if "toolUse" in block)
    usage = response["usage"]
    cost = usage["inputTokens"] / 1e6 * PRICE_PER_MILLION[0] + usage["outputTokens"] / 1e6 * PRICE_PER_MILLION[1]
    return {"po": tool_call["input"], "usage": usage, "estimated_cost_usd": round(cost, 6), "model_id": model_id}


if __name__ == "__main__":
    import sys

    from po_pipeline.text_extract import extract_text

    extracted = extract_text(sys.argv[1])
    result = extract_po_fields(extracted.text)
    print(json.dumps(result["po"], indent=2))
    print(f"\nmodel={result['model_id']}  text_method={extracted.method}  "
          f"tokens in/out={result['usage']['inputTokens']}/{result['usage']['outputTokens']}  "
          f"cost~${result['estimated_cost_usd']}")
