# -*- coding: utf-8 -*-
"""
运行记录模块 - 记录每次采集的详细结果
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class RunResult:
    """单次运行结果"""
    # 基本信息
    run_id: str = ""                          # 运行ID（时间戳）
    start_time: str = ""                      # 开始时间
    end_time: str = ""                        # 结束时间
    duration_seconds: float = 0.0             # 运行时长（秒）
    
    # 状态
    status: str = "running"                   # 状态: running/success/failed
    success: bool = False                     # 是否成功
    error_message: str = ""                   # 错误信息
    
    # 采集配置
    query_start_date: str = ""                # 查询开始日期
    query_end_date: str = ""                  # 查询结束日期
    statements_enabled: bool = True           # 流水是否启用
    receipts_enabled: bool = True             # 回单是否启用
    reconciliation_enabled: bool = True       # 电子对账单是否启用
    offline_import_enabled: bool = False      # 离线导入是否启用

    # 采集结果 - 流水
    statements_count: int = 0                 # 流水文件数量
    statements_files: List[str] = field(default_factory=list)  # 流水文件列表
    statements_no_data: bool = False          # 流水是否无数据

    # 采集结果 - 回单
    receipts_count: int = 0                   # 回单文件数量
    receipts_files: List[str] = field(default_factory=list)    # 回单文件列表
    receipts_no_data: bool = False            # 回单是否无数据

    # 采集结果 - 电子对账单
    reconciliation_count: int = 0             # 电子对账单文件数量
    reconciliation_files: List[str] = field(default_factory=list)  # 电子对账单文件列表
    reconciliation_no_data: bool = False      # 电子对账单是否无数据

    # 离线导入结果
    offline_import_file: str = ""             # 导入文件路径
    offline_import_success: bool = False      # 导入是否成功
    offline_import_recognized: bool = False   # 是否点击发起识别
    offline_import_error: str = ""            # 导入错误信息

    # 统计
    total_files: int = 0                      # 总文件数
    total_downloaded_bytes: int = 0           # 总下载字节数
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    def to_json(self, indent: int = 2) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save(self, output_dir: str = "./records") -> str:
        """保存记录到文件"""
        record_dir = Path(output_dir)
        record_dir.mkdir(parents=True, exist_ok=True)
        
        # 文件名格式: run_YYYYMMDD_HHMMSS.json
        if not self.run_id:
            self.run_id = datetime.now().strftime("%Y-%m-%d")
        
        file_path = record_dir / f"run_{self.run_id}.json"
        file_path.write_text(self.to_json(), encoding='utf-8')
        
        # 同时更新最新记录（方便快速读取）
        latest_path = record_dir / "latest.json"
        latest_path.write_text(self.to_json(), encoding='utf-8')
        
        return str(file_path)


class RunRecorder:
    """运行记录器"""
    
    def __init__(self, output_dir: str = "./records"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.result = RunResult()
        self.start_time = datetime.now()
        
        # 初始化基本信息
        self.result.run_id = self.start_time.strftime("%Y-%m-%d")
        self.result.start_time = self.start_time.strftime("%Y-%m-%d %H:%M:%S")
    
    def set_config(self, start_date: str, end_date: str,
                   statements_enabled: bool = True,
                   receipts_enabled: bool = True,
                   reconciliation_enabled: bool = True,
                   offline_import_enabled: bool = False):
        """设置采集配置"""
        self.result.query_start_date = start_date
        self.result.query_end_date = end_date
        self.result.statements_enabled = statements_enabled
        self.result.receipts_enabled = receipts_enabled
        self.result.reconciliation_enabled = reconciliation_enabled
        self.result.offline_import_enabled = offline_import_enabled

    def record_statements_result(self, files: List[str], no_data: bool = False):
        """记录流水采集结果"""
        self.result.statements_files = files
        self.result.statements_count = len(files)
        self.result.statements_no_data = no_data
        
        # 计算总大小
        total_bytes = 0
        for f in files:
            try:
                fp = Path(f)
                if fp.exists():
                    total_bytes += fp.stat().st_size
            except Exception:
                pass
        self.result.total_downloaded_bytes += total_bytes
    
    def record_receipts_result(self, files: List[str], no_data: bool = False):
        """记录回单采集结果"""
        self.result.receipts_files = files
        self.result.receipts_count = len(files)
        self.result.receipts_no_data = no_data

        # 计算总大小
        total_bytes = 0
        for f in files:
            try:
                fp = Path(f)
                if fp.exists():
                    total_bytes += fp.stat().st_size
            except Exception:
                pass
        self.result.total_downloaded_bytes += total_bytes

    def record_reconciliation_result(self, files: List[str], no_data: bool = False):
        """记录电子对账单采集结果"""
        self.result.reconciliation_files = files
        self.result.reconciliation_count = len(files)
        self.result.reconciliation_no_data = no_data

        # 计算总大小
        total_bytes = 0
        for f in files:
            try:
                fp = Path(f)
                if fp.exists():
                    total_bytes += fp.stat().st_size
            except Exception:
                pass
        self.result.total_downloaded_bytes += total_bytes

    def record_offline_import_result(self, file: str = "", success: bool = False,
                                     recognized: bool = False, error: str = ""):
        """记录离线导入结果"""
        self.result.offline_import_file = file
        self.result.offline_import_success = success
        self.result.offline_import_recognized = recognized
        self.result.offline_import_error = error

    def finish(self, success: bool = True, error_message: str = "") -> str:
        """完成记录"""
        end_time = datetime.now()
        self.result.end_time = end_time.strftime("%Y-%m-%d %H:%M:%S")
        self.result.duration_seconds = (end_time - self.start_time).total_seconds()
        self.result.success = success
        self.result.status = "success" if success else "failed"
        self.result.error_message = error_message
        
        # 计算总数
        self.result.total_files = (
            self.result.statements_count
            + self.result.receipts_count
            + self.result.reconciliation_count
            + (1 if self.result.offline_import_success else 0)
        )

        # 保存记录
        record_path = self.result.save(str(self.output_dir))
        
        # 打印摘要
        self._print_summary()
        
        return record_path
    
    def _print_summary(self):
        """打印运行摘要"""
        print("\n" + "=" * 60)
        print("运行结果摘要")
        print("=" * 60)
        print(f"运行ID: {self.result.run_id}")
        print(f"状态: {'√ 成功' if self.result.success else '× 失败'}")
        print(f"开始时间: {self.result.start_time}")
        print(f"结束时间: {self.result.end_time}")
        print(f"运行时长: {self.result.duration_seconds:.2f} 秒")
        print(f"查询日期: {self.result.query_start_date} 至 {self.result.query_end_date}")
        print("-" * 60)
        print(f"银行流水:")
        print(f"  文件数: {self.result.statements_count}")
        if self.result.statements_no_data:
            print(f"  状态: 无数据")
        for f in self.result.statements_files[:3]:  # 只显示前3个
            print(f"    - {Path(f).name}")
        if len(self.result.statements_files) > 3:
            print(f"    ... 还有 {len(self.result.statements_files) - 3} 个文件")
        print(f"电子回单:")
        print(f"  文件数: {self.result.receipts_count}")
        if self.result.receipts_no_data:
            print(f"  状态: 无数据")
        for f in self.result.receipts_files[:3]:
            print(f"    - {Path(f).name}")
        if len(self.result.receipts_files) > 3:
            print(f"    ... 还有 {len(self.result.receipts_files) - 3} 个文件")

        if self.result.reconciliation_enabled:
            print(f"电子对账单:")
            print(f"  文件数: {self.result.reconciliation_count}")
            if self.result.reconciliation_no_data:
                print(f"  状态: 无数据")
            for f in self.result.reconciliation_files[:3]:
                print(f"    - {Path(f).name}")
            if len(self.result.reconciliation_files) > 3:
                print(f"    ... 还有 {len(self.result.reconciliation_files) - 3} 个文件")

        if self.result.offline_import_enabled:
            print(f"离线明细导入:")
            print(f"  状态: {'√ 成功' if self.result.offline_import_success else '× 失败'}")
            if self.result.offline_import_file:
                print(f"  文件: {Path(self.result.offline_import_file).name}")
            if self.result.offline_import_recognized:
                print(f"  识别: 已发起")
            if self.result.offline_import_error:
                print(f"  错误: {self.result.offline_import_error}")

        print("-" * 60)
        print(f"总文件数: {self.result.total_files}")
        print(f"总大小: {self.result.total_downloaded_bytes / 1024:.2f} KB")
        print("=" * 60)
        print(f"记录文件: {self.output_dir}/run_{self.result.run_id}.json")
        print(f"最新记录: {self.output_dir}/latest.json")
        print("=" * 60 + "\n")


def load_latest_record(output_dir: str = "./records") -> Optional[RunResult]:
    """加载最新的运行记录"""
    latest_path = Path(output_dir) / "latest.json"
    if not latest_path.exists():
        return None
    
    try:
        data = json.loads(latest_path.read_text(encoding='utf-8'))
        return RunResult(**data)
    except Exception as e:
        print(f"加载记录失败: {e}")
        return None


def is_last_run_success(output_dir: str = "./records") -> bool:
    """检查上一次运行是否成功"""
    record = load_latest_record(output_dir)
    if not record:
        return False
    return record.success and record.status == "success"


def get_last_run_files(output_dir: str = "./records") -> List[str]:
    """获取上一次运行的所有文件"""
    record = load_latest_record(output_dir)
    if not record:
        return []
    return record.statements_files + record.receipts_files
