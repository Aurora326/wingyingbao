#!/usr/bin/env python3
"""
打包 GF3 机柜管理界面为可执行文件
"""
import os
import shutil
import subprocess
import sys


def clean_build():
    dirs_to_remove = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_remove:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"[OK] 清理 {dir_name}")


def build():
    print("=" * 60)
    print("开始打包 GF3 机柜管理工具...")
    print("=" * 60)

    clean_build()

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=GF3机柜管理工具',
        '--windowed',
        '--onefile',
        '--add-data=gf3.exe;.',
        'gf3_manager.py'
    ]

    print("\n执行打包命令...")
    subprocess.check_call(cmd)

    print("\n" + "=" * 60)
    print("打包完成！")
    print("输出文件: dist/GF3机柜管理工具.exe")
    print("=" * 60)


if __name__ == '__main__':
    try:
        try:
            subprocess.run(['pyinstaller', '--version'], capture_output=True, check=True)
        except FileNotFoundError:
            print("正在安装 PyInstaller...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])
        build()
    except Exception as e:
        print(f"\n[ERROR] 打包失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
