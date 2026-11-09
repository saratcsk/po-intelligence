import shutil
from pathlib import Path

from botocore.exceptions import ClientError

from po_pipeline import handler

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "synthetic"

ACME_PO = {
    "customer_name": "Acme Supply Co", "po_number": "4500012345",
    "po_date": "2026-10-27", "requested_ship_date": "2026-11-10", "ship_to": "Warehouse 2",
    "line_items": [{"line_number": 1, "part_number": "MR-4410", "description": "Bolt Kit",
                    "quantity": 37, "uom": "BX", "unit_price": 764.98, "confidence": 0.95,
                    "catalog_status": "found"}],
    "field_confidence": {}, "missing_fields": [],
}


class FakeS3:
    def download_file(self, bucket, key, path):
        shutil.copy(SAMPLES / Path(key).name, path)


class FakeBedrock:
    def __init__(self, po):
        self.po = po

    def converse(self, **request):
        return {"output": {"message": {"role": "assistant", "content": [
                    {"toolUse": {"toolUseId": "t", "name": "record_purchase_order", "input": self.po}}]}},
                "usage": {"inputTokens": 1000, "outputTokens": 200}}


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):                      # NEW: the handler now reads CONFIG#current
        item = self.items.get(Key["po_id"])
        return {"Item": item} if item else {}

    def update_item(self, Key, ExpressionAttributeValues, **kwargs):   # S2: daily counters
        row = self.items.setdefault(Key["po_id"], {"po_id": Key["po_id"], "used": 0})
        if row["used"] >= ExpressionAttributeValues[":limit"]:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        row["used"] += 1

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and Item["po_id"] in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[Item["po_id"]] = Item


def s3_event(key):
    return {"Records": [{"s3": {"bucket": {"name": "po-bucket"}, "object": {"key": key}}}]}


def fake_clients(monkeypatch, po):
    table = FakeTable()
    monkeypatch.setattr(handler, "_clients", lambda: {
        "s3": FakeS3(), "textract": None, "bedrock": FakeBedrock(po), "table": table})
    return table


def saved_pos(table):                            # NEW: skip config rows as well as ERP markers
    return [i for i in table.items.values() if not i["po_id"].startswith(("ERP#", "CONFIG#", "LIMIT#"))]


def test_ready_po_is_posted_and_saved(monkeypatch):
    table = fake_clients(monkeypatch, ACME_PO)
    result = handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))
    assert result["processed"][0]["status"] == "POSTED"
    item = saved_pos(table)[0]
    assert item["erp_response"]["status"] == "SUCCESS"
    assert "ERP#70101#4500012345" in table.items            # idempotency marker written


def test_same_po_uploaded_twice_is_not_posted_twice(monkeypatch):
    table = fake_clients(monkeypatch, ACME_PO)
    handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))
    second = handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))
    assert second["processed"][0]["status"] == "DUPLICATE"


def test_bad_po_is_saved_for_review(monkeypatch):
    table = fake_clients(monkeypatch, {**ACME_PO, "po_number": None})
    result = handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))
    assert result["processed"][0]["status"] == "NEEDS_REVIEW"
    assert saved_pos(table)[0]["issues"]


def test_crash_is_recorded_not_lost(monkeypatch):
    table = fake_clients(monkeypatch, ACME_PO)
    result = handler.lambda_handler(s3_event("incoming/does-not-exist.pdf"))
    assert result["processed"][0]["status"] == "FAILED"
    assert "error" in saved_pos(table)[0]


def test_s3_key_with_spaces_is_decoded(monkeypatch):
    fake_clients(monkeypatch, ACME_PO)
    result = handler.lambda_handler(s3_event("incoming/My+PO.pdf"))
    assert result["processed"][0]["s3_key"] == "incoming/My PO.pdf"


def test_saved_item_has_no_floats_for_dynamodb(monkeypatch):
    table = fake_clients(monkeypatch, ACME_PO)
    handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))

    def has_float(value):
        if isinstance(value, float):
            return True
        if isinstance(value, dict):
            return any(has_float(v) for v in value.values())
        if isinstance(value, list):
            return any(has_float(v) for v in value)
        return False

    assert not has_float(saved_pos(table)[0])   # real DynamoDB rejects Python floats
