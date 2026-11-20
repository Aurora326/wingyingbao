"""收付款账户基础资料：付款账户和收款账户放在一个列表里维护。"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.models import (
    Artifact,
    BalanceSnapshot,
    BankAccount,
    CollectionTask,
    Credential,
    Enterprise,
    PayeeAccount,
    PayeeAccountChange,
    PaymentDoc,
    Receipt,
    Statement,
    SubAccount,
)
from app.services import permissions
from app.services.abc_ledger import sub_region
from app.services.audit import snapshot, write_changes, write_create, write_delete
from app.services.banks import BANK_NAMES
from app.services.payment_master import sync_payment_master
from app.web.pagination import is_hx, normalize_size, paginate_items
from app.web.routes import templates

router = APIRouter()

PAGE_SIZE = 10


def _actor(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or ""


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _redirect(msg: str = "") -> RedirectResponse:
    from urllib.parse import quote

    target = "/master-data"
    if msg:
        target += f"?msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


def _denied(request: Request):
    user = getattr(request.state, "user", None)
    if user is not None and permissions.can_menu(user.role, "master_data"):
        return None
    return RedirectResponse("/", status_code=303)


def _is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _fragment(
    request: Request,
    msg: str = "",
    q: str = "",
    type_: str = "",
    page: int = 1,
    size: int = PAGE_SIZE,
    spage: int = 1,
    ssize: int = PAGE_SIZE,
):
    """htmx 请求：只回渲染后的内容区域，提示走 HX-Trigger 交给 toast。"""
    with SessionLocal() as db:
        if request.headers.get("HX-Target") == "sub-region":
            sub_data = sub_region(db)
            sub_paged = paginate_items(sub_data["sub_rows"], spage, ssize, default_size=10)
            ctx = {
                **sub_data,
                "sub_page_rows": sub_paged["items"],
                "sub_pager": sub_paged,
                "sub_params": {
                    "tab": "sub",
                    "ssize": sub_paged["size"],
                    "page": request.query_params.get("page", ""),
                    "size": request.query_params.get("size", ""),
                    "dpage": request.query_params.get("dpage", ""),
                    "dsize": request.query_params.get("dsize", ""),
                    "dq": request.query_params.get("dq", ""),
                },
                "msg": msg,
            }
            name = "_sub_accounts_panel.html"
        else:
            ctx = _region_context(db, q, type_, page, size, msg)
            name = "_master_data_region.html"
    response = templates.TemplateResponse(request=request, name=name, context=ctx)
    if msg:
        # HTTP 头只能用 ASCII，中文由 json 转义，前端解析后仍是原文。
        response.headers["HX-Trigger"] = json.dumps({"showMessage": {"value": msg}})
    return response


def _int_param(params, key: str, default: int) -> int:
    try:
        return int(params.get(key, default) or default)
    except ValueError:
        return default


def _query_parts(request: Request) -> tuple[str, str, int, int, int, int]:
    """筛选条件跟着 hx 请求走，片段渲染后不会跳回默认列表。

    htmx 表单 POST 的筛选参数不在请求 URL 上，改从当前页面地址取，
    这样保存后列表还停在原来的搜索和页码。
    """
    params: dict[str, str] = dict(request.query_params)
    if not any(key in params for key in ("q", "type", "page", "size")):
        current = request.headers.get("HX-Current-URL", "")
        if current:
            parsed = parse_qs(urlparse(current).query)
            params = {key: values[0] for key, values in parsed.items() if values}
    return (
        params.get("q", ""),
        params.get("type", ""),
        _int_param(params, "page", 1),
        normalize_size(_int_param(params, "size", PAGE_SIZE), PAGE_SIZE),
        _int_param(params, "spage", 1),
        normalize_size(_int_param(params, "ssize", PAGE_SIZE), PAGE_SIZE),
    )


def _done(request: Request, msg: str = ""):
    """写操作统一出口：局部请求回片段，普通请求维持原来的整页跳转。"""
    if not _is_hx(request):
        return _redirect(msg)
    q, type_, page, size, spage, ssize = _query_parts(request)
    return _fragment(request, msg, q, type_, page, size, spage, ssize)


def _build_rows(db) -> list[dict]:
    enterprises = {e.id: e.name for e in db.execute(select(Enterprise)).scalars().all()}
    payer_docs = dict(
        db.execute(
            select(PaymentDoc.payer_account_id, func.count()).group_by(PaymentDoc.payer_account_id)
        ).all()
    )
    payee_docs = dict(
        db.execute(
            select(PaymentDoc.payee_account_id, func.count()).group_by(PaymentDoc.payee_account_id)
        ).all()
    )
    rows: list[dict] = []
    for account in db.execute(select(BankAccount).order_by(BankAccount.id)).scalars().all():
        rows.append(
            {
                "type": "payer",
                "type_label": "付款账户",
                "id": account.id,
                "name": account.account_name or enterprises.get(account.enterprise_id, ""),
                "account_no": account.account_no,
                "bank": BANK_NAMES.get(account.bank_code, account.bank_code),
                "branch": "",
                "status": account.account_status or "正常",
                "source": "账户主数据",
                "docs": payer_docs.get(account.id, 0),
            }
        )
    for payee in db.execute(select(PayeeAccount).order_by(PayeeAccount.id)).scalars().all():
        rows.append(
            {
                "type": "payee",
                "type_label": "收款账户",
                "id": payee.id,
                "name": payee.payee_name,
                "account_no": payee.account_no,
                "bank": payee.bank_name,
                "branch": payee.branch_name,
                "status": "启用" if payee.status == "active" else "停用",
                "source": payee.source or "手工",
                "docs": payee_docs.get(payee.id, 0),
                "_raw_status": payee.status,
                "remark": payee.remark or "",
            }
        )
    return rows


def _region_context(db, q: str, type_: str, page: int, size: int, msg: str) -> dict:
    """整页和局部片段共用的一份页面数据。"""
    rows = _build_rows(db)
    unlinked = db.execute(
        select(func.count()).where(
            or_(PaymentDoc.payer_account_id.is_(None), PaymentDoc.payee_account_id.is_(None))
        )
    ).scalar()
    sub_region_data = sub_region(db)
    payer_total = sum(1 for row in rows if row["type"] == "payer")
    payee_total = len(rows) - payer_total
    keyword = q.strip()
    if keyword:
        rows = [
            row
            for row in rows
            if keyword in row["name"]
            or keyword in row["account_no"]
            or keyword in row["bank"]
            or keyword in row["branch"]
        ]
    if type_ in ("payer", "payee"):
        rows = [row for row in rows if row["type"] == type_]
    paged = paginate_items(rows, page, size, default_size=PAGE_SIZE)
    return {
        "rows": paged["items"],
        "q": keyword,
        "type": type_,
        "page_no": paged["page"],
        "pages": paged["pages"],
        "total": paged["total"],
        "size": paged["size"],
        "msg": msg,
        "payer_total": payer_total,
        "payee_total": payee_total,
        "unlinked_docs": unlinked,
        **sub_region_data,
    }


@router.get("/master-data")
def master_data_page(
    request: Request,
    q: str = "",
    type: str = "",
    page: int = 1,
    size: int = PAGE_SIZE,
    msg: str = "",
):
    denied = _denied(request)
    if denied:
        return denied
    with SessionLocal() as db:
        context = _region_context(db, q, type, page, size, msg)
    context["page"] = "master_data"
    tpl = "_master_data_region.html" if is_hx(request, "master-region") else "master_data.html"
    return templates.TemplateResponse(
        request=request,
        name=tpl,
        context=context,
    )


@router.post("/master-data/sync")
def sync_master_data(request: Request):
    with SessionLocal() as db:
        result = sync_payment_master(db)
    payees = result["payees"]
    payers = result["payers"]
    return _done(
        request,
        f"同步完成：收款账户 新增 {payees['created']}、关联 {payees['linked']}、补全 {payees['enriched']}；"
        f"付款账户 关联 {payers['linked']} 条，未匹配 {payers['unmatched']} 条（付款账户在账户管理中维护）",
    )


@router.post("/master-data/payee")
def create_payee_from_master(
    request: Request,
    name: str = Form(...),
    account_no: str = Form(""),
    bank: str = Form(""),
    branch: str = Form(""),
):
    name = name.strip()
    account_no = "".join(account_no.split())
    if not name:
        return _done(request, "收款单位不能为空")
    with SessionLocal() as db:
        exists = None
        if account_no:
            exists = db.execute(
                select(PayeeAccount).where(PayeeAccount.account_no == account_no)
            ).scalars().first()
        if exists is not None:
            return _done(request, "该收款账号已存在")
        db.add(
            PayeeAccount(
                payee_name=name[:256],
                account_no=account_no,
                bank_name=bank.strip()[:128],
                branch_name=branch.strip()[:128],
                status="active",
                source="手工",
            )
        )
        db.commit()
    return _done(request, "收款账户已新增")


@router.post("/master-data/update")
def update_master_account(
    request: Request,
    type: str = Form(...),
    id: int = Form(...),
    name: str = Form(""),
    account_no: str = Form(""),
    bank: str = Form(""),
    branch: str = Form(""),
    status: str = Form(""),
    reason: str = Form(""),
):
    with SessionLocal() as db:
        if type == "payer":
            account = db.get(BankAccount, id)
            if account is None:
                return _done(request, "付款账户不存在")
            before = snapshot(account, "bank_account")
            account.account_name = name.strip()[:128]
            write_changes(
                db,
                account,
                before,
                actor=_actor(request),
                action="account_update",
                target_type="bank_account",
                reason=reason.strip(),
                ip=_client_ip(request),
            )
            db.commit()
            return _done(request, "付款账户已更新")
        payee = db.get(PayeeAccount, id)
        if payee is None:
            return _done(request, "收款账户不存在")
        before = snapshot(payee, "payee_account")
        new_account_no = "".join(account_no.split())
        if new_account_no and new_account_no != payee.account_no:
            exists = db.execute(
                select(PayeeAccount).where(
                    PayeeAccount.account_no == new_account_no,
                    PayeeAccount.id != payee.id,
                )
            ).scalars().first()
            if exists is not None:
                return _done(request, "该收款账号已被其他账户使用")
        payee.payee_name = name.strip()[:256] or payee.payee_name
        payee.account_no = new_account_no or payee.account_no
        payee.bank_name = bank.strip()[:128]
        payee.branch_name = branch.strip()[:128]
        if status in ("active", "disabled"):
            payee.status = status
        write_changes(
            db,
            payee,
            before,
            actor=_actor(request),
            action="payee_change",
            target_type="payee_account",
            reason=reason.strip(),
            ip=_client_ip(request),
        )
        db.commit()
    return _done(request, "收款账户已更新")


@router.get("/master-data/delete")
def delete_master_account_page():
    return _redirect()


@router.post("/master-data/delete")
def delete_master_account(request: Request, type: str = Form(...), id: int = Form(...)):
    with SessionLocal() as db:
        if type == "payer":
            account = db.get(BankAccount, id)
            if account is None:
                return _done(request, "付款账户不存在")
            refs = {
                "付款单据": PaymentDoc.payer_account_id,
                "子账簿": SubAccount.parent_account_id,
                "下级账户": BankAccount.parent_account_id,
                "采集任务": CollectionTask.account_id,
                "流水": Statement.account_id,
                "回单": Receipt.account_id,
                "余额快照": BalanceSnapshot.account_id,
                "原始文件": Artifact.account_id,
                "采集凭证": Credential.bank_account_id,
            }
            used = []
            for label, column in refs.items():
                count = db.execute(select(func.count()).where(column == id)).scalar() or 0
                if count:
                    used.append(f"{label} {count}")
            if used:
                return _done(request, f"该付款账户还有 {'、'.join(used)}，不能删除")
            write_delete(
                db,
                account,
                actor=_actor(request),
                action="account_delete",
                target_type="bank_account",
                ip=_client_ip(request),
            )
            db.delete(account)
            db.commit()
            return _done(request, "付款账户已删除")
        if type == "payee":
            payee = db.get(PayeeAccount, id)
            if payee is None:
                return _done(request, "收款账户不存在")
            refs = {
                "付款单据": PaymentDoc.payee_account_id,
                "变更记录": PayeeAccountChange.payee_account_id,
            }
            used = []
            for label, column in refs.items():
                count = db.execute(select(func.count()).where(column == id)).scalar() or 0
                if count:
                    used.append(f"{label} {count}")
            if used:
                return _done(request, f"该收款账户还有 {'、'.join(used)}，不能删除")
            write_delete(
                db,
                payee,
                actor=_actor(request),
                action="payee_delete",
                target_type="payee_account",
                ip=_client_ip(request),
            )
            db.delete(payee)
            db.commit()
            return _done(request, "收款账户已删除")
    return _done(request, "账户类型不正确")


@router.post("/master-data/sub-account")
def create_sub_account(
    request: Request,
    parent_account_id: int = Form(0),
    sub_no: str = Form(""),
    ledger_name: str = Form(""),
    project_name: str = Form(""),
):
    code = sub_no.strip()
    if not parent_account_id:
        return _done(request, "请选择主账户")
    if not code:
        return _done(request, "子账簿号不能为空")
    with SessionLocal() as db:
        parent = db.get(BankAccount, parent_account_id)
        if parent is None:
            return _done(request, "主账户不存在")
        exists = db.execute(
            select(SubAccount).where(
                SubAccount.parent_account_id == parent.id,
                SubAccount.sub_no == code,
            )
        ).scalars().first()
        if exists is not None:
            return _done(request, "该主账户下已存在这个子账簿号")
        sub = SubAccount(
            parent_account_id=parent.id,
            sub_no=code[:32],
            ledger_name=ledger_name.strip()[:256],
            project_name=project_name.strip()[:256],
            status="正常",
        )
        db.add(sub)
        db.flush()
        write_create(
            db,
            sub,
            actor=_actor(request),
            action="sub_account_create",
            target_type="sub_account",
            ip=_client_ip(request),
        )
        db.commit()
    return _done(request, "子账簿已新增")


@router.post("/master-data/sub-account/{sub_id}/status")
def toggle_sub_account(request: Request, sub_id: int, status: str = Form(...)):
    if status not in ("正常", "停用"):
        return _done(request, "状态不正确")
    with SessionLocal() as db:
        sub = db.get(SubAccount, sub_id)
        if sub is None:
            return _done(request, "子账簿不存在")
        before = snapshot(sub, "sub_account")
        sub.status = status
        write_changes(
            db,
            sub,
            before,
            actor=_actor(request),
            action="sub_account_status",
            target_type="sub_account",
            ip=_client_ip(request),
        )
        db.commit()
        docs = db.execute(
            select(func.count()).where(PaymentDoc.payer_sub_account_id == sub.id)
        ).scalar() or 0
    if status == "停用" and docs:
        return _done(request, f"子账簿已停用，但仍有 {docs} 条单据引用它")
    return _done(request, "子账簿已停用" if status == "停用" else "子账簿已启用")
