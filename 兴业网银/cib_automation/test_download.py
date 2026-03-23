# -*- coding: utf-8 -*-
"""
下载功能测试脚本
用于验证浏览器下载是否正常工作
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def test_download():
    """测试浏览器下载功能"""
    print("=" * 60)
    print("测试浏览器下载功能")
    print("=" * 60)
    
    # 设置下载路径
    download_path = Path(__file__).parent / "test_downloads"
    download_path.mkdir(exist_ok=True)
    print(f"下载路径: {download_path}")
    
    with sync_playwright() as p:
        # 启动浏览器，设置下载路径
        context = p.chromium.launch_persistent_context(
            str(Path(__file__).parent / "test_user_data"),
            headless=False,
            downloads_path=str(download_path),
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--start-maximized",
            ]
        )
        
        page = context.new_page()
        
        # 测试1: 下载测试文件
        print("\n测试1: 访问测试下载页面...")
        page.goto("https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf")
        time.sleep(3)
        
        # 检查下载目录中的文件
        files = list(download_path.iterdir())
        if files:
            print(f"✓ 测试1通过: 成功下载文件到 {download_path}")
            for f in files:
                print(f"  - {f.name}")
        else:
            print("✗ 测试1失败: 未检测到下载的文件")
        
        # 清理测试文件
        for f in files:
            f.unlink()
        
        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)
        print(f"\n浏览器下载路径已设置为: {download_path}")
        print("在兴业网银中点击下载后，文件应该会被保存到这个目录。")
        
        input("\n按 Enter 键关闭浏览器...")
        context.close()

if __name__ == "__main__":
    test_download()
