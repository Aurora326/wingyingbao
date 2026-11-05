from __future__ import annotations

import datetime as dt
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import BankAccount, PayeeAccount, PaymentCollectTask, PaymentDoc
from app.services.payment import (
    PaymentError,
    confirm_accounts,
    export_draft,
    import_payment_excel,
    review_doc,
)
from app.services import permissions
from app.services.payment_collect import (
    PaymentCollectError,
    enqueue_payment_collect,
    get_source_credentials,
    get_source_schedule,
    run_payment_collect,
    task_dict,
)
from app.web.routes import templates

router = APIRouter()

STATUS_LABELS = {
    "pending_confirm": "待确认账户",
    "pending_review": "待出纳审核",
    "ready": "可制单",
    "drafted": "已制单",
    "rejected": "已退回",
}
STATUS_ORDER = ["pending_confirm", "pending_review", "ready", "drafted", "rejected"]


def _operator(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or "system"


def _redirect(msg: str = "") -> RedirectResponse:
    target = "/payments"
    if msg:
        from urllib.parse import quote

        target += f"?msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


@router.get("/payments")
def payments_page(request: Request, status: str = "", q: str = "", page: int = 1, size: int = 10):
    user = getattr(request.state, "user", None)
    if user is None or not permissions.can_menu(user.role, "payments"):
        return RedirectResponse("/", status_code=303)
    size = size if size in (10, 20, 50, 100) else 10
    with SessionLocal() as db:
        query = select(PaymentDoc)
        if status:
            query = query.where(PaymentDoc.status == status)
        if q:
            pattern = f"%{q}%"
            query = query.where(
                or_(
                    PaymentDoc.apply_no.like(pattern),
                    PaymentDoc.subject.like(pattern),
                    PaymentDoc.payee_name.like(pattern),
                    PaymentDoc.payer_name.like(pattern),
                    PaymentDoc.project_name.like(pattern),
                )
            )
        total = db.execute(query.with_only_columns(func.count(PaymentDoc.id)).order_by(None)).scalar_one()
        page = max(page, 1)
        docs = db.execute(
            query.order_by(PaymentDoc.apply_date.desc().nullslast(), PaymentDoc.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).scalars().all()
        counts = dict(
            db.execute(select(PaymentDoc.status, func.count(PaymentDoc.id)).group_by(PaymentDoc.status)).all()
        )
        accounts = db.execute(select(BankAccount).order_by(BankAccount.id)).scalars().all()
        payees = db.execute(
            select(PayeeAccount).where(PayeeAccount.status == "active").order_by(PayeeAccount.id.desc()).limit(300)
        ).scalars().all()
        collect_tasks = db.execute(
            select(PaymentCollectTask).order_by(PaymentCollectTask.id.desc()).limit(5)
        ).scalars().all()
        source_username, source_password = get_source_credentials(db)
        schedule = get_source_schedule(db)

    return templates.TemplateResponse(
        request=request,
        name="payments.html",
        context={
            "page": "payments",
            "docs": docs,
            "accounts": accounts,
            "payees": payees,
            "source_username": source_username,
            "source_configured": bool(source_username and source_password),
            "schedule_enabled": schedule["enabled"],
            "schedule_cron": schedule["cron"],
            "schedule_limit": schedule["limit"],
            "is_admin": permissions.can_menu(user.role, "payment_settings"),
            "status": status,
            "q": q,
            "page_no": page,
            "size": size,
            "total": total,
            "counts": counts,
            "status_labels": STATUS_LABELS,
            "status_order": STATUS_ORDER,
            "collect_tasks": [task_dict(t) for t in collect_tasks],
            "collector_available": settings.payment_collect_mode == "agent" or settings.mingyuan_collector.exists(),
            "msg": request.query_params.get("msg", ""),
        },
    )


@router.post("/payments/collect")
def payments_collect(
    request: Request,
    background_tasks: BackgroundTasks,
    limit: int = Form(5),
    since: str = Form(""),
):
    try:
        with SessionLocal() as db:
            task = enqueue_payment_collect(db, limit=limit, since=since, created_by=_operator(request))
    except PaymentCollectError as exc:
        return _redirect(f"提交采集失败：{exc}")
    if settings.payment_collect_mode == "local":
        background_tasks.add_task(run_payment_collect, task.id)
        return _redirect(f"采集任务 #{task.id} 已提交，完成后自动导入")
    return _redirect(f"采集任务 #{task.id} 已提交，等待客户机 Agent 领取")


@router.post("/payments/import")
async def payments_import(request: Request, file: UploadFile = File(...), force: bool = Form(False)):
    if not file.filename or Path(file.filename).suffix.lower() not in (".xlsx", ".xlsm"):
        return _redirect("请选择 .xlsx 文件")
    upload_dir = settings.data_dir / "payment_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"payment_{dt.datetime.now():%Y%m%d_%H%M%S}{Path(file.filename).suffix.lower()}"
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        with SessionLocal() as db:
            result = import_payment_excel(db, target, force=force)
    except Exception as exc:
        return _redirect(f"导入失败：{exc}")
    return _redirect(
        f"导入完成：新增 {result['inserted']}，更新 {result['updated']}，跳过 {result['skipped']}，新收款账户 {result['payees_created']}"
    )


@router.post("/payments/{doc_id}/confirm")
def payments_confirm(
    request: Request,
    doc_id: int,
    payer_account_id: int = Form(...),
    payee_account_id: int = Form(...),
    reason: str = Form(""),
):
    try:
        with SessionLocal() as db:
            confirm_accounts(db, doc_id, payer_account_id, payee_account_id, reason=reason, operator=_operator(request))
    except PaymentError as exc:
        return _redirect(f"确认失败：{exc}")
    return _redirect("账户已确认，已进入出纳审核")


@router.post("/payments/{doc_id}/edit")
async def payments_edit(request: Request, doc_id: int):
    user = getattr(request.state, "user", None)
    if user is None or not permissions.can_menu(user.role, "payments"):
        return RedirectResponse("/", status_code=303)
    form = await request.form()

    def value(name: str) -> str:
        return str(form.get(name) or "").strip()

    with SessionLocal() as db:
        doc = db.get(PaymentDoc, doc_id)
        if doc is None:
            return _redirect("付款单据不存在")
        apply_no = value("apply_no")
        if not apply_no:
            return _redirect("申请编号不能为空")
        try:
            amount = Decimal(value("amount") or "0")
            paid_amount = Decimal(value("paid_amount")) if value("paid_amount") else None
        except InvalidOperation:
            return _redirect("金额格式不正确")
        status = value("status") or doc.status
        if status not in STATUS_LABELS:
            return _redirect("单据状态不正确")
        apply_date = None
        if value("apply_date"):
            try:
                apply_date = dt.date.fromisoformat(value("apply_date"))
            except ValueError:
                return _redirect("申请日期格式不正确")

        doc.apply_no = apply_no
        doc.subject = value("subject")
        doc.applicant = value("applicant")
        doc.department = value("department")
        doc.apply_date = apply_date
        doc.approval_type = value("approval_type")
        doc.payee_name = value("payee_name")
        doc.payee_account_no = value("payee_account_no")
        doc.payee_bank = value("payee_bank")
        doc.payee_branch = value("payee_branch")
        doc.payer_name = value("payer_name")
        doc.payer_account_no = value("payer_account_no")
        doc.payer_bank = value("payer_bank")
        doc.amount = amount
        doc.paid_amount = paid_amount
        doc.currency = value("currency") or "CNY"
        doc.project_name = value("project_name")
        doc.contract_name = value("contract_name")
        doc.contract_no = value("contract_no")
        doc.approval_status = value("approval_status")
        doc.status = status
        doc.description = value("description")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return _redirect("申请编号已存在，请修改后再保存")
    return _redirect("单据已更新")


@router.post("/payments/{doc_id}/review")
def payments_review(
    request: Request,
    doc_id: int,
    action: str = Form(...),
    opinion: str = Form(""),
):
    try:
        with SessionLocal() as db:
            review_doc(db, doc_id, action, opinion=opinion, reviewer=_operator(request))
    except PaymentError as exc:
        return _redirect(f"审核失败：{exc}")
    return _redirect("审核完成")


@router.post("/payments/drafts/export")
def payments_export(request: Request, doc_ids: list[int] = Form(...)):
    try:
        with SessionLocal() as db:
            path, count = export_draft(db, doc_ids, operator=_operator(request))
    except PaymentError as exc:
        return _redirect(f"制单失败：{exc}")
    return _redirect(f"已生成制单草稿 {count} 条：{path.name}")
