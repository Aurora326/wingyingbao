"""客户机插件热更新管理器与失败现场诊断上传工具。

职责：
1. 维护 %LOCALAPPDATA%/UKeyAgent/plugins/<plugin_id>/releases/<version>/ + current.json；
2. 收到平台下发的 desired_plugins 后，后台下载 zip → SHA256 + Ed25519 验签 → 解压 → 语法冒烟检查 → 拿 AgentBusLock 原子切 current.json；
3. 为 task_worker / draft_task_worker 解析已安装插件命令（未安装时无缝回退 collectors.yaml）；
4. 通过标准 Multipart 流式上传任务失败现场（截图、脱敏 DOM、meta.json、trace.zip）。
"""
from __future__ import annotations

import base64
import compileall
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from local_lock import AgentBusLock, default_lock_path

KEEP_RELEASES = 3
MAX_DIAG_UPLOAD_BYTES = 15 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 UKeyAgent/1.1"
)


def default_plugins_dir() -> Path:
    explicit = os.getenv("PLATFORM_PLUGINS_DIR", "").strip()
    if explicit:
        path = Path(explicit)
    else:
        local_app = os.getenv("LOCALAPPDATA", "").strip()
        if local_app:
            path = Path(local_app) / "UKeyAgent" / "plugins"
        else:
            path = Path.home() / ".ukeyagent" / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def installed_plugins(plugins_dir: Path | None = None) -> dict[str, str]:
    """返回本机所有已生效插件的 {plugin_id: version} 映射。"""
    root = Path(plugins_dir) if plugins_dir else default_plugins_dir()
    if not root.is_dir():
        return {}
    result: dict[str, str] = {}
    for item in root.iterdir():
        if not item.is_dir():
            continue
        cur = _read_json(item / "current.json")
        version = str(cur.get("version") or "").strip()
        if version and (item / "releases" / version).is_dir():
            result[item.name] = version
    return result


def _verify_ed25519(data: bytes, signature_b64: str, public_key_b64: str) -> bool:
    if not public_key_b64:
        return True
    if not signature_b64:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64.strip()))
        pub.verify(base64.b64decode(signature_b64.strip()), data)
        return True
    except (ImportError, InvalidSignature, ValueError, TypeError):
        return False


def _safe_extract_zip(data: bytes, target_dir: Path) -> dict:
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    root_resolved = target_dir.resolve()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            for member in zf.infolist():
                norm = member.filename.replace("\\", "/")
                if norm.startswith("/") or ".." in Path(norm).parts:
                    raise RuntimeError(f"压缩包包含非法路径: {member.filename}")
                dest = (root_resolved / norm).resolve()
                if root_resolved not in dest.parents and dest != root_resolved:
                    raise RuntimeError(f"压缩包路径越界: {member.filename}")
            zf.extractall(root_resolved)
    finally:
        tmp_path.unlink(missing_ok=True)
    manifest = _read_json(target_dir / "manifest.json")
    if not manifest.get("plugin_id") or not manifest.get("version"):
        raise RuntimeError("插件包缺少有效 manifest.json")
    return manifest


def _smoke_check(release_dir: Path, manifest: dict) -> None:
    """5 行轻量冒烟检查：确认所有 .py 无语法错误且 manifest 声明的入口文件存在。"""
    entry = manifest.get("entry") or {}
    for kind in ("collect", "draft"):
        cmd = entry.get(kind)
        if isinstance(cmd, list) and cmd:
            script = release_dir / str(cmd[0])
            if str(cmd[0]).endswith(".py") and not script.is_file():
                raise RuntimeError(f"插件缺少 {kind} 入口文件: {cmd[0]}")
    ok = compileall.compile_dir(str(release_dir), quiet=2, force=False)
    if not ok:
        raise RuntimeError("插件 Python 源码语法检查未通过")


