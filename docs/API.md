# API 文档

> 基线日期：2026-10-10。接口以 `backend/app/main.py` 当前路由为准。旧前端契约中但未出现在本文件的接口，均不视为已实现。

## 1. 通用约定

- 默认开发地址：`http://127.0.0.1:8080`。
- 当前没有认证或授权，请求不需要业务自定义 Header。
- JSON 请求应发送 `Content-Type: application/json`。
- JSON 请求体和响应体统一使用 UTF-8（请求体无 BOM），不得发送 GBK/ANSI 编码的 JSON 字节；JSON 的 `\uXXXX` Unicode 转义允许使用。前端通过 `Response.json()` 解码响应后展示 Unicode 文本，不做本地代码页转码。CSV 使用 UTF-8，导出可带 BOM。
- 多数查询接口成功响应为 `{ "code": 0, "message": "ok", "data": ... }`，但健康检查、采集写入、心跳、HTML、WebSocket 和 CSV 不使用该包装。
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
| GET | `/partials/db-status` | HTML | 侧栏「服务连接」卡片的数据库片段（HTMX 30 秒） |
| GET | `/health` | JSON | 四库连接健康检查 |
| GET | `/api/v1/logs/categories` | JSON | 日志类别 |
| GET | `/api/v1/logs/summary` | JSON | 日志总量与分类统计 |
| GET | `/api/v1/logs/categories/{category}` | JSON | 分类分页查询 |
| GET | `/api/v1/logs/recent` | JSON | 跨平台最近日志 |
| GET | `/api/v1/logs/trend` | JSON | 时间桶接入趋势（UTC 桶键，服务端 TTL 缓存） |
| GET | `/api/v1/logs/stats` | JSON | 窗口内 TOP 类别 / TOP 主机排行（服务端缓存） |
| POST | `/api/v1/logs/{platform}` | JSON | Linux/Windows 日志采集 |
| GET | `/api/v1/logs/export` | CSV | 分类日志导出 |
| GET | `/api/v1/monitor/summary` | JSON | 心跳状态摘要 |
| GET | `/api/v1/monitor/clients` | JSON | 心跳客户端列表 |
| GET | `/api/v1/monitor/clients/{client_id}` | JSON | 客户端详情 |
| GET | `/api/v1/monitor/clients/{client_id}/logs` | JSON | 客户端最近日志兼容接口 |
| POST | `/api/v1/heartbeat` | JSON | 心跳上报 |
| GET | `/api/v1/monitor/history` | JSON | 心跳历史序列（进程内环形缓冲，不查库） |
| WebSocket | `/ws/client-monitor` | WS | 客户端状态快照广播 |
| GET | `/api/v1/evidence/summary` | JSON | 存证状态摘要 |
| GET | `/api/v1/evidence` | JSON | 存证列表（分页） |
| GET | `/api/v1/evidence/export` | CSV | 存证导出（只读，不删除） |
| GET | `/api/v1/archive/preview` | JSON | 归档预览：只统计待归档条数 |
| POST | `/api/v1/archive/manual` | JSON | 手动归档：导出区间存证后删除 |
| POST | `/api/v1/archive/auto` | JSON | 立即执行一次自动归档 |
| GET | `/api/v1/archive/log` | JSON | 归档台账列表 |
| GET | `/api/v1/security/summary` | JSON | 三类安全日志实际入库数、断点和采集开关 |
| GET | `/api/v1/security/logs/{log_type}` | JSON | 安全日志 ID 游标分页 |
| GET | `/api/v1/security/logs/{log_type}/{record_id}` | JSON | 完整原始日志 JSON |
| POST | `/api/v1/admin/update` | JSON | 触发服务端代码更新（停服 → 原地 git 更新 → 重启） |

## 3. 页面与健康接口

### 3.1 `GET /`

返回 `text/html` 的仪表盘。无参数、请求体或必需 Header。

处理时会尝试统计日志库；统计失败时仍返回 HTTP 200，并把页面初始汇总降级为 0。浏览器随后还会从 API 刷新数据。

### 3.2 `GET /partials/db-status`

返回侧栏「服务连接」卡片的**数据库部分**的 HTML 片段（HTMX 每 30 秒替换一次）。无参数。

片段由「汇总行 + 四个库各一行」组成，每行是状态点 + 中文名 + 状态文字；真实库名放在该行的 `title` 上（侧栏只有 `col-lg-2` 宽，把 `linux_logs` 这类等宽文本塞进每一行会把中文标签挤成省略号）：

