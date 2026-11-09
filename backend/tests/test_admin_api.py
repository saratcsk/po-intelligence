import json

import pytest

from po_pipeline import api
from tests.test_api import FakeS3, FakeTableWithReads, add_po
from tests.test_handler import ACME_PO


@pytest.fixture
def table(monkeypatch):
    t = FakeTableWithReads()
    monkeypatch.setattr(api, "_clients", lambda: {"s3": FakeS3(), "table": t, "bucket": "po-bucket"})
    return t


def call(route, body=None, role="admin", po_id=None):
    event = {"routeKey": route, "pathParameters": {"id": po_id} if po_id else None,
             "body": json.dumps(body) if body is not None else None,
             "requestContext": {"authorizer": {"lambda": {"role": role}}}}
    response = api.lambda_handler(event)
    return response["statusCode"], json.loads(response["body"])


def test_csr_token_cannot_touch_settings(table):
    for route in api.ADMIN_ROUTES:
        assert call(route, {}, role="csr")[0] == 403
    assert call("GET /config", role=None)[0] == 403            # no role at all -> refused


def test_admin_sees_defaults_before_anything_is_saved(table):
    code, body = call("GET /config")
    assert code == 200 and body["version"] == 0 and body["accuracy"]["auto_approve_threshold"] == 0.85


def test_admin_change_is_saved_as_a_new_version(table):
    code, body = call("PUT /config", {"config": {"accuracy": {"auto_approve_threshold": 0.95}},
                                      "note": "tighten for demo"})
    assert code == 200 and body["version"] == 1
    assert body["accuracy"]["auto_approve_threshold"] == 0.95
    assert body["accuracy"]["error_penalty"] == 0.25            # untouched settings are kept
    assert call("GET /config")[1]["version"] == 1


def test_bad_values_are_refused_and_nothing_changes(table):
    code, body = call("PUT /config", {"config": {"accuracy": {"auto_approve_threshold": 7}}})
    assert code == 400 and "between 0 and 1" in body["problems"][0]
    assert call("PUT /config", {"config": {"hacks": {}}})[0] == 400
    assert call("GET /config")[1]["version"] == 0


def test_history_and_rollback(table):
    call("PUT /config", {"config": {"accuracy": {"auto_approve_threshold": 0.95}}, "note": "v1"})
    call("PUT /config", {"config": {"accuracy": {"auto_approve_threshold": 0.60}}, "note": "v2"})
    code, body = call("POST /config/rollback", {"version": 1})
    assert code == 200 and body["version"] == 3 and body["accuracy"]["auto_approve_threshold"] == 0.95
    code, history = call("GET /config/history")
    assert history["current_version"] == 3
    assert [v["version"] for v in history["items"]] == [3, 2, 1]
    assert history["items"][0]["note"] == "rollback to v1"
    assert call("POST /config/rollback", {"version": 99})[0] == 404


def test_csr_edit_uses_the_admin_settings(table):
    call("PUT /config", {"config": {"fields": {"po_number": "optional"}}})   # admin relaxes the rule
    add_po(table)                                                            # PO with no PO number
    code, body = call("PUT /pos/{id}", {"po": {**ACME_PO, "po_number": None}}, role="csr", po_id="po-1")
    assert code == 200 and body["status"] == "READY"


def test_po_list_hides_settings_rows(table):
    call("PUT /config", {"config": {"accuracy": {"auto_approve_threshold": 0.9}}})
    add_po(table)
    assert [p["po_id"] for p in call("GET /pos", role="csr")[1]["items"]] == ["po-1"]
