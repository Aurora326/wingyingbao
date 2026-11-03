from __future__ import annotations

from fastapi import APIRouter, Request

from app.web.routes import templates

router = APIRouter()

DATA_CUTOFF = "2026-11-03 08:00"

SPECIAL_ACCOUNTS = [
    {
        "id": 1,
        "company": "上海临港置业有限公司",
        "project": "临港项目",
        "type": "预售资金监管户",
        "type_key": "presale",
        "bank": "建设银行",
        "branch": "临港支行",
        "account_no": "**** 4821",
        "regulator": "临港新片区住建",
        "book": 12_860_000,
        "restricted": 9_200_000,
        "available": 3_660_000,
        "min_balance": 5_000_000,
        "release": "工程节点 / 监管额度",
        "last_release": "2026-10-28",
        "last_sync": "2026-11-03 07:42",
        "status": "warning",
        "status_text": "预警",
        "ukey": "MB-1533-07",
        "note": "本月工程节点释放材料待准备",
    },
    {
        "id": 2,
        "company": "上海临港置业有限公司",
        "project": "临港项目",
        "type": "农民工工资专户",
        "type_key": "wage",
        "bank": "上海银行",
        "branch": "临港支行",
        "account_no": "**** 7315",
        "regulator": "临港新片区人社局",
        "book": 1_860_000,
        "restricted": 1_200_000,
        "available": 660_000,
        "min_balance": 500_000,
        "release": "人工费拨付计划",
        "last_release": "2026-10-30",
        "last_sync": "2026-11-03 07:41",
        "status": "normal",
        "status_text": "正常",
        "ukey": "MB-1533-08",
        "note": "本月人工费已按计划拨付",
    },
    {
        "id": 3,
        "company": "上海张江置业有限公司",
        "project": "张江项目",
        "type": "项目资本金专户",
        "type_key": "capital",
        "bank": "工商银行",
        "branch": "张江支行",
        "account_no": "**** 2096",
        "regulator": "集团资金部",
        "book": 8_500_000,
        "restricted": 6_000_000,
        "available": 2_500_000,
        "min_balance": 3_000_000,
        "release": "项目资本金使用审批",
        "last_release": "2026-10-25",
        "last_sync": "2026-11-03 07:38",
        "status": "warning",
        "status_text": "预警",
        "ukey": "MB-1547-03",
        "note": "可用余额低于最低留存 50 万元",
    },
    {
        "id": 4,
        "company": "上海张江置业有限公司",
        "project": "张江项目",
        "type": "开发贷专户",
        "type_key": "loan",
        "bank": "农业银行",
        "branch": "张江支行",
        "account_no": "**** 6628",
        "regulator": "贷款银行",
        "book": 5_200_000,
        "restricted": 4_000_000,
        "available": 1_200_000,
        "min_balance": 0,
        "release": "受托支付",
        "last_release": "2026-10-31",
        "last_sync": "2026-11-03 07:36",
        "status": "normal",
        "status_text": "正常",
        "ukey": "MB-1547-04",
        "note": "本月开发贷按工程进度受托支付",
    },
    {
        "id": 5,
        "company": "上海滨江置业有限公司",
        "project": "滨江项目",
        "type": "拆迁补偿专户",
        "type_key": "demolition",
        "bank": "中国银行",
        "branch": "滨江支行",
        "account_no": "**** 9182",
        "regulator": "属地街道",
        "book": 3_400_000,
        "restricted": 2_800_000,
        "available": 600_000,
        "min_balance": 1_000_000,
        "release": "补偿协议 / 街道审批",
        "last_release": "2026-10-20",
        "last_sync": "2026-11-03 07:35",
        "status": "warning",
        "status_text": "预警",
        "ukey": "MB-1552-02",
        "note": "可用余额低于最低留存 40 万元",
    },
    {
        "id": 6,
        "company": "上海滨江置业有限公司",
        "project": "滨江项目",
        "type": "专项债资金专户",
        "type_key": "bond",
        "bank": "浦发银行",
        "branch": "滨江支行",
        "account_no": "**** 3350",
        "regulator": "区财政局",
        "book": 6_800_000,
        "restricted": 5_500_000,
        "available": 1_300_000,
        "min_balance": 0,
        "release": "专项债项目进度",
        "last_release": "2026-10-29",
        "last_sync": "2026-11-03 07:34",
        "status": "normal",
        "status_text": "正常",
        "ukey": "MB-1552-03",
        "note": "支出进度正常，待下一次项目节点复核",
    },
    {
        "id": 7,
        "company": "集团本部",
        "project": "集团本部",
        "type": "共管账户",
        "type_key": "joint",
        "bank": "招商银行",
        "branch": "上海分行",
        "account_no": "**** 1048",
        "regulator": "合作方",
        "book": 2_200_000,
        "restricted": 1_100_000,
        "available": 1_100_000,
        "min_balance": 0,
        "release": "双方签章",
        "last_release": "2026-10-18",
        "last_sync": "2026-11-03 07:30",
        "status": "normal",
        "status_text": "正常",
        "ukey": "MB-1501-01",
        "note": "共管账户余额与台账一致",
    },
]

