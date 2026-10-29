from __future__ import annotations

import base64
import datetime as dt
import gzip
import json
import re
from dataclasses import dataclass, field

import httpx

from app.config import settings

DEVICE_LIST_PATH = "/dev-api/userequ/new_get_usertick_usb_escalation"

BANK_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("icbc", ("工商银行", "ICBC")),
    ("ccb", ("建设银行", "CCB")),
    ("abc", ("农业银行", "ABC")),
    ("boc", ("中国银行", "BOCNET", "BOC")),
    ("bocom", ("交通银行", "BOCOM")),
    ("cmbc", ("民生银行", "CMBC")),
    ("cmb", ("招商银行", "CMB")),
    ("citic", ("中信银行", "CNCB")),
    ("cib", ("兴业银行", "兴业网盾")),
    ("nbcb", ("宁波银行",)),
    ("njcb", ("南京银行",)),
    ("cgb", ("广发银行",)),
    ("shrcb", ("上海农商", "SHRCB")),
    ("bosc", ("上海银行",)),
    ("spdb", ("浦发银行", "SPDB")),
    ("bod", ("大连银行", "BANKOFDALIAN")),
]

GENERIC_BANK_CODE = "generic"


class UsbRobotError(RuntimeError):
    pass


@dataclass
class RobotDevice:
    cabinet: int
    port: int
    bank_name: str
    bank_code: str
    org_name: str
    status: str
    cert_subject: str
    cert_expires_at: dt.date | None
    last_heartbeat: dt.datetime | None
    external_id: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def display_name(self) -> str:
        base = self.org_name or self.bank_name or f"端口 {self.port}"
        return f"{base[:60]}"[:64]


def bank_code_from_name(name: str) -> str:
    text = (name or "").upper()
    for code, keywords in BANK_RULES:
        for keyword in keywords:
            if keyword.upper() in text:
                return code
    if re.search(r"[\u4e00-\u9fff]", text):
        return GENERIC_BANK_CODE
    match = re.search(r"[A-Z_]{2,}", text)
    if match:
        return match.group(0).lower().replace("_", "")
    return GENERIC_BANK_CODE


def cabinet_from_ter_id(ter_id: str) -> int:
    digits = re.sub(r"\D", "", str(ter_id or ""))
    if not digits:
        return 0
    return int(digits[-4:])


def _to_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _parse_date(value) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(value) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _device_status(row: dict) -> str:
    if str(row.get("offline")) == "0":
        return "offline"
    if str(row.get("robotarm_with_dev_ports")) == "0":
        return "empty"
    return "online"


def parse_device(row: dict) -> RobotDevice:
    bank_infos = row.get("bank_infos") or {}
    bank_name = str(bank_infos.get("bank_type") or "").strip()
    product = str(bank_infos.get("product") or "").strip()
    vendor = str(bank_infos.get("vendor") or "").strip()
    port = _to_int(row.get("port_num") or row.get("robotarm_with_dev_ports") or row.get("tax_auth"))
    return RobotDevice(
        cabinet=cabinet_from_ter_id(row.get("ter_id")),
        port=port,
        bank_name=bank_name,
        bank_code=bank_code_from_name(bank_name),
        org_name=str(row.get("alias_name") or "").strip(),
        status=_device_status(row),
        cert_subject=f"{product} {vendor}".strip()[:256],
        cert_expires_at=_parse_date(row.get("end_time")),
        last_heartbeat=_parse_datetime(row.get("record_time")),
        external_id=str(row.get("pid") or row.get("remark1") or ""),
        raw=row,
    )


class UsbRobotClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        user_no: str | None = None,
        point_no: str | None = None,
        passwd: str | None = None,
        web_ip: str | None = None,
        list_type: str | None = None,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.usb_robot_base_url).rstrip("/")
        self.token = token if token is not None else settings.usb_robot_token
        self.user_no = user_no if user_no is not None else settings.usb_robot_user_no
        self.point_no = point_no if point_no is not None else settings.usb_robot_point_no
        self.passwd = passwd if passwd is not None else settings.usb_robot_passwd
        self.web_ip = web_ip if web_ip is not None else settings.usb_robot_web_ip
        self.list_type = list_type if list_type is not None else settings.usb_robot_list_type
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_settings(cls) -> "UsbRobotClient":
        return cls()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.user_no and self.point_no)

    @staticmethod
    def encode_payload(payload: dict) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return base64.b64encode(gzip.compress(raw)).decode("ascii")

    @staticmethod
    def decode_payload(body: str) -> dict:
        return json.loads(gzip.decompress(base64.b64decode(body)).decode("utf-8"))

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Encoding": "gzip",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/new/equipment",
            "hybrid": "1",
            "userno": self.user_no,
            "ticketedpointno": self.point_no,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["Cookie"] = f"Admin-Token={self.token}"
        if self.passwd:
            headers["passwd"] = self.passwd
        if self.web_ip:
            headers["webip"] = self.web_ip
        return headers

    def _payload(self) -> dict:
        return {
            "ticketed_point_no": self.point_no,
            "user_no": self.user_no,
            "listType": self.list_type,
            "isUse": "0",
            "type": "0",
            "page": 1,
            "limit": 200,
        }

    def list_devices(self) -> list[RobotDevice]:
        if not self.configured:
            raise UsbRobotError("未配置 USB 机器人接口（需要 token / user_no / ticketed_point_no）")
        body = self.encode_payload(self._payload())
        with httpx.Client(timeout=self.timeout, transport=self.transport, headers=self._headers()) as http:
            response = http.post(f"{self.base_url}{DEVICE_LIST_PATH}", content=body)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise UsbRobotError("接口返回内容不是 JSON") from exc
        if str(data.get("result")) != "s":
            detail = (data.get("response") or {}).get("errinfo") or "未知错误"
            raise UsbRobotError(f"USB 机器人返回失败: {detail}")
        rows = (data.get("response") or {}).get("usbdata") or []
        devices = [parse_device(row) for row in rows if isinstance(row, dict)]
        return [device for device in devices if device.port > 0]
