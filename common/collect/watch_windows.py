"""实时捕获证书/密码弹窗（Win32 原生枚举，开销很小）。

用法：python watch_windows.py [秒数]
弹窗出现/消失时打印标题、类名、矩形和子控件文字。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
KEYWORDS = ("证书", "密码", "U盾", "CERT", "ICBC")

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _text(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def _class(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _rect(hwnd):
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def _children(hwnd) -> list[tuple[str, str, tuple]]:
    items: list[tuple[str, str, tuple]] = []

    @WNDENUMPROC
    def callback(child, _):
        title = _text(child)
        if title:
            items.append((title, _class(child), _rect(child)))
        return True

    user32.EnumChildWindows(hwnd, callback, 0)
    return items


def snapshot() -> dict:
    result: dict = {}

    @WNDENUMPROC
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _text(hwnd)
        if not title:
            return True
        if any(keyword in title for keyword in KEYWORDS):
            result[title] = (_class(hwnd), _rect(hwnd), tuple(_children(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    return result


def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    log_path = sys.argv[2] if len(sys.argv) > 2 else ""
    handle = open(log_path, "a", encoding="utf-8") if log_path else None

    def emit(line: str) -> None:
        print(line, flush=True)
        if handle:
            handle.write(line + "\n")
            handle.flush()

    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        current = snapshot()
        if current != last:
            emit(time.strftime("%H:%M:%S") + " " + "=" * 40)
            if not current:
                emit("  (无匹配窗口)")
            for title, (cls, rect, children) in current.items():
                emit(f"  {title!r} class={cls} rect={rect}")
                for child_title, child_class, child_rect in children[:20]:
                    emit(f"     child {child_title!r} class={child_class} rect={child_rect}")
            last = current
        time.sleep(1)
    if handle:
        handle.close()


if __name__ == "__main__":
    main()
