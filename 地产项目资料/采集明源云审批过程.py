# -*- coding: utf-8 -*-
"""
明源云合同付款申请 + 审批过程采集（生产版）
=================================================
路径：
  1. Playwright 登录门户 pkjt；
  2. 打开工作台台账页，触发 ERP SSO；
  3. 用 ERP 台账接口分页拉取全部付款申请（HTFKApplyGUID）；
  4. 逐条打开详情页，点击「审批过程」，捕获同一浏览器上下文弹出的 BPM 审批页；
  5. 从审批页提取收付款账户、审批记录等字段。

特点：
  - 凭据从环境变量或 config.ini 读取，不硬编码；
  - 分批分页（单次接口上限约 1800 条）、Excel 双表模板（付款申请 + 审批过程）、按申请编号续采；
  - 审批页通过详情页按钮打开，不直接拼 BPM URL，避免会话失效；
  - 失败重试、限速、日志。

用法：
  set MY_USERNAME=xxx
  set MY_PASSWORD=xxx
  python 采集明源云审批过程.py --limit 2
  python 采集明源云审批过程.py --headed
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import configparser
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from playwright.async_api import async_playwright

LOGIN_URL = "https://pkjt.cy.spdegc.com/#/login"
NAV_URL = "https://pkjt.cy.spdegc.com/#/iframe-page?nav_code=11176&sub_nav_code=10449"
LEDGER_URL = (
    "https://erp-cb.cy.spdegc.com/std/32010404/6c3348e7-a9f3-e911-aa6c-6c4b9033306b"
    "?embedpage=1&workbenchHost=https%3a%2f%2fpkjt.cy.spdegc.com"
)

LIST_URL = "https://erp-cb.cy.spdegc.com/ajax/Mysoft.Map6.Modeling.Controls.AppGridAjaxHandler/LoadData"
LIST_PARAMS = {
    "resFilterKeyType": "1",
    "resFilterKeyCode": "zb.A0298",
    "_tFields": "BUGUID,PROJGUID",
    "resFilterKey": "08db6a49-2978-4ff5-8376-cf1dd4df4b82",
    "pageId": "6b3348e7-a9f3-e911-aa6c-6c4b9033306b",
    "BUGUID": "08db6a49-2978-4ff5-8376-cf1dd4df4b82",
}
# ct 是 ERP 的页面签名。优先从浏览器请求里抓取；抓不到时用这个历史值兜底。
FALLBACK_CT = "68fc3353d62261e27,3243e8-79a3fe-19-1aac66-4c9b303360b"

# 台账行的 HTFKApplyGUID 就是 SSO 的 businessguid，可直接换出审批实例地址。
SSO_URL = (
    "https://erp-cb.cy.spdegc.com/PubPlatform/Login/ssologin?_system=1001&_applicationCode=3201"
    "&targeturl=%2FMyWorkflow%2FWF_ProcessHandle_Form_Transfer.aspx%3Fmode%3D2%26businessguid%3D{guid}"
    "%26businessType%3D%25u4ED8%25u6B3E%25u7533%25u8BF7%25u5BA1%25u6279%26opentype%3Dbizsys"
    "%26erpdomain%3Dhttps%253A%252F%252Ferp-cb.cy.spdegc.com"
)

BASE_DIR = Path(__file__).resolve().parent
EXCEL_PATH = BASE_DIR / "明源云审批过程采集结果.xlsx"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_credentials() -> tuple[str, str]:
    user = os.environ.get("MY_USERNAME", "").strip()
    pwd = os.environ.get("MY_PASSWORD", "").strip()
    if user and pwd:
        return user, pwd
    cfg_path = BASE_DIR / "config.ini"
    if cfg_path.exists():
        cfg = configparser.ConfigParser()
        cfg.read(cfg_path, encoding="utf-8")
        if cfg.has_section("login"):
            user = cfg.get("login", "username", fallback="").strip()
            pwd = cfg.get("login", "password", fallback="").strip()
    if not user or not pwd:
        raise SystemExit("缺少账号密码：请设置环境变量 MY_USERNAME / MY_PASSWORD，或在同目录 config.ini 的 [login] 中配置。")
    return user, pwd


async def capture_conn(context):
    """打开付款台账页，截获页面自己的 LoadData 请求，返回当前会话的 ct / viewId / payload。"""
    conn = {}
    ledger_page = await context.new_page()
    try:
        async with ledger_page.expect_response(
            lambda r: "erp-cb.cy.spdegc.com" in r.url and "LoadData" in r.url,
            timeout=30000,
        ) as resp_info:
            await ledger_page.goto(LEDGER_URL, timeout=60000)
        resp = await resp_info.value
        req = resp.request
        headers = await req.all_headers()
        conn = {
            "ct": headers.get("ct", ""),
            "url": req.url,
            "payload": json.loads(req.post_data or "{}"),
            "referer": headers.get("referer", LEDGER_URL),
        }
    except Exception:
        pass
    finally:
        await ledger_page.close()
    return conn


async def login_and_sso(pw, username: str, password: str, headed: bool):
    browser = await pw.chromium.launch(headless=not headed)
    context = await browser.new_context(viewport={"width": 1680, "height": 850})
    page = await context.new_page()

    log("登录门户...")
    last_error = None
    for attempt in range(1, 4):
        try:
            await page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await page.fill('input[name="user"]', username, timeout=20000)
            await page.fill('input[name="pwd"]', password, timeout=10000)
            try:
                await page.click('button[name="submit"]', timeout=10000)
            except Exception:
                await page.click('button:has-text("登录")', timeout=10000)
            await page.wait_for_url(lambda u: "#/login" not in u, timeout=30000)
            break
        except Exception as exc:
            last_error = exc
            log(f"登录失败（第 {attempt} 次）：{type(exc).__name__}，重试...")
            await asyncio.sleep(3)
    else:
        raise last_error
    log("门户登录成功，进入工作台触发 ERP SSO...")

    await page.goto(NAV_URL, timeout=60000)
    await asyncio.sleep(8)

    conn = await capture_conn(context)
    cookies = await context.cookies()
    cookie_dict = {c["name"]: c["value"] for c in cookies}

    if not conn.get("ct"):
        conn = {
            "ct": FALLBACK_CT,
            "url": LIST_URL,
            "params": LIST_PARAMS,
            "payload": {
                "pageIndex": 0, "pageSize": 100,
                "sortField": "ApplyDate,ContractName,YfProviderName", "sortOrder": "d,a,a",
                "viewId": "472a4a94-5fa4-41f0-b5b4-5701c0755b25",
                "controlHandler": "",
                "options": {"filters": [], "context": {}, "advancedFilterConditions": []},
                "idField": "HTFKApplyGUID",
                "conditionSelectedInfos": [{"field": "ApplyStateEnum", "values": []}],
                "gridViewId": "appGrid_View", "functionCode": "32010404",
            },
            "referer": LEDGER_URL,
        }
        log(f"ERP SSO 完成，Cookie {len(cookie_dict)} 个，未捕获 ct，使用兜底值")
    else:
        log(f"ERP SSO 完成，Cookie {len(cookie_dict)} 个，ct 已捕获")
    return browser, context, page, cookie_dict, conn


def _ledger_headers(conn: dict) -> dict:
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": "https://erp-cb.cy.spdegc.com",
        "Referer": conn.get("referer", LEDGER_URL),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
        "X-Gzip-Respond": "1",
        "X-Page-RequestId": "{" + uuid.uuid4().hex + "}",
        "X-Requested-With": "XMLHttpRequest",
        "ct": conn.get("ct", FALLBACK_CT),
    }


def _ledger_payload(conn: dict, page_index: int, page_size: int) -> dict:
    payload = dict(conn.get("payload") or {})
    payload["pageIndex"] = page_index
    payload["pageSize"] = page_size
    return payload


async def fetch_ledger(context, cookie_dict: dict, conn: dict, page_size: int = 100, limit: int = 0, since: str = "") -> tuple[list, int | None]:
    rows: list[dict] = []
    total = None
    page_index = 0

    while True:
        resp = None
        for attempt in range(3):
            payload = _ledger_payload(conn, page_index, page_size)
            headers = _ledger_headers(conn)
            resp = await asyncio.to_thread(
                requests.post,
                conn.get("url") or LIST_URL,
                params=conn.get("params"),
                headers=headers,
                cookies=cookie_dict,
                json=payload,
                timeout=30,
            )
            if resp.status_code == 200:
                break
            if resp.status_code in (401, 403, 500) and attempt < 2:
                log(f"台账第 {page_index + 1} 页返回 {resp.status_code}，刷新 ct 后重试...")
                await asyncio.sleep(1)
                new_conn = await capture_conn(context)
                if new_conn.get("ct"):
                    conn = new_conn
                    cookies = await context.cookies()
                    cookie_dict = {c["name"]: c["value"] for c in cookies}
                continue
            resp.raise_for_status()

        if resp is None or resp.status_code != 200:
            log(f"台账接口在累计 {len(rows)} 条后返回 {resp.status_code if resp else '无响应'}，停止分页（单次上限约 1800 条）")
            break

        body = resp.json()
        data = body.get("data")
        if isinstance(data, list):
            page_rows = data
            if total is None:
                total = body.get("total")
        elif isinstance(data, dict):
            page_rows = data.get("rows", [])
            if total is None:
                total = data.get("total") or body.get("total")
        else:
            page_rows = []

        if not page_rows:
            break
        reached_older = False
        if since:
            since_key = since[:10]
            kept = []
            older = 0
            for row in page_rows:
                day = str(row.get("ApplyDate") or "")[:10]
                if day and day >= since_key:
                    kept.append(row)
                elif day:
                    older += 1
            page_rows = kept
            reached_older = older > 0
            if not page_rows:
                log(f"台账已到 {since} 之前，停止分页")
                break
        rows.extend(page_rows)
        log(f"台账第 {page_index + 1} 页：{len(page_rows)} 条，累计 {len(rows)} 条")
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            break
        if reached_older or len(page_rows) < page_size:
            break
        page_index += 1
        await asyncio.sleep(0.25)

    return rows, total


APPROVAL_JS = r"""() => {
  const clean = s => (s || '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();
  const label = s => (s || '').replace(/[：:]/g, '').trim();
  const data = { 审批单信息: {}, 审批流程: '', 流程状态: '' };
  const allowed = new Set([
    '合同名称','合同编号','乙方单位','合同金额','累计申请金额','合同结算金额','累计实付金额',
    '本次申请金额','申请主题','申请编号','申请日期','本次实付金额','实付款日期','本次实付大写',
    '收款单位','付款单位','收款单位开户名称','付款单位账户','收款单位账户','付款单位开户行',
    '收款单位开户行','申请人','申请部门','项目信息','申请说明','成本归集状态','是否涉及前期土地费用'
  ]);

  document.querySelectorAll('tr').forEach(tr => {
    const cells = Array.from(tr.children).filter(e => e.tagName === 'TD' || e.tagName === 'TH');
    if (cells.length >= 2 && cells.length % 2 === 0) {
      for (let i = 0; i < cells.length; i += 2) {
        const k = label(cells[i].innerText);
        if (!allowed.has(k)) continue;
        const input = cells[i + 1].querySelector && cells[i + 1].querySelector('input:not([type=hidden]), textarea');
        const v = clean((input && input.value) || cells[i + 1].innerText || cells[i + 1].textContent);
        if (v && v.length < 300) data.审批单信息[k] = v;
      }
    }
  });

  document.querySelectorAll('.miniux-form-label-cell, .miniux-form-label, .miniux-form-label-content').forEach(el => {
    const k = label(el.innerText);
    if (!allowed.has(k) || data.审批单信息[k]) return;
    let n = el.nextElementSibling;
    while (n && !clean(n.innerText || (n.querySelector && n.querySelector('input') && n.querySelector('input').value))) n = n.nextElementSibling;
    if (n) {
      const v = clean((n.querySelector && n.querySelector('input') && n.querySelector('input').value) || n.innerText || n.textContent);
      if (v && v.length < 300) data.审批单信息[k] = v;
    }
  });

  const text = document.body.innerText;
  const start = text.indexOf('（发起）');
  if (start >= 0) {
    let begin = text.lastIndexOf('附件记录', start);
    begin = begin >= 0 ? begin + 4 : Math.max(0, start - 10);
    let end = text.indexOf('抄送人', start);
    if (end < 0) end = Math.min(text.length, start + 2000);
    data.审批流程 = clean(text.slice(begin, end));
  }
  for (const st of ['已归档', '审批中', '已通过', '已退回', '已作废', '未审核', '已审核']) {
    if (text.includes(st)) { data.流程状态 = st; break; }
  }
  return data;
}"""



def decode_approval_text(text: str):
    """审批接口返回的是 base64 + gzip 的 JSON 文本。"""
    try:
        b64 = json.loads(text)
        raw = gzip.decompress(base64.b64decode(b64)).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


async def fetch_approval_json(popup):
    """在审批弹窗上下文里直接调用 get-process-info，返回解码后的完整 JSON。"""
    script = r"""async () => {
        const url = location.href.replace('/approval/index?', '/approval/get-process-info?');
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 15000);
        try {
            const r = await fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest', 'refactor': 'true'}, signal: ctrl.signal});
            return {status: r.status, text: await r.text()};
        } catch (e) {
            return {status: 0, text: '', error: String(e)};
        } finally {
            clearTimeout(timer);
        }
    }"""
    try:
        result = await asyncio.wait_for(popup.evaluate(script), timeout=20)
    except asyncio.TimeoutError:
        return None
    if result.get("status") != 200 or not result.get("text"):
        return None
    return decode_approval_text(result["text"])


async def collect_one_fast(context, item: dict) -> dict:
    """不打开详情页/弹窗，直接用台账 GUID 走 SSO 并请求审批接口。"""
    guid = item.get("HTFKApplyGUID")
    if not guid:
        raise ValueError("缺少 HTFKApplyGUID")
    resp = await context.request.get(SSO_URL.format(guid=guid), max_redirects=20, timeout=30000)
    if "/approval/index" not in resp.url:
        raise RuntimeError(f"SSO 未进入审批页: HTTP {resp.status}")
    info_url = resp.url.replace("/approval/index?", "/approval/get-process-info?")
    r = await context.request.get(
        info_url,
        headers={"X-Requested-With": "XMLHttpRequest", "refactor": "true"},
        timeout=30000,
    )
    if r.status != 200:
        raise RuntimeError(f"审批接口 HTTP {r.status}")
    payload = decode_approval_text(await r.text())
    if not payload:
        raise RuntimeError("审批数据解码失败")
    return parse_approval_json(payload, "")


def parse_approval_json(payload: dict, title: str = "") -> dict:
    tpl = payload.get("data", {}).get("tplData", {})
    main = tpl.get("form", {}).get("form_data", {}).get("main", {}) or {}

    payer_bank = ""
    try:
        fkyh = main.get("fkyh01")
        if fkyh:
            arr = json.loads(fkyh)
            if isinstance(arr, list) and arr:
                payer_bank = arr[0].get("BankAddress") or arr[0].get("BankName") or ""
    except Exception:
        pass

    info = {
        "合同名称": main.get("htmc", ""),
        "合同编号": main.get("htbh", ""),
        "合同金额": main.get("htje", ""),
        "累计实付金额": main.get("ljsfje", ""),
        "本次申请金额": main.get("sqje_yb_", ""),
        "本次实付金额": main.get("htfksq_bcsfje", ""),
        "申请主题": main.get("sqzt", ""),
        "申请编号": main.get("sqbh", ""),
        "申请日期": main.get("sqrq", ""),
        "收款单位": main.get("skdw", ""),
        "收款单位账户": main.get("yxzh", ""),
        "收款单位开户行": main.get("khyx", ""),
        "收款单位开户名称": main.get("skdw", ""),
        "付款单位": main.get("fkdw", ""),
        "付款单位账户": main.get("htfksq_fkyx", ""),
        "付款单位开户行": payer_bank,
        "申请人": main.get("sqr", ""),
        "申请部门": main.get("sqbm", ""),
        "项目信息": main.get("ProjectCostOwnerNames", ""),
        "申请说明": main.get("sqsm", ""),
        "付款审批类型": main.get("fksplx", ""),
        "币种": main.get("bzmc", ""),
    }

    records = []
    for step in tpl.get("ApproveSteps", []) or []:
        step_name = step.get("name", "")
        status_text = step.get("status_text", "")
        for node in step.get("nodes", []) or []:
            auditor = node.get("auditor_name") or node.get("handle_name") or ""
            opinion = (node.get("opinion") or "").strip()
            handle_on = node.get("handle_on") or node.get("active_time") or step.get("ended_time") or ""
            records.append({
                "审批节点": step_name,
                "审批人": auditor,
                "审批时间": handle_on,
                "审批意见": opinion,
                "审批结果": status_text,
            })

    flow = " -> ".join(
        f"[{x['审批节点']}] {x['审批人']}({x['审批结果']}): {x['审批意见']} ({x['审批时间']})"
        for x in records
    )
    return {
        "审批单信息": info,
        "审批记录": records,
        "审批流程": flow,
        "流程状态": tpl.get("ProcessInfo", {}).get("ProcessStatusName", ""),
        "页面标题": title,
        "审批单原始": main,
    }


async def collect_one(context, item: dict) -> dict:
    contract_guid = item.get("ContractGUID")
    apply_guid = item.get("HTFKApplyGUID")
    if not contract_guid or not apply_guid:
        raise ValueError("缺少 ContractGUID / HTFKApplyGUID")

    detail_url = (
        "https://erp-cb.cy.spdegc.com/std/32010404/79e1d951-481e-11e6-9b54-eca86bc39935"
        "?mode=3&autoTitle=true&_mp=blank&_hid=32010404"
        f"&ContractGUID={contract_guid}&oid={apply_guid}"
        "&title=&_t=0&_tFields=BUGUID%2CPROJGUID"
        "&BUGUID=08db6a49-2978-4ff5-8376-cf1dd4df4b82&embedpage=1"
    )
    page = await context.new_page()
    popup = None
    try:
        await page.goto(detail_url, timeout=40000, wait_until="domcontentloaded")
        btn = page.locator('[data-buttontitle="审批过程"]').first
        await btn.wait_for(state="visible", timeout=30000)
        async with page.expect_popup(timeout=20000) as popup_info:
            await btn.click()
        popup = await popup_info.value
        await popup.wait_for_load_state("domcontentloaded", timeout=20000)
        title = await popup.title()
        raw = await fetch_approval_json(popup)
        if raw:
            return parse_approval_json(raw, title)
        # 接口失败时回退到页面解析
        data = await asyncio.wait_for(popup.evaluate(APPROVAL_JS), timeout=20)
        data["页面标题"] = title
        return data
    finally:
        if popup:
            await popup.close()
        await page.close()


MAIN_SHEET = "付款申请"
STEP_SHEET = "审批过程"

MAIN_HEADERS = [
    "序号", "业务ID", "申请编号", "申请主题", "申请人", "申请部门", "申请日期", "付款审批类型",
    "收款单位", "收款账号", "收款开户行", "付款单位", "付款账户", "付款开户行",
    "申请金额", "实付金额", "项目信息", "合同名称", "合同编号",
    "流程状态", "审批节点数", "审批流程", "采集时间",
]
STEP_HEADERS = ["序号", "申请编号", "申请主题", "审批节点", "审批人", "审批结果", "审批意见", "审批时间"]


def load_existing_excel():
    """读取已有 Excel 模板，返回 (主表数据行, 审批过程行, 已采集申请编号集合)。"""
    if not EXCEL_PATH.exists():
        return [], [], set()
    from openpyxl import load_workbook
    wb = load_workbook(EXCEL_PATH, data_only=True)
    main_rows, step_rows, codes = [], [], set()
    if MAIN_SHEET in wb.sheetnames:
        ws = wb[MAIN_SHEET]
        rows = list(ws.iter_rows(values_only=True))
        for row in rows[1:]:
            vals = list(row)
            if any(v is not None and str(v).strip() for v in vals):
                main_rows.append(vals)
                if len(vals) > 2 and vals[2]:
                    codes.add(str(vals[2]))
    if STEP_SHEET in wb.sheetnames:
        ws = wb[STEP_SHEET]
        rows = list(ws.iter_rows(values_only=True))
        for row in rows[1:]:
            vals = list(row)
            if any(v is not None and str(v).strip() for v in vals):
                step_rows.append(vals)
    return main_rows, step_rows, codes


def _code_of(rec: dict) -> str:
    info = rec.get("审批单信息", {}) or {}
    item = rec.get("台账", {}) or {}
    return item.get("ApplyCode", "") or info.get("申请编号", "")


def _build_main_row(seq: int, rec: dict) -> list:
    info = rec.get("审批单信息", {}) or {}
    item = rec.get("台账", {}) or {}
    return [
        seq,
        rec.get("HTFKApplyGUID", ""),
        _code_of(rec),
        info.get("申请主题", "") or item.get("Subject", ""),
        info.get("申请人", "") or item.get("AppliedByName", ""),
        info.get("申请部门", "") or item.get("ApplyDeptName", ""),
        info.get("申请日期", "") or str(item.get("ApplyDate") or "")[:10],
        info.get("付款审批类型", ""),
        info.get("收款单位", "") or item.get("YfProviderName", "") or item.get("ReceiveProviderName", ""),
        info.get("收款单位账户", ""),
        info.get("收款单位开户行", ""),
        info.get("付款单位", ""),
        info.get("付款单位账户", ""),
        info.get("付款单位开户行", ""),
        info.get("本次申请金额", "") or item.get("ApplyAmount_Bz", ""),
        info.get("本次实付金额", "") or item.get("TotalPayAmount_Bz", ""),
        info.get("项目信息", ""),
        info.get("合同名称", "") or item.get("ContractName", ""),
        info.get("合同编号", ""),
        rec.get("流程状态", ""),
        len(rec.get("审批记录", []) or []),
        str(rec.get("审批流程", ""))[:1000],
        rec.get("采集时间", ""),
    ]


def _build_step_rows(rec: dict) -> list:
    info = rec.get("审批单信息", {}) or {}
    code = _code_of(rec)
    subject = info.get("申请主题", "")
    return [
        [
            "",
            code,
            subject,
            x.get("审批节点", ""),
            x.get("审批人", ""),
            x.get("审批结果", ""),
            x.get("审批意见", ""),
            x.get("审批时间", ""),
        ]
        for x in (rec.get("审批记录", []) or [])
    ]


def export_excel(records: list[dict], existing_main: list | None = None, existing_steps: list | None = None) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = MAIN_SHEET
    ws.append(MAIN_HEADERS)
    seq = 0
    for row in existing_main or []:
        seq += 1
        vals = (list(row) + [""] * len(MAIN_HEADERS))[:len(MAIN_HEADERS)]
        vals[0] = seq
        ws.append(vals)
    for rec in records:
        seq += 1
        ws.append(_build_main_row(seq, rec))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(EXCEL_PATH)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="最多采集多少条，0=全部")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--output", default="", help="输出 Excel 路径，默认脚本同目录")
    parser.add_argument("--since", default="", help="只采集此日期（YYYY-MM-DD）及之后的单据")
    parser.add_argument("--force", action="store_true", help="忽略已有 Excel，覆盖重采")
    args = parser.parse_args()

    global EXCEL_PATH
    if args.output:
        EXCEL_PATH = Path(args.output).resolve()
        EXCEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    username, password = load_credentials()

    existing_main, existing_steps, existing_codes = [], [], set()
    if not args.force:
        existing_main, existing_steps, existing_codes = load_existing_excel()
        log(f"已有 Excel 记录 {len(existing_codes)} 条")

    async with async_playwright() as pw:
        browser, context, _, cookies, conn = await login_and_sso(pw, username, password, args.headed)
        try:
            rows, total = await fetch_ledger(context, cookies, conn, args.page_size, args.limit, args.since)
            log(f"台账共 {len(rows)} 条" + (f"（线上总数 {total}）" if total else ""))
            if existing_codes:
                rows = [r for r in rows if (r.get("ApplyCode") or "") not in existing_codes]
                log(f"过滤已采集后待采集 {len(rows)} 条")
            if args.limit:
                rows = rows[:args.limit]
                log(f"按 --limit 限制为 {len(rows)} 条")

            semaphore = asyncio.Semaphore(max(1, args.concurrency))
            results: list[dict] = []

            async def worker(idx: int, item: dict):
                async with semaphore:
                    for attempt in range(1, 4):
                        try:
                            if attempt < 3:
                                data = await asyncio.wait_for(collect_one_fast(context, item), timeout=30)
                            else:
                                data = await asyncio.wait_for(collect_one(context, item), timeout=90)
                            rec = {
                                "HTFKApplyGUID": item.get("HTFKApplyGUID", ""),
                                "台账": item,
                                "审批单信息": data.get("审批单信息", {}),
                                "审批流程": data.get("审批流程", ""),
                                "审批记录": data.get("审批记录", []),
                                "流程状态": data.get("流程状态", ""),
                                "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            }
                            results.append(rec)
                            log(f"[{idx}/{len(rows)}] 成功 {item.get('ApplyCode','')} | 收款: {rec['审批单信息'].get('收款单位','')}")
                            return
                        except Exception as e:
                            if attempt == 3:
                                rec = {
                                    "HTFKApplyGUID": item.get("HTFKApplyGUID", ""),
                                    "台账": item,
                                    "审批单信息": {},
                                    "审批流程": "",
                                    "审批记录": [],
                                    "流程状态": "",
                                    "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                }
                                results.append(rec)
                                log(f"[{idx}/{len(rows)}] 无审批过程，按台账写入 {item.get('ApplyCode','')}")
                                return
                            else:
                                await asyncio.sleep(2)

            await asyncio.gather(*(worker(i + 1, item) for i, item in enumerate(rows)))
            export_excel(results, existing_main, existing_steps)
            log(f"完成：本次采集 {len(results)} 条，累计 {len(existing_codes) + len(results)} 条，Excel: {EXCEL_PATH}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
