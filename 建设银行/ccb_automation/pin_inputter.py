# -*- coding: utf-8 -*-
"""
PIN 自动输入模块 - 建设银行适配版
"""
import time
from logger import setup_logger
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard

logger = setup_logger()

PIN_TITLES = ("验证通用盾密码", "通用盾密码", "输入密码", "U盾密码", "校验U盾密码")
_HANDLED_DIALOGS = set()


def _find_pin_dialog():
    import ctypes
    user32 = ctypes.windll.user32
    for title in PIN_TITLES:
        hwnd = user32.FindWindowW(None, title)
        if hwnd and user32.IsWindowVisible(hwnd):
            return hwnd
    return 0


def _find_dialog_controls(hwnd):
    """返回 PIN 弹窗里的密码输入框和确定按钮。"""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    edit = ok = None

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(child, _):
        nonlocal edit, ok
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(child, cls, 64)
        if cls.value == "Edit" and user32.IsWindowVisible(child) and edit is None:
            edit = child
        if cls.value == "Button" and user32.IsWindowVisible(child):
            n = user32.GetWindowTextLengthW(child)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(child, buf, n + 1)
            if buf.value.strip().replace(" ", "") == "确定" and ok is None:
                ok = child
        return True

    user32.EnumChildWindows(hwnd, callback, 0)
    return edit, ok


