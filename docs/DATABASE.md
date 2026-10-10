# 数据库设计与代码依赖

> 基线日期：2026-10-10（同日按当前工作区代码复核）。本文件严格区分“仓库中有正式模型的结构”和“只能从 SQL 推导的代码依赖字段”。本次文档工作没有连接真实 MySQL，也没有执行 DDL/DML；既有日志表的真实类型、约束和索引均待只读核验。

## 1. 数据库划分

当前应用连接同一 MySQL 服务上的三个逻辑库，并为每个库创建独立同步 SQLAlchemy Engine：

| 配置项 | 默认库 | Engine | 用途 |
|---|---|---|---|
| `LINUX_DB` | `linux_logs` | `linux_engine` | 7 类 Linux 审计表；当前代码还从这里读取存证表 |
| `WINDOWS_DB` | `windows_logs` | `windows_engine` | 5 类 Windows 事件表 |
| `LOG_FUSION_DB` | `log_fusion` | `monitor_engine` | 平台心跳状态 `client_status` |

所有连接 URL 使用 `mysql+pymysql` 和 `charset=utf8mb4`，开启 `pool_pre_ping=True` 与 `pool_recycle=1800`。三个库之间没有跨库事务。

## 2. 连接配置

`backend/app/config.py` 使用 pydantic-settings，启动目录下的 `.env` 和进程环境变量可覆盖默认值：

| 环境变量 | 类型 | 默认/说明 |
|---|---|---|
| `DB_HOST` | string | 默认指向开发环境 MySQL 主机 |
| `DB_PORT` | integer | `3306` |
| `DB_USER` | string | 默认 `root`；生产不应使用 |
| `DB_PASSWORD` | string | 代码中存在默认密码，本文件不重复记录；应由环境覆盖并轮换 |
| `LINUX_DB` | string | `linux_logs` |
| `WINDOWS_DB` | string | `windows_logs` |
| `LOG_FUSION_DB` | string | `log_fusion` |
| `RAW_STORAGE_DIR` | string | `data/raw`，当前未参与数据库或文件写入 |

标准启动脚本先切换到 `backend/`，所以默认 `.env` 位置应为 `backend/.env`。仓库当前没有 `.env` 示例文件。

健康检查对每个 Engine 执行 `SELECT 1`，失败只返回 `false`，不暴露异常详情。

## 3. 由 ORM 正式声明的表

### 3.1 `log_fusion.client_status`

应用启动时执行 `Base.metadata.create_all(monitor_engine)`，当前 Base 只包含这张表。数据库本身必须预先存在；创建失败只记录日志，应用仍继续启动。2026-10-10 只读核验：`log_fusion` 库中确实只有 `client_status` 一张表，实际列与上表一致。

| 字段 | SQLAlchemy 声明 | 空值 | 约束/索引 | 用途 |
|---|---|---|---|---|
| `id` | `BIGINT` | 否 | 主键、自增 | 内部记录 ID |
| `client_id` | `VARCHAR(128)` | 否 | 唯一、索引 | 客户端稳定标识，upsert 冲突键 |
| `hostname` | `VARCHAR(255)` | 否 | 代码默认空字符串 | 主机名；兼容 `hostname`/`server_name` |
| `ip` | `VARCHAR(64)` | 否 | 代码默认空字符串 | IP；兼容 `ip`/`ip_address` |
| `system` | `VARCHAR(64)` | 否 | 代码默认空字符串 | 系统类型；兼容 `system`/`os_type` |
| `last_heartbeat` | `DATETIME` | 否 | 无显式索引 | 服务接收心跳时的 UTC 无时区时间 |
| `latency` | `BIGINT` | 否 | 代码默认 `0` | 客户端上传的延迟值；未上传为 0 |
| `status` | `VARCHAR(16)` | 否 | 普通索引；代码默认 `OFFLINE` | `ONLINE`/`DELAYED`/`OFFLINE` |
| `created_at` | `DATETIME` | 否 | 服务端默认 `NOW()` | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务端默认 `NOW()`；ORM 更新时使用 `NOW()` | 最近更新 |

