"""Step S2: nobody can run up the AWS bill, even with a valid token."""
import json

from po_pipeline import api, handler
from po_pipeline.config import DEFAULT_CONFIG, save_config, validate_config
from po_pipeline.limits import MAX_FILE_BYTES
from tests.test_api import call, table  # noqa: F401  (table is a pytest fixture)
from tests.test_handler import ACME_PO, fake_clients, s3_event, saved_pos


def set_limits(table, uploads=20, processing=25):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["orchestration"].update(daily_upload_limit=uploads, daily_processing_limit=processing)
    save_config(table, cfg, "admin", "test")


def test_upload_links_stop_at_the_daily_limit(table):
    set_limits(table, uploads=3)
    codes = [call("POST /uploads", body={"filename": "po.pdf"})[0] for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert "Daily upload limit (3)" in call("POST /uploads", body={"filename": "po.pdf"})[1]["message"]


def test_bad_file_type_does_not_use_up_the_limit(table):
    set_limits(table, uploads=1)
    assert call("POST /uploads", body={"filename": "virus.exe"})[0] == 400
    assert call("POST /uploads", body={"filename": "po.pdf"})[0] == 200


def test_upload_link_makes_s3_enforce_size_and_type(table):
    code, body = call("POST /uploads", body={"filename": "po.pdf"})
    assert code == 200 and body["fields"]["Content-Type"] == "application/pdf"
    assert body["max_bytes"] == MAX_FILE_BYTES == 5 * 1024 * 1024


def test_pipeline_skips_ai_when_daily_processing_limit_reached(monkeypatch):
    t = fake_clients(monkeypatch, ACME_PO)
    set_limits(t, processing=2)
    statuses = [handler.lambda_handler(s3_event("incoming/PO-1001_acme_native.pdf"))["processed"][0]["status"]
                for _ in range(3)]
    assert statuses[2] == "NEEDS_REVIEW"
    skipped = [p for p in saved_pos(t) if any("Daily processing limit (2)" in i["message"] for i in p.get("issues", []))]
    assert len(skipped) == 1 and skipped[0].get("ai_cost_usd") is None
    assert sum(1 for p in saved_pos(t) if p.get("ai_cost_usd") is not None) == 2   # AI ran only twice


def test_large_file_is_not_sent_to_ai(monkeypatch, tmp_path):
    t = fake_clients(monkeypatch, ACME_PO)

    class BigS3:
        def download_file(self, bucket, key, path):
            with open(path, "wb") as f:
                f.truncate(MAX_FILE_BYTES + 1)               # a 5 MB + 1 byte file

    clients = handler._clients()
    monkeypatch.setattr(handler, "_clients", lambda: {**clients, "s3": BigS3()})
    result = handler.lambda_handler(s3_event("incoming/huge.pdf"))
    assert result["processed"][0]["status"] == "NEEDS_REVIEW"
    assert "larger than 5 MB" in saved_pos(t)[0]["issues"][0]["message"]


def test_limits_must_be_sensible():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["orchestration"]["daily_upload_limit"] = 100000
    assert any("daily_upload_limit" in p for p in validate_config(cfg))


def test_counter_rows_are_hidden_from_the_po_list(table):
    call("POST /uploads", body={"filename": "po.pdf"})
    assert all(not p["po_id"].startswith("LIMIT#") for p in call("GET /pos")[1]["items"])
