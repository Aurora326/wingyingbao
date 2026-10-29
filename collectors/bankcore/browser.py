from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

DEFAULT_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=DownloadBubble,DownloadBubbleV2",
    "--safebrowsing-disable-download-protection",
    "--safebrowsing-disable-extension-blacklist",
    "--disable-download-notification",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
]

EXTENSION_DEFAULT_ARGS = [
    "--enable-automation",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-component-update",
]

MAXIMIZE_JS = """() => {
    try {
        window.moveTo(0, 0);
        window.resizeTo(screen.availWidth, screen.availHeight);
    } catch (e) {}
}"""


def find_chrome(explicit: str = "") -> str:
    if explicit and Path(explicit).exists():
        return explicit
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("chrome.exe") or shutil.which("msedge.exe")
    if found:
        return found
    raise FileNotFoundError("未找到 Chrome 或 Edge，请在配置中指定 chrome_path")


def extract_crx(crx_path: str, dest_dir: Path) -> Path | None:
    source = Path(crx_path)
    if not source.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / source.stem
    if target.exists() and any(target.iterdir()):
        return target
    with open(source, "rb") as handle:
        header = handle.read(16)
        if header[:4] != b"Cr24":
            return None
        version = int.from_bytes(header[4:8], "little")
        if version == 3:
            header_size = int.from_bytes(header[8:12], "little")
            offset = 12 + header_size
        elif version == 2:
            public_key_len = int.from_bytes(header[8:12], "little")
            signature_len = int.from_bytes(header[12:16], "little")
            offset = 16 + public_key_len + signature_len
        else:
            return None
        handle.seek(offset)
        payload = dest_dir / f"{source.stem}.zip"
        payload.write_bytes(handle.read())
    try:
        with zipfile.ZipFile(payload) as archive:
            archive.extractall(target)
    except zipfile.BadZipFile:
        return None
    finally:
        payload.unlink(missing_ok=True)
    return target


def extension_id(extension_dir: Path) -> str:
    metadata = Path(extension_dir) / "_metadata" / "verified_contents.json"
    if not metadata.exists():
        return ""
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        decoded = json.loads(base64.b64decode(payload.get("payload", "")))
        return str(decoded.get("item_id", ""))
    except Exception:
        return ""


def extension_installed(user_data_dir: Path, extension_dir: Path) -> bool:
    identifier = extension_id(extension_dir)
    if not identifier:
        return False
    return (Path(user_data_dir) / "Default" / "Extensions" / identifier).exists()


def kill_profile_chrome(profile_dir: Path) -> None:
    """启动前清理占用同一 profile 的残留 Chrome，避免 Chromium 单实例锁导致卡住"""
    marker = str(Path(profile_dir)).replace("'", "''")
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
        )
    except Exception:
        return


class BrowserSession:
    def __init__(
        self,
        user_data_dir: Path,
        downloads_dir: Path,
        chrome_path: str = "",
        extension_path: str = "",
        headless: bool = False,
        page_timeout: int = 30,
        window: tuple[int, int] = (1380, 900),
        maximize: bool = True,
        extra_args: list[str] | None = None,
        logger: logging.Logger | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self.user_data_dir = Path(user_data_dir)
        self.downloads_dir = Path(downloads_dir)
        self.chrome_path = chrome_path
        self.extension_path = extension_path
        self.headless = headless
        self.page_timeout = page_timeout
        self.window = window
        self.maximize = maximize
        self.extra_args = extra_args or []
        self.logger = logger
        self.base_dir = Path(base_dir) if base_dir else self.user_data_dir.parent
        self._playwright = None
        self.context = None
        self.page = None

    def _log(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _extension_args(self) -> tuple[list[str], list[str]]:
        # Playwright 默认会加 --disable-extensions，会连 profile 里已安装的 UKey 控件一起禁用
        ignore_args = ["--disable-extensions"]
        if not self.extension_path:
            return [], ignore_args
        source = Path(self.extension_path)
        extension_dir = source if source.is_dir() else extract_crx(self.extension_path, self.base_dir / "extensions")
        if extension_dir is None:
            self._log(f"扩展解包失败或文件不存在: {self.extension_path}")
            return [], ignore_args
        if self.headless:
            self._log("headless=true 时安全控件扩展不可用，请改用有头模式")
        if extension_installed(self.user_data_dir, extension_dir):
            self._log("扩展已在 profile 中，跳过重复加载")
            return [], ignore_args + list(EXTENSION_DEFAULT_ARGS)
        return [
            f"--disable-extensions-except={extension_dir}",
            f"--load-extension={extension_dir}",
        ], ignore_args + list(EXTENSION_DEFAULT_ARGS)

    def start(self):
        from playwright.sync_api import sync_playwright

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._log(f"清理占用 profile 的残留 Chrome: {self.user_data_dir}")
        kill_profile_chrome(self.user_data_dir)
        extension_args, ignore_args = self._extension_args()
        args = (
            list(DEFAULT_ARGS)
            + [f"--window-size={self.window[0]},{self.window[1]}"]
            + extension_args
            + self.extra_args
        )
        launch_kwargs = {
            "user_data_dir": str(self.user_data_dir),
            "executable_path": find_chrome(self.chrome_path),
            "headless": self.headless,
            "accept_downloads": True,
            "downloads_path": str(self.downloads_dir),
            "no_viewport": True,
            "args": args,
        }
        if ignore_args:
            launch_kwargs["ignore_default_args"] = ignore_args
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(self.page_timeout * 1000)
        try:
            if self.maximize:
                self.page.evaluate(MAXIMIZE_JS)
            else:
                # 固定窗口尺寸，保证按比例计算的兜底坐标可复现
                self.page.evaluate(
                    "(size) => { window.moveTo(0, 0); window.resizeTo(size[0], size[1]); }",
                    [self.window[0], self.window[1]],
                )
        except Exception:
            pass
        return self.page

    def close(self) -> None:
        for closer in (
            lambda: self.context and self.context.close(),
            lambda: self._playwright and self._playwright.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self.context = None
        self._playwright = None
        self.page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
