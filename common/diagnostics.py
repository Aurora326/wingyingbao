"""网银自动化标准错误码与失败现场诊断三件套（压缩截图 + 脱敏 DOM + 步骤轨迹）。

供所有银行采集器/制单器统一调用：报错瞬间采集脱敏现场写入 work_dir/diagnostics/，
由客户机 Agent 通过 Multipart 接口自动回传平台，研发无需远程客户电脑即可定位网银改版。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

# ==================== 标准错误码 ====================

ERROR_PAGE_CHANGED = "PAGE_CHANGED"
ERROR_AUTH_FAILED = "AUTH_FAILED"
ERROR_UKEY_ERROR = "UKEY_ERROR"
ERROR_BANK_TEMP_ERROR = "BANK_TEMP_ERROR"
ERROR_PLUGIN_CRASH = "PLUGIN_CRASH"
ERROR_COLLECT_FAILED = "COLLECT_FAILED"

ERROR_CODE_LABELS: dict[str, str] = {
    ERROR_PAGE_CHANGED: "页面结构变化（疑似网银改版）",
    ERROR_AUTH_FAILED: "登录认证失败（密码/验证码/证书）",
    ERROR_UKEY_ERROR: "UKey 或机柜端口异常",
    ERROR_BANK_TEMP_ERROR: "网银临时故障/网络超时",
    ERROR_PLUGIN_CRASH: "脚本运行异常",
    ERROR_COLLECT_FAILED: "采集执行失败",
}


class BankAutomationError(RuntimeError):
    """带标准错误码和阶段名的自动化异常基类。"""

    default_code = ERROR_COLLECT_FAILED

    def __init__(self, message: str, *, error_code: str = "", stage: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code or self.default_code
        self.stage = stage


class PageChangedError(BankAutomationError):
    default_code = ERROR_PAGE_CHANGED


class AuthFailedError(BankAutomationError):
    default_code = ERROR_AUTH_FAILED


class UkeyError(BankAutomationError):
    default_code = ERROR_UKEY_ERROR


class BankTempError(BankAutomationError):
    default_code = ERROR_BANK_TEMP_ERROR


# ==================== DOM 脱敏 ====================

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PASSWORD_VAL_RE = re.compile(
    r'(<input\b[^>]*type=["\']?password["\']?[^>]*value=["\'])([^"\']*)(["\'])',
    re.IGNORECASE,
)
# 金额（含千分位或两位小数）与 8 位及以上连续数字（账号/卡号/手机号/证件号）
_MONEY_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[¥￥$]\s*)?\d{1,3}(?:,\d{3})+(?:\.\d{2})?(?![A-Za-z0-9_])")
_DECIMAL_MONEY_RE = re.compile(r"(?<![A-Za-z0-9_.\-])\d+\.\d{2}(?![A-Za-z0-9_.])")
_LONG_DIGITS_RE = re.compile(r"(?<![A-Za-z0-9_\-])\d{8,}(?![A-Za-z0-9_\-])")

MAX_DOM_BYTES = 512 * 1024


def sanitize_html(html: str) -> str:
    """清理脚本样式并脱敏账号/金额等敏感数字，保留完整 DOM 骨架与定位属性。"""
    if not html:
        return ""
    cleaned = _SCRIPT_RE.sub("", html)
    cleaned = _STYLE_RE.sub("", cleaned)
    cleaned = _COMMENT_RE.sub("", cleaned)
    cleaned = _PASSWORD_VAL_RE.sub(r"\1***\3", cleaned)
    # 仅对标签外文本内容脱敏，保留标签内 id/class/name/data-* 便于研发定位元素
    parts = re.split(r"(<[^>]+>)", cleaned)
    for idx in range(0, len(parts), 2):
        text = parts[idx]
        if not text or not text.strip():
            continue
        text = _MONEY_RE.sub("***", text)
        text = _DECIMAL_MONEY_RE.sub("***", text)
        text = _LONG_DIGITS_RE.sub("***", text)
        parts[idx] = text
    result = "".join(parts)
    encoded = result.encode("utf-8", errors="ignore")
    if len(encoded) > MAX_DOM_BYTES:
        result = encoded[:MAX_DOM_BYTES].decode("utf-8", errors="ignore") + "\n<!-- truncated -->"
    return result


# ==================== 步骤轨迹与现场抓取 ====================


class StepTracker:
    """轻量步骤追踪器，记录采集/制单每个关键阶段的耗时与结果。"""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self._current_start: float | None = None
        self._current_name: str = ""

    def start(self, name: str) -> None:
        self._current_name = name
        self._current_start = time.time()

    def ok(self, name: str = "", note: str = "") -> None:
        label = name or self._current_name or "step"
        elapsed = int((time.time() - self._current_start) * 1000) if self._current_start else 0
        self.steps.append(
            {
                "step": label,
                "status": "ok",
                "duration_ms": elapsed,
                "note": note[:200],
                "at": dt.datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._current_start = None

    def fail(self, name: str = "", error: str = "", error_code: str = "") -> None:
        label = name or self._current_name or "step"
        elapsed = int((time.time() - self._current_start) * 1000) if self._current_start else 0
        self.steps.append(
            {
                "step": label,
                "status": "failed",
                "duration_ms": elapsed,
                "error_code": error_code,
                "error": str(error)[:300],
                "at": dt.datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._current_start = None


def capture_diagnostic(
    page: Any,
    output_dir: Path | str,
    *,
    stage: str = "",
    error_code: str = "",
    message: str = "",
    steps: list[dict[str, Any]] | None = None,
    prefix: str = "failure",
    kind: str = "failure",
) -> list[str]:
    """在报错瞬间抓取压缩截图、脱敏 DOM（含 iframe）和结构化元信息。

    任意子项抓取失败不会向外抛错，确保不遮盖原始业务异常。
    prefix/kind 用于区分失败现场（failure）与人工核对快照（snapshot）。
    """
    diag_dir = Path(output_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    saved: list[str] = []

    current_url = ""
    title = ""
    if page is not None:
        try:
            current_url = str(getattr(page, "url", "") or "")
        except Exception:
            pass
        try:
            if hasattr(page, "title"):
                title = str(page.title() or "")
        except Exception:
            pass

        # 1. 压缩截图（优先 JPEG quality=65，体积比 PNG 小 5~10 倍）
        shot_path = diag_dir / f"{prefix}_{stamp}.jpg"
        try:
            page.screenshot(path=str(shot_path), type="jpeg", quality=65, full_page=False)
            if shot_path.is_file():
                saved.append(str(shot_path))
        except Exception:
            png_path = diag_dir / f"{prefix}_{stamp}.png"
            try:
                page.screenshot(path=str(png_path))
                if png_path.is_file():
                    saved.append(str(png_path))
            except Exception:
                pass

        # 2. 脱敏 DOM 快照（主文档 + 各 iframe）
        dom_chunks: list[str] = []
        try:
            if hasattr(page, "content"):
                dom_chunks.append(
                    f"<!-- MAIN FRAME: {current_url} -->\n" + sanitize_html(page.content() or "")
                )
        except Exception:
            pass
        try:
            frames = list(getattr(page, "frames", []) or [])
            for idx, frame in enumerate(frames[1:8], start=1):
                try:
                    f_url = str(getattr(frame, "url", "") or "")
                    f_html = sanitize_html(frame.content() or "")
                    if f_html.strip():
                        dom_chunks.append(f"\n<!-- IFRAME #{idx}: {f_url} -->\n{f_html}")
                except Exception:
                    continue
        except Exception:
            pass
        if dom_chunks:
            dom_path = diag_dir / f"{prefix}_dom_{stamp}.html"
            try:
                dom_path.write_text("\n".join(dom_chunks), encoding="utf-8")
                saved.append(str(dom_path))
            except Exception:
                pass

    # 3. 结构化诊断元信息
    meta = {
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "stage": stage,
        "error_code": error_code or (ERROR_COLLECT_FAILED if kind == "failure" else ""),
        "error_label": ERROR_CODE_LABELS.get(error_code or ERROR_COLLECT_FAILED, "") if kind == "failure" else "",
        "message": str(message or "")[:1000],
        "url": current_url,
        "title": title,
        "steps": steps or [],
    }
    meta_path = diag_dir / f"{prefix}_meta_{stamp}.json"
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(str(meta_path))
    except Exception:
        pass

    return saved


def capture_snapshot(
    page: Any,
    output_dir: Path | str,
    *,
    stage: str = "",
    message: str = "",
    steps: list[dict[str, Any]] | None = None,
) -> list[str]:
    """保存人工核对用快照（截图 + 脱敏 DOM），不标记为失败。"""
    return capture_diagnostic(
        page,
        output_dir,
        stage=stage,
        message=message,
        steps=steps,
        prefix="snapshot",
        kind="snapshot",
    )
