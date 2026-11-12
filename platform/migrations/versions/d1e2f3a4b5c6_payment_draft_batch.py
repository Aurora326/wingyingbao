"""payment draft batch and task

Revision ID: d1e2f3a4b5c6
Revises: c3d4e5f6a7b8
Create Date: 2026-11-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_draft_batch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_no", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "batch_no", name="uq_draft_batch_no"),
    )

    op.create_table(
        "payment_draft_task",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("bank_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("payer_account_id", sa.Integer(), nullable=True),
        sa.Column("payer_account_no", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("payer_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("ukey_device_id", sa.Integer(), nullable=True),
        sa.Column("agent_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("file_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("error_msg", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("done_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["payment_draft_batch.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("payment_draft_task", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_payment_draft_task_batch_id"), ["batch_id"], unique=False)

    with op.batch_alter_table("payment_doc", schema=None) as batch_op:
        batch_op.add_column(sa.Column("draft_batch_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("draft_task_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payment_doc", schema=None) as batch_op:
        batch_op.drop_column("draft_task_id")
        batch_op.drop_column("draft_batch_id")
    with op.batch_alter_table("payment_draft_task", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_payment_draft_task_batch_id"))
    op.drop_table("payment_draft_task")
    op.drop_table("payment_draft_batch")
