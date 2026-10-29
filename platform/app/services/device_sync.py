from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditLog, UkeyDevice
from app.services import mb_local
from app.services.mb_local import LocalTerminal, MbLocalError
from app.services.usb_robot import RobotDevice, UsbRobotClient, UsbRobotError

MB_BACKEND = "mb"


class DeviceSyncError(RuntimeError):
    pass


def _apply_cloud(device: UkeyDevice, source: RobotDevice) -> None:
    raw = source.raw or {}
    device.backend = MB_BACKEND
    if not device.agent_code:
        device.agent_code = settings.agent_code
    device.name = source.display_name
    device.bank_code = source.bank_code[:32]
    device.external_id = source.external_id[:64]
    device.status = source.status
    device.cert_subject = source.cert_subject[:256]
    device.cert_expires_at = source.cert_expires_at
    device.last_heartbeat = source.last_heartbeat
    device.extra = {
        "ter_id": raw.get("ter_id", ""),
        "pid": raw.get("pid", ""),
        "port_num": raw.get("port_num", ""),
        "tax_auth": raw.get("tax_auth", ""),
        "plate_custom_type_key": raw.get("plate_custom_type_key", ""),
        "recordDataType": raw.get("recordDataType", ""),
        "client_ip": raw.get("client_ip", ""),
        "offline": raw.get("offline", ""),
        "mb_user": settings.mb_user,
        "source": "cloud",
    }


def _apply_local(device: UkeyDevice, source: RobotDevice) -> None:
    raw = source.raw or {}
    device.backend = MB_BACKEND
    if not device.agent_code:
        device.agent_code = settings.agent_code
    if not device.name:
        device.name = mb_local.local_device_name(source)
    if source.bank_code and (source.bank_code != "generic" or not device.bank_code):
        device.bank_code = source.bank_code[:32]
    device.external_id = source.external_id[:64]
    device.status = source.status
    if source.cert_subject:
        device.cert_subject = source.cert_subject[:256]
    if source.cert_expires_at:
        device.cert_expires_at = source.cert_expires_at
    device.last_heartbeat = source.last_heartbeat or dt.datetime.now()
    extra = dict(device.extra or {})
    extra.update(
        {
            "ter_id": raw.get("ter_id", ""),
            "pid": raw.get("usb_pid", "") or source.external_id,
            "port_num": raw.get("usb_port", ""),
            "user": raw.get("user", ""),
            "client_ip": raw.get("client_ip", ""),
            "port_power": raw.get("port_power", ""),
            "usb_share": raw.get("usb_share", ""),
            "mb_user": extra.get("mb_user") or settings.mb_user,
            "source": "local",
        }
    )
    device.extra = extra


def _audit(db: Session, source: str, total: int, created: int, updated: int, extra: dict | None = None) -> None:
    detail = {"total": total, "created": created, "updated": updated, "source": source}
    if extra:
        detail.update(extra)
    db.add(
        AuditLog(
            actor="system",
            action="usb_robot_sync",
            target_type="ukey_device",
            target_id="all",
            detail=detail,
        )
    )


def _sync_cloud(db: Session, client: UsbRobotClient | None = None) -> dict:
    client = client or UsbRobotClient.from_settings()
    devices = client.list_devices()
    created = 0
    updated = 0
    for source in devices:
        row = db.execute(
            select(UkeyDevice).where(
                UkeyDevice.backend == MB_BACKEND,
                UkeyDevice.gf3_cabinet == source.cabinet,
                UkeyDevice.gf3_port == source.port,
            )
        ).scalars().first()
        if row is None:
            row = UkeyDevice(
                name=source.display_name,
                bank_code=source.bank_code[:32],
                backend=MB_BACKEND,
                agent_code=settings.agent_code,
                gf3_cabinet=source.cabinet,
                gf3_port=source.port,
            )
            db.add(row)
            created += 1
        else:
            updated += 1
        _apply_cloud(row, source)
    db.flush()
    _audit(db, "cloud", len(devices), created, updated)
    db.commit()
    return {"total": len(devices), "created": created, "updated": updated, "source": "cloud"}


