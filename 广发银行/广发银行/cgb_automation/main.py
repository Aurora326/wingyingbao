# -*- coding: utf-8 -*-
"""
广发银行企业网银自动采集主程序
"""
import sys
import time
import argparse
from pathlib import Path
from typing import List

from logger import setup_logger
from config_loader import load_config, Config
from browser import CGBBrowser
from statement_collector import StatementCollector
from receipt_collector import ReceiptCollector
from run_recorder import RunRecorder
from captcha_recognizer import init_recognizer

logger = setup_logger()


class CGBAutomation:
    """广发银行网银自动化采集主类"""
    
    def __init__(self, config_path: str = None):
        self.config: Config = load_config(config_path)
        # 使用配置的日志设置重新初始化logger
        # 如果没有配置log_path，使用按日期命名的路径
        log_path = self.config.runtime.log_path
        if not log_path:
            from datetime import datetime
            today = datetime.now().strftime("%Y%m%d")
            log_path = f"./logs/cgb_automation_{today}.log"
        global logger
        # 传递日志保留天数配置
        logger = setup_logger(
            self.config.runtime.log_level, 
            log_path,
            self.config.runtime.log_retention_days
        )
        self.browser: CGBBrowser = None
        
        # 初始化验证码识别器（如果配置了百度OCR）
        self.captcha_recognizer = None
        if self.config.ocr.enabled and self.config.ocr.api_key and self.config.ocr.secret_key:
            self.captcha_recognizer = init_recognizer(
                api_key=self.config.ocr.api_key,
                secret_key=self.config.ocr.secret_key
            )
            logger.info("已启用百度OCR验证码自动识别")
        else:
            # 创建不带API Key的识别器（纯手动模式）
            self.captcha_recognizer = init_recognizer()

    def _try_auto_enter_pin(self):
        """尝试自动输入 PIN（主线程中执行）"""
        pin = self.config.credentials.pin
        logger.info(f"[_try_auto_enter_pin] 被调用，配置的 PIN: {repr(pin)}")
        
        if not pin:
            logger.warning("未配置 PIN，跳过自动输入")
            return
            
        logger.info(f"即将自动输入 PIN: {pin}")
        logger.info("如果 PIN 不正确，请立即点击密码框按 Ctrl+C 取消自动输入")
        
        # 等待页面完全加载
        logger.info("等待 3 秒让页面加载完成...")
        time.sleep(3)
        
        # 先尝试 AiKeyboard 方法
        logger.info("尝试使用 AiKeyboard 输入 PIN...")
        try:
            result = self.browser.auto_enter_pin_by_clicking(pin)
            if result:
                logger.info("AiKeyboard 输入 PIN 成功")
                return
            else:
                logger.warning("AiKeyboard 输入返回失败")
        except Exception as e:
            logger.error(f"AiKeyboard 输入失败: {e}")
        
        # 备选：使用 Playwright 方法
        logger.info("尝试使用 Playwright 输入 PIN...")
        try:
            result = self.browser.auto_enter_pin(pin)
            if result:
                logger.info("Playwright 输入 PIN 成功")
            else:
                logger.warning("Playwright 输入返回失败")
        except Exception as e:
            logger.error(f"Playwright 输入失败: {e}")
        
    def run(self, collect_statements: bool = True, collect_receipts: bool = True) -> dict:
        """
        执行采集任务
        
        Args:
            collect_statements: 是否采集流水（命令行参数，会被配置文件覆盖）
            collect_receipts: 是否采集电子回单（命令行参数，会被配置文件覆盖）
            
        Returns:
            采集结果统计
        """
        # 根据配置文件覆盖命令行参数
        collect_statements = collect_statements and self.config.collection.statements.enabled
        collect_receipts = collect_receipts and self.config.collection.receipts.enabled
        
        # 初始化运行记录器
        recorder = RunRecorder()
        recorder.set_config(
            start_date=self.config.collection.statements.start_date or '',
            end_date=self.config.collection.statements.end_date or '',
            statements_enabled=collect_statements,
            receipts_enabled=collect_receipts
        )
        
        results = {
            "statements": [],
            "receipts": [],
            "success": False,
            "error": None,
            "record_file": ""
        }
        
        max_retries = self.config.runtime.max_retries
        retry_delay = self.config.runtime.retry_delay
        
        # 打印采集计划
        logger.info("=" * 50)
        logger.info("采集任务计划:")
        logger.info(f"  - 银行流水: {'启用' if collect_statements else '跳过'}")
        logger.info(f"  - 电子回单: {'启用' if collect_receipts else '跳过'}")
        logger.info("=" * 50)
        
        for attempt in range(max_retries):
            try:
                logger.info(f"开始采集任务（尝试 {attempt + 1}/{max_retries}）...")
                
                # 步骤0: 先插入 U 盾（如果启用MB机柜）
                if self.config.mb and self.config.mb.enabled:
                    logger.info("=" * 50)
                    logger.info("步骤0: 插入 U 盾...")
                    from mb_robot import MBRobot
                    mb_robot = MBRobot(self.config.mb)
                    ukey_result = mb_robot.load_port()
                    if ukey_result:
                        logger.info("✓ U 盾插入成功，等待 10 秒让系统识别...")
                        time.sleep(10)
                    else:
                        logger.warning("✗ U 盾插入失败，继续执行...")
                    logger.info("=" * 50)
                
                # 启动浏览器
                self.browser = CGBBrowser(self.config.browser, self.config.credentials, self.config.mb)
                self.browser.start()
                
                # 导航到登录页
                self.browser.navigate_to_login()
                
                # ========== 步骤1: 检查当前登录方式 ==========
                logger.info("=" * 50)
                logger.info("步骤1: 检查登录方式...")
                
                # 先检查是否已经登录成功
                login_success = False
                start_check = time.time()
                while time.time() - start_check < 10:
                    if self.browser._check_login_success():
                        login_success = True
                        logger.info("✓ 检测到已登录成功！")
                        break
                    time.sleep(1)
                
                # 如果未登录成功，尝试自动登录
                if not login_success:
                    logger.info("未检测到登录状态，尝试自动登录...")
                    
                    # 尝试 Key盾登录（包含用户名、密码、验证码）
                    if self.config.credentials.username and self.config.credentials.password:
                        logger.info("尝试使用 Key盾登录...")
                        try:
                            login_success = self.browser.login(recognizer=self.captcha_recognizer)
                            if login_success:
                                time.sleep(3)
                                if self.browser._check_login_success():
                                    logger.info("✓ Key盾登录成功！")
                                    login_success = True
                        except Exception as e:
                            logger.error(f"Key盾登录异常: {e}")
                
                # 如果自动登录失败，进入半自动模式等待用户操作
                if not login_success:
                    logger.info("=" * 50)
                    logger.info("自动登录未成功，进入半自动模式")
                    logger.info("请手动完成登录：")
                    logger.info("  1. 输入用户名、密码、验证码")
                    logger.info("  2. 点击登录按钮")
                    logger.info("  3. 如需UKey验证，请插入UKey并输入密码")
                    logger.info("  4. 程序将自动检测登录状态")
                    logger.info("=" * 50)
                    
                    # 等待用户完成登录（自动检测）
                    if not self.browser.wait_for_manual_login():
                        raise Exception("登录取消")
                    login_success = True
                else:
                    logger.info("登录成功！")
                
                # 采集流水
                if collect_statements:
                    logger.info("=" * 50)
                    logger.info("开始采集银行流水...")
                    collector = StatementCollector(self.browser.page, self.config.collection.statements)
                    statement_files = collector.collect()
                    results["statements"] = statement_files
                    # 判断是否有数据（文件列表不为空且不是"当前查询结果为空"字符串）
                    has_data = statement_files and statement_files != ["当前查询结果为空"]
                    recorder.record_statements_result(
                        files=[f for f in statement_files if f and not f.startswith("当前")],
                        no_data=not has_data
                    )
                
                # 采集电子回单
                if collect_receipts:
                    logger.info("=" * 50)
                    logger.info("开始采集电子回单...")
                    from dataclasses import asdict
                    statement_dates = {
                        'start_date': self.config.collection.statements.start_date or '',
                        'end_date': self.config.collection.statements.end_date or ''
                    }
                    collector = ReceiptCollector(self.browser.page, asdict(self.config.collection.receipts), statement_dates)
                    receipt_files = collector.collect()
                    results["receipts"] = receipt_files
                    # 判断是否有数据
                    has_data = receipt_files and len(receipt_files) > 0
                    recorder.record_receipts_result(
                        files=[f for f in receipt_files if f],
                        no_data=not has_data
                    )
                
                results["success"] = True
                logger.info("=" * 50)
                logger.info("采集任务完成！")
                break
                
            except Exception as e:
                logger.error(f"采集任务出错: {e}")
                results["error"] = str(e)
                
                if attempt < max_retries - 1:
                    logger.info(f"{retry_delay}秒后重试...")
                    time.sleep(retry_delay)
                else:
                    logger.error("已达到最大重试次数，任务失败")
                    # 记录失败结果
                    record_file = recorder.finish(success=False, error_message=str(e))
                    results["record_file"] = record_file
            
            finally:
                if self.browser:
                    # 清理MB机柜（拔出UKey）
                    if hasattr(self.browser, 'mb_robot') and self.browser.mb_robot:
                        logger.info("清理MB机柜...")
                        self.browser.mb_robot.cleanup()
                    self.browser.close()
                    self.browser = None
        
        # 如果成功，记录结果
        if results["success"]:
            record_file = recorder.finish(success=True)
            results["record_file"] = record_file
        
        return results
    
    def run_statements_only(self) -> List[str]:
        """仅采集流水"""
        results = self.run(collect_statements=True, collect_receipts=False)
        return results["statements"]
    
    def run_receipts_only(self) -> List[str]:
        """仅采集电子回单"""
        results = self.run(collect_statements=False, collect_receipts=True)
        return results["receipts"]


