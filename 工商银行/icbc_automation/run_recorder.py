# -*- coding: utf-8 -*-
"""
运行记录模块 - 记录每次采集的详细结果
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class RunResult:
    """单次运行结果"""
    run_id: str = ""
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0

    status: str = "running"
    success: bool = False
    error_message: str = ""

    query_start_date: str = ""
    query_end_date: str = ""
    statements_enabled: bool = True
    receipts_enabled: bool = True

    statements_count: int = 0
    statements_files: List[str] = field(default_factory=list)
    statements_no_data: bool = False

    receipts_count: int = 0
    receipts_files: List[str] = field(default_factory=list)
    receipts_no_data: bool = False

    total_files: int = 0
    total_downloaded_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, output_dir: str = "./records") -> str:
        record_dir = Path(output_dir)
        record_dir.mkdir(parents=True, exist_ok=True)

        if not self.run_id:
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        file_path = record_dir / f"run_{self.run_id}.json"
        file_path.write_text(self.to_json(), encoding='utf-8')

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

        self.result.run_id = self.start_time.strftime("%Y%m%d_%H%M%S")
        self.result.start_time = self.start_time.strftime("%Y-%m-%d %H:%M:%S")

    def set_config(self, start_date: str, end_date: str,
                   statements_enabled: bool = True,
                   receipts_enabled: bool = True):
        self.result.query_start_date = start_date
        self.result.query_end_date = end_date
        self.result.statements_enabled = statements_enabled
        self.result.receipts_enabled = receipts_enabled

    def record_statements_result(self, files: List[str], no_data: bool = False):
        self.result.statements_files = files
        self.result.statements_count = len(files)
        self.result.statements_no_data = no_data
        self.result.total_downloaded_bytes += self._sum_file_sizes(files)

    def record_receipts_result(self, files: List[str], no_data: bool = False):
        self.result.receipts_files = files
        self.result.receipts_count = len(files)
        self.result.receipts_no_data = no_data
        self.result.total_downloaded_bytes += self._sum_file_sizes(files)

    @staticmethod
    def _sum_file_sizes(files: List[str]) -> int:
        total = 0
        for f in files:
            try:
                fp = Path(f)
                if fp.exists():
                    total += fp.stat().st_size
            except Exception:
                pass
        return total

    def finish(self, success: bool = True, error_message: str = "") -> str:
        end_time = datetime.now()
        self.result.end_time = end_time.strftime("%Y-%m-%d %H:%M:%S")
        self.result.duration_seconds = (end_time - self.start_time).total_seconds()
        self.result.success = success
        self.result.status = "success" if success else "failed"
        self.result.error_message = error_message
        self.result.total_files = self.result.statements_count + self.result.receipts_count

        record_path = self.result.save(str(self.output_dir))
        self._print_summary()
        return record_path

    def _print_summary(self):
        print("\n" + "=" * 60)
        print("运行结果摘要")
        print("=" * 60)
        print(f"运行ID: {self.result.run_id}")
        print(f"状态: {'✓ 成功' if self.result.success else '✗ 失败'}")
        print(f"开始时间: {self.result.start_time}")
        print(f"结束时间: {self.result.end_time}")
        print(f"运行时长: {self.result.duration_seconds:.2f} 秒")
        print(f"查询日期: {self.result.query_start_date} 至 {self.result.query_end_date}")
        print("-" * 60)
        print(f"银行流水:")
        print(f"  文件数: {self.result.statements_count}")
        if self.result.statements_no_data:
            print(f"  状态: 无数据")
        for f in self.result.statements_files[:3]:
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
        print("-" * 60)
        print(f"总文件数: {self.result.total_files}")
        print(f"总大小: {self.result.total_downloaded_bytes / 1024:.2f} KB")
        print("=" * 60)
        print(f"记录文件: {self.output_dir}/run_{self.result.run_id}.json")
        print(f"最新记录: {self.output_dir}/latest.json")
        print("=" * 60 + "\n")


def load_latest_record(output_dir: str = "./records") -> Optional[RunResult]:
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
    record = load_latest_record(output_dir)
    if not record:
        return False
    return record.success and record.status == "success"


def get_last_run_files(output_dir: str = "./records") -> List[str]:
    record = load_latest_record(output_dir)
    if not record:
        return []
    return record.statements_files + record.receipts_files