```html
<div class="conn-row"><span class="conn-dot ok"></span><span class="conn-name">数据库</span><span class="conn-meta">4/4 个库</span><span class="conn-state ok">正常</span></div>
<div class="conn-row conn-sub" title="Linux 日志库（linux_logs）"><span class="conn-dot ok"></span><span class="conn-name">Linux 日志库</span><span class="conn-state ok">已连接</span></div>
<!-- windows_logs / log_fusion / log_hash 同构 -->
```

- 汇总行的数量取自**本次真实探测**：`N/4 个库`，状态文字为 `正常` / `部分可用` / `全部不可用`（对应 ok / warn / bad 三种状态点）。
- 单个库不可用时对应行显示 `不可用`（红色状态点），**不影响其他行的结果**，也不返回 5xx。
- 卡片里「实时通道」那一行不在本片段里：它由浏览器自己的 WebSocket 状态渲染（Alpine），因为只有页面自己知道这条连接是否活着。
- 2026-10-13 之前该片段返回的是一串 Bootstrap badge（`linux_logs: 已连接`），随着侧栏改版一并调整；这是**展示层片段**，不构成对外接口契约。

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
    "ws_clients": 1
  }
}
```

当前实现中即使全部数据库不可用也返回 HTTP 200。

`realtime.ws_clients` 是当前打开的 `/ws/client-monitor` 连接数，用于排查“页面卡住但接口很快”的情况：浏览器对同一地址只允许 6 条 HTTP/1.1 连接，长连接不释放时后续 REST 请求会一直排队；页面每个标签页占用 1 条 WebSocket（不占 HTTP 连接额度）。WebSocket 连接/断开、状态阈值变化都会写日志。SSE 计数已于 2026-10-13 随该通道一起移除（ADR-015）。

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
| `full` | boolean | `false` | `true` 时返回整行真实字段（Linux 取 `LINUX_TABLE_FIELDS`，Windows 取 `WINDOWS_TABLE_FIELDS` + `id`/`imported_at`，含 `eventdata`），供分类日志视图的「原始详情」使用；默认只返回列表展示列 |

响应里的 `columns` 是**表格应展示的列**（Linux 取 `LINUX_TABLE_COLUMNS`，Windows 取 `WINDOWS_VIEW_COLUMNS`）。
`items` 在 `full=true` 时包含 `columns` 之外的字段——这些字段只给原始详情弹窗用，不要拿去渲染表格。

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
    "platform": "Linux",
    "columns": ["event_time", "event_id", "category", "type", "operation", "account", "executable", "hostname", "source_address", "result", "tips"]
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

### 4.6 `GET /api/v1/logs/trend`

时间桶接入趋势，为概览与日志统计页的图表供数（2026-10-13 新增，方案见 `PLAN-frontend-charts.md`）。

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `range` | string | `24h` | `1h` / `24h` / `7d` / `30d`，其他值返回 400 |
| `bucket` | string/null | 随 range 定（1h→minute，24h→hour，7d/30d→day） | `minute` / `hour` / `day`；组合出的点数 > 2000 时返回 400（如 `range=30d&bucket=minute`） |
| `platform` | string/null | null=全部 | 大小写不敏感的 `linux`/`windows`；其他值 400 |

```json
{
  "code": 0, "message": "ok",
  "data": {
    "bucket": "hour", "range": "24h",
    "generatedAt": "2026-10-13T06:00:00Z", "cached": true, "ageMs": 4200,
    "points": [
      {"bucket": "2026-10-13T05:00:00Z", "linux": 120, "windows": 300, "total": 420}
    ]
  }
}
```

- **桶键一律 UTC（`...Z` 结尾）**：DB 内即 UTC naive datetime，本接口不做时区转换；本地时间由前端渲染时转换，否则趋势图/热力图会偏 8 小时。
- 空桶补 0，`points` 按时间升序且无重复；`linux`/`windows` 在 `platform` 过滤后恒为 0。
- **服务端 TTL 缓存**：`1h` 5 秒、`24h` 15 秒、`7d`/`30d` 60 秒；`generatedAt`/`cached`/`ageMs` 用于观测数据新鲜度。
- 聚合按表 UNION ALL（Linux 7 表用 `event_time`，Windows 5 表用 `time_created`，两库分别查询后在 Python 合并）。
- 查询失败返回 **500**（与 `/api/v1/logs/summary` 一致），不会降级成「全 0 趋势」。

### 4.7 `GET /api/v1/logs/stats`

窗口内 TOP 日志类别与 TOP 主机排行，为日志统计页的条形图供数（2026-10-13 新增）。

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `range` | string | `7d` | `24h` / `7d` / `30d`，其他值 400 |
| `limit` | integer | `10` | 1～50，越界返回 422 |
| `platform` | string/null | null=全部 | 同 §4.6 |

```json
{
  "code": 0, "message": "ok",
  "data": {
    "range": "7d", "generatedAt": "…Z", "cached": false, "ageMs": 0,
    "topCategories": [{"key": "authentication_session", "label": "认证会话", "platform": "Linux", "count": 8123}],
    "topHosts": [{"host": "server-a", "count": 1502}]
  }
}
```

- 口径是**窗口内**计数（`event_time`/`time_created >= now - range`），与 `/logs/summary` 的全表口径不同，二者不可混用。
- `topHosts` 只查实际拥有主机列的表：Linux 三张带 `hostname` 的表（`DATABASE.md` §4.3）+ Windows 五张 `computer` 表，NULL/空值主机被排除，两平台同名主机合并计数。
- TTL 缓存 60 秒；查询失败返回 500。

## 5. 日志采集

> **实时通道统一走 WebSocket**：原先的日志 SSE 接口 `GET /api/v1/logs/stream` 已于 2026-10-13 整体移除
> （原因见 ADR-015）。现在唯一的实时通道是 `/ws/client-monitor`，消息格式与扩展约定见 §6 与 ADR-015。
> 采集接口不再向任何通道推送逐条事件；页面靠定时 REST 刷新获取统计与列表。

### 5.1 `POST /api/v1/logs/{platform}`

控制台诊断：解析、入库和存证错误会逐条写入 `log_fusion` 的 ERROR 日志，包含 `platform`、`index`、`client_id`、`stage`、`table` 与原因，并打印批次失败计数。请求体校验/解码等接收失败也打印路径、来源地址、HTTP 状态码及错误原因（不输出完整请求体）。HTTP 响应中的错误样例被截断时，控制台仍保留全部处理错误。

路径参数 `platform` 只接受大小写不敏感的 `linux` 或 `windows`；其他值返回 HTTP 404：

```json
{"detail": "unsupported platform"}
```

请求体是必填项：完全没有请求体时 FastAPI 会先返回 422，只有带合法 JSON 体（例如 `[]`）调用未知平台时才得到 404。

请求体可以是单个 JSON 对象或对象数组；服务统一转成列表。数组元素不是 JSON 对象时不会让接口返回 500：该记录会被跳过持久化，响应仍为 `persisted:false` 并计入 `skipped`（`errors[].stage = "parse"`）。

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

> 注意这种载荷**没有 `log` 字段**：它会被整条规范化为 JSON 后作为存证原文（见 §5「原始日志存证」）。
> 2026-10-15 之前 Windows 记录因缺 `log` 被逐条跳过存证，是「日志总量远大于存证总数」的主因。

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
- `errors`：错误样例。为兼容 Fluent Bit 1.8 的 4 KiB HTTP 响应缓冲区，接口最多返回 8 条样例，单条 `reason` 最多 160 个字符。
- `error_count`：本批次完整错误总数；仅在存在错误时返回。
- `errors_truncated`：错误样例是否被截断；为 `true` 时，完整错误明细只写入服务端日志。
- `hashed`：成功写入 `log_hash.log_hash` 存证表的条数（含唯一键命中的幂等重复上报）。
- `persisted`：仅当 `skipped == 0` 且 `failed == 0` 时为 `true`。**存证失败或跳过不影响该字段**。
- 整批写入失败时会退化为逐行重试，因此 `errors[].index` 能定位到具体记录。
- 解析规则：Linux 支持 `normalized` 载荷、auditd 行（`type=... msg=audit(...)`，按类型映射到 7 张表）与 syslog 行；Windows 支持 Fluent Bit winlog JSON（`TimeGenerated`/`EventID`/`Channel`/`Data` 等）。时间统一转成 UTC。
- 数据库错误摘要最多保留 300 字符；没有幂等键或重试令牌。

#### 原始日志存证

在写入业务表之后、返回响应之前，接口会为每条记录写一条 `log_hash.log_hash` 存证行（见 `docs/DATABASE.md` §3.2）：

- 摘要定义：`sha256( 原始时间戳字符串 + 0x1F + 存证原文 )`，小写十六进制 64 位。
- **存证原文按平台取不同字段**：Linux 用请求里的 `log` 字段；Windows 的 winlog 载荷没有
  `log`，改用**整条记录的规范化 JSON**（键排序、紧凑分隔符、非 ASCII 不转义）。
- 时间戳取值：Linux 用 `timestamp`；Windows 按 `TimeGenerated` → `TimeWritten` → `event_time`
  → `timestamp` → `date` 取第一个非空值（与 `normalize_windows` 同序）。
- 参与哈希的都是**未经业务加工**的取值，不 trim、不做大小写转换；Linux `log` 中的控制字符
  （如 auditd 的 `0x1D`）按原始字节参与哈希。Windows 侧的规范化序列化是一次已知加工，
  因此其存证语义与 Linux 不完全等价，详见 `docs/DATABASE.md` §3.2 的边界说明。
- `log_hash` 列有唯一索引：同一 `(时间戳, 原文)` 重复上报只留一条存证，按幂等成功计入 `hashed`。
- 存证原文为空时（Linux 缺 `log`、Windows 为空对象）**跳过存证**，追加一条
  `errors[].stage = "hash"`，日志本身仍正常入库。
- 存证库写入失败时逐行定位并记入 `errors[].stage = "hash"`；**已入库的业务日志不会被回滚**。

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

### 6.7 `GET /api/v1/monitor/history`

心跳历史序列，为心跳页的延迟趋势等图表供数（2026-10-13 新增）。

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `minutes` | integer | `15` | 1～60，越界返回 422 |

```json
{
  "code": 0, "message": "ok",
  "data": {
    "intervalSec": 5, "windowMinutes": 15, "samples": 3,
    "points": [
      {"t": "2026-10-13T06:01:00Z", "total": 2, "online": 1, "delayed": 0,
       "offline": 1, "avgDelay": 19.5}
    ],
    "clients": [
      {"clientId": "sim-linux-001", "status": "ONLINE",
       "points": [["2026-10-13T06:01:00Z", 4]]}
    ]
  }
}
```

- **数据源是进程内环形缓冲**（`backend/app/metrics.py`，watchdog 每 5 秒采样，容量 720 点 ≈ 1 小时），**不查库**：数据库故障时该接口依然可用。
- **进程重启后历史清零**：返回空序列（`points: []`）而不是错误，页面据此显示空态——「刚重启没有历史」与「查询失败」必须可区分。
- 序列时间 `t` 为 UTC（`...Z`），前端渲染时转本地时区；`avgDelay` 是该轮采样所有客户端 `heartbeatDelaySec` 的平均值（秒）。
- `clients[].points` 只含窗口内出现过的客户端，`[时间, 延迟秒]` 成对，按时间升序。

## 7. 存证与归档接口

> 2026-10-11 起，本节三条接口改为读取 `log_hash.log_hash`（原始日志 SHA-256 存证表）。
> 原先读取的 `linux_logs.audit_log_evidence` 表在真实库中并不存在，接口必然返回空数据；
> 旧的 `status` 过滤参数与 `verify_status` 字段一并移除。

### 7.1 `GET /api/v1/evidence/summary`

无参数。**逐条复算校验**：对每行按 `sha256(IFNULL(log_timestamp,'') + 0x1F + raw_log)`
重新计算并与 `log_hash` 比较，一次全表扫描给出四类计数。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "total": 128,
    "statuses": { "VALID": 125, "HASH_MISMATCH": 2, "RAW_MISSING": 1 },
    "healthy": false,
    "source": { "host": "192.168.5.6", "database": "log_hash", "table": "log_hash" }
  }
}
```

