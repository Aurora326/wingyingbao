# -*- coding: utf-8 -*-
"""
打包脚本 - 使用 PyInstaller 打包为可执行文件
"""
import os
import sys
import shutil
from pathlib import Path


def clean_build():
    """清理旧的构建文件"""
    dirs_to_remove = ['build', 'dist']
    for d in dirs_to_remove:
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"已清理: {d}")
    
    files_to_remove = [f for f in os.listdir('.') if f.endswith('.spec')]
    for f in files_to_remove:
        os.remove(f)
        print(f"已清理: {f}")


def build():
    """执行打包"""
    clean_build()
    
    import PyInstaller.__main__
    
    project_root = str(Path(__file__).parent.parent.parent.resolve())

    # 基础参数
    args = [
        'main.py',
        '--name=工商银行采集工具',
        '--onefile',
        '--noconsole',
        '--paths=' + project_root,
        '--add-data=config.ini;.',
        '--add-data=logger.py;.',
        '--add-data=config_loader.py;.',
        '--add-data=browser.py;.',
        '--add-data=popup_handler.py;.',
        '--add-data=pin_inputter.py;.',
        '--add-data=login_manager.py;.',
        '--add-data=statement_collector.py;.',
        '--add-data=receipt_collector.py;.',
        '--add-data=run_recorder.py;.',
        '--add-data=cabinet_controller.py;.',
    ]
    
    # 隐藏导入
    hidden_imports = [
        'playwright',
        'loguru',
        'yaml',
        'requests',
        'pyautogui',
        'pandas',
        'xlwt',
        'openpyxl',
        'common.config_loader',
        'common.logger',
        'common.cabinet_controller',
        'common.run_recorder',
    ]
    
    for imp in hidden_imports:
        args.append(f'--hidden-import={imp}')
    
    # 图标（如果有的话）
    # if os.path.exists('icon.ico'):
    #     args.append('--icon=icon.ico')
    
    print("开始打包...")
    PyInstaller.__main__.run(args)
    
    # 复制额外文件到 dist 目录
    dist_dir = Path('dist')
    if dist_dir.exists():
        # 创建发布目录
        release_dir = dist_dir / '工商银行采集工具_Release'
        release_dir.mkdir(exist_ok=True)
        
        # 移动exe
        exe_file = dist_dir / '工商银行采集工具.exe'
        if exe_file.exists():
            shutil.move(str(exe_file), str(release_dir / exe_file.name))
        
        # 复制配置文件示例
        shutil.copy('config.ini', str(release_dir / 'config.ini.example'))
        
        # 创建下载目录
        (release_dir / 'downloads' / 'statements').mkdir(parents=True, exist_ok=True)
        (release_dir / 'downloads' / 'receipts').mkdir(parents=True, exist_ok=True)
        
        print(f"\n打包完成！发布目录: {release_dir}")
    else:
        print("打包失败，未找到 dist 目录")


if __name__ == "__main__":
    build()
