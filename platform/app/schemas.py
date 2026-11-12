from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class TaskRetry(BaseModel):
    agent_code: str | None = None


class AgentCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=64)


class DeviceCreate(BaseModel):
    name: str
    bank_code: str
    backend: str = Field(default="gf3", pattern="^(gf3|mb)$")
    agent_code: str = "local"
    gf3_cabinet: int = 1
    gf3_port: int = 1
    external_id: str = ""
    ter_id: str = ""
    mb_user: str = ""
    cert_expires_at: dt.date | None = None


class AccountCreate(BaseModel):
    bank_code: str
    account_no: str
    account_name: str = ""
    enterprise_id: int | None = None
    ukey_device_id: int | None = None
    collect_enabled: bool = False
    collect_cron: str = "0 8,18 * * *"
    collect_mode: str = Field(default="both", pattern="^(statements|receipts|both)$")
    days_back: int = 30
    username: str = ""
    password: str = ""
    pin: str = ""


class CredentialUpdate(BaseModel):
    username: str = ""
    password: str = ""
    pin: str = ""


class AccountDeviceUpdate(BaseModel):
    ukey_device_id: int | None = None


class AccountUpdate(BaseModel):
    account_name: str | None = None
    collect_cron: str | None = None
    collect_mode: str | None = Field(default=None, pattern="^(statements|receipts|both)$")
    days_back: int | None = Field(default=None, ge=1, le=365)
    ukey_device_id: int | None = None
    username: str | None = None
    password: str | None = None
    pin: str | None = None


class CollectRequest(BaseModel):
    type: str = Field(default="statements", pattern="^(statements|receipts|both)$")
    start_date: dt.date | None = None
    end_date: dt.date | None = None


class ReconCreate(BaseModel):
    period_start: dt.date
    period_end: dt.date
    name: str = ""


class InternalDocCreate(BaseModel):
    type: str = "other"
    doc_no: str = ""
    amount: float
    direction: str = "credit"
    counterparty: str = ""
    doc_date: dt.date


class ReconAction(BaseModel):
    internal_doc_id: int | None = None


class AgentDeviceItem(BaseModel):
    port: int
    bank_code: str = ""
    bank_name: str = ""
    status: str = "online"
    external_id: str = ""
    port_name: str = ""
    usb_share: int = 0
    client_ip: str = ""
    port_power: int = 0
    user: str = ""


class AgentDeviceReport(BaseModel):
    agent_code: str
    agent_name: str = ""
    hostname: str = ""
    ip: str = ""
    version: str = ""
    ter_id: str
    ter_ip: str = ""
    port_count: int = 0
    devices: list[AgentDeviceItem] = Field(default_factory=list)


class AgentTaskClaim(BaseModel):
    agent_code: str


class AgentTaskFile(BaseModel):
    name: str
    content_b64: str


class AgentTaskResult(BaseModel):
    agent_code: str
    attempt: int
    exit_code: int | None = None
    duration_ms: int = 0
    log_text: str = ""
    result: dict = Field(default_factory=dict)
    files: list[AgentTaskFile] = Field(default_factory=list)