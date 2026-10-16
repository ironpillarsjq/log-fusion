# 安全产品数据入库设计草案

> **当前选定方案**：用户已指定三类日志在 `log_fusion` 库分表，见 [三类专用表设计](LOG_FUSION_TABLES.md) 和 [当前建表 SQL](../sql/log_fusion_nsfocus_tables.sql)。本页保留早期字段核验和通用四表方案作为历史参考，早期 `schema_draft.sql` 不再是当前选定的落库方案。

状态：**以下为早期 Windows/通用四表的历史草案，未执行该草案。当前实际三类日志建表和导入见 [LOG_FUSION_TABLES.md](LOG_FUSION_TABLES.md) 与 [SYNC.md](SYNC.md)。** SQL 在 `../sql/schema_draft.sql`。目标语法为 MySQL 8.0+，实际服务器版本和容量尚未验证。本项目与 Log Fusion 的现有 Linux/Windows 业务表、心跳表及存证表独立。

## 已取得的数据与字段数量

基于本地 `data/samples/windows_20261015/` 的实际 3,000 条记录：

| 层次 | 数量 | 含义 |
|---|---:|---|
| 日志响应 envelope | 6 个字段 | totalPage、totalSize、page、size、partOfData、data；属于分页信息 |
| 单条日志外层 | 2 个字段 | index、source |
| source 字段并集 | 50 个字段 | 本批样例实际出现的全部业务字段，不保证其他类型/日期也只有 50 个 |
| 单条 source | 46～50 个字段 | 46 个字段在 3,000 条中均出现，4 个字段为部分记录独有；出现不代表非空 |
| 事件响应 envelope | 5 个字段 | page、size、totalPage、totalSize、data |
| 真实事件 | 0 条 | 请求成功但 data=[]，没有真实事件字段可验 |
| 手册事件成功示例 | 25 个字段 | 仅作为暂定契约；字段表为 24 个，示例额外出现 infoRetrieve |

4 个部分出现字段：`domain`、`result` 各 15 条，`sip`、`sport` 各 1 条。实测类型和字符串长度统计见本地 `reports/windows_20261015/field_profile.json`。最长字符串是样例观测值，**不能直接当作产品字段长度上限**。

事件手册示例字段为：`action`、`alarmState`、`category1`、`description`、`destination`、`dstCity`、`dstCountry`、`dstProvince`、`duserName`、`endTime`、`eventDrill`、`eventNsuid`、`hasAlarm`、`infoRetrieve`、`name`、`nstimestamp`、`occurrence`、`risk`、`ruleId`、`stage`、`startTime`、`suggesstion`、`suserName`、`tags`、`valid`。

注意：手册字段表与示例类型有不一致，destination、地域、用户名和 tags 在示例中可能为数组；不能按字段表一律建成字符串/整数。`eventDrill` 示例是内嵌 JSON 字符串；`suggesstion` 拼写按上游原样保留。以上必须等待真实事件再验证。

## 四张表的职责

| 表 | 内容 | 去重和查询方式 |
|---|---|---|
| security_fetch_batch | 每页请求、参数、响应分页元信息、成功/失败、计数和本地响应文件及摘要 | 实例 + run_id + 数据类型 + 页码唯一；空事件结果也保留成功批次 |
| security_log | 原始日志的常用查询列 + 完整 JSON 记录 | 实例 + index + source.log_id 唯一；时间、事件码+时间、设备 IP+时间索引 |
| security_event | 事件常用查询列 + 完整 JSON；当前为手册推导 | 实例 + eventNsuid 唯一；时间、风险+时间索引 |
| security_event_log | 本地事件与本地日志的多对多关联 | event_id + log_id 唯一；仅真实关联成功解析后写入 |

`source_instance` 是配置分配的稳定产品实例编号（如 `nsfocus-site-a`），不能只按日志 ID 在所有设备间去重。API 地址更换时仍应保持实例编号稳定，替换成另一台产品时则分配新编号。密码和 Token 不进入业务表或批次参数。

