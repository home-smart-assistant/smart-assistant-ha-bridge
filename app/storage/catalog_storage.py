import json
import sqlite3
from typing import Any

from app.core import settings
from app.models.schemas import ApiCatalogItem, ToolCatalogFile, ToolCatalogItem
from app.services.catalog_defaults import default_api_catalog_items, default_tool_catalog_items


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.HA_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tool_catalog_schema(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(tool_catalog)").fetchall()
    columns = {str(row["name"]) for row in rows}
    migrations: list[str] = []

    if "tool_version" not in columns:
        migrations.append("ALTER TABLE tool_catalog ADD COLUMN tool_version INTEGER NOT NULL DEFAULT 1")
    if "schema_version" not in columns:
        migrations.append("ALTER TABLE tool_catalog ADD COLUMN schema_version TEXT NOT NULL DEFAULT '1.0'")
    if "permission_level" not in columns:
        migrations.append("ALTER TABLE tool_catalog ADD COLUMN permission_level TEXT NOT NULL DEFAULT 'low'")
    if "environment_tags_json" not in columns:
        migrations.append(
            "ALTER TABLE tool_catalog ADD COLUMN environment_tags_json TEXT NOT NULL DEFAULT '[\"home\",\"prod\"]'"
        )
    if "allowed_agents_json" not in columns:
        migrations.append(
            "ALTER TABLE tool_catalog ADD COLUMN allowed_agents_json TEXT NOT NULL DEFAULT '[\"home_automation_agent\"]'"
        )
    if "rollout_percentage" not in columns:
        migrations.append("ALTER TABLE tool_catalog ADD COLUMN rollout_percentage INTEGER NOT NULL DEFAULT 100")

    for sql in migrations:
        conn.execute(sql)


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _parse_json_string_list(raw: Any) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
        if isinstance(parsed, list):
            values = [str(item).strip() for item in parsed if str(item).strip()]
            return values
    except Exception:
        pass
    return []


def _normalize_permission_level(raw: Any) -> str:
    normalized = str(raw or "low").strip().lower()
    if normalized in {"low", "medium", "high", "critical"}:
        return normalized
    return "low"


def init_database() -> None:
    settings.HA_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with settings.storage_lock:
        with get_db_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tool_catalog (
                    tool_name TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    service TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    tool_version INTEGER NOT NULL DEFAULT 1,
                    schema_version TEXT NOT NULL DEFAULT '1.0',
                    permission_level TEXT NOT NULL DEFAULT 'low',
                    environment_tags_json TEXT NOT NULL DEFAULT '["home","prod"]',
                    allowed_agents_json TEXT NOT NULL DEFAULT '["home_automation_agent"]',
                    rollout_percentage INTEGER NOT NULL DEFAULT 100,
                    description TEXT NOT NULL DEFAULT '',
                    default_arguments_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS api_catalog (
                    endpoint_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    request_example_json TEXT,
                    read_only INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS runtime_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    ha_base_url TEXT NOT NULL,
                    ha_token TEXT NOT NULL DEFAULT '',
                    ha_timeout_sec REAL NOT NULL,
                    ha_context_timeout_sec REAL NOT NULL
                );
                """
            )
            _ensure_tool_catalog_schema(conn)
            conn.commit()


def write_legacy_tool_catalog(items: dict[str, ToolCatalogItem]) -> None:
    payload = ToolCatalogFile(
        version=1,
        tools=sorted((x.model_copy(deep=True) for x in items.values()), key=lambda x: x.tool_name),
    )
    settings.HA_TOOL_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings.HA_TOOL_CATALOG_PATH.write_text(
        json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_legacy_tool_catalog() -> dict[str, ToolCatalogItem]:
    if not settings.HA_TOOL_CATALOG_PATH.exists():
        defaults = {x.tool_name: x for x in default_tool_catalog_items()}
        write_legacy_tool_catalog(defaults)
        return defaults

    try:
        raw = settings.HA_TOOL_CATALOG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            items = [ToolCatalogItem.model_validate(x) for x in data]
        else:
            items = ToolCatalogFile.model_validate(data).tools
        if not items:
            raise ValueError("empty legacy catalog")
        return {x.tool_name: x for x in items}
    except Exception:
        defaults = {x.tool_name: x for x in default_tool_catalog_items()}
        write_legacy_tool_catalog(defaults)
        return defaults


def save_tool_catalog_to_db(items: dict[str, ToolCatalogItem]) -> None:
    with settings.storage_lock:
        with get_db_connection() as conn:
            _ensure_tool_catalog_schema(conn)
            conn.execute("DELETE FROM tool_catalog")
            for item in sorted(items.values(), key=lambda x: x.tool_name):
                conn.execute(
                    """
                    INSERT INTO tool_catalog (
                        tool_name, domain, service, strategy, enabled,
                        tool_version, schema_version, permission_level,
                        environment_tags_json, allowed_agents_json, rollout_percentage,
                        description, default_arguments_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.tool_name,
                        item.domain,
                        item.service,
                        item.strategy,
                        1 if item.enabled else 0,
                        item.tool_version,
                        item.schema_version,
                        item.permission_level,
                        json.dumps(item.environment_tags, ensure_ascii=False),
                        json.dumps(item.allowed_agents, ensure_ascii=False),
                        item.rollout_percentage,
                        item.description,
                        json.dumps(item.default_arguments, ensure_ascii=False),
                    ),
                )
            conn.commit()


def load_tool_catalog_from_db() -> dict[str, ToolCatalogItem]:
    with settings.storage_lock:
        with get_db_connection() as conn:
            _ensure_tool_catalog_schema(conn)
            rows = conn.execute(
                """
                SELECT
                    tool_name, domain, service, strategy, enabled,
                    tool_version, schema_version, permission_level,
                    environment_tags_json, allowed_agents_json, rollout_percentage,
                    description, default_arguments_json
                FROM tool_catalog
                ORDER BY tool_name
                """
            ).fetchall()

    items: dict[str, ToolCatalogItem] = {}
    for row in rows:
        defaults = _parse_json_dict(row["default_arguments_json"])
        environment_tags = _parse_json_string_list(row["environment_tags_json"]) or ["home", "prod"]
        allowed_agents = _parse_json_string_list(row["allowed_agents_json"]) or ["home_automation_agent"]

        item = ToolCatalogItem(
            tool_name=str(row["tool_name"]),
            domain=str(row["domain"]),
            service=str(row["service"]),
            strategy=str(row["strategy"]),
            enabled=bool(row["enabled"]),
            tool_version=max(1, int(row["tool_version"] or 1)),
            schema_version=str(row["schema_version"] or "1.0"),
            permission_level=_normalize_permission_level(row["permission_level"]),
            environment_tags=environment_tags,
            allowed_agents=allowed_agents,
            rollout_percentage=max(0, min(100, int(row["rollout_percentage"] or 100))),
            description=str(row["description"] or ""),
            default_arguments=defaults,
        )
        items[item.tool_name] = item
    return items


def seed_tool_catalog_if_needed() -> None:
    with settings.storage_lock:
        with get_db_connection() as conn:
            row = conn.execute("SELECT COUNT(1) AS c FROM tool_catalog").fetchone()
            count = int(row["c"]) if row else 0

    if count > 0:
        return

    seeded = (
        read_legacy_tool_catalog()
        if settings.HA_TOOL_CATALOG_PATH.exists()
        else {x.tool_name: x for x in default_tool_catalog_items()}
    )
    save_tool_catalog_to_db(seeded)
    write_legacy_tool_catalog(seeded)


def merge_missing_default_tools() -> None:
    current = load_tool_catalog_from_db()
    if not current:
        return

    def merge_missing_dict_values(target: dict[str, Any], defaults: dict[str, Any]) -> bool:
        changed_local = False
        for key, value in defaults.items():
            if key not in target:
                target[key] = value
                changed_local = True
                continue
            if isinstance(target[key], dict) and isinstance(value, dict):
                if merge_missing_dict_values(target[key], value):
                    changed_local = True
        return changed_local

    changed = False
    for item in default_tool_catalog_items():
        if item.tool_name not in current:
            current[item.tool_name] = item
            changed = True
            continue

        existing = current[item.tool_name]
        update_fields: dict[str, Any] = {}
        merged_defaults = dict(existing.default_arguments)
        if merge_missing_dict_values(merged_defaults, item.default_arguments):
            update_fields["default_arguments"] = merged_defaults
        if existing.tool_version < 1:
            update_fields["tool_version"] = item.tool_version
        if not existing.schema_version.strip():
            update_fields["schema_version"] = item.schema_version
        if not existing.environment_tags:
            update_fields["environment_tags"] = list(item.environment_tags)
        if not existing.allowed_agents:
            update_fields["allowed_agents"] = list(item.allowed_agents)
        if not 0 <= existing.rollout_percentage <= 100:
            update_fields["rollout_percentage"] = item.rollout_percentage

        if update_fields:
            current[item.tool_name] = existing.model_copy(update=update_fields)
            changed = True

    if changed:
        save_tool_catalog_to_storage(current)


def seed_api_catalog_if_needed() -> None:
    with settings.storage_lock:
        with get_db_connection() as conn:
            for item in sorted(default_api_catalog_items(), key=lambda x: x.sort_order):
                conn.execute(
                    """
                    INSERT INTO api_catalog (
                        endpoint_key, display_name, method, path, description,
                        request_example_json, read_only, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(endpoint_key) DO UPDATE SET
                        display_name = excluded.display_name,
                        method = excluded.method,
                        path = excluded.path,
                        description = excluded.description,
                        request_example_json = excluded.request_example_json,
                        read_only = excluded.read_only,
                        sort_order = excluded.sort_order
                    """,
                    (
                        item.endpoint_key,
                        item.display_name,
                        item.method,
                        item.path,
                        item.description,
                        json.dumps(item.request_example, ensure_ascii=False)
                        if item.request_example is not None
                        else None,
                        1 if item.read_only else 0,
                        item.sort_order,
                    ),
                )
            conn.commit()


def load_api_catalog_from_db() -> list[ApiCatalogItem]:
    with settings.storage_lock:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT endpoint_key, display_name, method, path, description,
                       request_example_json, read_only, sort_order
                FROM api_catalog
                ORDER BY sort_order, endpoint_key
                """
            ).fetchall()

    result: list[ApiCatalogItem] = []
    for row in rows:
        request_example: dict[str, Any] | list[Any] | None = None
        if row["request_example_json"]:
            try:
                parsed = json.loads(row["request_example_json"])
                if isinstance(parsed, (dict, list)):
                    request_example = parsed
            except Exception:
                request_example = None

        result.append(
            ApiCatalogItem(
                endpoint_key=str(row["endpoint_key"]),
                display_name=str(row["display_name"]),
                method=str(row["method"]),
                path=str(row["path"]),
                description=str(row["description"] or ""),
                request_example=request_example,
                read_only=bool(row["read_only"]),
                sort_order=int(row["sort_order"] or 0),
            )
        )
    return result


def load_runtime_config_from_db() -> dict[str, Any]:
    with settings.storage_lock:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT ha_base_url, ha_token, ha_timeout_sec, ha_context_timeout_sec
                FROM runtime_config
                WHERE id = 1
                """
            ).fetchone()

    if not row:
        return {
            "ha_base_url": settings.HA_BASE_URL,
            "ha_token": settings.HA_TOKEN,
            "ha_timeout_sec": settings.HA_TIMEOUT_SEC,
            "ha_context_timeout_sec": settings.HA_CONTEXT_TIMEOUT_SEC,
        }

    return {
        "ha_base_url": str(row["ha_base_url"] or settings.HA_BASE_URL),
        "ha_token": str(row["ha_token"] or ""),
        "ha_timeout_sec": float(row["ha_timeout_sec"] or settings.HA_TIMEOUT_SEC),
        "ha_context_timeout_sec": float(row["ha_context_timeout_sec"] or settings.HA_CONTEXT_TIMEOUT_SEC),
    }


