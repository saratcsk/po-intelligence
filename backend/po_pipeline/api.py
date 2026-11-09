"""Step B7: the Review API used by the web page (CSR screen).

Routes (API Gateway HTTP API, payload v2):
  GET  /pos                 list POs, newest first
  GET  /pos/{id}            one PO, plus a short-lived link to view its document
  PUT  /pos/{id}            CSR edits fields -> rules run again -> new status
  POST /pos/{id}/approve    CSR approves -> mock ERP (only if no rule errors)
  POST /pos/{id}/reject     CSR rejects with a reason
  POST /uploads             short-lived link the browser uses to upload a PO into incoming/

Step S1:
  GET  /me                  which role this token has (admin, csr or viewer)

Who may call what (checked here, on the server, for every request):
  viewer - read only: GET /pos, GET /pos/{id}, GET /me   (the public demo)
  csr    - everything above, plus edit, approve, reject and upload
  admin  - everything, including the settings routes below

Step B8 (part 3), admin only:
  GET  /config              current settings
  PUT  /config              change settings (validated, saved as a new version)
  GET  /config/history      every saved version: who, when, why
  POST /config/rollback     make an older version current again (saved as a new version)
"""

import json
import os
import re
import time
from decimal import Decimal

import boto3
from botocore.config import Config

from po_pipeline.config import DEFAULT_CONFIG, load_config, save_config
from po_pipeline.handler import _claim_erp_order, _to_dynamo
from po_pipeline.erp import MockErp, NotReadyError, build_sales_order
from po_pipeline.limits import MAX_FILE_BYTES, take_slot
from po_pipeline.validate import validate_po

LINK_SECONDS = 300                            # presigned links expire after 5 minutes
ALLOWED_UPLOADS = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
FINAL_STATUSES = {"POSTED", "DUPLICATE", "REJECTED"}
INTERNAL_PREFIXES = ("ERP#", "CONFIG#", "LIMIT#")   # bookkeeping rows, not POs


def _clients():
    region = os.environ.get("AWS_REGION", "us-east-2")
    # Regional S3 address, so browser uploads go straight to the bucket's region (no redirects).
    s3 = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                      config=Config(s3={"addressing_style": "virtual"}))
    return {"s3": s3,
            "table": boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]),
            "bucket": os.environ["BUCKET_NAME"]}


