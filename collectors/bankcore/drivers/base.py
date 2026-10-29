from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from bankcore.browser import BrowserSession
from bankcore.gf3 import Gf3Controller
from bankcore.mb import MbController
from bankcore.pin import PinInjector
from bankcore.profile import BankProfile
from bankcore.result import CollectResult, ErrorCode


@dataclass
class RunContext:
    task: dict
    profile: BankProfile
    work_dir: Path
    downloads_dir: Path
    archive_root: Path
    logger: logging.Logger
    profile_dir: Path | None = None
    browser: BrowserSession | None = None
    gf3: Gf3Controller | None = None
    mb: MbController | None = None
    pin: PinInjector | None = None

    @property
    def credentials(self) -> dict:
        return self.task.get("credentials", {}) or {}

    @property
    def options(self) -> dict:
        return self.task.get("options", {}) or {}

    def log(self, message: str, level: int = logging.INFO) -> None:
        self.logger.log(level, message)


class BankDriver(ABC):
    bank_code: str = ""
    display_name: str = ""
    needs_browser: bool = True

    def __init__(self, profile: BankProfile) -> None:
        self.profile = profile

    def sel(self, key: str) -> list[str]:
        return self.profile.selectors(key)

    def dlg(self, key: str) -> list[str]:
        return self.profile.dialogs(key)

    def url(self, key: str, default: str = "") -> str:
        return self.profile.url(key, default)

    def option(self, key: str, default=None):
        return self.profile.get(f"options.{key}", default)

    def ensure_profile(self, ctx: RunContext, profile_dir: Path) -> None:
        source = self.option("profile_source", "")
        if not source:
            return
        source_path = Path(source)
        if not source_path.exists():
            ctx.log(f"profile_source 不存在，忽略: {source_path}", logging.WARNING)
            return
        if (profile_dir / "Default").exists():
            return
        ctx.log(f"首次运行，复制已有 Chrome profile: {source_path} -> {profile_dir}")
        profile_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_path, profile_dir, dirs_exist_ok=True)

    def start_browser(self, ctx: RunContext) -> BrowserSession:
        profile_dir = Path(ctx.profile_dir) if ctx.profile_dir else ctx.work_dir / "chrome_profile"
        self.ensure_profile(ctx, profile_dir)
        session = BrowserSession(
            user_data_dir=profile_dir,
            downloads_dir=ctx.downloads_dir,
            chrome_path=self.option("chrome_path", ""),
            extension_path=self.option("extension_path", ""),
            headless=bool(self.option("headless", False)),
            page_timeout=int(self.option("page_timeout", 30)),
            window=tuple(self.option("window", [1380, 900])),
            maximize=bool(self.option("maximize", True)),
            extra_args=list(self.option("extra_args", []) or []),
            logger=ctx.logger,
            base_dir=profile_dir.parent,
        )
        session.start()
        ctx.browser = session
        return session

    def prepare(self, ctx: RunContext) -> None:
        port = int(ctx.task.get("gf3_port") or 0)
        cabinet = int(ctx.task.get("gf3_cabinet") or 1)
        backend = str(ctx.task.get("device_backend") or "gf3").lower()
        if backend == "gf3" and port:
            controller = Gf3Controller(exe=ctx.task.get("gf3_exe", "gf3"), cabinet=cabinet)
            controller.isolate(port)
            ctx.gf3 = controller
        elif backend == "mb" and port:
            ter_id = str(ctx.task.get("ter_id") or "").strip()
            if not ter_id:
                raise RuntimeError("MB 设备缺少 ter_id，请先执行「从 USB 机器人同步」或手动补全设备信息")
            controller = MbController(
                exe=ctx.task.get("mb_exe", ""),
                ter_id=ter_id,
                port=port,
                user=str(ctx.task.get("mb_user") or ""),
            )
            ctx.log(f"MB 插入 UKey: ter={controller.ter_id} port={port} user={controller.user}")
            if not controller.connect():
                raise RuntimeError(f"MB 机柜插入失败: ter={controller.ter_id} port={port}")
            wait_seconds = int(self.option("mb_ready_wait", 10))
            ctx.log(f"等待 {wait_seconds}s 让系统识别 UKey")
            controller.wait_ready(wait_seconds)
            ctx.mb = controller
        ctx.pin = PinInjector(ctx.credentials.get("pin", ""), self.option("aikeyboard_path", ""))

    @abstractmethod
    def login(self, ctx: RunContext, session: BrowserSession) -> bool: ...

    @abstractmethod
    def collect_statements(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult: ...

    @abstractmethod
    def collect_receipts(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult: ...

    def collect_reconciliation(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult:
        return CollectResult(error_code=ErrorCode.NOT_IMPLEMENTED, message="该银行暂不支持电子对账单采集")

    def healthcheck(self, ctx: RunContext, session: BrowserSession) -> bool:
        return True

    def teardown(self, ctx: RunContext) -> None:
        if ctx.browser:
            ctx.browser.close()
            ctx.browser = None
        if ctx.mb:
            try:
                ctx.mb.disconnect()
                ctx.log("MB 机柜已拔出 UKey")
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"MB 拔出失败: {exc}", logging.WARNING)
            ctx.mb = None
        if ctx.gf3:
            port = int(ctx.task.get("gf3_port") or 0)
            if port:
                try:
                    ctx.gf3.disconnect(port)
                except Exception:
                    pass


@dataclass
class DriverInfo:
    bank_code: str
    display_name: str
    code: str
    driver: type[BankDriver] = field(repr=False, default=None)  # type: ignore[assignment]
