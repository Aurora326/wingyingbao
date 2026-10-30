# -*- coding: utf-8 -*-
"""
打包脚本 - 将宁波银行采集程序打包为可执行文件（含 Chromium 浏览器和外部工具）
"""
import datetime
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 统一使用 UTF-8 输出，避免 Windows 终端因默认 GBK 编码导致的中文打印异常
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def get_chromium_path():
    """获取 Playwright Chromium 浏览器所在目录（非可执行文件本身）"""
    script = """
import sys
try:
    from playwright.sync_api import sync_playwright
    p = sync_playwright().__enter__()
    path = p.chromium.executable_path
    print(path)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, encoding="utf-8"
        )
        chrome_exe = result.stdout.strip()
        if chrome_exe and os.path.exists(chrome_exe):
            chromium_dir = os.path.dirname(chrome_exe)
            print(f"[OK] Chromium 可执行文件: {chrome_exe}")
            print(f"[OK] Chromium 目录: {chromium_dir}")
            return chromium_dir
        else:
            print(f"[WARN] Chromium 路径无效: {chrome_exe}")
            print(f"[WARN] 错误输出: {result.stderr}")
    except Exception as e:
        print(f"[WARN] 获取 Chromium 路径失败: {e}")
    return None


def clean_build():
    """清理旧的构建文件"""
    print("=" * 60)
    print("清理旧构建文件...")
    print("=" * 60)

    dirs_to_remove = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_remove:
        for path in Path('.').glob(dir_name):
            if path.is_dir():
                shutil.rmtree(path)
                print(f"[OK] 已删除目录: {path}")

    for path in Path('.').glob('*.spec'):
        if path.is_file():
            path.unlink()
            print(f"[OK] 已删除文件: {path}")

    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.pyc'):
                os.remove(os.path.join(root, file))


def build():
    """执行 PyInstaller 打包"""
    print("=" * 60)
    print("开始打包宁波银行采集程序（含 Chromium 浏览器）...")
    print("=" * 60)

    chromium_path = get_chromium_path()
    if chromium_path:
        print(f"[OK] 找到 Chromium: {chromium_path}")
    else:
        print("[WARN] 未找到 Chromium，将不包含浏览器（目标机器需自行安装）")

    clean_build()

    project_root = str(Path(__file__).resolve().parent.parent.parent)
    nbcb_dir = str(Path(__file__).resolve().parent)

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=宁波银行采集程序',
        '--console',
        '--onefile',
        '--clean',
        '--noconfirm',
        '--add-data=config.ini;.',
        f'--paths={project_root}',
        f'--paths={nbcb_dir}',
        # 第三方库
        '--hidden-import=playwright',
        '--hidden-import=playwright.sync_api',
        '--hidden-import=loguru',
        '--hidden-import=pyautogui',
        '--hidden-import=PIL',
        '--hidden-import=requests',
        '--hidden-import=yaml',
        # 宁波银行本地模块
        '--hidden-import=browser',
        '--hidden-import=config_loader',
        '--hidden-import=logger',
        '--hidden-import=login_manager',
        '--hidden-import=pin_inputter',
        '--hidden-import=popup_handler',
        '--hidden-import=run_recorder',
        '--hidden-import=statement_collector',
        '--hidden-import=receipt_collector',
        '--hidden-import=offline_statement_importer',
        '--hidden-import=cabinet_controller',
        # common 公共模块
        '--hidden-import=common.base_browser',
        '--hidden-import=common.captcha_solver',
        '--hidden-import=common.config_loader',
        '--hidden-import=common.download_helper',
        '--hidden-import=common.keyboard_helper',
        '--hidden-import=common.logger',
        '--hidden-import=common.run_recorder',
        '--hidden-import=common.cabinet_controller',
        # 排除不必要的包以减小体积
        '--exclude-module=matplotlib',
        '--exclude-module=numpy',
        '--exclude-module=pandas',
        'main.py'
    ]

    if chromium_path and os.path.exists(chromium_path):
        cmd.extend(['--add-data', f'{chromium_path};chromium'])
        print(f"[OK] 将 Chromium 打包进程序（约 150MB）")

    print("\n执行打包命令...")
    print(' '.join(cmd))
    subprocess.check_call(cmd)

    print("\n" + "=" * 60)
    print("[OK] PyInstaller 打包完成！")
    print(f"[OK] 输出文件: dist/宁波银行采集程序.exe")
    print("=" * 60)


def _copy_tool_dir(src, dest, excludes=None):
    """复制工具目录，跳过开发/日志等不需要的文件"""
    excludes = set(excludes or [])
    for item in os.listdir(src):
        if item.lower() in excludes:
            continue
        src_path = os.path.join(src, item)
        dest_path = os.path.join(dest, item)
        if os.path.isdir(src_path):
            shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
        else:
            shutil.copy2(src_path, dest_path)


def create_package():
    """创建发布包（exe + 配置 + 工具 + 启动脚本 + 说明）"""
    print("\n" + "=" * 60)
    print("开始制作发布包...")
    print("=" * 60)

    version = datetime.datetime.now().strftime("%Y%m%d")
    package_name = f"宁波银行采集程序_v{version}"
    package_dir = f"dist/{package_name}"

    os.makedirs(package_dir, exist_ok=True)

    exe_source = 'dist/宁波银行采集程序.exe'
    if os.path.exists(exe_source):
        shutil.copy(exe_source, package_dir)
        print(f"[OK] 复制可执行文件: {exe_source}")

    files_to_copy = ['config.ini', 'access_token.conf']
    for file_path in files_to_copy:
        if os.path.exists(file_path):
            shutil.copy(file_path, package_dir)
            print(f"[OK] 复制 {file_path}")

    tools_dir = os.path.join(package_dir, 'tools')
    os.makedirs(tools_dir, exist_ok=True)

    # 复制 AiKeyboard 完整目录（含 DLL、驱动、证书等），扁平化放入 tools/
    aikeyboard_src = 'c:/wangyingbao/keyboard'
    if os.path.exists(aikeyboard_src):
        _copy_tool_dir(aikeyboard_src, tools_dir, excludes={
            'logs', '.vscode', 'log.txt', 'test_case.py'
        })
        print("[OK] 复制 AiKeyboard 完整工具目录到 tools/")

    # 复制 MB 机柜完整目录（含 mb.conf），扁平化放入 tools/
    mb_src = 'c:/wangyingbao/m2'
    if os.path.exists(mb_src):
        _copy_tool_dir(mb_src, tools_dir, excludes={'logs'})
        print("[OK] 复制 MB 机柜完整工具目录到 tools/")

    bat_content = '''@echo off
chcp 65001 >nul
echo ========================================
echo  宁波银行企业网银采集程序
echo ========================================
echo.

REM 设置工具路径到环境变量
set "PATH=%~dp0tools;%PATH%"

REM 运行主程序
"%~dp0宁波银行采集程序.exe" %*

pause
'''
    with open(os.path.join(package_dir, '启动程序.bat'), 'w', encoding='utf-8') as f:
        f.write(bat_content)
    print("[OK] 创建启动脚本: 启动程序.bat")

    readme_content = f"""宁波银行企业网银采集程序
