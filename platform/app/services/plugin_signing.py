"""插件包 SHA256 与 Ed25519 离线签名校验工具。

私钥仅保存在研发离线发布环境，公钥内置于客户机与平台配置，
即使平台 Web 端被攻破，攻击者也无法向连接真实 UKey 的客户机下发未签名代码。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate_keypair() -> tuple[str, str]:
    """生成一对 Base64 编码的 32 字节 Ed25519 (private_key_b64, public_key_b64)。"""
    private_key = Ed25519PrivateKey.generate()
    priv_raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return (
        base64.b64encode(priv_raw).decode("ascii"),
        base64.b64encode(pub_raw).decode("ascii"),
    )


def sign_bytes(data: bytes, private_key_b64: str) -> str:
    raw = base64.b64decode(private_key_b64.strip())
    private_key = Ed25519PrivateKey.from_private_bytes(raw)
    signature = private_key.sign(data)
    return base64.b64encode(signature).decode("ascii")


def verify_bytes(data: bytes, signature_b64: str, public_key_b64: str) -> bool:
    if not signature_b64 or not public_key_b64:
        return False
    try:
        pub_raw = base64.b64decode(public_key_b64.strip())
        sig_raw = base64.b64decode(signature_b64.strip())
        public_key = Ed25519PublicKey.from_public_bytes(pub_raw)
        public_key.verify(sig_raw, data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
