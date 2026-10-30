# -*- coding: utf-8 -*-
"""
制单模块 - 中信银行企业网银批量模板导入制单
"""

import time
import json
import csv
import shutil
import ctypes
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional
from openpyxl import load_workbook
from playwright.sync_api import Page
from loguru import logger
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard

# 用于在模态弹窗阻塞 Playwright 时发送系统级键盘事件
try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    import pygetwindow as gw
except Exception:
    gw = None


class PaymentCreator:
    """批量模板导入制单"""

    DEFAULT_TEMPLATE_MAPPING = {
        "sheet": "Sheet2",
        "start_row": 6,
        "clear_until_row": 3006,
        "clear_columns": 12,
        "columns": [
            {"column": 1, "field": "payment_method", "default_config": "payment_method", "default": "跨行转账"},
            {"column": 2, "field": "payer_account", "default_config": "payer_account", "transform": "payer_account_no"},
            {"column": 3, "field": "currency", "default_config": "currency", "default": "人民币"},
            {"column": 4, "field": "payee_account"},
            {"column": 5, "field": "payee_name"},
            {"column": 6, "field": ["payee_account_no", "payee_account_number"]},
            {"column": 7, "field": "payee_bank_code", "default_config": "payee_bank_code"},
            {"column": 8, "field": "payee_branch", "default_config": "payee_branch"},
            {"column": 9, "field": "amount", "transform": "amount"},
            {"column": 10, "field": "purpose", "default_config": "purpose"},
            {"column": 11, "field": "remark", "default_config": "remark"},
            {"column": 12, "field": ["reconcile_no", "cst_bsn_seq_no", "对账编号"], "default_config": "reconcile_no"},
        ],
    }

    def __init__(self, page: Page, config: Dict, pin: str = "", cabinet_controller=None):
        self.page = page
        self.config = config
        self.action = config.get('action', 'save')
        self.pin = pin
        self.cabinet_controller = cabinet_controller
        self.items = self._load_items()

    def _load_items(self) -> List[Dict]:
        """加载付款项"""
        configured_items = self.config.get('items') or []
        if configured_items:
            return [dict(item) for item in configured_items]

        batch_file = (self.config.get('batch_file') or '').strip() or 'temp/batch_payments.json'
        items = self._load_batch_file(batch_file)
        if items:
            return items

        item = {k: v for k, v in self.config.items() if k not in (
            'enabled', 'mode', 'action', 'batch_file', 'template_file',
            'template_mapping_file', 'generated_file', 'items'
        )}
        if item.get('payer_account') or item.get('amount') or item.get('payee_account'):
            return [item]
        return []

    def _load_batch_file(self, path: str) -> List[Dict]:
        """从批量文件加载"""
        items = []
        batch_path = self._resolve_path(path)
        if not batch_path.exists():
            fallback = self._resolve_path("temp/batch_payments.json")
            if fallback.exists():
                logger.warning(f"批量文件不存在，改用默认付款清单: {batch_path} -> {fallback}")
                batch_path = fallback
        try:
            with open(batch_path, 'r', encoding='utf-8-sig') as f:
                if batch_path.suffix.lower() == '.json':
                    data = json.load(f)
                    items = data if isinstance(data, list) else [data]
                else:
                    reader = csv.DictReader(f)
                    items = list(reader)
        except Exception as e:
            logger.error(f"加载批量文件失败: {batch_path}, {e}")
        return items

    def _resolve_path(self, path: str) -> Path:
        """将配置路径解析为项目内绝对路径。"""
        p = Path(path)
        if p.is_absolute():
            return p
        return Path(__file__).resolve().parent / p

    def _payer_account_no(self, value: str) -> str:
        """模板里付款账号只写账号本身，不带户名/币种。"""
        value = (value or "").strip()
        if "/" in value:
            return value.split("/", 1)[0].strip()
        return value

    def _format_amount(self, value: str) -> str:
        try:
            return f"{Decimal(str(value).replace(',', '').strip()):.2f}"
        except (InvalidOperation, ValueError):
            return str(value or "").strip()

    def _amount_decimal(self, value: str) -> Decimal:
        try:
            return Decimal(self._format_amount(value))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    def _amount_equal(self, actual: str, expected: str) -> bool:
        try:
            actual_amount = Decimal(str(actual or "").replace(",", "").strip())
            expected_amount = Decimal(str(expected or "").replace(",", "").strip())
            return actual_amount == expected_amount
        except (InvalidOperation, ValueError):
            return str(actual or "").strip() == str(expected or "").strip()

    def _load_template_mapping(self) -> Dict[str, Any]:
        mapping_file = self.config.get("template_mapping_file") or "template/payments_batch_template.json"
        mapping_path = self._resolve_path(mapping_file)
        if not mapping_path.exists():
            logger.warning(f"批量模板映射文件不存在，使用内置映射: {mapping_path}")
            return dict(self.DEFAULT_TEMPLATE_MAPPING)
        try:
            with open(mapping_path, "r", encoding="utf-8-sig") as f:
                mapping = json.load(f)
            if not isinstance(mapping, dict) or not mapping.get("columns"):
                raise ValueError("mapping must be an object with columns")
            return mapping
        except Exception as e:
            logger.warning(f"加载批量模板映射失败，使用内置映射: {mapping_path}, {e}")
            return dict(self.DEFAULT_TEMPLATE_MAPPING)

    def _mapped_value(self, item: Dict, column_cfg: Dict[str, Any]) -> str:
        fields = column_cfg.get("field", [])
        if isinstance(fields, str):
            fields = [fields]

        value = ""
        for field in fields:
            value = item.get(field, "")
            if value not in ("", None):
                break

        default_config = column_cfg.get("default_config")
        if value in ("", None) and default_config:
            value = self.config.get(default_config, "")
        if value in ("", None):
            value = column_cfg.get("default", "")

        transform = column_cfg.get("transform")
        if transform == "payer_account_no":
            return self._payer_account_no(str(value or ""))
        if transform == "amount":
            return self._format_amount(value)
        return value or ""

    def _generate_batch_import_file(self) -> Path:
        """按中信批量转账模板生成导入文件。"""
        template_file = self.config.get("template_file") or "template/批量转账模板.xlsx"
        generated_file = self.config.get("generated_file") or "temp/payment_import.xlsx"
        template_path = self._resolve_path(template_file)
        output_path = self._resolve_path(generated_file)
        if not template_path.exists():
            raise Exception(f"批量转账模板不存在: {template_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_path, output_path)

        wb = load_workbook(output_path)
        mapping = self._load_template_mapping()
        sheet_name = mapping.get("sheet") or "Sheet2"
        ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb.active
        start_row = int(mapping.get("start_row") or 6)
        clear_until_row = int(mapping.get("clear_until_row") or 3006)
        clear_columns = int(mapping.get("clear_columns") or 12)
        columns = mapping.get("columns") or self.DEFAULT_TEMPLATE_MAPPING["columns"]

        for row in range(start_row, clear_until_row + 1):
            for col in range(1, clear_columns + 1):
                ws.cell(row=row, column=col).value = None

        total = Decimal("0")
        for offset, item in enumerate(self.items):
            row = start_row + offset
            amount = self._format_amount(item.get("amount", ""))
            try:
                total += Decimal(amount)
            except (InvalidOperation, ValueError):
                pass
            for column_cfg in columns:
                col = int(column_cfg["column"])
                ws.cell(row=row, column=col).value = self._mapped_value(item, column_cfg)

        wb.save(output_path)
        logger.info(f"已生成批量转账导入文件: {output_path}，笔数={len(self.items)}，总金额={total:.2f}")
        return output_path

    def _create_by_batch_import(self) -> str:
        """通过批量转账模板导入制单。"""
        import_file = self._generate_batch_import_file()
        self._navigate_to_batch_payment_page()
        self._dismiss_popups()
        self._upload_batch_file(import_file)
        self._fill_batch_summary()
        self._fill_batch_other_info()
        self._save_debug_snapshot()
        return self._finish_batch_action()

    def _navigate_to_batch_payment_page(self):
        """导航到批量转账导入页面。"""
        logger.info("导航到批量转账制单页面...")
        payment_url = "https://corp.bank.ecitic.com/cotb/index_v5.html#/00201AA1/00201AA1_bat"
        try:
            self.page.goto(payment_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            self._close_sidebar_menu()
            tab = self.page.locator("text=批量转账").first
            if tab.count() > 0 and tab.is_visible(timeout=2000):
                tab.click()
                time.sleep(1)
            if not self._is_batch_payment_page():
                raise Exception("未检测到批量转账导入控件")
            logger.info("已成功进入批量转账制单页面")
        except Exception as e:
            logger.error(f"批量转账页面导航失败: {e}")
            raise Exception("未能进入批量转账制单页面")

    def _is_batch_payment_page(self) -> bool:
        indicators = [
            "text=转账信息导入",
            "text=XLSX模板下载",
            "input[type='file']",
            "text=总笔数",
        ]
        for indicator in indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue
        return False

    def _upload_batch_file(self, import_file: Path):
        """上传生成的批量转账文件。"""
        logger.info(f"上传批量转账文件: {import_file}")
        file_input = self.page.locator("input#myFile[type='file'], input[type='file']").first
        if file_input.count() == 0:
            raise Exception("未找到批量转账文件上传控件")
        file_input.set_input_files(str(import_file))
        time.sleep(1)
        # 处理"今日已导入过该文件，是否继续"弹窗
        self._handle_duplicate_file_dialog()
        try:
            self.page.wait_for_function("""
                () => {
                    const uploaded = document.getElementById('uploaded');
                    const uploading = document.getElementById('uploading');
                    const file = document.getElementById('myFile');
                    const uploadedVisible = uploaded && window.getComputedStyle(uploaded).display !== 'none';
                    const uploadingVisible = uploading && window.getComputedStyle(uploading).display !== 'none';
                    return uploadedVisible || uploadingVisible || !!(file && file.value);
                }
            """, timeout=10000)
        except Exception:
            logger.warning("等待批量文件上传状态超时")
        status = self.page.evaluate("""
            () => {
                const style = (id) => {
                    const el = document.getElementById(id);
                    return el ? window.getComputedStyle(el).display : '';
                };
                const text = (id) => {
                    const el = document.getElementById(id);
                    return el ? (el.textContent || '').replace(/\\s+/g, ' ').trim() : '';
                };
                const file = document.getElementById('myFile');
                return {
                    fileValue: file ? file.value : '',
                    doUploadDisplay: style('doUpload'),
                    uploadingDisplay: style('uploading'),
                    uploadedDisplay: style('uploaded'),
                    uploadingText: text('uploading'),
                    uploadedText: text('uploaded')
                };
            }
        """)
        logger.info(f"批量文件上传状态: {status}")

    def _fill_batch_summary(self):
        """填写总笔数、总金额，供银行页面校验。"""
        total_count = str(len(self.items))
        total_amount = self._format_amount(sum(self._amount_decimal(i.get("amount", "0")) for i in self.items))
        result = self.page.evaluate("""
            (args) => {
                const setById = (id, value) => {
                    const input = document.getElementById(id);
                    if (!input) return false;
                    input.removeAttribute('readonly');
                    input.removeAttribute('disabled');
                    input.focus();
                    input.value = value;
                    input.setAttribute('value', value);
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    input.dispatchEvent(new Event('blur', {bubbles: true}));
                    const container = input.closest('.col-xs-5, .form-group, div');
                    const info = container && container.querySelector('.primaryInfo');
                    if (info) info.style.display = 'none';
                    input.classList.remove('input-err');
                    return true;
                };
                return {
                    count: setById('totalNum', args.count),
                    amount: setById('totalAmt', args.amount),
                    totalNumValue: document.getElementById('totalNum') && document.getElementById('totalNum').value,
                    totalAmtValue: document.getElementById('totalAmt') && document.getElementById('totalAmt').value
                };
            }
        """, {"count": total_count, "amount": total_amount})
        logger.info(f"批量页总笔数/总金额填写结果: {result}")
        if not result or result.get("totalNumValue") != total_count or not self._amount_equal(result.get("totalAmtValue"), total_amount):
            raise Exception(f"批量页总笔数/总金额填写异常: {result}")

    def _fill_batch_other_info(self):
        """填写批量页下方公共字段。"""
        first = self.items[0]
        business_flow = first.get("business_flow") or self.config.get("business_flow", "")
        if business_flow:
            self._fill_field("业务流程", business_flow)
            time.sleep(0.5)
        logger.info("批量页公共附言/备注不再填写，使用导入模板中的逐笔附言/备注")

    def _finish_batch_action(self) -> str:
        """批量导入页没有暂存按钮时，save 模式停在提交前供人工核对。"""
        if self.action == 'submit':
            return self._click_submit()
        logger.info("批量导入页未找到暂存按钮，已停在提交前等待人工核对")
        return "批量导入完成，已停在提交前"

    def create_all(self) -> Dict[str, str]:
        """通过批量模板导入创建付款单。"""
        results = {}
        if not self.items:
            logger.warning("没有配置付款项")
            return results

        try:
            result = self._create_by_batch_import()
            results["batch_import"] = result
        except Exception as e:
            logger.error(f"批量模板导入制单失败: {e}")
            results["batch_import"] = f"失败: {e}"
        return results

    def _close_sidebar_menu(self):
        """关闭侧边栏菜单弹窗"""
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
            self.page.mouse.click(600, 400)
            time.sleep(0.5)
            logger.info("已关闭侧边栏菜单")
        except Exception as e:
            logger.info(f"关闭侧边栏菜单失败: {e}")

    def _save_debug_snapshot(self):
        """保存提交前截图，便于核对银行表单真实状态。"""
        try:
            debug_dir = Path(__file__).resolve().parent / "debug"
            debug_dir.mkdir(exist_ok=True)
            date_name = time.strftime("%Y-%m-%d")
            path = debug_dir / f"{date_name}制单提交前.png"
            self.page.screenshot(path=str(path), full_page=True)
            logger.info(f"已保存制单提交前截图: {path}")
            html_path = debug_dir / f"{date_name}制单提交前.html"
            html_path.write_text(self.page.content(), encoding="utf-8")
            logger.info(f"已保存制单提交前HTML: {html_path}")
        except Exception as e:
            logger.debug(f"保存制单调试截图失败: {e}")

    def _save_result_screenshot(self) -> Optional[Path]:
        """保存提交结果弹窗截图，用于告知用户执行结果。"""
        try:
            result_dir = Path(__file__).resolve().parent / "records"
            result_dir.mkdir(exist_ok=True)
            path = result_dir / f"{time.strftime('%Y-%m-%d')}执行结果.png"
            self.page.screenshot(path=str(path), full_page=False)
            logger.info(f"已保存执行结果截图: {path}")
            return path
        except Exception as e:
            logger.warning(f"保存执行结果截图失败: {e}")
            return None

    def _click_submit(self) -> str:
        """点击提交"""
        logger.info("点击提交...")
        try:
            btn = self.page.locator("button:has-text('提交')").first
            if btn.count() > 0 and btn.is_visible(timeout=3000):
                btn.click()
                time.sleep(3)
                self._handle_confirm_dialog()
                # 处理交易签名 U盾密码弹窗
                self._handle_ukey_password_dialog()
                # 处理物理 U盾 OK 键确认提示
                self._handle_ukey_physical_confirm()
                self._handle_confirm_dialog()
                result = self._handle_result_dialog()
                return result or "提交成功"
            else:
                raise Exception("未找到提交按钮")
        except Exception as e:
            raise Exception(f"提交失败: {e}")

    def _handle_result_dialog(self) -> Optional[str]:
        """处理结果弹窗"""
        try:
            dialog_selectors = [
                ".dialog-body",
                ".modal-body",
                ".el-message-box__message",
                "[role='dialog']",
            ]
            for selector in dialog_selectors:
                try:
                    el = self.page.locator(selector).first
                    if el.count() > 0 and el.is_visible(timeout=3000):
                        text = el.text_content() or ""
                        logger.info(f"弹窗内容: {text.strip()}")
                        if "交易信息" in text and "制单提交成功" not in text and "提交成功" not in text:
                            logger.info("当前仍是交易信息确认弹窗，不作为最终结果")
                            return None
                        self._save_result_screenshot()
                        close_btn = self.page.locator(
                            "button:has-text('关闭'), button:has-text('确定'), .el-message-box__btns button"
                        ).first
                        if close_btn.count() > 0 and close_btn.is_visible(timeout=2000):
                            close_btn.click()
                            time.sleep(1)
                        return text.strip()
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _handle_confirm_dialog(self):
        """处理确认对话框"""
        try:
            for text in ['确定', '确认', '是']:
                btn = self.page.locator(f"button:has-text('{text}')").first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(1)
                    break
        except Exception:
            pass

    def _handle_ukey_password_dialog(self):
        """处理交易签名时的'校验U盾密码'弹窗，使用 AiKeyboard 输入密码。"""
        if not self.pin:
            logger.info("未配置 PIN，跳过 U盾密码弹窗自动输入")
            return
        try:
            logger.info("等待 U盾密码弹窗出现...")
            win = self._wait_for_ukey_window(timeout=20)
            if win:
                self._activate_ukey_window(win)
            else:
                logger.warning("未识别到 U盾密码窗口，按当前焦点尝试输入")

            self._click_ukey_password_input(win)
            if input_with_aikeyboard(self.pin):
                logger.info("AiKeyboard U盾密码输入完成")
            else:
                logger.warning("AiKeyboard 输入失败")

            time.sleep(0.5)
            self._confirm_ukey_window(win)
            time.sleep(5)
        except Exception as e:
            logger.error(f"处理 U盾密码弹窗失败: {e}")

    def _handle_ukey_physical_confirm(self):
        """确认完 U盾密码后，等待 5 秒调用机柜控制器按压物理 OK 键，按压后等待 10 秒。"""
        if not self.cabinet_controller:
            return
        try:
            logger.info("U盾密码已提交，等待 5 秒后调用机柜按压物理 OK 键...")
            time.sleep(5)
            for attempt in range(3):
                try:
                    if self.cabinet_controller.click_ok():
                        logger.info(f"机柜按压 OK 键成功（第 {attempt + 1} 次），等待 10 秒...")
                        time.sleep(10)
                        return
                    else:
                        logger.warning(f"机柜按压 OK 键返回失败（第 {attempt + 1} 次）")
                except Exception as e:
                    logger.warning(f"机柜按压 OK 键异常（第 {attempt + 1} 次）: {e}")
                time.sleep(1)
            logger.error("机柜按压 OK 键多次失败，请手动按 U盾 OK 键")
        except Exception as e:
            logger.debug(f"调用机柜按压 OK 键失败: {e}")

    def _wait_for_ukey_window(self, timeout: int = 20):
        """等待系统级 U盾密码窗口。"""
        if not gw:
            logger.warning("pygetwindow 不可用，无法按标题识别 U盾密码窗口")
            time.sleep(3)
            return None
        deadline = time.time() + timeout
        keywords = ["校验U盾密码", "U盾密码", "U盾", "密码"]
        while time.time() < deadline:
            try:
                for win in gw.getAllWindows():
                    title = (win.title or "").strip()
                    if title and any(k in title for k in keywords):
                        logger.info(f"检测到 U盾密码窗口: title={title}, box=({win.left},{win.top},{win.width},{win.height})")
                        return win
            except Exception as e:
                logger.debug(f"枚举窗口失败: {e}")
            time.sleep(0.5)
        return None

    def _activate_ukey_window(self, win):
        """激活 U盾密码窗口。"""
        if not win:
            return
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.4)
        except Exception as e:
            logger.debug(f"激活 U盾密码窗口失败: {e}")

    def _click_ukey_password_input(self, win):
        """按窗口相对位置点击 U盾密码输入框。"""
        if not pyautogui:
            return
        try:
            if win:
                x = win.left + int(win.width * 0.42)
                y = win.top + int(win.height * 0.47)
            else:
                width, height = pyautogui.size()
                # 根据视频全屏截图修正：密码输入框在屏幕中上部
                x = int(width * 0.443)
                y = int(height * 0.315)
            pyautogui.click(x, y)
            logger.info(f"已点击 U盾密码输入框: ({x}, {y})")
            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"点击 U盾密码输入框失败: {e}")

    def _confirm_ukey_window(self, win):
        """提交 U盾密码窗口。"""
        logger.info("提交 U盾密码窗口...")
        if win and self._click_ukey_confirm_button_win32(win):
            logger.info("已通过 Win32 控件点击 U盾密码确认按钮")
            return
        # 优先使用 AiKeyboard 发送回车（驱动级，可穿透安全控件拦截）
        logger.info("使用 AiKeyboard 发送回车提交 U盾密码...")
        if send_with_aikeyboard('Enter'):
            logger.info("AiKeyboard 回车发送完成")
            time.sleep(2)
            if win and not self._is_ukey_window_alive(win):
                logger.info("AiKeyboard 回车已关闭 U盾密码窗口")
                return
        else:
            logger.warning("AiKeyboard 发送回车失败")
        if pyautogui:
            try:
                if win:
                    self._activate_ukey_window(win)
                pyautogui.press("enter")
                time.sleep(0.5)
                pyautogui.press("enter")
                if win and not self._is_ukey_window_alive(win):
                    logger.info("已连续按 Enter 提交 U盾密码")
                    return
                if win:
                    logger.warning("连续按 Enter 后 U盾密码窗口仍存在，尝试点击按钮")
            except Exception as e:
                logger.debug(f"按 Enter 提交 U盾密码失败: {e}")
            try:
                if win:
                    self._activate_ukey_window(win)
                    x = win.left + int(win.width * 0.27)
                    y = win.top + int(win.height * 0.88)
                    pyautogui.click(x, y)
                    logger.info(f"已点击 U盾密码确认按钮坐标: ({x}, {y})")
                    return
                x = 876
                y = 596
                pyautogui.click(x, y)
                logger.info(f"已点击 U盾密码确认按钮: ({x}, {y})")
                return
            except Exception as e:
                logger.debug(f"系统级提交 U盾密码失败: {e}")
        try:
            self.page.keyboard.press("Enter")
            logger.info("已通过 Playwright 按 Enter 提交 U盾密码")
        except Exception:
            pass

    def _click_ukey_confirm_button_win32(self, win) -> bool:
        """通过 Win32 子控件查找并点击 U盾弹窗确认按钮。"""
        hwnd = getattr(win, "_hWnd", None) if win else None
        if not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            candidates = []

            EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def get_text(child_hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(child_hwnd, buf, 256)
                return buf.value.strip()

            def get_class(child_hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(child_hwnd, buf, 256)
                return buf.value.strip()

            def enum_child(child_hwnd, _):
                text = get_text(child_hwnd)
                cls = get_class(child_hwnd)
                if text:
                    logger.debug(f"U盾窗口子控件: hwnd={child_hwnd}, class={cls}, text={text}")
                if text in ("确认", "确定", "OK", "Ok", "ok"):
                    candidates.append(child_hwnd)
                return True

            user32.EnumChildWindows(hwnd, EnumChildProc(enum_child), 0)
            if not candidates:
                logger.warning("未找到 U盾窗口确认按钮子控件")
                return False

            BM_CLICK = 0x00F5
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            user32.SendMessageW(candidates[0], BM_CLICK, 0, 0)
            time.sleep(1)
            return not self._is_ukey_window_alive(win)
        except Exception as e:
            logger.debug(f"Win32 点击 U盾确认按钮失败: {e}")
            return False

    def _is_ukey_window_alive(self, win) -> bool:
        hwnd = getattr(win, "_hWnd", None) if win else None
        if not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))
        except Exception:
            return False

    def _handle_duplicate_file_dialog(self):
        """处理'今日已导入过该文件，是否继续'弹窗，点击确认继续上传。"""
        try:
            # 先等待弹窗可能出现
            time.sleep(1)
            dialog_detected = False
            # 1. 优先通过页面 body 文本检测（最稳定）
            try:
                body = self.page.locator("body").first
                if body.count() > 0:
                    page_text = body.text_content() or ""
                    if "今日已导入过该文件" in page_text:
                        dialog_detected = True
                        logger.info("检测到'今日已导入过该文件'提示弹窗")
            except Exception:
                pass
            # 2. 通过弹窗标题检测
            if not dialog_detected:
                try:
                    title_el = self.page.locator(
                        "#pleasemPop-show .modal-header .modal-title, "
                        ".modal.in .modal-title, .modal.show .modal-title, "
                        ".dialog-title, .layui-layer-title, .el-message-box__title"
                    ).first
                    if title_el.count() > 0 and title_el.is_visible(timeout=1000):
                        title_text = title_el.text_content() or ""
                        if "提示" in title_text:
                            dialog_detected = True
                            logger.info("检测到提示弹窗")
                except Exception:
                    pass
            if not dialog_detected:
                return
            # 尝试点击"确认"按钮，优先使用精确选择器
            confirmed = False
            confirm_selectors = [
                "#pleasemPop-show .modal-footer .btn-primary",
                ".modal.in .modal-footer .btn-primary",
                ".modal.show .modal-footer .btn-primary",
                "button:has-text('确认')",
                "a:has-text('确认')",
                "button:has-text('确定')",
                "a:has-text('确定')",
            ]
            for selector in confirm_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        logger.info(f"已点击确认按钮({selector})，继续导入文件")
                        confirmed = True
                        time.sleep(1)
                        break
                except Exception:
                    continue
            # 兜底：通过 JavaScript 查找并点击确认按钮
            if not confirmed:
                try:
                    result = self.page.evaluate("""
                        () => {
                            // 优先在已知弹窗容器内查找
                            const modal = document.getElementById('pleasemPop-show')
                                || document.querySelector('.modal.in')
                                || document.querySelector('.modal.show');
                            const scope = modal || document;
                            const btns = scope.querySelectorAll('button, a, input[type="button"]');
                            for (const btn of btns) {
                                const txt = (btn.textContent || btn.value || '').trim();
                                if (txt === '确认' || txt === '确定') {
                                    btn.click();
                                    return {clicked: true, text: txt};
                                }
                            }
                            // 全局兜底
                            const allBtns = document.querySelectorAll('button, a, input[type="button"]');
                            for (const btn of allBtns) {
                                const txt = (btn.textContent || btn.value || '').trim();
                                if (txt === '确认' || txt === '确定') {
                                    btn.click();
                                    return {clicked: true, text: txt};
                                }
                            }
                            return {clicked: false};
                        }
                    """)
                    if result and result.get('clicked'):
                        logger.info(f"通过 JS 点击了'{result.get('text')}'按钮")
                        time.sleep(1)
                except Exception as e:
                    logger.debug(f"JS 点击确认按钮失败: {e}")
        except Exception as e:
            logger.debug(f"处理重复文件提示弹窗失败: {e}")

    def _dismiss_popups(self):
        """清理页面弹窗和遮挡层"""
        try:
            result = self.page.evaluate("""
                () => {
                    let closed = false;
                    const modalSelectors = [
                        '.modal.in', '.modal.show', '.modal-dialog', '.modal-content',
                        '[role="dialog"]', '.layui-layer', '.el-message-box',
                        '.ant-modal-content', '.sweet-alert'
                    ];
                    for (const sel of modalSelectors) {
                        const modals = document.querySelectorAll(sel);
                        for (const modal of modals) {
                            if (modal.offsetParent === null) continue;
                            const rect = modal.getBoundingClientRect();
                            if (rect.width < 50 || rect.height < 50) continue;
                            const btns = modal.querySelectorAll(
                                'button, a, .close, .layui-layer-close, [aria-label="Close"]'
                            );
                            for (const btn of btns) {
                                const txt = btn.textContent.trim();
                                if (['关闭','确定','取消','我知道了','暂不'].includes(txt)
                                    || btn.classList.contains('close')
                                    || btn.getAttribute('aria-label') === 'Close') {
                                    btn.click();
                                    closed = true;
                                    break;
                                }
                            }
                            if (closed) break;
                        }
                        if (closed) break;
                    }
                    if (!closed) {
                        const banners = document.querySelectorAll(
                            '.alert, .notification, .toast, .banner, [class*="alert-"], [class*="notification-"], [class*="toast-"]'
                        );
                        for (const banner of banners) {
                            if (banner.offsetParent === null) continue;
                            const closeBtn = banner.querySelector('button.close, .close-btn, .dismiss, [class*="close"]');
                            if (closeBtn) { closeBtn.click(); closed = true; break; }
                        }
                    }
                    return {closed: closed};
                }
            """)
            if result and result.get('closed'):
                logger.info("已关闭页面弹窗/通知")
                time.sleep(1)
        except Exception:
            pass
        try:
            self.page.keyboard.press('Escape')
        except Exception:
            pass

    def _fill_field(self, label: str, value: str) -> bool:
        """综合策略：下拉框字段直接走下拉框逻辑，其他先尝试 input 填写"""
        if not value:
            logger.info(f"[{label}] 值为空，跳过")
            return True
        logger.info(f"[{label}] 开始填写，值: {value}")
        # 已知下拉框字段直接走下拉框，避免 _fill_input 错误匹配到页面其他 input
        dropdown_labels = ["业务流程"]
        if label in dropdown_labels:
            logger.info(f"[{label}] 使用下拉框策略")
            if self._select_custom_dropdown(label, value):
                return True
            logger.warning(f"[{label}] 下拉框策略失败")
            return False
        logger.info(f"[{label}] 使用input填写策略")
        if self._fill_input(label, value):
            return True
        logger.info(f"[{label}] input策略失败，尝试下拉框兜底")
        if self._select_custom_dropdown(label, value):
            return True
        logger.warning(f"[{label}] 所有策略均失败")
        return False

    def _fill_input(self, label: str, value: str) -> bool:
        """通过 placeholder 或 label 找到 input 并填写"""
        placeholder_map = {
            "备注": ["请输入，仅付方可见"],
        }
        candidates = placeholder_map.get(label, [label])
        for ph in candidates:
            try:
                el = self.page.locator(
                    f"input[placeholder='{ph}']:visible, textarea[placeholder='{ph}']:visible"
                )
                count = el.count()
                if label == "备注" and count > 1:
                    el = el.nth(count - 1)
                else:
                    el = el.first
                if el.count() > 0 and el.is_visible(timeout=1500):
                    el.click()
                    time.sleep(0.2)
                    el.fill(value)
                    time.sleep(0.2)
                    el.evaluate("el => el.dispatchEvent(new Event('input', {bubbles: true}))")
                    el.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
                    el.evaluate("el => el.dispatchEvent(new Event('blur', {bubbles: true}))")
                    logger.info(f"[{label}] placeholder匹配成功，已填写: {value}")
                    return True
            except Exception:
                continue

        try:
            result = self.page.evaluate("""
                (args) => {
                    const labelText = args.label;
                    const value = args.value;
                    const norm = (t) => t.replace(/\\*/g, '').replace(/[：:]/g, '').replace(/\\s+/g, ' ').trim();
                    const target = norm(labelText);
                    let labelEl = null;
                    for (const el of document.querySelectorAll('label')) {
                        if (norm(el.textContent) === target) { labelEl = el; break; }
                    }
                    if (!labelEl) {
                        for (const el of document.querySelectorAll('span, div, td, th')) {
                            if (norm(el.textContent) === target) { labelEl = el; break; }
                        }
                    }
                    if (!labelEl) return {success: false, reason: '未找到label'};
                    let inputEl = null;
                    if (labelEl.htmlFor) inputEl = document.getElementById(labelEl.htmlFor);
                    if (!inputEl) {
                        const container = labelEl.closest('.form-group, .form-item, li, .row, td, div, tr');
                        if (container) inputEl = container.querySelector('input:not([type="hidden"]), textarea');
                    }
                    if (!inputEl) {
                        let sibling = labelEl.nextElementSibling;
                        while (sibling && !inputEl) {
                            inputEl = sibling.querySelector('input, textarea');
                            if (!inputEl && (sibling.tagName === 'INPUT' || sibling.tagName === 'TEXTAREA')) {
                                inputEl = sibling;
                            }
                            sibling = sibling.nextElementSibling;
                        }
                    }
                    if (!inputEl) return {success: false, reason: '未找到input'};
                    if (inputEl.disabled || inputEl.readOnly || inputEl.type === 'hidden') {
                        return {success: false, reason: 'input不可用'};
                    }
                    const mainContent = document.querySelector('.panel-body, .main-content, .tab-content, .container');
                    if (mainContent && !mainContent.contains(inputEl)) {
                        return {success: false, reason: '不在主内容区'};
                    }
                    inputEl.focus();
                    inputEl.value = value;
                    inputEl.dispatchEvent(new Event('input', {bubbles: true}));
                    inputEl.dispatchEvent(new Event('change', {bubbles: true}));
                    inputEl.dispatchEvent(new Event('blur', {bubbles: true}));
                    return {success: true};
                }
            """, {"label": label, "value": value})
            if result and result.get('success'):
                logger.info(f"[{label}] JS label匹配成功，已填写: {value}")
                return True
            else:
                logger.warning(f"[{label}] JS label匹配失败: {result.get('reason', 'unknown')}")
        except Exception:
            pass
        return False

    def _focus_page(self):
        """把焦点从浏览器地址栏等 Chrome UI 拉回网页。"""
        try:
            self.page.bring_to_front()
            self.page.mouse.click(900, 500)
            time.sleep(0.15)
        except Exception:
            pass

    def _select_custom_dropdown(self, label: str, value: str) -> bool:
        """处理 ec-select-gyj / new-select 自定义下拉框。"""
        if not value:
            logger.info(f"[{label}] 值为空，跳过")
            return True
        logger.info(f"[{label}] 开始下拉框选择，目标值: {value}")
        try:
            self._focus_page()
            self.page.keyboard.press("Escape")
            time.sleep(0.3)

            result = self.page.evaluate("""
                (args) => {
                    const labelText = args.label;
                    const norm = (t) => t.replace(/\\*/g, '').replace(/[\uFF1A:]/g, '').replace(/\\s+/g, ' ').trim();
                    const target = norm(labelText);
                    let container = null;
                    let foundBy = '';

                    const uniquePlaceholderMap = {};
                    const targetPh = uniquePlaceholderMap[labelText];
                    if (targetPh) {
                        for (const inp of document.querySelectorAll('input')) {
                            const ph = (inp.getAttribute('placeholder') || '').trim();
                            if (ph === targetPh) {
                                container = inp.closest('.ec-select-gyj, .new-select, .el-select, [class*="select"]');
                                if (container) { foundBy = 'placeholder'; break; }
                            }
                        }
                    }

                    if (!container) {
                        let labelEl = null;
                        for (const el of document.querySelectorAll('label')) {
                            if (norm(el.textContent) === target) {
                                labelEl = el; break;
                            }
                        }
                        if (!labelEl) {
                            for (const el of document.querySelectorAll('span, div, td, th')) {
                                if (norm(el.textContent) === target) {
                                    labelEl = el; break;
                                }
                            }
                        }
                        if (labelEl) {
                            foundBy = 'label';
                            if (labelEl.htmlFor) {
                                const targetInp = document.getElementById(labelEl.htmlFor);
                                if (targetInp) {
                                    container = targetInp.closest('.ec-select-gyj, .new-select, .el-select, [class*="select"]');
                                }
                            }
                            if (!container) {
                                const parent = labelEl.closest('.form-group, .form-item, .row, td, div, tr, li');
                                if (parent) {
                                    const cs = parent.querySelectorAll('.ec-select-gyj, .new-select, .el-select, [class*="select"]');
                                    if (cs.length === 1) {
                                        container = cs[0];
                                    } else if (cs.length > 1) {
                                        let minDist = Infinity;
                                        for (const c of cs) {
                                            const dist = Math.abs(labelEl.getBoundingClientRect().top - c.getBoundingClientRect().top);
                                            if (dist < minDist) { minDist = dist; container = c; }
                                        }
                                    }
                                }
                            }
                            if (!container) {
                                let sibling = labelEl.nextElementSibling;
                                while (sibling && !container) {
                                    container = sibling.closest('.ec-select-gyj, .new-select, .el-select, [class*="select"]');
                                    if (!container) container = sibling.querySelector('.ec-select-gyj, .new-select, .el-select, [class*="select"]');
                                    sibling = sibling.nextElementSibling;
                                }
                            }
                        }
                    }

                    if (!container) return {success: false, reason: '未找到容器'};
                    container.scrollIntoView({block: 'center'});
                    container.click();
                    const inputEl = container.querySelector('input.form-control, input.el-input__inner, input');
                    if (!inputEl) return {success: false, reason: '未找到搜索input'};
                    inputEl.focus();
                    inputEl.click();
                    const rect = inputEl.getBoundingClientRect();
                    const containerClass = container.className || '';
                    return {
                        success: true,
                        containerClass,
                        foundBy,
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2
                    };
                }
            """, {"label": label})

            if not result or not result.get('success'):
                logger.warning(f"[{label}] 打开下拉框失败: {result.get('reason', 'unknown')}")
                return False

            logger.info(f"[{label}] 已打开下拉框(container={result.get('containerClass','')[:30]}, by={result.get('foundBy','')})")
            self.page.mouse.click(result["x"], result["y"])
            time.sleep(0.2)
            self.page.keyboard.press("Control+A")
            time.sleep(0.1)
            self.page.keyboard.type(value, delay=30)
            time.sleep(1.5)

            option_result = self.page.evaluate("""
                (args) => {
                    const value = args.value;
                    const dropdowns = document.querySelectorAll('.el-select-dropdown, .select-dropdown, .dropdown-menu, .ec-select-dropdown, .new-select-dropdown, [class*="dropdown"]');
                    let candidates = [];
                    const isVisible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    for (const dd of dropdowns) {
                        if (!isVisible(dd)) continue;
                        const isNav = dd.closest('.nav, .navbar, .menu, .header, .sidebar, .aside, .left-menu, [class*="nav-"]');
                        if (isNav) continue;

                        const items = dd.querySelectorAll('li, tr, td, div, span, a');
                        for (const item of items) {
                            if (!isVisible(item)) continue;
                            const text = (item.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (text) candidates.push({text: text, tag: item.tagName, className: item.className || ''});
                        }
                    }

                    const allTexts = candidates.map(c => c.text);
                    const matches = candidates.filter(c => c.text === value || c.text.includes(value) || value.includes(c.text));
                    const target = matches[0] || candidates[0];
                    if (target) {
                        for (const item of document.querySelectorAll('li, tr, td, div, span, a')) {
                            const text = (item.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (text === target.text && isVisible(item)) {
                                item.click();
                                return {success: true, text: target.text, allOptions: allTexts.slice(0, 30)};
                            }
                        }
                    }
                    return {success: false, allOptions: allTexts.slice(0, 30)};
                }
            """, {"value": value})

            if option_result and option_result.get('success'):
                matched = option_result.get('text', '')
                logger.info(f"[{label}] 匹配成功: {matched}")
                time.sleep(0.5)
                self.page.evaluate("() => { if(document.activeElement) document.activeElement.blur(); }")
                time.sleep(0.3)
                return True

            hidden_result = self._click_hidden_dropdown_option(label, value)
            if hidden_result:
                return True

            all_opts = option_result.get('allOptions', [])
            logger.warning(f"[{label}] 未点击到选项，尝试键盘确认；当前可见选项数: {len(all_opts)}, 前10个: {all_opts[:10]}")
            self.page.mouse.click(result["x"], result["y"])
            time.sleep(0.2)
            self.page.keyboard.press("ArrowDown")
            time.sleep(0.2)
            self.page.keyboard.press("Enter")
            time.sleep(0.5)
            self.page.evaluate("() => { if(document.activeElement) document.activeElement.blur(); }")
            if self._force_set_custom_select(label, value):
                return True
            return True

        except Exception as e:
            logger.error(f"[{label}] 下拉框选择异常: {e}")
            return False

    def _click_hidden_dropdown_option(self, label: str, value: str) -> bool:
        """直接点击当前字段容器内的隐藏 li，触发页面自己的 ng-click。"""
        try:
            result = self.page.evaluate("""
                (args) => {
                    const labelText = args.label;
                    const value = args.value;
                    const norm = (t) => (t || '').replace(/\\*/g, '').replace(/[：:]/g, '').replace(/\\s+/g, ' ').trim();
                    const target = norm(labelText);
                    let labelEl = null;
                    for (const el of document.querySelectorAll('label, span, div, td, th')) {
                        if (norm(el.textContent) === target) { labelEl = el; break; }
                    }
                    let container = null;
                    if (labelEl) {
                        const row = labelEl.closest('.form-group, .form-item, .row, td, div, tr, li');
                        if (row) {
                            container = row.querySelector('.ec-select-gyj, .new-select, .el-select, [class*="select"]') || row;
                        }
                    }
                    const idMap = {
                        '业务流程': 'payAccBsnCon00201AA1_bsnFlowIpId'
                    };
                    if (!container && idMap[labelText]) {
                        const input = document.getElementById(idMap[labelText]);
                        if (input) container = input.closest('.ec-select-gyj, .new-select, .el-select, [class*="select"]');
                    }
                    if (!container) return {success: false, reason: '未找到容器'};

                    const lis = Array.from(container.querySelectorAll('li'));
                    const candidates = lis.map(li => ({
                        text: norm(li.textContent),
                        value: li.getAttribute('data-value') || '',
                        label: li.getAttribute('data-label') || ''
                    }));
                    let li = lis.find(el => norm(el.textContent) === value || norm(el.getAttribute('data-label')) === value);
                    if (!li) li = lis.find(el => norm(el.textContent).includes(value) || value.includes(norm(el.textContent)) || norm(el.getAttribute('data-label')).includes(value));
                    if (!li) return {success: false, reason: '未找到选项', candidates: candidates.slice(0, 10)};

                    const ul = li.closest('ul');
                    const oldDisplay = ul ? ul.style.display : '';
                    if (ul) ul.style.display = 'block';
                    li.click();
                    if (ul) ul.style.display = oldDisplay || 'none';

                    const input = container.querySelector('input:not([type="hidden"])');
                    if (input) {
                        input.value = norm(li.getAttribute('data-label')) || value;
                        input.setAttribute('value', input.value);
                        input.setAttribute('data-value', li.getAttribute('data-value') || input.getAttribute('data-value') || '');
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    }
                    container.classList.remove('input-err');
                    const info = container.querySelector('.primaryInfo');
                    if (info) info.style.display = 'none';
                    return {success: true, text: norm(li.textContent), dataValue: li.getAttribute('data-value') || ''};
                }
            """, {"label": label, "value": value})
            if result and result.get("success"):
                logger.info(f"[{label}] 已点击隐藏下拉选项: {result.get('text', '')}, value={result.get('dataValue', '')}")
                time.sleep(0.8)
                return True
            logger.warning(f"[{label}] 隐藏选项点击失败: {result}")
        except Exception as e:
            logger.warning(f"[{label}] 隐藏选项点击异常: {e}")
        return False

    def _force_set_custom_select(self, label: str, value: str) -> bool:
        """下拉组件无可见选项时，强制同步输入框和常见 Angular 模型。"""
        try:
            result = self.page.evaluate("""
                (args) => {
                    const labelText = args.label;
                    const value = args.value;
                    const norm = (t) => (t || '').replace(/\\*/g, '').replace(/[：:]/g, '').replace(/\\s+/g, ' ').trim();
                    const target = norm(labelText);
                    let labelEl = null;
                    for (const el of document.querySelectorAll('label, span, div, td, th')) {
                        if (norm(el.textContent) === target) { labelEl = el; break; }
                    }
                    if (!labelEl) return {success: false, reason: '未找到label'};

                    let container = null;
                    const row = labelEl.closest('.form-group, .form-item, .row, td, div, tr, li');
                    if (row) {
                        container = row.querySelector('.ec-select-gyj, .new-select, .el-select, [class*="select"]') || row;
                    }
                    if (!container) return {success: false, reason: '未找到容器'};

                    const input = container.querySelector('input:not([type="hidden"])');
                    if (!input) return {success: false, reason: '未找到input'};
                    input.removeAttribute('readonly');
                    input.removeAttribute('disabled');
                    input.value = value;
                    input.setAttribute('value', value);
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    input.dispatchEvent(new Event('blur', {bubbles: true}));

                    for (const hidden of container.querySelectorAll('input[type="hidden"], select')) {
                        hidden.value = value;
                        hidden.setAttribute('value', value);
                        hidden.dispatchEvent(new Event('input', {bubbles: true}));
                        hidden.dispatchEvent(new Event('change', {bubbles: true}));
                    }

                    if (window.angular) {
                        const applyModel = (el) => {
                            const ngModel = el.getAttribute('ng-model') || el.getAttribute('data-ng-model');
                            if (!ngModel) return false;
                            const scope = window.angular.element(el).scope() || window.angular.element(el).isolateScope();
                            if (!scope) return false;
                            const parts = ngModel.split('.');
                            let obj = scope;
                            for (let i = 0; i < parts.length - 1; i++) {
                                obj = obj[parts[i]];
                                if (!obj) return false;
                            }
                            obj[parts[parts.length - 1]] = value;
                            if (!scope.$$phase) scope.$applyAsync();
                            return true;
                        };
                        applyModel(input);
                        for (const el of container.querySelectorAll('[ng-model], [data-ng-model]')) {
                            applyModel(el);
                        }
                    }
                    return {success: true, value: input.value, className: container.className || ''};
                }
            """, {"label": label, "value": value})
            if result and result.get("success"):
                logger.info(f"[{label}] 已强制同步下拉框值: {value}")
                time.sleep(0.5)
                return True
            logger.warning(f"[{label}] 强制同步下拉框失败: {result}")
        except Exception as e:
            logger.warning(f"[{label}] 强制同步下拉框异常: {e}")
        return False
