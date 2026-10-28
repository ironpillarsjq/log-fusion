# 数据库设计与代码依赖

> 基线日期：2026-10-10（同日按当前工作区代码复核）。本文件严格区分“仓库中有正式模型的结构”和“只能从 SQL 推导的代码依赖字段”。本次文档工作没有连接真实 MySQL，也没有执行 DDL/DML；既有日志表的真实类型、约束和索引均待只读核验。

## 1. 数据库划分

独立 `security-log-client/` 项目按用户要求设计在 `log_fusion` 中新增 `nsfocus_ips_log`、`nsfocus_ip_blacklist_log`、`nsfocus_web_security_log` 三张专用表，详见 [三类日志分表设计](../security-log-client/docs/LOG_FUSION_TABLES.md)。**2026-10-16 已实际创建三张日志表及同步表，接入后台与前端；按用户最新要求启用仅实时采集，已有历史足够，剩余历史不再自动补录，见本文 §11。**早期 Windows 字段及通用四表参考保留在 [原字段核验文档](../security-log-client/docs/DATABASE.md)。

当前应用连接同一 MySQL 服务上的四个逻辑库，并为每个库创建独立同步 SQLAlchemy Engine：

| 配置项 | 默认库 | Engine | 用途 |
|---|---|---|---|
| `LINUX_DB` | `linux_logs` | `linux_engine` | 7 类 Linux 审计表 |
| `WINDOWS_DB` | `windows_logs` | `windows_engine` | 5 类 Windows 事件表 |
| `LOG_FUSION_DB` | `log_fusion` | `monitor_engine` | 平台心跳状态 `client_status`、归档台账 `archive_log` |
| `LOG_HASH_DB` | `log_hash` | `hash_engine` | 原始日志原文 + SHA-256 摘要存证表 `log_hash`（2026-10-11 新增） |

> 2026-10-11 起存证读写全部走 `log_hash` 库；旧的 `linux_logs.audit_log_evidence` 在真实库中并不存在，
> 相关代码路径已移除（详见 §3.2）。
>
> 2026-10-13 只读核验：`log_fusion` 库实际有 **3 张表**——`client_status`、`archive_log`，
> 以及一张 **0 行的遗留空表 `log_hash`**（`LOG_HASH_DB` 拆分前由 `create_all()` 建在此处，
> 形态是 `varchar(64)` + `ix_log_hash_log_hash`，与 `log_hash` 库里的真实存证表不同）。
> 当前代码不读写它；是否删除见 §10 第 5 条。

所有连接 URL 使用 `mysql+pymysql` 和 `charset=utf8mb4`，开启 `pool_pre_ping=True` 与 `pool_recycle=1800`。四个库之间没有跨库事务。

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
| `LOG_HASH_DB` | string | `log_hash`，原始日志存证库 |
| `LOG_HASH_TABLE` | string | `log_hash`，存证表名 |
| `RAW_STORAGE_DIR` | string | `data/raw`，当前未参与数据库或文件写入 |

标准启动脚本先切换到 `backend/`，所以默认 `.env` 位置应为 `backend/.env`。仓库当前没有 `.env` 示例文件。

健康检查对每个 Engine 执行 `SELECT 1`，失败只返回 `false`，不暴露异常详情。

## 3. 由 ORM 正式声明的表

`backend/app/models.py` 当前声明两张表：心跳状态 `client_status`（`log_fusion` 库）与原始日志存证 `log_hash`（`log_hash` 库）。`client_status` 由 `create_all(monitor_engine)` 创建，`log_hash` 由启动时的 `create_all(hash_engine, tables=[LogHash.__table__])` 创建；两者建表失败都只记录日志、不中断启动。

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

### 3.2 `log_hash.log_hash`（2026-10-11 新增）

原始日志存证表。每条采集记录在写入业务表之前，先按 `sha256(timestamp + 0x1F + 存证原文)` 计算摘要，并把存证原文与摘要一起保存，用于事后防篡改校验。**存证原文按平台不同**：Linux 取采集 JSON 的 `log` 字段，Windows 取整条记录的规范化 JSON（见下方「哈希输入定义」）。可复现 DDL 见 [`docs/sql/log_hash.sql`](./sql/log_hash.sql)。

