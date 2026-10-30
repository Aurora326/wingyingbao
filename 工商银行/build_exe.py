# -*- coding: utf-8 -*-
"""
打包脚本 - 将工商网银自动化程序打包成可执行文件
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path


def clean_build():
    """清理之前的构建文件"""
    for dir_name in ['build', 'dist', '__pycache__']:
        dir_path = Path(dir_name)
        if dir_path.exists():
            print(f"清理 {dir_name}...")
            shutil.rmtree(dir_path)

    for spec_file in Path('.').glob('*.spec'):
        print(f"删除 {spec_file}...")
        spec_file.unlink()


def check_pyinstaller():
    """检查是否安装了 PyInstaller"""
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        print("PyInstaller 未安装，正在安装...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'], check=True)
        return True


def build_exe():
    """构建单文件可执行程序"""
    print("=" * 60)
    print("开始打包工商网银自动化程序...")
    print("=" * 60)

    os.chdir(Path(__file__).parent / 'icbc_automation')

    clean_build()
    check_pyinstaller()

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=ICBC_Automation',
        '--onefile',
        '--windowed',
        '--add-data=config.ini;.',
        '--add-data=../../keyboard;keyboard',
        '--add-data=logger.py;.',
        '--add-data=config_loader.py;.',
        '--add-data=browser.py;.',
        '--add-data=statement_collector.py;.',
        '--add-data=receipt_collector.py;.',
        '--add-data=run_recorder.py;.',
        '--hidden-import=playwright',
        '--hidden-import=loguru',
        '--hidden-import=yaml',
        '--hidden-import=pyautogui',
        '--hidden-import=pygetwindow',
        '--hidden-import=pyscreeze',
        '--hidden-import=pymsgbox',
        '--hidden-import=pytweening',
        '--hidden-import=mouseinfo',
        '--hidden-import=rubicon-objc',
        '--collect-all=playwright',
        '--icon=NONE',
        'main.py'
    ]

    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print("打包失败！")
        return False

    print("\n" + "=" * 60)
    print("打包成功！")
    print("=" * 60)
    print(f"可执行文件位置: {Path('dist/ICBC_Automation.exe').absolute()}")

    release_dir = Path('dist/ICBC_Automation_Release')
    release_dir.mkdir(exist_ok=True)

    shutil.copy('dist/ICBC_Automation.exe', release_dir)

    (release_dir / 'logs').mkdir(exist_ok=True)
    (release_dir / 'downloads').mkdir(exist_ok=True)
    (release_dir / 'downloads' / 'statements').mkdir(exist_ok=True)
    (release_dir / 'downloads' / 'receipts').mkdir(exist_ok=True)

    if Path('config.ini').exists():
        shutil.copy('config.ini', release_dir / 'config.ini.example')
        print(f"配置文件示例已复制到: {release_dir / 'config.ini.example'}")

    print(f"\n发布目录已创建: {release_dir.absolute()}")
    print("\n使用说明:")
    print("1. 复制 config.ini.example 为 config.ini")
    print("2. 编辑 config.ini，填入您的网银信息")
    print("3. 将 AiKeyboard.exe 放入 keyboard 目录")
    print("4. 运行 ICBC_Automation.exe")

    return True


def build_simple():
    """简化版打包（目录方式）"""
    print("=" * 60)
    print("开始打包工商网银自动化程序（目录版）...")
    print("=" * 60)

    os.chdir(Path(__file__).parent / 'icbc_automation')
    clean_build()
    check_pyinstaller()

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=ICBC_Automation',
        '--windowed',
        '--add-data=config.ini;.',
        '--add-data=../../keyboard;keyboard',
        '--hidden-import=playwright',
        '--hidden-import=loguru',
        '--hidden-import=yaml',
        '--hidden-import=pyautogui',
        '--collect-all=playwright',
        'main.py'
    ]

    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print("打包失败！")
        return False

    print("\n" + "=" * 60)
    print("打包成功！")
    print("=" * 60)
    print(f"程序目录: {Path('dist/ICBC_Automation').absolute()}")

    return True


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='打包工商网银自动化程序')
    parser.add_argument('--simple', action='store_true', help='打包为目录版（非单文件）')
    args = parser.parse_args()

    if args.simple:
        build_simple()
    else:
        build_exe()
