"""制单任务：批次按付款账户拆分，出纳下载批量文件到网银导入后回来标记完成。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.db import SessionLocal
from app.models import PaymentDraftBatch, PaymentDraftTask
from app.services import permissions
from app.services.banks import BANK_NAMES
from app.services.payment_draft import (
    BATCH_FAILED,
    BATCH_PARTIAL,
    BATCH_SUCCEEDED,
    DraftError,
    TASK_FAILED,
    TASK_SUCCEEDED,
    cancel_task,
    create_batch,
    _clean_log,
    mark_task_done,
    preview,
)
from app.web.routes import templates

router = APIRouter()

DRAFT_ROLES = {"cashier", "admin"}

TASK_LABELS = {
    "queued": "待执行",
    "running": "执行中",
    "review": "已导入待核对",
    "succeeded": "已制单",
    "failed": "已失败",
}
BATCH_LABELS = {
    "running": "进行中",
    "succeeded": "已完成",
    "partial": "部分完成",
    "failed": "已失败",
}


def _operator(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or "system"


def _role(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return permissions.effective_role(getattr(user, "role", None))


def _can_draft(request: Request) -> bool:
    return _role(request) in DRAFT_ROLES


def _back(msg: str = "", next_url: str = "/payment-drafts") -> RedirectResponse:
    target = next_url if next_url.startswith("/") else "/payment-drafts"
    if msg:
        target += ("&" if "?" in target else "?") + f"msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


@router.get("/payment-drafts")
def drafts_page(request: Request, msg: str = "", status: str = ""):
    with SessionLocal() as db:
        query = (
            select(PaymentDraftTask, PaymentDraftBatch)
            .join(PaymentDraftBatch, PaymentDraftTask.batch_id == PaymentDraftBatch.id)
            .order_by(PaymentDraftTask.id.desc())
            .limit(200)
        )
        if status:
            query = query.where(PaymentDraftTask.status == status)
        rows = db.execute(query).all()

    tasks = []
    for task, batch in rows:
        tasks.append(
            {
                "id": task.id,
                "batch_no": batch.batch_no,
                "bank_code": task.bank_code,
                "payer_name": task.payer_name,
                "payer_account_no": task.payer_account_no,
                "agent_code": task.agent_code,
                "doc_count": task.doc_count,
                "amount": task.amount,
                "status": task.status,
                "status_label": TASK_LABELS.get(task.status, task.status),
                "error_msg": task.error_msg,
                "error_code": task.error_code,
                "bank_batch_no": task.bank_batch_no,
                "has_file": bool(task.file_path),
                "has_log": bool(task.log_text or task.error_msg),
                "done_at": task.done_at,
            }
        )
    pending = sum(1 for item in tasks if item["status"] not in (TASK_SUCCEEDED, TASK_FAILED))
    return templates.TemplateResponse(
        request=request,
        name="payment_drafts.html",
        context={
            "page": "payment_drafts",
            "tasks": tasks,
            "pending": pending,
            "status": status,
            "status_labels": TASK_LABELS,
            "can_draft": _can_draft(request),
            "bank_names": BANK_NAMES,
            "msg": msg,
        },
    )


@router.get("/payment-drafts/{task_id}/log")
def draft_task_log(request: Request, task_id: int):
    if not _can_draft(request):
        return JSONResponse({"detail": "没有制单权限"}, status_code=403)
    with SessionLocal() as db:
        task = db.get(PaymentDraftTask, task_id)
        if task is None:
            return JSONResponse({"detail": "任务不存在"}, status_code=404)
        return JSONResponse(
            {
                "id": task.id,
                "status": task.status,
                "error_code": task.error_code,
                "error_msg": task.error_msg,
                "bank_batch_no": task.bank_batch_no,
                "log_text": _clean_log(task.log_text or task.error_msg or ""),
            }
        )


@router.post("/payments/drafts/preview")
def drafts_preview(request: Request, doc_ids: list[int] = Form(default=[])):
    if not _can_draft(request):
        return JSONResponse({"detail": "没有制单权限"}, status_code=403)
    with SessionLocal() as db:
        try:
            data = preview(db, doc_ids)
        except DraftError as exc:
            return JSONResponse({"detail": exc.message}, status_code=400)
    return data


@router.post("/payments/drafts")
def drafts_create(request: Request, doc_ids: list[int] = Form(default=[])):
    if not _can_draft(request):
        return JSONResponse({"detail": "没有制单权限"}, status_code=403)
    with SessionLocal() as db:
        try:
            batch = create_batch(db, doc_ids, operator=_operator(request))
        except DraftError as exc:
            return JSONResponse(
                {"detail": exc.message, "problems": exc.problems}, status_code=400
            )
    return {
        "ok": True,
        "batch_no": batch.batch_no,
        "redirect": f"/payment-drafts?msg={quote(f'已生成制单批次 {batch.batch_no}')}",
    }


@router.get("/payment-drafts/{task_id}/file")
def drafts_file(request: Request, task_id: int):
    with SessionLocal() as db:
        task = db.get(PaymentDraftTask, task_id)
        if task is None or not task.file_path:
            return _back("制单文件不存在", "/payment-drafts")
        path = Path(task.file_path)
        filename = f"{task.payer_account_no}_{task.bank_code}.xlsx"
    if not path.is_file():
        return _back("制单文件已丢失，请重新发起制单", "/payment-drafts")
    return FileResponse(path, filename=filename)


@router.post("/payment-drafts/{task_id}/done")
def drafts_done(request: Request, task_id: int):
    if not _can_draft(request):
        return _back("没有制单权限", "/payment-drafts")
    with SessionLocal() as db:
        try:
            task = mark_task_done(db, task_id, operator=_operator(request))
        except DraftError as exc:
            return _back(exc.message, "/payment-drafts")
        message = f"{task.payer_account_no} 已标记制单完成，{task.doc_count} 笔单据转为已制单"
    return _back(message, "/payment-drafts")


@router.post("/payment-drafts/{task_id}/cancel")
def drafts_cancel(request: Request, task_id: int):
    if not _can_draft(request):
        return _back("没有制单权限", "/payment-drafts")
    with SessionLocal() as db:
        try:
            task = cancel_task(db, task_id, operator=_operator(request))
        except DraftError as exc:
            return _back(exc.message, "/payment-drafts")
        message = f"{task.payer_account_no} 已撤回，{task.doc_count} 笔单据退回可制单"
    return _back(message, "/payment-drafts")