| 字段 | SQLAlchemy 声明 | 空值 | 约束/索引 | 用途 |
|---|---|---|---|---|
| `id` | `BIGINT` | 否 | 主键、自增 | 内部记录 ID |
| `log_hash` | `VARCHAR(64)` | 否 | **唯一**、索引 | `sha256(timestamp + 0x1F + 存证原文)` 的小写十六进制摘要 |
| `raw_log` | `LONGTEXT` | 否 | 无 | 存证原文：Linux = 采集 JSON 的 `log` 字段原文；Windows = 整条记录的规范化 JSON |
| `log_timestamp` | `VARCHAR(64)` | 是 | 无 | **参与哈希的原始时间字符串**，不做归一化，供复算 |
| `platform` | `VARCHAR(16)` | 是 | 无 | `linux` / `windows` |
| `source` | `VARCHAR(255)` | 是 | 无 | 采集来源，如 `ubuntu-audit`；Windows 载荷无 `source` 时回退用 `SourceName`（Provider 名） |
| `client_id` | `VARCHAR(128)` | 是 | 索引 | 上报客户端标识 |
| `event_time` | `DATETIME` | 是 | 索引 | 解析后的 UTC 时间，仅用于查询排序，**不参与哈希** |
| `created_at` | `DATETIME` | 否 | 服务端默认 `NOW()` | 服务端入库时间 |

> **上表写的是 ORM 声明，真实列以 `docs/sql/log_hash.sql` 为准**（2026-10-13 只读核验）：
> 真实 `log_hash.log_hash` 的 `log_hash` 列是 `char(64)`、唯一索引名是 `uq_log_hash`，与 SQL 脚本一致；
> 而只靠 `create_all()` 建表会得到 `varchar(64)` + `ix_log_hash_log_hash`（遗留的 `log_fusion.log_hash`
> 正是这种形态）。两者结构不同，**建表请用 `docs/sql/log_hash.sql`**。

#### 哈希输入定义（存证可复算的前提）

```
存证原文 = Linux: record["log"] 原文
           Windows: canonical_json(record)   # sort_keys + 紧凑分隔符 + ensure_ascii=False
log_hash = sha256_hex( 原始时间戳字符串 + "\x1f" + 存证原文 )
```

- 原始时间戳的取值：Linux 用 `timestamp`；Windows 按 `TimeGenerated` → `TimeWritten` →
  `event_time` → `timestamp` → `date` 的优先级取第一个非空值，**顺序与
  `parsers.normalize_windows` 保持一致**，否则存证时间与业务表时间会来自不同字段。
- 两个字段都取**未做业务加工**的值：不 trim、不做大小写转换。Windows 侧的"不加工"
  指的是不再对记录本身做字段挑选或归一化，但**序列化本身是一次加工**（见下条）。
- 分隔符用 US（`0x1F`，Unit Separator）而不是直接拼接，避免
  `timestamp="...Z" + log="abc"` 与 `timestamp="...Zabc" + log=""` 撞成同一摘要。
- **时间戳必须参与哈希**：syslog/auditd 里同一台机器在不同时刻产生内容完全相同的行很常见；
  若只哈希原文，这些行会得到同一摘要并被唯一索引吞掉，存证就无法与日志一一对应。
  Windows 侧时间戳已包含在规范化 JSON 里，再单独参与哈希是冗余但无害的。
- **Linux `log` 中的控制字符按原始字节参与哈希**。auditd 的多字段分隔符是 `0x1D`（GS），
  必须按解码后的字节计算，不能先用 `\u001d` 转义再哈希。
- **`raw_log` 不做列宽裁剪**（`LONGTEXT`），否则存证与原文不符，哈希无法复算。

> **两侧存证语义不等价（重要边界）**：Linux 的 `raw_log` 是采集端发来的**字节原文**；
> Windows 的 `raw_log` 是 FastAPI 反序列化之后再规范化输出的 JSON 文本——键序、空白、
> 数字字面量与重复键在解析阶段已经定型。所以 Windows 存证证明的是
> 「服务端收到的这条事件内容未被服务端改动」，**不能**证明「采集端发出的字节流未经中间环节篡改」。
> 要覆盖后者必须让 winlog 输出自带 `log` 字段（改采集端），已记入 `docs/TODO.md`。
>
> 复算**不需要**重新序列化：`raw_log` 列存的就是被哈希的那个字符串本身。

#### 写入与去重语义

- 每条接收到的日志写一条存证，`log_hash` 唯一索引保证**同一 `(时间戳, 原文)` 重复上报**
  （Fluent Bit 重试）只留一条；唯一键命中按幂等成功处理，不计入失败。
