"""Sprint 1 验收测试：制单超时防重、客户机互斥锁、master.key 备份、多租户告警隔离与失败现场诊断。"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import create_app
from app.models import (
    Agent,
    AlertEvent,
    AppUser,
    BankAccount,
    CollectionTask,
    Credential,
    Enterprise,
    PaymentDoc,
    PaymentDraftTask,
    TaskDiagnostic,
    Tenant,
    UkeyDevice,
)
from app.services import alerts, app_settings, backup, security
from app.services.diagnostics import cleanup_expired_diagnostics, list_diagnostics
from app.services.payment_draft import (
    TASK_QUEUED,
    TASK_TIMEOUT_REVIEW,
    claim_draft_task,
    create_batch,
    requeue_task,
)
from app.tenancy import tenant_context
from common.diagnostics import (
    ERROR_PAGE_CHANGED,
    StepTracker,
    capture_diagnostic,
    sanitize_html,
)

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from local_lock import AgentBusLock  # noqa: E402


def _seed_draft_task(db, suffix: str) -> tuple[PaymentDraftTask, PaymentDoc]:
    ent = Enterprise(name=f"防重测试主体-{suffix}")
    db.add(ent)
    db.flush()
    dev = UkeyDevice(
        name=f"建行口-{suffix}",
        bank_code="ccb",
        backend="mb",
        agent_code=f"agent-safe-{suffix}",
        gf3_cabinet=1,
        gf3_port=1,
        status="online",
    )
    db.add(dev)
    db.flush()
    acct = BankAccount(
        enterprise_id=ent.id,
        bank_code="ccb",
        account_no=f"3105019999{suffix}",
        account_name=f"防重账户-{suffix}",
        payment_enabled=True,
        ukey_device_id=dev.id,
    )
    db.add(acct)
    db.flush()
    db.add(Credential(bank_account_id=acct.id, pin_enc=security.encrypt("123456")))
    doc = PaymentDoc(
        enterprise_id=ent.id,
        apply_no=f"SAFE-DOC-{suffix}",
        payer_account_id=acct.id,
        payer_name=acct.account_name,
        payer_account_no=acct.account_no,
        payee_name="收款测试公司",
        payee_account_no="6222000011112222",
        payee_bank="建设银行上海支行",
        amount=Decimal("8800.00"),
        status="ready",
    )
    db.add(doc)
    db.commit()
    batch = create_batch(db, [doc.id], operator="cashier_a")
    task = db.execute(
        select(PaymentDraftTask).where(PaymentDraftTask.batch_id == batch.id)
    ).scalars().first()
    return task, doc


def test_draft_timeout_never_auto_requeues_and_triggers_alert(db):
    task, _doc = _seed_draft_task(db, "01")
    claimed = claim_draft_task(db, task.agent_code)
    assert claimed is not None and claimed["task_id"] == task.id

    # 模拟租约已过期
    row = db.get(PaymentDraftTask, task.id)
    row.lease_until = dt.datetime.now() - dt.timedelta(minutes=5)
    db.commit()

    # 再次领取：严禁自动放回队列被重复领取，应转为 timeout_review
    claimed_again = claim_draft_task(db, task.agent_code)
    assert claimed_again is None
    db.expire_all()
    row = db.get(PaymentDraftTask, task.id)
    assert row.status == TASK_TIMEOUT_REVIEW
    assert row.error_code == "LEASE_EXPIRED"

    # 告警扫描命中 draft_timeout
    alerts.scan(db, dt.datetime.now())
    ev = db.execute(
        select(AlertEvent).where(
            AlertEvent.kind == "draft_timeout",
            AlertEvent.object_id == task.id,
        )
    ).scalars().first()
    assert ev is not None and ev.level == "critical"

    # 只有人工核实后显式调用 requeue_task，才允许重新排队并被领取
    requeue_task(db, task.id, operator="cashier_a")
    db.expire_all()
    assert db.get(PaymentDraftTask, task.id).status == TASK_QUEUED
    claimed_third = claim_draft_task(db, task.agent_code)
    assert claimed_third is not None and claimed_third["task_id"] == task.id


def test_agent_bus_lock_mutual_exclusion(tmp_path):
    lock_file = tmp_path / "ukey_bus.lock"
    lock_a = AgentBusLock(lock_file)
    lock_b = AgentBusLock(lock_file)
    assert lock_a.acquire(timeout=1.0) is True
    try:
        # 锁已被 lock_a 占用，lock_b 在短超时内无法获取
        assert lock_b.acquire(timeout=0.2, interval=0.05) is False
    finally:
        lock_a.release()
    # 释放后 lock_b 可立即获取
    assert lock_b.acquire(timeout=1.0) is True
    lock_b.release()


def test_backup_includes_master_key(tmp_path, monkeypatch):
    fake_artifacts = tmp_path / "artifacts"
    fake_artifacts.mkdir()
    (fake_artifacts / "a.txt").write_text("ok", encoding="utf-8")
    fake_key = tmp_path / "master.key"
    fake_key.write_text("FAKE-FERNET-KEY", encoding="utf-8")
    backup_root = tmp_path / "backups"

    def fake_pg(target: Path, db_url=None):
        dump = target / "app_test.dump"
        dump.write_bytes(b"PGDMP")
        return dump

    monkeypatch.setattr(backup, "backup_postgres", fake_pg)
    out = backup.create_backup(
        artifacts_dir=fake_artifacts,
        target_root=backup_root,
        master_key_path=fake_key,
    )
    assert (out / "master.key").read_text(encoding="utf-8") == "FAKE-FERNET-KEY"
    assert (out / "artifacts" / "a.txt").exists()


def test_multi_tenant_alert_deliver_isolated(db, monkeypatch):
    stamp = int(dt.datetime.now().timestamp() * 1000)
    t1 = Tenant(name=f"租户一-{stamp}", code=f"t1_{stamp}", status="active")
    t2 = Tenant(name=f"租户二-{stamp}", code=f"t2_{stamp}", status="active")
    db.add_all([t1, t2])
    db.commit()

    with tenant_context(t1.id):
        app_settings.set_value(db, "alert_enabled", "1")
        app_settings.set_value(db, "alert_webhook", "https://hook.t1.example")
        db.add(AlertEvent(kind="task_failed", level="critical", title="T1告警", content="内容1", status="pending"))
        db.commit()

    with tenant_context(t2.id):
        app_settings.set_value(db, "alert_enabled", "1")
        app_settings.set_value(db, "alert_webhook", "https://hook.t2.example")
        db.add(AlertEvent(kind="task_failed", level="critical", title="T2告警", content="内容2", status="pending"))
        db.commit()

    sent_calls: list[tuple[str, str]] = []

    def fake_send(url: str, content: str, timeout: float = 8.0):
        sent_calls.append((url, content))
        return True, ""

    monkeypatch.setattr(alerts, "send_webhook", fake_send)
    alerts.deliver_all_tenants(db, dt.datetime.now())

    t1_msgs = [c for u, c in sent_calls if u == "https://hook.t1.example"]
    t2_msgs = [c for u, c in sent_calls if u == "https://hook.t2.example"]
    assert any("T1告警" in m for m in t1_msgs)
    assert all("T2告警" not in m for m in t1_msgs)
    assert any("T2告警" in m for m in t2_msgs)
    assert all("T1告警" not in m for m in t2_msgs)


def test_diagnostic_sanitize_and_capture(tmp_path):
    raw_html = """
    <html>
      <head><style>.a{color:red}</style><script>alert(1)</script></head>
      <body>
        <button id="btn-batch-pay" class="primary-menu">批量转账制单</button>
        <input type="password" name="pwd" value="SuperSecret99" />
        <div class="acct-info">付款账号 31050168360000012345 余额 ￥1,234,567.89 金额 88.50</div>
      </body>
    </html>
    """
    clean = sanitize_html(raw_html)
    assert "<script>" not in clean and "<style>" not in clean
    assert "SuperSecret99" not in clean
    assert "31050168360000012345" not in clean
    assert "1,234,567.89" not in clean
    assert "88.50" not in clean
    # 关键定位属性与菜单文字完整保留
    assert 'id="btn-batch-pay"' in clean
    assert 'class="primary-menu"' in clean
    assert "批量转账制单" in clean

    class FakeFrame:
        url = "https://bank.example/subframe"

        def content(self):
            return '<div id="iframe-menu">电子回单查询 6222021001123456789</div>'

    class FakePage:
        url = "https://bank.example/main"
        frames = [None, FakeFrame()]

        def title(self):
            return "企业网银"

        def screenshot(self, path, **kwargs):
            Path(path).write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")

        def content(self):
            return raw_html

    tracker = StepTracker()
    tracker.start("打开批量转账菜单")
    tracker.fail(error="元素 #btn-batch-pay 超时", error_code=ERROR_PAGE_CHANGED)

    files = capture_diagnostic(
        FakePage(),
        tmp_path / "diag",
        stage="batch_transfer",
        error_code=ERROR_PAGE_CHANGED,
        message="未找到批量转账按钮",
        steps=tracker.steps,
    )
    names = [Path(f).name for f in files]
    assert any(n.endswith(".jpg") for n in names)
    assert any(n.endswith(".html") for n in names)
    assert any(n.endswith(".json") for n in names)
    dom_file = next(Path(f) for f in files if f.endswith(".html"))
    dom_text = dom_file.read_text(encoding="utf-8")
    assert "IFRAME #1" in dom_text and "6222021001123456789" not in dom_text
    meta_file = next(Path(f) for f in files if f.endswith(".json"))
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["error_code"] == ERROR_PAGE_CHANGED
    assert len(meta["steps"]) == 1


def test_agent_diagnostic_upload_view_and_cleanup(db, monkeypatch):
    from types import SimpleNamespace
    from app.api import agent as agent_api

    monkeypatch.setattr(
        agent_api,
        "settings",
        SimpleNamespace(agent_tokens={"diag-token-01"}, agent_token_scopes={}, agent_tenants={}),
    )
    ent = Enterprise(name="诊断测试公司")
    db.add(ent)
    db.flush()
    acct = BankAccount(enterprise_id=ent.id, bank_code="bosh", account_no="316000019999", account_name="上海户")
    db.add(acct)
    db.flush()
    task = CollectionTask(
        account_id=acct.id,
        type="statements",
        start_date=dt.date.today(),
        end_date=dt.date.today(),
        status="failed",
        agent_code="diag-agent-1",
        error_code="PAGE_CHANGED",
        error_msg="菜单按钮找不到",
        idempotency_key=f"diag-task-{int(dt.datetime.now().timestamp() * 1000)}",
    )
    db.add(task)
    admin = db.execute(select(AppUser).where(AppUser.username == "diag_admin")).scalars().first()
    if admin is None:
        admin = AppUser(
            username="diag_admin",
            display_name="诊断管理员",
            password_hash=security.hash_password("DiagAdmin123!"),
            role="admin",
            is_active=True,
        )
        db.add(admin)
    db.commit()

    client = TestClient(create_app())
    resp_shot = client.post(
        f"/api/agent/tasks/{task.id}/diagnostics",
        headers={"Authorization": "Bearer diag-token-01"},
        data={
            "agent_code": "diag-agent-1",
            "attempt": "1",
            "error_code": "PAGE_CHANGED",
            "stage": "menu_click",
        },
        files={"file": ("failure_01.jpg", io.BytesIO(b"\xff\xd8\xff\xe0IMG"), "image/jpeg")},
    )
    assert resp_shot.status_code == 200 and resp_shot.json()["kind"] == "screenshot"

    resp_dom = client.post(
        f"/api/agent/tasks/{task.id}/diagnostics",
        headers={"Authorization": "Bearer diag-token-01"},
        data={
            "agent_code": "diag-agent-1",
            "attempt": "1",
            "error_code": "PAGE_CHANGED",
            "stage": "menu_click",
        },
        files={"file": ("dom_01.html", io.BytesIO(b"<div id='x'>menu</div>"), "text/html")},
    )
    assert resp_dom.status_code == 200 and resp_dom.json()["kind"] == "dom"
    dom_id = resp_dom.json()["id"]

    # 登录管理员查看诊断页面，且 DOM 原文件应以 text/plain 返回防 XSS
    client.cookies.set("session", security.create_session("diag_admin"))
    page_resp = client.get(f"/tasks/{task.id}/diagnostics")
    assert page_resp.status_code == 200
    assert "failure_01.jpg" in page_resp.text and "dom_01.html" in page_resp.text

    raw_dom = client.get(f"/diagnostics/{dom_id}/raw")
    assert raw_dom.status_code == 200
    assert raw_dom.headers["content-type"].startswith("text/plain")

    # 模拟 20 天前的数据被过期自动清理
    rows = list_diagnostics(db, "collection", task.id)
    assert len(rows) == 2
    for r in rows:
        r.created_at = dt.datetime.now() - dt.timedelta(days=20)
    db.commit()
    removed = cleanup_expired_diagnostics(db, keep_days=14)
    assert removed >= 2
    assert list_diagnostics(db, "collection", task.id) == []