- `total` 是存证表当前真实行数，等于三个状态之和。
- `statuses`：
  - `VALID` —— 复算结果与存证摘要一致；
  - `HASH_MISMATCH` —— **不一致，说明原文 / 时间戳 / 摘要被改动**；
  - `RAW_MISSING` —— 原文为空，无从复算。
  - 早先定义的 `PENDING`（缺时间戳无法校验）**已移除**：写入端把缺失时间戳按**空串**
    参与哈希（`raw_text(None) == ""`），因此这类记录同样可以复算；把它当成待校验会对
    合法存证误报。
- `healthy`：`HASH_MISMATCH == 0 且 RAW_MISSING == 0`，供页面直接判断整体是否正常。
- `source`：实际读取的主机 / 库 / 表，用于排除「页面和 Navicat 看的不是同一个库」。
- **校验规则必须与写入端完全一致**（分隔符 `0x1F`、NULL 时间戳按空串、大小写敏感）；
  任一端改动都会让全部存证被判成篡改，或让真正的篡改被判成正常。
- 查询失败时返回 **503** 并记录日志（**不再返回 `total: 0`**）：把数据库故障降级成
  「0 条存证」会让「库连不上」和「确实没有存证」无法区分，操作者会误以为数据被清空了。
- 性能：当前实现对**整表**复算 SHA-256，万级行数约 0.2 秒。百万级时应改为增量校验
  或异步任务（见 `docs/TODO.md`）。

