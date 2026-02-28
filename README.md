# smart_assistant_ha_bridge

Python Home Assistant Bridge.

## Tech Stack
- Python
- FastAPI / Uvicorn
- httpx
- SQLite (embedded)

## Responsibilities
- Execute HA tool calls through a whitelist/catalog
- Persist API catalog + tool catalog in embedded SQLite (`HA_DB_PATH`, default `app/bridge.db`)
- Keep legacy tool catalog JSON (`HA_TOOL_CATALOG_PATH`) in sync for compatibility
- Provide HA context summary for Agent
- Record external/API requests and UI operation logs to file (`HA_LOG_PATH`, JSONL)

## Project Structure
```
app/
  main.py                  # FastAPI app entrypoint, router assembly
  core/settings.py         # Runtime settings, paths, locks, entity map
  models/schemas.py        # Pydantic request/response models
  storage/catalog_storage.py # SQLite + legacy JSON persistence
  services/                # Business logic (config/catalog/HA)
  routers/                 # API route modules
  web/ui.html              # Frontend UI
```

## Core APIs
- `GET /v1/apis/catalog`
- `GET /v1/tools/whitelist`
- `GET /v1/tools/catalog`
- `PUT /v1/tools/catalog/{tool_name}`
- `DELETE /v1/tools/catalog/{tool_name}`
- `POST /v1/tools/catalog/reload`
- `POST /v1/tools/call`
- `POST /v1/device/lights/control`
- `POST /v1/device/curtains/control`
- `POST /v1/device/climate/control`
- `POST /v1/device/custom/control`
- `GET /v1/config/ha`
- `PUT /v1/config/ha`
- `GET /v1/context/summary`
- `GET /v1/ha/overview`
- `GET /v1/ha/areas`
- `GET /v1/ha/entities`
- `GET /v1/ha/entities/{entity_id}`
- `GET /v1/ha/services`
- `POST /v1/logs/ui`
- `GET /v1/logs/recent`

接口细节见：`docs/API.md`

## Local Run
```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8092 --reload
```

Runtime settings are loaded from `.env` automatically if the file exists.

## Key Config
See `.env.example`.

- `HA_BASE_URL`
- `HA_TOKEN`
- `HA_TIMEOUT_SEC`
- `HA_CONTEXT_TIMEOUT_SEC`
- `TEXT_ENCODING_STRICT` (`true` by default; reject irrecoverable garbled text with HTTP 400)
- `HA_TOOL_CATALOG_PATH`
- `HA_DB_PATH`
- `HA_LOG_PATH`
- `HA_LOG_MAX_BYTES`
- `HA_LOG_BACKUP_COUNT`
- `HA_LOG_RETENTION_DAYS`
- `HA_LOG_QUEUE_MAX`

Entity mapping:
- `HA_LIGHT_LIVING_ROOM_ENTITY_ID`
- `HA_LIGHT_BEDROOM_ENTITY_ID`
- `HA_LIGHT_STUDY_ENTITY_ID`
- `HA_CLIMATE_LIVING_ROOM_ENTITY_ID`
- `HA_CLIMATE_BEDROOM_ENTITY_ID`
- `HA_CLIMATE_STUDY_ENTITY_ID`
- `HA_COVER_LIVING_ROOM_ENTITY_ID`
- `HA_COVER_BEDROOM_ENTITY_ID`
- `HA_COVER_STUDY_ENTITY_ID`

## Notes
- The bridge always calls real Home Assistant services (no mock mode).
- API管理和工具管理目前建议按只读使用，后续可再开放 UI 写操作。
- 日志只写文件，不写数据库；采用异步队列写盘，避免阻塞接口请求。
- 日志采用按大小轮转 + 保留份数/天数清理，并在队列满时丢弃并计数。
- 可使用 `dry_run=true` 做无副作用预演。
- 编码策略：所有源码/文档使用 UTF-8；入参在执行前做统一文本规范化，无法可靠修复时返回 `400`（`error_code=invalid_text_encoding`）。

## CI/CD (Deploy to 192.168.3.103)
- Workflow: `.github/workflows/cicd-deploy.yml`
- Trigger: push to `main` or manual `workflow_dispatch`
- Output image:
  - `ghcr.io/home-smart-assistant/smart-assistant-ha-bridge:main`
  - `ghcr.io/home-smart-assistant/smart-assistant-ha-bridge:<commit_sha>`
- Deploy target service: `smart_assistant_ha_bridge` in `/opt/smart-assistant/docker-compose.yml`
- Runner labels:
  - Build: `[self-hosted, Windows, X64, builder-win]` (recommended on `192.168.3.11`)
  - Deploy: `[self-hosted, Linux, X64, deploy-linux]` (recommended on `192.168.3.103`)
- Optional config:
  - Repository variable `DEPLOY_PATH` (default `/opt/smart-assistant`)
  - `GHCR_USERNAME` + `GHCR_TOKEN` only if your package policy requires explicit login
