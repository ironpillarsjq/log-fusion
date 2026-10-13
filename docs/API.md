# API 文档

> 基线日期：2026-10-10。接口以 `backend/app/main.py` 当前路由为准。旧前端契约中但未出现在本文件的接口，均不视为已实现。

## 1. 通用约定

- 默认开发地址：`http://127.0.0.1:8080`。
- 当前没有认证或授权，请求不需要业务自定义 Header。
- JSON 请求应发送 `Content-Type: application/json`。
- 多数查询接口成功响应为 `{ "code": 0, "message": "ok", "data": ... }`，但健康检查、采集写入、心跳、HTML、SSE、WebSocket 和 CSV 不使用该包装。
- 未被业务代码捕获的异常由 FastAPI 返回 500；查询参数或请求体不满足类型/范围时通常返回 422。
- 框架自动提供 `/docs`、`/redoc` 和 `/openapi.json`。
- 当前只有一个自定义 HTTP 中间件 `_cache_policy`（`backend/app/main.py`），按三类设置 `Cache-Control`：
  `/static/vendor/*` → `public, max-age=31536000, immutable`（自托管库文件名带版本号）；
  其他 `/static/*` → `no-cache, no-store, must-revalidate`；HTML 页面 → `no-store, must-revalidate`
  （配合模板里的 `app.js?v=` 版本参数，避免浏览器继续用旧交互代码）。没有请求 ID、版本协商、速率限制、CORS 或统一错误响应模型。

## 2. 接口总表

| 方法 | 路径 | 类型 | 用途 |
|---|---|---|---|
| GET | `/` | HTML | Jinja2 仪表盘 |
| GET | `/partials/db-status` | HTML | HTMX 数据库状态片段 |
| GET | `/health` | JSON | 四库连接健康检查 |
| GET | `/api/v1/logs/categories` | JSON | 日志类别 |
| GET | `/api/v1/logs/summary` | JSON | 日志总量与分类统计 |
| GET | `/api/v1/logs/categories/{category}` | JSON | 分类分页查询 |
| GET | `/api/v1/logs/recent` | JSON | 跨平台最近日志 |
| GET | `/api/v1/logs/stream` | SSE | 日志事件流 |
| POST | `/api/v1/logs/{platform}` | JSON | Linux/Windows 日志采集 |
| GET | `/api/v1/logs/export` | CSV | 分类日志导出 |
| GET | `/api/v1/monitor/summary` | JSON | 心跳状态摘要 |
| GET | `/api/v1/monitor/clients` | JSON | 心跳客户端列表 |
| GET | `/api/v1/monitor/clients/{client_id}` | JSON | 客户端详情 |
| GET | `/api/v1/monitor/clients/{client_id}/logs` | JSON | 客户端最近日志兼容接口 |
| POST | `/api/v1/heartbeat` | JSON | 心跳上报 |
| WebSocket | `/ws/client-monitor` | WS | 客户端状态快照广播 |
| GET | `/api/v1/evidence/summary` | JSON | 存证状态摘要 |
| GET | `/api/v1/evidence` | JSON | 存证列表（分页） |
| GET | `/api/v1/evidence/export` | CSV | 存证导出（只读，不删除） |
| GET | `/api/v1/archive/preview` | JSON | 归档预览：只统计待归档条数 |
| POST | `/api/v1/archive/manual` | JSON | 手动归档：导出区间存证后删除 |
| POST | `/api/v1/archive/auto` | JSON | 立即执行一次自动归档 |
| GET | `/api/v1/archive/log` | JSON | 归档台账列表 |

## 3. 页面与健康接口

### 3.1 `GET /`

返回 `text/html` 的仪表盘。无参数、请求体或必需 Header。

处理时会尝试统计日志库；统计失败时仍返回 HTTP 200，并把页面初始汇总降级为 0。浏览器随后还会从 API 刷新数据。

### 3.2 `GET /partials/db-status`

返回 HTML `<span>` 片段，依次显示 `linux_logs`、`windows_logs`、`log_fusion`、`log_hash` 是否连接。无参数。

示例：

```html
<span class="badge rounded-pill text-bg-success">linux_logs: 已连接</span>
```

数据库连接失败不会使接口返回 5xx；对应 badge 显示“不可用”。

### 3.3 `GET /health`

无参数。始终以顶层 `status: ok` 表示应用能处理请求，数据库状态在独立字段中；`realtime` 给出当前进程内的实时连接数。