索引实际名称、MySQL 精确 DDL、时区设置和表字符集由 SQLAlchemy 方言及真实数据库决定，尚未在本次静态审计中核验。

#### 写入方式

心跳使用 MySQL `INSERT ... ON DUPLICATE KEY UPDATE`：

- 新 `client_id` 插入一行。
- 已有 `client_id` 更新主机名、IP、系统、最后心跳、延迟、状态和 `updated_at`。
- 请求中的多条心跳在一个 Session 事务中提交；任一异常导致接口返回 503。
- `client_id` 缺失时使用 `UNKNOWN_HOST`，因此多个缺失标识的客户端会覆盖同一行。
- **这张表不是日志表**：心跳只写 `log_fusion.client_status`，`/api/v1/logs/*`（汇总、列表、搜索、导出、`/recent`）只读 `linux_logs`/`windows_logs`，因此心跳记录**不计入**接口返回的日志总量。2026-10-10 实测：`client_status` 6 行 / 6 个客户端，同一时刻 `/api/v1/logs/summary` 的 `total` 为 425812（= Linux 160552 + Windows 265260），两者互不包含。
- **`AUTO_INCREMENT` 会跳号**：`ON DUPLICATE KEY UPDATE` 同样消耗自增值（先分配 id，撞唯一键再改写），所以 `AUTO_INCREMENT=1857` 只代表累计写过约 1856 次心跳，不代表有 1856 行——行数始终等于去重后的 `client_id` 数量。

watchdog 每 5 秒读取全表并在状态变化时提交。API 响应还会根据 `last_heartbeat` 动态重算状态，数据库 `status` 不是唯一计算来源。

## 4. `linux_logs` 代码依赖

仓库没有这些表的 ORM 模型、建表 SQL或迁移。下列内容只表示当前代码会读取/写入哪些字段，不等同于正式 DDL。

### 4.1 分类表与列表查询字段

| 表名 | 当前分类列表 SELECT 字段 |
|---|---|
| `authentication_session` | `event_time`, `event_id`, `category`, `type`, `operation`, `account`, `executable`, `hostname`, `source_address`, `result`, `tips` |
| `account_security_change` | `event_time`, `event_id`, `category`, `type`, `operation`, `executable`, `hostname`, `source_address`, `result`, `tips` |
| `process_command_execution` | `event_time`, `event_id`, `category`, `type`, `executable`, `command`, `result`, `tips` |
| `file_object_access` | `event_time`, `event_id`, `category`, `type`, `executable`, `command`, `object_path`, `action`, `result`, `tips` |
| `network_ipc_communication` | `event_time`, `event_id`, `category`, `type`, `executable`, `command`, `action`, `source_address`, `destination_address`, `source_port`, `destination_port`, `protocol`, `result`, `tips` |
| `system_service_audit_lifecycle` | `event_time`, `event_id`, `category`, `type`, `action`, `target`, `executable`, `hostname`, `result`, `tips` |
| `security_policy_config_change` | `event_time`, `event_id`, `category`, `type`, `action`, `target`, `executable`, `result`, `tips` |

分类列表用第一个字段倒序，即通常按 `event_time DESC`。汇总对每张表执行 `COUNT(*)`。分类列表与 CSV 导出共用该查询路径，但单次取数上限不同：列表接口 100 行，导出最多 10000 行（见 `API.md` §4.5）。

### 4.2 写入字段的来源与裁剪

采集写入的行**不再使用一份全局字段白名单**，而是由 `backend/app/parsers.py` 解析后按目标表裁剪：

