from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now()


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class TenantMixin:
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class Enterprise(Base, TimestampMixin):
    __tablename__ = "enterprise"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tax_no: Mapped[str] = mapped_column(String(64), default="")
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Agent(Base, TimestampMixin, TenantMixin):
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="offline")
    last_heartbeat: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)


class UkeyDevice(Base, TimestampMixin, TenantMixin):
    __tablename__ = "ukey_device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(32), nullable=False)
    backend: Mapped[str] = mapped_column(String(16), default="gf3")
    agent_code: Mapped[str] = mapped_column(String(32), default="local")
    external_id: Mapped[str] = mapped_column(String(64), default="")
    gf3_cabinet: Mapped[int] = mapped_column(Integer, default=1)
    gf3_port: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    last_heartbeat: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    cert_subject: Mapped[str] = mapped_column(String(256), default="")
    cert_expires_at: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class BankAccount(Base, TimestampMixin, TenantMixin):
    __tablename__ = "bank_account"
    __table_args__ = (UniqueConstraint("bank_code", "account_no", name="uq_bank_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(32), nullable=False)
    account_no: Mapped[str] = mapped_column(String(64), nullable=False)
    account_name: Mapped[str] = mapped_column(String(128), default="")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    ukey_device_id: Mapped[int | None] = mapped_column(ForeignKey("ukey_device.id"), nullable=True)
    collect_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    collect_cron: Mapped[str] = mapped_column(String(64), default="0 8,18 * * *")
    collect_mode: Mapped[str] = mapped_column(String(16), default="both")
    days_back: Mapped[int] = mapped_column(Integer, default=30)
    watermark_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    driver_options: Mapped[dict] = mapped_column(JSON, default=dict)


class Credential(Base, TimestampMixin, TenantMixin):
    __tablename__ = "credential"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id"), nullable=False, unique=True)
    username_enc: Mapped[str] = mapped_column(Text, default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    pin_enc: Mapped[str] = mapped_column(Text, default="")
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class CollectionTask(Base, TimestampMixin, TenantMixin):
    __tablename__ = "collection_task"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_idempotency"),
        Index("ix_task_status_priority", "status", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("ukey_device.id"), nullable=True)
    agent_code: Mapped[str] = mapped_column(String(32), default="local")
    lease_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(32), default="")
    error_msg: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class TaskRun(Base, TimestampMixin, TenantMixin):
    __tablename__ = "task_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("collection_task.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    agent_id: Mapped[str] = mapped_column(String(64), default="local")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_text: Mapped[str] = mapped_column(Text, default="")
    screenshot_path: Mapped[str] = mapped_column(String(512), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class Artifact(Base, TimestampMixin, TenantMixin):
    __tablename__ = "artifact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_run.id"), nullable=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_account.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str] = mapped_column(String(256), default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(64), default="")
    parsed_status: Mapped[str] = mapped_column(String(16), default="pending")
    parse_error: Mapped[str] = mapped_column(Text, default="")


class Statement(Base, TimestampMixin, TenantMixin):
    __tablename__ = "statement"
    __table_args__ = (
        UniqueConstraint("dedupe_hash", name="uq_statement_dedupe"),
        Index("ix_statement_account_date", "account_id", "txn_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id"), nullable=False)
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifact.id"), nullable=True)
    txn_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    txn_time: Mapped[str] = mapped_column(String(16), default="")
    direction: Mapped[str] = mapped_column(String(8), default="credit")
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    counterparty_name: Mapped[str] = mapped_column(String(256), default="")
    counterparty_account: Mapped[str] = mapped_column(String(64), default="")
    counterparty_bank: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(String(256), default="")
    serial_no: Mapped[str] = mapped_column(String(64), default="")
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_receipt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_internal_doc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Receipt(Base, TimestampMixin, TenantMixin):
    __tablename__ = "receipt"
    __table_args__ = (Index("ix_receipt_account_date", "account_id", "txn_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id"), nullable=False)
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifact.id"), nullable=True)
    receipt_no: Mapped[str] = mapped_column(String(64), default="")
    txn_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    counterparty_name: Mapped[str] = mapped_column(String(256), default="")
    object_key: Mapped[str] = mapped_column(String(512), default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    matched_statement_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BalanceSnapshot(Base, TimestampMixin, TenantMixin):
    __tablename__ = "balance_snapshot"
    __table_args__ = (UniqueConstraint("account_id", "snapshot_date", name="uq_balance_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id"), nullable=False)
    snapshot_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="statement")
    is_continuous: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(256), default="")


class InternalDoc(Base, TimestampMixin, TenantMixin):
    __tablename__ = "internal_doc"
    __table_args__ = (Index("ix_internal_doc_date", "doc_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(16), default="other")
    doc_no: Mapped[str] = mapped_column(String(64), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), default="credit")
    counterparty: Mapped[str] = mapped_column(String(256), default="")
    doc_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open")
    source: Mapped[str] = mapped_column(String(16), default="import")
    imported_batch: Mapped[str] = mapped_column(String(64), default="")


class ReconBatch(Base, TimestampMixin, TenantMixin):
    __tablename__ = "recon_batch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    period_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open")
    rule: Mapped[str] = mapped_column(String(32), default="standard")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_count: Mapped[int] = mapped_column(Integer, default=0)
    diff_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))


class ReconItem(Base, TimestampMixin, TenantMixin):
    __tablename__ = "recon_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("recon_batch.id"), nullable=False)
    statement_id: Mapped[int | None] = mapped_column(ForeignKey("statement.id"), nullable=True)
    internal_doc_id: Mapped[int | None] = mapped_column(ForeignKey("internal_doc.id"), nullable=True)
    match_type: Mapped[str] = mapped_column(String(16), default="none")
    confidence: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    diff_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), default="unmatched")
    note: Mapped[str] = mapped_column(String(256), default="")
    candidates: Mapped[list] = mapped_column(JSON, default=list)


class AuditLog(Base, TimestampMixin, TenantMixin):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AppUser(Base, TimestampMixin, TenantMixin):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
