# -*- coding: utf-8 -*-
"""
元素调试工具
用于查看页面上所有可点击的元素
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def debug_page_elements():
    """调试页面元素"""
    print("=" * 60)
    print("页面元素调试工具")
    print("=" * 60)
    print("\n这个工具会：")
    print("1. 启动浏览器")
    print("2. 让你登录网银")
    print("3. 分析当前页面的所有可点击元素")
    print("\n请按以下步骤操作：")
    print("- 在浏览器中登录网银")
    print("- 停留在交易明细页面")
    print("- 按 Enter 键继续分析")
    
    with sync_playwright() as p:
        # 启动浏览器
        context = p.chromium.launch_persistent_context(
            str(Path(__file__).parent / "chrome_user_data"),
            headless=False,
            args=["--no-sandbox", "--start-maximized"]
        )
        
        page = context.new_page()
        page.set_default_timeout(30000)
        
        # 访问登录页
        print("\n正在打开登录页面...")
        page.goto("https://corporatebank.cib.com.cn/firm/main/login/login")
        
        print("\n请完成登录，然后停留在交易明细页面")
        input("\n登录完成后，请确保看到左侧菜单（包含'回单'），然后按 Enter 键继续...")
        
        # 分析页面元素
        print("\n" + "=" * 60)
        print("分析页面元素...")
        print("=" * 60)
        
        # 获取所有元素
        elements_info = page.evaluate("""() => {
            const results = [];
            const allElements = document.querySelectorAll('*');
            
            for (const el of allElements) {
                const text = el.textContent?.trim();
                const rect = el.getBoundingClientRect();
                
                // 只收集有文本、可见、在合理位置的元素
                if (text && text.length > 0 && text.length < 50 && 
                    rect.width > 0 && rect.height > 0 &&
                    el.offsetParent !== null) {
                    
                    results.push({
                        tag: el.tagName,
                        text: text,
                        x: Math.round(rect.left),
                        y: Math.round(rect.top),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        class: el.className?.substring(0, 50),
                        id: el.id
                    });
                }
            }
            
            return results;
        }""")
        
        # 过滤并显示相关元素
        print("\n【左侧菜单区域元素】（x < 250）:")
        print("-" * 60)
        menu_elements = [e for e in elements_info if e['x'] < 250 and e['y'] > 100 and e['height'] > 15]
        for i, elem in enumerate(menu_elements[:30]):  # 只显示前30个
            print(f"{i+1}. {elem['tag']} | text='{elem['text']}' | x={elem['x']}, y={elem['y']} | class={elem['class'][:30]}")
        
        # 查找特定关键词
        print("\n" + "=" * 60)
        print("【包含'回单'的元素】:")
        print("-" * 60)
        receipt_elements = [e for e in elements_info if '回单' in e['text']]
        for elem in receipt_elements:
            print(f"- {elem['tag']} | text='{elem['text']}' | x={elem['x']}, y={elem['y']} | id={elem['id']} | class={elem['class']}")
        
        print("\n" + "=" * 60)
        print("【包含'电子回单查询'的元素】:")
        print("-" * 60)
        query_elements = [e for e in elements_info if '电子回单查询' in e['text']]
        for elem in query_elements:
            print(f"- {elem['tag']} | text='{elem['text']}' | x={elem['x']}, y={elem['y']} | id={elem['id']} | class={elem['class']}")
        
        # 生成Playwright选择器建议
        print("\n" + "=" * 60)
        print("【Playwright选择器建议】:")
        print("-" * 60)
        
        if receipt_elements:
            elem = receipt_elements[0]
            print(f"回单菜单选择器:")
            print(f"  - text='{elem['text']}'")
            print(f"  - .{elem['class'].split(' ')[0]} >> text='{elem['text']}'")
            if elem['id']:
                print(f"  - #{elem['id']}")
        
        if query_elements:
            elem = query_elements[0]
            print(f"\n电子回单查询选择器:")
            print(f"  - text='{elem['text']}'")
            print(f"  - .{elem['class'].split(' ')[0]} >> text='{elem['text']}'")
            if elem['id']:
                print(f"  - #{elem['id']}")
        
        print("\n" + "=" * 60)
        print("调试完成")
        print("=" * 60)
        
        input("\n按 Enter 键关闭浏览器...")
        context.close()

if __name__ == "__main__":
    debug_page_elements()