- 代码侧的真实列清单在 `catalog.LINUX_TABLE_FIELDS`（7 张 Linux 表）与 `catalog.WINDOWS_TABLE_FIELDS`（5 张 Windows 表共用），与 §4.3 的只读核验结果一致。
- 表里没有的字段**不会**导致 INSERT 失败：非空值会被追加进 `tips`，不丢信息。
- 整型列（`event_id`/`pid`/`ppid`/`uid`/`auid`/`session_id`/`source_port`/`destination_port` 以及 Windows 的 `source_year`/`event_record_id`/`execution_*`）空值写 `NULL`；字符串按列宽截断（如 `result` 64、`operation` 255、`eventdata` 为 JSON）。
- 时间列（`event_time` / `time_created`）必须传入 `datetime`：解析层把 ISO（含 `Z`/偏移）、`%Y-%m-%d %H:%M:%S %z`、epoch 秒/毫秒/微秒统一转成 **UTC naive `datetime`**。直接把原始字符串交给 MySQL 会报 `(1292, "Incorrect datetime value")`——这正是 2026-10-10 修复的故障。
- 缺少可解析时间戳的记录在解析阶段被拒，接口以 `errors[].stage="parse"` 报出，不再静默跳过。

### 4.3 关键词与最近日志查询的列适配（2026-10-10 只读核验）

本次对真实库执行了只读 `information_schema` 查询，7 张 Linux 表的列名已核验，`catalog.py` 的列定义与真实表一致。缺列情况如下：

| 表 | 有 `hostname` | 有 `source_address` |
|---|---|---|
| `authentication_session` | 是 | 是 |
| `account_security_change` | 是 | 是 |
| `process_command_execution` | 否 | 否 |
| `file_object_access` | 否 | 否 |
| `network_ipc_communication` | 否 | 是 |
| `system_service_audit_lifecycle` | 是 | 否 |
| `security_policy_config_change` | 否 | 否 |

7 张表都实际拥有 `event_time`、`category`、`type`、`result`。基于该结论，代码已按表适配：

- 带 `keyword` 的 Linux 分类查询只在目标表实际存在的列上做 `LIKE`。
- `/api/v1/logs/recent` 对缺失列生成 `NULL AS <列>` 占位，返回中这些字段为 `null`。

修复前上述位置直接拼接固定列名，实测返回 HTTP 500：`(1054, "Unknown column 'hostname' in 'field list'")`。

## 5. `windows_logs` 代码依赖

Windows 表名固定为：

- `application_logs`
- `security_logs`
- `setup_logs`
- `system_logs`
- `forwardedevents_logs`

仓库没有这些表的正式 DDL。当前读取和写入依赖如下：

| 用途 | 代码依赖字段 |
|---|---|
| 分类列表读取 | `id`, `time_created`, `event_id`, `level`, `channel`, `computer`, `provider_name`, `eventdata`, `imported_at` |
| 最近日志读取 | `time_created`, `channel`, `event_id`, `level`, `computer` |
| 采集写入 | `source_year`, `source_file`, `provider_name`, `event_id`, `time_created`, `event_record_id`, `channel`, `computer`, `system_extra`, `eventdata` |
| 关键词查询 | `computer`, `provider_name`, `event_id` |

`source_file` 写成 `fluent-bit://{client_id}/{channel}`，`system_extra` 固定写入字符串 `{}`，`eventdata` 是原始对象 JSON。`source_year` 从事件时间字符串前四位提取。

2026-10-10 只读核验：五张表的列名与上表的代码依赖完全一致（字段数量与名称均相同）；字段类型、主键、唯一约束、索引和 JSON 列类型仍待核验。

## 6. `audit_log_evidence` 兼容依赖

当前代码从 `linux_engine`（即默认 `linux_logs`）执行：

```sql
SELECT *
FROM audit_log_evidence
ORDER BY received_at DESC
LIMIT 1000
```

仓库没有该表模型或迁移，也不向该表写入。可以从代码确认的字段依赖仅有：

| 字段 | 依赖来源 | 用途 |
|---|---|---|
| `received_at` | SQL | 排序 |
| `verify_status` | 后端 | 摘要计数、列表过滤 |
| `evidence_id` | 当前模板 | 列表显示 |
| `category` | 当前模板 | 列表显示 |
| `raw_hash` | 当前模板 | 列表显示 |

状态统计识别 `VALID`、`HASH_MISMATCH`、`RAW_MISSING`、`PENDING`。其他列因为 `SELECT *` 会原样出现在列表和 CSV 中，但不能据此确认完整结构。

