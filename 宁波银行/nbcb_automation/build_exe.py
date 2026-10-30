# -*- coding: utf-8 -*-
"""
打包脚本 - 将宁波银行采集程序打包为可执行文件
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path


def clean_build():
    """清理构建目录"""
    dirs_to_remove = ['build', 'dist', '__pycache__', '*.spec']
    for pattern in dirs_to_remove:
        for path in Path('.').glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
                print(f"已删除目录: {path}")
            elif path.is_file():
                path.unlink()
                print(f"已删除文件: {path}")


def build():
    """执行打包"""
    print("=" * 60)
    print("开始打包宁波银行采集程序...")
    print("=" * 60)
    
    # 清理旧构建
    clean_build()
    
    project_root = str(Path(__file__).resolve().parent.parent.parent)

    # PyInstaller 参数
    args = [
        'pyinstaller',
        '--name=宁波银行采集程序',
        '--onefile',  # 打包为单个文件
        '--console',  # 显示控制台窗口
        '--clean',
        '--noconfirm',
        '--paths', project_root,
        # 添加数据文件
        '--add-data', 'config.ini;.',
        # 隐藏导入
        '--hidden-import', 'playwright',
        '--hidden-import', 'loguru',
        '--hidden-import', 'yaml',
        '--hidden-import', 'popup_handler',
        '--hidden-import', 'pin_inputter',
        '--hidden-import', 'login_manager',
        '--hidden-import', 'common.config_loader',
        '--hidden-import', 'common.logger',
        '--hidden-import', 'common.cabinet_controller',
        '--hidden-import', 'common.run_recorder',
        # 排除不必要的包以减小体积
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'numpy',
        '--exclude-module', 'PIL',
        '--exclude-module', 'pandas',
        # 主程序入口
        'main.py'
    ]
    
    print(f"执行命令: {' '.join(args)}")
    result = subprocess.run(args, capture_output=False, text=True)
    
    if result.returncode == 0:
        print("\n" + "=" * 60)
        print("✓ 打包成功！")
        print("=" * 60)
        print(f"输出目录: {Path('dist').absolute()}")
        print(f"可执行文件: {Path('dist/宁波银行采集程序.exe').absolute()}")
        
        # 复制配置文件到dist目录
        if Path('config.ini').exists():
            shutil.copy('config.ini', 'dist/config.ini')
            print(f"配置文件已复制到: {Path('dist/config.ini').absolute()}")
        
        return True
    else:
        print("\n" + "=" * 60)
        print("✗ 打包失败！")
        print("=" * 60)
        return False


def main():
    """主函数"""
    # 检查是否在正确的目录
    if not Path('main.py').exists():
        print("错误: 请在包含 main.py 的目录中运行此脚本")
        sys.exit(1)
    
    # 检查 pyinstaller 是否安装
    try:
        subprocess.run(['pyinstaller', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("正在安装 PyInstaller...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'], check=True)
    
    # 执行打包
    success = build()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