版本: {version}

[使用说明]
1. 修改 config.ini 配置文件，填入正确的账号密码
2. 如需百度 OCR 自动识别验证码，编辑 access_token.conf
3. 双击运行"启动程序.bat"
4. 程序会自动打开浏览器并执行采集

[目录结构]
宁波银行采集程序.exe  - 主程序（已内置 Chromium 浏览器）
config.ini            - 配置文件
access_token.conf     - 百度 OCR access_token（可选）
启动程序.bat          - 启动脚本（推荐用此启动）
tools/                - 外部工具目录（已包含完整依赖和配置文件）
  - AiKeyboard.exe    - 安全键盘输入工具（含 DLL、驱动、证书）
  - mb.exe            - MB 机柜控制工具（含 mb.conf）

[配置文件说明]
编辑 config.ini：
  [credentials]
  username = 你的企业编号
  password = 你的登录密码
  pin = UKey PIN码（如需要）

[注意事项]
- 本程序已内置 Chromium 浏览器，无需单独安装 Chrome/Edge
- 首次运行可能需要等待浏览器初始化
- 如果使用 UKey 登录，请确保 UKey 已插入电脑
- 如果使用 MB 机柜，请确保网络连接正常
- 程序退出时会强制关闭本机所有 chrome.exe 进程（可在配置中关闭）

[技术支持]
如有问题请检查程序目录下的 logs/ 日志文件

[系统要求]
- Windows 10/11 64位
- 4GB 以上内存
- 500MB 以上磁盘空间
"""
    with open(os.path.join(package_dir, '使用说明.txt'), 'w', encoding='utf-8') as f:
        f.write(readme_content)
    print("[OK] 创建使用说明: 使用说明.txt")

    zip_path = f"dist/{package_name}.zip"
    shutil.make_archive(f"dist/{package_name}", 'zip', package_dir)

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"[OK] 发布包已创建: {zip_path}")
    print(f"[OK] 文件大小: {size_mb:.1f} MB")
    print("=" * 60)
    return zip_path


def main():
    """主函数"""
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    if not Path('main.py').exists():
        print("[ERROR] 请在包含 main.py 的目录中运行此脚本")
        sys.exit(1)

    # 检查 PyInstaller 是否安装
    try:
        subprocess.run(
            [sys.executable, '-m', 'PyInstaller', '--version'],
            capture_output=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[INFO] 正在安装 PyInstaller...")
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', 'pyinstaller']
        )

    build()
    zip_path = create_package()
    sys.exit(0 if zip_path and os.path.exists(zip_path) else 1)


if __name__ == "__main__":
    main()
