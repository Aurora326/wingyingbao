from __future__ import annotations

import datetime as dt
import re
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import BankAccount, Enterprise, PayeeAccount, PaymentCollectTask, PaymentDoc, PaymentSourceAccount, SubAccount
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
from app.services.banks import BANK_NAMES
from app.web.routes import templates

router = APIRouter()

_ACCOUNT_NO_RE = re.compile(r"^[0-9][0-9\-]{6,}$")


def _valid_bank_account(value: str) -> bool:
    """真实的银行账号至少 8 位、以数字开头；占位数据（用账户名当账号）不放进来。"""
    return bool(_ACCOUNT_NO_RE.match(str(value or "").strip()))

STATUS_LABELS = {
    "pending_confirm": "待确认账户",
    "pending_review": "待出纳审核",
    "ready": "可制单",
    "drafting": "制单中",
    "drafted": "已制单",
    "rejected": "已退回",
}
STATUS_ORDER = ["pending_confirm", "ready", "drafting", "drafted"]


def _operator(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or "system"


def _redirect(msg: str = "", next_url: str = "") -> RedirectResponse:
    from urllib.parse import quote

    target = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/payments"
    if msg:
        sep = "&" if "?" in target else "?"
        target += f"{sep}msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


@router.get("/payments")
def payments_page(request: Request, status: str = "", q: str = "", payer: str = "", page: int = 1, size: int = 10):
    user = getattr(request.state, "user", None)
    if user is None or not permissions.can_menu(user.role, "payments"):
        return RedirectResponse("/", status_code=303)
    size = size if size in (10, 20, 50, 100) else 10
    with SessionLocal() as db:
        base = select(PaymentDoc)
        if q:
            pattern = f"%{q}%"
            base = base.where(
                or_(
                    PaymentDoc.apply_no.like(pattern),
                    PaymentDoc.subject.like(pattern),
                    PaymentDoc.payee_name.like(pattern),
                    PaymentDoc.payer_name.like(pattern),
                    PaymentDoc.project_name.like(pattern),
                )
            )
        if payer:
            base = base.where(PaymentDoc.payer_name == payer)
        # 各状态数量随搜索/付款单位一起变化，"全部"就是它们的合计
        counts = dict(
            db.execute(
                base.with_only_columns(PaymentDoc.status, func.count(PaymentDoc.id)).group_by(
                    PaymentDoc.status
                )
            ).all()
        )
        total_all = sum(counts.values())

        query = base.where(PaymentDoc.status == status) if status else base
        total = db.execute(query.with_only_columns(func.count(PaymentDoc.id)).order_by(None)).scalar_one()
        page = max(page, 1)
        docs = db.execute(
            query.order_by(PaymentDoc.apply_date.desc().nullslast(), PaymentDoc.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).scalars().all()
        payer_options = [
            row[0]
            for row in db.execute(
                select(PaymentDoc.payer_name)
                .where(PaymentDoc.payer_name.is_not(None), PaymentDoc.payer_name != "")
                .distinct()
                .order_by(PaymentDoc.payer_name)
            ).all()
        ]
        accounts = db.execute(select(BankAccount).order_by(BankAccount.id)).scalars().all()
        payees = db.execute(
            select(PayeeAccount).where(PayeeAccount.status == "active").order_by(PayeeAccount.payee_name).limit(500)
        ).scalars().all()
        all_payees = db.execute(
            select(PayeeAccount).order_by(PayeeAccount.payee_name).limit(1000)
        ).scalars().all()
        payee_data = [
            {
                "id": p.id,
                "name": p.payee_name,
                "account": p.account_no or "",
                "bank": p.bank_name or "",
                "branch": p.branch_name or "",
                "status": p.status or "",
            }
            for p in all_payees
        ]
        payer_data = [
            {
                "id": a.id,
                "name": a.account_name or "",
                "account": a.account_no or "",
                "bank": BANK_NAMES.get(a.bank_code, a.bank_code),
                "branch": "",
                "status": "active",
            }
            for a in accounts
            if a.payment_enabled and _valid_bank_account(a.account_no)
        ]
        bank_options = [{"code": code, "name": name} for code, name in BANK_NAMES.items() if code != "mock"]
        subs = db.execute(select(SubAccount)).scalars().all()
        sub_names = {sub.id: sub.ledger_name for sub in subs}
        sub_data = [
            {
                "id": sub.id,
                "parent_id": sub.parent_account_id,
                "sub_no": sub.sub_no,
                "name": sub.ledger_name or sub.project_name or sub.sub_no,
            }
            for sub in subs
        ]
        collect_tasks = db.execute(
            select(PaymentCollectTask).order_by(PaymentCollectTask.id.desc()).limit(5)
        ).scalars().all()
        source_username, source_password = get_source_credentials(db)
        oa_username, oa_password = get_source_credentials(db, "oa")
        schedule = get_source_schedule(db)
        today = dt.date.today()
        month_index = today.year * 12 + today.month - 3
        oa_since = dt.date(month_index // 12, month_index % 12 + 1, 1)

    return templates.TemplateResponse(
        request=request,
        name="payments.html",
        context={
            "page": "payments",
            "docs": docs,
            "accounts": accounts,
            "payees": payees,
            "all_payees": all_payees,
            "payee_data": payee_data,
            "payer_data": payer_data,
            "bank_options": bank_options,
            "sub_names": sub_names,
            "sub_data": sub_data,
            "bank_names": BANK_NAMES,
            "source_username": source_username,
            "source_configured": bool(source_username and source_password),
            "oa_configured": bool(oa_username and oa_password),
            "oa_since": oa_since.isoformat(),
            "schedule_enabled": schedule["enabled"],
            "schedule_cron": schedule["cron"],
            "schedule_limit": schedule["limit"],
            "is_admin": permissions.can_menu(user.role, "payment_settings"),
            "can_draft": permissions.effective_role(user.role) in {"cashier", "admin"},
            "status": status,
            "q": q,
            "payer": payer,
            "payer_options": payer_options,
            "page_no": page,
            "size": size,
            "total": total,
            "total_all": total_all,
            "counts": counts,
            "status_labels": STATUS_LABELS,
            "status_order": STATUS_ORDER,
            "collect_tasks": [task_dict(t) for t in collect_tasks],
            "collector_available": settings.payment_collect_mode == "agent" or settings.mingyuan_collector.exists(),
            "oa_collector_available": settings.payment_collect_mode == "agent" or settings.oa_collector.exists(),
            "msg": request.query_params.get("msg", ""),
        },
    )


@router.post("/payments/collect")
def payments_collect(
    request: Request,
    background_tasks: BackgroundTasks,
    limit: int = Form(200),
    limit_nocontract: int = Form(100),
    lookback_days: int = Form(30),
    since: str = Form(""),
    source: str = Form("mingyuan"),
    next: str = Form(""),
):
    try:
        with SessionLocal() as db:
            task = enqueue_payment_collect(
                db,
                limit=limit,
                limit_nocontract=limit_nocontract,
                lookback_days=lookback_days,
                since=since,
                created_by=_operator(request),
                source=source,
            )
            account = db.execute(
                select(PaymentSourceAccount).where(PaymentSourceAccount.source == source)
            ).scalars().first()
            if account is not None:
                if source == "oa":
                    account.collect_days = max(1, min(int(lookback_days), 365))
                else:
                    account.collect_limit = max(0, int(limit))
                    account.collect_nocontract = max(0, int(limit_nocontract))
                db.commit()
    except PaymentCollectError as exc:
        return _redirect(f"提交采集失败：{exc}")
    if settings.payment_collect_mode == "local":
        background_tasks.add_task(run_payment_collect, task.id)
        message = f"采集任务 #{task.id} 已提交，完成后自动导入"
    else:
        label = "OA" if source == "oa" else "明源云"
        message = f"{label}采集任务 #{task.id} 已提交，等待客户机 Agent 领取"
    if next in ("tasks:payment", "tasks:oa"):
        from urllib.parse import quote

        source_param = "oa" if next == "tasks:oa" else "payment"
        return RedirectResponse(f"/tasks?source={source_param}&msg={quote(message)}", status_code=303)
    return _redirect(message)


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

    next_url = value("next")
    with SessionLocal() as db:
        doc = db.get(PaymentDoc, doc_id)
        if doc is None:
            return _redirect("付款单据不存在", next_url)
        if doc.status == "drafting":
            return _redirect("单据正在制单中，如需修改请先在制单任务里撤回", next_url)
        apply_no = value("apply_no")
        if not apply_no:
            return _redirect("申请编号不能为空", next_url)
        try:
            amount = Decimal(value("amount") or "0")
            paid_amount = Decimal(value("paid_amount")) if value("paid_amount") else None
        except InvalidOperation:
            return _redirect("金额格式不正确", next_url)
        status = value("status") or doc.status
        if status not in STATUS_LABELS:
            return _redirect("单据状态不正确", next_url)
        apply_date = None
        if value("apply_date"):
            try:
                apply_date = dt.date.fromisoformat(value("apply_date"))
            except ValueError:
                return _redirect("申请日期格式不正确", next_url)

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

        payee_id = value("payee_account_id")
        payee = db.get(PayeeAccount, int(payee_id)) if payee_id.isdigit() else None
        if payee is not None:
            doc.payee_account_id = payee.id
            doc.payee_name = payee.payee_name
            doc.payee_account_no = payee.account_no
            doc.payee_bank = payee.bank_name
            doc.payee_branch = payee.branch_name
        else:
            linked = db.get(PayeeAccount, doc.payee_account_id) if doc.payee_account_id else None
            if linked is None or linked.account_no != doc.payee_account_no:
                doc.payee_account_id = None

        payer_id = value("payer_account_id")
        payer = db.get(BankAccount, int(payer_id)) if payer_id.isdigit() else None
        posted_sub_id = value("payer_sub_account_id")
        posted_sub = db.get(SubAccount, int(posted_sub_id)) if posted_sub_id.isdigit() else None
        if payer is not None:
            doc.payer_account_id = payer.id
            doc.payer_name = payer.account_name
            payer_account_no = payer.account_no
            if posted_sub is not None and posted_sub.parent_account_id == payer.id:
                doc.payer_sub_account_id = posted_sub.id
                payer_account_no = f"{payer.account_no}-{posted_sub.sub_no}"
            elif doc.payer_sub_account_id:
                sub = db.get(SubAccount, doc.payer_sub_account_id)
                if sub is not None and sub.parent_account_id == payer.id:
                    payer_account_no = f"{payer.account_no}-{sub.sub_no}"
                else:
                    doc.payer_sub_account_id = None
            doc.payer_account_no = payer_account_no
            doc.payer_bank = BANK_NAMES.get(payer.bank_code, payer.bank_code)
        else:
            linked = db.get(BankAccount, doc.payer_account_id) if doc.payer_account_id else None
            if linked is None or linked.account_no != doc.payer_account_no:
                doc.payer_account_id = None
                doc.payer_sub_account_id = None
        doc.amount = amount
        doc.paid_amount = paid_amount
        doc.currency = value("currency") or "CNY"
        doc.project_name = value("project_name")
        doc.contract_name = value("contract_name")
        doc.contract_no = value("contract_no")
        doc.approval_status = value("approval_status")
        doc.payment_status = value("payment_status")
        doc.approval_node = value("approval_node")
        doc.approval_handler = value("approval_handler")
        doc.status = status
        doc.description = value("description")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return _redirect("申请编号已存在，请修改后再保存", next_url)
    return _redirect("单据已更新", next_url)


def _payer_item(account: BankAccount) -> dict:
    return {
        "id": account.id,
        "name": account.account_name or "",
        "account": account.account_no or "",
        "bank": BANK_NAMES.get(account.bank_code, account.bank_code),
        "branch": "",
        "status": "active",
    }


def _payee_item(payee: PayeeAccount) -> dict:
    return {
        "id": payee.id,
        "name": payee.payee_name,
        "account": payee.account_no or "",
        "bank": payee.bank_name or "",
        "branch": payee.branch_name or "",
        "status": payee.status or "active",
    }


@router.post("/payments/accounts/quick-payee")
async def quick_create_payee(request: Request):
    user = getattr(request.state, "user", None)
    if user is None or not permissions.can_menu(user.role, "payments"):
        raise HTTPException(status_code=403, detail="无权限")
    form = await request.form()
    name = str(form.get("name") or "").strip()
    account_no = "".join(str(form.get("account_no") or "").split())
    bank = str(form.get("bank") or "").strip()
    branch = str(form.get("branch") or "").strip()
    if not name or not account_no:
        raise HTTPException(status_code=400, detail="收款单位和账号必须填写")
    if not re.fullmatch(r"[0-9A-Za-z\-]{6,64}", account_no):
        raise HTTPException(status_code=400, detail="账号格式不正确")
    with SessionLocal() as db:
        existing = db.execute(
            select(PayeeAccount).where(PayeeAccount.account_no == account_no)
        ).scalars().first()
        if existing is not None:
            return {"ok": True, "existing": True, "item": _payee_item(existing)}
        payee = PayeeAccount(
            payee_name=name[:256],
            account_no=account_no,
            bank_name=bank[:128],
            branch_name=branch[:128],
            status="active",
            source="付款协同",
        )
        db.add(payee)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(
                select(PayeeAccount).where(PayeeAccount.account_no == account_no)
            ).scalars().first()
            if existing is not None:
                return {"ok": True, "existing": True, "item": _payee_item(existing)}
            raise HTTPException(status_code=409, detail="账号已存在，保存失败")
        db.refresh(payee)
        return {"ok": True, "item": _payee_item(payee)}


@router.post("/payments/accounts/quick-payer")
async def quick_create_payer(request: Request):
    user = getattr(request.state, "user", None)
    if user is None or not permissions.can_edit_accounts(user.role):
        raise HTTPException(status_code=403, detail="只有管理员或资金经理可以新增付款账户")
    form = await request.form()
    name = str(form.get("name") or "").strip()
    account_no = "".join(str(form.get("account_no") or "").split())
    bank_code = str(form.get("bank_code") or "").strip()
    if not name or not account_no:
        raise HTTPException(status_code=400, detail="付款单位名称和账号必须填写")
    if not _valid_bank_account(account_no):
        raise HTTPException(status_code=400, detail="账号格式不正确")
    if bank_code not in BANK_NAMES:
        raise HTTPException(status_code=400, detail="请选择开户银行")
    with SessionLocal() as db:
        existing = db.execute(
            select(BankAccount).where(BankAccount.account_no == account_no)
        ).scalars().first()
        if existing is not None:
            return {"ok": True, "existing": True, "item": _payer_item(existing)}
        enterprise = db.execute(
            select(Enterprise).where(Enterprise.name == name)
        ).scalars().first()
        if enterprise is None:
            enterprise = Enterprise(name=name[:128])
            db.add(enterprise)
            db.flush()
        account = BankAccount(
            enterprise_id=enterprise.id,
            bank_code=bank_code,
            account_no=account_no,
            account_name=name[:128],
            collect_enabled=False,
            payment_enabled=True,
            has_ukey=False,
            usage_tag="付款协同新增",
        )
        db.add(account)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(
                select(BankAccount).where(BankAccount.account_no == account_no)
            ).scalars().first()
            if existing is not None:
                return {"ok": True, "existing": True, "item": _payer_item(existing)}
            raise HTTPException(status_code=409, detail="付款账户已存在，保存失败")
        db.refresh(account)
        return {"ok": True, "item": _payer_item(account)}


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