def _prune_old_releases(plugin_dir: Path, keep_versions: set[str]) -> None:
    releases_dir = plugin_dir / "releases"
    if not releases_dir.is_dir():
        return
    entries = sorted(
        (p for p in releases_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    kept = 0
    for entry in entries:
        if entry.name in keep_versions:
            kept += 1
            continue
        if kept < KEEP_RELEASES:
            kept += 1
            continue
        shutil.rmtree(entry, ignore_errors=True)


def _download_bytes(url: str, token: str, agent_code: str, download_url: str, timeout: int = 120) -> bytes:
    sep = "&" if "?" in download_url else "?"
    endpoint = url.rstrip("/") + download_url + f"{sep}agent_code={urllib.parse.quote(agent_code)}"
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def sync_plugins(
    url: str,
    token: str,
    agent_code: str,
    desired: dict[str, dict],
    *,
    plugins_dir: Path | None = None,
    public_key_b64: str | None = None,
    lock_path: Path | None = None,
    downloader=None,
) -> dict:
    """将平台期望插件清单静默同步到本地（后台下载验签，拿锁原子切换 current.json）。"""
    root = Path(plugins_dir) if plugins_dir else default_plugins_dir()
    pub_key = (
        os.getenv("PLATFORM_PLUGIN_PUBLIC_KEY", "").strip()
        if public_key_b64 is None
        else public_key_b64.strip()
    )
    current_map = installed_plugins(root)
    updated: list[str] = []
    errors: dict[str, str] = {}

    for plugin_id, spec in (desired or {}).items():
        target_version = str(spec.get("version") or "").strip()
        if not target_version or current_map.get(plugin_id) == target_version:
            continue
        expected_sha = str(spec.get("sha256") or "").strip().lower()
        signature = str(spec.get("signature") or "").strip()
        download_url = str(spec.get("download_url") or "").strip()
        if not download_url:
            continue

        plugin_dir = root / plugin_id
        release_dir = plugin_dir / "releases" / target_version
        try:
            if downloader is not None:
                data = downloader(spec)
            else:
                data = _download_bytes(url, token, agent_code, download_url)

            actual_sha = hashlib.sha256(data).hexdigest().lower()
            if expected_sha and actual_sha != expected_sha:
                raise RuntimeError(f"SHA256 不匹配: expected={expected_sha[:12]} actual={actual_sha[:12]}")
            if not _verify_ed25519(data, signature, pub_key):
                raise RuntimeError("Ed25519 数字签名校验失败，拒绝安装")

            manifest = _safe_extract_zip(data, release_dir)
            _smoke_check(release_dir, manifest)

            prev_pointer = _read_json(plugin_dir / "current.json")
            prev_ver = str(prev_pointer.get("version") or "")
            new_pointer = {
                "plugin_id": plugin_id,
                "bank_code": str(manifest.get("bank_code") or spec.get("bank_code") or "").strip().lower(),
                "version": target_version,
                "prev": prev_ver if prev_ver != target_version else str(prev_pointer.get("prev") or ""),
                "entry": manifest.get("entry") or {},
                "capabilities": manifest.get("capabilities") or [],
            }
            lock = AgentBusLock(lock_path or default_lock_path())
            if not lock.acquire(timeout=30.0, interval=0.3):
                raise RuntimeError("客户机正忙，暂缓切换插件指针（下次心跳重试）")
            try:
                _atomic_write_json(plugin_dir / "current.json", new_pointer)
            finally:
                lock.release()

            _prune_old_releases(plugin_dir, {target_version, new_pointer["prev"]})
            updated.append(f"{plugin_id}@{target_version}")
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(release_dir, ignore_errors=True)
            errors[plugin_id] = str(exc)

    return {"updated": updated, "errors": errors}


def resolve_plugin_command(
    bank_code: str,
    kind: str,
    task_path: Path,
    python_exe: str,
    *,
    plugins_dir: Path | None = None,
) -> tuple[list[str], Path, list[str]] | None:
    """按 bank_code 查找本机已生效插件，返回 (argv, cwd, extra_pythonpaths)。

    若未找到对应插件返回 None，调用方平滑回退到 collectors.yaml。
    """
    code = (bank_code or "").strip().lower()
    if not code:
        return None
    root = Path(plugins_dir) if plugins_dir else default_plugins_dir()
    if not root.is_dir():
        return None

    extra_paths: list[str] = []
    common_cur = _read_json(root / "common" / "current.json")
    common_ver = str(common_cur.get("version") or "").strip()
    if common_ver:
        common_dir = root / "common" / "releases" / common_ver
        if common_dir.is_dir():
            extra_paths.append(str(common_dir))

    for plugin_dir in root.iterdir():
        if not plugin_dir.is_dir():
            continue
        cur = _read_json(plugin_dir / "current.json")
        if str(cur.get("bank_code") or "").strip().lower() != code:
            continue
        version = str(cur.get("version") or "").strip()
        release_dir = plugin_dir / "releases" / version
        if not version or not release_dir.is_dir():
            continue
        entry = cur.get("entry") or {}
        raw_cmd = entry.get(kind)
        if not raw_cmd:
            default_script = "run_draft.py" if kind == "draft" else "run_task.py"
            if not (release_dir / default_script).is_file():
                continue
            raw_cmd = [default_script]
        parts = [str(p) for p in raw_cmd] if isinstance(raw_cmd, list) else [str(raw_cmd)]
        if parts and parts[0].endswith(".py"):
            argv = [python_exe, *parts, "--task", str(task_path)]
        else:
            argv = [*parts, "--task", str(task_path)]
        return argv, release_dir, extra_paths
    return None


def _encode_multipart(fields: dict[str, str], filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = f"----UKeyAgentDiag{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    safe_name = Path(filename).name.replace('"', "_")
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(content)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def upload_diagnostics(
    url: str,
    token: str,
    agent_code: str,
    task_id: int,
    attempt: int,
    file_paths: list[Path | str],
    *,
    task_kind: str = "collection",
    error_code: str = "",
    stage: str = "",
    timeout: int = 60,
) -> int:
    """将失败现场诊断文件通过 Multipart 逐个上传到平台（静默容错，不阻断主流程）。"""
    endpoint_path = (
        f"/api/agent/draft-tasks/{task_id}/diagnostics"
        if task_kind == "draft"
        else f"/api/agent/tasks/{task_id}/diagnostics"
    )
    endpoint = url.rstrip("/") + endpoint_path
    uploaded = 0
    seen: set[str] = set()
    for raw in file_paths:
        path = Path(str(raw))
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            content = path.read_bytes()
            if not content or len(content) > MAX_DIAG_UPLOAD_BYTES:
                continue
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body, ctype = _encode_multipart(
                {
                    "agent_code": agent_code,
                    "attempt": str(attempt),
                    "task_kind": task_kind,
                    "error_code": error_code,
                    "stage": stage,
                },
                path.name,
                content,
                mime,
            )
            request = urllib.request.Request(
                endpoint,
                data=body,
                method="POST",
                headers={
                    "Content-Type": ctype,
                    "Authorization": f"Bearer {token}",
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(request, timeout=timeout):
                uploaded += 1
        except Exception:
            continue
    return uploaded
