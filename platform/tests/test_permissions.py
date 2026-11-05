from __future__ import annotations

from app.services.permissions import can_access_path, can_collect_accounts, can_edit_accounts, can_menu, role_label


def test_menu_visibility_by_role():
    assert can_menu("admin", "settings")
    assert can_menu("manager", "payments") and can_menu("manager", "recon")
    assert can_menu("cashier", "payments") and not can_menu("cashier", "recon")
    assert can_menu("accountant", "recon") and not can_menu("accountant", "payments")
    assert can_menu("user", "payments") and can_menu("user", "recon")
    assert not can_menu("cashier", "devices")
    assert not can_menu("manager", "payment_settings")


def test_account_edit_roles():
    assert can_edit_accounts("admin") and can_edit_accounts("manager")
    assert not can_edit_accounts("cashier") and not can_edit_accounts("accountant")


def test_account_collect_roles():
    assert can_collect_accounts("admin") and can_collect_accounts("manager")
    assert can_collect_accounts("cashier")
    assert not can_collect_accounts("accountant")


def test_path_permissions():
    assert can_access_path("admin", "GET", "/api/tasks/1/log")
    assert not can_access_path("manager", "GET", "/settings")
    assert not can_access_path("manager", "GET", "/api/devices")
    assert can_access_path("cashier", "GET", "/payments")
    assert not can_access_path("accountant", "GET", "/payments")
    assert can_access_path("accountant", "GET", "/recon")
    assert not can_access_path("cashier", "GET", "/recon")
    assert can_access_path("cashier", "POST", "/api/accounts/1/collect")
    assert not can_access_path("accountant", "POST", "/api/accounts/1/collect")
    assert not can_access_path("cashier", "POST", "/api/accounts/1/toggle")
    assert can_access_path("manager", "POST", "/api/accounts/1/toggle")


def test_role_labels():
    assert role_label("admin") == "管理员"
    assert role_label("cashier") == "出纳"
    assert role_label("unknown") == "unknown"
