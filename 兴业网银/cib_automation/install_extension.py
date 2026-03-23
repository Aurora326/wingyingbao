# -*- coding: utf-8 -*-
"""
辅助安装扩展的脚本
运行后会打开Chrome，让你手动安装扩展
"""
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def _find_chrome_path() -> str:
    """查找系统已安装的 Chrome/Edge 可执行文件路径"""
    possible_paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in possible_paths:
        if Path(path).exists():
            return path
    return ""

def main():
    print("=" * 60)
    print("兴业网银扩展安装助手")
    print("=" * 60)
    print()
    
    # 获取扩展路径
    current_dir = Path(__file__).parent
    extension_path = current_dir.parent / "crx_extracted"
    
    if not extension_path.exists():
        print(f"[错误] 扩展文件夹不存在: {extension_path}")
        print("请确保 crx_extracted 文件夹存在")
        return
    
    print(f"扩展路径: {extension_path}")
    print()
    print("请按以下步骤操作：")
    print("1. 等待Chrome打开")
    print("2. 在Chrome地址栏输入: chrome://extensions/")
    print("3. 打开右上角'开发者模式'")
    print("4. 点击'加载已解压的扩展程序'")
    print(f"5. 选择文件夹: {extension_path}")
    print("6. 安装完成后，关闭Chrome窗口")
    print()
    input("按回车键开始...")
    print()
    
    # 启动Chrome（持久化模式）
    user_data_dir = current_dir / "chrome_user_data"
    user_data_dir.mkdir(exist_ok=True)
    
    with sync_playwright() as p:
        executable_path = _find_chrome_path()
        # 使用launch_persistent_context让扩展保持安装状态
        launch_options = {
            "headless": False,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        }
        if executable_path:
            launch_options["executable_path"] = executable_path
            print(f"使用浏览器: {executable_path}")
        else:
            print("未检测到系统 Chrome/Edge，Playwright 将尝试使用自带 Chromium。")

        context = p.chromium.launch_persistent_context(
            str(user_data_dir),
            **launch_options
        )
        
        page = context.new_page()
        page.goto("chrome://extensions/")
        
        print("Chrome已打开，请在浏览器中完成扩展安装")
        print("安装完成后，关闭浏览器窗口即可")
        print()
        
        # 等待用户关闭浏览器
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        
        context.close()
    
    print()
    print("=" * 60)
    print("扩展安装完成！")
    print("现在可以运行 run.bat 进行网银采集了")
    print("=" * 60)

if __name__ == "__main__":
    main()
