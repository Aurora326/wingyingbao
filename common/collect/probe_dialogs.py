"""弹窗诊断：在工行登录弹窗打开时运行，打印窗口与按钮信息。

用法：python probe_dialogs.py
"""
from __future__ import annotations

from common.collect import windows_ui


def main() -> None:
    print("== 当前窗口（含 证书/密码/U盾）")
    for title in windows_ui.list_windows(("证书", "密码", "U盾")):
        print(" -", title)

    for keyword in ("选择证书", "输入密码", "校验U盾密码", "修改初始密码"):
        info = windows_ui.describe_window((keyword,))
        if info:
            print(f"== {keyword}: title={info.get('title')!r} backend={info.get('backend')}")
            print("   buttons:", info.get("buttons"))
            print("   edits:", info.get("edits"))


if __name__ == "__main__":
    main()