def _find_pin_error_dialog():
    """查找 U盾中间件的“密码错误”提示窗口。"""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def top(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "#32770":
            return True
        texts = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def child(ch, __):
            c2 = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(ch, c2, 64)
            if c2.value == "Static":
                n = user32.GetWindowTextLengthW(ch)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(ch, buf, n + 1)
                texts.append(buf.value)
            return True

        user32.EnumChildWindows(hwnd, child, 0)
        if any("密码错误" in text for text in texts):
            found.append((hwnd, " ".join(texts)))
        return True

    user32.EnumWindows(top, 0)
    return found[0] if found else (0, "")


def _click_dialog_ok(hwnd):
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    clicked = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def child(ch, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(ch, cls, 64)
        if cls.value == "Button" and user32.IsWindowVisible(ch):
            n = user32.GetWindowTextLengthW(ch)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(ch, buf, n + 1)
            if buf.value.strip().replace(" ", "") == "确定":
                user32.SendMessageW(ch, 0x00F5, 0, 0)
                clicked.append(True)
        return True

    user32.EnumChildWindows(hwnd, child, 0)
    return bool(clicked)


def _window_foreground(hwnd) -> bool:
    """确认目标窗口真的在前台，否则硬件键盘输入会落到别的窗口。"""
    import ctypes

    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    return foreground == user32.GetAncestor(hwnd, 2)  # GA_ROOT


def _edit_has_focus(hwnd, edit) -> bool:
    """确认键盘焦点真的落在密码输入框里，且弹窗在前台。"""
    import ctypes
    import ctypes.wintypes as wt

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("flags", wt.DWORD),
            ("hwndActive", wt.HWND),
            ("hwndFocus", wt.HWND),
            ("hwndCapture", wt.HWND),
            ("hwndMenuOwner", wt.HWND),
            ("hwndMoveSize", wt.HWND),
            ("hwndCaret", wt.HWND),
            ("rcCaret", wt.RECT),
        ]

    user32 = ctypes.windll.user32
    if not _window_foreground(hwnd):
        return False
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return False
    return info.hwndFocus == edit


def _focus_edit(hwnd, edit) -> bool:
    """把焦点强制放进密码输入框，否则 AiKeyboard 会把密码打到别的窗口。"""
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    time.sleep(0.2)
    if _edit_has_focus(hwnd, edit):
        return True

    root = user32.GetAncestor(hwnd, 2)
    foreground = user32.GetForegroundWindow()
    cur_tid = kernel32.GetCurrentThreadId()
    attached = []
    for tid in (
        user32.GetWindowThreadProcessId(root, None),
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0,
    ):
        if tid and tid != cur_tid and user32.AttachThreadInput(cur_tid, tid, True):
            attached.append(tid)
    try:
        user32.BringWindowToTop(root)
        user32.SetForegroundWindow(root)
        user32.SetFocus(edit)
    finally:
        for tid in attached:
            user32.AttachThreadInput(cur_tid, tid, False)
    time.sleep(0.3)
    return _edit_has_focus(hwnd, edit)


def _cancel_pin_dialog(hwnd):
    """放弃输入时点「取消」关闭弹窗，避免原生弹窗阻塞浏览器。"""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    clicked = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def child(ch, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(ch, cls, 64)
        if cls.value == "Button" and user32.IsWindowVisible(ch):
            n = user32.GetWindowTextLengthW(ch)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(ch, buf, n + 1)
            if buf.value.strip().replace(" ", "") in ("取消", "Cancel"):
                user32.SendMessageW(ch, 0x00F5, 0, 0)
                clicked.append(True)
        return True

    user32.EnumChildWindows(hwnd, child, 0)
    if not clicked:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


class PinInputterMixin:
    """PIN 自动输入 Mixin"""

    def _handle_pin_dialog(self):
        """处理建设银行 PIN 码验证弹窗（Windows系统弹窗）。"""
        logger.info("检查 PIN 码验证弹窗...")

        pin = self.credentials.pin if self.credentials else None

        if not pin:
            logger.info("未配置 PIN 码，等待手动输入...")
            logger.info("请在网银盾弹窗中输入 PIN 码并点击确认")
            time.sleep(20)
            return

        import ctypes
        user32 = ctypes.windll.user32

        deadline = time.time() + 20
        hwnd = _find_pin_dialog()
        while not hwnd and time.time() < deadline:
            time.sleep(0.3)
            hwnd = _find_pin_dialog()
        if not hwnd:
            logger.info("未检测到 PIN 弹窗，跳过")
            return

        if hwnd in _HANDLED_DIALOGS:
            logger.warning("该 PIN 弹窗已经处理过，停止重复输入")
            return
        _HANDLED_DIALOGS.add(hwnd)

        edit, ok = _find_dialog_controls(hwnd)
        if not edit:
            logger.warning("PIN 弹窗里没找到密码输入框")
            return

        # 把焦点强制放进密码框，否则 AiKeyboard 会把密码打到开始菜单等窗口
        if not _focus_edit(hwnd, edit):
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            user32.GetWindowRect(edit, ctypes.byref(rect))
            x = (rect.left + rect.right) // 2
            y = (rect.top + rect.bottom) // 2
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            user32.SetCursorPos(x, y)
            time.sleep(0.2)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.4)
            if not _focus_edit(hwnd, edit):
                logger.error("键盘焦点没能进入 PIN 输入框，放弃本次输入（避免密码打到别处）")
                _cancel_pin_dialog(hwnd)
                return

        # 关掉输入法，避免字母被中文输入法拦截
        try:
            ctypes.windll.imm32.ImmAssociateContext(edit, 0)
        except Exception:
            pass

        # CapsLock 开着会让大写字母输入变形，先关掉
        try:
            if user32.GetKeyState(0x14) & 1:
                send_with_aikeyboard("capslock")
                logger.info("检测到 CapsLock 开启，已关闭后输入 PIN")
        except Exception:
            pass

        # 清空可能残留的内容（密码控件长度不可靠，固定多按几次退格）
        for _ in range(12):
            send_with_aikeyboard("backspace")

        logger.info(f"使用 AiKeyboard 输入 PIN 码（共 {len(pin)} 位）...")
        if not input_with_aikeyboard(pin):
            logger.error("AiKeyboard 输入 PIN 失败")
            return

        time.sleep(0.8)
        user32.SendMessageW(ok, 0x00F5, 0, 0)  # BM_CLICK
        logger.info("已点击 PIN 弹窗的「确定」")

        time.sleep(2)
        err_hwnd, err_text = _find_pin_error_dialog()
        if err_hwnd:
            logger.error(f"U盾提示: {err_text.strip()[:80]}")
            _click_dialog_ok(err_hwnd)
            raise RuntimeError("U盾PIN码错误：请先在账户管理里更正该账户的 U盾 PIN，再重新采集")

        deadline = time.time() + 10
        while time.time() < deadline and _find_pin_dialog():
            time.sleep(0.2)
        if _find_pin_dialog():
            logger.warning("PIN 弹窗未关闭")
        else:
            logger.info("PIN 码输入流程完成")
