"""alert event

Revision ID: e8f9a0b1c2d3
Revises: c6d7e8f9a0b1
Create Date: 2026-11-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_msg", sa.String(length=512), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column("handled_by", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_event_kind_object", "alert_event", ["kind", "object_type", "object_id"])
    op.create_index("ix_alert_event_tenant_id", "alert_event", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_event_tenant_id", table_name="alert_event")
    op.drop_index("ix_alert_event_kind_object", table_name="alert_event")
    op.drop_table("alert_event")