def save_runtime_config_to_db(
    *,
    ha_base_url: str,
    ha_token: str,
    ha_timeout_sec: float,
    ha_context_timeout_sec: float,
) -> None:
    with settings.storage_lock:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_config (
                    id, ha_base_url, ha_token, ha_timeout_sec, ha_context_timeout_sec
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    ha_base_url = excluded.ha_base_url,
                    ha_token = excluded.ha_token,
                    ha_timeout_sec = excluded.ha_timeout_sec,
                    ha_context_timeout_sec = excluded.ha_context_timeout_sec
                """,
                (ha_base_url, ha_token, ha_timeout_sec, ha_context_timeout_sec),
            )
            conn.commit()


def save_tool_catalog_to_storage(items: dict[str, ToolCatalogItem]) -> None:
    save_tool_catalog_to_db(items)
    write_legacy_tool_catalog(items)


def seed_runtime_config_if_needed() -> None:
    with settings.storage_lock:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_config (
                    id, ha_base_url, ha_token, ha_timeout_sec, ha_context_timeout_sec
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    settings.HA_BASE_URL,
                    settings.HA_TOKEN,
                    settings.HA_TIMEOUT_SEC,
                    settings.HA_CONTEXT_TIMEOUT_SEC,
                ),
            )
            conn.commit()


def bootstrap_storage() -> None:
    init_database()
    seed_tool_catalog_if_needed()
    merge_missing_default_tools()
    seed_api_catalog_if_needed()
    seed_runtime_config_if_needed()
