from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.config import settings

SAFE_PART = re.compile(r"[^0-9A-Za-z_\-\u4e00-\u9fa5]")


def _safe(value: str, default: str = "unknown") -> str:
    cleaned = SAFE_PART.sub("_", value or "").strip("_")
    return cleaned[:64] or default


def save_bytes(content: bytes, bank_code: str, account_no: str, original_name: str) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    ext = Path(original_name).suffix.lower()
    object_key = f"{_safe(bank_code)}/{_safe(account_no)}/{digest[:2]}/{digest}{ext}"
    target = settings.artifacts_dir / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    return {
        "object_key": object_key,
        "sha256": digest,
        "size": len(content),
        "original_name": original_name,
        "content_type": ext.lstrip("."),
    }


def save_file(source: Path | str, bank_code: str, account_no: str) -> dict:
    source_path = Path(source)
    content = source_path.read_bytes()
    return save_bytes(content, bank_code, account_no, source_path.name)


def resolve(object_key: str) -> Path:
    root = settings.artifacts_dir.resolve()
    target = (root / object_key).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("非法文件路径")
    return target


def delete(object_key: str) -> None:
    try:
        resolve(object_key).unlink(missing_ok=True)
    except ValueError:
        return
