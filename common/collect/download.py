from __future__ import annotations

import logging
import re
import time
from pathlib import Path

KNOWN_SUFFIXES = {".xlsx", ".xls", ".csv", ".pdf", ".zip", ".rar", ".7z"}
HTML_SUFFIXES = {".html", ".htm"}
PARTIAL_SUFFIXES = {".crdownload", ".tmp", ".part", ".partial"}
MIN_SIZE = 1000
MIN_SIZE_OTHER = 200
MIN_SIZE_GUID = 100
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def is_html(path: Path) -> bool:
    return Path(path).suffix.lower() in HTML_SUFFIXES


def is_candidate(path: Path, size: int) -> bool:
    suffix = path.suffix.lower()
    if suffix in PARTIAL_SUFFIXES:
        return False
    if suffix in KNOWN_SUFFIXES:
        return size >= MIN_SIZE
    if suffix in HTML_SUFFIXES:
        return size >= MIN_SIZE_OTHER
    if not suffix and GUID_RE.match(path.name):
        return size >= MIN_SIZE_GUID
    return size >= MIN_SIZE


def snapshot(dirs: list[Path]) -> dict[str, float]:
    seen: dict[str, float] = {}
    for directory in dirs:
        if not directory.exists():
            continue
        for path in directory.iterdir():
            try:
                if path.is_file():
                    seen[str(path)] = path.stat().st_size
            except OSError:
                continue
    return seen


def newest_since(dirs: list[Path], since: float, exclude: set[str] | None = None) -> Path | None:
    exclude = exclude or set()
    found: list[Path] = []
    for directory in dirs:
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if str(path) in exclude or not path.is_file():
                continue
            if path.suffix.lower() in PARTIAL_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime >= since and is_candidate(path, stat.st_size):
                found.append(path)
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


class DownloadWatcher:
    def __init__(
        self,
        dirs: list[Path],
        timeout: int = 90,
        settle_checks: int = 3,
        interval: float = 0.5,
        logger: logging.Logger | None = None,
    ) -> None:
        self.dirs = [Path(d) for d in dirs]
        self.timeout = timeout
        self.settle_checks = settle_checks
        self.interval = interval
        self.logger = logger

    def _log(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def wait(self, before: dict[str, float]) -> Path | None:
        deadline = time.time() + self.timeout
        stable: dict[str, int] = {}
        last_size: dict[str, int] = {}
        while time.time() < deadline:
            candidates: list[Path] = []
            for directory in self.dirs:
                if not directory.exists():
                    continue
                for path in directory.iterdir():
                    key = str(path)
                    if key in before or not path.is_file():
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if not is_candidate(path, size):
                        continue
                    if last_size.get(key) == size:
                        stable[key] = stable.get(key, 0) + 1
                    else:
                        stable[key] = 0
                    last_size[key] = size
                    if stable[key] >= self.settle_checks:
                        candidates.append(path)
            if candidates:
                chosen = max(candidates, key=lambda p: p.stat().st_mtime)
                self._log(f"检测到下载文件: {chosen.name} ({chosen.stat().st_size} 字节)")
                return chosen
            time.sleep(self.interval)
        listing = []
        for directory in self.dirs:
            if not directory.exists():
                continue
            for path in directory.iterdir():
                try:
                    if path.is_file():
                        listing.append(f"{path.name}({path.stat().st_size})")
                except OSError:
                    continue
        self._log(f"等待下载超时 {self.timeout}s，目录内容: {', '.join(listing[:20]) or '空'}")
        return None