def print_results(results: dict):
    """打印采集结果"""
    print("\n" + "=" * 60)
    print("采集结果汇总")
    print("=" * 60)
    
    if results["success"]:
        print(f"✓ 任务状态: 成功")
    else:
        print(f"✗ 任务状态: 失败")
        print(f"  错误信息: {results['error']}")
    
    print(f"\n银行流水:")
    print(f"  下载数量: {len(results['statements'])}")
    for f in results["statements"]:
        print(f"    - {f}")
    
    print(f"\n电子回单:")
    print(f"  下载数量: {len(results['receipts'])}")
    for f in results["receipts"]:
        print(f"    - {f}")
    
    print("=" * 60)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="广发银行企业网银自动采集工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 采集流水和回单
  python main.py -s                 # 仅采集流水
  python main.py -r                 # 仅采集回单
  python main.py -c custom_config.yaml  # 使用自定义配置
        """
    )
    
    parser.add_argument(
        "-c", "--config",
        default=None,
        help="配置文件路径 (默认: 自动查找 config.json 或 config.yaml)"
    )
    
    parser.add_argument(
        "-s", "--statements-only",
        action="store_true",
        help="仅采集银行流水"
    )
    
    parser.add_argument(
        "-r", "--receipts-only",
        action="store_true",
        help="仅采集电子回单"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0"
    )
    
    args = parser.parse_args()
    
    # 创建自动化实例（自动查找配置文件）
    try:
        automation = CGBAutomation(args.config)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("请创建 config.json 或 config.yaml 配置文件")
        sys.exit(1)
    
    # 确定采集类型
    if args.statements_only:
        results = automation.run(collect_statements=True, collect_receipts=False)
    elif args.receipts_only:
        results = automation.run(collect_statements=False, collect_receipts=True)
    else:
        results = automation.run(collect_statements=True, collect_receipts=True)
    
    # 打印结果
    print_results(results)
    
    # 确保所有日志都被写入文件
    from loguru import logger as loguru_logger
    loguru_logger.complete()
    
    # 返回退出码
    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()
