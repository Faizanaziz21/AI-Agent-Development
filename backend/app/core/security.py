from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

import bcrypt
import jwt

from app.core.config import get_settings

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "viewer": {"read"},
    "approver": {"read", "approval.decide"},
    "finance_manager": {"read", "approval.decide"},
    "compliance_officer": {"read", "approval.decide", "audit.read", "policy.write"},
    "operator": {"read", "project.write", "knowledge.write", "approval.decide"},
    "admin": {
        "read", "project.write", "knowledge.write", "approval.decide", "agent.write", "tool.write",
        "policy.write", "memory.approve", "settings.write", "user.write", "audit.read",
    },
}
ROLE_PERMISSIONS["owner"] = ROLE_PERMISSIONS["admin"] | {"org.manage"}
ROLES = list(ROLE_PERMISSIONS)


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def can_decide_approval(role: str, required_role: str) -> bool:
    if not has_permission(role, "approval.decide"):
        return False
    if role in ("owner", "admin"):
        return True
    if required_role in ("approver", "operator", ""):
        return True
    return role == required_role


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    role: str
    email: str
    name: str = ""

    def can(self, permission: str) -> bool:
        return has_permission(self.role, permission)


def create_access_token(user_id: str, org_id: str, role: str) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {"sub": user_id, "org": org_id, "role": role, "iat": now, "exp": now + s.jwt_ttl_minutes * 60}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])


def sign_webhook(secret: str, body: bytes, timestamp: str) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_webhook(secret: str, body: bytes, timestamp: str, signature: str, tolerance_s: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > tolerance_s:
        return False
    return hmac.compare_digest(sign_webhook(secret, body, timestamp), signature or "")
