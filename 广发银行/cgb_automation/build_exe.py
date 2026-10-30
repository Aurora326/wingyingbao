#!/usr/bin/env python3
"""
打包广发银行采集程序为可执行文件（包含 Chromium 浏览器）
"""
import os
import shutil
import subprocess
import sys


def get_chromium_path():
    """获取 Playwright Chromium 浏览器路径"""
    try:
        # 尝试多种方式获取 Chromium 路径
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
            # 返回 chrome-win64 目录
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
    
    # 删除 .pyc 文件
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.pyc'):
                os.remove(os.path.join(root, file))


def build():
    """执行打包"""
    print("=" * 60)
    print("开始打包广发银行采集程序（包含 Chromium）...")
    print("=" * 60)
    
    # 获取 Chromium 路径
    chromium_path = get_chromium_path()
    if chromium_path:
        print(f"[OK] 找到 Chromium: {chromium_path}")
    else:
        print("[WARN] 未找到 Chromium，将不包含浏览器（目标机器需自行安装）")
    
    # 清理旧文件
    clean_build()
    
    # 构建 PyInstaller 命令（使用 python -m pyinstaller）
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=广发银行采集程序',
        '--console',
        '--onefile',
        '--add-data=config.ini;.',
        '--add-binary=c:/wangyingbao/keyboard/AiKeyboard.exe;keyboard',
        '--paths=c:/wangyingbao',
        '--hidden-import=playwright',
        '--hidden-import=playwright.sync_api',
        '--hidden-import=pandas',
        '--hidden-import=openpyxl',
        '--hidden-import=xlwt',
        '--hidden-import=pyautogui',
        '--hidden-import=keyboard',
        '--hidden-import=popup_handler',
        '--hidden-import=pin_inputter',
        '--hidden-import=login_manager',
        '--hidden-import=common.config_loader',
        '--hidden-import=common.logger',
        '--hidden-import=common.cabinet_controller',
        '--hidden-import=common.run_recorder',
        'main.py'
    ]
    
    # 添加 Chromium（如果找到）
    if chromium_path and os.path.exists(chromium_path):
        # 使用相对路径存放 Chromium
        cmd.extend(['--add-data', f'{chromium_path};chromium'])
        print(f"[OK] 将 Chromium 打包进程序（约 150MB）")
    
    print("\n执行打包命令...")
    subprocess.check_call(cmd)
    
    print("\n" + "=" * 60)
    print("打包完成！")
    print("输出文件: dist/广发银行采集程序.exe")
    print("=" * 60)


def create_package():
    """创建发布包"""
    import datetime
    
    version = datetime.datetime.now().strftime("%Y%m%d")
    package_name = f"广发银行采集程序_v{version}"
    package_dir = f"dist/{package_name}"
    
    # 创建发布目录
    os.makedirs(package_dir, exist_ok=True)
    
    # 复制可执行文件
    exe_source = 'dist/广发银行采集程序.exe'
    if os.path.exists(exe_source):
        shutil.copy(exe_source, package_dir)
        print(f"[OK] 复制可执行文件")
    
    # 复制必要的配置文件
    files_to_copy = [
        'config.ini',
    ]
    
    for file_path in files_to_copy:
        if os.path.exists(file_path):
            shutil.copy(file_path, package_dir)
            print(f"[OK] 复制 {file_path}")
    
    # 创建外部工具目录
    tools_dir = os.path.join(package_dir, 'tools')
    os.makedirs(tools_dir, exist_ok=True)
    
    # 复制外部工具（不打包进 exe，减小体积）
    external_tools = [
        ('c:/wangyingbao/keyboard/AiKeyboard.exe', 'AiKeyboard.exe'),
        ('c:/wangyingbao/m2/mb.exe', 'mb.exe'),
    ]
    
    for src, dest_name in external_tools:
        if os.path.exists(src):
            shutil.copy(src, os.path.join(tools_dir, dest_name))
            print(f"[OK] 复制工具 {dest_name}")
    
    # 创建启动脚本
    bat_content = '''@echo off
chcp 65001 >nul
echo ========================================
echo  广发银行企业网银采集程序
echo ========================================
echo.

REM 设置工具路径到环境变量
set "PATH=%~dp0tools;%PATH%"

REM 运行主程序
"%~dp0广发银行采集程序.exe" %*

pause
'''
    
    with open(os.path.join(package_dir, '启动程序.bat'), 'w', encoding='utf-8') as f:
        f.write(bat_content)
    print("[OK] 创建启动脚本")
    
    # 创建说明文件
    readme_content = f"""广发银行企业网银采集程序
版本: {version}

[使用说明]
1. 修改 config.ini 配置文件，填入正确的账号密码
2. 双击运行"启动程序.bat"
3. 程序会自动打开浏览器并执行采集

[目录结构]
广发银行采集程序.exe  - 主程序（包含 Chromium 浏览器）
config.ini            - 配置文件
启动程序.bat          - 启动脚本（推荐用此启动）
tools/                - 外部工具目录
  - AiKeyboard.exe    - 安全键盘输入工具
  - mb.exe            - MB 机柜控制工具（如需要）

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
    
    # 打包成 zip
    zip_path = f"dist/{package_name}.zip"
    shutil.make_archive(f"dist/{package_name}", 'zip', package_dir)
    
    print(f"\n" + "=" * 60)
    print(f"[OK] 发布包已创建: {zip_path}")
    
    # 计算文件大小
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  文件大小: {size_mb:.1f} MB")
    print("=" * 60)
    
    return zip_path


if __name__ == '__main__':
    try:
        # 检查 pyinstaller
        try:
            subprocess.run(['pyinstaller', '--version'], capture_output=True, check=True)
        except FileNotFoundError:
            print("正在安装 PyInstaller...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])
        
        build()
        create_package()
        
    except Exception as e:
        print(f"\n[ERROR] 打包失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