### 7.2 `GET /api/v1/evidence`

查询参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `page` | integer | `1` | `max(1, page)` |
| `size` | integer | `30` | 1～100；越界返回 422 |
| `platform` | string/null | null | 精确匹配 `platform`（`linux`/`windows`） |
| `keyword` | string/null | null | 对 `raw_log`/`client_id`/`source`/`log_hash` 做 `LIKE` 模糊匹配 |
| `verify` | string/null | null | `VALID`/`HASH_MISMATCH`/`RAW_MISSING`，只看某一类；其他值返回 400 |
| `start` | string/null | null | `YYYY-MM-DD`，按入库时间 `created_at` 过滤，含当天 00:00:00 |
| `end` | string/null | null | `YYYY-MM-DD`，按入库时间 `created_at` 过滤，含当天 23:59:59 |

`start`/`end` 用**入库时间**而不是 `event_time`：采集端补传的历史日志其事件时间可能很早，
按入库时间才能回答「这段时间系统实际存了什么」。日期格式错误返回 400（`日期格式应为 YYYY-MM-DD`）。

每行额外返回 `verify_status`（取值同上），用来直接筛出被改动的记录。

```json
{
  "code": 0,
  "message": "ok",
  "data": { "items": [], "page": 1, "size": 30, "total": 0, "pages": 0 }
}
```