表缺失或任何查询异常都会被 `_evidence_rows()` 吞掉并返回空列表，因此 API 无法区分“没有存证”和“存证库故障”。存证摘要、分页和导出都只覆盖最近 1000 条。2026-10-10 只读核验：`linux_logs` 与 `log_fusion` 中都不存在该表，因此本环境的存证接口必然返回空结果——这正是“故障被伪装成空数据”的实际案例。

## 7. 事务与异常行为

| 路径 | 事务边界 | 失败行为 |
|---|---|---|
| Linux 日志写入 | 先尝试一个请求列表一次 `linux_engine.begin()`；失败后退化为**逐行事务** | 逐行重试用于把失败定位到具体记录，接口返回 200、`persisted:false`、`errors[].stage="insert"` 与失败下标 |
| Windows 日志写入 | 先尝试一个请求列表一次 `windows_engine.begin()`；失败后同样逐行重试 | 同上 |
| 心跳 upsert | 一个请求列表使用一个 ORM Session 提交 | 失败返回 503；上下文退出时 Session 关闭，未提交事务回滚 |
| 日志查询/统计 | 每个 helper 打开独立连接 | 多表汇总不具备一致快照；异常通常成为 500 |
| 存证读取 | 单连接读取最多 1000 条 | 所有异常降级为空集合 |

日志写入在线程中执行，并由 `asyncio.wait_for` 设置 8 秒等待上限。超时只能停止等待，不能保证底层数据库线程已被取消；真实提交状态可能需要进一步验证。解析在入库之前完成，且为纯函数（`backend/app/parsers.py`），因此“解析失败”不会占用数据库连接。

## 8. Linux 与 Windows 模型差异

| 方面 | Linux | Windows |
|---|---|---|
| 分表方式 | 分类键直接作为表名 | `{channel}_logs` |
| 时间字段 | `event_time` | `time_created` |
| 类型/事件 | `type`、`event_id` | `channel`、`event_id`、`provider_name` |
| 主机 | 部分类别使用 `hostname` | `computer` |
| 原始内容 | 当前写入可能落到 `tips` 或载荷字段；无正式原始存储 | `eventdata` JSON |
| 统一主键 | 代码查询不依赖统一 `id` | 列表依赖 `id` |
| 来源客户端 | 没有专门列被当前写入逻辑保留 | 编码在 `source_file` URI 中 |

## 9. 初始化、迁移与保留策略

- `requirements.txt` 包含 Alembic，但仓库没有 `alembic.ini`、versions 目录或迁移脚本。
- 启动时仅 `create_all()` 管理 `client_status`；不会创建数据库本身、日志表或存证表，也不会变更已有列。
- 代码中未发现种子数据、数据库回滚脚本、数据归档、TTL、分区、清理任务或备份恢复策略。
- 代码中未发现外键关系。
- 日志表、存证表、心跳表的容量目标和数据保留周期均待确认。

## 10. 待确认清单

1. 三个逻辑库的正式 `SHOW CREATE TABLE`、索引、字符集、时区与 MySQL 版本；列名已于 2026-10-10 只读核验，类型、索引与字符集仍未核验。
2. 7 张 Linux 表是否都实际拥有关键词和 recent 查询依赖的字段：**已于 2026-10-10 只读核验**，结论见 §4.3，代码已按表适配。
3. 5 张 Windows 表的字段类型，以及 `eventdata`/`system_extra` 是 TEXT 还是 JSON；列名已核验且与代码依赖一致。
4. `audit_log_evidence` 的正式归属、完整字段、主键、索引和数据生产方；2026-10-10 只读核验确认 `linux_logs` 与 `log_fusion` 中均不存在该表。
5. `client_status` 的实际索引名称与 `updated_at` 数据库行为；已核验 `log_fusion` 库仅有这一张表且列与 ORM 模型一致，索引名称待核验。
6. 应用账号所需的最小权限，以及生产是否允许应用执行 DDL。
7. 数据保留、备份、恢复、归档与隐私/合规要求。
