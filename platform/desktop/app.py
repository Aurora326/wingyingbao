from __future__ import annotations

import datetime as dt
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

try:
    from .config import AgentConfig, APP_NAME, app_root, local_dir, log_dir
except ImportError:
    from config import AgentConfig, APP_NAME, app_root, local_dir, log_dir

APP_VERSION = "desktop/1.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 UKeyAgent/1.0"
)


def make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#16374e"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QColor("#f4f1ea"))
    font = QFont("Microsoft YaHei UI", 30, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "U")
    painter.end()
    return QIcon(pixmap)


class PingWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, url: str, token: str) -> None:
        super().__init__()
        self.url = url
        self.token = token

    def run(self) -> None:
        if not self.token:
            self.done.emit(False, "未填写 Agent 令牌")
            return
        endpoint = self.url.rstrip("/") + "/api/agent/ping"
        request = urllib.request.Request(
            endpoint,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                self.done.emit(True, f"连接成功（HTTP {response.status}）")
        except urllib.error.HTTPError as exc:
            self.done.emit(False, f"平台返回 HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self, autostart: bool = False) -> None:
        super().__init__()
        self.cfg = AgentConfig.load()
        self.autostart_launch = autostart
        self.procs: dict[str, QProcess] = {}
        self._quitting = False
        self._stopping = False
        self.stop_file = local_dir() / "run" / "agent.stop"
        self.stop_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.stop_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._ping: PingWorker | None = None
        self.setWindowTitle(f"UKey 采集助手 · {APP_VERSION}")
        self.setWindowIcon(make_icon())
        self.resize(1080, 720)
        self._build_ui()
        self._apply_style()
        self._load_config_to_form()
        self._build_tray()
        self._update_buttons()
        self.append_log(f"程序启动，配置目录 {app_root()}")
        if self.cfg.auto_start_workers and self.cfg.platform_url and self.cfg.agent_token:
            QTimer.singleShot(800, self.start_workers)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("UKey 采集助手")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        self.status_label = QLabel("● 已停止")
        self.status_label.setObjectName("status")
        header.addWidget(self.status_label)
        root.addLayout(header)

        self.activity_label = QLabel("等待启动")
        self.activity_label.setObjectName("activity")
        root.addWidget(self.activity_label)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        connection = QGroupBox("平台连接")
        form = QFormLayout(connection)
        form.setLabelAlignment(Qt.AlignRight)
        self.url_edit = QLineEdit()
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.code_edit = QLineEdit()
        self.name_edit = QLineEdit()
        form.addRow("平台地址", self.url_edit)
        form.addRow("Agent 令牌", self.token_edit)
        form.addRow("Agent 编号", self.code_edit)
        form.addRow("显示名称", self.name_edit)
        left_layout.addWidget(connection)

        device = QGroupBox("MB 机柜与采集环境")
        device_form = QFormLayout(device)
        device_form.setLabelAlignment(Qt.AlignRight)
        self.ter_edit = QLineEdit()
        self.ter_edit.setPlaceholderText("多个 ter_id 用逗号分隔")
        self.mb_edit = QLineEdit()
        self.mb_user_edit = QLineEdit()
        self.collector_edit = QLineEdit()
        self.work_edit = QLineEdit()
        self.python_edit = QLineEdit()
        self.python_edit.setPlaceholderText("留空自动使用 runtime\\Scripts\\python.exe")
        device_form.addRow("机柜 ter_id", self.ter_edit)
        device_form.addRow("mb.exe", self.mb_edit)
        device_form.addRow("MB 用户", self.mb_user_edit)
        device_form.addRow("采集器目录", self.collector_edit)
        device_form.addRow("工作目录", self.work_edit)
        device_form.addRow("Python", self.python_edit)
        left_layout.addWidget(device)

        timing = QGroupBox("运行参数")
        timing_form = QFormLayout(timing)
        timing_form.setLabelAlignment(Qt.AlignRight)
        self.report_spin = QSpinBox()
        self.report_spin.setRange(30, 86400)
        self.report_spin.setSuffix(" 秒")
        self.task_spin = QSpinBox()
        self.task_spin.setRange(5, 3600)
        self.task_spin.setSuffix(" 秒")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(120, 7200)
        self.timeout_spin.setSuffix(" 秒")
        timing_form.addRow("设备上报间隔", self.report_spin)
        timing_form.addRow("任务轮询间隔", self.task_spin)
        timing_form.addRow("单任务超时", self.timeout_spin)
        left_layout.addWidget(timing)

        options = QGroupBox("启动选项")
        options_layout = QVBoxLayout(options)
        self.auto_workers = QCheckBox("打开程序后自动开始采集")
        self.autostart_box = QCheckBox("随 Windows 登录自动启动")
        options_layout.addWidget(self.auto_workers)
        options_layout.addWidget(self.autostart_box)
        left_layout.addWidget(options)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("保存配置")
        self.test_btn = QPushButton("测试连接")
        self.check_btn = QPushButton("检测环境")
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.test_btn)
        buttons.addWidget(self.check_btn)
        left_layout.addLayout(buttons)

        run_buttons = QHBoxLayout()
        self.start_btn = QPushButton("启动采集")
        self.start_btn.setObjectName("primary")
        self.stop_btn = QPushButton("停止采集")
        self.log_btn = QPushButton("打开日志")
        run_buttons.addWidget(self.start_btn)
        run_buttons.addWidget(self.stop_btn)
        run_buttons.addWidget(self.log_btn)
        left_layout.addLayout(run_buttons)
        left_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        scroll.setFrameShape(QFrame.NoFrame)
        splitter.addWidget(scroll)

        right = QGroupBox("运行日志")
        right_layout = QVBoxLayout(right)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setPlaceholderText("启动后这里会显示设备上报和任务执行日志")
        right_layout.addWidget(self.log_view)
        splitter.addWidget(right)
        splitter.setSizes([500, 580])

        self.save_btn.clicked.connect(self.save_config)
        self.test_btn.clicked.connect(self.test_connection)
        self.check_btn.clicked.connect(self.check_environment)
        self.start_btn.clicked.connect(self.start_workers)
        self.stop_btn.clicked.connect(self.stop_workers)
        self.log_btn.clicked.connect(self.open_logs)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
            QMainWindow { background: #f4f1ea; }
            QLabel#title { font-size: 22px; font-weight: 700; color: #16374e; }
            QLabel#status { font-size: 13px; font-weight: 600; color: #8a5a1f; }
            QLabel#activity { color: #5d6570; padding: 6px 10px; background: #fbf8f1;
                border: 1px solid #e3dccb; border-radius: 6px; }
            QGroupBox { background: #fffdf8; border: 1px solid #e3dccb; border-radius: 8px;
                margin-top: 12px; padding: 12px 10px 10px 10px; font-weight: 600; color: #16374e; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            QLineEdit, QSpinBox { padding: 6px 8px; border: 1px solid #d8d2c4;
                border-radius: 6px; background: #ffffff; color: #1f2933; }
            QLineEdit:focus, QSpinBox:focus { border: 1px solid #275a80; }
            QPushButton { padding: 7px 14px; border: 1px solid #d0c7b4;
                border-radius: 6px; background: #fffdf8; color: #16374e; }
            QPushButton:hover { background: #f1ece0; }
            QPushButton:disabled { color: #a7a29a; background: #f2efe8; }
            QPushButton#primary { background: #16374e; color: #ffffff; border-color: #16374e; }
            QPushButton#primary:hover { background: #1c4767; }
            QPlainTextEdit { background: #101820; color: #d7e1e8; border: 1px solid #2a3b4a;
                border-radius: 6px; font-family: "Consolas", "Microsoft YaHei UI"; font-size: 12px; }
            QScrollArea { background: transparent; }
            """
        )

    def _load_config_to_form(self) -> None:
        cfg = self.cfg
        self.url_edit.setText(cfg.platform_url)
        self.token_edit.setText(cfg.agent_token)
        self.code_edit.setText(cfg.agent_code)
        self.name_edit.setText(cfg.agent_name)
        self.ter_edit.setText(cfg.ter_ids)
        self.mb_edit.setText(cfg.mb_exe)
        self.mb_user_edit.setText(cfg.mb_user)
        self.collector_edit.setText(cfg.collector_root)
        self.work_edit.setText(cfg.work_dir)
        self.python_edit.setText(cfg.python_exe)
        self.report_spin.setValue(cfg.report_interval)
        self.task_spin.setValue(cfg.task_interval)
        self.timeout_spin.setValue(cfg.task_timeout)
        self.auto_workers.setChecked(cfg.auto_start_workers)
        self.autostart_box.setChecked(cfg.autostart)

    def _collect_form(self) -> AgentConfig:
        cfg = self.cfg
        cfg.platform_url = self.url_edit.text().strip()
        cfg.agent_token = self.token_edit.text().strip()
        cfg.agent_code = self.code_edit.text().strip()
        cfg.agent_name = self.name_edit.text().strip()
        cfg.ter_ids = self.ter_edit.text().strip()
        cfg.mb_exe = self.mb_edit.text().strip()
        cfg.mb_user = self.mb_user_edit.text().strip()
        cfg.collector_root = self.collector_edit.text().strip()
        cfg.work_dir = self.work_edit.text().strip()
        cfg.python_exe = self.python_edit.text().strip()
        cfg.report_interval = self.report_spin.value()
        cfg.task_interval = self.task_spin.value()
        cfg.task_timeout = self.timeout_spin.value()
        cfg.auto_start_workers = self.auto_workers.isChecked()
        cfg.autostart = self.autostart_box.isChecked()
        return cfg

    def save_config(self) -> None:
        cfg = self._collect_form()
        if not cfg.platform_url or not cfg.agent_code:
            QMessageBox.warning(self, "配置不完整", "平台地址和 Agent 编号不能为空。")
            return
        cfg.save()
        self._set_autostart(cfg.autostart)
        self.append_log("配置已保存")
        self.activity_label.setText("配置已保存")

    def test_connection(self) -> None:
        cfg = self._collect_form()
        if not cfg.platform_url:
            QMessageBox.warning(self, "缺少地址", "请先填写平台地址。")
            return
        self.test_btn.setEnabled(False)
        self.activity_label.setText("正在测试连接…")
        self._ping = PingWorker(cfg.platform_url, cfg.agent_token)
        self._ping.done.connect(self._on_ping_done)
        self._ping.start()

    def _on_ping_done(self, ok: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.activity_label.setText(message)
        self.append_log(("连接成功：" if ok else "连接失败：") + message)
        if ok:
            QMessageBox.information(self, "连接测试", message)
        else:
            QMessageBox.warning(self, "连接测试", message)

    def check_environment(self) -> None:
        cfg = self._collect_form()
        checks: list[tuple[str, bool, str]] = []
        python = cfg.python()
        checks.append(("Python", Path(python).exists() or python == "python", python))
        device = cfg.agent_script("device_report.py")
        worker = cfg.agent_script("task_worker.py")
        checks.append(("设备上报脚本", device.exists(), str(device)))
        checks.append(("任务执行脚本", worker.exists(), str(worker)))
        collectors = cfg.collectors()
        checks.append(("采集器 bankcore", (collectors / "bankcore" / "run_task.py").exists(), str(collectors)))
        mb = Path(cfg.mb_exe) if cfg.mb_exe else None
        checks.append(("mb.exe", bool(mb and mb.exists()), cfg.mb_exe or "未配置"))
        lines = [f"{'✓' if ok else '✗'} {name}：{detail}" for name, ok, detail in checks]
        text = "\n".join(lines)
        self.append_log("环境检测\n" + text)
        if all(ok for _, ok, _ in checks):
            QMessageBox.information(self, "环境检测", text)
        else:
            QMessageBox.warning(self, "环境检测", text)

    def _process_env(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PLATFORM_AGENT_TOKEN", self.cfg.agent_token)
        env.insert("PLATFORM_AGENT_STOP_FILE", str(self.stop_file))
        return env

    def _start_proc(self, name: str, program: str, args: list[str]) -> None:
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setWorkingDirectory(str(app_root()))
        proc.setProcessEnvironment(self._process_env())
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._read_output(name))
        proc.finished.connect(lambda code, status: self._proc_finished(name, code, status))
        proc.errorOccurred.connect(lambda err: self._proc_error(name, err))
        self.procs[name] = proc
        proc.start()

    def start_workers(self) -> None:
        if any(proc.state() != QProcess.NotRunning for proc in self.procs.values()):
            return
        cfg = self._collect_form()
        if not cfg.platform_url or not cfg.agent_token:
            QMessageBox.warning(self, "缺少配置", "请先填写平台地址和 Agent 令牌。")
            return
        cfg.save()
        try:
            self.stop_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._stopping = False
        python = cfg.python()
        device = cfg.agent_script("device_report.py")
        worker = cfg.agent_script("task_worker.py")
        if not device.exists() or not worker.exists():
            QMessageBox.critical(self, "文件缺失", f"未找到 Agent 脚本：\n{device}\n{worker}")
            return

        device_args = [
            str(device),
            "--url", cfg.platform_url,
            "--agent-code", cfg.agent_code,
            "--agent-name", cfg.agent_name,
            "--ter-id", cfg.ter_ids,
            "--mb-exe", cfg.mb_exe,
            "--interval", str(cfg.report_interval),
        ]
        task_args = [
            str(worker),
            "--url", cfg.platform_url,
            "--agent-code", cfg.agent_code,
            "--mb-exe", cfg.mb_exe,
            "--mb-user", cfg.mb_user,
            "--gf3-exe", cfg.gf3_exe,
            "--collector-python", python,
            "--collector-root", str(cfg.collectors()),
            "--work-dir", str(cfg.work()),
            "--interval", str(cfg.task_interval),
            "--timeout", str(cfg.task_timeout),
        ]
        self.append_log(f"启动设备上报：{device}")
        self._start_proc("设备上报", python, device_args)
        self.append_log(f"启动任务执行：{worker}")
        self._start_proc("任务执行", python, task_args)
        self.status_label.setText("● 运行中")
        self.status_label.setStyleSheet("color: #2f7d4f; font-weight: 600;")
        self.activity_label.setText(f"Agent {cfg.agent_code} 已启动")
        self._update_buttons()

    def stop_workers(self) -> None:
        self._stopping = True
        try:
            self.stop_file.write_text("stop", encoding="utf-8")
        except OSError:
            pass
        for name, proc in list(self.procs.items()):
            if proc.state() == QProcess.NotRunning:
                continue
            self.append_log(f"停止 {name}…")
            if proc.waitForFinished(8000):
                continue
            proc.terminate()
            if not proc.waitForFinished(3000):
                self._kill_tree(proc)
                proc.waitForFinished(2000)
        try:
            self.stop_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._stopping = False
        self.status_label.setText("● 已停止")
        self.status_label.setStyleSheet("color: #8a5a1f; font-weight: 600;")
        self.activity_label.setText("采集已停止")
        self._update_buttons()

    def _proc_error(self, name: str, error) -> None:
        if self._stopping and error == QProcess.ProcessError.Crashed:
            return
        self.append_log(f"[{name}] 进程错误: {error}")

    @staticmethod
    def _kill_tree(proc: QProcess) -> None:
        pid = int(proc.processId())
        if pid <= 0:
            proc.kill()
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            return
        proc.kill()

    def _read_output(self, name: str) -> None:
        proc = self.procs.get(name)
        if proc is None:
            return
        text = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip():
                self.append_log(f"[{name}] {line}")
                self._track_activity(line)

    def _track_activity(self, line: str) -> None:
        if "领取任务" in line:
            self.activity_label.setText(line.strip())
        elif "回传成功" in line or "上报成功" in line:
            self.activity_label.setText(line.strip())
        elif "失败" in line:
            self.activity_label.setText(line.strip())

    def _proc_finished(self, name: str, code: int, status) -> None:
        self.append_log(f"[{name}] 已退出，代码 {code}")
        running = any(
            proc.state() != QProcess.NotRunning for proc in self.procs.values()
        )
        if not running:
            self.status_label.setText("● 已停止")
            self.status_label.setStyleSheet("color: #8a5a1f; font-weight: 600;")
        self._update_buttons()

    def _update_buttons(self) -> None:
        running = any(
            proc.state() != QProcess.NotRunning for proc in self.procs.values()
        )
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def append_log(self, message: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        line = f"{stamp}  {message}"
        self.log_view.appendPlainText(line)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )
        try:
            with (log_dir() / "desktop.log").open("a", encoding="utf-8") as fh:
                fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
        except OSError:
            pass

    def open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_root() / "agent")))

    def _build_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        show_action = menu.addAction("显示主界面")
        quit_action = menu.addAction("退出")
        show_action.triggered.connect(self._show_window)
        quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window()
            if reason == QSystemTrayIcon.DoubleClick
            else None
        )
        self.tray.show()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _autostart_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" --autostart'
        script = Path(__file__).resolve()
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = pythonw if pythonw.exists() else Path(sys.executable)
        return f'"{exe}" "{script}" --autostart'

    def _set_autostart(self, enabled: bool) -> None:
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, self._autostart_command())
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            self.append_log(f"设置开机启动失败：{exc}")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tray and not self._quitting and any(
            proc.state() != QProcess.NotRunning for proc in self.procs.values()
        ):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                APP_NAME, "采集助手已最小化到系统托盘，右键可退出。",
                QSystemTrayIcon.Information, 3000
            )
            return
        self.stop_workers()
        event.accept()

    def quit_app(self) -> None:
        self._quitting = True
        self.stop_workers()
        if self.tray:
            self.tray.hide()
        QApplication.quit()


def main() -> int:
    autostart = "--autostart" in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("UKey 采集助手")
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(autostart=autostart)
    if autostart:
        window.hide()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
