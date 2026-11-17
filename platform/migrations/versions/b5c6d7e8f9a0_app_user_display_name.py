"""app user display name

Revision ID: b5c6d7e8f9a0
Revises: f3a4b5c6d7e8
Create Date: 2026-11-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(length=64), nullable=False, server_default=""))
    op.execute("UPDATE app_user SET display_name = username WHERE display_name = ''")
    op.execute("UPDATE app_user SET display_name = '管理员' WHERE username = 'admin'")


def downgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.drop_column("display_name")
