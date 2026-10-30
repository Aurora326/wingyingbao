# -*- coding: utf-8 -*-
"""忠实复现：用真实 NJCBBrowser 启动路径 + 真实机柜插 U 盾 + 真实 login_manager 代码，
测量密码框到底丢在哪一位、丢在 OS 层还是 OCX 层。

放在项目目录内运行，保证 import 与生产一致。
用法: python _repro_password.py [轮数]
"""
import ctypes
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from logger import setup_logger  # noqa: E402
from config_loader import load_config  # noqa: E402
from browser import NJCBBrowser  # noqa: E402
from cabinet_controller import create_cabinet_controller  # noqa: E402

logger = setup_logger("INFO", "./logs/_repro_password.log", 7)

# 生产（打包 exe）实际使用的 AiKeyboard 路径：exe 同级的 tools 目录，在共享盘上
SHARE_AIKB = r"\\192.168.200.1\rpa_s\GJTF\网银机器人\南京银行\南京银行采集程序\tools\AiKeyboard.exe"

# --share 参数：强制用共享盘那份 AiKeyboard，复刻生产
if "--share" in sys.argv:
    import common.keyboard_helper as _kh
    _kh.get_aikeyboard_path = lambda: Path(SHARE_AIKB)
    print(f"[强制] AiKeyboard 路径 = {SHARE_AIKB}")
    print(f"[存在] {Path(SHARE_AIKB).exists()}")

user32 = ctypes.windll.user32

SPY = """
() => {
  if (window.__rec) { document.removeEventListener('keydown', window.__rec, true); }
  if (window.__ft) { clearInterval(window.__ft); }
  window.__keys = [];
  window.__focus = [];
  window.__rec = (e) => { window.__keys.push({k: e.key, t: performance.now(), code: e.code}); };
  document.addEventListener('keydown', window.__rec, true);
  window.__ft = setInterval(() => {
    const a = document.activeElement;
    const id = a ? (a.id || a.tagName) : 'null';
    const last = window.__focus[window.__focus.length - 1];
    if (last && last.id === id) { last.n++; } else { window.__focus.push({id, n: 1}); }
  }, 50);
}
"""


if "--slow" in sys.argv:
    # 放慢注入速度（生产用默认 500ms），验证"慢一点能不能不丢"
    import subprocess as _sp
    import common.keyboard_helper as _kh2
    _INTERVAL = 1200
    _TIMEOUT = max(10, int(_INTERVAL * 14 / 1000) + 10)

    def _slow_input(text):
        p = _kh2.get_aikeyboard_path()
        if not p:
            return False
        _kh2.switch_to_english_layout()
        time.sleep(2)
        r = _sp.run([str(p), "input", "-k", text, "-i", str(_INTERVAL)],
                    capture_output=True, text=True, timeout=_TIMEOUT, cwd=str(p.parent))
        return r.returncode == 0

    _kh2.input_with_aikeyboard = _slow_input
    import login_manager as _lm
    _lm.input_with_aikeyboard = _slow_input
    print(f"[放慢] interval={_INTERVAL}ms timeout={_TIMEOUT}s")


def fg_title():
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return hwnd, buf.value