- 现在由数据库侧 `LIMIT/OFFSET` 真分页（旧实现一次性取回全部再在 Python 切片）。
- `items` 每项字段固定为：`id`、`log_timestamp`、`platform`、`source`、`client_id`、
  `event_time`、`created_at`、`log_hash`、`verify_status`；**不返回 `raw_log` 原文**，
  避免列表响应过大（`verify_status` 由 SQL 复算得出，不需要把原文传给客户端）。
- 查询失败时返回 **503**（同 §7.1），不返回空列表冒充「没有数据」。

### 7.3 `GET /api/v1/evidence/export`

只读导出（**不删除**任何记录）；删除请走 §7.4～§7.6 的归档接口。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `platform` | string/null | null | 同 §7.2 |
| `keyword` | string/null | null | 同 §7.2 |
| `verify` | string/null | null | 同 §7.2，用来只导出异常行 |
| `start` | string/null | null | 同 §7.2（入库时间范围） |
| `end` | string/null | null | 同 §7.2（入库时间范围） |
| `size` | integer | `10000` | 1～100000，单次导出上限 |

- HTTP 200，`Content-Type: text/csv`
- `Content-Disposition: attachment; filename="log-hash-evidence.csv"`
- UTF-8 BOM；列顺序 `id, log_timestamp, platform, source, client_id, event_time, created_at,
  log_hash, verify_status`（**不含 `raw_log`**；需要原文请用归档接口导出的 CSV，它包含全部列）
- 带 `verify_status` 便于在 Excel 里直接筛出被改动的记录
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

## 8. 服务端维护接口

### 8.1 `POST /api/v1/admin/update`

触发一次服务端代码更新：从 GitHub 拉取最新代码，停止服务、替换部署目录后重新启动。完整流程与部署前提见 `docs/DEPLOY.md`。

请求体（必填）：

```json
{"confirm": true}
```

- `confirm` 必须为 `true`，否则返回 **400**（避免误触）。
- 该接口**不连接数据库**，也没有鉴权：任何能访问本服务的人都能触发一次停服重部署。仅适用于受控内网/测试环境。

处理是**异步**的：接口把 `deploy/update-log-fusion.sh` 复制到仓库外的固定路径
（`/usr/local/bin/log-fusion-update.sh`），再用 `systemd-run` 起一个**独立**的 transient 单元
`log-fusion-update` 后立即返回；真正的停服 / 克隆 / 重启由该单元执行。之所以用独立单元而不是直接
`setsid` 后台运行，是因为 systemd 停止 `log-fusion` 时会清理它整条 cgroup，普通后台进程会被一起杀掉，
导致更新半途中断（见 `docs/DEPLOY.md`）。

