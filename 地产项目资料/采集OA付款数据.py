# -*- coding: utf-8 -*-
"""
OA 请款 / 财务报销数据采集
==========================
范围：
  - 请款（wf_id=69）
  - 无合同请款（wf_id=219）
  - 财务报销：人员经费、党团经费、行政费用、税金还本付息、内部资金划转、其他类
    （wf_id=119/121/125/127/129/131）

路径：
  1. requests 登录 OA（密码为 md5(base64(md5(明文)))，图形验证码用 ddddocr 识别）；
  2. 调 WorkFlowHandler.ashx?method=GetJSSX 分页取列表（JSON）；
  3. 按登记日期过滤，逐条打开 WorkFlow_Form.aspx 解析详情；
  4. 输出平台可导入的 Excel（付款申请 sheet）。

用法：
  set OA_USERNAME=xxx
  set OA_PASSWORD=xxx
  python 采集OA付款数据.py --since 2026-09-01
"""

from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import html as html_lib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from lxml import html

OA_BASE = "http://172.28.1.214/OA"
LOGIN_URL = f"{OA_BASE}/mylogin.aspx"
LIST_API = f"{OA_BASE}/ajax/WorkFlowHandler.ashx"
DETAIL_URL = f"{OA_BASE}/workflow/Form/WorkFlow_Form.aspx"
LIST_REFERER = f"{OA_BASE}/workflow/form/WorkFlow_BY_List.aspx"

FLOWS = [
    ("请款", 69),
    ("无合同请款", 219),
    ("财务报销（人员经费）", 119),
    ("财务报销（党团经费）", 121),
    ("财务报销（行政费用）", 125),
    ("财务报销（税金、还本付息）", 127),
    ("财务报销（内部资金划转）", 129),
    ("财务报销（其他）", 131),
]

LIST_FIELDS = "lsh,lcmc,biaoti,djrq,create_dept_name,dqwz,gdrq,wf_id,doc_id,act_id"

HEADERS = [
    "序号", "业务ID", "来源系统", "单据类型", "审核状态", "申请编号", "申请主题",
    "申请人", "申请部门", "申请日期", "付款审批类型", "收款单位", "收款账号", "收款开户行",
    "付款单位", "付款账户", "付款开户行", "申请金额", "实付金额", "项目信息",
    "合同名称", "合同编号", "流程状态", "当前节点", "当前处理人", "采集时间",
]

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "OA付款数据采集结果.xlsx"

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_credentials() -> tuple[str, str]:
    user = os.environ.get("OA_USERNAME", "").strip()
    pwd = os.environ.get("OA_PASSWORD", "").strip()
    if user and pwd:
        return user, pwd
    cfg_path = BASE_DIR / "config.ini"
    if cfg_path.exists():
        cfg = configparser.ConfigParser()
        cfg.read(cfg_path, encoding="utf-8")
        if cfg.has_section("oa"):
            user = cfg.get("oa", "username", fallback="").strip()
            pwd = cfg.get("oa", "password", fallback="").strip()
    if not user or not pwd:
        raise SystemExit("缺少 OA 账号密码：请设置 OA_USERNAME / OA_PASSWORD，或在 config.ini 的 [oa] 中配置。")
    return user, pwd


def _ocr():
    try:
        import ddddocr
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("缺少 ddddocr，无法识别 OA 验证码：pip install ddddocr") from exc
    return ddddocr.DdddOcr(show_ad=False)


def _hidden_fields(page: str) -> dict:
    values = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', page)
        values[name] = match.group(1) if match else ""
    return values


def _failure_count(page: str) -> int:
    match = re.search(r"if \((\d+) >= 5\)", page)
    return int(match.group(1)) if match else 0


def login(username: str, password: str, ocr) -> requests.Session:
    inner = hashlib.md5(password.encode("utf-8")).hexdigest()
    hashed = hashlib.md5(base64.b64encode(inner.encode("ascii"))).hexdigest()
    user_b64 = base64.b64encode(username.encode("utf-8")).decode("ascii")

    last_error = ""
    for attempt in range(1, 4):
        session = requests.Session()
        session.trust_env = False  # OA 在内网，绕过系统代理直连
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/145.0.0.0 Safari/537.36",
            "Referer": LOGIN_URL,
        })
        page = session.get(LOGIN_URL, timeout=30)
        page.encoding = "utf-8"
        if _failure_count(page.text) >= 5:
            last_error = "登录会话已被锁定"
            log(f"登录第 {attempt} 次：当前会话已锁定，换新会话重试")
            continue

        code = ""
        for _ in range(3):
            img = session.get(f"{OA_BASE}/ImgCode.aspx?t={time.time()}", timeout=30)
            code = (ocr.classification(img.content) or "").strip()
            if len(code) == 4:
                break
            code = ""
        if not code:
            last_error = "验证码识别失败"
            log(f"登录第 {attempt} 次：验证码识别失败，重试")
            continue

        data = {
            **_hidden_fields(page.text),
            "txtUserName": username,
            "H_User_Name": user_b64,
            "txtPassword": hashed,
            "txtCode": code,
            "ImageButton1.x": "0",
            "ImageButton1.y": "0",
        }
        resp = session.post(LOGIN_URL, data=data, allow_redirects=False, timeout=30)
        if resp.status_code == 302 and resp.headers.get("Location", "").lower().endswith("main.aspx"):
            if "person" not in session.cookies.get_dict():
                last_error = "登录成功但未取得 person Cookie"
                log(f"登录第 {attempt} 次：{last_error}")
                continue
            log(f"OA 登录成功（第 {attempt} 次，验证码 {code}）")
            return session
        last_error = f"HTTP {resp.status_code}"
        log(f"登录第 {attempt} 次失败：{last_error}，换新会话重试")
        time.sleep(1)
    raise SystemExit(f"OA 登录失败：{last_error}")


