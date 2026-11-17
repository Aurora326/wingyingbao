"""cashier scope one-per-book and delegation

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-11-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c6d7e8f9a0b1"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cashier_scope", schema=None) as batch_op:
        batch_op.drop_constraint("uq_cashier_scope", type_="unique")
        batch_op.create_unique_constraint(
            "uq_cashier_scope", ["tenant_id", "book_set_id", "role"]
        )
        batch_op.create_index("ix_cashier_scope_user", ["user_id"])

    op.create_table(
        "cashier_delegation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("from_user_id", sa.Integer(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("to_user_id", sa.Integer(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("remark", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cashier_delegation_to", "cashier_delegation", ["to_user_id"])


def downgrade() -> None:
    op.drop_index("ix_cashier_delegation_to", table_name="cashier_delegation")
    op.drop_table("cashier_delegation")
    with op.batch_alter_table("cashier_scope", schema=None) as batch_op:
        batch_op.drop_index("ix_cashier_scope_user")
        batch_op.drop_constraint("uq_cashier_scope", type_="unique")
        batch_op.create_unique_constraint(
            "uq_cashier_scope", ["tenant_id", "user_id", "book_set_id", "role"]
        )
