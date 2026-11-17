"""个人设置：修改自己的密码，出纳自助发起与结束账套委托。"""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote, unquote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppUser, AuditLog, BookSet, CashierDelegation, CashierScope
from app.services import permissions, security

from app.web.routes import templates

router = APIRouter()

MAX_DELEGATION_DAYS = 60


def _redirect(msg: str = "") -> RedirectResponse:
    target = "/profile"
    if msg:
        target += f"?msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


def _profile_flash(message: str, kind: str = "error") -> RedirectResponse:
    """密码表单的提示只在页面内出现一次，不把中文原因带进地址栏。"""
    response = RedirectResponse("/profile", status_code=303)
    response.set_cookie(
        "profile_flash",
        quote(f"{kind}:{message}"),
        max_age=30,
        httponly=True,
        samesite="lax",
    )
    return response


def _current(request: Request) -> AppUser | None:
    user = getattr(request.state, "user", None)
    if user is None:
        return None
    return user


@router.get("/profile")
def profile_page(request: Request, msg: str = ""):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    pw_flash = request.cookies.get("profile_flash", "")
    pw_kind = pw_message = ""
    if pw_flash:
        decoded = unquote(pw_flash)
        if ":" in decoded:
            pw_kind, pw_message = decoded.split(":", 1)
    role = permissions.effective_role(user.role)
    books: list[str] = []
    outgoing: list[dict] = []
    incoming: list[dict] = []
    peers: list[AppUser] = []
    with SessionLocal() as db:
        me = db.get(AppUser, user.id)
        if me is None:
            return RedirectResponse("/login", status_code=303)
        if role == "cashier":
            books = [
                row[0]
                for row in db.execute(
                    select(BookSet.name)
                    .join(CashierScope, CashierScope.book_set_id == BookSet.id)
                    .where(
                        CashierScope.user_id == me.id,
                        CashierScope.role == "cashier",
                        CashierScope.status == "active",
                    )
                    .order_by(BookSet.name)
                ).all()
            ]
            names = {
                u.id: (u.display_name or u.username)
                for u in db.execute(select(AppUser)).scalars().all()
            }
            outgoing = [
                {
                    "id": row.id,
                    "peer": names.get(row.to_user_id, "—"),
                    "start": row.start_date,
                    "end": row.end_date,
                    "status": row.status,
                    "remark": row.remark,
                }
                for row in db.execute(
                    select(CashierDelegation)
                    .where(CashierDelegation.from_user_id == me.id)
                    .order_by(CashierDelegation.id.desc())
                ).scalars().all()
            ]
            incoming = [
                {
                    "id": row.id,
                    "peer": names.get(row.from_user_id, "—"),
                    "start": row.start_date,
                    "end": row.end_date,
                    "status": row.status,
                    "remark": row.remark,
                }
                for row in db.execute(
                    select(CashierDelegation)
                    .where(CashierDelegation.to_user_id == me.id)
                    .order_by(CashierDelegation.id.desc())
                ).scalars().all()
            ]
            peers = db.execute(
                select(AppUser)
                .where(AppUser.role == "cashier", AppUser.is_active.is_(True), AppUser.id != me.id)
                .order_by(AppUser.id)
            ).scalars().all()
        profile = {
            "display_name": me.display_name or me.username,
            "username": me.username,
            "role_label": permissions.role_label(me.role),
            "created_at": me.created_at,
        }
    response = templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "page": "profile",
            "profile": profile,
            "role": role,
            "books": books,
            "outgoing": outgoing,
            "incoming": incoming,
            "peers": peers,
            "today": dt.date.today(),
            "max_days": MAX_DELEGATION_DAYS,
            "msg": msg,
            "pw_error": pw_message if pw_kind == "error" else "",
            "pw_ok": pw_message if pw_kind == "ok" else "",
        },
    )
    if pw_flash:
        response.delete_cookie("profile_flash")
    return response


@router.post("/profile/password")
def change_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    error = security.validate_password(new_password, user.username)
    if error:
        return _profile_flash(error)
    if new_password == old_password:
        return _profile_flash("新密码不能与原密码相同")
    with SessionLocal() as db:
        me = db.get(AppUser, user.id)
        if me is None or not security.verify_password(old_password, me.password_hash):
            return _profile_flash("原密码不正确")
        me.password_hash = security.hash_password(new_password)
        db.add(
            AuditLog(
                actor=me.username,
                action="user_password_self",
                target_type="app_user",
                target_id=str(me.id),
                detail={"username": me.username},
            )
        )
        db.commit()
    response = _profile_flash("密码已修改，下次登录请使用新密码", "ok")
    response.delete_cookie("pwd_change")
    return response


@router.post("/profile/delegations")
def create_my_delegation(
    request: Request,
    to_user_id: int = Form(...),
    start_date: str = Form(""),
    end_date: str = Form(""),
    remark: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if permissions.effective_role(user.role) != "cashier":
        return _redirect("只有出纳可以发起委托")
    try:
        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
    except ValueError:
        return _redirect("请填写有效的委托起止日期")
    if end < start:
        return _redirect("截止日期不能早于开始日期")
    if (end - start).days + 1 > MAX_DELEGATION_DAYS:
        return _redirect(f"单次委托不能超过 {MAX_DELEGATION_DAYS} 天")
    if to_user_id == user.id:
        return _redirect("不能委托给自己")
    with SessionLocal() as db:
        dst = db.get(AppUser, to_user_id)
        if dst is None or dst.role != "cashier" or not dst.is_active:
            return _redirect("受托人必须是启用中的出纳")
        overlap = db.execute(
            select(CashierDelegation).where(
                CashierDelegation.from_user_id == user.id,
                CashierDelegation.status == "active",
                CashierDelegation.start_date <= end,
                CashierDelegation.end_date >= start,
            )
        ).scalars().first()
        if overlap is not None:
            return _redirect("这段时间里已经有委托了，请先结束或调整日期")
        db.add(
            CashierDelegation(
                from_user_id=user.id,
                to_user_id=to_user_id,
                start_date=start,
                end_date=end,
                remark=remark.strip(),
            )
        )
        db.add(
            AuditLog(
                actor=user.username,
                action="cashier_delegation_self",
                target_type="app_user",
                target_id=str(user.id),
                detail={
                    "to": dst.display_name or dst.username,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
        )
        db.commit()
    return _redirect(f"已将账套委托给 {dst.display_name or dst.username}，委托到期自动收回")


@router.post("/profile/delegations/{delegation_id}/close")
def close_my_delegation(request: Request, delegation_id: int):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as db:
        row = db.get(CashierDelegation, delegation_id)
        if row is not None and row.from_user_id == user.id:
            row.status = "closed"
            db.commit()
    return _redirect("委托已结束")
