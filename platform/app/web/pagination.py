"""全平台统一的服务端分页与 HTMX 局部请求辅助函数。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar
from urllib.parse import urlencode

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

T = TypeVar("T")

PAGE_SIZES: tuple[int, ...] = (10, 20, 50, 100)


def normalize_size(size: int | None, default: int = 10) -> int:
    if size in PAGE_SIZES:
        return int(size)
    return default if default in PAGE_SIZES else 10


def paginate_items(
    items: Sequence[T],
    page: int = 1,
    size: int | None = None,
    *,
    default_size: int = 10,
) -> dict[str, Any]:
    """对已查询到的列表进行服务端切片分页。"""
    resolved_size = normalize_size(size, default_size)
    total = len(items)
    pages = max(1, (total + resolved_size - 1) // resolved_size)
    page_no = min(max(1, int(page or 1)), pages)
    start = (page_no - 1) * resolved_size
    return {
        "items": list(items[start : start + resolved_size]),
        "total": total,
        "page": page_no,
        "size": resolved_size,
        "pages": pages,
    }


def paginate_scalars(
    db: Session,
    query: Any,
    page: int = 1,
    size: int | None = None,
    *,
    default_size: int = 10,
) -> dict[str, Any]:
    """对 SQLAlchemy Select 语句执行 COUNT + OFFSET/LIMIT 服务端分页。"""
    resolved_size = normalize_size(size, default_size)
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = int(db.execute(count_query).scalar_one() or 0)
    pages = max(1, (total + resolved_size - 1) // resolved_size)
    page_no = min(max(1, int(page or 1)), pages)
    rows = (
        db.execute(query.offset((page_no - 1) * resolved_size).limit(resolved_size))
        .scalars()
        .all()
    )
    return {
        "items": list(rows),
        "total": total,
        "page": page_no,
        "size": resolved_size,
        "pages": pages,
    }


def page_url(
    base: str,
    params: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> str:
    """拼接保留查询条件且过滤空值的分页 URL。"""
    merged: dict[str, Any] = dict(params or {})
    merged.update(overrides)
    pairs = [
        (key, str(value))
        for key, value in merged.items()
        if value is not None and value != ""
    ]
    qs = urlencode(pairs)
    return f"{base}?{qs}" if qs else base


def is_hx(request: Request, target: str | None = None) -> bool:
    """判断是否为 HTMX 局部请求；若指定 target 则同时校验目标区域 ID。"""
    if request.headers.get("HX-Request") != "true":
        return False
    if target is not None:
        return request.headers.get("HX-Target") == target
    return True
