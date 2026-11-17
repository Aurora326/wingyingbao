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


PASSWORD_MIN = 8
PASSWORD_MAX = 128
WEAK_PASSWORDS = {
    "admin123",
    "admin@123",
    "12345678",
    "123456789",
    "password",
    "passw0rd",
    "qwerty123",
    "abc12345",
    "11111111",
    "88888888",
    "a1234567",
    "1qaz2wsx",
}


def validate_password(password: str, username: str = "") -> str:
    """返回不符合密码策略的原因，空字符串表示通过。

    按结构性问题优先的顺序检查，避免一个密码同时命中多条规则时，
    用笼统的弱密码提示盖住真正需要改的地方。
    """
    if len(password) < PASSWORD_MIN:
        return f"密码至少 {PASSWORD_MIN} 个字符"
    if len(password) > PASSWORD_MAX:
        return f"密码不能超过 {PASSWORD_MAX} 个字符"
    if username and password.lower() == username.lower():
        return "密码不能与登录账户相同"
    if len(set(password)) == 1:
        return "密码不能全部是相同字符"
    if not any(ch.isalpha() for ch in password):
        return "密码至少包含一个字母"
    if not any(ch.isdigit() for ch in password):
        return "密码至少包含一个数字"
    if password.lower() in WEAK_PASSWORDS:
        return "这是常见的弱密码，容易被猜到，请换一个"
    return ""


def is_default_password(username: str, password_hash: str) -> bool:
    """管理员仍在使用配置里的初始密码。"""
    return username == settings.admin_user and verify_password(
        settings.admin_password, password_hash
    )


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _pack_user(username: str) -> str:
    """Cookie 只能放 ASCII，中文用户名必须先编码。"""
    return base64.urlsafe_b64encode(username.encode("utf-8")).decode("ascii").rstrip("=")


def _unpack_user(raw: str) -> str | None:
    try:
        name = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    except Exception:
        return None
    return name if _pack_user(name) == raw else None


def create_session(username: str) -> str:
    expires = int(time.time()) + SESSION_TTL
    payload = f"{_pack_user(username)}:{expires}"
    return f"{payload}:{_sign(payload)}"


def verify_session(token: str | None) -> str | None:
    if not token:
        return None
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return None
    user_part, expires, signature = parts
    if not hmac.compare_digest(_sign(f"{user_part}:{expires}"), signature):
        return None
    try:
        if int(expires) < int(time.time()):
            return None
    except ValueError:
        return None
    return _unpack_user(user_part)
