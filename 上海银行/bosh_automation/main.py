# -*- coding: utf-8 -*-
"""
上海银行企业网银自动采集主程序（骨架）

结构照搬建设银行：机柜插盾 → 启动浏览器 → 登录 → 采集流水 → 拔盾。
目前只有流水采集，页面元素待调试后补齐（见 statement_collector.py / login_manager.py）。
"""
import sys
import time
import argparse
from pathlib import Path

# 将项目根目录加入 sys.path，确保能引用 common 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from logger import setup_logger
from config_loader import load_config, Config
from browser import BOSCBrowser
from statement_collector import StatementCollector
from receipt_collector import ReceiptCollector
from payment_creator import PaymentCreator

logger = setup_logger()


def _browser_pids() -> set:
    """当前在跑的 Chrome/Edge 进程号。"""
    import csv
    import io
    import subprocess

    pids = set()
    for name in ("chrome.exe", "msedge.exe"):
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
        )
        for row in csv.reader(io.StringIO(result.stdout or "")):
            if len(row) >= 2 and row[1].strip().isdigit():
                pids.add(int(row[1]))
    return pids


def _close_new_browsers(before: set) -> None:
    """插盾后银行驱动会自动开一个本机浏览器，关掉它，避免遮挡和抢焦点。"""
    import subprocess

    for pid in _browser_pids() - before:
        logger.info(f"关闭插盾后自动打开的浏览器进程 {pid}")
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


def _close_bank_site_windows() -> None:
    """关闭银行官网窗口（Edge）。它可能是上一次运行留下的，进程不"新"但照样抢焦点。"""
    import subprocess

    script = (
        "Get-Process msedge -ErrorAction SilentlyContinue | "
        "Where-Object { $_.MainWindowTitle -match '上海银行' } | "
        "ForEach-Object { $_.CloseMainWindow() | Out-Null }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=20,
    )