没有必要把这 50 个字段全部固定为数据库列：将经常筛选的时间、设备 IP、主机、事件码、分类和风险等抽成普通列，完整 `index` + `source` 保留在 `raw_record JSON`。这样既能建立常用索引，也不会丢失暂未映射、类型变化或将来新增的字段。

MySQL 原生 JSON 会验证合法性；整个 JSON 列不直接建立普通索引，因此本方案将常查值抽成独立列。参考：[MySQL JSON 类型说明](https://dev.mysql.com/doc/refman/8.0/en/json.html)。SQL 的 VARCHAR 长度是待验证设计容量；超长标量不得静默截断，应保留原始响应、记录拒绝原因，再扩展列或调整投影。

## 关键映射

| 上游字段 | 建议投影 | 规则 |
|---|---|---|
| record.index | upstream_index | 真实索引名，不等同于 source.table |
| source.log_id | upstream_log_id | 保留原值，不用主机+时间代替唯一标识 |
| source.oevent_id | event_code | 本批实际 Windows 事件码；不要用均为 0 的 eventidentifier |
| source.timestamp | event_time | Unix 秒转 UTC DATETIME(3) |
| source.recv_time | received_time | Unix 毫秒转 UTC DATETIME(3)，不与 timestamp 混用单位 |
| source.host / dev_ip | hostname / device_ip | 保留原值；host 与原文 ComputerName 的命名差异不自动覆盖 |
| source.sip / sport | source_ip / source_port | 缺失写 NULL，不编造 0 或空字符串 |
| source.risk / severity | risk_value / severity_value | 保留原始整数；不能套用事件接口的风险等级 |
| 完整日志 record | raw_record | 保留全部 50 个 source 字段及 index，包含原始 raw_data 字符串 |
| eventNsuid | upstream_event_id | 事件的上游标识，待真实样例验证 |
| nstimestamp / startTime / endTime | event_time / start_time / end_time | 事件手册示例为秒；真实值需类型和范围校验 |
| 事件完整对象 | raw_record | 数组、嵌套 JSON 字符串、未知字段均原样保留其 JSON 值 |

JSON 保留语义和值，但不承诺保留接口 HTTP 原始字节、键序和空白。已有采样文件是客户端序列化保存的响应，不应称作原始传输字节。摘要只用于文件或规范化记录一致性检查，不能作为防伪签名。时间统一按应用约定写 UTC，展示再转北京时间；DATETIME 本身不附带时区，参考 [MySQL 日期时间类型](https://dev.mysql.com/doc/refman/8.0/en/datetime.html)。

## 入库流程及幂等

1. 获取一页并先保存响应文件及 manifest，校验请求成功、分页参数一致、`data` 为数组且 `partOfData` 不为 true。
2. 创建一条批次，记录固定的时间范围、页码和计数；不得将 Auth-Token 或登录请求体写入 request_json。
3. 校验标识、时间、投影长度和类型。数据保留于完整 JSON；不满足固定列约束的记录标记拒绝并保留文件，不自动截断。
4. 一页的行写入和批次 committed 状态在一个事务中提交；失败回滚，另行记录失败批次。未提交成功的页不能推进后续同步位点。
5. 日志按唯一键幂等插入，重复且摘要相同则跳过；相同标识但内容不同视为冲突，保留首次内容及本次文件并记录拒绝，不能静默覆盖。
6. 事件状态可能变化，可按 eventNsuid 更新当前投影和 last_batch_id，保留 first_batch_id；各次响应文件保留历史。当前 SQL 不提供独立事件版本表，如需永久版本审计应再扩展，不能只依赖覆盖后的 JSON。
7. 本次事件为 0 条时只记录成功的空批次，不插入占位事件。关联表暂不写入。

3.5.5 的手册示例不保证提供与日志查询相同的 index。获得真实关联响应后，需先确认它的命名空间和 log_id 如何对应现有日志，才能建立外键；不能凭 table 名或相近时间硬连事件与日志。应用还需保证关联双方属于同一 source_instance。

## 上线、回滚和容量边界

- 兼容性：只建议建立独立逻辑库或使用这组新表名，不改变现有四个逻辑库的既有表结构。独立项目目前没有数据库连接配置和导入命令。
- 验证顺序：确认目标版本/字符集/权限 → 空测试库执行草案 → 导入本批 3,000 条 → 重跑确认新增 0 条、重复 3,000 条 → 确认时间/IP/事件码查询 → 有真实事件后再验事件和关联。
- 本次没有执行上述入库测试，不宣称 SQL 已由真实 MySQL 验证。DDL 是可审查草案，不是现行结构或已批准上线迁移。
- 回滚：先停导入程序，保留本地响应文件；新建空测试表可按关联表、事件表、日志表、批次表顺序删除。已有数据时先导出备份，不自动删除表或数据。草案不含 DROP/ALTER，也不含对已有业务表的修改。
- 不根据本批前几页估算实际全天容量。接口曾在 24 小时窗口返回数百万匹配数，正式持续采集前需核算平均记录大小、保留期、索引空间、批次提交大小和查询负载。
- 深分页上限与稳定排序/游标尚未确认；数据库去重可以减少重入，但不能恢复 API 分页漏掉的日志。持续采集需要重叠时间窗口、进度台账和一致性验证，不能单凭保存了部分样例就认定全量同步完成。

无实际数据库结构变化，无实际接口行为变化。本次交付仅为字段核验、文档和未执行的建表草案。

## 日志 50 字段实测清单

以下由本批 3,000 条样例统计；不是官方全量字段合同。

| 字段 | 样例类型 | 出现条数 / 3000 | 最长字符串字符数 |
|---|---|---:|---:|
| `agent_id` | str | 3000 | 36 |
| `asset_name` | str | 3000 | 11 |
| `cascade_node_id` | str | 3000 | 36 |
| `category` | str | 3000 | 5 |
| `collector_id` | str | 3000 | 19 |
| `dev_ip` | str | 3000 | 11 |
| `dip` | str | 3000 | 11 |
| `domain` | str | 15 | 12 |
| `eventidentifier` | str | 3000 | 1 |
| `facility` | int | 3000 | — |
| `host` | str | 3000 | 15 |
| `insertionstrings` | str | 3000 | 437 |
| `keywords` | str | 3000 | 0 |
| `level` | str | 3000 | 11 |
| `log_id` | str | 3000 | 32 |
| `log_source_id` | str | 3000 | 36 |
| `log_src_id` | str | 3000 | 36 |
| `log_sub_category` | str | 3000 | 11 |
| `log_time` | int | 3000 | — |
| `log_type` | int | 3000 | — |
| `logon_type` | int | 3000 | — |
| `module` | str | 3000 | 43 |
| `ns_date` | str | 3000 | 8 |
| `object` | str | 3000 | 7 |
| `oevent_id` | int | 3000 | — |
| `offset` | str | 3000 | 10 |
| `operation` | str | 3000 | 27 |
| `os` | str | 3000 | 7 |
| `os_type` | str | 3000 | 7 |
| `os_version` | str | 3000 | 43 |
| `partition` | str | 3000 | 1 |
| `priority` | int | 3000 | — |
| `product` | str | 3000 | 13 |
| `raw_data` | str | 3000 | 904 |
| `recordnumber` | str | 3000 | 12 |
| `recv_time` | int | 3000 | — |
| `result` | str | 15 | 13 |
| `risk` | int | 3000 | — |
| `severity` | int | 3000 | — |
| `shost` | str | 3000 | 25 |
| `sinfo1` | str | 3000 | 0 |
| `sip` | str | 1 | 9 |
| `sport` | int | 1 | — |
| `suser` | str | 3000 | 28 |
| `table` | str | 3000 | 11 |
| `tag` | str | 3000 | 22 |
| `timestamp` | int | 3000 | — |
| `topic` | str | 3000 | 13 |
| `type` | str | 3000 | 4 |
| `vender` | str | 3000 | 9 |
