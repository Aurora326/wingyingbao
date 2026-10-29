# -*- coding: utf-8 -*-
"""
虚拟键盘输入辅助模块（AiKeyboard.exe 封装）
供各银行自动化项目共享
"""
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from loguru import logger


def get_aikeyboard_path() -> Path:
    """获取 AiKeyboard.exe 的路径（兼容打包和开发环境）"""
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
    else:
        # 开发环境：向上回溯到项目根目录（common 的上两级）
        exe_dir = Path(__file__).resolve().parent.parent

    possible_paths = [
        Path.cwd() / "tools" / "AiKeyboard.exe",
        Path.cwd() / "keyboard" / "AiKeyboard.exe",
        exe_dir / "tools" / "AiKeyboard.exe",
        exe_dir / "keyboard" / "AiKeyboard.exe",
        exe_dir.parent / "keyboard" / "AiKeyboard.exe",
        exe_dir.parent.parent / "keyboard" / "AiKeyboard.exe",
    ]

    for path in possible_paths:
        try:
            path = path.resolve()
            if path.exists():
                logger.info(f"找到 AiKeyboard.exe: {path}")
                return path
        except Exception:
            continue

    logger.warning("AiKeyboard.exe 未找到")
    return None


# 常见英文键盘布局的语言 ID（美式英语 0409、英式英语 0809 等）
_ENGLISH_LANGUAGE_IDS = {
    0x0409, 0x0809, 0x0C09, 0x1009, 0x1409, 0x1809, 0x1C09, 0x2009, 0x2409,
    0x2809, 0x2C09, 0x3009, 0x3409, 0x3809, 0x3A09,
}