PROJECTS = [
    {
        "name": "临港项目",
        "company": "临港置业",
        "sales": 2_860_000,
        "cost": 1_420_000,
        "gap": 1_440_000,
        "status": "normal",
        "status_text": "回款覆盖支出",
    },
    {
        "name": "张江项目",
        "company": "张江置业",
        "sales": 1_200_000,
        "cost": 2_100_000,
        "gap": -900_000,
        "status": "watch",
        "status_text": "资金缺口",
    },
    {
        "name": "滨江项目",
        "company": "滨江置业",
        "sales": 880_000,
        "cost": 1_650_000,
        "gap": -770_000,
        "status": "warning",
        "status_text": "缺口扩大",
    },
    {
        "name": "集团本部",
        "company": "集团资金部",
        "sales": 350_000,
        "cost": 200_000,
        "gap": 150_000,
        "status": "normal",
        "status_text": "收支正常",
    },
]

ALERTS = [
    {
        "level": "red",
        "title": "张江项目资本金专户低于留存线",
        "detail": "可用余额 250.00 万，最低留存 300.00 万",
    },
    {
        "level": "red",
        "title": "滨江拆迁补偿专户低于留存线",
        "detail": "可用余额 60.00 万，最低留存 100.00 万",
    },
    {
        "level": "amber",
        "title": "临港预售监管户释放材料待准备",
        "detail": "本月工程节点临近，需准备监管释放材料",
    },
    {
        "level": "amber",
        "title": "3 个账户今日更新晚于 07:40",
        "detail": "建议检查 UKey/Agent 采集状态",
    },
]

