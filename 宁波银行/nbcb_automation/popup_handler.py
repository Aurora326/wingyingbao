# -*- coding: utf-8 -*-
"""
弹窗处理模块 - 宁波银行适配版
"""
import time
from logger import setup_logger

logger = setup_logger()


class PopupHandlerMixin:
    """弹窗处理 Mixin"""

    def _handle_reconciliation_popup(self):
        """处理登录后可能出现的对账单弹窗"""
        logger.info("=" * 50)
        logger.info("【对账单弹窗】开始检查...")
        
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            
            # 等待一下让弹窗完全显示
            time.sleep(3)
            
            # 获取屏幕尺寸
            screen_width, screen_height = pyautogui.size()
            logger.info(f"【对账单弹窗】屏幕尺寸: {screen_width} x {screen_height}")
            
            # 弹窗在屏幕中央，"详细核对"按钮在弹窗右下角
            # 根据实际截图调整坐标：屏幕 1920x1080，弹窗居中
            # 弹窗宽度约 700px，高度约 500px，居中显示
            # "详细核对"按钮约在屏幕 x=1400, y=830 位置（偏右下）
            
            # 计算按钮位置（根据实际屏幕尺寸）
            if screen_width >= 1920:
                # 大屏：按钮在右下角
                btn_x = int(screen_width * 0.73)   # 约 1400
                btn_y = int(screen_height * 0.77)  # 约 830
            elif screen_width >= 1500:
                # 中屏
                btn_x = int(screen_width * 0.73)
                btn_y = int(screen_height * 0.77)
            else:
                # 小屏
                btn_x = int(screen_width * 0.75)
                btn_y = int(screen_height * 0.75)
            
            # 先点击"详细核对"按钮（黄色按钮）
            logger.info(f"【对账单弹窗】尝试点击'详细核对'按钮位置: ({btn_x}, {btn_y})")
            pyautogui.click(btn_x, btn_y)
            logger.info("【对账单弹窗】已点击'详细核对'按钮")
            time.sleep(3)  # 等待更长时间
            
            # 检查弹窗是否还在
            logger.info("【对账单弹窗】检查弹窗是否已关闭...")
            still_exists = self._check_popup_exists()
            
            if still_exists:
                # 尝试点击"核对后相符"按钮（左侧按钮）
                left_btn_x = btn_x - 150  # 左侧约 150px
                left_btn_y = btn_y
                logger.info(f"【对账单弹窗】尝试点击'核对后相符'按钮: ({left_btn_x}, {left_btn_y})")
                pyautogui.click(left_btn_x, left_btn_y)
                time.sleep(3)
                
                still_exists = self._check_popup_exists()
            
            if still_exists:
                # 尝试点击弹窗右上角 X 关闭按钮
                close_x = int(screen_width * 0.65)  # 弹窗右上角
                close_y = int(screen_height * 0.28)  # 弹窗顶部
                logger.info(f"【对账单弹窗】尝试点击关闭按钮: ({close_x}, {close_y})")
                pyautogui.click(close_x, close_y)
                time.sleep(2)
                
                still_exists = self._check_popup_exists()
            
            if still_exists:
                logger.warning("【对账单弹窗】弹窗仍然存在，尝试按 ESC 键...")
                pyautogui.press('escape')
                time.sleep(2)
                
                still_exists = self._check_popup_exists()
            
            if still_exists:
                logger.warning("【对账单弹窗】弹窗仍未关闭，请手动处理...")
                time.sleep(10)  # 给用户时间手动处理
            else:
                logger.info("【对账单弹窗】✓ 弹窗已关闭")
            
        except Exception as e:
            logger.error(f"【对账单弹窗】关闭失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        logger.info("=" * 50)
    
    def _check_popup_exists(self):
        """检查对账单弹窗是否还存在"""
        try:
            # 通过检查页面上是否还有弹窗相关文本
            still_exists = self.page.evaluate("""() => {
                const text = document.body.innerText;
                return text.includes('综合余额对账单') || text.includes('余额对账');
            }""")
            return still_exists
        except Exception:
            return False

    def _handle_usbkey_confirm_dialog(self):
        """处理 USBKey 确认提示框（点击确定按钮关闭）"""
        logger.info("检查 USBKey 确认提示框...")
        
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            
            # 等待提示框出现
            time.sleep(2)
            
            # 获取屏幕尺寸
            screen_width, screen_height = pyautogui.size()
            center_x = screen_width // 2
            center_y = screen_height // 2
            
            # 根据截图，确定按钮在弹窗右下角，取消按钮在左下角
            # 确定按钮位置：中央偏右下方
            # 先尝试按 Tab 键切换到确定按钮（从取消按钮向右切）
            logger.info("尝试按 Tab 键切换到确定按钮...")
            pyautogui.press('tab')  # 从左边的取消切换到右边的确定
            time.sleep(0.3)
            
            # 然后按回车键
            logger.info("按回车键确认...")
            pyautogui.press('return')
            time.sleep(1)
            
            logger.info("已点击确定按钮关闭提示框")
            
        except Exception as e:
            logger.warning(f"自动关闭提示框失败: {e}")
            logger.info("请手动点击确定按钮关闭提示框...")
            time.sleep(5)
