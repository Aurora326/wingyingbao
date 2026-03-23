# -*- coding: utf-8 -*-
"""
安装CFCA扩展到Chrome的脚本
运行后会打开Chrome并提示你手动安装扩展
"""
import os
import sys
import time
import subprocess
from pathlib import Path

def find_chrome():
    """查找Chrome路径"""
    possible_paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in possible_paths:
        if Path(path).exists():
            return path
    return None

def main():
    print("=" * 70)
    print("  CFCA证书扩展安装助手")
    print("=" * 70)
    print()
    
    # 获取扩展路径
    current_dir = Path(__file__).parent.parent
    extension_dir = current_dir / "crx_extracted"
    
    if not extension_dir.exists():
        print(f"[错误] 扩展文件夹不存在: {extension_dir}")
        print("请确保 crx_extracted 文件夹存在")
        input("按回车键退出...")
        return
    
    extension_path = extension_dir.resolve()
    print(f"扩展路径: {extension_path}")
    print()
    
    # 查找Chrome
    chrome_path = find_chrome()
    if not chrome_path:
        print("[错误] 未找到Chrome浏览器")
        input("按回车键退出...")
        return
    
    print(f"Chrome路径: {chrome_path}")
    print()
    
    # 创建用户数据目录
    user_data_dir = Path(__file__).parent / "chrome_user_data"
    user_data_dir.mkdir(exist_ok=True)
    
    print("=" * 70)
    print("  请按以下步骤操作：")
    print("=" * 70)
    print()
    print("1. Chrome会自动打开")
    print("2. 在地址栏输入: chrome://extensions/")
    print("3. 打开右上角【开发者模式】")
    print("4. 点击【加载已解压的扩展程序】")
    print(f"5. 选择文件夹: {extension_path}")
    print("6. 确认CFCA扩展已显示在扩展列表中")
    print("7. 关闭Chrome浏览器")
    print()
    print("注意：安装完成后，后续使用 run.bat 即可自动加载扩展")
    print()
    input("按回车键开始安装...")
    print()
    
    # 启动Chrome
    try:
        subprocess.Popen([
            chrome_path,
            f"--user-data-dir={user_data_dir}",
            "chrome://extensions/"
        ])
        
        print("Chrome已启动，请完成扩展安装...")
        print()
        input("安装完成后请按回车键退出...")
        
    except Exception as e:
        print(f"启动Chrome失败: {e}")
        input("按回车键退出...")

if __name__ == "__main__":
    main()
