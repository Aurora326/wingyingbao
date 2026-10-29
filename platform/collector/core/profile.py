from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


@dataclass
class BankProfile:
    data: dict

    @classmethod
    def load(cls, bank_code: str, path: str | Path | None = None) -> "BankProfile":
        target = Path(path) if path else PROFILE_DIR / f"{bank_code}.yaml"
        if not target.exists():
            return cls({})
        return cls(yaml.safe_load(target.read_text(encoding="utf-8")) or {})

    def get(self, path: str, default=None):
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def selectors(self, key: str) -> list[str]:
        value = self.get(f"selectors.{key}") or []
        return value if isinstance(value, list) else [value]

    def dialogs(self, key: str) -> list[str]:
        value = self.get(f"dialogs.{key}") or []
        return value if isinstance(value, list) else [value]

    def url(self, key: str, default: str = "") -> str:
        return self.get(f"urls.{key}", default) or default
