"""audit_log 变更留痕索引与 payment_doc_change 回填

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-20
"""
from __future__ import annotations

import json
from collections import OrderedDict

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_log_target",
        "audit_log",
        ["tenant_id", "target_type", "target_id", "id"],
    )
    op.create_index(
        "ix_audit_log_action",
        "audit_log",
        ["tenant_id", "action", "id"],
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            select c.doc_id, c.field, c.old_value, c.new_value, c.reason,
                   c.operator, c.created_at, d.tenant_id
            from payment_doc_change c
            left join payment_doc d on d.id = c.doc_id
            order by c.id
            """
        )
    ).mappings().all()

    grouped: "OrderedDict[tuple, dict]" = OrderedDict()
    for row in rows:
        created_at = row["created_at"]
        if created_at is not None:
            created_at = created_at.replace(microsecond=0)
        key = (row["doc_id"], row["operator"], row["reason"], created_at)
        entry = grouped.setdefault(
            key,
            {"changes": {}, "tenant_id": row["tenant_id"], "created_at": created_at},
        )
        entry["changes"][row["field"]] = {
            "from": row["old_value"] or "",
            "to": row["new_value"] or "",
        }

    insert_sql = sa.text(
        """
        insert into audit_log
            (actor, action, target_type, target_id, ip, detail, created_at, tenant_id)
        values
            (:actor, 'payment_change', 'payment_doc', :target_id, '',
             cast(:detail as json), :created_at, :tenant_id)
        """
    )
    for (doc_id, operator, reason, created_at), entry in grouped.items():
        if not entry["changes"]:
            continue
        detail = {
            "changes": entry["changes"],
            "reason": reason or "",
            "source": "legacy",
        }
        bind.execute(
            insert_sql,
            {
                "actor": operator or "",
                "target_id": str(doc_id),
                "detail": json.dumps(detail, ensure_ascii=False),
                "created_at": entry["created_at"],
                "tenant_id": entry["tenant_id"],
            },
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "delete from audit_log "
            "where action = 'payment_change' and detail->>'source' = 'legacy'"
        )
    )
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_target", table_name="audit_log")