def _plain(value):
    """DynamoDB returns Decimal; turn it back into int/float for JSON and for our rules."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _reply(status_code: int, body) -> dict:
    return {"statusCode": status_code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def _load(clients, po_id):
    item = clients["table"].get_item(Key={"po_id": po_id}).get("Item")
    return _plain(item) if item else None


def _save(clients, item, action, detail=""):
    item.setdefault("history", []).append({"at": int(time.time()), "action": action, "detail": detail})
    clients["table"].put_item(Item=_to_dynamo(item))   # audit trail kept on the record


# ---------- route handlers ----------

def _scan_all(clients):
    items, kwargs = [], {}
    while True:                                          # scan returns pages of up to 1 MB
        page = clients["table"].scan(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return items


def list_pos(clients, event):
    pos = [_plain(i) for i in _scan_all(clients) if not i["po_id"].startswith(INTERNAL_PREFIXES)]
    pos.sort(key=lambda i: i.get("created_at", 0), reverse=True)
    summary = [{"po_id": p["po_id"], "status": p.get("status"), "score": p.get("score"),
                "s3_key": p.get("s3_key"), "created_at": p.get("created_at"),
                "customer_name": (p.get("po") or {}).get("customer_name"),
                "po_number": (p.get("po") or {}).get("po_number"),
                "issue_count": len(p.get("issues") or [])} for p in pos]
    return _reply(200, {"items": summary})


def get_po(clients, event, po_id):
    item = _load(clients, po_id)
    if not item:
        return _reply(404, {"message": "PO not found"})
    item["document_url"] = clients["s3"].generate_presigned_url(
        "get_object", Params={"Bucket": clients["bucket"], "Key": item["s3_key"]}, ExpiresIn=LINK_SECONDS)
    return _reply(200, item)


def update_po(clients, event, po_id):
    item = _load(clients, po_id)
    if not item:
        return _reply(404, {"message": "PO not found"})
    if item.get("status") in FINAL_STATUSES:
        return _reply(409, {"message": f"PO is already {item['status']}"})
    edited = json.loads(event.get("body") or "{}").get("po")
    if not isinstance(edited, dict):
        return _reply(400, {"message": "Body must be {\"po\": {...}}"})
    validation = validate_po(edited, load_config(clients["table"]))   # same rules as the pipeline
    item.update(po=validation["po"], status=validation["status"], score=validation["score"],
                issues=validation["issues"])
    _save(clients, item, "edited", f"now {validation['status']}")
    return _reply(200, item)


def approve_po(clients, event, po_id):
    item = _load(clients, po_id)
    if not item:
        return _reply(404, {"message": "PO not found"})
    if item.get("status") in FINAL_STATUSES:
        return _reply(409, {"message": f"PO is already {item['status']}"})
    validation = validate_po(item.get("po") or {}, load_config(clients["table"]))
    if validation["errors"]:                             # a person may accept warnings, never errors
        return _reply(422, {"message": "Fix the errors before approving", "issues": validation["issues"]})
    try:
        order = build_sales_order({**validation, "status": "READY"})   # CSR approval overrides the score
    except NotReadyError as err:
        return _reply(422, {"message": str(err)})
    if _claim_erp_order(clients["table"], order["customer_account"], order["customer_po"]):
        item.update(status="POSTED", erp_payload=order, erp_response=MockErp().post_sales_order(order))
    else:
        item["status"] = "DUPLICATE"
    _save(clients, item, "approved", item["status"])
    return _reply(200, item)


def reject_po(clients, event, po_id):
    item = _load(clients, po_id)
    if not item:
        return _reply(404, {"message": "PO not found"})
    if item.get("status") in FINAL_STATUSES:
        return _reply(409, {"message": f"PO is already {item['status']}"})
    reason = (json.loads(event.get("body") or "{}").get("reason") or "").strip()
    if not reason:
        return _reply(400, {"message": "A reason is required"})
    item.update(status="REJECTED", reject_reason=reason[:500])
    _save(clients, item, "rejected", reason[:500])
    return _reply(200, item)


def create_upload(clients, event):
    filename = (json.loads(event.get("body") or "{}").get("filename") or "").strip()
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_UPLOADS:
        return _reply(400, {"message": "Only PDF, PNG or JPG files can be uploaded"})
    limit = load_config(clients["table"])["orchestration"]["daily_upload_limit"]
    if not take_slot(clients["table"], "uploads", limit):              # S2: daily cap
        return _reply(429, {"message": f"Daily upload limit ({limit}) reached. Please try again tomorrow."})
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[:100]
    key = f"incoming/{int(time.time())}-{safe_name}"
    content_type = ALLOWED_UPLOADS[extension]
    # A presigned POST (not PUT) lets S3 itself enforce the size limit and the file type.
    post = clients["s3"].generate_presigned_post(
        Bucket=clients["bucket"], Key=key, ExpiresIn=LINK_SECONDS,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_FILE_BYTES]])
    return _reply(200, {"upload_url": post["url"], "fields": post["fields"], "s3_key": key,
                        "content_type": content_type, "max_bytes": MAX_FILE_BYTES})


# ---------- admin: settings ----------

def get_config(clients, event):
    return _reply(200, load_config(clients["table"]))


def put_config(clients, event):
    body = json.loads(event.get("body") or "{}")
    changes = body.get("config")
    if not isinstance(changes, dict) or not changes:
        return _reply(400, {"message": "Body must be {\"config\": {...}, \"note\": \"why\"}"})
    unknown = [s for s in changes if s not in DEFAULT_CONFIG]
    if unknown:
        return _reply(400, {"message": f"Unknown settings section(s): {unknown}"})
    cfg = load_config(clients["table"])                  # start from what is live now...
    for section, values in changes.items():              # ...and apply only what the admin changed
        if not isinstance(values, dict):
            return _reply(400, {"message": f"'{section}' must be an object"})
        cfg[section].update(values)
    try:
        saved = save_config(clients["table"], cfg, "admin", str(body.get("note") or ""))
    except ValueError as err:                            # bad values are refused, nothing is saved
        return _reply(400, {"message": "Settings not saved", "problems": str(err).split("; ")})
    return _reply(200, saved)


def config_history(clients, event):
    versions = [_plain(i) for i in _scan_all(clients)
                if i["po_id"].startswith("CONFIG#v")]
    versions.sort(key=lambda v: v["version"], reverse=True)
    current = load_config(clients["table"])["version"]
    return _reply(200, {"current_version": current,
                        "items": [{k: v.get(k) for k in ("version", "changed_by", "changed_at", "note")}
                                  for v in versions]})


def rollback_config(clients, event):
    version = json.loads(event.get("body") or "{}").get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return _reply(400, {"message": "Body must be {\"version\": <number>}"})
    old = clients["table"].get_item(Key={"po_id": f"CONFIG#v{version:05d}"}).get("Item")
    if not old:
        return _reply(404, {"message": f"Version {version} not found"})
    # History only moves forward: the old settings are saved again as a NEW version.
    saved = save_config(clients["table"], _plain(old["config"]), "admin", f"rollback to v{version}")
    return _reply(200, saved)


def who_am_i(clients, event):
    return _reply(200, {"role": _role(event)})


ADMIN_ROUTES = {"GET /config", "PUT /config", "GET /config/history", "POST /config/rollback"}
READ_ONLY_ROUTES = {"GET /pos", "GET /pos/{id}", "GET /me"}      # all a viewer may do

ROUTES = {
    "GET /pos": list_pos,
    "GET /pos/{id}": get_po,
    "PUT /pos/{id}": update_po,
    "POST /pos/{id}/approve": approve_po,
    "POST /pos/{id}/reject": reject_po,
    "POST /uploads": create_upload,
    "GET /config": get_config,
    "PUT /config": put_config,
    "GET /config/history": config_history,
    "POST /config/rollback": rollback_config,
    "GET /me": who_am_i,
}


def _role(event) -> str | None:
    """The role the authorizer attached to this request ("admin" or "csr")."""
    return ((event.get("requestContext") or {}).get("authorizer") or {}).get("lambda", {}).get("role")


def lambda_handler(event, context=None):
    route = ROUTES.get(event.get("routeKey"))
    if not route:
        return _reply(404, {"message": "Unknown route"})
    role = _role(event)
    if event["routeKey"] in ADMIN_ROUTES and role != "admin":
        return _reply(403, {"message": "Admin access required"})   # valid token, wrong role
    if event["routeKey"] not in READ_ONLY_ROUTES and role not in ("csr", "admin"):
        return _reply(403, {"message": "This is a read-only demo account"})   # viewers can only look
    clients = _clients()
    po_id = (event.get("pathParameters") or {}).get("id")
    try:
        return route(clients, event, po_id) if po_id else route(clients, event)
    except json.JSONDecodeError:
        return _reply(400, {"message": "Body must be valid JSON"})