```json
{
  "status": "ok",
  "databases": {
    "linux_logs": true,
    "windows_logs": true,
    "log_fusion": false,
    "log_hash": true
  },
  "realtime": {
    "sse_clients": 1,
    "ws_clients": 1
  }
}
```

当前实现中即使全部数据库不可用也返回 HTTP 200。

`realtime.sse_clients` 是当前打开的 `/api/v1/logs/stream` 连接数，`realtime.ws_clients` 是 `/ws/client-monitor` 的连接数。两者用于排查“页面卡住但接口很快”的情况：浏览器对同一地址只允许 6 条 HTTP/1.1 连接，每个页面会占 1 条 SSE + 1 条 WebSocket，连接未释放时新请求会一直排队。SSE 连接的建立/关闭也会写日志。

## 4. 日志查询接口

### 4.1 `GET /api/v1/logs/categories`

返回 7 个 Linux 类别和 5 个 Windows 类别，无参数。

```json
{
  "code": 0,
  "message": "ok",
  "data": [
    {"key": "authentication_session", "label": "认证会话", "platform": "Linux"},
    {"key": "application", "label": "Application", "platform": "Windows"}
  ]
}
```

完整类别：

- Linux：`authentication_session`、`account_security_change`、`process_command_execution`、`file_object_access`、`network_ipc_communication`、`system_service_audit_lifecycle`、`security_policy_config_change`。
- Windows：`application`、`security`、`setup`、`system`、`forwardedevents`。

### 4.2 `GET /api/v1/logs/summary`

无参数。逐表统计 12 个类别（7 张 Linux 表 + 5 张 Windows 表）。

`total` 只包含这 12 张日志表；**客户端心跳（`log_fusion.client_status`）不计入**，心跳客户端数量见 §6 `/api/v1/monitor/summary`。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "total": 42,
    "by_platform": {"Linux": 12, "Windows": 30},
    "categories": [
      {"key": "authentication_session", "label": "认证会话", "platform": "Linux", "count": 12}
    ]
  }
}
```

任一表不存在或查询失败时没有局部降级，接口通常返回 500。

### 4.3 `GET /api/v1/logs/categories/{category}`

路径参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `category` | string | 表类别；代码只允许 ASCII 字母、数字、下划线，但目前没有强制属于类别 catalog |

查询参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `platform` | string | `Linux` | 只有大小写不敏感的 `windows` 走 Windows；其他值均走 Linux |
| `page` | integer | `1` | 内部小于 1 时改为 1 |
| `size` | integer | `20` | 内部限制到 1～100 |
| `keyword` | string/null | null | Windows 匹配 computer/provider/event_id；Linux 匹配 hostname/source_address/type |

示例：

```http
GET /api/v1/logs/categories/authentication_session?platform=Linux&page=1&size=20&keyword=server-a
```

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [
      {
        "event_time": "2026-10-10T08:00:00",
        "event_id": 1001,
        "category": "authentication_session",
        "type": "USER_AUTH",
        "operation": "login",
        "account": "root",
        "executable": "/usr/bin/sshd",
        "hostname": "server-a",
        "source_address": "10.0.0.8",
        "result": "success",
        "tips": ""
      }
    ],
    "page": 1,
    "size": 20,
    "total": 1,
    "category": "authentication_session",
    "platform": "Linux"
  }
}
```

错误行为：

- `category` 含 `-`、`.`、中文等非 ASCII 字母数字/下划线字符：HTTP 400，`{"detail":"invalid category"}`。
- 合法字符但表不存在、列不存在或数据库异常：通常 HTTP 500。
- 关键词只匹配目标表实际存在的列（`hostname`/`source_address`/`type` 的交集）：7 张 Linux 表均已按真实列适配，不再因缺列返回 500，详见 `DATABASE.md` §4.3。

### 4.4 `GET /api/v1/logs/recent`

查询参数：`limit`，整数，默认 20，范围 1～100；越界或类型错误返回 422。

响应 `data` 是按 `event_time` 倒序合并后的数组，最多 `limit` 条：

```json
{
  "code": 0,
  "message": "ok",
  "data": [
    {
      "event_time": "2026-10-10T08:00:00",
      "category": "authentication_session",
      "type": "USER_AUTH",
      "result": "success",
      "hostname": "server-a",
      "source_address": "10.0.0.8"
    }
  ]
}
```

实现先分别在两个库查询最多 `limit` 条，再在 Python 合并。Linux 各表缺失的 `hostname`/`source_address` 用 `NULL` 占位，缺列不再导致 500，这些字段在返回中为 `null`。

