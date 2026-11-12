from __future__ import annotations

ROLE_LABELS = {
    "admin": "管理员",
    "manager": "资金经理",
    "cashier": "出纳",
    "accountant": "会计",
    "user": "普通用户",
}

# 普通用户是旧角色，按资金经理的业务范围兼容。
_ROLE_ALIASES = {"user": "manager"}

MENU_ACCESS = {
    "admin": {"dashboard", "accounts", "statements", "receipts", "payments", "recon", "devices", "payment_settings", "tasks", "settings", "master_data"},
    "manager": {"dashboard", "accounts", "statements", "receipts", "payments", "recon"},
    "cashier": {"dashboard", "accounts", "statements", "receipts", "payments"},
    "accountant": {"dashboard", "accounts", "statements", "receipts", "recon"},
}

_EDIT_ACCOUNT_ROLES = {"admin", "manager"}
_COLLECT_ACCOUNT_ROLES = {"admin", "manager", "cashier"}
_PAYMENT_ROLES = {"manager", "cashier"}
_RECON_ROLES = {"manager", "accountant"}
_ADMIN_PREFIXES = ("/settings", "/payment-settings", "/master-data", "/devices", "/tasks", "/tenant/switch", "/api/settings", "/api/payment-settings", "/api/devices", "/api/tasks")

_PAGE_MENUS = {
    "/": "dashboard",
    "/accounts": "accounts",
    "/statements": "statements",
    "/receipts": "receipts",
    "/payments": "payments",
    "/payment-drafts": "payments",
    "/recon": "recon",
    "/devices": "devices",
    "/payment-settings": "payment_settings",
    "/master-data": "master_data",
    "/tasks": "tasks",
    "/settings": "settings",
}


def effective_role(role: str | None) -> str:
    value = role or "user"
    return _ROLE_ALIASES.get(value, value)


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", role or "普通用户")


def can_menu(role: str | None, key: str) -> bool:
    return key in MENU_ACCESS.get(effective_role(role), set())


def can_edit_accounts(role: str | None) -> bool:
    return effective_role(role) in _EDIT_ACCOUNT_ROLES


def can_collect_accounts(role: str | None) -> bool:
    return effective_role(role) in _COLLECT_ACCOUNT_ROLES


def can_access_path(role: str | None, method: str, path: str) -> bool:
    current = effective_role(role)
    if current not in MENU_ACCESS:
        return False
    if current == "admin":
        return True
    path = path.rstrip("/") or "/"

    if path.startswith(_ADMIN_PREFIXES):
        return False
    if path.startswith(("/api/payment",)):
        return current in _PAYMENT_ROLES
    if path.startswith(("/api/recon",)):
        return current in _RECON_ROLES
    if path.startswith("/api/accounts"):
        if method in {"GET", "HEAD", "OPTIONS"}:
            return True
        if "/collect" in path:
            return current in {"manager", "cashier"}
        return current in _EDIT_ACCOUNT_ROLES

    for prefix, menu_key in _PAGE_MENUS.items():
        if path == prefix or (prefix != "/" and path.startswith(prefix + "/")):
            return can_menu(role, menu_key)
    return True
