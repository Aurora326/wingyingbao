# -*- coding: utf-8 -*-
"""
下载监控辅助模块
供各银行自动化项目共享
"""
import datetime as dt
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Set

from loguru import logger


def get_downloaded_files_before(browser_download_path: Path) -> Dict[Path, Set[str]]:
    """获取多个下载目录中的文件列表（包括程序目录和系统下载目录）"""
    paths_to_check = [
        browser_download_path,
        Path.home() / "Downloads",
    ]
    # 如果 browser_download_path 不是 common/downloads，也把它加进来
    common_downloads = Path(__file__).parent.parent / "common" / "downloads"
    if common_downloads.exists() and str(common_downloads) not in {str(p) for p in paths_to_check}:
        paths_to_check.append(common_downloads)

    all_files = {}
    for path in paths_to_check:
        if path.exists():
            all_files[path] = set(f.name for f in path.iterdir() if f.is_file())
            logger.info(f"  目录 {path}: {len(all_files[path])} 个文件")
        else:
            all_files[path] = set()
            logger.info(f"  目录 {path}: 不存在")

    return all_files


def is_guid_filename(filename: str) -> bool:
    """检查文件名是否是 GUID 格式（Chrome 临时下载文件）"""
    guid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    return bool(re.match(guid_pattern, filename))


def wait_and_find_new_file(before_files: Dict[Path, Set[str]], timeout: int = 60) -> Optional[Path]:
    """等待并查找新下载的文件（支持多目录监控）"""
    start_time = time.time()

    # 合并所有目录的旧文件列表，同时记录每个旧文件的修改时间
    all_before_files = set()
    before_mtime = {}
    for path, files in before_files.items():
        all_before_files.update(files)
        for fname in files:
            fpath = path / fname
            try:
                before_mtime[fname] = fpath.stat().st_mtime
            except Exception:
                before_mtime[fname] = 0

    logger.info(f"等待下载完成，初始监控文件数: {len(all_before_files)}")

    # 要监控的目录列表（去重）
    download_dirs = []
    seen = set()
    for path in before_files.keys():
        if str(path) not in seen:
            seen.add(str(path))
            download_dirs.append(path)
    if str(Path.home() / "Downloads") not in seen:
        download_dirs.append(Path.home() / "Downloads")

    # 记录临时文件状态用于稳定性判断: path -> (size, mtime, stable_count)
    pending_tmp: Dict[Path, tuple] = {}

    while time.time() - start_time < timeout:
        time.sleep(1)

        for download_dir in download_dirs:
            if not download_dir.exists():
                continue

            for file_path in download_dir.iterdir():
                if not file_path.is_file():
                    continue

                file_name = file_path.name

                # 检查是否是新文件（按文件名或修改时间）
                is_new_by_name = file_name not in all_before_files
                is_new_by_mtime = False
                mtime_diff = 0
                if not is_new_by_name:
                    try:
                        current_mtime = file_path.stat().st_mtime
                        old_mtime = before_mtime.get(file_name, 0)
                        mtime_diff = current_mtime - old_mtime
                        # 修改时间比记录的旧时间晚 1 秒以上，说明是覆盖下载的新文件
                        is_new_by_mtime = current_mtime > old_mtime + 1
                    except Exception:
                        pass

                if is_new_by_name or is_new_by_mtime:
                    try:
                        file_size = file_path.stat().st_size
                    except Exception:
                        file_size = 0

                    logger.info(f"发现新文件: {file_name} (大小: {file_size} bytes, 新文件名={is_new_by_name}, 新修改时间={is_new_by_mtime}, mtime_diff={mtime_diff:.1f}s) 在目录: {download_dir}")

                    # Chrome / 服务端未返回文件名时的临时文件
                    if file_name.endswith(('.crdownload', '.tmp')):
                        try:
                            stat = file_path.stat()
                            state = pending_tmp.get(file_path)
                            if state and state[0] == stat.st_size and state[1] == stat.st_mtime:
                                stable_count = state[2] + 1
                            else:
                                stable_count = 0
                            pending_tmp[file_path] = (stat.st_size, stat.st_mtime, stable_count)
                            if stable_count >= 2:
                                # 大小和修改时间连续多轮不变，说明下载已写完但浏览器未重命名，直接采用
                                logger.info(f"临时文件已稳定({stat.st_size} bytes)，采用: {file_path}")
                                return file_path
                            logger.info(f"检测到下载临时文件，等待重命名: {file_name}")
                        except Exception:
                            pass
                        continue

                    is_valid = (
                        file_name.endswith(('.xlsx', '.xls', '.csv', '.pdf', '.zip', '.txt', '.html', '.htm')) or
                        (is_guid_filename(file_name) and file_size > 100)
                    )

                    if is_valid:
                        logger.info(f"确认新下载文件: {file_path}")
                        # 等待文件稳定
                        stable_count = 0
                        last_size = -1
                        while stable_count < 3:
                            time.sleep(0.5)
                            if file_path.exists():
                                current_size = file_path.stat().st_size
                                if current_size == last_size:
                                    stable_count += 1
                                else:
                                    stable_count = 0
                                    last_size = current_size
                            else:
                                break
                        return file_path

    # 超时兜底：只看等待期间新出现/更新的、且属于流水常见格式的文件
    logger.warning("下载超时，尝试兜底查找最近修改的流水文件...")
    candidates = []
    for download_dir in download_dirs:
        if not download_dir.exists():
            continue
        for f in download_dir.iterdir():
            if not f.is_file():
                continue
            try:
                stat = f.stat()
            except Exception:
                continue
            if stat.st_size <= 0:
                continue
            if not (
                f.name.endswith(('.xlsx', '.xls', '.csv', '.pdf', '.zip')) or
                is_guid_filename(f.name)
            ):
                continue
            if stat.st_mtime < start_time - 5:
                continue
            candidates.append((f, stat.st_mtime))

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        newest = candidates[0][0]
        logger.warning(f"超时兜底: {newest} (mtime={candidates[0][1]})")
        return newest

    logger.warning("下载超时，且未找到任何候选流水文件")
    return None


