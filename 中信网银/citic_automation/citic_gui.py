# -*- coding: utf-8 -*-
"""
中信银行企业网银 - Web 可视化工具
支持：批量制单、银行流水采集、电子回单采集
纯 Python 内置库实现，无需安装额外依赖
启动后在浏览器打开 http://localhost:8765
"""

import sys
import os
import json
import time
import queue
import threading
import urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config_loader import load_config, save_config, Config
from loguru import logger

# ========== 全局状态 ==========
_LOG_QUEUE = queue.Queue()
_LOG_SINK_ID = None
_APP_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _APP_DIR / "config.ini"
_BATCH_FILE = _APP_DIR / "temp" / "batch_payments.json"

# 确保 temp 目录存在
_BATCH_FILE.parent.mkdir(parents=True, exist_ok=True)

# 运行状态锁
_RUNNING_LOCK = threading.Lock()
_IS_RUNNING = False
_CURRENT_TASK = ""


# ========== 日志捕获 ==========
def _log_sink(message):
    _LOG_QUEUE.put(message.record["message"])


def _enable_log_capture():
    global _LOG_SINK_ID
    if _LOG_SINK_ID is None:
        _LOG_SINK_ID = logger.add(_log_sink, level="INFO", format="{message}")


def _disable_log_capture():
    global _LOG_SINK_ID
    if _LOG_SINK_ID is not None:
        logger.remove(_LOG_SINK_ID)
        _LOG_SINK_ID = None


def _get_logs(since=0):
    items = []
    while not _LOG_QUEUE.empty() and len(items) < 200:
        try:
            items.append(_LOG_QUEUE.get_nowait())
        except queue.Empty:
            break
    return items


# ========== 配置读写 ==========
def _load_config_dict():
    cfg = load_config(str(_CONFIG_PATH))
    return {
        # 凭证
        "username": cfg.credentials.username,
        "password": cfg.credentials.password,
        "pin": cfg.credentials.pin,
        # 付款
        "payer_account": cfg.collection.payments.payer_account,
        "currency": cfg.collection.payments.currency,
        "business_flow": cfg.collection.payments.business_flow,
        "payment_method": cfg.collection.payments.payment_method,
        "action": cfg.collection.payments.action,
        # 流水
        "statements_enabled": cfg.collection.statements.enabled,
        "statements_start_date": cfg.collection.statements.start_date or "",
        "statements_end_date": cfg.collection.statements.end_date or "",
        "statements_days_back": cfg.collection.statements.days_back,
        # 回单
        "receipts_enabled": cfg.collection.receipts.enabled,
        "receipts_days_back": cfg.collection.receipts.days_back,
        # 浏览器
        "chrome_path": cfg.browser.chrome_path,
        "headless": cfg.browser.headless,
        # 机柜
        "cabinet_type": cfg.cabinet.type,
        "manual_mode": cfg.cabinet.manual_mode,
        "port": cfg.cabinet.port,
    }


def _save_config_dict(data: dict):
    cfg = load_config(str(_CONFIG_PATH))
    # 凭证
    cfg.credentials.username = data.get("username", "")
    cfg.credentials.password = data.get("password", "")
    cfg.credentials.pin = data.get("pin", "")
    # 付款
    cfg.collection.payments.payer_account = data.get("payer_account", "")
    cfg.collection.payments.currency = data.get("currency", "人民币")
    cfg.collection.payments.business_flow = data.get("business_flow", "")
    cfg.collection.payments.payment_method = data.get("payment_method", "")
    cfg.collection.payments.action = data.get("action", "submit")
    # 流水
    cfg.collection.statements.enabled = bool(data.get("statements_enabled", False))
    cfg.collection.statements.start_date = data.get("statements_start_date", "")
    cfg.collection.statements.end_date = data.get("statements_end_date", "")
    cfg.collection.statements.days_back = int(data.get("statements_days_back", 90))
    # 回单
    cfg.collection.receipts.enabled = bool(data.get("receipts_enabled", False))
    cfg.collection.receipts.days_back = int(data.get("receipts_days_back", 90))
    # 浏览器
    cfg.browser.chrome_path = data.get("chrome_path", "")
    cfg.browser.headless = bool(data.get("headless", False))
    # 机柜
    cfg.cabinet.type = data.get("cabinet_type", "mock")
    cfg.cabinet.manual_mode = bool(data.get("manual_mode", False))
    cfg.cabinet.port = int(data.get("port", 1))
    save_config(cfg, str(_CONFIG_PATH))


