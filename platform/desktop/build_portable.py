from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
PLATFORM_DIR = DESKTOP_DIR.parent
WORKSPACE = PLATFORM_DIR.parent
DEFAULT_COLLECTORS = WORKSPACE / "collectors"
DEFAULT_MINGYUAN = WORKSPACE / "地产项目资料" / "采集明源云审批过程.py"
DEFAULT_OUTPUT = PLATFORM_DIR / "dist" / "UKeyAgent"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "work", "agent.env")


def run(command: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(str(item) for item in command))
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        raise SystemExit(f"目录不存在: {source}")
    shutil.copytree(source, target, ignore=IGNORE)


def build_desktop(output: Path) -> Path:
    work = PLATFORM_DIR / "build" / "pyinstaller"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noconsole",
        "--onedir",
        "--name",
        "UKeyAgent",
        "--distpath",
        str(output),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--paths",
        str(DESKTOP_DIR),
        "--icon",
        str(DESKTOP_DIR / "ukey.ico"),
        str(DESKTOP_DIR / "app.py"),
    ]
    run(command)
    app_dir = output / "UKeyAgent"
    if not (app_dir / "UKeyAgent.exe").exists():
        raise SystemExit("PyInstaller 没有生成 UKeyAgent.exe")
    return app_dir


def build_runtime(app_dir: Path, requirements: Path, install_browser: bool) -> Path:
    runtime = app_dir / "runtime"
    run([sys.executable, "-m", "venv", str(runtime)])
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "-r", str(requirements)])
    if install_browser:
        print("安装 Playwright Chromium（需要联网）…")
        result = subprocess.run(
            [str(python), "-m", "playwright", "install", "chromium"],
            check=False,
        )
        if result.returncode != 0:
            print("警告：Playwright 浏览器安装失败，首次运行时请在桌面端重试。")
    return python


def main() -> int:
    parser = argparse.ArgumentParser(description="组装 UKey 采集助手便携目录")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--collectors", default=str(DEFAULT_COLLECTORS))
    parser.add_argument("--mingyuan-collector", default=str(DEFAULT_MINGYUAN))
    parser.add_argument("--force", action="store_true", help="覆盖已有输出目录")
    parser.add_argument("--skip-runtime", action="store_true", help="不创建 Python 运行时")
    parser.add_argument("--skip-browser", action="store_true", help="不安装 Playwright Chromium")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    dist_root = (PLATFORM_DIR / "dist").resolve()
    if dist_root not in output.parents and output != dist_root:
        raise SystemExit(f"输出目录必须位于 {dist_root} 下")
    if output.exists():
        if not args.force:
            raise SystemExit(f"输出目录已存在，加 --force 覆盖: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    app_dir = build_desktop(output)
    copy_tree(PLATFORM_DIR / "agent", app_dir / "agent")
    collectors_source = Path(args.collectors)
    if collectors_source.exists():
        copy_tree(collectors_source, app_dir / "collectors")
    else:
        print(f"警告：采集器目录不存在，跳过银行采集器: {collectors_source}")
    mingyuan_source = Path(args.mingyuan_collector)
    if mingyuan_source.exists():
        target_dir = app_dir / "mingyuan"
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mingyuan_source, target_dir / "采集明源云审批过程.py")
        print(f"已复制明源云采集脚本: {mingyuan_source}")
    else:
        print(f"警告：明源云采集脚本不存在，桌面端需要手动指定: {mingyuan_source}")

    if not args.skip_runtime:
        python = build_runtime(
            app_dir,
            DESKTOP_DIR / "requirements-worker.txt",
            install_browser=not args.skip_browser,
        )
        print(f"运行时: {python}")
    else:
        print("已跳过 Python 运行时；客户机需要自行提供 PLATFORM_COLLECTOR_PYTHON。")

    print(f"便携目录: {app_dir}")
    print(f"桌面程序: {app_dir / 'UKeyAgent.exe'}")
    print("下一步：用 Inno Setup 编译 desktop/installer.iss")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
