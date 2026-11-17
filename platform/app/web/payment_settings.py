from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AppUser, AuditLog, BookSet, CashierDelegation, CashierScope, PaymentDoc, PaymentSourceAccount
from app.services.payment_collect import get_source_credentials, save_source_credentials
from app.services.scheduler import cron_matches
from app.services.payee_admin import (
    PayeeError,
    approve_change,
    create_payee,
    reject_change,
    request_change,
)
from app.services import permissions
from app.web.routes import templates

router = APIRouter()

PAYMENT_TABS = {"source", "cashiers"}


def _operator(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or "system"


def _is_named(book: BookSet) -> bool:
    """没匹配出付款单位名的账套是导入残留，不参与出纳分配。"""
    return bool(book.name and book.name != "未命名付款单位")


def _redirect(request: Request, msg: str = "") -> RedirectResponse:
    tab = request.query_params.get("tab", "source")
    if tab not in PAYMENT_TABS:
        tab = "source"
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
def payment_settings_page(request: Request, tab: str = "source"):
    user = getattr(request.state, "user", None)
    if user is None or not permissions.can_menu(user.role, "payment_settings"):
        return RedirectResponse("/", status_code=303)
    if tab not in PAYMENT_TABS:
        tab = "source"
    with SessionLocal() as db:
        scopes = db.execute(select(CashierScope).order_by(CashierScope.id.desc())).scalars().all()
        delegations = db.execute(
            select(CashierDelegation).order_by(CashierDelegation.id.desc())
        ).scalars().all()
        users = db.execute(select(AppUser).order_by(AppUser.id)).scalars().all()
        book_sets = db.execute(select(BookSet).order_by(BookSet.id)).scalars().all()
        book_usage = dict(
            db.execute(
                select(PaymentDoc.book_set_id, func.count())
                .where(PaymentDoc.book_set_id.is_not(None))
                .group_by(PaymentDoc.book_set_id)
            ).all()
        )
        user_map = {u.id: u for u in users}
        book_map = {b.id: b for b in book_sets}
        source_username, source_password = get_source_credentials(db)
        source_configured = bool(source_username and source_password)
        oa_username, oa_password = get_source_credentials(db, "oa")
        oa_configured = bool(oa_username and oa_password)

    context = {
        "page": "payment_settings",
        "tab": tab,
        "source_username": source_username,
        "source_configured": source_configured,
        "oa_username": oa_username,
        "oa_configured": oa_configured,
        "scopes": scopes,
        "cashier_users": [u for u in users if u.role == "cashier"],
        "scope_owner": {s.book_set_id: user_map.get(s.user_id) for s in scopes if s.status == "active"},
        "delegations": delegations,
        "today": dt.date.today(),
        "users": users,
        "book_sets": book_sets,
        "used_books": [b for b in book_sets if book_usage.get(b.id) and _is_named(b)],
        "idle_books": [b for b in book_sets if not book_usage.get(b.id) and _is_named(b)],
        "book_usage": book_usage,
        "user_map": user_map,
        "book_map": book_map,
        "msg": request.query_params.get("msg", ""),
    }
    return templates.TemplateResponse(
        request=request,
        name="payment_settings.html",
        context=context,
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


@router.post("/payment-settings/cashiers")
def assign_cashiers(request: Request, user_id: int = Form(...), book_set_ids: list[int] = Form(default=[])):
    """把勾选的账套整体分配给一个出纳；已被别人占用的账套会转移过来。"""
    selected = {int(i) for i in book_set_ids}
    with SessionLocal() as db:
        target = db.get(AppUser, user_id)
        if target is None or target.role != "cashier":
            return _redirect(request, "请选择一个出纳账号")
        scopes = db.execute(select(CashierScope).where(CashierScope.role == "cashier")).scalars().all()
        owners = {s.book_set_id: s for s in scopes}
        removed = 0
        for book_set_id, scope in list(owners.items()):
            if scope.user_id == user_id and book_set_id not in selected:
                db.delete(scope)
                removed += 1
        added = 0
        for book_set_id in selected:
            scope = owners.get(book_set_id)
            if scope is None:
                db.add(CashierScope(user_id=user_id, book_set_id=book_set_id, role="cashier"))
                added += 1
            elif scope.user_id != user_id:
                scope.user_id = user_id
                added += 1
        db.add(
            AuditLog(
                actor=_operator(request),
                action="cashier_scope",
                target_type="app_user",
                target_id=str(user_id),
                detail={"display_name": target.display_name or target.username, "books": len(selected), "removed": removed},
            )
        )
        db.commit()
    return _redirect(request, f"{target.display_name or target.username}：已分配 {len(selected)} 个账套（新增或转移 {added}，收回 {removed}）")


@router.post("/payment-settings/delegations")
def create_delegation(
    request: Request,
    from_user_id: int = Form(...),
    to_user_id: int = Form(...),
    start_date: str = Form(""),
    end_date: str = Form(""),
    remark: str = Form(""),
):
    """出纳请假时把名下账套临时委托给另一名出纳，到期自动失效。"""
    try:
        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
    except ValueError:
        return _redirect(request, "请填写有效的委托起止日期")
    if end < start:
        return _redirect(request, "截止日期不能早于开始日期")
    if from_user_id == to_user_id:
        return _redirect(request, "委托方和受托方不能是同一个人")
    with SessionLocal() as db:
        src = db.get(AppUser, from_user_id)
        dst = db.get(AppUser, to_user_id)
        if src is None or dst is None or src.role != "cashier" or dst.role != "cashier":
            return _redirect(request, "委托双方都必须是出纳账号")
        overlap = db.execute(
            select(CashierDelegation).where(
                CashierDelegation.from_user_id == from_user_id,
                CashierDelegation.status == "active",
                CashierDelegation.start_date <= end,
                CashierDelegation.end_date >= start,
            )
        ).scalars().first()
        if overlap is not None:
            return _redirect(request, "该出纳在这段时间里已有委托，请先结束或调整日期")
        db.add(
            CashierDelegation(
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                start_date=start,
                end_date=end,
                remark=remark.strip(),
            )
        )
        db.add(
            AuditLog(
                actor=_operator(request),
                action="cashier_delegation",
                target_type="app_user",
                target_id=str(from_user_id),
                detail={
                    "from": src.display_name or src.username,
                    "to": dst.display_name or dst.username,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
        )
        db.commit()
    return _redirect(request, f"{src.display_name or src.username} 的账套已委托给 {dst.display_name or dst.username}")


@router.post("/payment-settings/delegations/{delegation_id}/close")
def close_delegation(request: Request, delegation_id: int):
    with SessionLocal() as db:
        row = db.get(CashierDelegation, delegation_id)
        if row is not None:
            row.status = "closed"
            db.commit()
    return _redirect(request, "委托已结束")


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
