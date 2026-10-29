from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _window_text(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def find_hwnd(title_keywords):
    """快速查找可见顶层窗口（Win32 原生枚举，毫秒级）"""
    found = []

    @_WNDENUMPROC
    def callback(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        if _title_matches(_window_text(hwnd), title_keywords):
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(callback, 0)
    return found[0] if found else None


def _title_matches(title: str, keywords) -> bool:
    return bool(title) and any(keyword in title for keyword in keywords)


def _desktop(backend: str):
    from pywinauto import Desktop

    return Desktop(backend=backend)


def find_dialog(title_keywords, timeout: float = 0.0, interval: float = 0.5):
    """查找标题包含任一关键字的顶层窗口，返回 (窗口, backend) 或 (None, None)"""
    deadline = time.time() + timeout
    while True:
        for backend in ("uia", "win32"):
            try:
                for window in _desktop(backend).windows():
                    try:
                        if not window.is_visible():
                            continue
                        title = window.window_text()
                    except Exception:
                        continue
                    if _title_matches(title, title_keywords):
                        return window, backend
            except Exception:
                continue
        if time.time() >= deadline:
            return None, None
        time.sleep(interval)


def _hwnd_rect(hwnd):
    rect = wt.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


def _hwnd_point(hwnd, x_ratio: float, y_ratio: float):
    rect = _hwnd_rect(hwnd)
    if rect is None:
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return int(rect.left + width * x_ratio), int(rect.top + height * y_ratio)


def _focus_hwnd(hwnd) -> None:
    try:
        _user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _chrome_hwnds() -> list:
    """快速枚举 Chrome 顶层窗口句柄（不依赖标题文字）"""
    handles = []

    @_WNDENUMPROC
    def callback(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buffer, 256)
        if buffer.value == "Chrome_WidgetWin_1":
            handles.append(hwnd)
        return True

    _user32.EnumWindows(callback, 0)
    return handles


def find_chrome_dialog(title_keywords):
    """在 Chrome 窗口的 UIA 子树里查找浏览器内置对话框（如「选择证书」）"""
    try:
        desktop = _desktop("uia")
    except Exception:
        return None
    fallback = None
    for hwnd in _chrome_hwnds():
        try:
            window = desktop.window(handle=hwnd)
            children = window.descendants()
        except Exception:
            continue
        for child in children:
            try:
                name = (child.window_text() or "").strip()
                control_type = str(getattr(child.element_info, "control_type", ""))
            except Exception:
                continue
            if not _title_matches(name, title_keywords):
                continue
            if control_type in ("Window", "Dialog"):
                return child
            if control_type == "Pane" and fallback is None:
                fallback = child
    return fallback


def click_chrome_dialog_button(title_keywords, button_texts=("确定", "确认", "OK"), timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while True:
        dialog = find_chrome_dialog(title_keywords)
        if dialog is not None:
            wanted = tuple(button_texts)
            try:
                for child in dialog.descendants():
                    try:
                        text = (child.window_text() or "").strip()
                        control_type = str(getattr(child.element_info, "control_type", ""))
                    except Exception:
                        continue
                    if text in wanted and control_type == "Button":
                        try:
                            child.invoke()
                        except Exception:
                            child.click_input()
                        return True
            except Exception:
                pass
        if time.time() >= deadline:
            return False
        time.sleep(0.3)


def dialog_visible(title_keywords) -> bool:
    if find_hwnd(title_keywords) is not None:
        return True
    return find_chrome_dialog(title_keywords) is not None


def list_windows(keywords=()) -> list[str]:
    """列出可见顶层窗口标题（可按关键字过滤），用于排查弹窗"""
    titles: list[str] = []
    for backend in ("uia", "win32"):
        try:
            for window in _desktop(backend).windows():
                try:
                    if not window.is_visible():
                        continue
                    title = window.window_text()
                except Exception:
                    continue
                if not title:
                    continue
                if keywords and not _title_matches(title, keywords):
                    continue
                if title not in titles:
                    titles.append(title)
        except Exception:
            continue
    return titles


def describe_window(title_keywords) -> dict:
    """返回匹配窗口的按钮/输入框信息，用于排查 UI 自动化是否可见"""
    window, backend = find_dialog(title_keywords, timeout=0.5)
    if window is None:
        return {}
    info = {"title": "", "backend": backend, "buttons": [], "edits": 0}
    try:
        info["title"] = window.window_text()
    except Exception:
        pass
    try:
        info["buttons"] = [button.window_text() for button in window.descendants(control_type="Button")]
    except Exception:
        pass
    try:
        info["edits"] = len(window.descendants(control_type="Edit"))
    except Exception:
        pass
    return info


def _invoke(child, backend: str) -> bool:
    try:
        if backend == "uia":
            try:
                child.invoke()
                return True
            except Exception:
                pass
        child.click()
        return True
    except Exception:
        try:
            child.click_input()
            return True
        except Exception:
            return False


def _find_child_by_text(window, texts):
    """按控件文字查找子控件（不依赖 control_type，兼容 #32770 自定义控件）"""
    try:
        children = window.descendants()
    except Exception:
        return None
    wanted = {text.strip() for text in texts}
    for child in children:
        try:
            text = (child.window_text() or "").strip()
        except Exception:
            continue
        if text and text in wanted:
            return child
    return None


def _click(window, backend: str, texts) -> bool:
    for text in texts:
        try:
            child = window.child_window(title=text)
            if child.exists(timeout=1):
                if _invoke(child, backend):
                    return True
        except Exception:
            continue
    child = _find_child_by_text(window, texts)
    if child is not None:
        return _invoke(child, backend)
    return False


def click_button(title_keywords, button_texts=("确定", "确认", "OK"), timeout: float = 10.0) -> bool:
    window, backend = find_dialog(title_keywords, timeout=timeout)
    if window is None:
        return False
    return _click(window, backend, tuple(button_texts))


def _find_edit(window, backend: str):
    try:
        edits = window.descendants(control_type="Edit")
        if edits:
            return edits[0]
    except Exception:
        pass
    try:
        edits = window.descendants(class_name="Edit")
        if edits:
            return edits[0]
    except Exception:
        pass
    try:
        for child in window.descendants():
            try:
                class_name = (child.class_name() or "").lower()
                control_type = str(getattr(child.element_info, "control_type", "") or "")
            except Exception:
                continue
            if "edit" in class_name or control_type.lower() == "edit":
                return child
    except Exception:
        pass
    return None


def focus_edit(title_keywords, timeout: float = 5.0) -> bool:
    """把焦点放到对话框里的输入框。比按坐标点击可靠，避免密码输进错误控件。"""
    window, backend = find_dialog(title_keywords, timeout=timeout)
    if window is None:
        return False
    edit = _find_edit(window, backend)
    if edit is None:
        return False
    try:
        edit.set_focus()
        return True
    except Exception:
        try:
            edit.click_input()
            return True
        except Exception:
            return False


def window_process_name(hwnd) -> str:
    """返回窗口所属进程的可执行文件路径，用于区分是不是 U盾中间件的弹窗。"""
    if not hwnd:
        return ""
    pid = wt.DWORD()
    try:
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except Exception:
        return ""
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(handle)
    return ""


def read_dialog_text(title_keywords, timeout: float = 1.0) -> str:
    """读取对话框正文，用于识别「密码输入有误」这类系统提示。"""
    window, _ = find_dialog(title_keywords, timeout=timeout)
    if window is None:
        return ""
    try:
        return (window.window_text() or "") + " " + " ".join(
            (child.window_text() or "") for child in window.descendants()
        )
    except Exception:
        return ""


def input_text(title_keywords, value: str, timeout: float = 10.0) -> bool:
    window, backend = find_dialog(title_keywords, timeout=timeout)
    if window is None:
        return False
    edit = _find_edit(window, backend)
    if edit is None:
        try:
            window.set_focus()
            window.type_keys("^a{BACKSPACE}", set_foreground=True)
            window.type_keys(value, with_spaces=True, set_foreground=True)
            return True
        except Exception:
            return False
    try:
        edit.set_focus()
        edit.type_keys("^a{BACKSPACE}", set_foreground=True)
        edit.type_keys(value, with_spaces=True)
        return True
    except Exception:
        try:
            edit.click_input()
            edit.type_keys("^a{BACKSPACE}")
            edit.type_keys(value, with_spaces=True)
            return True
        except Exception:
            return False


def _window_point(window, x_ratio: float, y_ratio: float) -> tuple[int, int] | None:
    try:
        rect = window.rectangle()
    except Exception:
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return int(rect.left + width * x_ratio), int(rect.top + height * y_ratio)


def click_in_dialog(title_keywords, x_ratio: float, y_ratio: float, timeout: float = 10.0) -> bool:
    """按对话框窗口的相对坐标点击（不依赖控件树）"""
    # 快路径：Win32 原生枚举 + GetWindowRect，毫秒级；pywinauto 枚举要 1-3 秒
    deadline = time.time() + max(timeout, 0)
    while True:
        hwnd = find_hwnd(title_keywords)
        if hwnd is not None:
            point = _hwnd_point(hwnd, x_ratio, y_ratio)
            if point is not None:
                _focus_hwnd(hwnd)
                try:
                    import pyautogui

                    pyautogui.FAILSAFE = False
                    pyautogui.click(point[0], point[1])
                    return True
                except Exception:
                    return False
        if time.time() >= deadline:
            break
        time.sleep(0.1)

    window, _ = find_dialog(title_keywords, timeout=timeout)
    if window is None:
        return False
    try:
        window.set_focus()
    except Exception:
        pass
    point = _window_point(window, x_ratio, y_ratio)
    if point is None:
        return False
    try:
        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.click(point[0], point[1])
        return True
    except Exception:
        return False


def double_click_in_dialog(title_keywords, x_ratio: float, y_ratio: float, timeout: float = 10.0) -> bool:
    """按对话框窗口的相对坐标双击（用于双击证书行完成选择）"""
    deadline = time.time() + max(timeout, 0)
    while True:
        hwnd = find_hwnd(title_keywords)
        if hwnd is not None:
            point = _hwnd_point(hwnd, x_ratio, y_ratio)
            if point is not None:
                _focus_hwnd(hwnd)
                try:
                    import pyautogui

                    pyautogui.FAILSAFE = False
                    pyautogui.doubleClick(point[0], point[1], interval=0.15)
                    return True
                except Exception:
                    return False
        if time.time() >= deadline:
            break
        time.sleep(0.1)

    window, _ = find_dialog(title_keywords, timeout=timeout)
    if window is None:
        return False
    try:
        window.set_focus()
    except Exception:
        pass
    point = _window_point(window, x_ratio, y_ratio)
    if point is None:
        return False
    try:
        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.doubleClick(point[0], point[1], interval=0.15)
        return True
    except Exception:
        return False


def click_dialog_button(title_keywords, button_texts=("确定", "确认", "OK"), ratio=(0.71, 0.87), timeout: float = 10.0) -> bool:
    """先尝试控件树点击，失败后按窗口相对坐标点击"""
    if click_button(title_keywords, button_texts, timeout=0.5):
        return True
    return click_in_dialog(title_keywords, ratio[0], ratio[1], timeout=timeout)
