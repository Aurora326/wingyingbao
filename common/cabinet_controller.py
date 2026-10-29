# -*- coding: utf-8 -*-
"""
机柜控制器抽象层 - 支持多供应商兼容
支持: MB机柜、以及其他未来扩展的机柜类型
"""
import abc
import os
import subprocess
import sys
import time
import json
from pathlib import Path
from typing import Dict, Optional, Any
from loguru import logger as _loguru_logger
# 不要在导入时调 setup_logger：它会在当前目录建日志并清理旧文件，
# 采集程序有自己的日志目录，不该被这个副作用影响
logger = _loguru_logger


class CabinetController(abc.ABC):
    """机柜控制器抽象基类"""
    
    @abc.abstractmethod
    def connect(self) -> bool:
        """连接到机柜"""
        pass
    
    @abc.abstractmethod
    def click_ok(self) -> bool:
        """点击USBKey的OK键（确认操作）"""
        pass
    
    @abc.abstractmethod
    def click_cancel(self) -> bool:
        """点击USBKey的Cancel键（取消操作）"""
        pass
    
    @abc.abstractmethod
    def input_pin(self, pin: str) -> bool:
        """输入PIN码"""
        pass
    
    @abc.abstractmethod
    def close(self) -> bool:
        """关闭连接/释放资源"""
        pass
    
    @abc.abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """获取机柜状态"""
        pass


