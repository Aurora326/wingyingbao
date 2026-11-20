"""极简版采集/制单插件发布中心：1 张表 + test/prod 双通道切换 + 一键回滚。"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Agent, PluginRelease
from app.services.audit import write_audit
from app.services.plugin_signing import sha256_bytes, verify_bytes

VALID_CHANNELS = {"test", "prod"}
MAX_PLUGIN_BYTES = 30 * 1024 * 1024
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")
_VER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


class PluginError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _parse_and_validate_zip(content: bytes) -> dict:
    if not content:
        raise PluginError("上传文件为空")
    if len(content) > MAX_PLUGIN_BYTES:
        raise PluginError("插件包体积不能超过 30MB", 413)
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise PluginError("不是有效的 zip 压缩包") from exc

    with zf:
        names = zf.namelist()
        if not names:
            raise PluginError("插件包为空")
        manifest_entry = ""
        for name in names:
            norm = name.replace("\\", "/")
            if norm.startswith("/") or ".." in Path(norm).parts:
                raise PluginError(f"压缩包包含非法路径: {name}")
            if norm == "manifest.json":
                manifest_entry = name
        if not manifest_entry:
            raise PluginError("插件包根目录缺少 manifest.json")
        try:
            manifest = json.loads(zf.read(manifest_entry).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginError("manifest.json 不是合法的 UTF-8 JSON") from exc

    if not isinstance(manifest, dict):
        raise PluginError("manifest.json 必须是 JSON 对象")
    plugin_id = str(manifest.get("plugin_id") or "").strip().lower()
    version = str(manifest.get("version") or "").strip()
    if not _ID_RE.match(plugin_id):
        raise PluginError("manifest.json 中的 plugin_id 格式不合法（仅允许小写字母、数字、.-_）")
    if not _VER_RE.match(version):
        raise PluginError("manifest.json 中的 version 格式不合法")
    entry = manifest.get("entry")
    if entry is not None and not isinstance(entry, dict):
        raise PluginError("manifest.json 中的 entry 必须是对象")
    manifest["plugin_id"] = plugin_id
    manifest["version"] = version
    manifest["bank_code"] = str(manifest.get("bank_code") or "").strip().lower()
    return manifest


def upload_plugin_release(
    db: Session,
    *,
    content: bytes,
    signature: str = "",
    changelog: str = "",
    operator: str = "",
) -> PluginRelease:
    manifest = _parse_and_validate_zip(content)
    plugin_id = manifest["plugin_id"]
    version = manifest["version"]
    bank_code = manifest["bank_code"]
    digest = sha256_bytes(content)
    sig = (signature or str(manifest.get("signature") or "")).strip()

    if settings.plugin_public_key and not verify_bytes(content, sig, settings.plugin_public_key):
        raise PluginError("插件包 Ed25519 签名校验未通过，拒绝入库", 403)

    exists = db.execute(
        select(PluginRelease).where(
            PluginRelease.plugin_id == plugin_id,
            PluginRelease.version == version,
        )
    ).scalars().first()
    if exists is not None:
        raise PluginError(f"插件 {plugin_id} 版本 {version} 已存在，请递增版本号后重新上传", 409)

    rel_path = Path(plugin_id) / f"{plugin_id}-{version}.zip"
    abs_path = settings.plugins_dir / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)

    note = (changelog or str(manifest.get("changelog") or "")).strip()[:512]
    row = PluginRelease(
        plugin_id=plugin_id,
        bank_code=bank_code,
        version=version,
        sha256=digest,
        signature=sig[:256],
        file_path=rel_path.as_posix(),
        size=len(content),
        manifest=manifest,
        changelog=note,
        active_test=False,
        active_prod=False,
        uploaded_by=(operator or "")[:64],
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        operator or "admin",
        "plugin_upload",
        "plugin_release",
        row.id,
        {"plugin_id": plugin_id, "bank_code": bank_code, "version": version, "sha256": digest[:16]},
    )
    db.commit()
    db.refresh(row)
    return row


def activate_release(
    db: Session,
    release_id: int,
    channel: str,
    operator: str = "",
) -> PluginRelease:
    """将指定插件版本设为 test 或 prod 通道生效版（历史版本再次激活即一键回退）。"""
    if channel not in VALID_CHANNELS:
        raise PluginError("通道只支持 test 或 prod")
    row = db.get(PluginRelease, release_id)
    if row is None:
        raise PluginError("插件版本不存在", 404)

    siblings = db.execute(
        select(PluginRelease).where(PluginRelease.plugin_id == row.plugin_id)
    ).scalars().all()
    for item in siblings:
        if channel == "test":
            item.active_test = item.id == row.id
        else:
            item.active_prod = item.id == row.id

    write_audit(
        db,
        operator or "admin",
        "plugin_activate",
        "plugin_release",
        row.id,
        {"plugin_id": row.plugin_id, "version": row.version, "channel": channel},
    )
    db.commit()
    db.refresh(row)
    return row


def deactivate_release(
    db: Session,
    release_id: int,
    channel: str,
    operator: str = "",
) -> PluginRelease:
    if channel not in VALID_CHANNELS:
        raise PluginError("通道只支持 test 或 prod")
    row = db.get(PluginRelease, release_id)
    if row is None:
        raise PluginError("插件版本不存在", 404)
    if channel == "test":
        row.active_test = False
    else:
        row.active_prod = False
    write_audit(
        db,
        operator or "admin",
        "plugin_deactivate",
        "plugin_release",
        row.id,
        {"plugin_id": row.plugin_id, "version": row.version, "channel": channel},
    )
    db.commit()
    db.refresh(row)
    return row


def set_agent_channel(db: Session, agent_id: int, channel: str, operator: str = "") -> Agent:
    if channel not in VALID_CHANNELS:
        raise PluginError("执行机通道只支持 test 或 prod")
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise PluginError("执行机不存在", 404)
    agent.channel = channel
    write_audit(
        db,
        operator or "admin",
        "agent_channel_update",
        "agent",
        agent.code,
        {"channel": channel},
    )
    db.commit()
    db.refresh(agent)
    return agent


def resolve_release_path(row: PluginRelease) -> Path:
    root = settings.plugins_dir.resolve()
    target = (root / row.file_path).resolve()
    if root not in target.parents and target != root:
        raise PluginError("非法的插件文件路径", 403)
    return target


def _release_descriptor(row: PluginRelease) -> dict:
    manifest = row.manifest or {}
    return {
        "release_id": row.id,
        "plugin_id": row.plugin_id,
        "bank_code": row.bank_code,
        "version": row.version,
        "sha256": row.sha256,
        "signature": row.signature,
        "size": row.size,
        "download_url": f"/api/agent/plugins/{row.id}/download",
        "entry": manifest.get("entry") or {},
        "capabilities": manifest.get("capabilities") or [],
        "min_agent_version": str(manifest.get("min_agent_version") or ""),
    }


def desired_plugins_for_channel(db: Session, channel: str = "prod") -> dict[str, dict]:
    """计算指定通道（test/prod）的期望插件清单。

    prod 仅下发 active_prod=True 的版本；
    test 优先下发 active_test=True，若某插件未单独设测试版则继承 active_prod=True。
    """
    rows = db.execute(
        select(PluginRelease).where(
            PluginRelease.active_prod.is_(True) | PluginRelease.active_test.is_(True)
        )
    ).scalars().all()
    desired: dict[str, dict] = {}
    for row in rows:
        if row.active_prod:
            desired[row.plugin_id] = _release_descriptor(row)
    if channel == "test":
        for row in rows:
            if row.active_test:
                desired[row.plugin_id] = _release_descriptor(row)
    return desired


def desired_plugins_for_agent(db: Session, agent_code: str) -> dict[str, dict]:
    agent = db.execute(select(Agent).where(Agent.code == agent_code)).scalars().first()
    channel = (agent.channel if agent else "prod") or "prod"
    return desired_plugins_for_channel(db, channel)


def list_releases(db: Session) -> list[PluginRelease]:
    return (
        db.execute(
            select(PluginRelease).order_by(
                PluginRelease.plugin_id.asc(),
                PluginRelease.created_at.desc(),
                PluginRelease.id.desc(),
            )
        )
        .scalars()
        .all()
    )
