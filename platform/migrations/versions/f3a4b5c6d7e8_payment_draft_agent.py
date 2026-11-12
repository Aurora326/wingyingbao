"""payment draft task agent fields

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-11-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("payment_draft_task", schema=None) as batch_op:
        batch_op.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("finished_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("lease_until", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("error_code", sa.String(length=32), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("bank_batch_no", sa.String(length=64), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("log_text", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payment_draft_task", schema=None) as batch_op:
        batch_op.drop_column("log_text")
        batch_op.drop_column("bank_batch_no")
        batch_op.drop_column("error_code")
        batch_op.drop_column("attempts")
        batch_op.drop_column("lease_until")
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
