import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    host: str = os.getenv("PLATFORM_HOST", "127.0.0.1")
    port: int = int(os.getenv("PLATFORM_PORT", "8800"))
    data_dir: Path = Path(os.getenv("PLATFORM_DATA_DIR", str(BASE_DIR / "data"))).resolve()
    secret_key: str = os.getenv("PLATFORM_SECRET_KEY", "change-this-in-production")
    admin_user: str = os.getenv("PLATFORM_ADMIN_USER", "admin")
    admin_password: str = os.getenv("PLATFORM_ADMIN_PASSWORD", "admin123")
    timezone: str = os.getenv("PLATFORM_TIMEZONE", "Asia/Shanghai")
    enable_worker: bool = _env_bool("PLATFORM_ENABLE_WORKER", True)
    enable_scheduler: bool = _env_bool("PLATFORM_ENABLE_SCHEDULER", True)
    gf3_exe: str = os.getenv("PLATFORM_GF3_EXE", "gf3")
    mb_exe: str = os.getenv("PLATFORM_MB_EXE", "")
    mb_user: str = os.getenv("PLATFORM_MB_USER", "")
    mb_ter_ids: str = os.getenv("PLATFORM_MB_TER_IDS", "")
    agent_token: str = os.getenv("PLATFORM_AGENT_TOKEN", "")
    agent_token_map: str = os.getenv("PLATFORM_AGENT_TOKEN_MAP", "")
    agent_code: str = os.getenv("PLATFORM_AGENT_CODE", "local")
    agent_name: str = os.getenv("PLATFORM_AGENT_NAME", "")
    collector_python: str = os.getenv("PLATFORM_COLLECTOR_PYTHON", "")
    collectors_file: Path = Path(
        os.getenv("PLATFORM_COLLECTORS_FILE") or str(BASE_DIR / "collectors.yaml")
    )
    # 采集程序的源码目录（仓库里与 platform 平级），源码模式下的工作目录
    collectors_root: Path = Path(
        os.getenv("PLATFORM_COLLECTORS_ROOT") or str(BASE_DIR.parent / "collectors")
    )
    usb_robot_base_url: str = os.getenv("PLATFORM_USB_ROBOT_BASE_URL", "https://www.mbiai.com")
    usb_robot_token: str = os.getenv("PLATFORM_USB_ROBOT_TOKEN", "")
    usb_robot_user_no: str = os.getenv("PLATFORM_USB_ROBOT_USER_NO", "")
    usb_robot_point_no: str = os.getenv("PLATFORM_USB_ROBOT_POINT_NO", "")
    usb_robot_passwd: str = os.getenv("PLATFORM_USB_ROBOT_PASSWD", "")
    usb_robot_web_ip: str = os.getenv("PLATFORM_USB_ROBOT_WEB_IP", "")
    usb_robot_list_type: str = os.getenv("PLATFORM_USB_ROBOT_LIST_TYPE", "0")
    db_url: str = field(default="")

    def __post_init__(self) -> None:
        if not self.db_url:
            object.__setattr__(self, "db_url", os.getenv("PLATFORM_DB_URL") or f"sqlite:///{self.data_dir / 'app.db'}")

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def bus_lock_path(self) -> Path:
        return self.data_dir / "bus.lock"

    @property
    def usb_robot_enabled(self) -> bool:
        return bool(self.usb_robot_token and self.usb_robot_user_no and self.usb_robot_point_no)

    @property
    def mb_ter_id_list(self) -> list[str]:
        return [item.strip() for item in self.mb_ter_ids.split(",") if item.strip()]

    @property
    def agent_tokens(self) -> set[str]:
        return {item.strip() for item in self.agent_token.split(",") if item.strip()}

    @property
    def agent_token_scopes(self) -> dict[str, set[str]]:
        scopes: dict[str, set[str]] = {}
        for pair in self.agent_token_map.split(";"):
            code, _, token = pair.strip().partition("=")
            code = code.strip()
            token = token.strip()
            if code and token:
                scopes.setdefault(token, set()).add(code)
        return scopes

    @property
    def master_key_path(self) -> Path:
        return self.data_dir / "master.key"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.artifacts_dir, self.tasks_dir, self.profiles_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
