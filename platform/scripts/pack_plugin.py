"""研发侧插件一键打包与 Ed25519 离线签名工具。

用法示例：
  1. 生成离线密钥对（仅执行一次，私钥离线保管，公钥配到客户端/平台）：
     python scripts/pack_plugin.py --keygen --key-dir ./keys

  2. 打包并签名某家银行插件目录（目录根需含 manifest.json）：
     python scripts/pack_plugin.py ../上海银行/bosh_automation --private-key ./keys/plugin_private.b64 --out ./dist/plugins
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from app.services.plugin_signing import generate_keypair, sha256_bytes, sign_bytes  # noqa: E402

EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".pylibs",
    "venv",
    ".venv",
    "build",
    "dist",
    "downloads",
    "logs",
    "records",
    "temp",
    "debug",
    "chrome_user_data",
    "chrome_portable",
    "发布版",
}
EXCLUDED_FILES = {
    ".env",
    "access_token.conf",
    "config.ini",
    "config.json",
    "config.yaml",
    ".DS_Store",
    "Thumbs.db",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".zip", ".mp4", ".exe", ".dll", ".db"}


def _should_skip(rel_path: Path) -> bool:
    for part in rel_path.parts[:-1]:
        if part in EXCLUDED_DIRS:
            return True
    name = rel_path.name
    if name in EXCLUDED_FILES:
        return True
    if rel_path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    return False


def pack_plugin(
    source_dir: Path,
    out_dir: Path,
    *,
    private_key_b64: str = "",
) -> dict:
    source_dir = Path(source_dir).resolve()
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"目录缺少 manifest.json: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plugin_id = str(manifest.get("plugin_id") or "").strip().lower()
    version = str(manifest.get("version") or "").strip()
    if not plugin_id or not version:
        raise RuntimeError("manifest.json 必须包含 plugin_id 和 version")

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{plugin_id}-{version}.zip"

    file_count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(source_dir.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(source_dir)
            if _should_skip(rel):
                continue
            zf.write(item, rel.as_posix())
            file_count += 1

    raw = zip_path.read_bytes()
    digest = sha256_bytes(raw)
    signature = sign_bytes(raw, private_key_b64) if private_key_b64 else ""
    meta = {
        "plugin_id": plugin_id,
        "bank_code": str(manifest.get("bank_code") or "").strip().lower(),
        "version": version,
        "zip_path": str(zip_path),
        "size": len(raw),
        "files": file_count,
        "sha256": digest,
        "signature": signature,
        "changelog": str(manifest.get("changelog") or ""),
    }
    meta_path = out_dir / f"{plugin_id}-{version}.release.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="司库平台采集插件打包与 Ed25519 离线签名工具")
    parser.add_argument("source_dir", nargs="?", help="包含 manifest.json 的插件目录")
    parser.add_argument("--keygen", action="store_true", help="生成新的 Ed25519 签名密钥对")
    parser.add_argument("--key-dir", default=".", help="--keygen 输出目录")
    parser.add_argument("--private-key", default="", help="Ed25519 私钥文件路径或 Base64 字符串")
    parser.add_argument("--out", default=str(PLATFORM_ROOT / "dist" / "plugins"), help="输出目录")
    args = parser.parse_args()

    if args.keygen:
        priv, pub = generate_keypair()
        key_dir = Path(args.key_dir).resolve()
        key_dir.mkdir(parents=True, exist_ok=True)
        priv_file = key_dir / "plugin_private.b64"
        pub_file = key_dir / "plugin_public.b64"
        priv_file.write_text(priv + "\n", encoding="utf-8")
        pub_file.write_text(pub + "\n", encoding="utf-8")
        print(f"私钥已保存（请离线妥善保管，严禁提交 Git）: {priv_file}")
        print(f"公钥已保存（可配入 PLATFORM_PLUGIN_PUBLIC_KEY 与客户机）: {pub_file}")
        print(f"公钥内容: {pub}")
        return 0

    if not args.source_dir:
        parser.error("请指定插件目录，或使用 --keygen 生成密钥对")

    priv_b64 = ""
    if args.private_key:
        candidate = Path(args.private_key)
        priv_b64 = candidate.read_text(encoding="utf-8").strip() if candidate.is_file() else args.private_key.strip()

    meta = pack_plugin(Path(args.source_dir), Path(args.out), private_key_b64=priv_b64)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