def _sync_local(
    db: Session,
    terminals: list[LocalTerminal],
    devices: list[RobotDevice],
    errors: list[str],
) -> dict:
    created = 0
    updated = 0
    seen: dict[str, set[int]] = {}
    for source in devices:
        raw = source.raw or {}
        ter_id = str(raw.get("ter_id") or "")
        row = db.execute(
            select(UkeyDevice).where(
                UkeyDevice.backend == MB_BACKEND,
                UkeyDevice.gf3_cabinet == source.cabinet,
                UkeyDevice.gf3_port == source.port,
            )
        ).scalars().first()
        if row is None:
            row = UkeyDevice(
                name="",
                bank_code=source.bank_code[:32],
                backend=MB_BACKEND,
                agent_code=settings.agent_code,
                gf3_cabinet=source.cabinet,
                gf3_port=source.port,
            )
            db.add(row)
            created += 1
        else:
            updated += 1
        _apply_local(row, source)
        seen.setdefault(ter_id, set()).add(source.port)

    for terminal in terminals:
        rows = db.execute(
            select(UkeyDevice).where(
                UkeyDevice.backend == MB_BACKEND,
                UkeyDevice.gf3_cabinet == terminal.cabinet,
            )
        ).scalars().all()
        present = seen.get(terminal.ter_id, set())
        for row in rows:
            extra = dict(row.extra or {})
            if extra.get("ter_id") and extra["ter_id"] != terminal.ter_id:
                continue
            if row.gf3_port not in present:
                row.status = "offline"
                extra["missing_since"] = dt.datetime.now().isoformat(timespec="seconds")
                row.extra = extra

    db.flush()
    _audit(
        db,
        "local",
        len(devices),
        created,
        updated,
        {
            "terminals": [terminal.ter_id for terminal in terminals],
            "errors": errors[:5],
        },
    )
    db.commit()
    return {
        "total": len(devices),
        "created": created,
        "updated": updated,
        "source": "local",
        "terminals": len(terminals),
        "errors": errors,
    }


def _local_ter_ids(db: Session) -> list[str]:
    configured = settings.mb_ter_id_list
    if configured:
        return configured
    rows = db.execute(select(UkeyDevice).where(UkeyDevice.backend == MB_BACKEND)).scalars().all()
    ter_ids = sorted({str((row.extra or {}).get("ter_id")) for row in rows if (row.extra or {}).get("ter_id")})
    if not ter_ids:
        raise DeviceSyncError(
            "未配置本地 MB 机柜编号，请在 .env 设置 PLATFORM_MB_TER_IDS（多个用逗号分隔），或先使用服务商接口完成一次盘点"
        )
    return ter_ids


def sync_devices(db: Session, client: UsbRobotClient | None = None, source: str = "auto") -> dict:
    """默认本地优先：mb.exe status -> 平台库存；失败时回退服务商 Web 接口。"""
    mode = (source or "auto").strip().lower()
    if client is not None:
        return _sync_cloud(db, client)
    if mode not in ("auto", "local", "cloud"):
        raise DeviceSyncError(f"不支持的同步方式: {source}")

    local_error: Exception | None = None
    if mode in ("auto", "local"):
        try:
            terminals, devices, errors = mb_local.read_all(_local_ter_ids(db), exe=settings.mb_exe)
            return _sync_local(db, terminals, devices, errors)
        except (MbLocalError, DeviceSyncError) as exc:
            local_error = exc
            if mode == "local":
                raise DeviceSyncError(f"本地 MB 同步失败: {exc}") from exc

    if not settings.usb_robot_enabled:
        prefix = f"本地 MB 同步失败: {local_error}；" if local_error else ""
        raise DeviceSyncError(prefix + "未配置服务商接口，无法回退")

    try:
        return _sync_cloud(db, UsbRobotClient.from_settings())
    except (UsbRobotError, httpx.HTTPError) as exc:
        prefix = f"本地 MB 同步失败: {local_error}；" if local_error else ""
        raise DeviceSyncError(prefix + f"服务商接口同步失败: {exc}") from exc
