from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from app.config import settings
from app.services.usb_robot import RobotDevice, bank_code_from_name, cabinet_from_ter_id
from collector.core.mb import MbController, MbError


class MbLocalError(RuntimeError):
    pass


BANK_DISPLAY_NAMES = {
    "icbc": "工商银行",
    "ccb": "建设银行",
    "abc": "农业银行",
    "boc": "中国银行",
    "bocom": "交通银行",
    "cmbc": "民生银行",
    "cmb": "招商银行",
    "citic": "中信银行",
    "cib": "兴业银行",
    "nbcb": "宁波银行",
    "njcb": "南京银行",
    "cgb": "广发银行",
    "shrcb": "上海农商银行",
    "bosc": "上海银行",
    "spdb": "浦发银行",
    "bod": "大连银行",
}


@dataclass
class LocalTerminal:
    ter_id: str
    cabinet: int = 0
    ip: str = ""
    mac: str = ""
    version: str = ""
    port_count: int = 0
    raw: dict = field(default_factory=dict, repr=False)


def _to_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _extract_terminals(payload) -> list[dict]:
    if isinstance(payload, list):
        if any(isinstance(item, dict) and "ter_id" in item for item in payload):
            return [item for item in payload if isinstance(item, dict) and "ter_id" in item]
        for item in payload:
            found = _extract_terminals(item)
            if found:
                return found
        return []
    if not isinstance(payload, dict):
        return []
    if "ter_id" in payload and any(key in payload for key in ("usb_port_manage", "robotarm_manage")):
        return [payload]
    for key in ("data", "response", "result", "list", "items"):
        if key in payload:
            found = _extract_terminals(payload[key])
            if found:
                return found
    for value in payload.values():
        found = _extract_terminals(value)
        if found:
            return found
    return []


def _port_status(port: dict) -> str:
    if str(port.get("dev_exists") or "") != "1":
        return "empty"
    if str(port.get("usb_share") or "") == "1" or str(port.get("client_ip") or "").strip():
        return "busy"
    return "online"


def _local_device_name(source: RobotDevice) -> str:
    raw = source.raw or {}
    port_name = str(raw.get("port_name") or "").strip()
    if port_name:
        return port_name
    label = BANK_DISPLAY_NAMES.get(source.bank_code) or source.bank_name or "UKey"
    return f"{label} · {source.port}号口"


def parse_terminal(ter: dict) -> tuple[LocalTerminal, list[RobotDevice]]:
    ter_id = str(ter.get("ter_id") or "").strip()
    if not ter_id:
        raise MbLocalError("本地 MB 返回的机柜数据缺少 ter_id")
    eth = ter.get("eth_manage") or {}
    port_manage = ter.get("usb_port_manage") or {}
    ports = port_manage.get("usb_port_info") or []
    robot_ports = {
        str(item.get("port_num")): item
        for item in ((ter.get("robotarm_manage") or {}).get("robotarm_info") or [])
        if isinstance(item, dict)
    }
    terminal = LocalTerminal(
        ter_id=ter_id,
        cabinet=cabinet_from_ter_id(ter_id),
        ip=str(eth.get("ip_address") or ""),
        mac=str(eth.get("ter_mac") or ""),
        version=str(ter.get("ter_version_detail") or ter.get("ter_version") or ""),
        port_count=_to_int(port_manage.get("usb_port_num")) or len(ports),
        raw=ter,
    )

    devices: list[RobotDevice] = []
    for port in ports:
        if not isinstance(port, dict):
            continue
        port_no = _to_int(port.get("usb_port"))
        if port_no <= 0:
            continue
        bank_info = port.get("bank_infos") or {}
        bank_name = str(bank_info.get("bank_type") or "").strip()
        bank_code = bank_code_from_name(bank_name)
        robot = robot_ports.get(str(port_no), {})
        raw = {
            **port,
            "ter_id": ter_id,
            "ter_version": terminal.version,
            "ter_ip": terminal.ip,
            "ter_mac": terminal.mac,
            "robotarm_status": robot.get("robotarm_status", ""),
            "robotarm_with_dev_ports": robot.get("robotarm_with_dev_ports", ""),
            "source": "local",
        }
        devices.append(
            RobotDevice(
                cabinet=cabinet_from_ter_id(ter_id),
                port=port_no,
                bank_name=bank_name,
                bank_code=bank_code,
                org_name=str(port.get("port_name") or "").strip(),
                status=_port_status(port),
                cert_subject=f"{str(bank_info.get('product') or '').strip()} {str(bank_info.get('vendor') or '').strip()}".strip(),
                cert_expires_at=None,
                last_heartbeat=dt.datetime.now(),
                external_id=str(port.get("usb_pid") or "").strip(),
                raw=raw,
            )
        )
    return terminal, devices


def read_terminal(ter_id: str, exe: str = "") -> tuple[LocalTerminal, list[RobotDevice]]:
    try:
        payload = MbController(exe=exe or settings.mb_exe, ter_id=ter_id).status()
    except MbError as exc:
        raise MbLocalError(f"{ter_id}: {exc}") from exc
    terminals = _extract_terminals(payload)
    if not terminals:
        raise MbLocalError(f"{ter_id}: 本地 MB 返回中没有机柜数据")
    ter = next((item for item in terminals if str(item.get("ter_id")) == ter_id), terminals[0])
    return parse_terminal(ter)


def read_all(ter_ids: list[str], exe: str = "") -> tuple[list[LocalTerminal], list[RobotDevice], list[str]]:
    terminals: list[LocalTerminal] = []
    devices: list[RobotDevice] = []
    errors: list[str] = []
    for ter_id in ter_ids:
        try:
            terminal, items = read_terminal(ter_id, exe=exe)
        except MbLocalError as exc:
            errors.append(str(exc))
            continue
        terminals.append(terminal)
        devices.extend(items)
    if not terminals:
        raise MbLocalError("；".join(errors) or "没有可读取的本地 MB 机柜")
    return terminals, devices, errors


def local_device_name(source: RobotDevice) -> str:
    return _local_device_name(source)
