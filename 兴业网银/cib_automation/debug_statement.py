# -*- coding: utf-8 -*-
"""
调试脚本 - 诊断交易明细查询导航问题
"""
import sys
import time
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from main import CIBAutomation
from logger import setup_logger

logger = setup_logger()


def debug_navigation():
    """调试导航到交易明细查询页面"""
    
    # 创建自动化实例
    automation = CIBAutomation("config.yaml")
    
    try:
        # 只执行登录，不采集
        logger.info("=" * 60)
        logger.info("步骤1: 启动浏览器并登录")
        logger.info("=" * 60)
        
        from browser import CIBBrowser
        automation.browser = CIBBrowser(automation.config.browser, automation.config.credentials)
        automation.browser.start()
        automation.browser.navigate_to_login()
        
        # ========== 步骤1: 先输入网盾密码（PIN）==========
        pin_value = automation.config.credentials.pin
        if pin_value:
            logger.info("=" * 60)
            logger.info("步骤1: 自动输入网盾密码（PIN）...")
            import threading
            pin_thread = threading.Thread(
                target=automation._try_auto_enter_pin,
                daemon=True
            )
            pin_thread.start()
            logger.info("PIN 输入线程已启动，等待网盾密码输入完成...")
            time.sleep(8)
        else:
            logger.info("未配置 PIN，跳过网盾密码输入")
        
        # ========== 步骤2: 登录网银 ==========
        logger.info("=" * 60)
        logger.info("步骤2: 登录网银...")
        
        # 尝试自动登录
        login_success = False
        try:
            login_success = automation.browser.login()
        except Exception as e:
            logger.error(f"自动登录异常: {e}")
        
        # 如果自动登录失败，进入半自动模式等待用户操作
        if not login_success:
            logger.info("自动登录未成功，进入半自动模式等待用户完成登录...")
            
            # 等待用户完成登录
            logger.info("等待用户完成登录...")
            if not automation.browser.wait_for_manual_login():
                logger.error("登录取消")
                return
        else:
            logger.info("自动登录成功！")
        
        # 登录成功，开始调试
        page = automation.browser.page
        
        logger.info("=" * 60)
        logger.info("步骤2: 调试首页元素")
        logger.info("=" * 60)
        
        # 截图保存当前页面
        screenshot_path = "debug_homepage.png"
        page.screenshot(path=screenshot_path, full_page=True)
        logger.info(f"首页截图已保存: {screenshot_path}")
        
        # 检查当前URL
        current_url = page.url
        logger.info(f"当前URL: {current_url}")
        
        # 检查是否在iframe中
        frames = page.frames
        logger.info(f"页面帧数量: {len(frames)}")
        for i, frame in enumerate(frames):
            logger.info(f"  帧[{i}]: {frame.url[:80]}...")
        
        # 检查 a#queryUrl 元素
        logger.info("=" * 60)
        logger.info("步骤3: 检查 a#queryUrl 元素")
        logger.info("=" * 60)
        
        # 在主页面中查找
        query_url_locator = page.locator("a#queryUrl")
        count = query_url_locator.count()
        logger.info(f"主页面中 a#queryUrl 元素数量: {count}")
        
        if count > 0:
            for i in range(count):
                try:
                    element = query_url_locator.nth(i)
                    is_visible = element.is_visible()
                    href = element.get_attribute("href") or ""
                    text = element.text_content() or ""
                    bbox = element.bounding_box()
                    logger.info(f"  元素[{i}]: visible={is_visible}, text='{text}', href='{href[:50]}...'")
                    logger.info(f"    位置: x={bbox['x'] if bbox else 'N/A'}, y={bbox['y'] if bbox else 'N/A'}")
                except Exception as e:
                    logger.error(f"  元素[{i}] 检查失败: {e}")
        
        # 尝试在主页面点击
        if count > 0:
            logger.info("尝试在主页面点击 a#queryUrl...")
            try:
                query_url_locator.first.click()
                logger.info("点击成功！")
                time.sleep(3)
                
                # 截图保存跳转后的页面
                screenshot_path2 = "debug_after_click.png"
                page.screenshot(path=screenshot_path2, full_page=True)
                logger.info(f"点击后截图已保存: {screenshot_path2}")
                logger.info(f"点击后URL: {page.url}")
                
                # ========== 测试下载功能 ==========
                if "queryTradeDetail" in page.url:
                    logger.info("=" * 60)
                    logger.info("进入查询页面，开始测试下载功能")
                    logger.info("=" * 60)
                    from debug_download import debug_download
                    debug_download(page, automation.config.collection.statements)
                
                return
            except Exception as e:
                logger.error(f"主页面点击失败: {e}")
        
        # 检查 iframe 中是否有该元素
        logger.info("=" * 60)
        logger.info("步骤4: 检查 iframe 中的元素")
        logger.info("=" * 60)
        
        for i, frame in enumerate(frames):
            if frame == page.main_frame:
                continue
            try:
                locator = frame.locator("a#queryUrl")
                count = locator.count()
                logger.info(f"  帧[{i}] 中 a#queryUrl 元素数量: {count}")
                if count > 0:
                    logger.info(f"  尝试在帧[{i}]中点击...")
                    locator.first.click()
                    logger.info(f"  帧[{i}]点击成功！")
                    time.sleep(3)
                    return
            except Exception as e:
                logger.debug(f"  帧[{i}]检查失败: {e}")
        
        # 尝试通过其他选择器查找
        logger.info("=" * 60)
        logger.info("步骤5: 尝试其他选择器")
        logger.info("=" * 60)
        
        alternative_selectors = [
            "text=查询明细",
            "[href*='queryTradeDetail']",
            ".common-quick-box a",
            "ul.clearfix a",
        ]
        
        for selector in alternative_selectors:
            try:
                locator = page.locator(selector)
                count = locator.count()
                if count > 0:
                    logger.info(f"选择器 '{selector}' 找到 {count} 个元素")
                    # 显示前3个元素的信息
                    for i in range(min(3, count)):
                        try:
                            elem = locator.nth(i)
                            text = elem.text_content() or ""
                            href = elem.get_attribute("href") or ""
                            logger.info(f"  [{i}] text='{text[:20]}', href='{href[:50]}...'")
                        except:
                            pass
            except Exception as e:
                logger.debug(f"选择器 '{selector}' 检查失败: {e}")
        
        # 尝试菜单导航
        logger.info("=" * 60)
        logger.info("步骤6: 尝试菜单导航")
        logger.info("=" * 60)
        
        try:
            # 点击查询中心
            page.locator("text=查询中心").first.click()
            logger.info("点击: 查询中心")
            time.sleep(2)
            page.screenshot(path="debug_after_menu_click.png")
            
            # 尝试多种方式点击交易明细查询
            menu_selectors = [
                "#menu-item-130100",
                "text=交易明细查询",
                ".menu-sub-title:has-text('交易明细查询')",
            ]
            
            for selector in menu_selectors:
                try:
                    locator = page.locator(selector)
                    if locator.count() > 0 and locator.first.is_visible():
                        logger.info(f"找到菜单项: {selector}")
                        locator.first.click()
                        logger.info(f"点击菜单项: {selector}")
                        time.sleep(3)
                        page.screenshot(path="debug_after_detail_click.png")
                        logger.info(f"当前URL: {page.url}")
                        
                        # ========== 测试下载功能 ==========
                        logger.info("=" * 60)
                        logger.info("进入查询页面，开始测试下载功能")
                        logger.info("=" * 60)
                        from debug_download import debug_download
                        debug_download(page, automation.config.collection.statements)
                        
                        return
                except Exception as e:
                    logger.debug(f"选择器 {selector} 失败: {e}")
                    
        except Exception as e:
            logger.error(f"菜单导航失败: {e}")
        
        logger.info("=" * 60)
        logger.info("调试完成")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"调试过程出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        if automation.browser:
            automation.browser.close()


if __name__ == "__main__":
    debug_navigation()