### 4.5 `GET /api/v1/logs/export`

查询参数：

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `platform` | string | `Linux` | 语义同分类查询 |
| `category` | string | `authentication_session` | 语义同分类查询 |
| `size` | integer | `1000` | 1～10000；无效值返回 422。导出按该值取数（内部上限 10000），不受分类列表接口 100 行上限约束 |

成功返回：

- HTTP 200
- `Content-Type: text/csv`
- `Content-Disposition: attachment; filename="{category}.csv"`
- 内容以 UTF-8 BOM 开头；没有数据时只返回 BOM。

导出沿用分类查询的字段、排序和错误行为，但单次取数上限放宽到 10000 行（分类列表接口的 100 行上限不适用于导出）。内容在内存一次性生成，当前没有 CSV 公式注入防护。

## 5. 日志采集与 SSE

### 5.1 `POST /api/v1/logs/{platform}`

路径参数 `platform` 只接受大小写不敏感的 `linux` 或 `windows`；其他值返回 HTTP 404：

```json
{"detail": "unsupported platform"}
```

请求体是必填项：完全没有请求体时 FastAPI 会先返回 422，只有带合法 JSON 体（例如 `[]`）调用未知平台时才得到 404。

请求体可以是单个 JSON 对象或对象数组；服务统一转成列表。数组元素不是 JSON 对象时不会让接口返回 500：该记录会被跳过持久化（响应仍为 `persisted:false`），其在实时事件中的 `category` 与 `eventTime` 为 `null`。

Linux 兼容示例：

```json
[
  {
    "client_id": "linux-001",
    "timestamp": "2026-10-10T08:00:00Z",
    "log": "sshd login accepted"
  }
]
```

Linux 归一化示例：

```json
[
  {
    "client_id": "linux-001",
    "normalized": {
      "event_time": "2026-10-10T08:00:00",
      "event_id": 1001,
      "category": "authentication_session",
      "type": "USER_AUTH",
      "hostname": "server-a",
      "result": "success"
    }
  }
]
```

Windows 旧字段兼容示例：

```json
[
  {
    "client_id": "windows-001",
    "TimeGenerated": "2026-10-10T08:00:00Z",
    "EventID": "4624",
    "RecordNumber": 88,
    "SourceName": "Microsoft-Windows-Security-Auditing",
    "ComputerName": "win-a",
    "Channel": "Security"
  }
]
```

成功或降级响应均为 HTTP 200，并按“解析”“入库”“存证”分别给出计数：

```json
{"accepted": 2, "persisted": true, "inserted": 2, "skipped": 0, "failed": 0, "hashed": 2, "errors": []}
```

```json
{"accepted": 2, "persisted": false, "inserted": 1, "skipped": 1, "failed": 0, "hashed": 1,
 "errors": [{"index": 1, "stage": "parse", "reason": "缺少可解析的时间戳（timestamp / event_time / syslog 行首）"}]}
```

语义说明：

- `accepted`：请求数组长度，不保证每条都已插入。
- `inserted`：真正写入数据库的行数。
- `skipped`：解析阶段被拒绝的记录数（`errors[].stage = "parse"`）。
- `failed`：入库阶段失败的记录数（`errors[].stage = "insert"`，`reason` 为数据库错误摘要）。
- `hashed`：成功写入 `log_hash.log_hash` 存证表的条数（含唯一键命中的幂等重复上报）。
- `persisted`：仅当 `skipped == 0` 且 `failed == 0` 时为 `true`。**存证失败或跳过不影响该字段**。
- 整批写入失败时会退化为逐行重试，因此 `errors[].index` 能定位到具体记录。
- 解析规则：Linux 支持 `normalized` 载荷、auditd 行（`type=... msg=audit(...)`，按类型映射到 7 张表）与 syslog 行；Windows 支持 Fluent Bit winlog JSON（`TimeGenerated`/`EventID`/`Channel`/`Data` 等）。时间统一转成 UTC。
- 数据库错误摘要最多保留 300 字符；没有幂等键或重试令牌。
- SSE 队列满时 `put()` 会等待，因而响应可能延迟。

#### 原始日志存证

在写入业务表之后、返回响应之前，接口会为每条记录写一条 `log_hash.log_hash` 存证行（见 `docs/DATABASE.md` §3.2）：

