from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import AppUser, BankAccount, BookSet, CashierScope, Enterprise, PayeeAccount, PaymentSourceAccount, SubAccount
from app.services.account_master import AccountMasterError, account_master_summary, import_account_master
from app.services.payment_collect import get_source_credentials, save_source_credentials
from app.services.scheduler import cron_matches
from app.services.banks import BANK_NAMES
from app.services.payee_admin import (
    PayeeError,
    approve_change,
    create_payee,
    list_payees,
    reject_change,
    request_change,
)
from app.services import permissions
from app.web.routes import templates

router = APIRouter()

PAYMENT_TABS = {"accounts", "source", "cashiers", "payees"}


def _operator(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or "system"


def _redirect(request: Request, msg: str = "") -> RedirectResponse:
    tab = request.query_params.get("tab", "accounts")
    if tab not in PAYMENT_TABS:
        tab = "accounts"
    target = f"/payment-settings?tab={tab}"
    if msg:
        from urllib.parse import quote

        target += f"&msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


def _redirect_after_source(request: Request, next_page: str, msg: str) -> RedirectResponse:
    if next_page in ("tasks:payment", "tasks:oa"):
        from urllib.parse import quote

        source = "oa" if next_page == "tasks:oa" else "payment"
        target = f"/tasks?source={source}"
        if msg:
            target += f"&msg={quote(msg)}"
        return RedirectResponse(target, status_code=303)
    if next_page == "payments":
        from urllib.parse import quote

        target = "/payments"
        if msg:
            target += f"?msg={quote(msg)}"
        return RedirectResponse(target, status_code=303)
    return _redirect(request, msg)


@router.get("/payment-settings")
def payment_settings_page(request: Request, tab: str = "accounts"):
    user = getattr(request.state, "user", None)
    if user is None or not permissions.can_menu(user.role, "payment_settings"):
        return RedirectResponse("/", status_code=303)
    if tab not in PAYMENT_TABS:
        tab = "accounts"
    with SessionLocal() as db:
        accounts = [
            a for a in db.execute(select(BankAccount).order_by(BankAccount.id)).scalars().all()
            if a.account_type
        ]
        subs = db.execute(select(SubAccount).order_by(SubAccount.parent_account_id, SubAccount.sub_no)).scalars().all()
        enterprises = {e.id: e.name for e in db.execute(select(Enterprise)).scalars().all()}
        books = {b.enterprise_id: b for b in db.execute(select(BookSet)).scalars().all()}
        scopes = db.execute(select(CashierScope).order_by(CashierScope.id.desc())).scalars().all()
        users = db.execute(select(AppUser).order_by(AppUser.id)).scalars().all()
        book_sets = db.execute(select(BookSet).order_by(BookSet.id)).scalars().all()
        payees = list_payees(db)
        sub_count: dict[int, int] = {}
        for sub in subs:
            sub_count[sub.parent_account_id] = sub_count.get(sub.parent_account_id, 0) + 1
        user_map = {u.id: u for u in users}
        book_map = {b.id: b for b in book_sets}
        summary = account_master_summary(db)
        source_username, source_password = get_source_credentials(db)
        source_configured = bool(source_username and source_password)
        oa_username, oa_password = get_source_credentials(db, "oa")
        oa_configured = bool(oa_username and oa_password)

    return templates.TemplateResponse(
        request=request,
        name="payment_settings.html",
        context={
            "page": "payments",
            "tab": tab,
            "accounts": accounts,
            "account_map": {a.id: a for a in accounts},
            "sub_accounts": subs,
            "enterprises": enterprises,
            "books": books,
            "summary": summary,
            "source_username": source_username,
            "source_configured": source_configured,
            "oa_username": oa_username,
            "oa_configured": oa_configured,
            "sub_count": sub_count,
            "bank_names": BANK_NAMES,
            "scopes": scopes,
            "users": users,
            "book_sets": book_sets,
            "user_map": user_map,
            "book_map": book_map,
            "payees": payees,
            "msg": request.query_params.get("msg", ""),
        },
    )


@router.post("/payment-settings/source-account")
def save_source_account(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    source: str = Form("mingyuan"),
):
    if source not in ("mingyuan", "oa"):
        return _redirect_after_source(request, next, "不支持的采集来源")
    try:
        with SessionLocal() as db:
            save_source_credentials(db, username.strip(), password, source=source)
    except Exception as exc:
        return _redirect_after_source(request, next, f"保存失败：{exc}")
    label = "OA" if source == "oa" else "明源云"
    return _redirect_after_source(request, next, f"{label}采集账号已保存，后续任务会随任务下发给执行器")


@router.post("/payment-settings/source-schedule")
def save_source_schedule(
    request: Request,
    enabled: str = Form(""),
    cron: str = Form("0 8 * * *"),
    source: str = Form("mingyuan"),
    next: str = Form(""),
):
    if source not in ("mingyuan", "oa"):
        return _redirect_after_source(request, next, "不支持的采集来源")
    cron = " ".join(cron.split())
    if len(cron.split()) != 5:
        return _redirect_after_source(request, next, "定时计划需要 5 段：分 时 日 月 周")
    try:
        cron_matches(cron, dt.datetime.now())
    except (TypeError, ValueError):
        return _redirect_after_source(request, next, "定时计划格式不正确，请检查分 时 日 月 周")
    with SessionLocal() as db:
        account = db.execute(
            select(PaymentSourceAccount).where(PaymentSourceAccount.source == source)
        ).scalars().first()
        if account is None:
            label = "OA" if source == "oa" else "明源云"
            return _redirect_after_source(request, next, f"请先配置{label}采集账号")
        account.collect_enabled = bool(enabled)
        account.collect_cron = cron
        db.commit()
    label = "OA" if source == "oa" else "明源云"
    return _redirect_after_source(request, next, f"{label}定时采集计划已保存")


@router.post("/payment-settings/accounts/import")
async def import_accounts(request: Request, file: UploadFile = File(...)):
    if not file.filename or Path(file.filename).suffix.lower() not in (".xlsx", ".xlsm"):
        return _redirect(request, "请选择账户主数据 Excel")
    upload_dir = settings.data_dir / "payment_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"accounts_{dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        with SessionLocal() as db:
            result = import_account_master(db, target)
    except AccountMasterError as exc:
        return _redirect(request, f"导入失败：{exc}")
    return _redirect(request,
        f"账户导入完成：新增 {result['accounts_created']}，更新 {result['accounts_updated']}，"
        f"子账簿新增 {result['sub_accounts_created']}，更新 {result['sub_accounts_updated']}"
    )


@router.post("/payment-settings/cashiers")
def create_cashier(request: Request, user_id: int = Form(...), book_set_id: int = Form(...), role: str = Form("cashier")):
    with SessionLocal() as db:
        scope = CashierScope(user_id=user_id, book_set_id=book_set_id, role=role)
        db.add(scope)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return _redirect(request, "该用户已分配该账套")
    return _redirect(request, "出纳分配已保存")


@router.post("/payment-settings/cashiers/{scope_id}/delete")
def delete_cashier(request: Request, scope_id: int):
    with SessionLocal() as db:
        scope = db.get(CashierScope, scope_id)
        if scope is not None:
            db.delete(scope)
            db.commit()
    return _redirect(request, "出纳分配已删除")


@router.post("/payment-settings/payees")
def add_payee(
    request: Request,
    payee_name: str = Form(...),
    account_no: str = Form(...),
    bank_name: str = Form(""),
    branch_name: str = Form(""),
    cnaps_code: str = Form(""),
):
    try:
        with SessionLocal() as db:
            create_payee(
                db,
                {
                    "payee_name": payee_name,
                    "account_no": account_no,
                    "bank_name": bank_name,
                    "branch_name": branch_name,
                    "cnaps_code": cnaps_code,
                },
                operator=_operator(request),
            )
    except PayeeError as exc:
        return _redirect(request, f"新增失败：{exc}")
    return _redirect(request, "收款账户已新增")


@router.post("/payment-settings/payees/{payee_id}/change")
def change_payee(
    request: Request,
    payee_id: int,
    payee_name: str = Form(""),
    account_no: str = Form(""),
    bank_name: str = Form(""),
    branch_name: str = Form(""),
    cnaps_code: str = Form(""),
    status: str = Form(""),
    reason: str = Form(""),
):
    values = {
        "payee_name": payee_name or None,
        "account_no": account_no or None,
        "bank_name": bank_name or None,
        "branch_name": branch_name or None,
        "cnaps_code": cnaps_code or None,
        "status": status or None,
    }
    try:
        with SessionLocal() as db:
            request_change(db, payee_id, values, reason=reason, operator=_operator(request))
    except PayeeError as exc:
        return _redirect(request, f"变更失败：{exc}")
    return _redirect(request, "收款账户已更新")


@router.post("/payment-settings/payees/changes/{change_id}/approve")
def approve_payee_change(request: Request, change_id: int):
    try:
        with SessionLocal() as db:
            approve_change(db, change_id, operator=_operator(request))
    except PayeeError as exc:
        return _redirect(request, f"审批失败：{exc}")
    return _redirect(request, "变更已通过并生效")


@router.post("/payment-settings/payees/changes/{change_id}/reject")
def reject_payee_change(request: Request, change_id: int):
    try:
        with SessionLocal() as db:
            reject_change(db, change_id, operator=_operator(request))
    except PayeeError as exc:
        return _redirect(request, f"审批失败：{exc}")
    return _redirect(request, "变更已驳回")