# ==================== 下载目录与归档 ====================


def resolve_browser_download_dir(base_dir: Path) -> Path:
    """解析浏览器实际下载目录。

    PyInstaller 打包环境用 exe 所在目录，开发环境用调用方（银行程序）目录；
    base_dir 必须由调用方传自己的 __file__.parent，不能用 common 的路径。
    """
    base = Path(base_dir)
    if getattr(sys, "frozen", False):
        target = Path(sys.executable).parent / "downloads"
    else:
        target = base / "downloads"
    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_archive_dir(download_root: Path, archive_by_month: bool, now: Optional[dt.datetime] = None) -> Path:
    """返回归档目录：按月归档时为 download_root/YYYY/MM，否则为 download_root。"""
    root = Path(download_root)
    if not archive_by_month:
        root.mkdir(parents=True, exist_ok=True)
        return root
    current = now or dt.datetime.now()
    target = root / f"{current.year}" / f"{current.month:02d}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _months_before(day: dt.datetime, months: int) -> dt.datetime:
    """把某月 1 日往前推 N 个月（逐月相减，不按 31 天近似）。"""
    cutoff = day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(max(0, months)):
        if cutoff.month == 1:
            cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
        else:
            cutoff = cutoff.replace(month=cutoff.month - 1)
    return cutoff


def cleanup_old_archives(
    download_root: Path,
    retention_months: int,
    label: str = "采集",
    now: Optional[dt.datetime] = None,
) -> int:
    """删除超过保留期的 YYYY/MM 归档目录，返回删除的目录数。

    保留期按“逐月相减”计算，与各银行原有实现保持一致；
    label 只用于日志（如“流水”“回单”）。
    """
    root = Path(download_root)
    if retention_months <= 0 or not root.exists():
        return 0

    current = now or dt.datetime.now()
    cutoff_date = _months_before(current, retention_months)
    logger.info(
        f"清理 {retention_months} 个月前的{label}归档数据，"
        f"截止日期: {cutoff_date.strftime('%Y-%m')}"
    )

    deleted_count = 0
    for year_dir in root.iterdir():
        if not year_dir.is_dir():
            continue
        try:
            year = int(year_dir.name)
            if year < 2000 or year > 2100:
                continue
        except ValueError:
            continue

        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir():
                continue
            try:
                month = int(month_dir.name)
                if month < 1 or month > 12:
                    continue
                dir_date = dt.datetime(year, month, 1)
                if dir_date < cutoff_date:
                    import shutil

                    shutil.rmtree(month_dir)
                    deleted_count += 1
                    logger.info(f"已删除旧归档目录: {year}/{month:02d}")
            except (ValueError, OSError) as e:
                logger.debug(f"处理目录 {month_dir} 时出错: {e}")
                continue

    for year_dir in root.iterdir():
        if year_dir.is_dir() and not any(year_dir.iterdir()):
            year_dir.rmdir()
            logger.info(f"已删除空年份目录: {year_dir.name}")

    if deleted_count > 0:
        logger.info(f"共清理 {deleted_count} 个旧归档目录")
    else:
        logger.info("没有需要清理的旧归档数据")
    return deleted_count

