"""Sprint 2 验收测试：Ed25519 离线签名、打包工具、双通道发布/回退与客户机静默热更新。"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import create_app
from app.models import Agent, AppUser
from app.services import security
from app.services.plugin_signing import generate_keypair, sha256_bytes, sign_bytes, verify_bytes
from app.services.plugins import (
    PluginError,
    activate_release,
    desired_plugins_for_agent,
    upload_plugin_release,
)

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLATFORM_ROOT.parent
AGENT_DIR = PLATFORM_ROOT / "agent"
SCRIPTS_DIR = PLATFORM_ROOT / "scripts"
for p in (AGENT_DIR, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pack_plugin  # noqa: E402
import plugin_manager  # noqa: E402


def _make_plugin_zip(
    plugin_id: str,
    bank_code: str,
    version: str,
    script_body: str = "print('ok')\n",
) -> bytes:
    buf = io.BytesIO()
    manifest = {
        "plugin_id": plugin_id,
        "bank_code": bank_code,
        "version": version,
        "capabilities": ["collect", "draft"],
        "entry": {"collect": ["run_task.py"], "draft": ["run_draft.py"]},
        "changelog": f"release {version}",
    }
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("run_task.py", script_body)
        zf.writestr("run_draft.py", script_body)
    return buf.getvalue()


def test_ed25519_signing_and_pack_plugin_cli(tmp_path):
    priv, pub = generate_keypair()
    payload = b"bank-collector-zip-content"
    sig = sign_bytes(payload, priv)
    assert verify_bytes(payload, sig, pub) is True
    assert verify_bytes(payload + b"tampered", sig, pub) is False
    assert verify_bytes(payload, "", pub) is False

    # 使用真实上海银行目录测试一键打包并签名
    bosh_dir = REPO_ROOT / "上海银行" / "bosh_automation"
    if (bosh_dir / "manifest.json").is_file():
        meta = pack_plugin.pack_plugin(bosh_dir, tmp_path / "out", private_key_b64=priv)
        zip_path = Path(meta["zip_path"])
        assert zip_path.is_file()
        assert verify_bytes(zip_path.read_bytes(), meta["signature"], pub) is True
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "run_task.py" in names
            assert all(not n.endswith(".env") and "__pycache__" not in n for n in names)


def test_dual_channel_release_rollback_and_signature_enforcement(db, monkeypatch):
    from dataclasses import replace
    from app.services import plugins as plugins_service

    priv, pub = generate_keypair()
    monkeypatch.setattr(plugins_service, "settings", replace(settings, plugin_public_key=pub))
    stamp = int(dt.datetime.now().timestamp() * 1000) % 100000
    pid = f"bank-bosh-{stamp}"

    v1_bytes = _make_plugin_zip(pid, "bosh", "1.0.0")
    v2_bytes = _make_plugin_zip(pid, "bosh", "1.0.1")

    # 未签名或篡改签名被平台强制拒绝
    with pytest.raises(PluginError):
        upload_plugin_release(db, content=v1_bytes, signature="bad-sig", operator="admin")

    r1 = upload_plugin_release(db, content=v1_bytes, signature=sign_bytes(v1_bytes, priv), operator="admin")
    r2 = upload_plugin_release(db, content=v2_bytes, signature=sign_bytes(v2_bytes, priv), operator="admin")

    test_agent = Agent(code=f"ag-test-{stamp}", name="内部测试机", channel="test", status="online")
    prod_agent = Agent(code=f"ag-prod-{stamp}", name="正式客户机", channel="prod", status="online")
    db.add_all([test_agent, prod_agent])
    db.commit()

    # v1 推正式机：测试机和正式机都应继承 v1
    activate_release(db, r1.id, "prod", operator="admin")
    assert desired_plugins_for_agent(db, prod_agent.code)[pid]["version"] == "1.0.0"
    assert desired_plugins_for_agent(db, test_agent.code)[pid]["version"] == "1.0.0"

    # v2 仅推测试机：测试机拿到 1.0.1，正式机保持 1.0.0 零影响
    activate_release(db, r2.id, "test", operator="admin")
    assert desired_plugins_for_agent(db, test_agent.code)[pid]["version"] == "1.0.1"
    assert desired_plugins_for_agent(db, prod_agent.code)[pid]["version"] == "1.0.0"

    # 验证通过后 v2 推正式机
    activate_release(db, r2.id, "prod", operator="admin")
    assert desired_plugins_for_agent(db, prod_agent.code)[pid]["version"] == "1.0.1"

    # 线上异常一键回退：对 v1 再点一次推正式机
    activate_release(db, r1.id, "prod", operator="admin")
    assert desired_plugins_for_agent(db, prod_agent.code)[pid]["version"] == "1.0.0"


def test_agent_plugin_manager_sync_verify_smoke_and_resolve(tmp_path):
    priv, pub = generate_keypair()
    plugins_dir = tmp_path / "plugins"
    lock_file = tmp_path / "bus.lock"

    v1_bytes = _make_plugin_zip("bank-bosh", "bosh", "1.0.0")
    v2_bad_syntax = _make_plugin_zip("bank-bosh", "bosh", "1.0.1", script_body="def broken(:\n")
    v2_good = _make_plugin_zip("bank-bosh", "bosh", "1.0.2")

    # 1. 正常签名包同步成功
    desired_v1 = {
        "bank-bosh": {
            "plugin_id": "bank-bosh",
            "bank_code": "bosh",
            "version": "1.0.0",
            "sha256": sha256_bytes(v1_bytes),
            "signature": sign_bytes(v1_bytes, priv),
            "download_url": "/api/agent/plugins/1/download",
        }
    }
    res1 = plugin_manager.sync_plugins(
        "http://local",
        "tok",
        "ag-1",
        desired_v1,
        plugins_dir=plugins_dir,
        public_key_b64=pub,
        lock_path=lock_file,
        downloader=lambda _spec: v1_bytes,
    )
    assert res1["updated"] == ["bank-bosh@1.0.0"]
    assert res1["errors"] == {}
    assert plugin_manager.installed_plugins(plugins_dir) == {"bank-bosh": "1.0.0"}

    # 2. 篡改包（验签失败）直接拦截，不影响当前生效的 1.0.0
    tampered = v2_good + b"evil"
    res_bad_sig = plugin_manager.sync_plugins(
        "http://local",
        "tok",
        "ag-1",
        {
            "bank-bosh": {
                "plugin_id": "bank-bosh",
                "bank_code": "bosh",
                "version": "1.0.2",
                "sha256": sha256_bytes(tampered),
                "signature": sign_bytes(v2_good, priv),
                "download_url": "/api/agent/plugins/2/download",
            }
        },
        plugins_dir=plugins_dir,
        public_key_b64=pub,
        lock_path=lock_file,
        downloader=lambda _spec: tampered,
    )
    assert "bank-bosh" in res_bad_sig["errors"]
    assert plugin_manager.installed_plugins(plugins_dir) == {"bank-bosh": "1.0.0"}

    # 3. 含 Python 语法错误的包被冒烟检查拦截，不影响 1.0.0
    res_bad_py = plugin_manager.sync_plugins(
        "http://local",
        "tok",
        "ag-1",
        {
            "bank-bosh": {
                "plugin_id": "bank-bosh",
                "bank_code": "bosh",
                "version": "1.0.1",
                "sha256": sha256_bytes(v2_bad_syntax),
                "signature": sign_bytes(v2_bad_syntax, priv),
                "download_url": "/api/agent/plugins/3/download",
            }
        },
        plugins_dir=plugins_dir,
        public_key_b64=pub,
        lock_path=lock_file,
        downloader=lambda _spec: v2_bad_syntax,
    )
    assert "bank-bosh" in res_bad_py["errors"]
    assert plugin_manager.installed_plugins(plugins_dir) == {"bank-bosh": "1.0.0"}

    # 4. 升级到 1.0.2，校验 current.json 保留 prev=1.0.0，且 resolve_plugin_command 准确命中
    res2 = plugin_manager.sync_plugins(
        "http://local",
        "tok",
        "ag-1",
        {
            "bank-bosh": {
                "plugin_id": "bank-bosh",
                "bank_code": "bosh",
                "version": "1.0.2",
                "sha256": sha256_bytes(v2_good),
                "signature": sign_bytes(v2_good, priv),
                "download_url": "/api/agent/plugins/4/download",
            }
        },
        plugins_dir=plugins_dir,
        public_key_b64=pub,
        lock_path=lock_file,
        downloader=lambda _spec: v2_good,
    )
    assert res2["updated"] == ["bank-bosh@1.0.2"]
    cur = json.loads((plugins_dir / "bank-bosh" / "current.json").read_text(encoding="utf-8"))
    assert cur["version"] == "1.0.2" and cur["prev"] == "1.0.0"

    task_json = tmp_path / "task.json"
    resolved = plugin_manager.resolve_plugin_command(
        "bosh", "draft", task_json, sys.executable, plugins_dir=plugins_dir
    )
    assert resolved is not None
    argv, cwd, _extra = resolved
    assert argv[0] == sys.executable and argv[1] == "run_draft.py"
    assert cwd == plugins_dir / "bank-bosh" / "releases" / "1.0.2"


def test_plugin_web_ui_and_agent_heartbeat_api(db, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.api import agent as agent_api
    from app.services import plugins as plugins_service

    monkeypatch.setattr(plugins_service, "settings", replace(settings, plugin_public_key=""))
    monkeypatch.setattr(
        agent_api,
        "settings",
        SimpleNamespace(agent_tokens={"plugin-agent-tok"}, agent_token_scopes={}, agent_tenants={}),
    )
    admin = db.execute(select(AppUser).where(AppUser.username == "plugin_admin")).scalars().first()
    if admin is None:
        admin = AppUser(
            username="plugin_admin",
            display_name="插件管理员",
            password_hash=security.hash_password("PluginAdmin123!"),
            role="admin",
            is_active=True,
        )
        db.add(admin)
        db.commit()

    client = TestClient(create_app())
    client.cookies.set("session", security.create_session("plugin_admin"))

    # 1. 打开采集插件管理页
    page = client.get("/settings/plugins")
    assert page.status_code == 200
    assert "采集插件热更新" in page.text

    # 2. 通过 Web 表单上传插件 zip
    stamp = int(dt.datetime.now().timestamp() * 1000) % 100000
    pid = f"bank-ccb-{stamp}"
    raw_zip = _make_plugin_zip(pid, "ccb", "2.0.0")
    up = client.post(
        "/settings/plugins/upload",
        data={"changelog": "建行测试版"},
        files={"file": (f"{pid}-2.0.0.zip", io.BytesIO(raw_zip), "application/zip")},
        follow_redirects=False,
    )
    assert up.status_code == 303

    # 3. 激活到 prod 通道并通过 Agent 心跳接口验证上报与下发
    from app.models import PluginRelease

    rel = db.execute(select(PluginRelease).where(PluginRelease.plugin_id == pid)).scalars().first()
    assert rel is not None
    act = client.post(
        f"/settings/plugins/{rel.id}/activate",
        data={"channel": "prod"},
        follow_redirects=False,
    )
    assert act.status_code == 303

    hb = client.post(
        "/api/agent/devices/report",
        headers={"Authorization": "Bearer plugin-agent-tok"},
        json={
            "agent_code": f"hb-ag-{stamp}",
            "ter_id": "068330001599",
            "devices": [],
            "installed_plugins": {pid: "1.9.0"},
        },
    )
    assert hb.status_code == 200
    body = hb.json()
    assert body["desired_plugins"][pid]["version"] == "2.0.0"

    # 4. 下载接口返回完整 zip
    dl = client.get(
        body["desired_plugins"][pid]["download_url"],
        params={"agent_code": f"hb-ag-{stamp}"},
        headers={"Authorization": "Bearer plugin-agent-tok"},
    )
    assert dl.status_code == 200
    assert dl.content == raw_zip

    # 清理本次心跳测试产生的审计记录，避免干扰其他单测查询首条 agent_device_report 日志
    from app.models import AuditLog

    db.query(AuditLog).filter(AuditLog.target_id == f"hb-ag-{stamp}").delete()
    db.commit()
