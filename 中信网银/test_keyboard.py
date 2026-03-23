# -*- coding: utf-8 -*-
"""
测试 AiKeyboard 的调用方式
"""
import subprocess
import sys
import os
from pathlib import Path

# 查找 AiKeyboard 路径
project_root = Path(__file__).parent
aikeyboard_path = project_root / ".." / "keyboard" / "AiKeyboard.exe"

print(f"AiKeyboard 路径: {aikeyboard_path}")
print(f"文件存在: {aikeyboard_path.exists()}")

# 测试密码
test_pwd = "Hzd810218"

# 方式1: 命令行参数
print("\n=== 方式1: 命令行参数 ===")
try:
    result = subprocess.run(
        [str(aikeyboard_path), test_pwd],
        capture_output=True,
        text=True,
        timeout=5
    )
    print(f"返回码: {result.returncode}")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
except Exception as e:
    print(f"失败: {e}")

# 方式2: 带引号的参数
print("\n=== 方式2: 带引号的参数 ===")
try:
    result = subprocess.run(
        f'"{aikeyboard_path}" "{test_pwd}"',
        capture_output=True,
        text=True,
        timeout=5,
        shell=True
    )
    print(f"返回码: {result.returncode}")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
except Exception as e:
    print(f"失败: {e}")

# 方式3: 使用 shell 命令
print("\n=== 方式3: 使用 shell 命令 ===")
try:
    cmd = f'cd "{aikeyboard_path.parent}" && .\AiKeyboard.exe "{test_pwd}"'
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=5,
        shell=True
    )
    print(f"返回码: {result.returncode}")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
except Exception as e:
    print(f"失败: {e}")

# 方式4: 查看帮助
print("\n=== 方式4: 查看帮助 ===")
try:
    result = subprocess.run(
        [str(aikeyboard_path), "--help"],
        capture_output=True,
        text=True,
        timeout=5
    )
    print(f"返回码: {result.returncode}")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
except Exception as e:
    print(f"失败: {e}")

# 方式5: 不带参数运行
print("\n=== 方式5: 不带参数运行 ===")
try:
    result = subprocess.run(
        [str(aikeyboard_path)],
        capture_output=True,
        text=True,
        timeout=5
    )
    print(f"返回码: {result.returncode}")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
except Exception as e:
    print(f"失败: {e}")

print("\n测试完成")
