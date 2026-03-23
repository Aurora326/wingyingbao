# -*- coding: utf-8 -*-
"""
打印对话框处理模块
使用 PyAutoGUI 处理 Chrome 打印预览对话框
"""
import time
import pyautogui
import pygetwindow as gw
from pathlib import Path
from logger import setup_logger

logger = setup_logger()


class PrintDialogHandler:
    """打印对话框处理器"""
    
    def __init__(self):
        self.save_path = None
        
    def wait_for_print_dialog(self, timeout: int = 30) -> bool:
        """等待打印对话框出现"""
        logger.info("等待打印对话框...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # 查找包含"打印"的窗口
            windows = gw.getAllWindows()
            for win in windows:
                if "打印" in win.title or "print" in win.title.lower():
                    logger.info(f"找到打印窗口: {win.title}")
                    return True
            time.sleep(0.5)
        
        logger.warning("未找到打印对话框")
        return False
    
    def handle_print_dialog(self, save_path: str, filename: str = None) -> bool:
        """
        处理打印对话框，保存为 PDF
        
        Args:
            save_path: 保存目录
            filename: 文件名（可选）
            
        Returns:
            是否成功
        """
        try:
            logger.info("开始处理打印对话框...")
            
            # 等待打印对话框
            if not self.wait_for_print_dialog(timeout=10):
                logger.warning("未检测到打印对话框，可能已自动处理")
                return False
            
            # 激活 Chrome 窗口
            chrome_windows = gw.getWindowsWithTitle('Chrome')
            if not chrome_windows:
                chrome_windows = gw.getWindowsWithTitle('兴业银行')
            
            if chrome_windows:
                win = chrome_windows[0]
                win.activate()
                time.sleep(1)
                logger.info("已激活 Chrome 窗口")
            
            # 检查是否已经是"另存为 PDF"
            # 按 Tab 键导航到打印机选择下拉框（需要按多次）
            logger.info("选择'另存为 PDF'...")
            
            # 按 Tab 键导航（打印对话框中，打印机选择通常是第一个或第二个可交互元素）
            pyautogui.press('tab', presses=3, interval=0.2)
            time.sleep(0.5)
            
            # 按向下箭头打开下拉菜单
            pyautogui.press('down')
            time.sleep(0.5)
            
            # 按向上箭头选择"另存为 PDF"（通常在列表顶部）
            pyautogui.press('up', presses=5)
            time.sleep(0.5)
            
            # 按 Enter 确认选择
            pyautogui.press('enter')
            time.sleep(0.5)
            
            # 按 Tab 导航到"保存"按钮
            pyautogui.press('tab', presses=5, interval=0.2)
            time.sleep(0.5)
            
            # 按 Enter 点击保存
            pyautogui.press('enter')
            logger.info("已点击保存按钮")
            
            # 等待文件保存对话框（如果出现）
            time.sleep(2)
            
            # 如果有文件保存对话框，输入文件名
            if filename:
                pyautogui.typewrite(filename, interval=0.05)
                time.sleep(0.5)
                pyautogui.press('enter')
            
            logger.info("打印对话框处理完成")
            return True
            
        except Exception as e:
            logger.error(f"处理打印对话框失败: {e}")
            return False
    
    def cancel_print_dialog(self) -> bool:
        """取消打印对话框"""
        try:
            # 激活 Chrome 窗口
            chrome_windows = gw.getWindowsWithTitle('Chrome')
            if chrome_windows:
                chrome_windows[0].activate()
                time.sleep(0.5)
            
            # 按 Escape 取消
            pyautogui.press('escape')
            logger.info("已取消打印对话框")
            return True
        except Exception as e:
            logger.error(f"取消打印对话框失败: {e}")
            return False


def handle_print_preview(save_path: str, timeout: int = 60) -> bool:
    """
    处理 Chrome 打印预览，保存为 PDF
    
    Args:
        save_path: 保存路径
        timeout: 超时时间（秒）
        
    Returns:
        是否成功
    """
    handler = PrintDialogHandler()
    return handler.handle_print_dialog(save_path)