class BOSCAutomation:
    """上海银行网银自动化采集主类"""

    def __init__(self, config_path: str = None):
        self.config: Config = load_config(config_path)
        log_path = self.config.runtime.log_path
        if not log_path:
            from datetime import datetime
            today = datetime.now().strftime("%Y%m%d")
            log_path = f"./logs/bosh_automation_{today}.log"
        global logger
        logger = setup_logger(
            self.config.runtime.log_level,
            log_path,
            self.config.runtime.log_retention_days,
        )
        self.browser: BOSCBrowser = None

    def run(self, collect_statements: bool = True, collect_receipts: bool = False, collect_payments: bool = False) -> dict:
        """执行采集任务，返回与建行一致的结果结构。"""
        collect_statements = collect_statements and self.config.collection.statements.enabled
        collect_receipts = collect_receipts and self.config.collection.receipts.enabled
        collect_payments = collect_payments and self.config.collection.payments.enabled

        results = {
            "statements": [],
            "receipts": [],
            "payments": {},
            "success": False,
            "error": None,
        }

        max_retries = self.config.runtime.max_retries
        retry_delay = self.config.runtime.retry_delay

        for attempt in range(max_retries):
            try:
                logger.info(f"开始采集任务（尝试 {attempt + 1}/{max_retries}）...")

                cabinet_config = self.config.get_cabinet_config()
                if cabinet_config.type != "mock" and not cabinet_config.manual_mode:
                    logger.info("步骤0: 插入 U 盾...")
                    pids_before = _browser_pids()
                    from cabinet_controller import create_cabinet_controller
                    controller = create_cabinet_controller(cabinet_config)
                    if controller:
                        if controller.connect():
                            logger.info("✓ U 盾插入命令执行成功")
                        else:
                            logger.warning("✗ U 盾插入命令执行失败")
                        logger.info("等待 10 秒让系统识别 U 盾...")
                        time.sleep(10)
                        _close_new_browsers(pids_before)
                        _close_bank_site_windows()

                self.browser = BOSCBrowser(
                    self.config.browser,
                    self.config.credentials,
                    cabinet_config=cabinet_config,
                )
                self.browser.start()
                self.browser.navigate_to_login()

                login_success = self.browser._check_login_success()
                if not login_success:
                    logger.info("未检测到登录状态，尝试自动登录...")
                    login_success = self.browser.login()

                if not login_success:
                    raise Exception("自动登录失败")

                logger.info("登录成功！")

                if collect_statements:
                    logger.info("开始采集银行流水...")
                    collector = StatementCollector(self.browser.page, self.config.collection.statements)
                    results["statements"] = collector.collect()

                if collect_receipts:
                    logger.info("开始采集电子回单...")
                    collector = ReceiptCollector(self.browser.page, self.config.collection.receipts)
                    results["receipts"] = collector.collect()

                if collect_payments:
                    logger.info("开始制单...")
                    creator = PaymentCreator(
                        self.browser.page,
                        self.config.collection.payments,
                        cabinet_controller=getattr(self.browser, "cabinet_controller", None),
                    )
                    results["payments"] = creator.create()

                results["success"] = True
                logger.info("采集任务完成！")
                break

            except Exception as e:
                logger.error(f"采集任务出错: {e}")
                results["error"] = str(e)
                pin_error = "U盾PIN码错误" in str(e)
                if pin_error:
                    logger.error("PIN 码错误，停止重试，避免把 U 盾锁住")
                if attempt < max_retries - 1 and not pin_error:
                    logger.info(f"{retry_delay}秒后重试...")
                    time.sleep(retry_delay)
                else:
                    logger.error("已达到最大重试次数，任务失败")

            finally:
                if self.browser:
                    if getattr(self.browser, "cabinet_controller", None):
                        logger.info("拔出 U 盾...")
                        try:
                            self.browser.cabinet_controller.close()
                            logger.info("✓ U 盾已拔出")
                        except Exception as e:
                            logger.warning(f"拔出 U 盾时出错: {e}")
                    try:
                        self.browser.close()
                    except Exception as e:
                        logger.warning(f"关闭浏览器时出错: {e}")
                    self.browser = None

        return results


def print_results(results: dict):
    print("\n" + "=" * 60)
    print("采集结果汇总")
    print("=" * 60)
    if results["success"]:
        print("✓ 任务状态: 成功")
    else:
        print("✗ 任务状态: 失败")
        print(f"  错误信息: {results['error']}")
    print("\n银行流水:")
    print(f"  下载数量: {len(results['statements'])}")
    for f in results["statements"]:
        print(f"    - {f}")
    print("\n电子回单:")
    print(f"  下载数量: {len(results['receipts'])}")
    for f in results["receipts"]:
        print(f"    - {f}")

    if results["payments"]:
        print("\n制单:")
        for key, value in results["payments"].items():
            print(f"  {key}: {value}")


def main():
    parser = argparse.ArgumentParser(
        description="上海银行企业网银自动采集工具（骨架）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 采集流水和回单
  python main.py -s                 # 仅采集流水
  python main.py -r                 # 仅采集回单
  python main.py -c custom.ini      # 使用自定义配置
        """,
    )
    parser.add_argument("-c", "--config", default=None, help="配置文件路径 (默认: config.ini)")
    parser.add_argument("-s", "--statements-only", action="store_true", help="仅采集银行流水")
    parser.add_argument("-r", "--receipts-only", action="store_true", help="仅采集电子回单")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    args = parser.parse_args()

    try:
        automation = BOSCAutomation(args.config)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("请创建 config.ini 配置文件")
        sys.exit(1)

    results = automation.run(
        collect_statements=not args.receipts_only,
        collect_receipts=args.receipts_only or not args.statements_only,
    )
    print_results(results)

    try:
        from loguru import logger as loguru_logger
        loguru_logger.complete()
    except Exception:
        pass

    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户手动中断程序")
        sys.exit(130)
