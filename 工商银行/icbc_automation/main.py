# -*- coding: utf-8 -*-
"""
工商企业网银自动采集主程序
"""
import sys
import time
import argparse
from pathlib import Path
from typing import List

from logger import setup_logger
from config_loader import load_config, Config
from browser import ICBCBrowser
from statement_collector import StatementCollector
from receipt_collector import ReceiptCollector
from run_recorder import RunRecorder

logger = setup_logger()


class ICBCAutomation:
    """工商网银自动化采集主类"""

    def __init__(self, config_path: str = None):
        self.config: Config = load_config(config_path)

        log_path = self.config.runtime.log_path
        if not log_path:
            from datetime import datetime
            today = datetime.now().strftime("%Y%m%d")
            log_path = f"./logs/icbc_automation_{today}.log"

        global logger
        logger = setup_logger(
            self.config.runtime.log_level,
            log_path,
            self.config.runtime.log_retention_days
        )
        self.browser: ICBCBrowser = None

        # 机柜控制器：配置了才自动插拔 U盾；manual_mode=true 表示手动插好
        self.cabinet_controller = None
        cabinet = self.config.cabinet
        if cabinet.type != "mock" and not cabinet.manual_mode:
            try:
                from cabinet_controller import create_cabinet_controller

                self.cabinet_controller = create_cabinet_controller(cabinet)
                if self.cabinet_controller:
                    logger.info(
                        f"机柜控制器已初始化: {cabinet.type} ter={cabinet.ter_id} port={cabinet.port} user={cabinet.user}"
                    )
                else:
                    logger.warning("机柜控制器初始化失败")
            except Exception as exc:
                logger.error(f"机柜控制器初始化异常: {exc}")

    @staticmethod
    def _no_data(files: List[str]) -> bool:
        """判断采集结果是否表示无数据"""
        if not files:
            return True
        if files == ["当前查询结果为空"]:
            return True
        return all("_无数据_" in Path(f).name for f in files if f)

    def run(self, collect_statements: bool = True, collect_receipts: bool = True) -> dict:
        """执行采集任务"""
        collect_statements = collect_statements and self.config.collection.statements.enabled
        collect_receipts = collect_receipts and self.config.collection.receipts.enabled

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

        logger.info("=" * 50)
        logger.info("采集任务计划:")
        logger.info(f"  - 银行流水: {'启用' if collect_statements else '跳过'}")
        logger.info(f"  - 电子回单: {'启用' if collect_receipts else '跳过'}")
        logger.info("=" * 50)

        for attempt in range(max_retries):
            try:
                logger.info(f"开始采集任务（尝试 {attempt + 1}/{max_retries}）...")

                self.browser = ICBCBrowser(self.config.browser, self.config.credentials)

                if self.cabinet_controller:
                    if not self.cabinet_controller.connect():
                        raise Exception("机柜插入 U盾失败")
                    logger.info("机柜已插入 U盾，等待 10s 让系统识别")
                    time.sleep(10)

                self.browser.start()
                self.browser.navigate_to_login()

                logger.info("=" * 50)
                logger.info("步骤1: U盾登录（证书选择 + U盾密码）...")

                login_success = False
                try:
                    login_success = self.browser.login()
                except Exception as e:
                    logger.error(f"自动登录异常: {e}")

                if not login_success:
                    raise Exception("自动登录失败")
                logger.info("登录成功！")

                if collect_statements:
                    logger.info("=" * 50)
                    logger.info("开始采集银行流水...")
                    collector = StatementCollector(self.browser.page, self.config.collection.statements)
                    statement_files = collector.collect()
                    results["statements"] = statement_files
                    recorder.record_statements_result(
                        files=[f for f in statement_files if f and not f.startswith("当前")],
                        no_data=self._no_data(statement_files)
                    )

                if collect_receipts:
                    logger.info("=" * 50)
                    logger.info("开始采集电子回单...")
                    from dataclasses import asdict
                    statement_dates = {
                        'start_date': self.config.collection.statements.start_date or '',
                        'end_date': self.config.collection.statements.end_date or ''
                    }
                    collector = ReceiptCollector(
                        self.browser.page,
                        asdict(self.config.collection.receipts),
                        statement_dates
                    )
                    receipt_files = collector.collect()
                    results["receipts"] = receipt_files
                    recorder.record_receipts_result(
                        files=[f for f in receipt_files if f],
                        no_data=self._no_data(receipt_files)
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
                    results["record_file"] = recorder.finish(success=False, error_message=str(e))

            finally:
                if self.browser:
                    self.browser.close()
                    self.browser = None
                if self.cabinet_controller:
                    try:
                        self.cabinet_controller.close()
                        logger.info("机柜已拔出 U盾")
                    except Exception as exc:
                        logger.warning(f"机柜拔出 U盾失败: {exc}")

        if results["success"]:
            results["record_file"] = recorder.finish(success=True)

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

    print("=" * 60)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="工商企业网银自动采集工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                       # 采集流水和回单
  python main.py -s                    # 仅采集流水
  python main.py -r                    # 仅采集回单
  python main.py -c custom_config.ini  # 使用自定义配置
        """
    )

    parser.add_argument("-c", "--config", default=None,
                        help="配置文件路径 (默认: 自动查找 config.ini / config.json / config.yaml)")
    parser.add_argument("-s", "--statements-only", action="store_true", help="仅采集银行流水")
    parser.add_argument("-r", "--receipts-only", action="store_true", help="仅采集电子回单")
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")

    args = parser.parse_args()

    try:
        automation = ICBCAutomation(args.config)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("请创建 config.ini 或 config.json 配置文件")
        sys.exit(1)

    if args.statements_only:
        results = automation.run(collect_statements=True, collect_receipts=False)
    elif args.receipts_only:
        results = automation.run(collect_statements=False, collect_receipts=True)
    else:
        results = automation.run(collect_statements=True, collect_receipts=True)

    print_results(results)

    from loguru import logger as loguru_logger
    loguru_logger.complete()

    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()