def collect(page, tag):
    """页面侧状态：焦点、几何、真实点击坐标、点击点下方元素。"""
    st = page.evaluate("""() => ({
        hasFocus: document.hasFocus(),
        active: document.activeElement ? (document.activeElement.tagName + '#' +
                (document.activeElement.id || '') + '.' + (document.activeElement.placeholder || '')) : 'null',
        geom: {sx: window.screenX, sy: window.screenY, ow: window.outerWidth, oh: window.outerHeight,
               iw: window.innerWidth, ih: window.innerHeight},
        screen: {w: screen.width, h: screen.height},
    })""")
    print(f"  [{tag}] hasFocus={st['hasFocus']} active={st['active']}")
    print(f"        geom={st['geom']} screen={st['screen']}")
    return st


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    config = load_config()
    cabinet_config = config.get_cabinet_config()
    print("机柜:", cabinet_config.type, "manual_mode=", cabinet_config.manual_mode)

    cabinet = None
    browser = None
    if cabinet_config.type != "mock" and not cabinet_config.manual_mode:
        cabinet = create_cabinet_controller(cabinet_config)

    try:
        if cabinet:
            print("步骤0: 插入 U 盾...")
            print("   connect() ->", cabinet.connect())
            print("   等待 10 秒让系统识别 U 盾...")
            time.sleep(10)

        browser = NJCBBrowser(config.browser, config.credentials,
                              cabinet_config=cabinet_config, captcha_config=config.captcha)
        browser.start()
        browser.navigate_to_login()
        browser._click_ukey_tab()
        time.sleep(2)

        page = browser.page
        page.bring_to_front()
        time.sleep(0.5)
        print("前台窗口:", fg_title()[1][:50])
        collect(page, "初始")

        inp = browser._locate_password_input()
        if not inp:
            print("!! 找不到密码框（U 盾可能未识别）")
            return

        if "--full" in sys.argv:
            print("\n=== 端到端：直接调用 browser.login() ===")
            t0 = time.time()
            ok = browser.login()
            print(f"login() -> {ok}  ({time.time() - t0:.1f}s)")
            val = browser._read_input_value(inp)
            print(f"密码框最终长度 = {len(val) if val else 0} / {len(config.credentials.password)}")
            print(f"最终 URL = {page.url}")
            return

        box = inp.bounding_box()
        print("密码框 box(viewport) =", box)
        screen = page.evaluate(
            """(rect) => {
                const cx = rect.x + rect.width/2, cy = rect.y + rect.height/2;
                return {x: Math.round(window.screenX + (window.outerWidth - window.innerWidth)/2 + cx),
                        y: Math.round(window.screenY + (window.outerHeight - window.innerHeight) + cy)};
            }""", box)
        print("_real_click_element 会点到 =", screen)

        results = []
        for i in range(rounds):
            if i:
                print(f"\n--- 第 {i+1} 轮：重置登录页 ---")
                if not browser._reset_login_form():
                    print("   重置失败")
                    break
                time.sleep(2)
                inp = browser._locate_password_input()
                if not inp:
                    print("   重置后找不到密码框")
                    break

            page.evaluate(SPY)
            print(f"\n--- 第 {i+1} 轮 ---")
            collect(page, "打字前")

            ok = browser._type_password_once(inp, config.credentials.password)

            keys = page.evaluate("() => window.__keys")
            focus = page.evaluate("() => window.__focus")
            page.evaluate("() => clearInterval(window.__ft)")
            val = browser._read_input_value(inp)

            seq = "".join(k["k"] for k in keys)
            gaps = [round(keys[j]["t"] - keys[j - 1]["t"]) for j in range(1, len(keys))]

            # 按 >=250ms 间隔把按键切成"每个字符一组"，看是哪一位丢了
            groups, cur = [], [0]
            for j, g in enumerate(gaps):
                if g >= 250:
                    groups.append(cur)
                    cur = [j + 1]
                else:
                    cur.append(j + 1)
            groups.append(cur)
            sizes = [len(g) for g in groups]
            big = [round(g) for g in gaps if g >= 750]  # 某位整组消失 -> 间隔翻倍
            # 正常一组 3 个事件；把不足 3 个的组打印出来看少了哪一个
            odd = [(i, [keys[j]["k"] for j in g]) for i, g in enumerate(groups) if len(g) != 3]

            print(f"    _type_password_once -> {ok}")
            print(f"    DOM keydown {len(keys)} 次, 分 {len(groups)} 组: {sizes}")
            print(f"    异常组: {odd}")
            print(f"    双倍间隔(疑整位丢失): {big}")
            print(f"    控件读回长度 = {len(val) if val else 0} / {len(config.credentials.password)}")
            print(f"    焦点轨迹 = {focus}")
            print(f"    打字后前台窗口 = {fg_title()[1][:50]}")
            results.append((len(keys), len(val) if val else 0, ok, sizes, big))

        print("\n== 汇总 (DOM收到, 控件长度, 方法返回) ==")
        for r in results:
            print("   ", r)
    finally:
        try:
            if browser:
                browser.close()
        except Exception as e:
            print("关闭浏览器出错:", e)
        if cabinet:
            print("步骤9: 拔出 U 盾...")
            try:
                cabinet.close()
                print("   U 盾已拔出")
            except Exception as e:
                print("   拔出出错:", e)


if __name__ == "__main__":
    main()
