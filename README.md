# smart_assistant_ha_bridge

Python Home Assistant Bridge 服务。

## 能力范围（V1）
- 白名单工具调用入口：`POST /v1/tools/call`
- 工具列表查询：`GET /v1/tools/whitelist`
- 健康检查：`GET /health`
- 支持真实 HA 调用或模拟模式

## 白名单工具
- `home.lights.on`
- `home.lights.off`
- `home.scene.activate`
- `home.climate.set_temperature`

## 工具调用契约
- `docs/schemas/tool-call.schema.json`

## 本地运行
```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8092 --reload
```

## 环境变量
参考 `.env.example`

- `HA_ENABLED=false` 时，返回模拟成功，便于联调
- `HA_ENABLED=true` 时，按 `HA_BASE_URL` + `HA_TOKEN` 调用真实 Home Assistant

## Docker
```bash
docker build -t smart-assistant-ha-bridge .
docker run --rm -p 8092:8092 --env-file .env.example smart-assistant-ha-bridge
```

