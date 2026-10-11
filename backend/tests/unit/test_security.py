import time

import jwt
import pytest

from app.core import crypto
from app.core.security import (
    can_decide_approval,
    create_access_token,
    decode_access_token,
    has_permission,
    hash_password,
    sign_webhook,
    verify_password,
    verify_webhook,
)


def test_password_hashing_roundtrip():
    h = hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_jwt_roundtrip_and_tamper_detection():
    token = create_access_token("u1", "o1", "operator")
    claims = decode_access_token(token)
    assert (claims["sub"], claims["org"], claims["role"]) == ("u1", "o1", "operator")
    header, payload, sig = token.split(".")
    forged = jwt.encode({**claims, "role": "owner"}, "attacker-secret-attacker-secret-attacker", algorithm="HS256")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(forged)
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(f"{header}.{payload}.{sig[:-2]}xx")


def test_webhook_signature_verification():
    body = b'{"subject":"help"}'
    ts = str(int(time.time()))
    sig = sign_webhook("whsec_test", body, ts)
    assert verify_webhook("whsec_test", body, ts, sig)
    assert not verify_webhook("whsec_other", body, ts, sig)
    assert not verify_webhook("whsec_test", body + b" ", ts, sig)
    stale = str(int(time.time()) - 3600)
    assert not verify_webhook("whsec_test", body, stale, sign_webhook("whsec_test", body, stale))
    assert not verify_webhook("whsec_test", body, "not-a-number", sig)


@pytest.mark.parametrize(("role", "required", "allowed"), [
    ("viewer", "approver", False),
    ("approver", "approver", True),
    ("approver", "finance_manager", False),
    ("finance_manager", "finance_manager", True),
    ("compliance_officer", "finance_manager", False),
    ("admin", "finance_manager", True),
    ("owner", "compliance_officer", True),
    ("operator", "admin", False),
])
def test_approval_role_matrix(role, required, allowed):
    assert can_decide_approval(role, required) is allowed


def test_rbac_permissions():
    assert has_permission("viewer", "read")
    assert not has_permission("viewer", "project.write")
    assert has_permission("operator", "project.write")
    assert not has_permission("operator", "agent.write")
    assert has_permission("admin", "agent.write")
    assert has_permission("owner", "org.manage")
    assert not has_permission("unknown-role", "read")


def test_secret_encryption():
    ct = crypto.encrypt("sk-live-1234567890")
    assert "sk-live" not in ct
    assert crypto.decrypt(ct) == "sk-live-1234567890"
    masked = crypto.mask("sk-live-1234567890")
    assert masked.endswith("90")
    assert "live-1234" not in masked
