import json
from decimal import Decimal

import pytest

from po_pipeline import api
from tests.test_handler import ACME_PO, FakeTable


class FakeTableWithReads(FakeTable):
    def get_item(self, Key):
        item = self.items.get(Key["po_id"])
        return {"Item": item} if item else {}

    def scan(self, **kwargs):
        return {"Items": list(self.items.values())}


class FakeS3:
    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://example.test/{operation}/{Params['Key']}?expires={ExpiresIn}"

    def generate_presigned_post(self, Bucket, Key, ExpiresIn, Fields, Conditions):
        return {"url": f"https://example.test/{Bucket}", "fields": {**Fields, "key": Key},
                "conditions": Conditions}


@pytest.fixture
def table(monkeypatch):
    t = FakeTableWithReads()
    monkeypatch.setattr(api, "_clients", lambda: {"s3": FakeS3(), "table": t, "bucket": "po-bucket"})
    return t


def add_po(table, po_id="po-1", status="NEEDS_REVIEW", po=None, created_at=100):
    table.items[po_id] = {"po_id": po_id, "status": status, "s3_key": f"incoming/{po_id}.pdf",
                          "created_at": Decimal(created_at), "po": po or {**ACME_PO, "po_number": None},
                          "issues": [{"severity": "error", "field": "po_number", "message": "missing"}]}


def call(route, po_id=None, body=None, role="csr"):
    event = {"routeKey": route, "pathParameters": {"id": po_id} if po_id else None,
             "body": json.dumps(body) if body is not None else None,
             "requestContext": {"authorizer": {"lambda": {"role": role}}}}
    response = api.lambda_handler(event)
    return response["statusCode"], json.loads(response["body"])


def test_list_hides_erp_markers_and_sorts_newest_first(table):
    add_po(table, "old", created_at=100)
    add_po(table, "new", created_at=200)
    table.items["ERP#70101#X"] = {"po_id": "ERP#70101#X"}
    code, body = call("GET /pos")
    assert code == 200 and [p["po_id"] for p in body["items"]] == ["new", "old"]


def test_get_includes_short_lived_document_link(table):
    add_po(table)
    code, body = call("GET /pos/{id}", "po-1")
    assert code == 200 and "get_object/incoming/po-1.pdf" in body["document_url"]
    assert call("GET /pos/{id}", "missing")[0] == 404


def test_edit_reruns_rules_and_keeps_history(table):
    add_po(table)
    code, body = call("PUT /pos/{id}", "po-1", {"po": ACME_PO})   # CSR fills in the PO number
    assert code == 200 and body["status"] == "READY" and body["issues"] == []
    assert body["history"][-1]["action"] == "edited"


def test_approve_is_blocked_while_errors_remain(table):
    add_po(table)
    code, body = call("POST /pos/{id}/approve", "po-1")
    assert code == 422 and "Fix the errors" in body["message"]


def test_approve_posts_to_erp_once(table):
    add_po(table, po=ACME_PO)
    code, body = call("POST /pos/{id}/approve", "po-1")
    assert code == 200 and body["status"] == "POSTED"
    assert body["erp_response"]["status"] == "SUCCESS"
    assert body["erp_response"]["order_number"] == 5100001
    assert call("POST /pos/{id}/approve", "po-1")[0] == 409        # already final


def test_reject_needs_a_reason(table):
    add_po(table)
    assert call("POST /pos/{id}/reject", "po-1", {"reason": ""})[0] == 400
    code, body = call("POST /pos/{id}/reject", "po-1", {"reason": "Customer cancelled"})
    assert code == 200 and body["status"] == "REJECTED"


def test_upload_link_only_for_allowed_types(table):
    code, body = call("POST /uploads", body={"filename": "my PO.pdf"})
    assert code == 200 and body["s3_key"].startswith("incoming/") and body["s3_key"].endswith("my_PO.pdf")
    assert call("POST /uploads", body={"filename": "virus.exe"})[0] == 400


def test_unknown_route_and_bad_json(table):
    assert call("DELETE /pos")[0] == 404
    bad = api.lambda_handler({"routeKey": "POST /uploads", "body": "{not json",
                              "requestContext": {"authorizer": {"lambda": {"role": "csr"}}}})
    assert bad["statusCode"] == 400
