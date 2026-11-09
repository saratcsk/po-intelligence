import hashlib

from po_pipeline import authorizer

TOKEN = "demo-token-123"
ADMIN_TOKEN = "demo-admin-456"
HASH = hashlib.sha256(TOKEN.encode()).hexdigest()
ADMIN_HASH = hashlib.sha256(ADMIN_TOKEN.encode()).hexdigest()
BOTH = {"csr": HASH, "admin": ADMIN_HASH}


def test_correct_token_is_allowed():
    assert authorizer.is_valid(f"Bearer {TOKEN}", HASH) is True


def test_wrong_missing_or_malformed_token_is_denied():
    assert authorizer.is_valid("Bearer wrong", HASH) is False
    assert authorizer.is_valid(None, HASH) is False
    assert authorizer.is_valid(TOKEN, HASH) is False          # no "Bearer " prefix


def test_each_token_gets_its_own_role():
    assert authorizer.role_for(f"Bearer {TOKEN}", BOTH) == "csr"
    assert authorizer.role_for(f"Bearer {ADMIN_TOKEN}", BOTH) == "admin"
    assert authorizer.role_for("Bearer wrong", BOTH) is None


def test_handler_passes_role_to_the_api(monkeypatch):
    monkeypatch.setattr(authorizer, "_cached_hashes", BOTH)
    csr = authorizer.lambda_handler({"headers": {"authorization": f"Bearer {TOKEN}"}})
    admin = authorizer.lambda_handler({"headers": {"authorization": f"Bearer {ADMIN_TOKEN}"}})
    denied = authorizer.lambda_handler({"headers": {}})
    assert csr == {"isAuthorized": True, "context": {"role": "csr"}}
    assert admin == {"isAuthorized": True, "context": {"role": "admin"}}
    assert denied == {"isAuthorized": False}


def test_viewer_token_gets_the_viewer_role():
    viewer_hash = hashlib.sha256(b"demo-viewer-789").hexdigest()
    hashes = {**BOTH, "viewer": viewer_hash}
    assert authorizer.role_for("Bearer demo-viewer-789", hashes) == "viewer"
    assert authorizer.role_for(f"Bearer {ADMIN_TOKEN}", hashes) == "admin"
