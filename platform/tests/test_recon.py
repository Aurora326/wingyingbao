from __future__ import annotations

import datetime as dt

from app.models import BankAccount, Enterprise, InternalDoc, Statement
from app.services import recon


def _account(db) -> BankAccount:
    enterprise = Enterprise(name="测试企业")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="mock",
        account_no="MOCK0002",
        account_name="测试账户",
    )
    db.add(account)
    db.commit()
    return account


def _statement(db, account, day, amount, counterparty, direction="credit"):
    row = Statement(
        account_id=account.id,
        txn_date=day,
        txn_time="10:00:00",
        direction=direction,
        amount=amount,
        balance=amount,
        counterparty_name=counterparty,
        dedupe_hash=f"{account.id}-{day}-{amount}-{counterparty}",
    )
    db.add(row)
    return row


def test_recon_exact_and_fuzzy(db):
    account = _account(db)
    base = dt.date(2030, 3, 1)
    _statement(db, account, base, 1000, "上海隐石信息科技发展有限公司")
    _statement(db, account, base + dt.timedelta(days=2), 2000, "杭州云启网络科技有限公司")
    _statement(db, account, base + dt.timedelta(days=20), 500, "无人匹配的对手方")
    db.add_all(
        [
            InternalDoc(enterprise_id=1, amount=1000, direction="credit", counterparty="上海隐石信息科技", doc_date=base),
            InternalDoc(
                enterprise_id=1,
                amount=2000,
                direction="credit",
                counterparty="杭州云启网络科技",
                doc_date=base + dt.timedelta(days=1),
            ),
        ]
    )
    db.commit()

    batch = recon.create_batch(db, base, base + dt.timedelta(days=25), "test")
    recon.run_batch(db, batch)

    from sqlalchemy import select

    from app.models import ReconItem

    items = db.execute(select(ReconItem).where(ReconItem.batch_id == batch.id)).scalars().all()
    matched = [item for item in items if item.status == "matched"]
    unmatched = [item for item in items if item.status == "unmatched"]
    assert len(matched) == 2
    assert len(unmatched) == 1
    assert any(item.match_type == "fuzzy" for item in matched)
