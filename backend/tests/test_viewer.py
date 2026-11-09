"""Step S1: the public demo account can look at everything but change nothing."""
import pytest

from tests.test_api import add_po, call, table  # noqa: F401  (table is a pytest fixture)

WRITE_ROUTES = [("PUT /pos/{id}", "po-1", {"po": {}}), ("POST /pos/{id}/approve", "po-1", None),
                ("POST /pos/{id}/reject", "po-1", {"reason": "x"}), ("POST /uploads", None, {"filename": "a.pdf"})]


def test_viewer_can_list_and_open_pos(table):
    add_po(table)
    assert call("GET /pos", role="viewer")[0] == 200
    assert call("GET /pos/{id}", "po-1", role="viewer")[0] == 200


@pytest.mark.parametrize("route,po_id,body", WRITE_ROUTES)
def test_viewer_cannot_change_anything(table, route, po_id, body):
    add_po(table)
    code, response = call(route, po_id, body, role="viewer")
    assert code == 403 and "read-only" in response["message"]
    assert table.items["po-1"]["status"] == "NEEDS_REVIEW"        # nothing changed


def test_viewer_cannot_see_settings(table):
    assert call("GET /config", role="viewer")[0] == 403


def test_missing_role_is_treated_as_no_access(table):
    add_po(table)
    assert call("POST /pos/{id}/approve", "po-1", role=None)[0] == 403


def test_me_reports_the_role(table):
    for role in ("viewer", "csr", "admin"):
        assert call("GET /me", role=role)[1] == {"role": role}
