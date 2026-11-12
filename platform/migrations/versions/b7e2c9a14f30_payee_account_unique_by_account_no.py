"""payee account unique by account_no

Revision ID: b7e2c9a14f30
Revises: df0c895b3784
Create Date: 2026-11-09

收款账户以账号为唯一键：
1. 先合并同账号的多行（保留关联单据最多、字段最全的一行，其余行删除并把单据指过来）；
2. 再建 (tenant_id, account_no) 唯一索引，空账号不参与约束。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b7e2c9a14f30"
down_revision: str | None = "df0c895b3784"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FIELDS = ("bank_name", "branch_name", "cnaps_code")


def _merge_duplicates(bind) -> None:
    rows = bind.execute(
        sa.text(
            "select id, tenant_id, account_no, payee_name, bank_name, branch_name, "
            "cnaps_code, remark from payee_account where account_no <> '' order by id"
        )
    ).mappings().all()
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[(row["tenant_id"], row["account_no"])].append(dict(row))

    for items in groups.values():
        if len(items) < 2:
            continue
        counts = {
            row["id"]: bind.execute(
                sa.text("select count(*) from payment_doc where payee_account_id = :id"),
                {"id": row["id"]},
            ).scalar()
            or 0
            for row in items
        }
        survivor = max(
            items,
            key=lambda row: (
                counts.get(row["id"], 0),
                sum(1 for field in _FIELDS if row[field]),
                -row["id"],
            ),
        )
        for other in items:
            if other["id"] == survivor["id"]:
                continue
            for field in _FIELDS:
                if not survivor[field] and other[field]:
                    bind.execute(
                        sa.text(f"update payee_account set {field} = :value where id = :id"),
                        {"value": other[field], "id": survivor["id"]},
                    )
            alias = f"{other['payee_name']}（{other['bank_name'] or '未知开户行'}）"
            remark = (survivor["remark"] or "").strip()
            merged = f"{remark} 合并：{alias}".strip()
            bind.execute(
                sa.text("update payee_account set remark = :remark where id = :id"),
                {"remark": merged[:256], "id": survivor["id"]},
            )
            survivor["remark"] = merged[:256]
            bind.execute(
                sa.text("update payment_doc set payee_account_id = :keep where payee_account_id = :drop"),
                {"keep": survivor["id"], "drop": other["id"]},
            )
            bind.execute(
                sa.text(
                    "update payee_account_change set payee_account_id = :keep "
                    "where payee_account_id = :drop"
                ),
                {"keep": survivor["id"], "drop": other["id"]},
            )
            bind.execute(sa.text("delete from payee_account where id = :id"), {"id": other["id"]})


def upgrade() -> None:
    _merge_duplicates(op.get_bind())
    op.create_index(
        "uq_payee_account_tenant_account",
        "payee_account",
        ["tenant_id", "account_no"],
        unique=True,
        postgresql_where=sa.text("account_no <> ''"),
        sqlite_where=sa.text("account_no <> ''"),
    )


def downgrade() -> None:
    op.drop_index("uq_payee_account_tenant_account", table_name="payee_account")
