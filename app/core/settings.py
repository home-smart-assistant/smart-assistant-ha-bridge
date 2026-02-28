import os
from pathlib import Path
from threading import RLock


APP_NAME = "smart_assistant_ha_bridge"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE_PATH = PROJECT_ROOT / ".env"


def load_local_env(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        os.environ.setdefault(key, value)


load_local_env(ENV_FILE_PATH)


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_path(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    return value


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


HA_BASE_URL = os.getenv("HA_BASE_URL", "http://homeassistant.local:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_TIMEOUT_SEC = env_float("HA_TIMEOUT_SEC", 6.0)
HA_CONTEXT_TIMEOUT_SEC = env_float("HA_CONTEXT_TIMEOUT_SEC", 8.0)
TEXT_ENCODING_STRICT = env_bool("TEXT_ENCODING_STRICT", True)

APP_DIR = Path(__file__).resolve().parent.parent
HA_TOOL_CATALOG_PATH = env_path("HA_TOOL_CATALOG_PATH", str(APP_DIR / "tool_catalog.json"))
HA_DB_PATH = env_path("HA_DB_PATH", str(APP_DIR / "bridge.db"))
HA_LOG_PATH = env_path("HA_LOG_PATH", str(APP_DIR / "logs" / "operations.jsonl"))
HA_LOG_MAX_BYTES = env_int("HA_LOG_MAX_BYTES", 5 * 1024 * 1024)
HA_LOG_BACKUP_COUNT = max(1, env_int("HA_LOG_BACKUP_COUNT", 10))
HA_LOG_RETENTION_DAYS = max(1, env_int("HA_LOG_RETENTION_DAYS", 14))
HA_LOG_QUEUE_MAX = max(100, env_int("HA_LOG_QUEUE_MAX", 5000))
UI_PAGE_PATH = APP_DIR / "web" / "ui.html"

# Entity mapping can be overridden by environment variables.
HA_LIGHT_LIVING_ROOM_ENTITY_ID = env_str("HA_LIGHT_LIVING_ROOM_ENTITY_ID", "light.living_room")
HA_LIGHT_BEDROOM_ENTITY_ID = env_str("HA_LIGHT_BEDROOM_ENTITY_ID", "light.bedroom")
HA_LIGHT_STUDY_ENTITY_ID = env_str("HA_LIGHT_STUDY_ENTITY_ID", "light.study")
HA_CLIMATE_LIVING_ROOM_ENTITY_ID = env_str("HA_CLIMATE_LIVING_ROOM_ENTITY_ID", "climate.living_room_ac")
HA_CLIMATE_BEDROOM_ENTITY_ID = env_str("HA_CLIMATE_BEDROOM_ENTITY_ID", "climate.bedroom_ac")
HA_CLIMATE_STUDY_ENTITY_ID = env_str("HA_CLIMATE_STUDY_ENTITY_ID", "climate.study_ac")
HA_COVER_LIVING_ROOM_ENTITY_ID = env_str("HA_COVER_LIVING_ROOM_ENTITY_ID", "cover.living_room")
HA_COVER_BEDROOM_ENTITY_ID = env_str("HA_COVER_BEDROOM_ENTITY_ID", "cover.bedroom")
HA_COVER_STUDY_ENTITY_ID = env_str("HA_COVER_STUDY_ENTITY_ID", "cover.study")

AREA_ENTITY_MAP = {
    "light": {
        "living_room": HA_LIGHT_LIVING_ROOM_ENTITY_ID,
        "bedroom": HA_LIGHT_BEDROOM_ENTITY_ID,
        "study": HA_LIGHT_STUDY_ENTITY_ID,
    },
    "climate": {
        "living_room": HA_CLIMATE_LIVING_ROOM_ENTITY_ID,
        "bedroom": HA_CLIMATE_BEDROOM_ENTITY_ID,
        "study": HA_CLIMATE_STUDY_ENTITY_ID,
    },
    "cover": {
        "living_room": HA_COVER_LIVING_ROOM_ENTITY_ID,
        "bedroom": HA_COVER_BEDROOM_ENTITY_ID,
        "study": HA_COVER_STUDY_ENTITY_ID,
    },
}

runtime_config_lock = RLock()
catalog_lock = RLock()
storage_lock = RLock()
log_lock = RLock()
