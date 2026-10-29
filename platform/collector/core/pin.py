from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from collector.core import ui

AIKEYBOARD_CANDIDATES = [
    Path(r"C:\wangyingbao\keyboard\AiKeyboard.exe"),
    Path(r"C:\wangyingbao\keyboard\AiKeyboard_for_win10\AiKeyboard.exe"),
    Path.cwd() / "keyboard" / "AiKeyboard.exe",
    Path.cwd().parent / "keyboard" / "AiKeyboard.exe",
]

KEY_NAMES = {
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "space": "Space",
    "backspace": "Backspace",
}

JS_SET_VALUE = """([selectors, value]) => {
    for (const sel of selectors) {
        let el = null;
        try { el = document.querySelector(sel); } catch (e) { continue; }
        if (!el) continue;
        el.focus();
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }
    return false;
}"""


class PinInjector:
    def __init__(self, pin: str, aikeyboard_path: str = "") -> None:
        self.pin = pin or ""
        self.aikeyboard_path = self._resolve_aikeyboard(aikeyboard_path)

    @staticmethod
    def _resolve_aikeyboard(explicit: str) -> str:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        env = os.getenv("AIKEYBOARD_PATH", "")
        if env:
            candidates.append(Path(env))
        candidates.extend(AIKEYBOARD_CANDIDATES)
        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate)
            except OSError:
                continue
        return shutil.which("AiKeyboard.exe") or ""

    def _ensure_english_layout(self) -> None:
        try:
            import ctypes

            layout = ctypes.windll.user32.GetKeyboardLayout(0) & 0xFFFF
            if layout in (0x0409, 0x0809):
                return
            import pyautogui

            pyautogui.press("shift")
            time.sleep(0.5)
        except Exception:
            return

    def activate_window(self, title_part: str = "Chrome") -> bool:
        try:
            import pygetwindow

            for window in pygetwindow.getWindowsWithTitle(title_part):
                if window.isMinimized:
                    window.restore()
                window.activate()
                time.sleep(0.5)
                return True
        except Exception:
            return False
        return False

    def aikeyboard(self) -> bool:
        if not self.pin or not self.aikeyboard_path:
            return False
        self._ensure_english_layout()
        try:
            proc = subprocess.run(
                [self.aikeyboard_path, "input", "-k", self.pin],
                capture_output=True,
                timeout=10,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def fill(self, scope: Any, selectors: list[str]) -> bool:
        if not self.pin:
            return False
        return ui.fill(scope, selectors, self.pin)

    def js_set(self, scope: Any, selectors: list[str]) -> bool:
        if not self.pin:
            return False
        try:
            return bool(scope.evaluate(JS_SET_VALUE, [selectors, self.pin]))
        except Exception:
            return False

    def pyautogui(self, click_point: tuple[int, int] | None = None) -> bool:
        if not self.pin:
            return False
        try:
            import pyautogui

            pyautogui.FAILSAFE = False
            if click_point:
                pyautogui.click(int(click_point[0]), int(click_point[1]))
                time.sleep(0.3)
            pyautogui.typewrite(self.pin, interval=0.05)
            return True
        except Exception:
            return False

    def press(self, key: str = "enter", times: int = 1) -> bool:
        for _ in range(times):
            if not self.send_key(key):
                try:
                    import pyautogui

                    pyautogui.FAILSAFE = False
                    pyautogui.press(key)
                except Exception:
                    return False
            time.sleep(0.3)
        return True

    def send_key(self, key: str) -> bool:
        """用 AiKeyboard 发送单个按键（如 Enter/Tab），失败返回 False"""
        if not self.aikeyboard_path:
            return False
        name = KEY_NAMES.get(key.lower(), key)
        try:
            proc = subprocess.run(
                [self.aikeyboard_path, "send", "-k", name],
                capture_output=True,
                timeout=5,
                cwd=str(Path(self.aikeyboard_path).parent),
            )
            return proc.returncode == 0
        except Exception:
            return False

    def clear(self) -> bool:
        try:
            import pyautogui

            pyautogui.FAILSAFE = False
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.2)
            pyautogui.press("backspace")
            time.sleep(0.2)
            return True
        except Exception:
            return False

    @staticmethod
    def has_window(title_keywords: tuple[str, ...] | list[str]) -> bool:
        try:
            import pygetwindow

            for window in pygetwindow.getAllWindows():
                title = window.title or ""
                if title and any(keyword in title for keyword in title_keywords):
                    return True
        except Exception:
            return False
        return False

    def click_ratio(self, x_ratio: float, y_ratio: float) -> bool:
        try:
            import pyautogui

            pyautogui.FAILSAFE = False
            width, height = pyautogui.size()
            pyautogui.click(int(width * x_ratio), int(height * y_ratio))
            return True
        except Exception:
            return False

    def inject(self, scope: Any, selectors: list[str], order: list[str] | None = None) -> bool:
        steps = order or ["aikeyboard", "fill", "js", "pyautogui"]
        for step in steps:
            if step == "aikeyboard" and self.aikeyboard():
                return True
            if step == "fill" and self.fill(scope, selectors):
                return True
            if step == "js" and self.js_set(scope, selectors):
                return True
            if step == "pyautogui" and self.pyautogui():
                return True
        return False
