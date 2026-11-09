"""Step B8: the admin-controlled settings for rules, accuracy and orchestration.

Defaults are exactly the values that used to be hard-coded, so behaviour only changes
when an admin saves a new version. Every version is kept, so any change can be rolled back.
"""

import copy
import json
import time
from decimal import Decimal

CURRENT_KEY = "CONFIG#current"
SEVERITIES = ("off", "warning", "error")
IMPORTANCE = ("required", "important", "optional")
# Must match the Bedrock permission in the infra stack: config can never exceed the IAM guardrail.
ALLOWED_MODELS = ("global.anthropic.claude-haiku-4-5-20251001-v1:0",)

DEFAULT_CONFIG = {
    "accuracy": {
        "auto_approve_threshold": 0.85,   # score needed to skip human review
        "error_penalty": 0.25,            # each error lowers the score by 25%
        "warning_penalty": 0.10,          # each warning lowers the score by 10%
        "low_model_confidence": 0.70,     # below this, the AI itself was unsure
        "price_warn_pct": 0.20,           # more than 20% from list price -> warning
        "price_block_pct": 0.50,          # more than 50% from list price -> error
    },
    "fields": {                           # required -> error if missing, important -> warning, optional -> ignored
        "po_number": "required",
        "customer_name": "required",
        "po_date": "required",
        "requested_ship_date": "important",
        "ship_to": "important",
        "bill_to": "optional",
        "buyer_contact": "optional",
    },
    "rules": {                            # off / warning / error
        "ship_date_before_po_date": "error",
        "unknown_part": "error",
        "invalid_quantity": "error",
        "uom_mismatch": "warning",
        "price_check": "warning",         # "off" disables both price tolerances
        "low_ai_confidence": "warning",
    },
    "orchestration": {
        "ocr_enabled": True,              # read scans with Textract
        "catalog_lookup_enabled": True,   # let the agent check parts in the catalog
        "auto_post_enabled": True,        # READY POs post to the ERP without a person
        "max_agent_turns": 6,
        "model_id": ALLOWED_MODELS[0],
        "daily_upload_limit": 20,         # S2: upload links handed out per day (UTC)
        "daily_processing_limit": 25,     # S2: POs sent to OCR/AI per day, however they arrived
    },
}


def _to_dynamo(data):
    """DynamoDB stores numbers as Decimal, not float."""
    return json.loads(json.dumps(data), parse_float=Decimal)


def _from_dynamo(data):
    """Turn DynamoDB Decimals back into int / float so the rules can do arithmetic."""
    return json.loads(json.dumps(data, default=lambda d: int(d) if d == d.to_integral_value() else float(d)))


def merged(stored: dict | None) -> dict:
    """Defaults overlaid with what is stored, so new settings get a default automatically."""
    result = copy.deepcopy(DEFAULT_CONFIG)
    for section, values in (stored or {}).items():
        if section in result and isinstance(values, dict):
            result[section].update(values)
    return result


def validate_config(cfg: dict) -> list[str]:
    """Return a list of problems; an empty list means the config is safe to save."""
    problems = []
    for section in DEFAULT_CONFIG:
        if not isinstance(cfg.get(section), dict):
            problems.append(f"Missing section '{section}'")
    if problems:
        return problems

    acc = cfg["accuracy"]
    for key in DEFAULT_CONFIG["accuracy"]:
        value = acc.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            problems.append(f"accuracy.{key} must be a number between 0 and 1")
    if not problems and acc["price_warn_pct"] > acc["price_block_pct"]:
        problems.append("accuracy.price_warn_pct must not be larger than price_block_pct")

    for field, level in cfg["fields"].items():
        if level not in IMPORTANCE:
            problems.append(f"fields.{field} must be one of {IMPORTANCE}")
    for rule, level in cfg["rules"].items():
        if rule not in DEFAULT_CONFIG["rules"]:
            problems.append(f"Unknown rule '{rule}'")
        elif level not in SEVERITIES:
            problems.append(f"rules.{rule} must be one of {SEVERITIES}")

    orch = cfg["orchestration"]
    for key in ("ocr_enabled", "catalog_lookup_enabled", "auto_post_enabled"):
        if not isinstance(orch.get(key), bool):
            problems.append(f"orchestration.{key} must be true or false")
    turns = orch.get("max_agent_turns")
    if not isinstance(turns, int) or isinstance(turns, bool) or not 1 <= turns <= 10:
        problems.append("orchestration.max_agent_turns must be a whole number from 1 to 10")
    for key in ("daily_upload_limit", "daily_processing_limit"):
        value = orch.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 200:
            problems.append(f"orchestration.{key} must be a whole number from 1 to 200")
    if orch.get("model_id") not in ALLOWED_MODELS:
        problems.append(f"orchestration.model_id must be one of {ALLOWED_MODELS}")
    return problems


def load_config(table) -> dict:
    """Current settings (defaults if an admin has never saved any)."""
    item = table.get_item(Key={"po_id": CURRENT_KEY}).get("Item") if table else None
    cfg = merged(_from_dynamo((item or {}).get("config")))
    cfg["version"] = int((item or {}).get("version", 0))
    return cfg


def save_config(table, new_cfg: dict, changed_by: str, note: str = "") -> dict:
    """Validate, then store as the new current version and as a history copy."""
    cfg = merged({k: v for k, v in new_cfg.items() if k in DEFAULT_CONFIG})
    problems = validate_config(cfg)
    if problems:
        raise ValueError("; ".join(problems))
    version = load_config(table)["version"] + 1
    record = {"version": version, "config": cfg, "changed_by": changed_by,
              "note": note[:200], "changed_at": int(time.time())}
    table.put_item(Item=_to_dynamo({"po_id": f"CONFIG#v{version:05d}", **record}))   # history copy
    table.put_item(Item=_to_dynamo({"po_id": CURRENT_KEY, **record}))                # what everyone reads
    return {**cfg, "version": version}
