from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select
from starlette.requests import Request

from app.models import BankAccount, Enterprise, PayeeAccount, PaymentDoc
from app.web.payments import payments_confirm_quick


def _request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/payments/1/confirm-quick",
        "headers": [(b"hx-request", b"true")],
        "client": ("127.0.0.1", 12345),
        "state": {},
    }
    request = Request(scope)
    request.state.user = SimpleNamespace(role="admin", username="tester")
    return request


def test_confirm_quick(db):
    enterprise = Enterprise(name="确认接口测试")
    db.add(enterprise)
    db.flush()
    payer = BankAccount(
        enterprise_id=enterprise.id, bank_code="ccb", account_no="CONFIRM-1", account_name="付款户"
    )
    payee = PayeeAccount(payee_name="收款公司", account_no="CONFIRM-2", bank_name="测试银行", status="active")
    db.add_all([payer, payee])
    db.flush()
    doc = PaymentDoc(
        apply_no="CONFIRM-DOC-1",
        payer_account_id=payer.id,
        payee_account_id=payee.id,
        payer_account_no=payer.account_no,
        payee_account_no=payee.account_no,
        payee_name=payee.payee_name,
        amount=Decimal("100.00"),
        apply_date=dt.date(2026, 11, 20),
        status="pending_confirm",
    )
    db.add(doc)
    db.commit()

    response = payments_confirm_quick(_request(), doc.id)

    db.expire_all()
    refreshed = db.get(PaymentDoc, doc.id)
    assert response.status_code == 204
    assert refreshed.status == "ready"
