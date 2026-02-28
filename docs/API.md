# smart_assistant_ha_bridge API 文档

Base URL 示例：`http://<bridge-host>:8092`

## 1) API 管理（只读）
### `GET /v1/apis/catalog`
用途：
- 提供 UI 的 API 管理清单（名称、方法、路径、描述、示例）

响应关键字段：
- `storage`（当前为 `sqlite`）
- `db_path`
- `apis[]`

---

## 2) 工具管理
### `GET /v1/tools/catalog`
用途：
- 读取完整工具清单（包含禁用项）

响应关键字段：
- `storage`
- `db_path`
- `legacy_path`
- `tools[]`

### `GET /v1/tools/whitelist`
用途：
- 读取已启用工具关键字（可直接用于调试调用）

响应示例：
```json
{
  "tools": [
    "home.lights.on",
    "home.lights.off",
    "home.curtains.open",
    "home.curtains.close",
    "home.curtains.stop",
    "home.scene.activate",
    "home.climate.turn_on",
    "home.climate.turn_off",
    "home.climate.set_temperature"
  ]
}
```

### `PUT /v1/tools/catalog/{tool_name}`
用途：
- 新增或更新工具定义

### `DELETE /v1/tools/catalog/{tool_name}`
用途：
- 删除工具定义

### `POST /v1/tools/catalog/reload`
用途：
- 重新从数据库加载工具清单到内存

---

## 3) API 调试
### `POST /v1/tools/call`
用途：
- 根据工具关键字执行 HA 调用（核心调试接口）

请求体示例：
```json
{
  "tool_name": "home.lights.on",
  "arguments": {
    "area": "study"
  },
  "trace_id": "ui-debug-001",
  "dry_run": false
}
```

响应关键字段：
- `success`
- `message`
- `trace_id`
- `data.domain`
- `data.service`
- `data.service_data`
- `data.ha_response`（调用成功时）
- 可使用 `dry_run=true` 仅查看解析结果，不触发 HA 动作

---

## 4) 设备控制（Agent 推荐）
### `POST /v1/device/lights/control`
用途：
- 标准化灯光控制接口（`action=on/off`）
- 支持 `area` 或 `entity_id`（可为数组）

请求体示例：
```json
{
  "action": "on",
  "area": "study",
  "trace_id": "agent-light-001",
  "dry_run": false
}
```

### `POST /v1/device/curtains/control`
用途：
- 标准化窗帘控制接口（`action=open/close/stop`）

### `POST /v1/device/climate/control`
用途：
- 标准化空调接口（`action=turn_on/turn_off/set_temperature`）
- `set_temperature` 时必须提供 `temperature(16~30)`

### `POST /v1/device/custom/control`
用途：
- 自定义设备控制接口，底层走工具关键字能力
- 适合扩展暂未提供专用接口的设备

---

## 5) 系统配置
### `GET /v1/config/ha`
用途：
- 读取 HA 配置（token 脱敏）

### `PUT /v1/config/ha`
用途：
- 更新 HA 配置并即时生效

---

## 6) Agent 上下文
### `GET /v1/context/summary`
用途：
- 返回 Agent 可用上下文（工具、实体、状态、服务）

---

## 7) HA 发现接口（Agent 推荐）
### `GET /v1/ha/overview`
用途：
- 返回 HA 总览统计（实体数、房间数、服务域数、Top Domain）

### `GET /v1/ha/areas`
用途：
- 返回 HA 房间列表
- 先尝试 HA 模板接口 `areas()` / `area_entities()`，再合并配置映射作为兜底

### `GET /v1/ha/entities`
用途：
- 返回 HA 实体列表，支持以下查询参数：
  - `domain`：按域过滤（如 `switch`）
  - `area`：按房间过滤（`area_id` 或 `area_name`）
  - `q`：关键字过滤（entity_id/friendly_name/state）
  - `limit`：条数上限（1~2000）
  - `include_attributes`：是否携带完整 attributes

### `GET /v1/ha/entities/{entity_id}`
用途：
- 返回指定实体状态（可选携带 attributes）

### `GET /v1/ha/services`
用途：
- 返回 HA 服务目录（可选 `domain` 过滤）

---

## 8) 日志中心
### `POST /v1/logs/ui`
用途：
- UI 上报操作日志（菜单切换、调试发送、配置保存等）

请求体示例：
```json
{
  "action": "debug.call_api",
  "view": "debug",
  "detail": {
    "path": "/v1/tools/call"
  },
  "success": true
}
```

### `GET /v1/logs/recent`
用途：
- 查询最近日志（文件存储），支持 `limit/source/event_type` 过滤
- `source` 支持多值：`?source=ui&source=system`
- 兼容逗号写法：`?source=ui,system`

响应关键字段：
- `storage`（固定 `file`）
- `log_path`
- `current_size_bytes`
- `max_bytes`
- `backup_count`
- `retention_days`
- `queue_max`
- `queue_size`
- `dropped_count`
- `worker_alive`
- `logs[]`
- `logs[].detail.request`（`ha_request` 类型中包含发给 HA 的请求详情：`base_url/path/json`）

---

## 9) Strategy 说明
- `passthrough`：透传 `arguments`
- `light_area`：按 `area -> HA_LIGHT_*_ENTITY_ID`
- `cover_area`：按 `area -> HA_COVER_*_ENTITY_ID`
- `scene_id`：要求 `scene_id`
- `climate_area`：按 `area -> HA_CLIMATE_*_ENTITY_ID`
- `climate_area_temperature`：按 `area` 映射实体并要求 `temperature(16~30)`

---

## 10) 常见错误
- `400 tool not allowed`: 工具不存在或被禁用
- `400 ... is required`: 缺少策略必填参数
- `400 temperature must be between 16 and 30`: 空调温度越界
- `success=false, message=HA token missing`: 真实模式缺少 HA token
