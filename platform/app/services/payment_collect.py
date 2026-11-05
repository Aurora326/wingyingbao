from __future__ import annotations

import base64
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import PaymentCollectTask, PaymentSourceAccount
from app.schemas import AgentTaskResult
from app.services.payment import import_payment_excel
from app.services import security
from app.tenancy import tenant_context


class PaymentCollectError(Exception):
    pass


def get_source_credentials(db: Session, source: str = "mingyuan") -> tuple[str, str]:
    account = db.execute(
        select(PaymentSourceAccount).where(PaymentSourceAccount.source == source)
    ).scalars().first()
    if account is not None:
        return security.decrypt(account.username_enc), security.decrypt(account.password_enc)
    return settings.mingyuan_username, settings.mingyuan_password


def get_source_schedule(db: Session, source: str = "mingyuan") -> dict:
    account = db.execute(
        select(PaymentSourceAccount).where(PaymentSourceAccount.source == source)
    ).scalars().first()
    if account is None:
        return {"enabled": False, "cron": "0 8 * * *", "limit": 5}
    return {
        "enabled": bool(account.collect_enabled),
        "cron": account.collect_cron or "0 8 * * *",
        "limit": account.collect_limit or 5,
    }


def save_source_credentials(
    db: Session, username: str, password: str, source: str = "mingyuan"
) -> PaymentSourceAccount:
    account = db.execute(
        select(PaymentSourceAccount).where(PaymentSourceAccount.source == source)
    ).scalars().first()
    if account is None:
        if not username or not password:
            raise PaymentCollectError("首次配置需要同时填写明源云账号和密码")
        account = PaymentSourceAccount(source=source)
        db.add(account)
    if username:
        account.username_enc = security.encrypt(username)
    if password:
        account.password_enc = security.encrypt(password)
    account.updated_at = dt.datetime.now()
    db.commit()
    db.refresh(account)
    return account


