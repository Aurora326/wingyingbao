"""agent token

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent", schema=None) as batch_op:
        batch_op.add_column(sa.Column("token_hash", sa.String(length=64), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("token_enc", sa.String(length=256), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("agent", schema=None) as batch_op:
        batch_op.drop_column("token_enc")
        batch_op.drop_column("token_hash")