def fetch_flow_rows(session: requests.Session, wf_id: int, since: str = "", until: str = "") -> list[dict]:
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": LIST_REFERER}
    rows: list[dict] = []
    page = 1
    total = None
    while True:
        params = {
            "method": "GetJSSX",
            "page": page,
            "rows": 500,
            "fields": LIST_FIELDS,
            "wf_id": wf_id,
        }
        if since:
            params["djrq_S"] = since
        if until:
            params["djrq_E"] = until
        resp = session.get(LIST_API, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("rows") or []
        total = body.get("total") if total is None else total
        if not batch:
            break
        rows.extend(batch)
        if len(rows) >= (total or 0) or len(batch) < 500:
            break
        page += 1
        time.sleep(0.15)
    return rows


def _text_value(el) -> str:
    if el is None:
        return ""
    if el.tag == "select":
        selected = el.xpath(".//option[@selected]")
        if selected:
            return " ".join(selected[0].text_content().split())
        options = el.xpath(".//option")
        return " ".join(options[0].text_content().split()) if options else ""
    return (el.get("value") or "").strip()


def _input_map(doc) -> dict[str, str]:
    result: dict[str, str] = {}
    for el in doc.xpath("//input|//select|//textarea"):
        name = el.get("name") or el.get("id")
        if not name or name.startswith("__") or name in result:
            continue
        result[name] = _text_value(el)
    return result


def _span_text(doc, element_id: str) -> str:
    items = doc.xpath(f'//*[@id="{element_id}"]')
    if not items:
        return ""
    return " ".join(items[0].text_content().split())


def _row_text(doc, labels: list[str]) -> str:
    for tr in doc.xpath("//tr"):
        text = " ".join(" ".join(td.text_content().split()) for td in tr.xpath("./td"))
        if any(label in text for label in labels):
            return text
    return ""


def _after_label(text: str, labels: list[str]) -> str:
    for label in labels:
        match = re.search(rf"{label}[：:]\s*([^\s]+)", text)
        if match:
            return match.group(1).strip()
    return ""


def _decimal(value: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d.\-]", "", value or "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _position(raw: str) -> tuple[str, str, str]:
    """从当前办理位置解析（流程状态, 当前节点, 当前处理人）。"""
    raw = raw or ""
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", raw)).replace("\xa0", " ")
    text = re.sub(r"\(\s*\d+\s*天\s*\)", "", text)
    text = " ".join(text.split())
    ended = "流程已结束" in text or "已归档" in text
    parts = [part.strip() for part in text.split("/") if part.strip()]
    node = parts[0] if parts else ""
    handler = parts[1] if len(parts) > 1 else ""
    if ended:
        return "已归档", "已归档", ""
    return "处理中", node, handler


def parse_detail(session: requests.Session, row: dict, flow_name: str) -> dict:
    params = {
        "WF_ID": row.get("wf_id"),
        "finish": "",
        "open": "",
        "act_id": row.get("act_id"),
    }
    resp = session.get(DETAIL_URL, params=params, headers={"Referer": LIST_REFERER}, timeout=60)
    resp.raise_for_status()
    doc = html.fromstring(resp.text)
    inputs = _input_map(doc)

    title = inputs.get("biaoti") or inputs.get("Biaoti") or row.get("biaoti", "")
    lcmc = inputs.get("LCMC") or row.get("lcmc", "")
    amount = (
        _decimal(inputs.get("shbcfkje", ""))
        or _decimal(inputs.get("bcqfk", ""))
        or _decimal(inputs.get("HJJE", ""))
        or _decimal(inputs.get("HJJE_", ""))
    )
    if amount is None:
        raise ValueError("缺少金额")

    company = inputs.get("TitleDW", "")
    dept_row = _row_text(doc, ["请款部门", "申请部门", "报销部门"])
    dept_labels = doc.xpath('//*[@id="BM_ID"]//label')
    department = (
        " ".join(dept_labels[0].text_content().split()) if dept_labels else ""
    ) or _after_label(dept_row, ["请款部门", "申请部门", "报销部门"]) or row.get("create_dept_name", "")
    people_row = _row_text(doc, ["请款人", "申请人", "报销人", "经办人"])
    applicant = _span_text(doc, "L_NGR") or _after_label(people_row, ["请款人", "申请人", "报销人", "经办人"])

    status, node, handler = _position(row.get("dqwz", ""))
    if row.get("gdrq") and status != "已归档":
        status, node, handler = "已归档", "已归档", ""

    approval_type = inputs.get("FYLB") or flow_name
    contract_name = _span_text(doc, "htmc")
    contract_no = _span_text(doc, "htbh")
    project = inputs.get("zchsxm") or ""

    return {
        "业务ID": str(row.get("act_id") or row.get("lsh") or ""),
        "来源系统": "OA",
        "单据类型": flow_name,
        "审核状态": "已审核" if status == "已归档" else "审核中",
        "申请编号": lcmc,
        "申请主题": title,
        "申请人": applicant,
        "申请部门": department,
        "申请日期": str(row.get("djrq") or "")[:10],
        "付款审批类型": approval_type,
        "收款单位": inputs.get("SKDW", ""),
        "收款账号": re.sub(r"\s+", "", inputs.get("SKYHZH", "")),
        "收款开户行": inputs.get("SKYH", ""),
        "付款单位": company,
        "付款账户": re.sub(r"\s+", "", inputs.get("FKYHZH", "")),
        "付款开户行": inputs.get("FKYH", ""),
        "申请金额": amount,
        "实付金额": amount,
        "项目信息": project,
        "合同名称": contract_name,
        "合同编号": contract_no,
        "流程状态": status,
        "当前节点": node,
        "当前处理人": handler,
        "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def export_excel(records: list[dict], output: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "付款申请"
    ws.append(HEADERS)
    for index, rec in enumerate(records, 1):
        values = []
        for header in HEADERS:
            value = index if header == "序号" else rec.get(header, "")
            if isinstance(value, Decimal):
                value = float(value)
            values.append(value)
        ws.append(values)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="", help="只采集此日期（YYYY-MM-DD）及之后的单据，默认全部")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 Excel 路径")
    parser.add_argument("--workers", type=int, default=4, help="详情并发数，默认 4")
    parser.add_argument("--codes", default="", help="只采集指定流程编号，逗号分隔")
    parser.add_argument("--skip-codes-file", default="", help="跳过文件中的流程编号（每行一个），用于跳过已归档单据")
    args = parser.parse_args()

    username, password = load_credentials()
    ocr = _ocr()
    session = login(username, password, ocr)

    rows_by_code: dict[str, dict] = {}
    until = datetime.now().strftime("%Y-%m-%d") if args.since else ""
    for flow_name, wf_id in FLOWS:
        rows = fetch_flow_rows(session, wf_id, args.since, until)
        in_range = [r for r in rows if not args.since or str(r.get("djrq") or "")[:10] >= args.since]
        for row in in_range:
            rows_by_code.setdefault(row.get("lcmc", ""), {**row, "_FlowName": flow_name})
        log(f"{flow_name:14s} 全部 {len(rows):4d} 条，范围内 {len(in_range):4d} 条")

    todo = list(rows_by_code.values())
    if args.codes:
        wanted = {code.strip() for code in args.codes.split(",") if code.strip()}
        todo = [row for row in todo if row.get("lcmc") in wanted]
        log(f"按流程编号过滤后 {len(todo)} 条")
    if args.skip_codes_file:
        skip_path = Path(args.skip_codes_file)
        if skip_path.exists():
            skip = {line.strip() for line in skip_path.read_text(encoding="utf-8").splitlines() if line.strip()}
            before = len(todo)
            todo = [row for row in todo if row.get("lcmc") not in skip]
            log(f"跳过已归档 {before - len(todo)} 条，剩余 {len(todo)} 条")
    log(f"去重后待采集 {len(todo)} 条")
    if not todo:
        log("没有需要采集的单据")
        output = Path(args.output).resolve()
        export_excel([], output)
        log(f"完成：0 条，Excel: {output}")
        return

    base_cookies = session.cookies.get_dict()
    thread_local = threading.local()

    def thread_session() -> requests.Session:
        if not hasattr(thread_local, "session"):
            local = requests.Session()
            local.trust_env = False
            local.cookies.update(base_cookies)
            local.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/145.0.0.0 Safari/537.36",
                "Referer": LIST_REFERER,
            })
            thread_local.session = local
        return thread_local.session

    def work(row: dict) -> dict:
        last_error = None
        for attempt in range(1, 4):
            try:
                return parse_detail(thread_session(), row, row.get("_FlowName", ""))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(1)
        raise last_error  # type: ignore[misc]

    records: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, row): row for row in todo}
        for index, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{row.get('lcmc')}: {exc}")
                log(f"[{index}/{len(todo)}] 失败 {row.get('lcmc')}：{exc}")
                continue
            if index % 10 == 0 or index == len(todo):
                log(f"[{index}/{len(todo)}] 已解析")

    records.sort(key=lambda r: (r.get("申请日期", ""), r.get("申请编号", "")), reverse=True)
    output = Path(args.output).resolve()
    export_excel(records, output)
    log(f"完成：成功 {len(records)} 条，失败 {len(errors)} 条，Excel: {output}")
    for error in errors[:10]:
        log(f"  失败：{error}")


if __name__ == "__main__":
    main()