- 摘要定义：`sha256( timestamp原始字符串 + 0x1F + log原始字符串 )`，小写十六进制 64 位。
- 两个字段都取请求里的**原始值**，不 trim、不归一化、不转义；`log` 中的控制字符（如 auditd 的 `0x1D`）按原始字节参与哈希。
- `log_hash` 列有唯一索引：同一 `(timestamp, log)` 重复上报只留一条存证，按幂等成功计入 `hashed`。
- 记录缺少 `log` 字段或内容为空时**跳过存证**，追加一条 `errors[].stage = "hash"`，日志本身仍正常入库。
- 存证库写入失败时逐行定位并记入 `errors[].stage = "hash"`；**已入库的业务日志不会被回滚**。

### 5.2 `GET /api/v1/logs/stream`

返回 `text/event-stream`，无参数、鉴权或历史游标支持。浏览器可使用：

```javascript
const stream = new EventSource('/api/v1/logs/stream')
stream.addEventListener('log', event => console.log(JSON.parse(event.data)))
```

事件类型：

```text
event: connected
data: {"serverTime":"2026-10-10T08:00:00+00:00"}
```

```text
event: log
data: {"platform":"linux","category":"authentication_session","eventTime":"2026-10-10T08:00:00Z"}
```

```text
event: heartbeat
data: {"serverTime":"2026-10-10T08:00:20+00:00"}
```

- 建连立即发送 `connected`。
- 队列有数据时发送 `log`；摘要字段取值顺序为 `normalized` 子对象优先，其次顶层同名字段，其中 `category` 回退到 `channel`/`Channel`，`eventTime` 回退到顶层 `event_time`、`TimeGenerated`、`timestamp`。
- 20 秒无事件时发送 `heartbeat`。
- 除业务事件外，sse-starlette 每 15 秒还会发送一个注释帧 `: ping`（运行验证中可见），仅作底层保活，浏览器 `EventSource` 不会触发事件回调。
- 所有连接竞争同一个内存队列，并非每个订阅者都收到每条日志。
- 重启丢失队列；无事件 ID、补发或服务端显式断线恢复逻辑。
- 当前 Web 前端**不再订阅**该接口：每页 1 条 SSE + 1 条 WebSocket 会占满浏览器同源 6 条连接额度，前端改为 WebSocket 推送 + 定时 REST 刷新；接口保留给外部消费方，服务端仍按 `/health` 的 `realtime.sse_clients` 计数。

## 6. 心跳监控接口

### 6.1 `POST /api/v1/heartbeat`

请求体必须是 JSON 对象数组。当前没有 Pydantic 业务模型，代码从每项读取以下兼容字段：

| 标准字段 | 兼容字段 | 默认 |
|---|---|---|
| `client_id` | 无 | `UNKNOWN_HOST` |
| `hostname` | `server_name` | client_id |
| `ip` | `ip_address` | 空字符串 |
| `system` | `os_type` | `Unknown` |
| `latency` | 无 | `0` |

示例：

```json
[
  {
    "client_id": "sim-linux-001",
    "server_name": "sim-linux-001",
    "ip_address": "10.0.0.9",
    "os_type": "Linux",
    "latency": 3
  }
]
```

成功时 upsert MySQL、广播完整快照，并返回 HTTP 200 的 JSON 字符串：

```json
"pong"
```

错误：

- 请求体不是数组或不符合 `list[dict]`：通常 422。
- 数据库写入/类型转换失败：HTTP 503，`{"detail":"heartbeat persist failed"}`。

`heartbeat_interval_sec` 等额外字段会被忽略。

### 6.2 `GET /api/v1/monitor/summary`

无参数。状态以服务器当前 UTC 时间和最后心跳计算。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "total": 2,
    "states": {"ONLINE": 1, "DELAYED": 0, "OFFLINE": 1},
    "onlineRate": 50.0
  }
}
```

数据库不可用时没有降级，通常返回 500。

### 6.3 `GET /api/v1/monitor/clients`

查询参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `keyword` | string/null | 对完整客户端 JSON 做大小写不敏感的子串匹配 |
| `os_type` | string/null | 与存储的 `system` 做大小写敏感精确匹配 |

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [
      {
        "clientId": "sim-linux-001",
        "serverName": "server-a",
        "ipAddress": "10.0.0.9",
        "os_type": "Linux",
        "lastHeartbeatAt": "2026-10-10T08:00:00",
        "heartbeatDelaySec": 2,
        "heartbeatStatus": "ONLINE"
      }
    ],
    "total": 1
  }
}
```

当前接口不分页，也没有 heartbeat status 查询参数。