- 相同内容、不同时间戳的日志会各自成行（摘要不同），业务表与存证表保持一一对应。
- **存证原文为空时不写存证**（Linux 缺 `log`、Windows 为空对象），在采集响应 `errors[]`
  里记一条 `stage="hash"`，但**不影响日志入库**：`persisted` 只反映业务表结果。
- **存证写库失败不影响已入库的日志**：业务表写入先完成，存证独立失败并逐行定位上报。
- 实测校验（同一 SQL 可在 MySQL 侧复算，`IFNULL` 与写入端 `raw_text(None)==""` 对齐）：
  `SHA2(CONCAT(IFNULL(log_timestamp,''), CHAR(31), raw_log), 256) = log_hash`。

#### 存证覆盖率的历史基线（2026-10-15 只读核验）

「日志总量」与「存证总数」在 2026-10-15 之前**本来就不该相等**，差额 670,693 条由两部分构成：

| 来源 | 条数 | 能否补齐 |
|---|---|---|
| Windows 全部日志（载荷无 `log` 字段，逐条被跳过） | 441,408 | 已修复写入端，**存量无法补**（原文未保存） |
| Linux 早于存证功能上线（2026-10-11 15:19）批量导入的历史 | 约 229,285 | 无法补，除非重放 `旧后端/` 的导入源文件 |

修复后 Windows 新数据开始正常存证；按 `event_time` 逐日对照，Linux 侧 2026-10-13 的覆盖率为 100%
（8,597/8,597），说明 Linux 链路本身没有漏。2026-10-12 那 233 条无存证的 Linux 记录是当日
21:31 手动归档删除所致（台账 `archive_log` #1），CSV 仍在 `data/archive/`，可用
`tools/restore_archive.py` 回灌，不是丢失。

### 3.3 `log_fusion.archive_log`（2026-10-11 新增）

存证归档台账。每次「导出并删除」成功后写一行，记录归档产物与范围。
**删除不可逆，台账是事后追溯「哪些记录在什么时候被导出到哪里」的唯一依据。**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK | 自增 |
| `mode` | VARCHAR(16) | `auto`（自动归档）/ `manual`（手动区间归档） |
| `file_path` | VARCHAR(512) | 归档 CSV 的绝对路径 |
| `row_count` | BIGINT | 写入 CSV 的记录数 |
| `deleted_count` | BIGINT | 实际删除的行数（正常应等于 `row_count`） |
| `file_sha256` | VARCHAR(64) | CSV 文件内容摘要，可校验归档文件未被改动 |
| `range_start` / `range_end` | DATETIME | 手动归档的时间区间（按 `created_at`） |
| `before_time` | DATETIME | 自动归档的截止时间（`created_at < before_time`） |
| `created_at` | DATETIME | 归档时间 |

> 2026-10-13 只读核验：真实表与上表一致——`file_sha256` 是 `varchar(64)`，索引只有
> `ix_archive_log_mode` 与主键（**没有** created_at 索引）。`docs/sql/archive_log.sql` 已按此改正。

#### 归档策略与安全约束

- **保留期**：`archive_retention_months`（默认 3）个**日历月**；自动归档删除
  `created_at` 早于该时间点的记录。用 `created_at`（入库时间）而**不是** `event_time`，
  否则客户端补传的历史日志会刚入库就被归档掉。
- **归档目录**：`archive_dir`（默认 `data/archive`）。相对路径锚定到**仓库根目录**，
  保证从不同工作目录启动时文件都落在同一处；导出的 CSV **永久保留，不做自动清理**。
- **先落盘后删除**：写 CSV → `flush` + `fsync` → 校验文件存在、大小 > 0、行数与 id 数一致
  → 才执行 `DELETE`。任一步失败立即中止，**一条都不删**。
- **按 id 删除**，不是 `DELETE WHERE created_at < X`：否则归档期间新写入、同样满足条件的
  记录会被误删。
- **单批上限** `archive_batch_limit`（默认 200000）限制单次归档行数，避免把整表拉进内存。
- 台账写入失败**不影响**已完成的导出与删除，只记录日志。

实测（2026-10-12，`log_hash` 表 34099 条）：手动归档整日区间导出
13,746,826 字节 CSV，用 CSV 解析器读回 **34099 条**、`raw_log` 无空值、`log_hash` 全为 64 位，
数据库剩余 0 条，台账记录 `rows=34099 deleted=34099`，文件摘要与接口返回值一致。

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

