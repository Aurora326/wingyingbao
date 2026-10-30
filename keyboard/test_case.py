# -*- coding: utf-8 -*-
"""测试 AiKeyboard send -k 的大小写行为"""
import subprocess
import time
import sys
from pathlib import Path

aikeyboard = Path(__file__).parent / "AiKeyboard.exe"

def send(key):
    r = subprocess.run([str(aikeyboard), "send", "-k", key], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

# 打开记事本
print("1. 请手动打开记事本，并点击记事本窗口使其获得焦点")
print("2. 5秒后将自动输入测试字符")
for i in range(5, 0, -1):
    print(f"   倒计时: {i}")
    time.sleep(1)

print("\n>>> 测试 1: send -k A")
send("A")
time.sleep(0.5)

print(">>> 测试 2: send -k B")
send("B")
time.sleep(0.5)

print(">>> 测试 3: send -k 1")
send("1")
time.sleep(0.5)

print(">>> 测试 4: send -k Shift 然后 send -k C")
send("Shift")
time.sleep(0.1)
send("C")
time.sleep(0.1)
send("Shift")
time.sleep(0.5)

print(">>> 测试 5: send -k a (应该报错)")
rc, out, err = send("a")
print(f"   returncode={rc}")

print("\n请查看记事本中的输出结果，告诉我：")
print("- 测试1输出了 'A' 还是 'a'？")
print("- 测试4输出了 'C' 还是 'c'？")
input("按回车退出...")
