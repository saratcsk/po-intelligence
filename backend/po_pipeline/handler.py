"""Steps B6 + B8: the AWS Lambda entry point.

S3 calls this function when a PO file is uploaded. For each file it:
download -> read text / OCR -> AI agent -> rules -> (READY only) mock ERP -> save result in DynamoDB.
Which steps run is controlled by the admin config (orchestration section).
"""

import json
import os
import time
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

from po_pipeline.agent import run_po_agent
from po_pipeline.config import load_config
from po_pipeline.extract_fields import extract_po_fields
from po_pipeline.erp import MockErp, NotReadyError, build_sales_order
from po_pipeline.limits import MAX_FILE_BYTES, take_slot
from po_pipeline.text_extract import IMAGE_TYPES, extract_pdf_text, extract_text
from po_pipeline.validate import validate_po


def _clients():
    """Created per call so tests can swap in fakes. Region and credentials come from Lambda."""
    return {
        "s3": boto3.client("s3"),
        "textract": boto3.client("textract"),
        "bedrock": boto3.client("bedrock-runtime"),
        "table": boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]),
    }


def _to_dynamo(data):
    """DynamoDB stores numbers as Decimal, not float."""
    return json.loads(json.dumps(data), parse_float=Decimal)


def _claim_erp_order(table, customer_account, customer_po) -> bool:
    """Idempotency: write a marker only if it does not exist yet. False = already posted."""
    try:
        table.put_item(Item={"po_id": f"ERP#{customer_account}#{customer_po}", "created_at": int(time.time())},
                       ConditionExpression="attribute_not_exists(po_id)")
        return True
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _read_text(local: Path, orch: dict, textract):
    """Return (text, method) or (None, reason) when OCR is needed but switched off."""
    if orch["ocr_enabled"]:
        result = extract_text(local, textract=textract)
        return result.text, result.method
    if local.suffix.lower() in IMAGE_TYPES:
        return None, "Image file needs OCR, which an admin has turned off"
    result = extract_pdf_text(local)
    if result.needs_ocr:
        return None, "Scanned PDF needs OCR, which an admin has turned off"
    return result.text, result.method


def _run_ai(text: str, orch: dict, bedrock) -> dict:
    """Agent with the catalog tool, or a single extraction call when the tool is switched off."""
    if orch["catalog_lookup_enabled"]:
        return run_po_agent(text, bedrock=bedrock, model_id=orch["model_id"], max_turns=orch["max_agent_turns"])
    result = extract_po_fields(text, bedrock=bedrock, model_id=orch["model_id"])
    return {**result, "trace": [], "turns": 1}


def process_file(bucket: str, key: str, clients: dict) -> dict:
    record = {"po_id": str(uuid.uuid4()), "s3_bucket": bucket, "s3_key": key,
              "created_at": int(time.time())}
    try:
        cfg = load_config(clients["table"])                 # admin settings, read on every PO
        orch = cfg["orchestration"]
        record["config_version"] = cfg["version"]

        local = Path("/tmp") / Path(key).name          # Lambda may only write to /tmp
        clients["s3"].download_file(bucket, key, str(local))

        # S2 cost guards, checked BEFORE any paid OCR or AI call
        if local.stat().st_size > MAX_FILE_BYTES:
            text, method = None, f"File is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB, so it was not processed"
        elif not take_slot(clients["table"], "processed", orch["daily_processing_limit"]):
            text, method = None, (f"Daily processing limit ({orch['daily_processing_limit']}) reached, so OCR "
                                  "and AI were skipped. An admin can raise the limit in Settings.")
        else:
            text, method = _read_text(local, orch, clients["textract"])
        if text is None:                                     # skip the AI: no text to read, no cost
            record.update(status="NEEDS_REVIEW", score=0, text_method="none",
                          issues=[{"severity": "error", "field": "document", "message": method}])
        else:
            agent = _run_ai(text, orch, clients["bedrock"])
            validation = validate_po(agent["po"], cfg)
            record.update(status=validation["status"], score=validation["score"],
                          issues=validation["issues"], po=validation["po"],
                          text_method=method, ai_cost_usd=agent["estimated_cost_usd"],
                          agent_trace=agent["trace"], model_id=agent["model_id"])

            if validation["status"] == "READY" and orch["auto_post_enabled"]:
                try:
                    order = build_sales_order(validation)
                    if _claim_erp_order(clients["table"], order["customer_account"], order["customer_po"]):
                        record["erp_response"] = MockErp().post_sales_order(order)
                        record["status"] = "POSTED"
                    else:
                        record["status"] = "DUPLICATE"
                    record["erp_payload"] = order
                except NotReadyError as err:  # e.g. customer not in address book
                    record.update(status="NEEDS_REVIEW",
                                  issues=validation["issues"] + [{"severity": "error", "field": "erp", "message": str(err)}])
            # READY with auto-post off stays READY: it waits for a CSR to approve it
    except Exception as err:  # never lose a PO: record the failure so a person can see it
        record.update(status="FAILED", error=f"{type(err).__name__}: {err}")

    clients["table"].put_item(Item=_to_dynamo(record))
    return {"po_id": record["po_id"], "s3_key": key, "status": record["status"]}


def lambda_handler(event, context=None):
    clients = _clients()
    results = []
    for rec in event.get("Records", []):
        bucket = rec["s3"]["bucket"]["name"]
        key = unquote_plus(rec["s3"]["object"]["key"])   # S3 sends "My PO.pdf" as "My+PO.pdf"
        results.append(process_file(bucket, key, clients))
        print(json.dumps(results[-1]))                     # goes to CloudWatch Logs
    return {"processed": results}
