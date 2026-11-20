"""statement 增加付款单据匹配字段

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-11-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("statement", sa.Column("matched_payment_doc_id", sa.Integer(), nullable=True))
    op.add_column(
        "statement",
        sa.Column("payment_match_type", sa.String(length=8), nullable=False, server_default=""),
    )
    op.add_column("statement", sa.Column("payment_matched_at", sa.DateTime(), nullable=True))
    op.create_foreign_key(
        "fk_statement_matched_payment_doc",
        "statement",
        "payment_doc",
        ["matched_payment_doc_id"],
        ["id"],
    )
    op.create_index(
        "ix_statement_matched_payment_doc_id",
        "statement",
        ["matched_payment_doc_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_statement_matched_payment_doc_id", table_name="statement")
    op.drop_constraint("fk_statement_matched_payment_doc", "statement", type_="foreignkey")
    op.drop_column("statement", "payment_matched_at")
    op.drop_column("statement", "payment_match_type")
    op.drop_column("statement", "matched_payment_doc_id")
