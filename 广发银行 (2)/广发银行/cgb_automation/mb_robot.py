# -*- coding: utf-8 -*-
"""
MB智能机柜控制模块
用于通过命令行控制网线连接的UKey机柜
"""
import os
import subprocess
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from logger import setup_logger

logger = setup_logger()


@dataclass
class MBConfig:
    """MB机柜配置"""
    mb_path: str = "mb"  # mb命令路径，可配置完整路径或简写（如果在PATH中）
    ter_id: str = ""     # 机柜编号
    port: int = 1        # 端口号
    user: str = "test"   # 用户名
    enabled: bool = False  # 是否启用


class MBRobot:
    """MB机柜控制器"""
    
    def __init__(self, config: MBConfig):
        self.config = config
        self._mb_exe_path = self._find_mb_exe()
    
    def _find_mb_exe(self) -> str:
        """自动查找 mb.exe 路径"""
        import sys
        
        # 如果配置的是完整路径且存在，直接使用
        if self.config.mb_path and Path(self.config.mb_path).exists():
            return str(Path(self.config.mb_path).resolve())
        
        # 尝试从PATH中查找
        mb_name = "mb.exe" if sys.platform == "win32" else "mb"
        
        # 常见位置查找（基于截图中的路径）
        search_paths = [
            Path("C:/waicai/wangyingbao/m2/mb.exe"),
            Path("C:/waicai/wangyingbao/m2/mb"),
            Path(sys.executable).parent / "mb.exe",
            Path(sys.executable).parent / "m2" / "mb.exe",
            Path.cwd() / "mb.exe",
            Path.cwd() / "m2" / "mb.exe",
            Path.cwd().parent / "m2" / "mb.exe",
            Path.cwd().parent.parent / "m2" / "mb.exe",
        ]
        
        for path in search_paths:
            if path.exists():
                logger.info(f"找到 mb.exe: {path}")
                return str(path.resolve())
        
        # 都没找到，使用配置的值（可能是在PATH中）
        logger.warning(f"未找到 mb.exe 文件，将使用配置的命令: {self.config.mb_path}")
        return self.config.mb_path
        
    def _run_command(self, command: list, timeout: int = 30) -> tuple[bool, str]:
        """
        运行mb命令
        
        Args:
            command: 命令参数列表
            timeout: 超时时间（秒）
            
        Returns:
            (是否成功, 输出内容)
        """
        cmd = [self._mb_exe_path] + command
        logger.info("=" * 60)
        logger.info(f"【MB命令】完整命令: {' '.join(cmd)}")
        logger.info(f"【MB命令】可执行文件路径: {self._mb_exe_path}")
        logger.info(f"【MB命令】工作目录: {os.getcwd()}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='ignore'
            )
            
            if result.returncode == 0:
                logger.info(f"MB命令执行成功: {result.stdout[:200]}")
                return True, result.stdout
            else:
                logger.error(f"MB命令执行失败: {result.stderr}")
                return False, result.stderr
                
        except subprocess.TimeoutExpired:
            logger.error(f"MB命令执行超时")
            return False, "timeout"
        except Exception as e:
            logger.error(f"MB命令执行异常: {e}")
            return False, str(e)
    
    def find_ters(self) -> dict[str, str]:
        """
        查找可用的机柜
        
        Returns:
            机柜ID到IP的映射字典
        """
        success, output = self._run_command(["find_ter"])
        if success:
            try:
                import json
                data = json.loads(output)
                if data.get("result") and data.get("data"):
                    return data["data"]
            except:
                pass
        return {}
    
    def get_status(self) -> bool:
        """
        获取机柜状态
        
        Returns:
            是否在线可用
        """
        success, _ = self._run_command([
            "status",
            "--ter_id", self.config.ter_id
        ])
        return success
    
    def load_port(self) -> bool:
        """
        插入UKey（加载端口）
        
        Returns:
            是否成功
        """
        cmd = [
            "load_port",
            "--ter_id", self.config.ter_id,
            "--port", str(self.config.port)
        ]
        if self.config.user:
            cmd.extend(["--user", self.config.user])
        
        success, _ = self._run_command(cmd)
        return success
    
    def shutdown_port(self) -> bool:
        """
        拔出UKey（关闭端口）
        
        Returns:
            是否成功
        """
        cmd = [
            "shutdown_port",
            "--ter_id", self.config.ter_id,
            "--port", str(self.config.port)
        ]
        if self.config.user:
            cmd.extend(["--user", self.config.user])
        
        success, _ = self._run_command(cmd)
        return success
    
    def click_ok(self) -> bool:
        """
        点击UKey上的OK键
        
        Returns:
            是否成功
        """
        logger.info("=" * 60)
        logger.info("【MB点击OK】准备执行OK键点击命令")
        logger.info(f"【MB点击OK】机柜ID: {self.config.ter_id}, 端口: {self.config.port}")
        
        success, output = self._run_command([
            "robot",
            "--ter_id", self.config.ter_id,
            "--port", str(self.config.port)
        ])
        
        logger.info(f"【MB点击OK】执行结果: {'成功' if success else '失败'}")
        logger.info("=" * 60)
        return success
    
    def restart_port(self) -> bool:
        """
        重启端口（拔出后重新插入）
        
        Returns:
            是否成功
        """
        success, _ = self._run_command([
            "restart_port",
            "--ter_id", self.config.ter_id,
            "--port", str(self.config.port)
        ])
        return success
    
    def do_login_flow(self) -> bool:
        """
        执行完整的登录流程：
        1. 确保UKey已插入（如未插入则插入）
        2. 点击OK键确认登录
        
        Returns:
            是否成功
        """
        if not self.config.enabled:
            logger.warning("MB控制未启用，跳过")
            return False
        
        if not self.config.ter_id or not self.config.port:
            logger.error("MB配置不完整，缺少ter_id或port")
            return False
        
        logger.info("=" * 50)
        logger.info("开始MB机柜控制流程...")
        logger.info(f"机柜ID: {self.config.ter_id}, 端口: {self.config.port}")
        
        # 第1步：检查机柜状态
        logger.info("检查机柜状态...")
        if not self.get_status():
            logger.error("机柜不在线或无法连接")
            return False
        logger.info("机柜状态正常")
        
        # 第2步：加载端口（插入UKey）
        logger.info("插入UKey...")
        if not self.load_port():
            logger.warning("插入UKey失败，可能已插入")
        else:
            logger.info("UKey插入成功")
        
        # 等待UKey被系统识别
        logger.info("等待UKey被系统识别（3秒）...")
        time.sleep(3)
        
        logger.info("MB机柜初始化完成，等待登录按钮点击...")
        logger.info("=" * 50)
        return True
    
    def confirm_login(self) -> bool:
        """
        登录后确认（点击OK键）
        
        Returns:
            是否成功
        """
        if not self.config.enabled:
            return False
        
        logger.info("=" * 50)
        logger.info("点击UKey OK键确认登录...")
        
        # 点击OK键
        if self.click_ok():
            logger.info("OK键点击成功")
            logger.info("=" * 50)
            return True
        else:
            logger.error("OK键点击失败")
            logger.info("=" * 50)
            return False
    
    def cleanup(self):
        """
        清理：拔出UKey
        """
        if not self.config.enabled:
            return
        
        logger.info("MB清理：拔出UKey...")
        self.shutdown_port()
