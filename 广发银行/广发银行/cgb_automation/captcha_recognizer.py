# -*- coding: utf-8 -*-
"""
验证码识别模块 - 使用百度OCR
"""
import base64
import urllib.parse
import requests
from pathlib import Path
from typing import Optional
from logger import setup_logger

logger = setup_logger()


class BaiduOCR:
    """百度OCR验证码识别"""
    
    def __init__(self, api_key: str = None, secret_key: str = None):
        """
        初始化百度OCR
        
        Args:
            api_key: 百度AI平台的API Key
            secret_key: 百度AI平台的Secret Key
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.access_token = None
        
    def _get_access_token(self) -> Optional[str]:
        """
        使用 AK，SK 生成鉴权签名（Access Token）
        :return: access_token，或是None(如果错误)
        """
        if not self.api_key or not self.secret_key:
            logger.warning("未配置百度OCR API Key 或 Secret Key")
            return None
            
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key
        }
        
        try:
            response = requests.post(url, params=params, timeout=10)
            result = response.json()
            
            if "access_token" in result:
                logger.info("百度OCR Access Token 获取成功")
                return result["access_token"]
            else:
                logger.error(f"获取Access Token失败: {result}")
                return None
                
        except Exception as e:
            logger.error(f"获取Access Token异常: {e}")
            return None
    
    def _get_file_content_as_base64(self, path: Path, urlencoded: bool = False) -> str:
        """
        获取文件base64编码
        :param path: 文件路径
        :param urlencoded: 是否对结果进行urlencoded 
        :return: base64编码信息
        """
        with open(path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf8")
            if urlencoded:
                content = urllib.parse.quote_plus(content)
        return content
    
    def recognize_image(self, image_path: Path) -> Optional[str]:
        """
        识别图片中的文字
        
        Args:
            image_path: 图片文件路径
            
        Returns:
            识别出的文字，失败返回None
        """
        if not self.access_token:
            self.access_token = self._get_access_token()
            
        if not self.access_token:
            return None
        
        try:
            # 将图片转为base64
            image_base64 = self._get_file_content_as_base64(image_path, urlencoded=True)
            
            # 调用百度OCR精确识别接口
            url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={self.access_token}"
            
            payload = f"image={image_base64}"
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            logger.info("正在调用百度OCR识别验证码...")
            response = requests.post(url, headers=headers, data=payload.encode("utf-8"), timeout=10)
            response.encoding = "utf-8"
            
            result = response.json()
            logger.debug(f"百度OCR返回结果: {result}")
            
            # 解析识别结果
            if "words_result" in result and len(result["words_result"]) > 0:
                # 取第一个识别结果
                text = result["words_result"][0]["words"]
                # 清理识别结果（验证码通常是字母数字）
                text = self._clean_captcha_text(text)
                logger.info(f"百度OCR识别结果: {text}")
                return text
            else:
                logger.warning(f"百度OCR未识别到文字: {result.get('error_msg', '未知错误')}")
                return None
                
        except Exception as e:
            logger.error(f"百度OCR识别异常: {e}")
            return None
    
    def _clean_captcha_text(self, text: str) -> str:
        """
        清理验证码文字
        去除空格、特殊字符，只保留字母和数字
        """
        import re
        # 去除所有非字母数字字符
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text)
        return cleaned.lower()  # 转小写


class CaptchaRecognizer:
    """验证码识别器 - 支持百度OCR和手动输入"""
    
    def __init__(self, api_key: str = None, secret_key: str = None):
        """
        初始化验证码识别器
        
        Args:
            api_key: 百度OCR API Key（可选）
            secret_key: 百度OCR Secret Key（可选）
        """
        self.baidu_ocr = None
        if api_key and secret_key:
            self.baidu_ocr = BaiduOCR(api_key, secret_key)
            logger.info("已启用百度OCR自动识别验证码")
        else:
            logger.info("未配置百度OCR，将使用手动输入方式")
    
    def recognize(self, image_path: Path, use_manual_fallback: bool = True) -> Optional[str]:
        """
        识别验证码
        
        Args:
            image_path: 验证码图片路径
            use_manual_fallback: 是否允许手动输入作为备选
            
        Returns:
            识别结果，失败返回None
        """
        # 首先尝试百度OCR
        if self.baidu_ocr:
            result = self.baidu_ocr.recognize_image(image_path)
            if result and len(result) >= 3:  # 验证码通常至少3位
                logger.info(f"百度OCR自动识别成功: {result}")
                return result
            else:
                logger.warning("百度OCR识别结果无效，将尝试手动输入")
        
        # 如果百度OCR失败且允许手动输入
        if use_manual_fallback:
            return self._manual_input(image_path)
        
        return None
    
    def _manual_input(self, image_path: Path) -> Optional[str]:
        """手动输入验证码"""
        try:
            import tkinter as tk
            
            # 尝试打开图片
            try:
                import os
                os.startfile(str(image_path))
            except Exception as e:
                logger.debug(f"打开验证码图片失败: {e}")
            
            # 创建输入对话框
            root = tk.Tk()
            root.title("广发银行验证码输入")
            root.geometry("300x150")
            root.lift()
            root.attributes('-topmost', True)
            
            # 居中显示
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            x = (screen_width - 300) // 2
            y = (screen_height - 150) // 2
            root.geometry(f"300x150+{x}+{y}")
            
            tk.Label(root, text="请输入验证码：", font=("Arial", 12)).pack(pady=10)
            
            captcha_var = tk.StringVar()
            entry = tk.Entry(root, textvariable=captcha_var, font=("Arial", 14), width=15)
            entry.pack(pady=5)
            entry.focus()
            
            result = [None]
            
            def on_ok():
                result[0] = captcha_var.get().strip()
                root.destroy()
            
            def on_cancel():
                result[0] = None
                root.destroy()
            
            btn_frame = tk.Frame(root)
            btn_frame.pack(pady=10)
            
            tk.Button(btn_frame, text="确定", command=on_ok, width=10).pack(side=tk.LEFT, padx=5)
            tk.Button(btn_frame, text="取消", command=on_cancel, width=10).pack(side=tk.LEFT, padx=5)
            
            # 绑定回车键
            root.bind('<Return>', lambda e: on_ok())
            root.bind('<Escape>', lambda e: on_cancel())
            
            root.mainloop()
            
            return result[0]
            
        except ImportError:
            logger.warning("未找到 tkinter，使用命令行输入方式")
            return self._manual_input_cli(image_path)
    
    def _manual_input_cli(self, image_path: Path) -> Optional[str]:
        """命令行方式手动输入"""
        print("\n" + "=" * 60)
        print("需要输入验证码")
        print("=" * 60)
        print(f"验证码图片已保存至: {image_path}")
        print("请打开图片文件查看验证码")
        print("-" * 60)
        
        try:
            import os
            os.startfile(str(image_path))
            print("(已尝试自动打开图片)")
        except:
            pass
        
        print("-" * 60)
        
        try:
            captcha_code = input("请输入验证码（直接回车取消）: ").strip()
            return captcha_code if captcha_code else None
        except KeyboardInterrupt:
            return None


# 全局验证码识别器实例（延迟初始化）
_recognizer = None


def init_recognizer(api_key: str = None, secret_key: str = None):
    """初始化全局验证码识别器"""
    global _recognizer
    _recognizer = CaptchaRecognizer(api_key, secret_key)
    return _recognizer


def get_recognizer() -> Optional[CaptchaRecognizer]:
    """获取全局验证码识别器"""
    return _recognizer