def _get_active_language_id() -> int:
    """获取当前前台窗口实际使用的键盘布局语言 ID（比 GetKeyboardLayout(0) 更准确）。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            thread_id = user32.GetWindowThreadProcessId(hwnd, None)
            return user32.GetKeyboardLayout(thread_id) & 0xFFFF
    except Exception:
        pass
    try:
        import ctypes
        return ctypes.windll.user32.GetKeyboardLayout(0) & 0xFFFF
    except Exception:
        return 0


def switch_to_english_layout() -> bool:
    """
    确定性切换到英文键盘布局，避免中文输入法（微软拼音等）在输入密码时
    吞字符 / 走拼音组合，导致密码不完整。

    注意：AiKeyboard.exe 的 input 模式内部也会做输入法切换；本函数先把
    系统布局切到英文，AiKeyboard 检测到英文后就不会再按 Shift，从而避免
    双重切换把输入法又切回中文。

    Returns:
        bool: 是否已处于英文键盘布局（尽力而为，失败不阻塞后续输入）
    """
    lang_id = _get_active_language_id()
    logger.info(f"当前输入法语言 ID: 0x{lang_id:04X}")
    if lang_id in _ENGLISH_LANGUAGE_IDS:
        logger.info("当前已是英文输入法")
        return True

    # 方法1：直接激活美式英语键盘布局（KLF_ACTIVATE）
    try:
        import ctypes
        user32 = ctypes.windll.user32
        KLF_ACTIVATE = 0x00000001
        user32.LoadKeyboardLayoutW("00000409", KLF_ACTIVATE)
        time.sleep(0.5)
        lang_id = _get_active_language_id()
        logger.info(f"LoadKeyboardLayout 后输入法: 0x{lang_id:04X}")
        if lang_id in _ENGLISH_LANGUAGE_IDS:
            return True
    except Exception as e:
        logger.debug(f"LoadKeyboardLayout 切换失败: {e}")

    # 方法2：Win+Space 循环切换输入法（最多 3 次）
    try:
        import pyautogui
        for i in range(3):
            pyautogui.hotkey('win', 'space')
            time.sleep(0.4)
            lang_id = _get_active_language_id()
            if lang_id in _ENGLISH_LANGUAGE_IDS:
                logger.info(f"Win+Space 第 {i + 1} 次后切到英文输入法 (0x{lang_id:04X})")
                return True
    except Exception as e:
        logger.debug(f"Win+Space 切换输入法失败: {e}")

    # 方法3：兜底按一次 Shift（中文输入法的中/英内部模式切换，尽力而为）
    try:
        import pyautogui
        logger.info("兜底按 Shift 切换输入法...")
        pyautogui.press('shift')
        time.sleep(0.3)
    except Exception as e:
        logger.debug(f"Shift 切换失败: {e}")

    logger.warning("未能确认切换到英文输入法，继续尝试输入")
    return False


def input_with_aikeyboard(text: str) -> bool:
    """
    使用 AiKeyboard.exe 虚拟键盘输入文本
    命令格式: AiKeyboard.exe input -k <password>
    """
    aikeyboard_path = get_aikeyboard_path()
    if not aikeyboard_path:
        return False

    logger.info("使用 AiKeyboard 输入密码...")

    # 确定性切换到英文键盘布局，避免中文输入法吞字符；
    # 同时避免与 AiKeyboard 内部输入法切换发生"重复按 Shift"导致又切回中文
    switch_to_english_layout()

    # 延时确保准备就绪
    time.sleep(2)

    result = subprocess.run(
        [str(aikeyboard_path), "input", "-k", text],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(aikeyboard_path.parent),
    )
    if result.returncode == 0:
        logger.info("AiKeyboard 输入完成")
        return True
    else:
        logger.error(f"AiKeyboard 失败: {result.stderr}")
        return False


def type_text_raw(text: str, interval_ms: int = None) -> bool:
    """
    直接调用 AiKeyboard 输入文本，**不做输入法切换、不额外延时**。

    与 input_with_aikeyboard 的区别：后者每次调用都会切一次输入法并 sleep 2 秒，
    适合"一次性打完一整串"；本函数供**逐位闭环输入**使用——那种场景要连续调用
    很多次，调用方自己负责在开始前切好输入法，不能每字符都重切 + 等 2 秒。

    Args:
        text: 要输入的文本（逐位场景下通常只有 1 个字符）
        interval_ms: 按键间隔毫秒；None 用 AiKeyboard 默认值(500)

    Returns:
        bool: 进程是否成功退出（不代表按键一定落入目标窗口）
    """
    aikeyboard_path = get_aikeyboard_path()
    if not aikeyboard_path:
        return False

    cmd = [str(aikeyboard_path), "input", "-k", text]
    if interval_ms:
        cmd += ["-i", str(interval_ms)]

    # 单字符很快，但冷启动可能慢，给足余量
    timeout = max(15, int(len(text) * interval_ms / 1000) + 10) if interval_ms else 15
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(aikeyboard_path.parent),
        )
    except subprocess.TimeoutExpired:
        logger.error(f"AiKeyboard 超时（{timeout}s）")
        return False

    if result.returncode != 0:
        logger.error(f"AiKeyboard 失败: {result.stderr}")
        return False
    return True


# 常见按键名称映射（小写/错误写法 -> AiKeyboard 正确写法）
_KEY_NAME_MAP = {
    "return": "Enter",
    "enter": "Enter",
    "space": "Space",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "delete": "Delete",
    "del": "Delete",
    "backspace": "Backspace",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "shift": "LeftShift",
    "leftshift": "LeftShift",
    "rightshift": "RightShift",
}


def send_with_aikeyboard(key: str) -> bool:
    """
    使用 AiKeyboard.exe 发送单个按键
    命令格式: AiKeyboard.exe send -k <key>
    """
    aikeyboard_path = get_aikeyboard_path()
    if not aikeyboard_path:
        return False

    # 规范化按键名称
    normalized_key = _KEY_NAME_MAP.get(key.lower(), key)
    if normalized_key != key:
        logger.debug(f"AiKeyboard 按键名称规范化: '{key}' -> '{normalized_key}'")

    result = subprocess.run(
        [str(aikeyboard_path), "send", "-k", normalized_key],
        capture_output=True,
        text=True,
        timeout=5,
        cwd=str(aikeyboard_path.parent),
    )
    if result.returncode != 0:
        logger.warning(f"AiKeyboard send '{key}' (normalized='{normalized_key}') 失败: rc={result.returncode}, stderr={result.stderr.strip()}")
    return result.returncode == 0


def click_native_button(
    button_texts: Iterable[str] = ("确认", "确定", "OK"),
    window_title_keywords: Iterable[str] = ("用户登录", "USB", "UKey", "Key", "密码"),
    timeout: float = 5.0,
) -> bool:
    """Click a button in a native Windows dialog, useful when Enter is ignored."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception as e:
        logger.warning(f"无法加载 Windows 原生控件接口: {e}")
        return False

    user32 = ctypes.windll.user32
    enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    enum_child_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    bm_click = 0x00F5
    sw_restore = 9
    button_text_set = {str(text).strip().lower() for text in button_texts}
    title_keywords = tuple(str(text).lower() for text in window_title_keywords)

    def get_text(hwnd) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def get_class(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    deadline = time.time() + timeout
    while time.time() < deadline:
        windows = []

        @enum_windows_proc
        def enum_windows(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                title = get_text(hwnd)
                if title and any(keyword in title.lower() for keyword in title_keywords):
                    windows.append((hwnd, title))
            return True

        user32.EnumWindows(enum_windows, 0)

        for hwnd, title in windows:
            matches = []

            @enum_child_proc
            def enum_children(child_hwnd, _lparam):
                child_text = get_text(child_hwnd).strip()
                child_class = get_class(child_hwnd)
                if child_text.lower() in button_text_set and "button" in child_class.lower():
                    matches.append((child_hwnd, child_text, child_class))
                    return False
                return True

            user32.EnumChildWindows(hwnd, enum_children, 0)
            if matches:
                child_hwnd, child_text, child_class = matches[0]
                logger.info(f"点击原生弹窗按钮: window='{title}', button='{child_text}', class='{child_class}'")
                try:
                    user32.ShowWindow(hwnd, sw_restore)
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                user32.SendMessageW(child_hwnd, bm_click, 0, 0)
                return True

        time.sleep(0.2)

    logger.warning("未找到可点击的原生弹窗确认按钮")
    return False
