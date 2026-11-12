"""平台级开关的读写。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting

ABC_LEDGER_AUTO_MATCH = "payment.abc_ledger_auto_match"


def get_value(db: Session, key: str, default: str = "") -> str:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalars().first()
    if row is None or row.value is None:
        return default
    return row.value


def get_bool(db: Session, key: str, default: bool = False) -> bool:
    value = get_value(db, key, "1" if default else "0")
    return value.strip().lower() in ("1", "true", "on", "yes")


def set_bool(db: Session, key: str, value: bool) -> None:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalars().first()
    if row is None:
        row = AppSetting(key=key, value="1" if value else "0")
        db.add(row)
    else:
        row.value = "1" if value else "0"
    db.commit()
