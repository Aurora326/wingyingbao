# -*- coding: utf-8 -*-
"""
广发银行企业网银自动采集主程序
"""
import sys
from pathlib import Path
# 将项目根目录加入 sys.path，确保能引用 common 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import time
import argparse
from typing import List

from logger import setup_logger
from config_loader import load_config, Config
from browser import CGBBrowser
from statement_collector import StatementCollector
from receipt_collector import ReceiptCollector
from run_recorder import RunRecorder

logger = setup_logger()


class CGBAutomation:
    """广发银行网银自动化采集主类"""
    
    def __init__(self, config_path: str = None):
        self.config: Config = load_config(config_path)
        # 使用配置的日志设置重新初始化logger
        log_path = self.config.runtime.log_path
        if not log_path:
            from datetime import datetime
            today = datetime.now().strftime("%Y%m%d")
            log_path = f"./logs/cgb_automation_{today}.log"
        global logger
        logger = setup_logger(
            self.config.runtime.log_level, 
            log_path,
            self.config.runtime.log_retention_days
        )
        self.browser: CGBBrowser = None

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
        
        # 打印机柜配置
        cabinet_config = self.config.get_cabinet_config()
        logger.info("-" * 50)
        logger.info("机柜配置信息:")
        logger.info(f"  - 控制器类型: {cabinet_config.type}")
        logger.info(f"  - 手动模式: {'是' if cabinet_config.manual_mode else '否'}")
        if cabinet_config.type == 'mb':
            logger.info(f"  - 机柜编号(ter_id): {cabinet_config.ter_id or '(未设置)'}")
            logger.info(f"  - 端口号(port): {cabinet_config.port}")
            logger.info(f"  - 用户名(user): {cabinet_config.user}")
            logger.info(f"  - mb.exe路径: {cabinet_config.mb_path}")
        elif cabinet_config.type == 'gf3':
            logger.info(f"  - 产品ID: {cabinet_config.product_id}")
            logger.info(f"  - gf3.exe路径: {cabinet_config.gf3_path}")
        logger.info("=" * 50)
        
        for attempt in range(max_retries):
            try:
                logger.info(f"开始采集任务（尝试 {attempt + 1}/{max_retries}）...")
                
                # 步骤0: 先插入 U 盾（如果启用机柜）
                cabinet_config = self.config.get_cabinet_config()
                if cabinet_config.type != "mock" and not cabinet_config.manual_mode:
                    logger.info("=" * 50)
                    logger.info("步骤0: 插入 U 盾...")
                    from cabinet_controller import create_cabinet_controller
                    controller = create_cabinet_controller(cabinet_config)
                    if controller:
                        ukey_result = controller.connect()
                        if ukey_result:
                            logger.info("✓ U 盾插入命令执行成功")
                        else:
                            logger.warning("✗ U 盾插入命令执行失败")
                        # 不管成功与否，等待10秒让系统识别U盾
                        logger.info("等待 10 秒让系统识别 U 盾...")
                        time.sleep(10)
                    logger.info("=" * 50)
                
                # 启动浏览器
                self.browser = CGBBrowser(self.config.browser, self.config.credentials, cabinet_config=cabinet_config)
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
                    
                    # 尝试登录（包含用户名、密码、验证码）
                    if self.config.credentials.username and self.config.credentials.password:
                        logger.info("尝试使用用户名密码登录...")
                        try:
                            login_success = self.browser.login()
                            if login_success:
                                time.sleep(3)
                                if self.browser._check_login_success():
                                    logger.info("✓ 登录成功！")
                                    login_success = True
                        except Exception as e:
                            logger.error(f"登录异常: {e}")
                
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
                
                # 登录成功后，处理对账单弹窗
                logger.info("检查并关闭对账单弹窗...")
                self.browser._handle_reconciliation_popup()
                
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
                    # 清理机柜（拔出U盾）
                    if hasattr(self.browser, 'cabinet_controller') and self.browser.cabinet_controller:
                        logger.info("步骤9: 拔出 U 盾...")
                        try:
                            self.browser.cabinet_controller.close()
                            logger.info("✓ U 盾已拔出")
                        except Exception as e:
                            logger.warning(f"拔出 U 盾时出错: {e}")
                    self.browser.close(kill_all_chrome=True)
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
        help="配置文件路径 (默认: 自动查找 config.ini 或 config.yaml)"
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
        print("请创建 config.ini 或 config.yaml 配置文件")
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
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户手动中断程序")
        try:
            from loguru import logger as loguru_logger
            loguru_logger.complete()
        except Exception:
            pass
        sys.exit(130)