def enqueue_payment_collect(db: Session, limit: int = 5, since: str = "", created_by: str = "") -> PaymentCollectTask:
    running = db.execute(
        select(PaymentCollectTask).where(PaymentCollectTask.status.in_(("queued", "running")))
    ).scalars().first()
    if running is not None:
        raise PaymentCollectError("已有采集任务在执行，请稍后再试")
    if settings.payment_collect_mode == "local" and not settings.mingyuan_collector.exists():
        raise PaymentCollectError(f"采集脚本不存在：{settings.mingyuan_collector}")
    username, password = get_source_credentials(db)
    if not username or not password:
        raise PaymentCollectError("请先在付款设置里配置明源云采集账号")

    since_date = None
    if since:
        try:
            since_date = dt.date.fromisoformat(since[:10])
        except ValueError as exc:
            raise PaymentCollectError("日期格式应为 YYYY-MM-DD") from exc

    task = PaymentCollectTask(
        status="queued",
        limit_count=max(0, int(limit)),
        since_date=since_date,
        created_by=created_by,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def task_dict(task: PaymentCollectTask) -> dict:
    return {
        "id": task.id,
        "status": task.status,
        "agent_code": task.agent_code,
        "limit_count": task.limit_count,
        "since_date": task.since_date.isoformat() if task.since_date else "",
        "output_path": task.output_path,
        "error_msg": task.error_msg,
        "created_by": task.created_by,
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "started_at": task.started_at.isoformat() if task.started_at else "",
        "finished_at": task.finished_at.isoformat() if task.finished_at else "",
        "log_text": task.log_text or "",
    }


def run_payment_collect(task_id: int) -> None:
    """在后台线程里调起明源云采集脚本，完成后自动导入平台。"""
    with SessionLocal() as db:
        task = db.get(PaymentCollectTask, task_id)
        if task is None:
            return
        task.status = "running"
        task.started_at = dt.datetime.now()
        db.commit()

        output = settings.data_dir / "payment_collect" / f"payment_task_{task.id}.xlsx"
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(settings.mingyuan_collector),
            "--limit",
            str(task.limit_count or 5),
            "--output",
            str(output),
            "--force",
            "--concurrency",
            "3",
        ]
        if task.since_date:
            command += ["--since", task.since_date.isoformat()]

        username, password = get_source_credentials(db)
        if not username or not password:
            task.status = "failed"
            task.error_msg = "平台未配置明源云采集账号"
            task.finished_at = dt.datetime.now()
            db.commit()
            return
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["MY_USERNAME"] = username
        env["MY_PASSWORD"] = password

        try:
            proc = subprocess.run(
                command,
                cwd=str(settings.mingyuan_collector.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
                env=env,
            )
            log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            task.log_text = log[-20000:]
            task.output_path = str(output) if output.exists() else ""
            if proc.returncode != 0:
                task.status = "failed"
                task.error_msg = f"采集脚本退出码 {proc.returncode}"
            elif not output.exists():
                task.status = "failed"
                task.error_msg = "采集脚本未生成 Excel"
            else:
                with tenant_context(task.tenant_id):
                    result = import_payment_excel(db, output, force=False)
                task.status = "succeeded"
                task.error_msg = ""
                task.log_text += (
                    f"\n导入完成：新增 {result['inserted']}，更新 {result['updated']}，"
                    f"跳过 {result['skipped']}，新收款账户 {result['payees_created']}"
                )
        except Exception as exc:
            task.status = "failed"
            task.error_msg = str(exc)[:500]
        finally:
            task.finished_at = dt.datetime.now()
            db.commit()


def claim_payment_task(db: Session, agent_code: str) -> dict | None:
    """客户机 Agent 领取一个待执行的明源云采集任务。"""
    task = db.execute(
        select(PaymentCollectTask)
        .where(
            PaymentCollectTask.status == "queued",
            or_(PaymentCollectTask.agent_code == "", PaymentCollectTask.agent_code == agent_code),
        )
        .order_by(PaymentCollectTask.id)
        .limit(1)
    ).scalars().first()
    if task is None:
        return None
    username, password = get_source_credentials(db)
    if not username or not password:
        task.status = "failed"
        task.error_msg = "平台未配置明源云采集账号"
        task.finished_at = dt.datetime.now()
        db.commit()
        return None
    task.agent_code = agent_code
    task.status = "running"
    task.started_at = dt.datetime.now()
    db.commit()
    return {
        "task_id": task.id,
        "attempt": 1,
        "agent_code": agent_code,
        "limit": task.limit_count,
        "since": task.since_date.isoformat() if task.since_date else "",
        "output_name": f"payment_task_{task.id}.xlsx",
        "username": username,
        "password": password,
    }


def apply_payment_task_result(
    db: Session, task_id: int, payload: AgentTaskResult, agent_code: str
) -> dict:
    """Agent 回传采集结果：保存 Excel 并自动导入付款单据。"""
    task = db.get(PaymentCollectTask, task_id)
    if task is None:
        raise PaymentCollectError("采集任务不存在")
    if task.agent_code and task.agent_code != agent_code:
        raise PaymentCollectError("任务不属于该 Agent")
    if task.status not in ("queued", "running"):
        raise PaymentCollectError(f"任务状态为 {task.status}，不能回传结果")

    task.log_text = (payload.log_text or "")[-20000:]
    task.finished_at = dt.datetime.now()
    task.agent_code = agent_code

    excel = next(
        (item for item in payload.files or [] if item.name.lower().endswith((".xlsx", ".xlsm"))),
        None,
    )
    if payload.exit_code not in (0, None):
        task.status = "failed"
        task.error_msg = f"采集脚本退出码 {payload.exit_code}"
        db.commit()
        return task_dict(task)
    if excel is None:
        task.status = "failed"
        task.error_msg = "Agent 未回传 Excel"
        db.commit()
        return task_dict(task)

    try:
        content = base64.b64decode(excel.content_b64, validate=True)
    except Exception as exc:
        task.status = "failed"
        task.error_msg = "Excel 内容不是合法 base64"
        db.commit()
        return task_dict(task)
    if len(content) > 20 * 1024 * 1024:
        task.status = "failed"
        task.error_msg = "Excel 超过 20MB"
        db.commit()
        return task_dict(task)

    output = settings.data_dir / "payment_collect" / f"payment_task_{task.id}.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    task.output_path = str(output)

    try:
        with tenant_context(task.tenant_id):
            result = import_payment_excel(db, output, force=False)
        task.status = "succeeded"
        task.error_msg = ""
        task.log_text += (
            f"\n导入完成：新增 {result['inserted']}，更新 {result['updated']}，"
            f"跳过 {result['skipped']}，新收款账户 {result['payees_created']}"
        )
    except Exception as exc:
        task.status = "failed"
        task.error_msg = f"导入失败：{exc}"
    db.commit()
    return task_dict(task)
