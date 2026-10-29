from __future__ import annotations

import datetime as dt
import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Enterprise, InternalDoc, ReconBatch, ReconItem, Statement
from app.schemas import InternalDocCreate, ReconAction, ReconCreate
from app.services import doc_import, recon

router = APIRouter(prefix="/api/recon", tags=["recon"])


@router.post("/batches")
def create_batch(payload: ReconCreate, db: Session = Depends(get_db)):
    batch = recon.create_batch(db, payload.period_start, payload.period_end, payload.name, "ui")
    recon.run_batch(db, batch)
    return {"id": batch.id, "matched": batch.matched_count, "unmatched": batch.unmatched_count}


@router.get("/batches")
def list_batches(db: Session = Depends(get_db)):
    rows = db.execute(select(ReconBatch).order_by(ReconBatch.id.desc()).limit(50)).scalars().all()
    return {
        "items": [
            {
                "id": b.id,
                "name": b.name,
                "period_start": b.period_start,
                "period_end": b.period_end,
                "status": b.status,
                "matched_count": b.matched_count,
                "unmatched_count": b.unmatched_count,
                "diff_amount": str(b.diff_amount),
            }
            for b in rows
        ]
    }


@router.get("/batches/{batch_id}/items")
def batch_items(batch_id: int, status: str | None = None, db: Session = Depends(get_db)):
    query = select(ReconItem).where(ReconItem.batch_id == batch_id)
    if status:
        query = query.where(ReconItem.status == status)
    items = db.execute(query.order_by(ReconItem.id)).scalars().all()
    statements = {s.id: s for s in db.execute(select(Statement)).scalars().all()}
    docs = {d.id: d for d in db.execute(select(InternalDoc)).scalars().all()}
    result = []
    for item in items:
        statement = statements.get(item.statement_id) if item.statement_id else None
        doc = docs.get(item.internal_doc_id) if item.internal_doc_id else None
        result.append(
            {
                "id": item.id,
                "status": item.status,
                "match_type": item.match_type,
                "confidence": float(item.confidence or 0),
                "diff_amount": str(item.diff_amount),
                "statement": {
                    "id": statement.id,
                    "date": statement.txn_date,
                    "direction": statement.direction,
                    "amount": str(statement.amount),
                    "counterparty": statement.counterparty_name,
                    "summary": statement.summary,
                }
                if statement
                else None,
                "doc": {
                    "id": doc.id,
                    "doc_no": doc.doc_no,
                    "date": doc.doc_date,
                    "direction": doc.direction,
                    "amount": str(doc.amount),
                    "counterparty": doc.counterparty,
                }
                if doc
                else None,
                "candidates": item.candidates or [],
            }
        )
    return {"items": result}


@router.post("/items/{item_id}/confirm")
def confirm(item_id: int, payload: ReconAction, db: Session = Depends(get_db)):
    item = db.get(ReconItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="对账项不存在")
    recon.confirm_item(db, item, payload.internal_doc_id)
    return {"ok": True}


@router.post("/items/{item_id}/ignore")
def ignore(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ReconItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="对账项不存在")
    recon.ignore_item(db, item)
    return {"ok": True }


@router.post("/import")
async def import_internal_docs(file: UploadFile = File(...), db: Session = Depends(get_db)):
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="默认企业")
        db.add(enterprise)
        db.commit()
    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    batch = f"imp_{dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    target = settings.data_dir / "uploads" / f"{batch}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await file.read())
    inserted, skipped = doc_import.import_docs(db, enterprise.id, target, batch)
    return {"inserted": inserted, "skipped": skipped, "batch": batch}


@router.post("/docs")
def create_doc(payload: InternalDocCreate, db: Session = Depends(get_db)):
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="默认企业")
        db.add(enterprise)
        db.commit()
    doc = InternalDoc(
        enterprise_id=enterprise.id,
        type=payload.type,
        doc_no=payload.doc_no,
        amount=payload.amount,
        direction=payload.direction,
        counterparty=payload.counterparty,
        doc_date=payload.doc_date,
        source="manual",
    )
    db.add(doc)
    db.commit()
    return {"id": doc.id}


@router.get("/docs")
def list_docs(limit: int = 200, db: Session = Depends(get_db)):
    rows = db.execute(select(InternalDoc).order_by(InternalDoc.doc_date.desc()).limit(limit)).scalars().all()
    return {
        "items": [
            {
                "id": d.id,
                "doc_no": d.doc_no,
                "doc_date": d.doc_date,
                "amount": str(d.amount),
                "direction": d.direction,
                "counterparty": d.counterparty,
                "status": d.status,
                "source": d.source,
            }
            for d in rows
        ]
    }


@router.get("/batches/{batch_id}/export")
def export_batch(batch_id: int, db: Session = Depends(get_db)):
    from openpyxl import Workbook

    batch = db.get(ReconBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    items = db.execute(select(ReconItem).where(ReconItem.batch_id == batch_id)).scalars().all()
    statements = {s.id: s for s in db.execute(select(Statement)).scalars().all()}
    docs = {d.id: d for d in db.execute(select(InternalDoc)).scalars().all()}

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "对账差异"
    sheet.append(["状态", "匹配方式", "置信度", "银行日期", "银行方向", "银行金额", "银行对方", "单据日期", "单据号", "单据金额", "单据对方", "差额"])
    for item in items:
        statement = statements.get(item.statement_id) if item.statement_id else None
        doc = docs.get(item.internal_doc_id) if item.internal_doc_id else None
        sheet.append(
            [
                item.status,
                item.match_type,
                float(item.confidence or 0),
                statement.txn_date.isoformat() if statement else "",
                "收入" if statement and statement.direction == "credit" else ("支出" if statement else ""),
                str(statement.amount) if statement else "",
                statement.counterparty_name if statement else "",
                doc.doc_date.isoformat() if doc else "",
                doc.doc_no if doc else "",
                str(doc.amount) if doc else "",
                doc.counterparty if doc else "",
                str(item.diff_amount),
            ]
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    filename = f"recon_{batch.period_start}_{batch.period_end}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
