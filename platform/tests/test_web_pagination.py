from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import (
    Agent,
    AppUser,
    AuditLog,
    BankAccount,
    BookSet,
    CashierScope,
    CollectionTask,
    Enterprise,
    PayeeAccount,
    PaymentCollectTask,
    PaymentDoc,
    PaymentDraftBatch,
    PaymentDraftTask,
    Receipt,
    ReconBatch,
    ReconItem,
    Statement,
    SubAccount,
    UkeyDevice,
)
from app.services import security


def _hx_headers(target: str) -> dict[str, str]:
    return {"HX-Request": "true", "HX-Target": target}


def _assert_unified_pager(html: str, *, target: str, size_name: str = "size", page_name: str = "page"):
    assert 'class="pagination"' in html
    assert 'class="page-info"' in html
    assert f'name="{size_name}"' in html
    assert f'hx-target="#{target}"' in html
    assert 'hx-push-url="true"' in html
    assert "page-jump" not in html
    assert "data-page-size" not in html
    assert f"{page_name}=" in html or "第 1 / 1 页" in html


@pytest.fixture()
def admin_client(db):
    user = db.execute(select(AppUser).where(AppUser.username == "pager_admin")).scalars().first()
    if user is None:
        user = AppUser(
            username="pager_admin",
            password_hash=security.hash_password("Secret123!"),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
    client = TestClient(app)
    client.cookies.set("session", security.create_session(user.username))
    return client


def test_pagination_unit_helpers():
    from app.web.pagination import normalize_size, page_url, paginate_items

    assert normalize_size(20) == 20
    assert normalize_size(99, default=50) == 50

    res = paginate_items(list(range(25)), page=99, size=10)
    assert res["page"] == 3
    assert res["pages"] == 3
    assert res["total"] == 25
    assert res["items"] == [20, 21, 22, 23, 24]

    assert page_url("/tasks", {"status": "", "size": 20}, page=2) == "/tasks?size=20&page=2"


def test_accounts_devices_sub_server_pagination_and_htmx(db, admin_client):
    ent = Enterprise(name="分页测试企业")
    db.add(ent)
    db.flush()
    book = BookSet(enterprise_id=ent.id, name="分页测试账套", code="PAGER-BOOK")
    db.add(book)

    agent = Agent(code="pager-agent-1", name="分页测试执行机", status="online")
    db.add(agent)

    accts = []
    for i in range(15):
        acct = BankAccount(
            enterprise_id=ent.id,
            bank_code="icbc",
            account_no=f"62220000100{i:02d}",
            account_name=f"分页测试户-{i:02d}",
            account_type="基本户",
            payment_enabled=True,
        )
        accts.append(acct)
    db.add_all(accts)
    db.flush()

    for i in range(14):
        db.add(
            UkeyDevice(
                name=f"分页测试盾-{i:02d}",
                bank_code="icbc",
                backend="gf3",
                agent_code="pager-agent-1",
                gf3_cabinet=1,
                gf3_port=i + 1,
                status="online",
            )
        )
        db.add(
            SubAccount(
                parent_account_id=accts[0].id,
                sub_no=f"0000010{i:02d}",
                ledger_name=f"分页子账簿-{i:02d}",
                project_name="测试项目",
                status="正常",
            )
        )
    db.commit()

    last_acct_page = (db.query(BankAccount).count() + 9) // 10
    last_dev_page = (db.query(UkeyDevice).count() + 9) // 10
    last_sub_page = (db.query(SubAccount).count() + 9) // 10

    # 1. /accounts 整页包含三个独立表格的分页栏且无旧前端分页
    full = admin_client.get("/accounts")
    assert full.status_code == 200
    assert "data-page-size" not in full.text
    assert "page-jump" not in full.text
    assert "分页测试户-14" not in full.text

    # 2. /accounts 银行账户 HTMX 局部翻页
    hx_acct = admin_client.get(
        f"/accounts?tab=accounts&page={last_acct_page}&size=10",
        headers=_hx_headers("accounts-table-region"),
    )
    assert hx_acct.status_code == 200
    assert "<!doctype html>" not in hx_acct.text.lower()
    assert 'id="accounts-table-region"' in hx_acct.text
    assert "分页测试户-14" in hx_acct.text
    assert "分页测试户-00" not in hx_acct.text
    _assert_unified_pager(hx_acct.text, target="accounts-table-region")

    # 3. /accounts UKey 设备表 HTMX 局部翻页 + 服务端搜索
    hx_dev = admin_client.get(
        f"/accounts?tab=devices&dpage={last_dev_page}&dsize=10",
        headers=_hx_headers("accounts-devices-region"),
    )
    assert hx_dev.status_code == 200
    assert 'id="accounts-devices-region"' in hx_dev.text
    assert "分页测试盾-13" in hx_dev.text
    assert "分页测试盾-00" not in hx_dev.text
    _assert_unified_pager(hx_dev.text, target="accounts-devices-region", size_name="dsize", page_name="dpage")

    hx_dev_search = admin_client.get(
        "/accounts?tab=devices&dq=分页测试盾-13", headers=_hx_headers("accounts-devices-region")
    )
    assert hx_dev_search.status_code == 200
    assert "分页测试盾-13" in hx_dev_search.text
    assert "分页测试盾-00" not in hx_dev_search.text

    # 4. /accounts 子账簿表 HTMX 局部翻页
    hx_sub = admin_client.get(
        f"/accounts?tab=sub&spage={last_sub_page}&ssize=10",
        headers=_hx_headers("sub-region"),
    )
    assert hx_sub.status_code == 200
    assert 'id="sub-region"' in hx_sub.text
    assert "分页子账簿-13" in hx_sub.text
    assert "分页子账簿-00" not in hx_sub.text
    _assert_unified_pager(hx_sub.text, target="sub-region", size_name="ssize", page_name="spage")

    # 5. /devices 设备明细页分页 & 搜索
    dev_full = admin_client.get("/devices")
    assert dev_full.status_code == 200
    assert "data-page-size" not in dev_full.text


def test_business_lists_pagination_and_htmx(db, admin_client):
    ent = Enterprise(name="业务分页企业")
    db.add(ent)
    db.flush()
    acct = BankAccount(
        enterprise_id=ent.id,
        bank_code="ccb",
        account_no="3105016100007777",
        account_name="业务分页户",
    )
    db.add(acct)
    db.flush()

    today = dt.date(2026, 11, 1)
    for i in range(15):
        db.add(
            PaymentDoc(
                apply_no=f"PAY-PG-{i:03d}",
                subject=f"付款主题-{i:03d}",
                payee_name=f"收款单位-{i:03d}",
                payer_name="业务分页户",
                amount=Decimal("100.00"),
                status="pending_confirm",
                apply_date=today,
            )
        )
        db.add(
            PayeeAccount(
                payee_name=f"基础收款方-{i:03d}",
                account_no=f"6222888800{i:02d}",
                bank_name="招商银行",
                status="active",
            )
        )
        db.add(
            Statement(
                account_id=acct.id,
                txn_date=today,
                txn_time="10:00:00",
                direction="debit",
                amount=Decimal("50.00"),
                counterparty_name=f"流水对手-{i:03d}",
                serial_no=f"SER-PG-{i:03d}",
                dedupe_hash=f"pg-dedupe-{i:03d}",
            )
        )
        db.add(
            Receipt(
                account_id=acct.id,
                txn_date=today,
                amount=Decimal("50.00"),
                counterparty_name=f"回单对手-{i:03d}",
                receipt_no=f"RCP-PG-{i:03d}",
            )
        )
        db.add(
            CollectionTask(
                account_id=acct.id,
                type="both",
                start_date=today,
                end_date=today,
                status="succeeded",
                idempotency_key=f"pg-task-{i}",
            )
        )
        db.add(
            PaymentCollectTask(
                source="mingyuan",
                status="succeeded",
                limit_count=10,
            )
        )
        db.add(
            AuditLog(
                actor="pager_admin",
                action="account_update",
                target_type="bank_account",
                target_id=str(i),
            )
        )

    batch = ReconBatch(name="对账分页测试", period_start=today, period_end=today, status="open")
    db.add(batch)
    db.flush()
    for i in range(12):
        db.add(ReconBatch(name=f"对账批次-{i:02d}", period_start=today, period_end=today, status="open"))
        db.add(ReconItem(batch_id=batch.id, status="unmatched", diff_amount=Decimal("10.00")))

    draft_batch = PaymentDraftBatch(batch_no="DRAFT-BATCH-PG", created_by="pager_admin", doc_count=12, total_amount=Decimal("120.00"))
    db.add(draft_batch)
    db.flush()
    for i in range(12):
        db.add(
            PaymentDraftTask(
                batch_id=draft_batch.id,
                payer_account_id=acct.id,
                bank_code="ccb",
                payer_name="业务分页户",
                payer_account_no=acct.account_no,
                doc_count=1,
                amount=Decimal("10.00"),
                status="queued",
            )
        )
    db.commit()

    # 1. /payments 局部无刷新分页
    pay_p1 = admin_client.get("/payments?page=1&size=10", headers=_hx_headers("payments-region"))
    pay_p2 = admin_client.get("/payments?page=2&size=10", headers=_hx_headers("payments-region"))
    assert pay_p1.status_code == 200 and pay_p2.status_code == 200
    assert "<!doctype html>" not in pay_p2.text.lower()
    assert 'id="payments-region"' in pay_p2.text
    _assert_unified_pager(pay_p2.text, target="payments-region")
    assert "PAY-PG-014" in pay_p1.text
    assert "PAY-PG-000" in pay_p2.text
    assert "PAY-PG-000" not in pay_p1.text

    # 2. /statements 局部无刷新分页（切换 size=10 验证真实分页）
    st_p2 = admin_client.get("/statements?page=2&size=10", headers=_hx_headers("statements-region"))
    assert st_p2.status_code == 200
    assert 'id="statements-region"' in st_p2.text
    assert "第 2 / 2 页" in st_p2.text
    _assert_unified_pager(st_p2.text, target="statements-region")

    # 3. /receipts 局部无刷新分页
    rc_p2 = admin_client.get("/receipts?page=2&size=10", headers=_hx_headers("receipts-region"))
    assert rc_p2.status_code == 200
    assert 'id="receipts-region"' in rc_p2.text
    assert "第 2 / 2 页" in rc_p2.text
    _assert_unified_pager(rc_p2.text, target="receipts-region")

    # 4. /master-data 局部无刷新分页
    md_p2 = admin_client.get("/master-data?page=2&size=10", headers=_hx_headers("master-region"))
    assert md_p2.status_code == 200
    assert 'id="master-region"' in md_p2.text
    _assert_unified_pager(md_p2.text, target="master-region")

    # 5. /tasks & /tasks/partial（银行采集 + 明源云付款）
    tk_p2 = admin_client.get("/tasks?page=2&size=10", headers=_hx_headers("task-region"))
    assert tk_p2.status_code == 200
    assert 'id="task-region"' in tk_p2.text
    _assert_unified_pager(tk_p2.text, target="task-region")

    tk_pay_p2 = admin_client.get("/tasks?source=payment&page=2&size=10", headers=_hx_headers("task-region"))
    assert tk_pay_p2.status_code == 200
    assert 'id="task-region"' in tk_pay_p2.text
    _assert_unified_pager(tk_pay_p2.text, target="task-region")

    tk_partial = admin_client.get("/tasks/partial?page=2&size=10")
    assert tk_partial.status_code == 200
    _assert_unified_pager(tk_partial.text, target="task-region")

    # 6. /payment-drafts 局部无刷新分页
    df_p2 = admin_client.get("/payment-drafts?page=2&size=10", headers=_hx_headers("payment-drafts-region"))
    assert df_p2.status_code == 200
    assert 'id="payment-drafts-region"' in df_p2.text
    _assert_unified_pager(df_p2.text, target="payment-drafts-region")

    # 7. /recon 批次表与明细表
    rc_batches = admin_client.get("/recon?page=2&size=10", headers=_hx_headers("recon-batches-region"))
    assert rc_batches.status_code == 200
    assert 'id="recon-batches-region"' in rc_batches.text
    _assert_unified_pager(rc_batches.text, target="recon-batches-region")

    rc_items = admin_client.get(
        f"/recon?batch_id={batch.id}&item_page=2&item_size=10",
        headers=_hx_headers("recon-items-region"),
    )
    assert rc_items.status_code == 200
    assert 'id="recon-items-region"' in rc_items.text
    _assert_unified_pager(rc_items.text, target="recon-items-region", size_name="item_size", page_name="item_page")

    # 8. /settings/users 与 /settings/system
    st_users = admin_client.get("/settings/users?page=2&size=10", headers=_hx_headers("settings-users-region"))
    assert st_users.status_code == 200
    assert 'id="settings-users-region"' in st_users.text
    _assert_unified_pager(st_users.text, target="settings-users-region")

    st_logs = admin_client.get("/settings/system?page=2&size=10", headers=_hx_headers("settings-logs-region"))
    assert st_logs.status_code == 200
    assert 'id="settings-logs-region"' in st_logs.text
    _assert_unified_pager(st_logs.text, target="settings-logs-region")