### 6.4 `GET /api/v1/monitor/clients/{client_id}`

返回单个客户端的内部字段风格：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "client_id": "sim-linux-001",
    "hostname": "server-a",
    "ip": "10.0.0.9",
    "system": "Linux",
    "latency": 3,
    "status": "ONLINE",
    "last_heartbeat": "2026-10-10T08:00:00",
    "heartbeatDelaySec": 2
  }
}
```

不存在时返回 HTTP 404：`{"detail":"client not found"}`。

### 6.5 `GET /api/v1/monitor/clients/{client_id}/logs`

查询参数 `limit` 默认 20，范围 1～100；无效值返回 422。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "clientId": "sim-linux-001",
    "items": []
  }
}
```

重要限制：当前日志表没有一致的 `client_id`，接口实际上返回全局 `/logs/recent` 结果，没有按路径中的客户端过滤；`client_id` 只在响应中回显。

### 6.6 `WebSocket /ws/client-monitor`

连接成功后服务立即发送完整快照 JSON，之后在每次成功心跳、以及后台巡检每 5 秒的轮次都会收到广播（无论状态是否变化）：

```json
{
  "total_clients": 2,
  "online_clients": 1,
  "delayed_clients": 0,
  "offline_clients": 1,
  "clients": [
    {
      "client_id": "sim-linux-001",
      "hostname": "server-a",
      "ip": "10.0.0.9",
      "system": "Linux",
      "latency": 3,
      "status": "ONLINE",
      "last_heartbeat": "2026-10-10T08:00:00",
      "heartbeatDelaySec": 2
    }
  ]
}
```

服务端随后等待客户端文本帧，仅用于检测断开；客户端可以不发送业务消息。没有应用级 ping 消息、订阅参数或鉴权，保活依赖每 5 秒的周期快照（页面以 20 秒无消息判定假死）。每次单连接发送等待上限 10 秒，失败连接会被移除。连接集合只存在于当前进程。

## 7. 存证与归档接口

> 2026-10-11 起，本节三条接口改为读取 `log_hash.log_hash`（原始日志 SHA-256 存证表）。
> 原先读取的 `linux_logs.audit_log_evidence` 表在真实库中并不存在，接口必然返回空数据；
> 旧的 `status` 过滤参数与 `verify_status` 字段一并移除。

### 7.1 `GET /api/v1/evidence/summary`

