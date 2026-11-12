# -*- coding: utf-8 -*-
"""
下载监控辅助模块
供各银行自动化项目共享
"""
import re
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
