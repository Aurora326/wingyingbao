from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_fernet: Fernet | None = None
SESSION_TTL = 12 * 3600


def _load_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    path = settings.master_key_path
    if path.exists():
        key = path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _load_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _load_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session(username: str) -> str:
    expires = int(time.time()) + SESSION_TTL
    payload = f"{username}:{expires}"
    return f"{payload}:{_sign(payload)}"


def verify_session(token: str | None) -> str | None:
    if not token:
        return None
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return None
    username, expires, signature = parts
    if not hmac.compare_digest(_sign(f"{username}:{expires}"), signature):
        return None
    try:
        if int(expires) < int(time.time()):
            return None
    except ValueError:
        return None
    return username
