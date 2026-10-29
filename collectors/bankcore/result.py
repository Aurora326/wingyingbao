from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class ErrorCode:
    NO_DATA = "NO_DATA"
    LOGIN_FAILED = "LOGIN_FAILED"
    CERT_EXPIRED = "CERT_EXPIRED"
    SELECTOR_NOT_FOUND = "SELECTOR_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    DEVICE_BUSY = "DEVICE_BUSY"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    UNKNOWN = "UNKNOWN"


RETRYABLE = {ErrorCode.TIMEOUT, ErrorCode.DOWNLOAD_FAILED, ErrorCode.DEVICE_BUSY}
FATAL_MANUAL = {ErrorCode.LOGIN_FAILED, ErrorCode.CERT_EXPIRED, ErrorCode.SELECTOR_NOT_FOUND}


@dataclass
class CollectResult:
    files: list[Path] = field(default_factory=list)
    no_data: bool = False
    error_code: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return not self.error_code

    @property
    def retryable(self) -> bool:
        return self.error_code in RETRYABLE

    def to_dict(self) -> dict:
        return {
            "files": [str(p) for p in self.files],
            "no_data": self.no_data,
            "error_code": self.error_code,
            "message": self.message,
            "ok": self.ok,
        }
