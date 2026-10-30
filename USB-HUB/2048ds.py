import sys
import subprocess
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTabWidget, QGridLayout, QTextEdit, QFrame)
from PySide6.QtCore import Qt, QThread, Signal


# 使用线程执行命令，防止界面卡死
class CommandRunner(QThread):
    result_ready = Signal(str, str)  # 传递：结果文本, 颜色

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        try:
            # 执行 gf3 命令并捕获输出
            process = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                text=True,
                encoding='gbk'  # 根据 Windows 控制台编码调整
            )
            output, _ = process.communicate()

            if "命令执行成功" in output:
                self.result_ready.emit(output.strip(), "black")
            else:
                self.result_ready.emit(output.strip(), "red")
        except Exception as e:
            self.result_ready.emit(f"执行失败: {str(e)}", "red")


class DeviceControlApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("楚云科技 - 设备控制系统")
        self.resize(900, 800)
        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # 1. 顶部栏 (VID/PID 暂时作为显示)
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("VID"))
        self.vid_in = QLineEdit("57AB")
        top_bar.addWidget(self.vid_in)
        top_bar.addWidget(QLabel("PID"))
        self.pid_in = QLineEdit("0001")
        top_bar.addWidget(self.pid_in)
        top_bar.addWidget(QPushButton("连接设备"))
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # 2. 选项卡
        tabs = QTabWidget()
        test_page = QWidget()
        tabs.addTab(test_page, "测试")
        layout.addWidget(tabs)

        # 3. 核心区域
        content_frame = QFrame()
        content_frame.setStyleSheet("QFrame { border: 1px solid #CCC; } QPushButton { height: 30px; }")
        content_layout = QHBoxLayout(content_frame)

        # --- 左侧：按压控制 (功能码 1) ---
        left_v = QVBoxLayout()
        left_v.addWidget(QLabel("按压 (Action)"))
        for i in range(1, 13):
            btn = QPushButton(f"第 {i:02d} 路【按压】")
            # 闭包捕获当前的 i
            btn.clicked.connect(lambda ch=None, port=i: self.run_gf3_cmd(1, port, 1))
            left_v.addWidget(btn)
        left_v.addStretch()

        # --- 右侧：Ukey控制 (连接=2, 断开=3) ---
        right_v = QVBoxLayout()
        right_v.addWidget(QLabel("Ukey (Power)"))
        grid = QGridLayout()
        for i in range(1, 13):
            btn_on = QPushButton(f"第 {i:02d} 路【连接】")
            btn_off = QPushButton(f"第 {i:02d} 路【断开】")

            btn_on.clicked.connect(lambda ch=None, port=i: self.run_gf3_cmd(1, port, 2))
            btn_off.clicked.connect(lambda ch=None, port=i: self.run_gf3_cmd(1, port, 3))

            grid.addWidget(btn_on, i - 1, 0)
            grid.addWidget(btn_off, i - 1, 1)
        right_v.addLayout(grid)

        content_layout.addLayout(left_v, 1)
        content_layout.addLayout(right_v, 1)

        test_layout = QVBoxLayout(test_page)
        test_layout.addWidget(content_frame)

        # 4. 日志输出 (对应 image_3b890c.png 的样式)
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas;")
        layout.addWidget(self.log_display)

    def run_gf3_cmd(self, cabinet, port, flag):
        """构造命令并执行"""
        cmd_str = f"gf3 {cabinet} {port} {flag}"
        self.add_log(f"> 正在发送指令: {cmd_str}", "#569cd6")

        # 启动后台线程执行
        self.runner = CommandRunner(cmd_str)
        self.runner.result_ready.connect(self.add_log)
        self.runner.start()

    def add_log(self, text, color="white"):
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        html_text = f'<div style="color: {color};">[{timestamp}] {text}</div>'
        self.log_display.append(html_text)
        self.log_display.ensureCursorVisible()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DeviceControlApp()
    window.show()
    sys.exit(app.exec())