# ========== 付款数据读写 ==========
_PAYMENT_COLUMNS = [
    ("payment_method", "付款方式"),
    ("payer_account", "付款账号"),
    ("currency", "币种"),
    ("payee_account", "收款账号"),
    ("payee_name", "收款户名"),
    ("payee_account_type", "账户类型"),
    ("payee_bank", "收款银行"),
    ("payee_branch", "开户网点"),
    ("payee_bank_code", "联行号"),
    ("amount", "金额"),
    ("purpose", "附言"),
    ("remark", "备注"),
    ("reconcile_no", "对账编号"),
    ("business_flow", "业务流程"),
]

_PAYMENT_KEYS = [k for k, _ in _PAYMENT_COLUMNS]


def _load_payments():
    if not _BATCH_FILE.exists():
        return []
    try:
        with open(_BATCH_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return [data]
    except Exception as e:
        logger.error(f"加载付款数据失败: {e}")
        return []


def _save_payments(items: list):
    with open(_BATCH_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _payments_to_rows(items: list):
    rows = []
    for item in items:
        row = [str(item.get(k, "")) for k in _PAYMENT_KEYS]
        rows.append(row)
    return rows


def _rows_to_payments(rows: list):
    items = []
    for row in rows:
        item = {}
        for idx, key in enumerate(_PAYMENT_KEYS):
            val = row[idx] if idx < len(row) else ""
            item[key] = str(val) if val is not None else ""
        items.append(item)
    return items


# ========== 业务操作 ==========
def _do_generate():
    """仅生成导入模板"""
    try:
        cfg = load_config(str(_CONFIG_PATH))
        from payment_creator import PaymentCreator

        payments_config = {
            "enabled": True,
            "action": cfg.collection.payments.action,
            "batch_file": str(_BATCH_FILE.relative_to(_APP_DIR)),
            "template_file": cfg.collection.payments.template_file or "template/批量转账模板.xlsx",
            "template_mapping_file": cfg.collection.payments.template_mapping_file or "template/payments_batch_template.json",
            "generated_file": cfg.collection.payments.generated_file or "temp/payment_import.xlsx",
            "payer_account": cfg.collection.payments.payer_account,
            "currency": cfg.collection.payments.currency,
            "business_flow": cfg.collection.payments.business_flow,
            "payment_method": cfg.collection.payments.payment_method,
        }
        creator = PaymentCreator(None, payments_config)
        creator.items = creator._load_items()
        path = creator._generate_batch_import_file()
        logger.info(f"模板已生成: {path}")
        return {"success": True, "message": f"模板已生成: {path}"}
    except Exception as e:
        logger.error(f"生成模板失败: {e}")
        return {"success": False, "message": str(e)}


def _do_run_task(task_type: str):
    """执行任务: payments / statements / receipts / all"""
    global _CURRENT_TASK
    _CURRENT_TASK = task_type
    try:
        # 清空旧日志
        while not _LOG_QUEUE.empty():
            try:
                _LOG_QUEUE.get_nowait()
            except queue.Empty:
                break

        logger.info("=" * 50)
        logger.info(f"开始执行任务: {task_type}")

        from main import CITICAutomation
        app = CITICAutomation(str(_CONFIG_PATH))

        # CITICAutomation 初始化时会重置 loguru，必须在之后添加 sink
        _enable_log_capture()

        if task_type == "payments":
            result = app.run(collect_statements=False, collect_receipts=False, collect_payments=True)
        elif task_type == "statements":
            result = app.run(collect_statements=True, collect_receipts=False, collect_payments=False)
        elif task_type == "receipts":
            result = app.run(collect_statements=False, collect_receipts=True, collect_payments=False)
        elif task_type == "all":
            result = app.run(collect_statements=True, collect_receipts=True, collect_payments=True)
        else:
            result = {"success": False, "error": f"未知任务类型: {task_type}"}

        if result.get("success"):
            logger.info("任务执行完成！")
        else:
            err = result.get("error") or "未知错误"
            logger.error(f"任务失败: {err}")

        _disable_log_capture()
        return result
    except Exception as e:
        _disable_log_capture()
        logger.error(f"执行异常: {e}")
        return {"success": False, "error": str(e)}
    finally:
        _CURRENT_TASK = ""


# ========== HTTP 处理器 ==========
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: bytes, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_html(_HTML_PAGE.encode("utf-8"))
        elif path == "/api/logs":
            qs = urllib.parse.parse_qs(parsed.query)
            since = int(qs.get("since", ["0"])[0])
            logs = _get_logs(since)
            self._send_json({"logs": logs})
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == "/api/config":
            self._send_json(_load_config_dict())
        elif path == "/api/save_config":
            _save_config_dict(body)
            self._send_json({"success": True})
        elif path == "/api/payments":
            self._send_json({"columns": _PAYMENT_COLUMNS, "rows": _payments_to_rows(_load_payments())})
        elif path == "/api/save_payments":
            items = _rows_to_payments(body.get("rows", []))
            _save_payments(items)
            self._send_json({"success": True, "count": len(items)})
        elif path == "/api/generate":
            res = _do_generate()
            self._send_json(res)
        elif path == "/api/run":
            global _IS_RUNNING
            with _RUNNING_LOCK:
                if _IS_RUNNING:
                    self._send_json({"success": False, "message": "已有任务正在运行，请等待完成"})
                    return
                _IS_RUNNING = True

            task = body.get("task", "payments")

            def worker():
                global _IS_RUNNING
                try:
                    _do_run_task(task)
                finally:
                    with _RUNNING_LOCK:
                        _IS_RUNNING = False

            threading.Thread(target=worker, daemon=True).start()
            self._send_json({"success": True, "message": f"任务 [{task}] 已启动"})
        elif path == "/api/status":
            self._send_json({"running": _IS_RUNNING, "task": _CURRENT_TASK})
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ========== 前端页面 ==========
_HTML_PAGE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>中信银行企业网银 - 可视化工具</title>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    margin: 0; padding: 0; background: #f5f7fa; color: #333;
  }
  .header {
    background: linear-gradient(90deg, #c41230 0%, #e03e5c 100%);
    color: #fff; padding: 14px 24px; font-size: 17px; font-weight: 600;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 100;
  }
  .header small { font-size: 12px; font-weight: 400; opacity: 0.9; }
  .container { display: flex; gap: 14px; padding: 14px; max-width: 1700px; margin: 0 auto; }
  .sidebar { width: 300px; flex-shrink: 0; }
  .main { flex: 1; min-width: 0; }
  .card {
    background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    padding: 14px; margin-bottom: 12px;
  }
  .card-title { font-size: 14px; font-weight: 600; margin-bottom: 10px; color: #222; display:flex; align-items:center; gap:6px; }
  .form-row { margin-bottom: 8px; }
  .form-row label {
    display: block; font-size: 12px; color: #555; margin-bottom: 3px;
  }
  .form-row input, .form-row select {
    width: 100%; padding: 6px 9px; border: 1px solid #d9d9d9; border-radius: 4px;
    font-size: 12px; outline: none; transition: border .2s;
  }
  .form-row input:focus, .form-row select:focus { border-color: #c41230; }
  .btn {
    display: inline-flex; align-items: center; justify-content: center;
    padding: 7px 14px; border: none; border-radius: 4px; cursor: pointer;
    font-size: 12px; font-weight: 500; transition: opacity .2s; gap: 4px;
  }
  .btn:hover { opacity: 0.9; }
  .btn-primary { background: #c41230; color: #fff; }
  .btn-secondary { background: #f0f0f0; color: #333; border: 1px solid #d9d9d9; }
  .btn-success { background: #52c41a; color: #fff; }
  .btn-blue { background: #1890ff; color: #fff; }
  .btn-orange { background: #fa8c16; color: #fff; }
  .btn-group { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
  .table-wrap { overflow-x: auto; border: 1px solid #e8e8e8; border-radius: 6px; }
  table {
    width: 100%; border-collapse: collapse; font-size: 12px; min-width: 1200px;
  }
  th {
    background: #fafafa; padding: 7px; text-align: left; font-weight: 600;
    border-bottom: 1px solid #e8e8e8; white-space: nowrap; position: sticky; top: 0;
  }
  td { padding: 5px 7px; border-bottom: 1px solid #f0f0f0; }
  td input {
    width: 100%; border: none; background: transparent; font-size: 12px;
    padding: 3px; outline: none;
  }
  td input:focus { background: #fffbe6; }
  tr:hover td { background: #fafafa; }
  tr.selected td { background: #fff1f0; }
  .log-box {
    background: #1e1e1e; color: #d4d4d4; font-family: "Consolas", "Monaco", monospace;
    font-size: 12px; padding: 12px; border-radius: 6px; height: 220px;
    overflow-y: auto; white-space: pre-wrap; word-break: break-all;
  }
  .badge {
    display: inline-block; padding: 1px 7px; border-radius: 10px;
    font-size: 11px; margin-left: 6px; font-weight: 500;
  }
  .badge-green { background: #f6ffed; color: #389e0d; border: 1px solid #b7eb8f; }
  .badge-gray { background: #f5f5f5; color: #666; border: 1px solid #d9d9d9; }
  .badge-blue { background: #e6f7ff; color: #096dd9; border: 1px solid #91d5ff; }
  .status-bar { font-size: 12px; color: #888; margin-top: 4px; min-height: 18px; }
  .section-divider { border-top: 1px dashed #e8e8e8; margin: 10px 0; }
  .tab-bar { display: flex; gap: 4px; margin-bottom: 10px; border-bottom: 1px solid #e8e8e8; padding-bottom: 8px; }
  .tab-btn { background: transparent; border: none; padding: 6px 14px; cursor: pointer; font-size: 13px; border-radius: 4px; }
  .tab-btn.active { background: #fff1f0; color: #c41230; font-weight: 600; }
  .tab-btn:hover:not(.active) { background: #f5f5f5; }
  .task-panel { display: none; }
  .task-panel.active { display: block; }
</style>
</head>
<body>
<div class="header">
  <span>🏦 中信银行企业网银 · 可视化工具</span>
  <small>Web 版 v1.1 | 支持制单 / 流水 / 回单</small>
</div>

<div class="container">
  <div class="sidebar">
    <div class="card">
      <div class="card-title">🔐 登录凭证</div>
      <div class="form-row"><label>用户名</label><input type="text" id="cfg_username"></div>
      <div class="form-row"><label>密码</label><input type="password" id="cfg_password"></div>
      <div class="form-row"><label>U盾 PIN</label><input type="password" id="cfg_pin"></div>
    </div>

    <div class="card">
      <div class="card-title">💳 付款默认配置</div>
      <div class="form-row"><label>付款账号</label><input type="text" id="cfg_payer_account" placeholder="账号/户名/币种"></div>
      <div class="form-row"><label>币种</label><input type="text" id="cfg_currency" value="人民币"></div>
      <div class="form-row"><label>业务流程</label><input type="text" id="cfg_business_flow" value="审批流程"></div>
      <div class="form-row"><label>付款方式</label><input type="text" id="cfg_payment_method" value="跨行转账"></div>
      <div class="form-row">
        <label>操作类型</label>
        <select id="cfg_action">
          <option value="submit">直接提交</option>
          <option value="save">暂存（停在提交前）</option>
        </select>
      </div>
    </div>

    <div class="card">
      <div class="card-title">📥 采集设置</div>
      <div class="form-row">
        <label>银行流水</label>
        <select id="cfg_statements_enabled">
          <option value="true">启用</option>
          <option value="false">禁用</option>
        </select>
      </div>
      <div class="form-row"><label>流水起始日期</label><input type="date" id="cfg_statements_start_date"></div>
      <div class="form-row"><label>流水结束日期</label><input type="date" id="cfg_statements_end_date"></div>
      <div class="section-divider"></div>
      <div class="form-row">
        <label>电子回单</label>
        <select id="cfg_receipts_enabled">
          <option value="true">启用</option>
          <option value="false">禁用</option>
        </select>
      </div>
      <div class="form-row"><label>回单查询天数</label><input type="number" id="cfg_receipts_days_back" value="90"></div>
    </div>

    <div class="card">
      <div class="card-title">🌐 浏览器 & 机柜</div>
      <div class="form-row"><label>Chrome 路径</label><input type="text" id="cfg_chrome_path" placeholder="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"></div>
      <div class="form-row">
        <label>无头模式</label>
        <select id="cfg_headless">
          <option value="false">否（显示窗口）</option>
          <option value="true">是（后台运行）</option>
        </select>
      </div>
      <div class="form-row">
        <label>机柜类型</label>
        <select id="cfg_cabinet_type">
          <option value="mock">无/手动</option>
          <option value="mb">MB 智能机柜</option>
          <option value="gf3">GF3 / USB-HUB</option>
        </select>
      </div>
      <div class="form-row">
        <label>手动模式</label>
        <select id="cfg_manual_mode">
          <option value="true">是（不控制机柜）</option>
          <option value="false">否（自动控制）</option>
        </select>
      </div>
      <div class="form-row"><label>端口号</label><input type="number" id="cfg_port" value="1"></div>
    </div>

    <div class="btn-group" style="padding: 0 2px;">
      <button class="btn btn-primary" onclick="saveConfig()" style="flex:1">💾 保存配置</button>
    </div>
    <div class="status-bar" id="config_status"></div>
  </div>

  <div class="main">
    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('payments')">📋 批量制单</button>
      <button class="tab-btn" onclick="switchTab('statements')">📥 银行流水</button>
      <button class="tab-btn" onclick="switchTab('receipts')">🧾 电子回单</button>
    </div>

    <!-- 制单面板 -->
    <div id="panel_payments" class="task-panel active">
      <div class="card">
        <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
          <span>付款明细列表 <span class="badge badge-green" id="row_count">0 笔</span></span>
        </div>
        <div class="btn-group">
          <button class="btn btn-secondary" onclick="addRow()">➕ 添加行</button>
          <button class="btn btn-secondary" onclick="deleteSelected()">➖ 删除选中</button>
          <button class="btn btn-secondary" onclick="savePayments()">💾 保存数据</button>
          <button class="btn btn-success" onclick="generateOnly()">📄 仅生成模板</button>
          <button class="btn btn-primary" onclick="runTask('payments')">🚀 执行制单</button>
        </div>
        <div class="table-wrap">
          <table id="payment_table">
            <thead><tr id="table_header"></tr></thead>
            <tbody id="table_body"></tbody>
          </table>
        </div>
        <div class="status-bar" id="payment_status"></div>
      </div>
    </div>

    <!-- 流水面板 -->
    <div id="panel_statements" class="task-panel">
      <div class="card">
        <div class="card-title">📥 银行流水采集</div>
        <p style="font-size:13px;color:#666;margin:0 0 12px 0;">
          自动登录网银，按配置的起止日期查询并下载银行流水 Excel 文件。<br>
          下载路径由 config.ini 中的 <code>collection.statements.download_path</code> 决定。
        </p>
        <div class="btn-group">
          <button class="btn btn-blue" onclick="runTask('statements')">▶️ 开始采集流水</button>
        </div>
        <div class="status-bar" id="statements_status"></div>
      </div>
    </div>

    <!-- 回单面板 -->
    <div id="panel_receipts" class="task-panel">
      <div class="card">
        <div class="card-title">🧾 电子回单采集</div>
        <p style="font-size:13px;color:#666;margin:0 0 12px 0;">
          自动登录网银，查询并下载电子回单 PDF 文件。<br>
          下载路径由 config.ini 中的 <code>collection.receipts.download_path</code> 决定。
        </p>
        <div class="btn-group">
          <button class="btn btn-orange" onclick="runTask('receipts')">▶️ 开始采集回单</button>
        </div>
        <div class="status-bar" id="receipts_status"></div>
      </div>
    </div>

    <!-- 公共日志区 -->
    <div class="card">
      <div class="card-title">
        📜 执行日志
        <span class="badge badge-gray" id="log_status">等待中</span>
        <span class="badge badge-blue" id="log_task" style="display:none"></span>
      </div>
      <div class="log-box" id="log_box">等待任务启动...\n</div>
    </div>
  </div>
</div>

<script>
let columns = [];
let selectedRow = -1;
let logPolling = false;

async function apiPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  return res.json();
}

// ========== Tab 切换 ==========
function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.task-panel').forEach(p => p.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('panel_' + name).classList.add('active');
}

// ========== 配置 ==========
async function loadConfig() {
  const cfg = await apiPost("/api/config");
  for (const [key, val] of Object.entries(cfg)) {
    const el = document.getElementById("cfg_" + key);
    if (!el) continue;
    if (el.tagName === "SELECT") {
      el.value = String(val);
    } else if (el.type === "date") {
      el.value = val || "";
    } else if (typeof val === "boolean") {
      el.value = String(val);
    } else {
      el.value = val || "";
    }
  }
}

async function saveConfig() {
  const data = {};
  const keys = [
    "username","password","pin","payer_account","currency","business_flow",
    "payment_method","action","chrome_path","headless","cabinet_type",
    "manual_mode","port","statements_enabled","statements_start_date",
    "statements_end_date","statements_days_back","receipts_enabled","receipts_days_back"
  ];
  keys.forEach(k => {
    const el = document.getElementById("cfg_" + k);
    if (!el) return;
    let v = el.value;
    if (k === "headless" || k === "manual_mode" || k.endsWith("_enabled")) v = v === "true";
    if (k === "port" || k.endsWith("_days_back")) v = parseInt(v) || 0;
    data[k] = v;
  });
  await apiPost("/api/save_config", data);
  showStatus("config_status", "✅ 配置已保存", "green");
}

// ========== 付款表格 ==========
async function loadPayments() {
  const res = await apiPost("/api/payments");
  columns = res.columns;
  const thead = document.getElementById("table_header");
  thead.innerHTML = columns.map((c, i) => `<th>${c[1]}</th>`).join("");
  renderRows(res.rows);
  updateCount(res.rows.length);
}

function renderRows(rows) {
  const tbody = document.getElementById("table_body");
  tbody.innerHTML = "";
  rows.forEach((row, idx) => {
    const tr = document.createElement("tr");
    tr.dataset.index = idx;
    tr.onclick = () => selectRow(idx);
    row.forEach((cell, cidx) => {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.value = cell;
      inp.onfocus = () => selectRow(idx);
      td.appendChild(inp);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

function selectRow(idx) {
  selectedRow = idx;
  document.querySelectorAll("#table_body tr").forEach(tr => tr.classList.remove("selected"));
  const tr = document.querySelector(`#table_body tr[data-index="${idx}"]`);
  if (tr) tr.classList.add("selected");
}

function getRows() {
  const rows = [];
  document.querySelectorAll("#table_body tr").forEach(tr => {
    const row = [];
    tr.querySelectorAll("td input").forEach(inp => row.push(inp.value));
    rows.push(row);
  });
  return rows;
}

function updateCount(n) {
  document.getElementById("row_count").textContent = n + " 笔";
}

function addRow() {
  const tbody = document.getElementById("table_body");
  const emptyRow = columns.map(() => "");
  const defaults = {
    0: document.getElementById("cfg_payment_method").value,
    1: document.getElementById("cfg_payer_account").value,
    2: document.getElementById("cfg_currency").value,
    12: document.getElementById("cfg_business_flow").value,
  };
  Object.entries(defaults).forEach(([k, v]) => { emptyRow[k] = v; });
  const tr = document.createElement("tr");
  tr.dataset.index = tbody.children.length;
  tr.onclick = () => selectRow(tbody.children.length);
  emptyRow.forEach((cell, cidx) => {
    const td = document.createElement("td");
    const inp = document.createElement("input");
    inp.value = cell;
    inp.onfocus = () => selectRow(tbody.children.length);
    td.appendChild(inp);
    tr.appendChild(td);
  });
  tbody.appendChild(tr);
  updateCount(tbody.children.length);
  selectRow(tbody.children.length - 1);
}

function deleteSelected() {
  if (selectedRow < 0) { showStatus("payment_status", "请先点击选中一行", "red"); return; }
  const tbody = document.getElementById("table_body");
  const tr = tbody.querySelector(`tr[data-index="${selectedRow}"]`);
  if (tr) {
    tr.remove();
    Array.from(tbody.children).forEach((tr, i) => { tr.dataset.index = i; });
    selectedRow = -1;
    updateCount(tbody.children.length);
    showStatus("payment_status", "✅ 已删除", "green");
  }
}

async function savePayments() {
  const rows = getRows();
  await apiPost("/api/save_payments", { rows });
  showStatus("payment_status", "✅ 付款数据已保存", "green");
}

async function generateOnly() {
  showStatus("payment_status", "正在生成模板...", "blue");
  const res = await apiPost("/api/generate");
  if (res.success) {
    showStatus("payment_status", "✅ " + res.message, "green");
  } else {
    showStatus("payment_status", "❌ " + res.message, "red");
  }
}

// ========== 执行任务 ==========
async function runTask(task) {
  // 如果是制单，先保存付款数据
  if (task === "payments") {
    await savePayments();
  }

  if (logPolling) {
    alert("已有任务正在运行，请等待完成");
    return;
  }

  const box = document.getElementById("log_box");
  box.textContent = `任务 [${task}] 启动中...\\n`;
  document.getElementById("log_status").textContent = "运行中";
  document.getElementById("log_status").className = "badge badge-green";
  document.getElementById("log_task").style.display = "inline-block";
  document.getElementById("log_task").textContent = task;

  const res = await apiPost("/api/run", { task });
  if (!res.success) {
    box.textContent += "启动失败: " + res.message + "\\n";
    document.getElementById("log_status").textContent = "失败";
    document.getElementById("log_status").className = "badge badge-gray";
    showStatus(task + "_status", res.message, "red");
    return;
  }

  logPolling = true;
  pollLogs(task);
}

async function pollLogs(task) {
  const box = document.getElementById("log_box");
  while (logPolling) {
    try {
      const res = await fetch("/api/logs?since=0");
      const data = await res.json();
      if (data.logs && data.logs.length) {
        box.textContent += data.logs.join("\\n") + "\\n";
        box.scrollTop = box.scrollHeight;
      }
      const text = box.textContent;
      if (text.includes("任务执行完成") || text.includes("任务失败") || text.includes("执行异常")) {
        logPolling = false;
        document.getElementById("log_status").textContent = "已完成";
        document.getElementById("log_status").className = "badge badge-gray";
        showStatus(task + "_status", "✅ 任务完成", "green");
      }
      // 也查询后端状态，防止前端 missed
      const statusRes = await fetch("/api/status");
      const status = await statusRes.json();
      if (!status.running && data.logs.length === 0) {
        // 如果后端没在运行且没有新日志，再等一轮确认
        await new Promise(r => setTimeout(r, 1500));
        const res2 = await fetch("/api/logs?since=0");
        const data2 = await res2.json();
        if (data2.logs && data2.logs.length) {
          box.textContent += data2.logs.join("\\n") + "\\n";
          box.scrollTop = box.scrollHeight;
        }
        logPolling = false;
        document.getElementById("log_status").textContent = "已完成";
        document.getElementById("log_status").className = "badge badge-gray";
        showStatus(task + "_status", "✅ 任务完成", "green");
      }
    } catch (e) {}
    await new Promise(r => setTimeout(r, 1000));
  }
}

function showStatus(id, text, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = color === "green" ? "#389e0d" : color === "red" ? "#cf1322" : "#096dd9";
  setTimeout(() => { el.textContent = ""; el.style.color = ""; }, 4000);
}

// 初始化
loadConfig();
loadPayments();
</script>
</body>
</html>
'''


def run_server(port=8765):
    server = HTTPServer(("0.0.0.0", port), _Handler)
    url = f"http://localhost:{port}"
    print(f"\n{'=' * 50}")
    print(f"  中信银行 Web 可视化工具已启动")
    print(f"  请在浏览器打开: {url}")
    print(f"{'=' * 50}\n")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")


if __name__ == "__main__":
    run_server()