class MBCabinetController(CabinetController):
    """MB机柜控制器实现"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.ter_id = config.get('ter_id', '')
        self.port = config.get('port', 1)
        self.user = config.get('user', '')
        self.mb_path = self._find_mb_path(config.get('mb_path', 'mb'))
        self._connected = False
        
        # 输出配置信息
        logger.info("[MB] 机柜控制器初始化配置:")
        logger.info(f"  - 机柜编号(ter_id): {self.ter_id or '(未设置!)'}")
        logger.info(f"  - 端口号(port): {self.port}")
        logger.info(f"  - 用户名(user): {self.user}")
        logger.info(f"  - mb.exe路径: {self.mb_path}")
    
    def _find_mb_path(self, mb_path: str) -> str:
        """查找 mb.exe 的完整路径（兼容开发、打包及 PATH 环境）"""
        import shutil

        # 如果配置的是完整路径且存在，直接返回
        if os.path.exists(mb_path):
            return mb_path

        # 尝试在 PATH 中查找（启动脚本已将 tools 加入 PATH）
        found_path = shutil.which(mb_path)
        if found_path:
            return found_path

        # 尝试常见位置
        _project_root = Path(__file__).resolve().parent.parent
        common_paths = [
            str(_project_root / "m2" / "mb.exe"),
            str(_project_root / "keyboard" / "mb.exe"),
            str(_project_root / "tools" / "mb.exe"),
            r"..\m2\mb.exe",
            r".\tools\mb.exe",
            r"..\tools\mb.exe",
        ]

        # 打包环境：基于 exe 所在目录再补充一次（不受 cwd 影响）
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
            common_paths = [
                str(exe_dir / "tools" / "mb.exe"),
                str(exe_dir / "m2" / "mb.exe"),
            ] + common_paths

        for path in common_paths:
            if os.path.exists(path):
                logger.info(f"[MB] 找到 mb.exe: {path}")
                return path

        # 如果没找到，返回原始值（让调用时报错）
        logger.warning(f"[MB] 未找到 mb.exe，将使用: {mb_path}")
        return mb_path
        
    def _execute_subcommand(self, subcmd: str, **kwargs) -> Dict:
        """执行MB子命令（使用子命令格式如: mb.exe load_port --ter_id xxx）"""
        # 检查ter_id是否设置
        if not self.ter_id:
            logger.warning("[MB] 警告: 机柜编号(ter_id)未设置！请在配置文件中设置[cabinet] ter_id")
        
        try:
            # 根据命令类型构建参数
            # 需要 ter_id + port 的命令: load_port, shutdown_port, force_shutdown_port, restart_port, robot
            # 只需要 ter_id 的命令: status, restart_ter
            # 不需要参数的命令: find_ter, get_mac
            full_cmd = [self.mb_path, subcmd]
            
            # 所有命令（除了 find_ter, get_mac）都需要 --ter_id
            if subcmd not in ['find_ter', 'get_mac']:
                full_cmd.extend(["--ter_id", self.ter_id if self.ter_id else ""])
            
            # 需要 port 的命令
            if subcmd in ['load_port', 'shutdown_port', 'force_shutdown_port', 'restart_port', 'robot']:
                full_cmd.extend(["--port", str(self.port)])
            
            # 需要 user 的命令（robot 不需要）
            if self.user and subcmd in ['load_port', 'shutdown_port']:
                full_cmd.extend(["--user", self.user])
            
            # 添加额外参数
            for key, value in kwargs.items():
                full_cmd.extend([f"--{key}", str(value)])
            
            logger.info(f"[MB] 执行子命令: {subcmd}")
            logger.info(f"[MB] 完整命令: {' '.join(full_cmd)}")
            # 将工作目录设为 mb.exe 所在目录，确保 mb.conf 等配置文件可被找到
            cwd = os.path.dirname(self.mb_path) or None
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                cwd=cwd
            )

            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                except Exception:
                    return {"success": True, "output": result.stdout}
                # mb.exe 有的子命令返回 {"result": true} 而不是 {"success": true}，
                # 不归一化的话调用方按 success 判断会把成功当成失败
                if isinstance(data, dict) and "result" in data and "success" not in data:
                    data["success"] = bool(data["result"])
                return data
            else:
                logger.error(f"[MB] 命令失败: {result.stderr}")
                return {"success": False, "error": result.stderr}
                
        except Exception as e:
            logger.error(f"[MB] 执行命令异常: {e}")
            return {"success": False, "error": str(e)}
    
    def connect(self) -> bool:
        """插入U盾到MB机柜端口 (load_port)"""
        result = self._execute_subcommand("load_port")
        self._connected = result.get("success", False)
        return self._connected
    
    def click_ok(self) -> bool:
        """点击USBKey的OK键 (robot)"""
        # robot 命令可能需要子命令，这里假设是点击OK
        result = self._execute_subcommand("robot")
        return result.get("success", False)
    
    def click_cancel(self) -> bool:
        """点击Cancel键 - MB机柜没有独立的Cancel键，拔出再插入模拟"""
        logger.info("[MB] 模拟Cancel操作: 拔出U盾再重新插入")
        self._execute_subcommand("shutdown_port")  # 拔出
        time.sleep(1)
        result = self._execute_subcommand("load_port")  # 重新插入
        return result.get("success", False)
    
    def input_pin(self, pin: str) -> bool:
        """输入PIN码（MB机柜通常通过物理键盘输入，这里预留接口）"""
        logger.info("[MB] PIN码通常通过物理键盘输入，请确保机柜键盘可用")
        return True
    
    def close(self) -> bool:
        """拔出U盾 (shutdown_port)"""
        result = self._execute_subcommand("shutdown_port")
        self._connected = False
        return result.get("success", False)
    
    def get_status(self) -> Dict[str, Any]:
        """获取机柜状态"""
        return self._execute_subcommand("status")


class MockCabinetController(CabinetController):
    """模拟机柜控制器（用于测试或无机柜环境）"""
    
    def __init__(self, config: Dict):
        self.config = config
        self._connected = False
        
    def connect(self) -> bool:
        logger.info("[Mock] 模拟连接到机柜")
        self._connected = True
        return True
    
    def click_ok(self) -> bool:
        logger.info("[Mock] 模拟点击OK键")
        return True
    
    def click_cancel(self) -> bool:
        logger.info("[Mock] 模拟点击Cancel键")
        return True
    
    def input_pin(self, pin: str) -> bool:
        logger.info(f"[Mock] 模拟输入PIN码: {'*' * len(pin)}")
        return True
    
    def close(self) -> bool:
        logger.info("[Mock] 模拟关闭连接")
        self._connected = False
        return True
    
    def get_status(self) -> Dict[str, Any]:
        return {"connected": self._connected, "mock": True}


class GF3CabinetController(CabinetController):
    """
    GF3机柜控制器（好周岛等USB-HUB机柜）
    命令格式: gf3.exe <product_id> <port> <flag>
    flag: 1=按压, 2=连接, 3=断开
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.gf3_path = self._find_gf3_path(config.get('gf3_path', 'gf3.exe'))
        self.product_id = config.get('product_id', 1)
        self.port = config.get('port', 1)
        self._connected = False
    
    def _find_gf3_path(self, gf3_path: str) -> str:
        """查找 gf3.exe 的完整路径"""
        import shutil
        
        # 如果配置的是完整路径且存在，直接返回
        if os.path.exists(gf3_path):
            return gf3_path
        
        # 尝试在 PATH 中查找
        found_path = shutil.which(gf3_path)
        if found_path:
            return found_path
        
        # 尝试常见位置
        _project_root = Path(__file__).resolve().parent.parent
        common_paths = [
            str(_project_root / "USB-HUB" / "gf3.exe"),
            str(_project_root / "keyboard" / "gf3.exe"),
            str(_project_root / "tools" / "gf3.exe"),
            r"..\USB-HUB\gf3.exe",
            r".\tools\gf3.exe",
            r"..\tools\gf3.exe",
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                logger.info(f"[GF3] 找到 gf3.exe: {path}")
                return path
        
        # 如果没找到，返回原始值
        logger.warning(f"[GF3] 未找到 gf3.exe，将使用: {gf3_path}")
        return gf3_path
        
    def _execute(self, flag: int) -> bool:
        """执行gf3命令"""
        try:
            cmd = [
                self.gf3_path,
                str(self.product_id),
                str(self.port),
                str(flag)
            ]
            
            flag_names = {1: '按压', 2: '连接', 3: '断开'}
            flag_name = flag_names.get(flag, f'未知操作({flag})')
            logger.info(f"[GF3] 执行{flag_name}: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                logger.info(f"[GF3] {flag_name}成功")
                return True
            else:
                logger.error(f"[GF3] {flag_name}失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"[GF3] 执行命令异常: {e}")
            return False
    
    def connect(self) -> bool:
        """连接UKey (flag=2)"""
        result = self._execute(2)
        self._connected = result
        return result
    
    def click_ok(self) -> bool:
        """按压UKey (flag=1)"""
        return self._execute(1)
    
    def click_cancel(self) -> bool:
        """GF3机柜没有独立的Cancel键，使用断开再连接的方式模拟"""
        logger.info("[GF3] 模拟Cancel操作: 断开再连接")
        self._execute(3)  # 断开
        time.sleep(1)
        return self._execute(2)  # 重新连接
    
    def input_pin(self, pin: str) -> bool:
        """GF3机柜不支持自动输入PIN码，需手动输入"""
        logger.info("[GF3] PIN码需手动输入，请使用机柜控制工具的物理键盘")
        return True
    
    def close(self) -> bool:
        """断开UKey (flag=3)"""
        result = self._execute(3)
        self._connected = False
        return result
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "type": "gf3",
            "connected": self._connected,
            "product_id": self.product_id,
            "port": self.port
        }


# 未来扩展示例：其他供应商机柜
class OtherCabinetController(CabinetController):
    """
    其他供应商机柜控制器示例
    只需实现相同的接口即可
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.api_url = config.get('api_url', '')
        self.api_key = config.get('api_key', '')
        # ... 其他初始化
        
    def connect(self) -> bool:
        # 实现连接逻辑
        logger.info(f"[Other] 连接到机柜: {self.api_url}")
        return True
    
    def click_ok(self) -> bool:
        # 实现点击OK逻辑
        logger.info("[Other] 点击OK键")
        return True
    
    def click_cancel(self) -> bool:
        logger.info("[Other] 点击Cancel键")
        return True
    
    def input_pin(self, pin: str) -> bool:
        logger.info(f"[Other] 输入PIN码")
        return True
    
    def close(self) -> bool:
        logger.info("[Other] 关闭连接")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        return {"type": "other", "connected": True}


class CabinetControllerFactory:
    """机柜控制器工厂"""
    
    # 注册所有支持的机柜类型
    _controllers = {
        'mb': MBCabinetController,
        'gf3': GF3CabinetController,  # 好周岛等USB-HUB机柜
        'mock': MockCabinetController,
        'other': OtherCabinetController,
        # 未来在这里添加新的机柜类型
    }
    
    @classmethod
    def _config_to_dict(cls, config) -> Dict:
        """将配置对象转换为字典（兼容 dataclass 和 dict）"""
        if isinstance(config, dict):
            return config
        # 如果是 dataclass 对象，转换为字典
        if hasattr(config, '__dict__'):
            result = {}
            for key, value in config.__dict__.items():
                # 递归处理嵌套对象
                if hasattr(value, '__dict__'):
                    result[key] = cls._config_to_dict(value)
                else:
                    result[key] = value
            return result
        return {}
    
    @classmethod
    def create(cls, config) -> Optional[CabinetController]:
        """
        根据配置创建对应的机柜控制器
        
        Args:
            config: 配置对象或字典，必须包含 'type' 字段
                    例如: {'type': 'mb', 'ter_id': 'xxx', 'port': 5}
        
        Returns:
            CabinetController 实例或 None
        """
        # 转换为字典（兼容 dataclass）
        config_dict = cls._config_to_dict(config)
        
        # 兼容旧配置：如果没有 type 字段但有 enabled，默认为 mb
        if 'type' not in config_dict and config_dict.get('enabled', False):
            config_dict['type'] = 'mb'
            logger.info("检测到旧版机柜配置，自动设置 type=mb")
        
        controller_type = config_dict.get('type', 'mock').lower()
        
        if controller_type not in cls._controllers:
            logger.error(f"不支持的机柜类型: {controller_type}")
            logger.info(f"支持的类型: {list(cls._controllers.keys())}")
            return None
        
        controller_class = cls._controllers[controller_type]
        logger.info(f"创建机柜控制器: {controller_type}")
        return controller_class(config_dict)
    
    @classmethod
    def register(cls, name: str, controller_class: type):
        """
        注册新的机柜控制器类型（用于插件扩展）
        
        Args:
            name: 机柜类型名称
            controller_class: 机柜控制器类
        """
        if not issubclass(controller_class, CabinetController):
            raise ValueError("控制器类必须继承 CabinetController")
        
        cls._controllers[name.lower()] = controller_class
        logger.info(f"注册新机柜控制器: {name}")
    
    @classmethod
    def get_supported_types(cls) -> list:
        """获取支持的机柜类型列表"""
        return list(cls._controllers.keys())


# 向后兼容的辅助函数
def create_cabinet_controller(config: Dict) -> Optional[CabinetController]:
    """创建机柜控制器的便捷函数"""
    return CabinetControllerFactory.create(config)
