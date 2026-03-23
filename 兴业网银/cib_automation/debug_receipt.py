# -*- coding: utf-8 -*-
"""
电子回单下载调试脚本
"""
import time
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from logger import setup_logger
from config_loader import load_config
from browser import CIBBrowser

logger = setup_logger()

def debug_receipt_download():
    """调试电子回单下载"""
    print("=" * 60)
    print("电子回单下载调试工具")
    print("=" * 60)
    
    # 加载配置
    config = load_config("config.yaml")
    
    # 启动浏览器
    browser = CIBBrowser(config.browser, config.credentials)
    page = browser.start()
    
    try:
        # 导航到登录页
        browser.navigate_to_login()
        
        # 等待手动登录
        print("\n请在浏览器中完成登录...")
        if not browser.wait_for_manual_login(timeout=180):
            print("登录超时")
            return
        
        print("登录成功！")
        time.sleep(2)
        
        # 导航到交易明细页面
        print("\n导航到交易明细页面...")
        for frame in browser.page.frames:
            try:
                locator = frame.locator("a#queryUrl")
                if locator.count() > 0:
                    locator.first.click()
                    print("已点击查询明细")
                    time.sleep(3)
                    break
            except:
                continue
        
        # 查找主frame
        main_frame = browser.page
        for frame in browser.page.frames:
            try:
                if frame.locator("#gbox_list").count() > 0:
                    main_frame = frame
                    print(f"找到主frame: {frame}")
                    break
            except:
                continue
        
        # 设置日期范围（示例：最近30天）
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")
        
        print(f"\n设置日期范围: {start_date_str} 至 {end_date_str}")
        try:
            main_frame.evaluate(f"""
                document.querySelector('input#startDate').value = '{start_date_str}';
                document.querySelector('input#endDate').value = '{end_date_str}';
                document.querySelector('input#startDate').dispatchEvent(new Event('change', {{ bubbles: true }}));
                document.querySelector('input#endDate').dispatchEvent(new Event('change', {{ bubbles: true }}));
            """)
            time.sleep(1)
        except Exception as e:
            print(f"设置日期失败: {e}")
        
        # 点击查询
        print("\n点击查询按钮...")
        try:
            main_frame.locator("input#queryOpeBtn").first.click()
            time.sleep(3)
        except Exception as e:
            print(f"点击查询失败: {e}")
        
        # 检查是否有数据
        try:
            no_data = main_frame.locator("text=无数据显示")
            if no_data.count() > 0 and no_data.first.is_visible(timeout=2000):
                print("当前查询结果为空")
                return
        except:
            pass
        
        print("\n" + "=" * 60)
        print("现在请手动操作：")
        print("1. 在浏览器中点击表头的全选复选框（选择所有交易明细）")
        print("2. 点击'批量打印回单'按钮")
        print("3. 在弹窗中选择'每页两张'")
        print("4. 点击'批量打印'按钮")
        print("=" * 60)
        
        # 监控页面元素变化
        print("\n监控页面元素...")
        check_count = 0
        while True:
            time.sleep(2)
            check_count += 1
            
            # 每10秒报告一次页面状态
            if check_count % 5 == 0:
                print(f"\n[监控中... {check_count * 2}秒]")
                
                # 检查是否有弹窗
                try:
                    for frame in browser.page.frames:
                        # 检查是否有"每页两张"选项
                        double_print = frame.locator("text=每页两张").first
                        if double_print.is_visible(timeout=1000):
                            print("  -> 检测到'每页两张'选项可见")
                            
                        # 检查是否有"批量打印"按钮
                        print_btn = frame.locator("button:has-text('批量打印')").first
                        if print_btn.is_visible(timeout=1000):
                            print("  -> 检测到'批量打印'按钮可见")
                            
                        # 检查是否有iframe弹窗
                        iframes = frame.locator("iframe").all()
                        if iframes:
                            print(f"  -> 检测到 {len(iframes)} 个iframe")
                            
                except Exception as e:
                    pass
            
            # 简单的退出检测
            if check_count > 150:  # 5分钟后自动退出
                print("\n调试时间到，自动退出")
                break
                
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        print("\n关闭浏览器...")
        browser.close()

if __name__ == "__main__":
    debug_receipt_download()
