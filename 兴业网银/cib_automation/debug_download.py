# -*- coding: utf-8 -*-
"""
下载功能调试脚本
用于手动测试和调试下载功能
"""
import time
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

def debug_download():
    """调试下载功能"""
    print("=" * 60)
    print("兴业网银下载调试工具")
    print("=" * 60)
    print("\n这个脚本会启动浏览器，你可以：")
    print("1. 手动登录网银")
    print("2. 手动查询并点击下载")
    print("3. 观察下载行为")
    print("\n请按以下步骤操作：")
    print("- 在浏览器中完成登录")
    print("- 导航到交易明细查询页面")
    print("- 点击查询，然后点击下载")
    print("- 观察下载的文件")
    
    # 设置下载路径
    download_path = Path(__file__).parent / "downloads"
    download_path.mkdir(exist_ok=True)
    print(f"\n浏览器下载目录: {download_path}")
    
    # 清理之前的下载
    for f in download_path.iterdir():
        if f.is_file():
            print(f"清理旧文件: {f.name}")
            f.unlink()
    
    with sync_playwright() as p:
        # 启动浏览器
        user_data_dir = Path(__file__).parent / "chrome_user_data"
        user_data_dir.mkdir(exist_ok=True)
        
        context = p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            downloads_path=str(download_path),
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--start-maximized",
            ]
        )
        
        page = context.new_page()
        page.set_default_timeout(30000)
        
        # 访问登录页
        print("\n正在打开登录页面...")
        page.goto("https://corporatebank.cib.com.cn/firm/main/login/login")
        
        print("\n" + "=" * 60)
        print("请在浏览器中完成登录并下载文件")
        print("=" * 60)
        
        # 监控下载
        last_files = set()
        check_count = 0
        
        while True:
            time.sleep(2)
            check_count += 1
            
            # 检查新下载的文件
            current_files = set(f.name for f in download_path.iterdir() if f.is_file())
            new_files = current_files - last_files
            
            if new_files:
                print(f"\n[检测到新文件]")
                for fname in new_files:
                    fpath = download_path / fname
                    fsize = fpath.stat().st_size
                    print(f"  文件名: {fname}")
                    print(f"  大小: {fsize} bytes ({fsize/1024:.2f} KB)")
                    
                    # 检查文件类型
                    with open(fpath, 'rb') as f:
                        header = f.read(100)
                        if b'<html' in header.lower() or b'<!doctype' in header.lower():
                            print(f"  ⚠️ 警告: 这是一个HTML文件，不是Excel！")
                            print(f"  可能原因: 文件还在生成中就被下载了")
                        elif header[:4] == b'PK\x03\x04':
                            print(f"  ✅ 这是一个ZIP/XLSX文件（正常的Excel文件）")
                        elif header[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                            print(f"  ✅ 这是一个XLS文件（正常的Excel文件）")
                        else:
                            print(f"  ? 未知文件类型，文件头: {header[:20]}")
                
                last_files = current_files
            
            # 每30秒提示一次
            if check_count % 15 == 0:
                print(f"\n[监控中...] 已运行 {check_count * 2} 秒")
                print("输入 'q' 退出，'c' 清理下载目录: ", end="", flush=True)
                
                # 简单的输入检测（非阻塞）
                import sys
                import select
                if select.select([sys.stdin], [], [], 0)[0]:
                    cmd = sys.stdin.readline().strip().lower()
                    if cmd == 'q':
                        break
                    elif cmd == 'c':
                        for f in download_path.iterdir():
                            if f.is_file():
                                f.unlink()
                        print("下载目录已清理")
                        last_files = set()
        
        print("\n" + "=" * 60)
        print("调试结束")
        print("=" * 60)
        print(f"下载目录: {download_path}")
        print("文件列表:")
        for f in download_path.iterdir():
            if f.is_file():
                print(f"  - {f.name} ({f.stat().st_size} bytes)")
        
        context.close()

if __name__ == "__main__":
    debug_download()
