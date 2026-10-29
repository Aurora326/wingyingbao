# -*- coding: utf-8 -*-
"""
验证码处理模块 - 基于百度云通用文字识别(OCR)

使用流程：
    1. 检测验证码弹窗
    2. 截图验证码图片
    3. 调用百度云OCR识别
    4. 填入识别结果并提交

配置文件示例（config.ini）：
    [captcha]
    enabled = true
    api_key = 你的API Key
    secret_key = 你的Secret Key
"""
import base64
import json
import time
from io import BytesIO
from typing import Optional
from pathlib import Path

import requests
from loguru import logger
from playwright.sync_api import Page, Locator


class BaiduOCR:
    """百度云通用文字识别客户端"""

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    # 高精度版对验证码（小图、干扰线、倾斜）识别率更好
    OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self._access_token: Optional[str] = None
        self._token_expire_time: float = 0

    def _get_access_token(self) -> str:
        """获取 Access Token（带缓存）"""
        if self._access_token and time.time() < self._token_expire_time - 300:
            return self._access_token

        logger.info("正在获取百度云 Access Token...")
        resp = requests.post(
            self.TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.secret_key,
            },
            timeout=10,
        )
        data = resp.json()
        if "access_token" not in data:
            raise Exception(f"获取 Access Token 失败: {data}")

        self._access_token = data["access_token"]
        # 提前5分钟过期，避免边界问题
        expires_in = data.get("expires_in", 2592000)
        self._token_expire_time = time.time() + expires_in - 300
        logger.info("Access Token 获取成功")
        return self._access_token

    def recognize(self, image_bytes: bytes) -> str:
        """
        识别图片中的文字，验证码为字母+数字组合，过滤掉其他字符

        Args:
            image_bytes: 图片二进制数据

        Returns:
            识别出的文字（仅保留字母和数字）
        """
        import re
        token = self._get_access_token()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        logger.info("正在调用百度云 OCR 识别...")
        resp = requests.post(
            self.OCR_URL,
            params={"access_token": token},
            data={"image": image_b64, "language_type": "ENG", "probability": "true"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        data = resp.json()

        if "error_code" in data:
            raise Exception(f"OCR 识别失败: {data.get('error_msg')}")

        words_result = data.get("words_result", [])
        if not words_result:
            logger.warning("OCR 未识别到任何文字")
            return ""

        # 拼接所有识别结果并清理，同时用置信度过滤低质量结果
        raw_parts = []
        for item in words_result:
            word = item.get("words", "")
            prob = item.get("probability", {})
            avg_prob = prob.get("average", 1.0) if isinstance(prob, dict) else 1.0
            raw_parts.append(word)
            if avg_prob < 0.7:
                logger.warning(f"OCR 低置信度识别: [{word}] 置信度={avg_prob:.2f}")

        raw_text = "".join(raw_parts)
        # 验证码为字母+数字，过滤掉所有非字母数字字符
        text = re.sub(r'[^a-zA-Z0-9]', '', raw_text)
        logger.info(f"OCR 原始结果: [{raw_text}] -> 过滤后: [{text}]")
        return text


class CaptchaSolver:
    """验证码处理类"""

    def __init__(self, page: Page, api_key: str = "", secret_key: str = "", enabled: bool = True):
        self.page = page
        self.enabled = enabled
        self.ocr: Optional[BaiduOCR] = None
        if api_key and secret_key:
            self.ocr = BaiduOCR(api_key, secret_key)

    def solve_if_present(
        self,
        dialog_selector: str = "",
        input_selector: str = "",
        img_selector: str = "",
        submit_selector: str = "",
        screenshot_path: str = "",
        max_retries: int = 3,
    ) -> bool:
        """
        如果检测到验证码弹窗，自动截图识别并提交
        支持重试：识别失败或结果不合理时自动刷新重试

        Args:
            dialog_selector: 验证码弹窗的选择器（为空则使用通用检测）
            input_selector: 验证码输入框选择器
            img_selector: 验证码图片选择器
            submit_selector: 提交按钮选择器
            screenshot_path: 调试截图保存路径
            max_retries: 最大重试次数

        Returns:
            True=处理成功或没有验证码, False=处理失败
        """
        if not self.enabled or not self.ocr:
            logger.info("验证码处理未启用或缺少OCR配置")
            return True

        # 1. 检测验证码弹窗
        dialog = self._find_captcha_dialog(dialog_selector)
        if not dialog:
            logger.debug("未检测到验证码弹窗")
            return True

        logger.info("检测到验证码弹窗，开始处理...")
        # 给弹窗和验证码图片加载时间
        time.sleep(1.5)

        for attempt in range(1, max_retries + 1):
            logger.info(f"验证码识别尝试 {attempt}/{max_retries}...")

            try:
                # 2. 截图验证码图片（等待充分加载）
                image_bytes = self._screenshot_captcha(dialog, img_selector, screenshot_path)
                if not image_bytes:
                    logger.error("验证码截图失败")
                    if attempt < max_retries:
                        self._refresh_captcha(dialog)
                        time.sleep(2)
                        continue
                    return False

                # 3. OCR 识别
                captcha_text = self.ocr.recognize(image_bytes)

                # 校验识别结果合理性（验证码通常为4-6位字母数字）
                cleaned = captcha_text.strip()
                if not cleaned:
                    logger.warning("验证码识别结果为空")
                    if attempt < max_retries:
                        self._refresh_captcha(dialog)
                        time.sleep(2)
                        continue
                    return False

                # 验证码为4位字母数字，过滤后若不是4位则重试
                if not cleaned or len(cleaned) != 4:
                    logger.warning(f"验证码识别结果异常（{len(cleaned)}位，期望4位），可能识别错误: [{cleaned}]")
                    if attempt < max_retries:
                        self._refresh_captcha(dialog)
                        time.sleep(2)
                        continue
                    else:
                        logger.error("验证码识别多次失败，放弃处理")
                        return False

                # 4. 填入输入框
                self._fill_captcha_input(input_selector, cleaned)
                time.sleep(1)

                # 5. 点击提交
                self._click_submit(submit_selector)
                logger.info("验证码已提交")
                return True

            except Exception as e:
                logger.error(f"验证码处理异常(尝试{attempt}): {e}")
                if attempt < max_retries:
                    self._refresh_captcha(dialog)
                    time.sleep(2)
                    continue
                return False

        logger.error(f"验证码处理失败，已达最大重试次数({max_retries})")
        return False

    def _find_captcha_dialog(self, dialog_selector: str = "") -> Optional[Locator]:
        """查找验证码弹窗"""
        if dialog_selector:
            try:
                dialog = self.page.locator(dialog_selector).first
                if dialog.count() > 0 and dialog.is_visible(timeout=2000):
                    return dialog
            except Exception:
                pass

        # 通用检测：根据常见关键词
        common_indicators = [
            "text=请输入验证码",
            "text=验证码",
            "text=图形验证码",
            "text=安全验证",
            ".captcha-dialog",
            ".verify-dialog",
            "[class*='captcha']",
            "[class*='verify']",
            ".codeModel",
            ".el-dialog__wrapper.codeModel",
        ]

        for indicator in common_indicators:
            try:
                el = self.page.locator(indicator).first
                if el.count() > 0 and el.is_visible(timeout=1500):
                    # 如果检测到关键词，尝试向上查找弹窗容器
                    return el
            except Exception:
                continue

        return None

    def _screenshot_captcha(
        self,
        dialog: Locator,
        img_selector: str = "",
        screenshot_path: str = "",
    ) -> Optional[bytes]:
        """截图验证码图片（放慢，确保图片已完全加载）"""
        # 放慢：先等待一下让验证码图片完全加载
        time.sleep(1)

        # 方式1：直接截图验证码图片元素
        if img_selector:
            try:
                img = self.page.locator(img_selector).first
                if img.count() > 0 and img.is_visible(timeout=3000):
                    # 放慢：等待图片加载完成（naturalWidth > 0）
                    self.page.wait_for_function(
                        f"() => {{ const el = document.querySelector('{img_selector.replace(chr(39), chr(92)+chr(39))}'); return el && el.naturalWidth > 0; }}",
                        timeout=5000
                    )
                    img_bytes = img.screenshot()
                    if screenshot_path:
                        Path(screenshot_path).write_bytes(img_bytes)
                    logger.info(f"验证码图片已截图: {screenshot_path}")
                    return img_bytes
            except Exception as e:
                logger.warning(f"截图验证码图片元素失败: {e}")

        # 方式2：南京银行精确选择器
        try:
            njcb_selector = "body > div.el-dialog__wrapper.codeModel > div > div.el-dialog__body > div > div.code > div.refsh > img"
            img = self.page.locator(njcb_selector).first
            if img.count() > 0 and img.is_visible(timeout=3000):
                img_bytes = img.screenshot()
                if screenshot_path:
                    Path(screenshot_path).write_bytes(img_bytes)
                logger.info(f"验证码图片已截图(南京银行精确选择器): {screenshot_path}")
                return img_bytes
        except Exception as e:
            logger.warning(f"南京银行精确选择器截图失败: {e}")

        # 方式3：在弹窗内查找 img 标签
        try:
            img = dialog.locator("img").first
            if img.count() > 0 and img.is_visible(timeout=3000):
                img_bytes = img.screenshot()
                if screenshot_path:
                    Path(screenshot_path).write_bytes(img_bytes)
                logger.info(f"验证码图片已截图(弹窗内img): {screenshot_path}")
                return img_bytes
        except Exception as e:
            logger.warning(f"弹窗内查找img截图失败: {e}")

        # 方式4：截图整个弹窗
        try:
            dialog_bytes = dialog.screenshot()
            if screenshot_path:
                Path(screenshot_path).write_bytes(dialog_bytes)
            logger.info(f"验证码弹窗已截图: {screenshot_path}")
            return dialog_bytes
        except Exception as e:
            logger.warning(f"弹窗截图失败: {e}")

        return None

    def _fill_captcha_input(self, input_selector: str = "", text: str = ""):
        """填入验证码"""
        selectors = []
        if input_selector:
            selectors.append(input_selector)

        selectors.extend([
            "input[placeholder*='验证码']",
            "input[placeholder*='captcha']",
            "input[name='captcha']",
            "input[name='verifyCode']",
            "input[name='code']",
            "input[id='captcha']",
            "input[id='verifyCode']",
            "input[type='text']",
        ])

        for selector in selectors:
            try:
                inp = self.page.locator(selector).first
                if inp.count() > 0 and inp.is_visible(timeout=2000):
                    inp.fill(text)
                    logger.info(f"已填入验证码 [{text}] 到: {selector}")
                    return
            except Exception:
                continue

        logger.warning("未找到验证码输入框")

    def _refresh_captcha(self, dialog: Locator):
        """点击刷新验证码"""
        refresh_selectors = [
            ".refsh",                       # 南京银行刷新按钮
            "text=刷新",                   # 通用刷新文字
            "text=换一张",                 # 常见换一张
            ".refresh-btn",
            "img[title*='刷新']",
            "img[title*='换一张']",
            "a:has-text('刷新')",
            "a:has-text('换一张')",
        ]
        for selector in refresh_selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click()
                    logger.info(f"已点击刷新验证码: {selector}")
                    time.sleep(1.5)
                    return
            except Exception:
                continue
        logger.warning("未找到验证码刷新按钮")

    def _click_submit(self, submit_selector: str = ""):
        """点击提交按钮"""
        selectors = []
        if submit_selector:
            selectors.append(submit_selector)

        selectors.extend([
            "button:has-text('提交')",
            "button:has-text('确定')",
            "button:has-text('确认')",
            "a:has-text('提交')",
            "a:has-text('确定')",
            "input[type='submit']",
            ".submit-btn",
            "#submitBtn",
        ])

        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=3000):
                    btn.click()
                    logger.info(f"已点击提交按钮: {selector}")
                    time.sleep(2)
                    return
            except Exception:
                continue

        logger.warning("未找到提交按钮")