成功响应：

```json
{"code": 0, "message": "更新已启动",
 "data": {"unit": "log-fusion-update", "log": "journalctl -u log-fusion-update -f",
          "note": "服务将在数秒内重启，期间页面会短暂不可用"}}
```

错误：

| 场景 | HTTP | detail |
|---|---|---|
| 缺少 `confirm` | 400 | 需要 confirm=true 才会执行更新 |
| 服务器没有 `systemd-run` | 500 | 服务器未安装 systemd-run，无法执行更新（见 docs/DEPLOY.md） |
| 仓库内找不到更新脚本 | 500 | 更新脚本不存在：… |
| 已有更新在进行 | 409 | 已有一次更新正在进行，请稍后再试 |
| 复制脚本 / 启动失败 | 500 | 准备更新脚本失败 / 启动更新失败：… |

状态查询：`systemctl status log-fusion-update`；日志：`journalctl -u log-fusion-update -f`。
升级完成后该 transient 单元会退出（`--collect` 会回收失败单元的残留）。

## 9. 当前未实现的旧契约接口

以下能力可在遗留文档中找到，但当前 FastAPI 路由不存在：

- 心跳趋势接口。
- 日志 overview、单条详情、异步导出任务及任务查询。
- 存证详情、单条校验、当前页批量校验。
- 完整的原始日志写入、SHA-256 生成和原文读取。

调用这些未实现路径通常得到 HTTP 404。后续实现时必须以新代码和本文件为准，不得直接把遗留示例视为现有兼容承诺。

## 安全产品日志查询（2026-10-16）

三个接口均只读，成功使用 `{code:0,message:"ok",data:...}`。数据库/表不可用返回 503，不能当作空结果。允许的 `log_type` 只有 `ipslog`、`ipblacklistlog`、`ips_web_secure_event_log`，其余返回 422，不能用于探测任意表。

- `GET /api/v1/security/summary`：无参数；`data` 包含 `sourceInstance`、`enabled`、`collectorActive`（数据库会话锁是否被工作进程持有）、`pollSeconds`、`overlapSeconds`、`lagSeconds`、`collectionMode:"live_only"`、`liveWindowSeconds`、`generatedAt` 和 `types`。每类含 type/label/table/initialized/rows、history/live 状态、pending 时间范围、checked_until Unix 秒、error/retry_at/last_success。`rows` 由窗口事务维护，代表本实例已写入唯一记录数；不是上游总量。历史 `expected` 仅是最初整段查询的参考值，可为 null。手工修改表后需要单独核对计数；不要把故障和未初始化显示成真实零行。
- `GET /api/v1/security/logs/{log_type}`：`limit` 默认 30、1..100；可选 `before` 为正整数，按 `id < before ORDER BY id DESC` 返回。`data={type,order:"id_desc",items:[...],nextBefore}`。items 含 id/event_time（UTC ISO Z）/sip/dip/sport/dport/ruleid/threat_type/log_id，另含 event/protocol/severity/action/acted/count/dev_ip；黑名单类型额外含 domain/ip/engine，WEB 类型额外含 url。以上扩展字段可为 null，severity/action/acted 保留上游原值，不映射等级/动作含义；0 是有效值。列表不含完整 raw_record/rawinfo。nextBefore=null 表示当前无下一页。补录期间顺序是入库顺序，不能当作事件时间排序。无 OFFSET 深分页或每页全表 COUNT。
- `GET /api/v1/security/logs/{log_type}/{record_id}`：正整数记录 ID；返回原始 `{index,source}`。不存在返回 404，非正 ID 返回 422。原始内容仅作为文本渲染。

WebSocket 仍为 `/ws/client-monitor`，新增通知 `{type:"security_logs",logType:"ipslog",mode:"history",rows:123,status:"running"}`（错误通知可只含 logType/status）。它是刷新提示，不包含逐条日志。旧快照无 type 或 type=snapshot 时继续更新心跳；未知类型忽略。CLI 导入不广播，页面 10 秒轮询会显示变化。

安全摘要状态补充（仅实时策略）：后台不再继续历史任务，旧未完成 history.status 为 stopped（保留 pending 供显式手动使用，不表示仍会执行），新实例不建历史任务、状态为 disabled；已完成历史仍为 complete。每次启动重设 live.since 和 checked_until 到近期起点，清空旧 live.pending；collection_mode=live_only。接口路径和既有字段保留，只增加 collectionMode/liveWindowSeconds 及上述状态含义，无新增路由。