## 6. `audit_log_evidence`（已废弃，2026-10-11 移除）

2026-10-11 之前，`/api/v1/evidence/*` 从 `linux_engine`（默认 `linux_logs`）执行：

```sql
SELECT * FROM audit_log_evidence ORDER BY received_at DESC LIMIT 1000
```

仓库没有该表模型或迁移，也从不向它写入。当时的字段依赖：

| 字段 | 依赖来源 | 用途 |
|---|---|---|
| `received_at` | SQL | 排序 |
| `verify_status` | 后端 | 摘要计数、列表过滤 |
| `evidence_id` | 模板 | 列表显示 |
| `category` | 模板 | 列表显示 |
| `raw_hash` | 模板 | 列表显示 |

表缺失或任何查询异常都被 `_evidence_rows()` 吞掉并返回空列表，因此 API 无法区分
「没有存证」和「存证库故障」。2026-10-10 只读核验：`linux_logs` 与 `log_fusion` 中
**都不存在**该表，页面上「完整性存证」永远是空的——故障被伪装成空数据。

**现状**：这三个接口已改为读取 `log_hash.log_hash`（见 §3.2），`_evidence_rows()`、
`verify_status` 过滤参数与相关模板字段一并移除。该表及其字段不再是任何代码的依赖，
仅作为历史记录保留在本节。

## 7. 事务与异常行为

| 路径 | 事务边界 | 失败行为 |
|---|---|---|
| Linux 日志写入 | 先尝试一个请求列表一次 `linux_engine.begin()`；失败后退化为**逐行事务** | 逐行重试用于把失败定位到具体记录，接口返回 200、`persisted:false`、`errors[].stage="insert"` 与失败下标 |
| Windows 日志写入 | 先尝试一个请求列表一次 `windows_engine.begin()`；失败后同样逐行重试 | 同上 |
| 心跳 upsert | 一个请求列表使用一个 ORM Session 提交 | 失败返回 503；上下文退出时 Session 关闭，未提交事务回滚 |
| 日志查询/统计 | 每个 helper 打开独立连接 | 多表汇总不具备一致快照；异常通常成为 500 |
| 存证读取 | 每个 helper 打开独立连接 | 查询失败返回 **503**（2026-10-12 前是降级为空集合，会把「库连不上」伪装成「没有存证」） |
| 存证写入 | 先尝试整批一次 `hash_engine.begin()`；失败后退化为**逐行事务** | 只有唯一键冲突（errno 1062）算幂等成功；其他完整性错误计入 `errors[].stage="hash"`，不影响业务表入库 |
| 存证归档 | 导出落盘校验通过后，按 id 分批 `DELETE` | 导出/校验任一步失败即中止且**不删除**；存量台账写入失败不影响已完成的归档 |

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
- 启动时仅 `create_all()` 管理 `log_fusion` 库的表（`client_status`、`archive_log`）与
  `log_hash.log_hash`；不会创建数据库本身、日志表，也不会变更已有列。
- **保留策略（2026-10-11 新增）**：存证由自动归档任务处理——超过
  `archive_retention_months`（默认 3 个日历月）的记录导出为 CSV 后从库中删除。
  业务日志表（`linux_logs`/`windows_logs`）仍**没有任何** TTL、分区或清理任务。
- 归档 CSV 永久保留在 `archive_dir`，不做自动清理。
- 代码中未发现种子数据、数据库回滚脚本或备份恢复策略。
- 代码中未发现外键关系。
- 日志表与心跳表的容量目标和数据保留周期均待确认。

## 10. 待确认清单

1. 四个逻辑库的正式 `SHOW CREATE TABLE`、索引、字符集、时区与 MySQL 版本；列名已于 2026-10-10 只读核验，类型、索引与字符集仍未核验。
2. 7 张 Linux 表是否都实际拥有关键词和 recent 查询依赖的字段：**已于 2026-10-10 只读核验**，结论见 §4.3，代码已按表适配。
3. 5 张 Windows 表的字段类型，以及 `eventdata`/`system_extra` 是 TEXT 还是 JSON；列名已核验且与代码依赖一致。
4. `audit_log_evidence` 的正式归属与数据生产方：**已于 2026-10-11 确认废弃**，真实库中不存在且已无代码依赖（见 §6）。
5. `client_status` 的真实索引名称与 `updated_at` 数据库行为仍待核验（列已核验且与 ORM 一致）。`log_fusion` 库实际有 **3 张表**：`client_status`、`archive_log`，以及**遗留空表 `log_hash`（0 行）**——它是 `LOG_HASH_DB` 拆分前由 `create_all()` 建在这里的，当前代码不读写；**是否删除需单独确认**（删除属于数据库变更，不在文档修正范围内）。
6. 应用账号所需的最小权限，以及生产是否允许应用执行 DDL。
7. 生产环境的归档保留期（当前默认 3 个月）、归档文件存放位置与异地备份要求；
   归档 CSV 目前永久保留，磁盘容量规划待确认。

