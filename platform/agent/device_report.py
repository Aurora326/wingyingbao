from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AGENT_VERSION = "device-report/1.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 UKeyAgent/1.0"
)
BASE_DIR = Path(__file__).resolve().parent


def load_env() -> None:
    env_file = BASE_DIR / "agent.env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def log(message: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def stop_requested() -> bool:
    path = os.getenv("PLATFORM_AGENT_STOP_FILE", "").strip()
    return bool(path and Path(path).exists())


def sleep_until_next(seconds: int) -> None:
    end = time.time() + max(1, seconds)
    while time.time() < end:
        if stop_requested():
            return
        time.sleep(min(1.0, end - time.time()))


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return "127.0.0.1"


def _find_terminals(payload) -> list[dict]:
    if isinstance(payload, list):
        if any(isinstance(item, dict) and "ter_id" in item for item in payload):
            return [item for item in payload if isinstance(item, dict) and "ter_id" in item]
        for item in payload:
            found = _find_terminals(item)
            if found:
                return found
        return []
    if not isinstance(payload, dict):
        return []
    if "ter_id" in payload and any(key in payload for key in ("usb_port_manage", "robotarm_manage")):
        return [payload]
    for key in ("data", "response", "result", "list", "items"):
        if key in payload:
            found = _find_terminals(payload[key])
            if found:
                return found
    for value in payload.values():
        found = _find_terminals(value)
        if found:
            return found
    return []


def _to_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _port_status(port: dict) -> str:
    if str(port.get("dev_exists") or "") != "1":
        return "empty"
    if str(port.get("usb_share") or "") == "1" or str(port.get("client_ip") or "").strip():
        return "busy"
    return "online"


def read_terminal(ter_id: str, mb_exe: str, timeout: int = 30) -> dict:
    command = [mb_exe, "status", "--ter_id", ter_id]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(Path(mb_exe).parent) if Path(mb_exe).parent.exists() else None,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"未找到 mb.exe: {mb_exe}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"mb.exe status 超时: {ter_id}") from exc
    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError(f"mb.exe 没有输出: {(proc.stderr or '').strip()[:200]}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"mb.exe 输出不是 JSON: {text[:200]}") from exc
    if not payload.get("result"):
        raise RuntimeError(str(payload.get("error") or "mb.exe 返回失败"))
    terminals = _find_terminals(payload)
    if not terminals:
        raise RuntimeError("mb.exe 返回中没有机柜数据")
    ter = next((item for item in terminals if str(item.get("ter_id")) == ter_id), terminals[0])
    eth = ter.get("eth_manage") or {}
    port_manage = ter.get("usb_port_manage") or {}
    ports = port_manage.get("usb_port_info") or []
    devices = []
    for port in ports:
        if not isinstance(port, dict):
            continue
        port_no = _to_int(port.get("usb_port"))
        if port_no <= 0:
            continue
        bank_info = port.get("bank_infos") or {}
        devices.append(
            {
                "port": port_no,
                "bank_code": "",
                "bank_name": str(bank_info.get("bank_type") or "").strip(),
                "status": _port_status(port),
                "external_id": str(port.get("usb_pid") or "").strip(),
                "port_name": str(port.get("port_name") or "").strip(),
                "usb_share": _to_int(port.get("usb_share")),
                "client_ip": str(port.get("client_ip") or "").strip(),
                "port_power": _to_int(port.get("port_power")),
                "user": str(port.get("user") or "").strip(),
            }
        )
    return {
        "ter_id": str(ter.get("ter_id") or ter_id),
        "ter_ip": str(eth.get("ip_address") or ""),
        "version": str(ter.get("ter_version_detail") or ter.get("ter_version") or ""),
        "port_count": _to_int(port_manage.get("usb_port_num")) or len(ports),
        "devices": devices,
    }


def build_report(ter_id: str, mb_exe: str, agent_code: str, agent_name: str, timeout: int) -> dict:
    terminal = read_terminal(ter_id, mb_exe, timeout=timeout)
    return {
        "agent_code": agent_code,
        "agent_name": agent_name or socket.gethostname(),
        "hostname": socket.gethostname(),
        "ip": local_ip(),
        "version": AGENT_VERSION,
        "ter_id": terminal["ter_id"],
        "ter_ip": terminal["ter_ip"],
        "port_count": terminal["port_count"],
        "devices": terminal["devices"],
    }


def post_report(url: str, token: str, report: dict, timeout: int = 30) -> dict:
    endpoint = url.rstrip("/") + "/api/agent/devices/report"
    body = json.dumps(report, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"平台返回 {exc.code}: {detail}") from exc


def run_once(args) -> int:
    failed = 0
    ter_ids = [item.strip() for item in (args.ter_id or "").split(",") if item.strip()]
    if not ter_ids:
        log("未配置 ter_id")
        return 1
    for ter_id in ter_ids:
        try:
            report = build_report(ter_id, args.mb_exe, args.agent_code, args.agent_name, args.timeout)
            result = post_report(args.url, args.token, report, timeout=args.timeout)
            log(
                f"上报成功 ter={ter_id} total={result.get('total')} "
                f"created={result.get('created')} updated={result.get('updated')} "
                f"missing={result.get('missing')}"
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log(f"上报失败 ter={ter_id}: {exc}")
    return 1 if failed else 0


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="MB UKey 设备上报 Agent")
    parser.add_argument("--url", default=os.getenv("PLATFORM_URL", ""))
    parser.add_argument("--token", default=os.getenv("PLATFORM_AGENT_TOKEN", ""))
    parser.add_argument("--agent-code", default=os.getenv("PLATFORM_AGENT_CODE", "cust-1517"))
    parser.add_argument("--agent-name", default=os.getenv("PLATFORM_AGENT_NAME", ""))
    parser.add_argument("--ter-id", default=os.getenv("PLATFORM_MB_TER_IDS", ""))
    parser.add_argument("--mb-exe", default=os.getenv("PLATFORM_MB_EXE", "mb.exe"))
    parser.add_argument("--interval", type=int, default=int(os.getenv("PLATFORM_REPORT_INTERVAL", "300")))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.url or not args.token:
        log("缺少 PLATFORM_URL 或 PLATFORM_AGENT_TOKEN")
        return 1
    if args.once:
        return run_once(args)
    log(f"Agent 启动：{args.agent_code} → {args.url}，间隔 {args.interval}s")
    while not stop_requested():
        run_once(args)
        sleep_until_next(max(30, args.interval))
    log("收到停止指令，设备上报退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
