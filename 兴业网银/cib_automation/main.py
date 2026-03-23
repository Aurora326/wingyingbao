# -*- coding: utf-8 -*-
"""
兴业企业网银自动采集主程序
"""
import sys
import time
import argparse
from pathlib import Path
from typing import List

from logger import setup_logger
from config_loader import load_config, Config
from browser import CIBBrowser
from statement_collector import StatementCollector
from receipt_collector import ReceiptCollector

logger = setup_logger()


class CIBAutomation:
    """兴业网银自动化采集主类"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config: Config = load_config(config_path)
        # 使用配置的日志设置重新初始化logger
        global logger
        logger = setup_logger(self.config.runtime.log_level, self.config.runtime.log_path)
        self.browser: CIBBrowser = None

    def _try_auto_enter_pin(self):
        """尝试自动输入 PIN（后台线程）"""
        pin = self.config.credentials.pin
        logger.info(f"[_try_auto_enter_pin] 被调用，配置的 PIN: {repr(pin)}")
        
        if not pin:
            logger.warning("未配置 PIN，跳过自动输入")
            return
            
        logger.info(f"即将自动输入 PIN: {pin}")
        logger.info("如果 PIN 不正确，请立即点击密码框按 Ctrl+C 取消自动输入")
        
        # 等待网盾密码弹窗出现
        logger.info("等待 5 秒让网盾弹窗出现...")
        time.sleep(5)
        
        # 先尝试 pyautogui 方法
        logger.info("尝试使用 pyautogui 输入 PIN...")
        try:
            result = self.browser.auto_enter_pin_by_clicking(pin)
            if result:
                logger.info("pyautogui 输入 PIN 成功")
                return
            else:
                logger.warning("pyautogui 输入返回失败")
        except Exception as e:
            logger.error(f"pyautogui 输入失败: {e}")
        
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
            collect_statements: 是否采集流水
            collect_receipts: 是否采集电子回单
            
        Returns:
            采集结果统计
        """
        results = {
            "statements": [],
            "receipts": [],
            "success": False,
            "error": None
        }
        
        max_retries = self.config.runtime.max_retries
        retry_delay = self.config.runtime.retry_delay
        
        for attempt in range(max_retries):
            try:
                logger.info(f"开始采集任务（尝试 {attempt + 1}/{max_retries}）...")
                
                # 启动浏览器
                self.browser = CIBBrowser(self.config.browser, self.config.credentials)
                self.browser.start()
                
                # 导航到登录页
                self.browser.navigate_to_login()
                
                # ========== 步骤1: 先输入网盾密码（PIN）==========
                pin_value = self.config.credentials.pin
                if pin_value:
                    logger.info("=" * 50)
                    logger.info("步骤1: 自动输入网盾密码（PIN）...")
                    import threading
                    pin_thread = threading.Thread(
                        target=self._try_auto_enter_pin,
                        daemon=True
                    )
                    pin_thread.start()
                    logger.info("PIN 输入线程已启动，等待网盾密码输入完成...")
                    # 等待 PIN 输入完成（网盾密码弹窗通常在页面加载后不久出现）
                    time.sleep(8)
                else:
                    logger.info("未配置 PIN，跳过网盾密码输入")
                
                # ========== 步骤2: 登录网银 ==========
                logger.info("=" * 50)
                logger.info("步骤2: 登录网银...")
                
                # 尝试自动登录
                login_success = False
                try:
                    login_success = self.browser.login()
                except Exception as e:
                    logger.error(f"自动登录异常: {e}")
                
                # 如果自动登录失败，进入半自动模式等待用户操作
                if not login_success:
                    logger.info("自动登录未成功，进入半自动模式等待用户完成登录...")
                    
                    # 等待用户完成登录（自动检测）
                    if not self.browser.wait_for_manual_login():
                        raise Exception("登录取消")
                else:
                    logger.info("自动登录成功！")
                
                # 采集流水
                if collect_statements:
                    logger.info("=" * 50)
                    logger.info("开始采集银行流水...")
                    collector = StatementCollector(self.browser.page, self.config.collection.statements)
                    results["statements"] = collector.collect()
                
                # 采集电子回单
                if collect_receipts:
                    logger.info("=" * 50)
                    logger.info("开始采集电子回单...")
                    # 传递流水的日期配置给回单采集器
                    from dataclasses import asdict
                    statement_dates = {
                        'start_date': self.config.collection.statements.start_date or '',
                        'end_date': self.config.collection.statements.end_date or ''
                    }
                    collector = ReceiptCollector(self.browser.page, asdict(self.config.collection.receipts), statement_dates)
                    results["receipts"] = collector.collect()
                
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
            
            finally:
                if self.browser:
                    self.browser.close()
                    self.browser = None
        
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
        description="兴业企业网银自动采集工具",
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
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
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
    
    # 检查配置文件
    if not Path(args.config).exists():
        print(f"错误: 配置文件不存在: {args.config}")
        print("请复制 config.yaml.example 为 config.yaml 并进行配置")
        sys.exit(1)
    
    # 创建自动化实例
    automation = CIBAutomation(args.config)
    
    # 确定采集类型
    if args.statements_only:
        results = automation.run(collect_statements=True, collect_receipts=False)
    elif args.receipts_only:
        results = automation.run(collect_statements=False, collect_receipts=True)
    else:
        results = automation.run(collect_statements=True, collect_receipts=True)
    
    # 打印结果
    print_results(results)
    
    # 返回退出码
    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()
