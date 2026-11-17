"""告警扫描、去重、外发与站内记录。

扫描由调度线程每分钟触发一次。没有配置 Webhook 时，事件仍然落库，
在平台管理的告警列表里可见，不会因为通道没配好而丢失。
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Agent, AlertEvent, BankAccount, CollectionTask, PaymentCollectTask
from app.services import app_settings
from app.services.banks import bank_label

KIND_LABELS = {
    "task_failed": "采集失败",
    "agent_offline": "设备离线",
    "account_stale": "账户长期未采集",
    "payment_collect_failed": "付款采集失败",
}

STATUS_LABELS = {
    "pending": "待发送",
    "sent": "已发送",
    "failed": "发送失败",
    "resolved": "已恢复",
}

DEFAULTS = {
    "alert_enabled": "1",
    "alert_webhook": "",
    "alert_device_offline_minutes": "30",
    "alert_account_stale_days": "2",
    "alert_cooldown_hours": "6",
    "alert_quiet_hours": "",
}


@dataclass
class AlertConfig:
    enabled: bool
    webhook: str
    device_offline_minutes: int
    account_stale_days: int
    cooldown_hours: int
    quiet_hours: str


def _int(value: str, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def load_config(db: Session) -> AlertConfig:
    raw = {key: app_settings.get_value(db, key, default) for key, default in DEFAULTS.items()}
    return AlertConfig(
        enabled=str(raw["alert_enabled"]).strip().lower() in ("1", "true", "on", "yes"),
        webhook=str(raw["alert_webhook"]).strip(),
        device_offline_minutes=max(_int(raw["alert_device_offline_minutes"], 30), 1),
        account_stale_days=max(_int(raw["alert_account_stale_days"], 2), 1),
        cooldown_hours=max(_int(raw["alert_cooldown_hours"], 6), 0),
        quiet_hours=str(raw["alert_quiet_hours"]).strip(),
    )


def _recent(db: Session, kind: str, object_type: str, object_id: int, now: dt.datetime, hours: int) -> bool:
    if hours <= 0:
        return False
    cutoff = now - dt.timedelta(hours=hours)
    exists = db.execute(
        select(AlertEvent.id).where(
            AlertEvent.kind == kind,
            AlertEvent.object_type == object_type,
            AlertEvent.object_id == object_id,
            AlertEvent.created_at >= cutoff,
        )
    ).first()
    return exists is not None


def _add(
    db: Session,
    *,
    kind: str,
    level: str,
    title: str,
    content: str,
    object_type: str,
    object_id: int,
    tenant_id: int | None,
) -> None:
    db.add(
        AlertEvent(
            kind=kind,
            level=level,
            title=title,
            content=content,
            object_type=object_type,
            object_id=object_id,
            tenant_id=tenant_id,
            status="pending",
        )
    )


def scan(db: Session, now: dt.datetime | None = None) -> int:
    """按配置扫描四类异常，生成去重后的告警事件。"""
    now = now or dt.datetime.now()
    cfg = load_config(db)
    if not cfg.enabled:
        return 0

    created = 0

    since = now - dt.timedelta(hours=24)
    failed_tasks = db.execute(
        select(CollectionTask)
        .where(CollectionTask.status == "failed", CollectionTask.finished_at >= since)
        .order_by(CollectionTask.finished_at.desc())
        .limit(200)
    ).scalars().all()
    for task in failed_tasks:
        if _recent(db, "task_failed", "collection_task", task.id, now, cfg.cooldown_hours):
            continue
        account = db.get(BankAccount, task.account_id)
        name = f"{account.account_name or account.account_no}（{bank_label(account.bank_code)}）" if account else f"账户 #{task.account_id}"
        detail = task.error_msg or task.error_code or "未知错误"
        _add(
            db,
            kind="task_failed",
            level="critical",
            title=f"采集失败：{name}",
            content=f"任务 #{task.id}（{task.type}）重试 {task.attempts} 次后失败。\n错误：{detail}",
            object_type="collection_task",
            object_id=task.id,
            tenant_id=task.tenant_id,
        )
        created += 1

    heartbeat_cutoff = now - dt.timedelta(minutes=cfg.device_offline_minutes)
    agents = db.execute(select(Agent)).scalars().all()
    for agent in agents:
        heartbeat = agent.last_heartbeat or agent.created_at
        if heartbeat is None or heartbeat >= heartbeat_cutoff:
            continue
        if _recent(db, "agent_offline", "agent", agent.id, now, cfg.cooldown_hours):
            continue
        _add(
            db,
            kind="agent_offline",
            level="warning",
            title=f"采集设备离线：{agent.name}",
            content=f"设备 {agent.code} 已超过 {cfg.device_offline_minutes} 分钟没有心跳。\n最后心跳：{heartbeat:%Y-%m-%d %H:%M}",
            object_type="agent",
            object_id=agent.id,
            tenant_id=agent.tenant_id,
        )
        created += 1

    last_success = dict(
        db.execute(
            select(CollectionTask.account_id, func.max(CollectionTask.finished_at))
            .where(CollectionTask.status == "success")
            .group_by(CollectionTask.account_id)
        ).all()
    )
    accounts = db.execute(select(BankAccount).where(BankAccount.collect_enabled.is_(True))).scalars().all()
    stale_cutoff = now - dt.timedelta(days=cfg.account_stale_days)
    for account in accounts:
        latest = last_success.get(account.id) or account.created_at
        if latest is None or latest >= stale_cutoff:
            continue
        if _recent(db, "account_stale", "bank_account", account.id, now, cfg.cooldown_hours):
            continue
        name = f"{account.account_name or account.account_no}（{bank_label(account.bank_code)}）"
        _add(
            db,
            kind="account_stale",
            level="warning",
            title=f"账户长期未采集：{name}",
            content=f"最近一次成功采集是 {latest:%Y-%m-%d %H:%M}，已超过 {cfg.account_stale_days} 天。",
            object_type="bank_account",
            object_id=account.id,
            tenant_id=account.tenant_id,
        )
        created += 1

    failed_payments = db.execute(
        select(PaymentCollectTask)
        .where(PaymentCollectTask.status == "failed", PaymentCollectTask.finished_at >= since)
        .order_by(PaymentCollectTask.finished_at.desc())
        .limit(100)
    ).scalars().all()
    for task in failed_payments:
        if _recent(db, "payment_collect_failed", "payment_collect_task", task.id, now, cfg.cooldown_hours):
            continue
        _add(
            db,
            kind="payment_collect_failed",
            level="critical",
            title=f"付款数据采集失败（{task.source}）",
            content=f"任务 #{task.id} 失败。\n错误：{task.error_msg or '未知错误'}",
            object_type="payment_collect_task",
            object_id=task.id,
            tenant_id=task.tenant_id,
        )
        created += 1

    db.commit()
    return created


def valid_quiet_hours(expr: str) -> bool:
    expr = (expr or "").strip()
    if not expr:
        return True
    if "-" not in expr:
        return False
    try:
        start_text, end_text = expr.split("-", 1)
        dt.datetime.strptime(start_text.strip(), "%H:%M")
        dt.datetime.strptime(end_text.strip(), "%H:%M")
    except ValueError:
        return False
    return True


def in_quiet_hours(now: dt.datetime, expr: str) -> bool:
    """静默时段支持跨天，例如 22:00-07:00。"""
    expr = (expr or "").strip()
    if "-" not in expr:
        return False
    try:
        start_text, end_text = expr.split("-", 1)
        start = dt.datetime.strptime(start_text.strip(), "%H:%M").time()
        end = dt.datetime.strptime(end_text.strip(), "%H:%M").time()
    except ValueError:
        return False
    current = now.time()
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def format_message(event: AlertEvent) -> str:
    when = event.created_at or dt.datetime.now()
    return (
        "### 司库平台告警\n"
        f"**{event.title}**\n"
        f"{event.content}\n"
        f"> 类型：{KIND_LABELS.get(event.kind, event.kind)}　时间：{when:%Y-%m-%d %H:%M}"
    )


def send_webhook(url: str, content: str, timeout: float = 8.0) -> tuple[bool, str]:
    payload = {"msgtype": "markdown", "markdown": {"content": content[:4000]}}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]
    if body.get("errcode") == 0:
        return True, ""
    return False, f"errcode={body.get('errcode')} {body.get('errmsg', '')}".strip()


def deliver(db: Session, now: dt.datetime | None = None) -> int:
    """把待发送事件推送到企业微信，失败保留待重试。"""
    now = now or dt.datetime.now()
    cfg = load_config(db)
    if not cfg.enabled or not cfg.webhook or in_quiet_hours(now, cfg.quiet_hours):
        return 0
    rows = db.execute(
        select(AlertEvent)
        .where(AlertEvent.status.in_(("pending", "failed")), AlertEvent.attempts < 3)
        .order_by(AlertEvent.id)
        .limit(20)
    ).scalars().all()
    sent = 0
    for event in rows:
        ok, error = send_webhook(cfg.webhook, format_message(event))
        event.attempts += 1
        if ok:
            event.status = "sent"
            event.sent_at = now
            event.error_msg = ""
            sent += 1
        else:
            event.status = "failed"
            event.error_msg = error[:512]
    db.commit()
    return sent


def send_test(cfg: AlertConfig) -> tuple[bool, str]:
    if not cfg.webhook:
        return False, "请先填写 Webhook 地址"
    text = f"### 司库平台告警测试\n> 告警通道配置正常。\n> 时间：{dt.datetime.now():%Y-%m-%d %H:%M}"
    return send_webhook(cfg.webhook, text)
