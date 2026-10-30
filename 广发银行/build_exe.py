# -*- coding: utf-8 -*-
"""
广发银行网银采集程序打包脚本
"""
import subprocess
import sys
from pathlib import Path


def build():
    """使用 PyInstaller 打包程序"""
    
    # 确保在正确的目录
    base_dir = Path(__file__).parent
    cgb_dir = base_dir / "cgb_automation"
    
    print(f"打包目录: {cgb_dir}")
    
    # 安装依赖
    print("安装依赖...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(cgb_dir / "requirements.txt")], check=True)
    
    # 安装 PyInstaller
    print("安装 PyInstaller...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    
    project_root = str(base_dir.parent.resolve())

    # 构建命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "CGB_Automation",
        "--onefile",
        "--windowed",
        "--paths", project_root,
        "--add-data", f"{cgb_dir / 'config.ini'};.",
        "--add-data", f"..\\keyboard;keyboard",
        "--hidden-import", "playwright",
        "--hidden-import", "loguru",
        "--hidden-import", "yaml",
        "--hidden-import", "pyautogui",
        "--hidden-import", "pygetwindow",
        "--hidden-import", "pyscreeze",
        "--hidden-import", "pymsgbox",
        "--hidden-import", "pytweening",
        "--hidden-import", "mouseinfo",
        str(cgb_dir / "main.py")
    ]
    
    print("开始打包...")
    print(f"命令: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, cwd=str(cgb_dir))
    
    if result.returncode == 0:
        print("\n打包成功！")
        print(f"可执行文件位于: {cgb_dir / 'dist' / 'CGB_Automation.exe'}")
    else:
        print("\n打包失败！")
        return result.returncode
    
    return 0


def create_spec():
    """创建 PyInstaller spec 文件"""
    spec_content = '''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('config.ini', '.'), ('../../keyboard', 'keyboard'), ('../../common', 'common'), ('logger.py', '.'), ('config_loader.py', '.'), ('browser.py', '.'), ('statement_collector.py', '.'), ('receipt_collector.py', '.'), ('run_recorder.py', '.')]
binaries = []
hiddenimports = ['playwright', 'loguru', 'yaml', 'pyautogui', 'pygetwindow', 'pyscreeze', 'pymsgbox', 'pytweening', 'mouseinfo', 'rubicon-objc']
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=['C:/wangyingbao'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CGB_Automation',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='NONE',
)
'''
    cgb_dir = Path(__file__).parent / "cgb_automation"
    spec_file = cgb_dir / "CGB_Automation.spec"
    spec_file.write_text(spec_content, encoding='utf-8')
    print(f"spec文件已创建: {spec_file}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="广发银行网银采集程序打包工具")
    parser.add_argument("--spec", action="store_true", help="仅创建 spec 文件")
    args = parser.parse_args()
    
    if args.spec:
        create_spec()
    else:
        sys.exit(build())
