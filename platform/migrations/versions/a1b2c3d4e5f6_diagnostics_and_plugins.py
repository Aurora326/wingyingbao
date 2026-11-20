"""add agent channel, task_diagnostic, plugin_release

Revision ID: a1b2c3d4e5f6
Revises: f9a0b1c2d3e4
Create Date: 2026-11-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent",
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="prod"),
    )

    op.create_table(
        "task_diagnostic",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("task_kind", sa.String(length=16), nullable=False, server_default="collection"),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="screenshot"),
        sa.Column("error_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("stage", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("original_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_task_diagnostic_tenant_id", "task_diagnostic", ["tenant_id"])
    op.create_index("ix_task_diag_target", "task_diagnostic", ["task_kind", "task_id"])
    op.create_index("ix_task_diag_created", "task_diagnostic", ["created_at"])

    op.create_table(
        "plugin_release",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("plugin_id", sa.String(length=64), nullable=False),
        sa.Column("bank_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("changelog", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("active_test", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active_prod", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False, server_default=""),
        sa.UniqueConstraint("plugin_id", "version", name="uq_plugin_release_id_ver"),
    )
    op.create_index("ix_plugin_release_bank", "plugin_release", ["bank_code"])


def downgrade() -> None:
    op.drop_index("ix_plugin_release_bank", table_name="plugin_release")
    op.drop_table("plugin_release")
    op.drop_index("ix_task_diag_created", table_name="task_diagnostic")
    op.drop_index("ix_task_diag_target", table_name="task_diagnostic")
    op.drop_index("ix_task_diagnostic_tenant_id", table_name="task_diagnostic")
    op.drop_table("task_diagnostic")
    op.drop_column("agent", "channel")