无参数。直接对存证表做 `COUNT(*)`：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "total": 128,
    "statuses": { "VALID": 128, "HASH_MISMATCH": 0, "RAW_MISSING": 0, "PENDING": 0 }
  }
}
```

- `total` 是存证表当前的真实行数（旧实现只统计「最近 1000 条」，不是全表总数）。
- `VALID` 等于 `total`：存证是入库前计算并校验写入的，不存在待校验状态；
  篡改检测需要按 §3.2 的 SQL 复算，接口暂不提供逐条校验。
- 查询失败时返回 **503** 并记录日志（**不再返回 `total: 0`**）：把数据库故障降级成
  「0 条存证」会让「库连不上」和「确实没有存证」无法区分，操作者会误以为数据被归档清空了。

### 7.2 `GET /api/v1/evidence`

查询参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `page` | integer | `1` | `max(1, page)` |
| `size` | integer | `30` | 1～100；越界返回 422 |
| `platform` | string/null | null | 精确匹配 `platform`（`linux`/`windows`） |
| `keyword` | string/null | null | 对 `raw_log`/`client_id`/`source`/`log_hash` 做 `LIKE` 模糊匹配 |

```json
{
  "code": 0,
  "message": "ok",
  "data": { "items": [], "page": 1, "size": 30, "total": 0, "pages": 0 }
}
```

- 现在由数据库侧 `LIMIT/OFFSET` 真分页（旧实现一次性取回全部再在 Python 切片）。
- `items` 每项字段固定为：`id`、`log_timestamp`、`platform`、`source`、`client_id`、
  `event_time`、`created_at`、`log_hash`；**不返回 `raw_log` 原文**，避免列表响应过大。
- 查询失败时返回 **503**（同 §7.1），不返回空列表冒充「没有数据」。

### 7.3 `GET /api/v1/evidence/export`

只读导出（**不删除**任何记录）；删除请走 §7.4～§7.6 的归档接口。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `platform` | string/null | null | 同 §7.2 |
| `keyword` | string/null | null | 同 §7.2 |
| `size` | integer | `10000` | 1～100000，单次导出上限 |

- HTTP 200，`Content-Type: text/csv`
- `Content-Disposition: attachment; filename="log-hash-evidence.csv"`
- UTF-8 BOM；列顺序 `id, log_hash, log_timestamp, platform, source, client_id, event_time, created_at`
  （**不含 `raw_log`**；需要原文请用归档接口导出的 CSV，它包含全部列）
- 查询失败时返回 **503**，不会返回一个看似正常的空 CSV

### 7.4 `GET /api/v1/archive/preview`

只统计、不导出、不删除，供前端二次确认。查询参数 `start`、`end`（`YYYY-MM-DD`，按 `created_at` 本地时间口径；
`end` 自动取当天 23:59:59）。日期格式错误返回 400。

**`start`/`end` 都不传时 `candidates` 等于全表条数**（2026-10-13 实测：`log_hash` 34099 行时为 34099），
因为这走的是「手动归档的空区间」语义，**不是**自动归档的候选集。自动归档候选按
`created_at < months_ago(retentionMonths)` 计算（同一时刻按 3 个日历月算是 0 条）；
真正的删除动作受 §7.5/§7.6 约束——`/archive/manual` 拒绝空区间、`/archive/auto` 只按该截止时间选取。
前端只在初始化时无参调用本接口读取 `retentionMonths`，归档预览一律带 `start`/`end`。

```json
{
  "code": 0, "message": "ok",
  "data": {
    "mode": "manual", "candidates": 42, "exported": 0, "deleted": 0,
    "file": null, "fileSha256": null, "dryRun": true,
    "retentionMonths": 3, "archiveDir": "data/archive"
  }
}
```

### 7.5 `POST /api/v1/archive/manual`

手动归档：把指定区间的存证导出为 CSV，**确认文件落盘后**再从数据库删除。

请求体：

```json
{"start": "2026-07-01", "end": "2026-07-31", "confirm": true}
```

- `confirm` 必须为 `true`，否则返回 400（防误触）。
- `start`/`end` 至少给一个；`start > end` 返回 400。
- 成功响应：

```json
{
  "code": 0, "message": "ok",
  "data": {
    "mode": "manual", "candidates": 42, "exported": 42, "deleted": 42,
    "file": "data/archive/log_hash_manual_20261011_141500.csv",
    "fileSha256": "…64 位小写十六进制…", "dryRun": false
  }
}
```

- 失败时返回 **500**，且**不会删除任何记录**（响应 detail 说明失败原因）。
- 导出文件永久保留在 `archive_dir`，不做自动清理。

### 7.6 `POST /api/v1/archive/auto`

立即执行一次自动归档的逻辑（与后台每日任务相同）：导出并删除
`created_at < 当前时间 - archive_retention_months 个日历月` 的全部存证。

- `archive_retention_months <= 0` 时返回 `{"enabled": false}`，不做任何事。
- 成功响应的 `data` 在 §7.5 基础上多 `enabled` 与 `cutoff` 两个字段。

### 7.7 `GET /api/v1/archive/log`

归档台账，分页参数 `page`（默认 1）、`size`（默认 30，1～100）。字段：
`id`、`mode`（`auto`/`manual`）、`file_path`、`row_count`、`deleted_count`、
`file_sha256`、`range_start`、`range_end`、`before_time`、`created_at`。

由于删除不可逆，台账是事后追溯「哪些记录在什么时候被导出到哪里」的唯一依据。

#### 归档安全流程

1. 按条件只查 **id 列表**（`archive_batch_limit` 限制单次行数）；
2. 按 id 分批取完整行并流式写 CSV；
3. `flush` + `fsync` 落盘，校验文件存在、大小 > 0、行数与 id 数一致；
4. **任一步失败立即中止，绝不执行 DELETE**；
5. 用**明确的 id 列表**删除（不是 `DELETE WHERE created_at < X`），
   避免归档期间新写入、同样满足条件的记录被误删；
6. 删除成功后写一条 `archive_log` 台账；台账写入失败不影响已完成的归档。

## 8. 当前未实现的旧契约接口

以下能力可在遗留文档中找到，但当前 FastAPI 路由不存在：

- 心跳趋势接口。
- 日志 overview、单条详情、异步导出任务及任务查询。
- 存证详情、单条校验、当前页批量校验。
- 完整的原始日志写入、SHA-256 生成和原文读取。

调用这些未实现路径通常得到 HTTP 404。后续实现时必须以新代码和本文件为准，不得直接把遗留示例视为现有兼容承诺。