## 11. 绿盟三类安全日志（2026-10-16 实际新增）

目标是已有 `log_fusion`，实际新增 `nsfocus_ips_log`（118 列）、`nsfocus_ip_blacklist_log`（70 列）、`nsfocus_web_security_log`（92 列）与 `nsfocus_sync_state`（4 列）。实机 MySQL 8.4.9 / 16KiB InnoDB 页 / 64MiB max_allowed_packet，原有三表保留。三张日志表完整 DDL 在 `security-log-client/sql/log_fusion_nsfocus_tables.sql`；断点表 DDL 在 `security-log-client/sql/nsfocus_sync_state.sql`；字段类型事实源在同目录 fields.json。详见 [分表与字段](../security-log-client/docs/LOG_FUSION_TABLES.md) 和 [迁移/续跑/配置](../security-log-client/docs/SYNC.md)。

每条保存全部来源列、完整原始 JSON、UTC 时间和规范化 JSON SHA-256。唯一键 `(source_instance,upstream_index,log_id)`；相同内容跳过，内容冲突失败并保留原条。普通索引覆盖实例+时间/源 IP/目的 IP/规则，黑名单补充域名前缀索引。同步 JSON 队列与对应窗口数据同事务写入；只有提交后前移边界，连接失败不跳过窗口。后台及 CLI 用同一会话锁串行处理。新表独立迁移，不注册进现有 ORM create_all，也不产生现有 log_hash 存证。

迁移顺序：审阅 DDL → CLI migrate 创建缺失表并验证列名/类型/NULL/身份索引 → 手动 run 历史补录 → 由用户选择开启后台。兼容性：不修改原表/采集端协议。回滚：先关闭 SECURITY_SYNC_ENABLED 并重启，回退应用代码，保留新表和断点；不自动删数据。DDL 隐式提交，中断后重跑验证现有表。用户已确认现有导入量足够：停止剩余历史，启动仅采集最新数据。保留已有日志和历史窗口，未完成历史状态改为 stopped，实时状态在每次启动重置为近期起点。无新增 DDL、无数据删除。


## WAF/DAS Syslog 六表（2026-10-28，DDL 已入仓库，真实库未执行）

新增设计与写入代码面向 LOG_FUSION_DB（默认 log_fusion），六表为 waf_audit_log、waf_security_log、waf_traffic_log、das_statement_log、das_session_log、das_sensitive_log。可复现 DDL 是 [sql/device_syslog.sql](sql/device_syslog.sql)，列事实源是 backend/app/device_log_catalog.py 的 COMMON/BUSINESS/TABLES；这与既有 Linux/Windows 的 catalog.LINUX_TABLE_FIELDS / WINDOWS_TABLE_FIELDS 是分别维护的协议边界，不复用旧平台裁剪规则。

每表均有 raw_log LONGTEXT NOT NULL、raw_bytes LONGBLOB NOT NULL、raw_sha256 BINARY(32)、payload JSON；另有 source_instance、设备与 peer 地址、transport、log_type、UTC event_time/received_at/ingested_at、原始时间和解析状态。保留产品原生业务字段，六表完整字段和迁移/回滚顺序见 [SYSLOG.md](SYSLOG.md)。自动建表 Base 未引入这些表。脚本只新增六表，不创建库，不升级已有同名表；手工执行前需核对数据库名与现有结构。

只有 ingest_id（首次本地接收 UUID）唯一；raw_sha256、ssid、correlation_id、data_id 等不被臆设为唯一事件键。接收器默认随主服务启停；接收缓冲 SQLite inbox 位于仓库根 data/syslog/，未知报文留 quarantine，写 MySQL 失败保留 pending。SQLite 队列不是第七张 MySQL 业务表。接收器不写 log_hash 存证库，不改变已有哈希算法和存证归档。无实际数据库结构变化；本次开发未执行 DDL/DML。后续部署须显式执行 SQL，回滚保留六表及缓冲，不自动删表/清数据。
