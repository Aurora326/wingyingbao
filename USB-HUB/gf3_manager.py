import os
import socket
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import scrolledtext


class GF3ManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GF3 机柜管理工具")
        self.root.geometry("960x820")
        self.root.resizable(False, False)
        self.root.configure(bg="#f0f2f5")
        self._build_ui()

    def _build_ui(self):
        # ========== 顶部连接栏 ==========
        top = tk.Frame(self.root, bg="#ffffff", height=55, highlightbackground="#d9d9d9", highlightthickness=1)
        top.pack(fill=tk.X, padx=14, pady=(14, 10))
        top.pack_propagate(False)

        tk.Label(top, text="VID", font=("Microsoft YaHei", 10, "bold"), bg="#ffffff", fg="#555555").pack(side=tk.LEFT, padx=(20, 4))
        self.vid_in = tk.Entry(top, width=8, justify="center", font=("Microsoft YaHei", 10), relief=tk.SOLID, bd=1, fg="#333333")
        self.vid_in.insert(0, "57AB")
        self.vid_in.pack(side=tk.LEFT, padx=2)

        tk.Label(top, text="PID", font=("Microsoft YaHei", 10, "bold"), bg="#ffffff", fg="#555555").pack(side=tk.LEFT, padx=(15, 4))
        self.pid_in = tk.Entry(top, width=8, justify="center", font=("Microsoft YaHei", 10), relief=tk.SOLID, bd=1, fg="#333333")
        self.pid_in.insert(0, "0001")
        self.pid_in.pack(side=tk.LEFT, padx=2)

        self.conn_btn = tk.Button(
            top, text="连接设备", width=12, height=1,
            bg="#1890ff", fg="#ffffff", font=("Microsoft YaHei", 10, "bold"),
            activebackground="#40a9ff", relief=tk.FLAT, cursor="hand2",
            command=self.connect_device,
        )
        self.conn_btn.pack(side=tk.LEFT, padx=22)

        self.status_label = tk.Label(top, text="未连接", font=("Microsoft YaHei", 10), bg="#ffffff", fg="#bfbfbf")
        self.status_label.pack(side=tk.RIGHT, padx=20)

        # ========== 中部：12 路控制卡片（3列 × 4行） ==========
        grid = tk.Frame(self.root, bg="#f0f2f5")
        grid.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        for c in range(3):
            grid.columnconfigure(c, weight=1)
        for r in range(4):
            grid.rowconfigure(r, weight=1)

        self.btn_map = {}
        for i in range(1, 13):
            r, c = (i - 1) // 3, (i - 1) % 3

            # 外边框层（模拟 1px 灰色边框）
            card_border = tk.Frame(grid, bg="#d9d9d9", bd=0)
            card_border.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")

            # 内内容层（白色背景，四周留 1px 形成边框效果）
            card = tk.Frame(card_border, bg="#ffffff", bd=0)
            card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

            # 路号标题栏（浅灰底色，突出显示）
            title_bar = tk.Frame(card, bg="#fafafa", height=34)
            title_bar.pack(fill=tk.X)
            title_bar.pack_propagate(False)
            tk.Label(title_bar, text=f"第 {i:02d} 路", font=("Microsoft YaHei", 10, "bold"),
                     bg="#fafafa", fg="#333333").pack(expand=True)

            # 细分割线
            tk.Frame(card, height=1, bg="#f0f0f0").pack(fill=tk.X)

            # 按钮行
            btn_row = tk.Frame(card, bg="#ffffff")
            btn_row.pack(pady=10)

            b1 = tk.Button(btn_row, text="按压", width=8, height=1,
                           bg="#f6ffed", fg="#389e0d", font=("Microsoft YaHei", 9),
                           activebackground="#d9f7be", relief=tk.FLAT, cursor="hand2",
                           command=lambda p=i: self.run_gf3_cmd(1, p, 1))
            b1.pack(side=tk.LEFT, padx=5)

            b2 = tk.Button(btn_row, text="插入", width=8, height=1,
                           bg="#e6f7ff", fg="#096dd9", font=("Microsoft YaHei", 9),
                           activebackground="#bae7ff", relief=tk.FLAT, cursor="hand2",
                           command=lambda p=i: self.run_gf3_cmd(1, p, 2))
            b2.pack(side=tk.LEFT, padx=5)

            b3 = tk.Button(btn_row, text="拔出", width=8, height=1,
                           bg="#fff1f0", fg="#cf1322", font=("Microsoft YaHei", 9),
                           activebackground="#ffccc7", relief=tk.FLAT, cursor="hand2",
                           command=lambda p=i: self.run_gf3_cmd(1, p, 3))
            b3.pack(side=tk.LEFT, padx=5)

            self.btn_map[i] = (b1, b2, b3)

        # ========== 批量操作栏（左中右排列，有间隔） ==========
        batch_card = tk.Frame(self.root, bg="#ffffff", highlightbackground="#d9d9d9", highlightthickness=1)
        batch_card.pack(fill=tk.X, padx=14, pady=(0, 10))

        batch = tk.Frame(batch_card, bg="#ffffff", height=50)
        batch.pack(fill=tk.X)
        batch.pack_propagate(False)
        batch.columnconfigure(0, weight=1)
        batch.columnconfigure(1, weight=1)
        batch.columnconfigure(2, weight=1)

        tk.Button(batch, text="全部插入", width=13, height=1,
                  bg="#1890ff", fg="#ffffff", font=("Microsoft YaHei", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2",
                  command=lambda: self.batch_cmd(2, "插入")).grid(row=0, column=0, sticky="e", padx=(0, 18), pady=10)

        tk.Button(batch, text="全部拔出", width=13, height=1,
                  bg="#ff4d4f", fg="#ffffff", font=("Microsoft YaHei", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2",
                  command=lambda: self.batch_cmd(3, "拔出")).grid(row=0, column=1, padx=18, pady=10)

        tk.Button(batch, text="全部按压", width=13, height=1,
                  bg="#52c41a", fg="#ffffff", font=("Microsoft YaHei", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2",
                  command=lambda: self.batch_cmd(1, "按压")).grid(row=0, column=2, sticky="w", padx=(18, 0), pady=10)

        # ========== 底部日志区 ==========
        log_card = tk.Frame(self.root, bg="#ffffff", highlightbackground="#d9d9d9", highlightthickness=1)
        log_card.pack(fill=tk.BOTH, expand=False, padx=14, pady=(0, 14))

        log_header = tk.Frame(log_card, bg="#ffffff", height=36)
        log_header.pack(fill=tk.X)
        log_header.pack_propagate(False)
        tk.Label(log_header, text="运行日志", font=("Microsoft YaHei", 10, "bold"),
                 bg="#ffffff", fg="#333333").pack(side=tk.LEFT, padx=14, pady=8)

        tk.Frame(log_card, height=1, bg="#f0f0f0").pack(fill=tk.X)

        self.log_display = scrolledtext.ScrolledText(
            log_card, state=tk.DISABLED, wrap=tk.WORD,
            bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 10),
            insertbackground="white", height=11, relief=tk.FLAT,
        )
        self.log_display.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        self.log_display.tag_config("info", foreground="#569cd6")
        self.log_display.tag_config("success", foreground="#4ec9b0")
        self.log_display.tag_config("error", foreground="#f48771")
        self.log_display.tag_config("warn", foreground="#ce9178")

    def connect_device(self):
        vid = self.vid_in.get().strip()
        pid = self.pid_in.get().strip()
        self.add_log(f"> 连接设备 VID={vid} PID={pid}", "info")
        self.status_label.config(text="已连接", fg="#52c41a")
        self.conn_btn.config(text="已连接", bg="#73d13d")

    def batch_cmd(self, flag, name):
        self.add_log(f"> 批量{name}全部 12 路...", "info")
        for port in range(1, 13):
            self.run_gf3_cmd(1, port, flag, silent=True)
        self.add_log(f"> 批量{name}完成", "success")

    def _find_gf3_exe(self):
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(base_dir, "gf3.exe"),
            os.path.join(os.path.dirname(base_dir), "USB-HUB", "gf3.exe"),
            r"C:\wangyingbao\USB-HUB\gf3.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return "gf3.exe"

    def _read_tail(self, path, lines=3):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.readlines()
            return "".join(content[-lines:]).strip()
        except Exception:
            return ""

    def _get_mtime(self, path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _build_failure_message(self, gf3_dir, returncode):
        log_path = os.path.join(gf3_dir, "gf3.log")
        auth_fail_path = os.path.join(gf3_dir, "gf3_auth_fail.log")
        log_tail = self._read_tail(log_path, lines=3)
        auth_tail = self._read_tail(auth_fail_path, lines=2)
        parts = [f"gf3.exe 执行失败 (Exit code: {returncode})"]
        parts.append(f"机器名: {socket.gethostname()}")
        parts.append(f"工作目录: {gf3_dir}")
        log_mtime = self._get_mtime(log_path)
        auth_mtime = self._get_mtime(auth_fail_path)
        if log_mtime is not None:
            parts.append("gf3.log 修改时间: " + datetime.fromtimestamp(log_mtime).strftime("%Y-%m-%d %H:%M:%S"))
        else:
            parts.append("gf3.log 不存在")
        if auth_mtime is not None:
            parts.append("gf3_auth_fail.log 修改时间: " + datetime.fromtimestamp(auth_mtime).strftime("%Y-%m-%d %H:%M:%S"))
        parts.append(f"最近 gf3.log: {log_tail if log_tail else '无'}")
        if auth_tail:
            parts.append(f"最近 gf3_auth_fail.log: {auth_tail}")
        parts.append("提示: 如果 gf3.log 没有当前时间的新记录，通常表示 gf3 在主流程前就退出，常见原因是机器绑定、授权校验或依赖环境异常。")
        return " | ".join(parts)

    def run_gf3_cmd(self, cabinet, port, flag, silent=False):
        gf3_path = self._find_gf3_exe()
        gf3_dir = os.path.dirname(gf3_path)
        cmd_str = f'"{gf3_path}" {cabinet} {port} {flag}'
        action_map = {1: "按压", 2: "插入", 3: "拔出"}
        action_name = action_map.get(flag, f"操作({flag})")
        if not silent:
            self.add_log(f"> [{action_name}] 第 {port:02d} 路", "info")

        def worker():
            try:
                log_path = os.path.join(gf3_dir, "gf3.log")
                auth_fail_path = os.path.join(gf3_dir, "gf3_auth_fail.log")
                log_mtime_before = self._get_mtime(log_path)
                auth_mtime_before = self._get_mtime(auth_fail_path)

                process = subprocess.Popen(
                    cmd_str, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    shell=True, text=True, encoding="gbk",
                    cwd=gf3_dir if gf3_dir else None,
                )
                output, _ = process.communicate(timeout=30)

                if output.strip():
                    if "命令执行成功" in output:
                        if not silent:
                            self.add_log(output.strip(), "success")
                    else:
                        if not silent:
                            self.add_log(output.strip(), "warn")
                        if process.returncode != 0:
                            self.add_log(self._build_failure_message(gf3_dir, process.returncode), "error")
                    return

                log_tail = self._read_tail(log_path, lines=3)
                log_mtime_after = self._get_mtime(log_path)
                auth_mtime_after = self._get_mtime(auth_fail_path)

                if process.returncode != 0:
                    msg = self._build_failure_message(gf3_dir, process.returncode)
                    if log_mtime_before == log_mtime_after:
                        msg += " | 检测: 本次执行后 gf3.log 未更新"
                    if auth_mtime_after is not None and auth_mtime_after != auth_mtime_before:
                        msg += " | 检测: 本次执行后 gf3_auth_fail.log 有新增记录"
                    self.add_log(msg, "error")
                elif log_tail and "命令执行成功" in log_tail:
                    if not silent:
                        self.add_log("命令执行成功", "success")
                elif log_tail:
                    if not silent:
                        self.add_log(f"gf3 日志: {log_tail}", "warn")
                else:
                    if not silent:
                        self.add_log("命令已发出(无回应)", "warn")
            except Exception as e:
                self.add_log(f"执行失败: {e}", "error")

        threading.Thread(target=worker, daemon=True).start()

    def add_log(self, text, tag=""):
        def append():
            self.log_display.config(state=tk.NORMAL)
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_display.insert(tk.END, f"[{timestamp}] {text}\n", tag)
            self.log_display.see(tk.END)
            self.log_display.config(state=tk.DISABLED)
        self.root.after(0, append)


if __name__ == "__main__":
    app = tk.Tk()
    GF3ManagerApp(app)
    app.mainloop()
