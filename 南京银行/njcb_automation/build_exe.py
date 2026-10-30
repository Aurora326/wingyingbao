#!/usr/bin/env python3
"""
打包南京银行采集程序为可执行文件（包含 Chromium 浏览器）
"""
import os
import shutil
import subprocess
import sys


def get_chromium_path():
    """获取 Playwright Chromium 浏览器路径"""
    try:
        script = '''
import sys
try:
    from playwright.sync_api import sync_playwright
    p = sync_playwright().__enter__()
    path = p.chromium.executable_path
    print(path)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
'''
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True, text=True
        )
        chrome_exe = result.stdout.strip()
        if chrome_exe and os.path.exists(chrome_exe):
            chromium_dir = os.path.dirname(chrome_exe)
            print(f"  Chromium 可执行文件: {chrome_exe}")
            print(f"  Chromium 目录: {chromium_dir}")
            return chromium_dir
        else:
            print(f"  Chromium 路径无效: {chrome_exe}")
            print(f"  错误输出: {result.stderr}")
    except Exception as e:
        print(f"获取 Chromium 路径失败: {e}")
    return None


def clean_build():
    """清理旧的构建文件"""
    dirs_to_remove = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_remove:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"[OK] 清理 {dir_name}")
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.pyc'):
                os.remove(os.path.join(root, file))


def build():
    """执行打包"""
    print("=" * 60)
    print("开始打包南京银行采集程序（包含 Chromium）...")
    print("=" * 60)

    chromium_path = get_chromium_path()
    if chromium_path:
        print(f"[OK] 找到 Chromium: {chromium_path}")
    else:
        print("[WARN] 未找到 Chromium，将不包含浏览器（目标机器需自行安装）")

    clean_build()

    # 项目根目录（用于引用 common 模块）
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    njcb_dir = os.path.dirname(os.path.abspath(__file__))

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=南京银行采集程序',
        '--console',
        '--onefile',
        '--add-data=config.ini;.',
        '--add-data=template;template',
        f'--paths={project_root}',
        f'--paths={njcb_dir}',
        # 第三方库
        '--hidden-import=playwright',
        '--hidden-import=playwright.sync_api',
        '--hidden-import=loguru',
        '--hidden-import=pyautogui',
        '--hidden-import=PIL',
        '--hidden-import=requests',
        # 南京银行本地模块
        '--hidden-import=browser',
        '--hidden-import=config_loader',
        '--hidden-import=logger',
        '--hidden-import=login_manager',
        '--hidden-import=pin_inputter',
        '--hidden-import=popup_handler',
        '--hidden-import=run_recorder',
        '--hidden-import=statement_collector',
        '--hidden-import=receipt_collector',
        '--hidden-import=reconciliation_collector',
        '--hidden-import=convert_statement_to_template',
        '--hidden-import=cabinet_controller',
        # common 模块
        '--hidden-import=common.base_browser',
        '--hidden-import=common.captcha_solver',
        '--hidden-import=common.config_loader',
        '--hidden-import=common.download_helper',
        '--hidden-import=common.keyboard_helper',
        '--hidden-import=common.logger',
        '--hidden-import=common.run_recorder',
        '--hidden-import=common.cabinet_controller',
        'main.py'
    ]

    if chromium_path and os.path.exists(chromium_path):
        cmd.extend(['--add-data', f'{chromium_path};chromium'])
        print(f"[OK] 将 Chromium 打包进程序（约 150MB）")

    print("\n执行打包命令...")
    subprocess.check_call(cmd)

    print("\n" + "=" * 60)
    print("打包完成！")
    print("输出文件: dist/南京银行采集程序.exe")
    print("=" * 60)


def create_package():
    """创建发布包"""
    import datetime

    version = datetime.datetime.now().strftime("%Y%m%d")
    package_name = f"南京银行采集程序_v{version}"
    package_dir = f"dist/{package_name}"

    os.makedirs(package_dir, exist_ok=True)

    exe_source = 'dist/南京银行采集程序.exe'
    if os.path.exists(exe_source):
        shutil.copy(exe_source, package_dir)
        print(f"[OK] 复制可执行文件")

    files_to_copy = ['config.ini']
    for file_path in files_to_copy:
        if os.path.exists(file_path):
            shutil.copy(file_path, package_dir)
            print(f"[OK] 复制 {file_path}")

    # 复制模板目录（已打包进 exe，同时保留一份方便查看/修改）
    if os.path.exists('template'):
        template_dest = os.path.join(package_dir, 'template')
        shutil.copytree('template', template_dest, dirs_exist_ok=True)
        print(f"[OK] 复制 template 目录")

    tools_dir = os.path.join(package_dir, 'tools')
    os.makedirs(tools_dir, exist_ok=True)

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
echo  南京银行企业网银采集程序
echo ========================================
echo.

REM 设置工具路径到环境变量
set "PATH=%~dp0tools;%PATH%"

REM 运行主程序
"%~dp0南京银行采集程序.exe" %*

pause
'''
    with open(os.path.join(package_dir, '启动程序.bat'), 'w', encoding='utf-8') as f:
        f.write(bat_content)
    print("[OK] 创建启动脚本")

    readme_content = f"""南京银行企业网银采集程序
版本: {version}

[使用说明]
1. 修改 config.ini 配置文件，填入正确的账号密码
2. 双击运行"启动程序.bat"
3. 程序会自动打开浏览器并执行采集

[目录结构]
南京银行采集程序.exe  - 主程序（包含 Chromium 浏览器）
config.ini            - 配置文件
启动程序.bat          - 启动脚本（推荐用此启动）
tools/                - 外部工具目录（已包含完整依赖和配置文件）
  - AiKeyboard.exe    - 安全键盘输入工具（含 DLL、驱动、证书）
  - mb.exe            - MB 机柜控制工具（含 mb.conf）

[配置文件说明]
编辑 config.ini：
  [credentials]
  username = 你的用户名
  password = 你的密码
  pin = 你的Key盾密码

[注意事项]
- 本程序已内置 Chromium 浏览器，无需单独安装 Chrome
- 首次运行可能需要等待浏览器初始化
- 如果使用 Key 盾登录，请确保 Key 盾已插入电脑
- 如果使用 MB 机柜，请确保网络连接正常

[技术支持]
如有问题请检查程序目录下的 logs/app.log 日志文件

[系统要求]
- Windows 10/11 64位
- 4GB 以上内存
- 500MB 以上磁盘空间
"""
    with open(os.path.join(package_dir, '使用说明.txt'), 'w', encoding='utf-8') as f:
        f.write(readme_content)
    print("[OK] 创建使用说明")

    zip_path = f"dist/{package_name}.zip"
    shutil.make_archive(f"dist/{package_name}", 'zip', package_dir)

    print(f"\n" + "=" * 60)
    print(f"[OK] 发布包已创建: {zip_path}")
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  文件大小: {size_mb:.1f} MB")
    print("=" * 60)
    return zip_path


if __name__ == '__main__':
    try:
        build()
        create_package()
    except Exception as e:
        print(f"\n[ERROR] 打包失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
