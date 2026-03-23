# -*- coding: utf-8 -*-
"""
测试 AiKeyboard input -k 命令
"""
import subprocess
from pathlib import Path

# 查找 AiKeyboard 路径
project_root = Path(__file__).parent
aikeyboard_path = project_root / ".." / "keyboard" / "AiKeyboard.exe"

test_pwd = "Hzd810218"

# 测试 input -k 命令
print("=== 测试 input -k 命令 ===")
try:
    result = subprocess.run(
        [str(aikeyboard_path), "input", "-k", test_pwd],
        capture_output=True,
        text=True,
        timeout=5
    )
    print(f"返回码: {result.returncode}")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
except Exception as e:
    print(f"失败: {e}")

# 测试 input --keys 命令
print("\n=== 测试 input --keys 命令 ===")
try:
    result = subprocess.run(
        [str(aikeyboard_path), "input", "--keys", test_pwd],
        capture_output=True,
        text=True,
        timeout=5
    )
    print(f"返回码: {result.returncode}")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
except Exception as e:
    print(f"失败: {e}")