TREND = [
    ("2026-10-20", 38_200_000, 10_100_000),
    ("2026-10-21", 38_600_000, 10_300_000),
    ("2026-10-22", 39_000_000, 10_400_000),
    ("2026-10-23", 38_800_000, 10_200_000),
    ("2026-10-24", 39_400_000, 10_500_000),
    ("2026-10-25", 39_800_000, 10_600_000),
    ("2026-10-26", 40_100_000, 10_800_000),
    ("2026-10-27", 39_900_000, 10_700_000),
    ("2026-10-28", 40_400_000, 10_900_000),
    ("2026-10-29", 40_800_000, 11_000_000),
    ("2026-10-30", 40_500_000, 10_900_000),
    ("2026-10-31", 40_900_000, 11_000_000),
    ("2026-11-01", 40_600_000, 10_800_000),
    ("2026-11-02", 40_700_000, 10_900_000),
    ("2026-11-03", 40_820_000, 11_020_000),
]


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _decorate_accounts(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        item = dict(row)
        item["book_text"] = _money(item["book"])
        item["restricted_text"] = _money(item["restricted"])
        item["available_text"] = _money(item["available"])
        item["min_balance_text"] = _money(item["min_balance"])
        item["status_class"] = {
            "normal": "green",
            "watch": "amber",
            "warning": "red",
        }.get(item["status"], "")
        item["search"] = " ".join(
            [
                item["company"],
                item["project"],
                item["type"],
                item["bank"],
                item["branch"],
                item["account_no"],
                item["regulator"],
                item["status_text"],
            ]
        )
        result.append(item)
    return result


def _summary(accounts: list[dict]) -> dict:
    book = sum(item["book"] for item in accounts)
    restricted = sum(item["restricted"] for item in accounts)
    available = sum(item["available"] for item in accounts)
    return {
        "book": book,
        "book_text": _money(book),
        "restricted": restricted,
        "restricted_text": _money(restricted),
        "available": available,
        "available_text": _money(available),
        "count": len(accounts),
        "warnings": sum(1 for item in accounts if item["status"] == "warning"),
        "projects": len({item["project"] for item in accounts if item["project"] != "集团本部"}),
        "companies": len({item["company"] for item in accounts}),
        "available_ratio": round(available / book * 100, 1) if book else 0,
        "restricted_ratio": round(restricted / book * 100, 1) if book else 0,
    }


def _line_points(values: list[float], width: int = 620, height: int = 190, pad: int = 14) -> str:
    low, high = min(values), max(values)
    span = max(high - low, 1)
    step = (width - pad * 2) / max(len(values) - 1, 1)
    return " ".join(
        f"{pad + index * step:.1f},{height - pad - (value - low) / span * (height - pad * 2):.1f}"
        for index, value in enumerate(values)
    )


def _trend_context() -> dict:
    book_values = [row[1] for row in TREND]
    available_values = [row[2] for row in TREND]
    book_points = _line_points(book_values)
    available_points = _line_points(available_values)
    baseline = 190 - 14
    return {
        "labels": [row[0][5:] for row in TREND],
        "book_points": book_points,
        "available_points": available_points,
        "book_area": f"14,{baseline} {book_points} 606,{baseline}",
        "available_area": f"14,{baseline} {available_points} 606,{baseline}",
        "first_date": TREND[0][0][5:],
        "last_date": TREND[-1][0][5:],
    }


def _distribution(accounts: list[dict]) -> list[dict]:
    projects: dict[str, dict] = {}
    for item in accounts:
        bucket = projects.setdefault(
            item["project"],
            {"name": item["project"], "amount": 0, "color": item["type_key"]},
        )
        bucket["amount"] += item["book"]
    total = sum(item["amount"] for item in projects.values()) or 1
    return [
        {
            **item,
            "percent": round(item["amount"] / total * 100, 1),
            "amount_text": _money(item["amount"]),
        }
        for item in sorted(projects.values(), key=lambda row: row["amount"], reverse=True)
    ]


def _project_cards(accounts: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for item in accounts:
        bucket = totals.setdefault(
            item["project"],
            {"book": 0, "available": 0, "restricted": 0},
        )
        bucket["book"] += item["book"]
        bucket["available"] += item["available"]
        bucket["restricted"] += item["restricted"]
    cards = []
    for project in PROJECTS:
        total = totals.get(project["name"], {"book": 0, "available": 0})
        item = dict(project)
        item.update(
            {
                "book": total["book"],
                "available": total["available"],
                "book_text": _money(total["book"]),
                "available_text": _money(total["available"]),
                "sales_text": _money(project["sales"]),
                "cost_text": _money(project["cost"]),
                "gap_text": ("+" if project["gap"] >= 0 else "-") + _money(abs(project["gap"])),
                "gap_class": "in" if project["gap"] >= 0 else "out",
            }
        )
        cards.append(item)
    return cards


def _base_context() -> dict:
    accounts = _decorate_accounts(SPECIAL_ACCOUNTS)
    return {
        "data_cutoff": DATA_CUTOFF,
        "accounts": accounts,
        "summary": _summary(accounts),
        "alerts": ALERTS,
        "projects": _project_cards(accounts),
        "distribution": _distribution(accounts),
        "trend": _trend_context(),
        "companies": sorted({item["company"] for item in accounts}),
        "special_types": sorted({item["type"] for item in accounts}),
    }


@router.get("/treasury")
def treasury_page(request: Request):
    context = _base_context()
    context.update(
        {
            "page": "treasury",
            "preview_accounts": context["accounts"][:5],
            "today_in": _money(2_860_000),
            "today_out": _money(1_580_000),
            "today_net": _money(1_280_000),
        }
    )
    return templates.TemplateResponse(request=request, name="treasury.html", context=context)


@router.get("/special-accounts")
def special_accounts_page(request: Request):
    context = _base_context()
    context.update({"page": "special_accounts"})
    return templates.TemplateResponse(request=request, name="special_accounts.html", context=context)
