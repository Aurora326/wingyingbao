"""app setting table

Revision ID: c3d4e5f6a7b8
Revises: b7e2c9a14f30
Create Date: 2026-11-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b7e2c9a14f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_app_setting_tenant_key"),
    )
    with op.batch_alter_table("app_setting", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_app_setting_tenant_id"), ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_table("app_setting")
