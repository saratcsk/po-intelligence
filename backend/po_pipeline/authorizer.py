"""Step I4 + B8 part 3: API Gateway asks this function "who is calling?" before every API request.

The browser sends   Authorization: Bearer <token>
There are three tokens, one per role:
  viewer - look only (the public demo): list and open POs
  csr    - review, edit, approve, reject and upload POs
  admin  - everything a CSR can do, plus change the settings
We store only SHA-256 hashes of the tokens (in SSM Parameter Store), never the tokens themselves,
and compare in constant time so timing cannot leak how much of a guess was right.
The role is handed to the API function, which refuses admin routes to anyone who is not admin.
"""

import hashlib
import hmac
import os

import boto3

_cached_hashes = None  # read once per Lambda container, not on every request


def _expected_hashes() -> dict:
    """{"viewer": hash, "csr": hash, "admin": hash} - a role is skipped if its parameter is not set."""
    global _cached_hashes
    if _cached_hashes is None:
        ssm = boto3.client("ssm")
        params = {"viewer": os.environ.get("VIEWER_TOKEN_HASH_PARAM"),
                  "csr": os.environ.get("TOKEN_HASH_PARAM"),
                  "admin": os.environ.get("ADMIN_TOKEN_HASH_PARAM")}
        _cached_hashes = {role: ssm.get_parameter(Name=name)["Parameter"]["Value"].strip()
                          for role, name in params.items() if name}
    return _cached_hashes


def role_for(header: str | None, hashes: dict) -> str | None:
    """Return the caller's role, or None if the token matches nothing."""
    if not header or not header.startswith("Bearer "):
        return None
    given = hashlib.sha256(header[len("Bearer "):].strip().encode()).hexdigest()
    matches = [role for role, expected in hashes.items() if hmac.compare_digest(given, expected)]
    for role in ("admin", "csr", "viewer"):          # if two hashes ever matched, the higher role wins
        if role in matches:
            return role
    return None


def is_valid(header: str | None, expected_hash: str) -> bool:
    return role_for(header, {"csr": expected_hash}) is not None


def lambda_handler(event, context=None):
    role = role_for((event.get("headers") or {}).get("authorization"), _expected_hashes())
    if role is None:
        return {"isAuthorized": False}
    return {"isAuthorized": True, "context": {"role": role}}   # API Gateway passes this to the API function
