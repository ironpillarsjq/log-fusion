# 三类绿盟日志的 log_fusion 分表设计

设计日期：2026-10-16。**状态：设计文件已完成，未连接数据库、未执行建表、未写入日志。** 本文件是当前选定方案；此前 `schema_draft.sql` 的通用四表仅保留作历史参考，不需要同时执行。

## 目标表

按用户要求使用已有 `log_fusion` 数据库，三类分别保存，不复用心跳或归档表：

| 上游 API 类型 | 中文名称 | 目标表 | API 样例字段并集 | CSV 模板列数 | 上游字段独立列数 | 加入管理字段后总列数 |
|---|---|---|---:|---:|---:|---:|
| ipslog | 入侵防护日志 | log_fusion.nsfocus_ips_log | 70 | 100 | 110 | 118 |
| ipblacklistlog | 恶意 IP 全局黑名单日志 | log_fusion.nsfocus_ip_blacklist_log | 62 | 未提供 | 62 | 70 |
| ips_web_secure_event_log | WEB 安全日志 | log_fusion.nsfocus_web_security_log | 64 | 73 | 84 | 92 |

字段证据来自已保存的 24 小时及月度探测真实样例，以及用户手动导出文件的已核验表头。月度两个窗口可能返回同一条日志，不能将多个窗口的样例数当作多个独立日志。上述并集仍不是产品官方全量字段合同。

IPS 的 110 列是实际 API 70 字段与 CSV 模板 100 列的并集，不是声称一条 API 记录已有 110 个字段；其中 40 个仅在 CSV 表头出现。WEB 同理，20 个仅在 CSV 表头出现。CSV 有些模板列在本批全部为空，这些字段暂用可空文本列保留，不猜测数据含义。黑名单类没有对应 CSV 样例。

建表 SQL：[log_fusion_nsfocus_tables.sql](../sql/log_fusion_nsfocus_tables.sql)。逐字段 SQL 类型、样例类型、是否来自 CSV 和证据路径见 [机器可读字段映射](../sql/log_fusion_nsfocus_tables.fields.json)。后附完整字段字典供直接审阅。

## 每类保存内容

- **入侵防护**：源/目的 IP、端口、协议、规则 ruleid、检测描述 event、threat_level/threat_type、action/acted、次数 count、受影响系统 affect_os、CVE/CNNVD/CWE、原始日志 raw_data、明细 detail、报文 rawinfo 等。
- **恶意 IP 全局黑名单**：同样保留网络五元组和处理结果，另外明确保存 `ip`、`domain`、`engine`。真实样例 engine 为 DNS black list 且 domain 非空，ip 为 0.0.0.0，不能把它直接等同于源/目的 IP，也不能只按 IP 存储。
- **WEB 安全**：网络五元组、规则、攻击分类 websecure_type/threat_type、URL、动作、次数、明细、原始报文等。虽然上游类型名包含 event，这里仍是 §3.4.1 查询的日志，不是 §3.5.1 的独立安全事件。

保留上游字段名，例如 `event`、`count`、`time`、`table`、`partition`，SQL 中全部使用反引号。`ruleid` 与 `rule_id`、`protocol` 与 `proto`、`rawinfo` 与 `raw_info` 等没有验证为完全等价的字段分别存储，导入时不得擅自合并。

## 8 个本地管理列

| 列 | 类型 | 用途 |
|---|---|---|
| id | BIGINT UNSIGNED | 自增主键，与上游 log_id 分离 |
| source_instance | VARCHAR(64) | 采集配置分配的稳定产品实例编号，例如 nsfocus-site-a；不使用密码或 Token |
| upstream_index | VARCHAR(255) | API 记录外层 index；与 source.table 分别保存 |
| event_time | DATETIME(3) | source.timestamp 秒值转换成 UTC，供索引查询 |
| received_time | DATETIME(3) | source.recv_time 毫秒值转换成 UTC，可空 |
| ingested_at | DATETIME(3) | 本地入库 UTC 时间，应用显式提供 |
| raw_record | JSON | 完整 API 单条 `{index, source}`，保留所有原值、类型和未来新增字段 |
| record_sha256 | BINARY(32) | 完整记录的规范化 JSON 摘要，用于重复内容/冲突检查，不是防伪签名 |

API `timestamp`、`recv_time`、`log_time`、`time` 仍各自保存为独立 BIGINT 列；后者在真实样例中是数字字符串，导入时校验后转换。**不能互相替代这四个时间**，尤其设备 time 与平台 timestamp 已观测到不一致。

`raw_data`、`detail`、`rawinfo`、`digest`、URL 等长内容使用 LONGTEXT；digest 样例是较长编码内容，不能误用 CHAR(64) 当作普通摘要。其他未明确约束长度的非索引字符串使用 TEXT。IP 使用 VARCHAR(64) 兼容现有文本形式，端口使用 SMALLINT UNSIGNED。数值列为空时写 NULL，真实 0 保留为 0。

完整 JSON 与独立列有意重复存储：独立列便于查询和按类型导出，JSON 作为完整采样快照及未知字段兜底。它会增加数百万条日志的存储量，部署前必须实测平均行大小、索引空间和保留周期，不能仅凭单条样例给出准确磁盘估算。JSON 不保证保留 HTTP 原始字节、键序或空白。

## 去重、索引和查询

三张表统一唯一键：`(source_instance, upstream_index, log_id)`。相同产品实例在不同窗口重复拉到同一条日志，不应新增记录。不同设备、不同索引不能只按 log_id 全局去重。

初始索引：

1. 产品实例 + event_time：按时间查询与后续分段同步。
2. 产品实例 + sip + event_time：按源 IP 查行为。
3. 产品实例 + dip + event_time：按目的 IP 查受影响目标。
4. 产品实例 + ruleid + event_time：按规则查询。
5. 黑名单表额外有产品实例 + domain 的前 191 字符 + event_time 前缀索引，查询仍比较完整域名值。

不为 raw_data、rawinfo、URL 或全部模板列建立索引，不预设所有字段都需要高频过滤。威胁等级与 action/acted 先保留原始数值；不要套用事件接口的 risk=1/3/5 映射。

示例查询（会话时区展示不改变 event_time 的 UTC 存储约定）：

```sql
SELECT `event_time`, `sip`, `sport`, `dip`, `dport`, `event`, `ruleid`, `threat_level`
FROM `log_fusion`.`nsfocus_ips_log`
WHERE `source_instance` = 'nsfocus-site-a'
  AND `event_time` >= '2026-10-01 00:00:00'
  AND `event_time` < '2026-10-02 00:00:00'
ORDER BY `event_time` DESC
LIMIT 100;
```

## 入库契约（尚未实现导入器）

1. 只按 API 的 type 路由到固定的三张表，不使用外部值直接拼接任意 SQL 表名；参数值通过数据库驱动绑定。
2. 校验记录 index/source.table 与请求类别，log_id 非空；配置提供 source_instance，API 外层提供 upstream_index。
3. 空字符串投影为 NULL（log_id 不允许空），完整 JSON 保留原始空串；整数严格解析，不将非数字文本写进整数列，也不把空值变为 0。端口必须在 0..65535。BIGINT 需校验有符号或无符号范围。
4. Unix 秒/毫秒分别转换为 UTC DATETIME(3)，不要直接写 ISO 带 Z 或偏移后缀的字符串。应用负责时区，不能依赖服务器当前本地时间。
5. CSV 的 timestamp/recv_time 是日期文本，必须经已确认的导出时区转换；不能照 API 数字规则解析。CSV 无 index，必须先建立可靠命名空间映射；本次 SQL 不是直接 LOAD DATA CSV 的脚本。
6. 逐列仅写表字典允许的字段；未知字段仍保留于 raw_record，不能触发 Unknown column。字符串超长、整数非法、日期错误时保留原始响应并记录拒绝原因，不截断、不忽略错误。
7. `record_sha256` 定义为 UTF-8 编码的 `json.dumps(raw_record, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)` 的 SHA-256 二进制值。不可将上游 digest 当作这个值。
8. 相同唯一键、相同摘要：幂等跳过。相同唯一键、不同摘要：保留首次内容和本次响应文件并报冲突，不能默认 REPLACE 或静默覆盖。
9. 一页内分批事务写入；全部成功才推进同步进度。API partOfData=true、失败、分页深度受限等不能当作完整同步成功。当前 API 拉取程序仍只写本地文件。

## 执行顺序、兼容性、回滚和影响

- **当前只交付设计，不执行下面操作。** SQL 显式使用 `log_fusion`.`表名`，不依赖默认数据库、不建库、不包含用户凭据。
- 目标 MySQL 8.0+、InnoDB、utf8mb4、ROW_FORMAT=DYNAMIC；实际服务版本、页大小、max_allowed_packet、磁盘、权限与现有同名表均未核验。字符串长度是设计容量，不是从少量样例推断出的产品上限。
- 上线顺序：只读检查目标库和同名表 → 在独立测试实例的同名 log_fusion 库建三张空表 → 导入保存样例并验证空值、时间、去重、长文本和查询 → 审核生产容量与权限 → 执行三条 CREATE TABLE → 再部署独立导入器。MySQL DDL 不是包一层事务就能整体回滚，第三张失败时需检查前两张是否已存在。
- SQL 不使用 IF NOT EXISTS，不静默掩盖同名不兼容表；再次执行前应核验结构。现有 client_status、archive_log 和遗留 log_hash 均不修改，不加触发器、不变更现有 ORM create_all。
- 无外键到主服务表，三张日志表可独立创建和验证。若仅创建了空表且需要回退，经确认后只删除本次新建的三张表；已经写入时必须先导出备份并停导入，不自动删除。此交付不提供或执行破坏性回滚命令。
- 同库大批量写入可能争用心跳/归档资源；正式采集使用独立账号和连接池并控制事务及速率。已有存证归档任务不会自动管理这三张表，它们的保留期和归档策略待确定，不默认删除三个月前日志。
- 本次没有运行真实 MySQL 建表或写入检查，也没有修改 FastAPI 路由、现有存证口径和前端。无实际数据库结构变化，无实际接口行为变化。

语法和数据类型参考：[MySQL CREATE TABLE](https://dev.mysql.com/doc/refman/8.0/en/create-table.html)、[JSON 类型](https://dev.mysql.com/doc/refman/8.0/en/json.html)。

## 本次离线验证

- 三张表仅有 CREATE TABLE 语句，均显式限定 log_fusion，不含建库、DROP、ALTER、INSERT、DELETE 等其他数据库操作。
- 表列数分别为 118/70/92，每个来源字段都能对应到独立列，8 个管理列与来源列没有重名。IPS 的 100 个、WEB 的 73 个 CSV 表头均被覆盖。
- 对照保存的 9 条样例响应记录（重复窗口去重后 6 条独立日志），检查字段覆盖、已观测字符串长度、整数范围、端口范围、秒/毫秒转 UTC 和规范化摘要一致性，全部通过。
- 校验唯一键字段及声明长度预算、Python 语法；仓库文档一致性检查 4 项通过。
- 这些是静态及离线数据验证，不是实际 MySQL 的建表成功证明；没有执行数据库语法或写入测试。

## 完整字段字典

下列表格包含 API 真实样例字段与已提供 CSV 的模板扩展字段；“仅 CSV”字段可空且类型仍待非空数据核实。

### log_fusion.nsfocus_ips_log

来源列 110 个，另加前述 8 个本地管理列。

| 字段（上游同名） | SQL 类型 | 来源证据 | 可空 | 说明 |
|---|---|---|---|---|
| `acted` | BIGINT | API + CSV | 是 | 实际处理动作原值，不能与 action 合并 |
| `action` | BIGINT | API + CSV | 是 | 动作原值，不猜测 1/3/5 的中文含义 |
| `action_mapping` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `affect_os` | TEXT | API + CSV | 是 | API 字段 affect_os |
| `app_id` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `app_name` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `ar` | BIGINT | API + CSV | 是 | API 字段 ar |
| `asset_name` | TEXT | API + CSV | 是 | API 字段 asset_name |
| `attack_type` | TEXT | API + CSV | 是 | 上游字符串分类，不强制假定为整数枚举 |
| `breaking_sighn` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `card` | TEXT | API + CSV | 是 | API 字段 card |
| `cascade_node_id` | VARCHAR(128) | API + CSV | 是 | API 字段 cascade_node_id |
| `cdnip` | VARCHAR(64) | API + CSV | 是 | API 字段 cdnip |
| `cnnvd_id` | TEXT | API + CSV | 是 | API 字段 cnnvd_id |
| `collector_id` | VARCHAR(128) | API + CSV | 是 | API 字段 collector_id |
| `count` | BIGINT UNSIGNED | API + CSV | 是 | 次数；API 样例为数字字符串，非空时转非负整数 |
| `cve_id` | TEXT | API + CSV | 是 | API 字段 cve_id |
| `cwe_id` | TEXT | API + CSV | 是 | API 字段 cwe_id |
| `danger_degree` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `dcity` | TEXT | API + CSV | 是 | API 字段 dcity |
| `dcountry` | TEXT | API + CSV | 是 | API 字段 dcountry |
| `detail` | LONGTEXT | API + CSV | 是 | 详细内容，与 raw_data 分别保留 |
| `dev_id` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `dev_ip` | VARCHAR(64) | API + CSV | 是 | API 字段 dev_ip |
| `device_type` | VARCHAR(64) | API + CSV | 是 | API 字段 device_type |
| `digest` | LONGTEXT | API + CSV | 是 | 上游 digest，可能是较长内容编码，不当作固定长度哈希 |
| `dip` | VARCHAR(64) | API + CSV | 是 | API 字段 dip |
| `dip_int` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `direction` | TEXT | API + CSV | 是 | API 字段 direction |
| `dmac` | TEXT | API + CSV | 是 | API 字段 dmac |
| `dport` | SMALLINT UNSIGNED | API + CSV | 是 | 目的端口；非空时转整数并校验 0..65535 |
| `ds` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `dst_addr` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `dst_asset` | BIGINT | API + CSV | 是 | API 字段 dst_asset |
| `dst_port` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `dzonename` | TEXT | API + CSV | 是 | API 字段 dzonename |
| `event` | TEXT | API + CSV | 是 | 上游日志名称/检测描述，并非事件接口 eventNsuid |
| `extension` | LONGTEXT | API + CSV | 是 | API 字段 extension |
| `facility` | BIGINT | API | 是 | API 字段 facility |
| `gr_danger` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_danger_mapping` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_os` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_pop` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_service` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_servicer` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_tech` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_type` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `gr_type_mapping` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `hash` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `key1` | TEXT | API + CSV | 是 | API 字段 key1 |
| `last_times` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `log_date` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `log_id` | VARCHAR(128) | API + CSV | 否 | 上游日志 ID；必填，按原值保存 |
| `log_source_id` | VARCHAR(128) | API + CSV | 是 | 日志来源 ID，不等同于产品实例编号 |
| `log_src_id` | VARCHAR(128) | API | 是 | 上游 log_src_id，按原值保留 |
| `log_time` | BIGINT | API | 是 | API log_time 秒值 |
| `log_type` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `loginip` | VARCHAR(64) | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `manufacturers` | TEXT | API + CSV | 是 | API 字段 manufacturers |
| `module` | BIGINT | API + CSV | 是 | API 字段 module |
| `msel` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `msg` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `msgtype` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `ns_date` | TEXT | API | 是 | API 字段 ns_date |
| `offset` | TEXT | API | 是 | API 字段 offset |
| `oper_result` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `partition` | TEXT | API | 是 | API 字段 partition |
| `platform_gid` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `platform_id` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `policy_id` | BIGINT | API + CSV | 是 | API 字段 policy_id |
| `popular` | TEXT | API + CSV | 是 | API 字段 popular |
| `priority` | BIGINT | API | 是 | API 字段 priority |
| `product` | TEXT | API + CSV | 是 | API 字段 product |
| `proto` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `protocol` | VARCHAR(32) | API + CSV | 是 | API 字段 protocol |
| `pversion` | TEXT | API + CSV | 是 | API 字段 pversion |
| `raw_data` | LONGTEXT | API + CSV | 是 | API 原始日志文本；CSV 导出样例该列为空 |
| `raw_info` | LONGTEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `raw_len` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `rawinfo` | LONGTEXT | API + CSV | 是 | 原始报文/内容字符串，不解码或截短 |
| `rawlen` | BIGINT | API + CSV | 是 | API 字段 rawlen |
| `recv_time` | BIGINT | API + CSV | 是 | API Unix 毫秒接收时间戳 |
| `rule_id` | TEXT | 仅 CSV 模板 | 是 | CSV 导出模板列，与 ruleid 的别名关系未验证 |
| `ruleid` | BIGINT | API + CSV | 是 | 上游 ruleid；不要未经核对合并 CSV 的 rule_id |
| `scity` | TEXT | API + CSV | 是 | API 字段 scity |
| `scountry` | TEXT | API + CSV | 是 | API 字段 scountry |
| `security_name` | VARCHAR(128) | API + CSV | 是 | API 字段 security_name |
| `service` | TEXT | API + CSV | 是 | API 字段 service |
| `severity` | BIGINT | API | 是 | 上游 severity 原值，不套用事件接口 risk 映射 |
| `sip` | VARCHAR(64) | API + CSV | 是 | API 字段 sip |
| `sip_int` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `smac` | TEXT | API + CSV | 是 | API 字段 smac |
| `smt_user` | TEXT | API + CSV | 是 | API 字段 smt_user |
| `snapshot` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `sport` | SMALLINT UNSIGNED | API + CSV | 是 | 源端口；非空时转整数并校验 0..65535 |
| `src_addr` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `src_asset` | BIGINT | API + CSV | 是 | API 字段 src_asset |
| `src_port` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `szonename` | TEXT | API + CSV | 是 | API 字段 szonename |
| `table` | VARCHAR(128) | API | 是 | source.table，与 API 外层 index 分别保留 |
| `threat_level` | BIGINT | API + CSV | 是 | 上游威胁等级原值，未确认统一中文等级 |
| `threat_type` | VARCHAR(255) | API + CSV | 是 | API 字段 threat_type |
| `time` | BIGINT | API + CSV | 是 | 设备上报 time；API 样例为秒值字符串，与 timestamp 独立保留 |
| `timestamp` | BIGINT | API + CSV | 是 | API Unix 秒时间戳，不是 MySQL TIMESTAMP 类型 |
| `topic` | TEXT | API | 是 | API 字段 topic |
| `type` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `user` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `user_name` | TEXT | API + CSV | 是 | API 字段 user_name |
| `vender` | TEXT | API + CSV | 是 | 上游厂商字段拼写原样保留 |
| `vid` | BIGINT | API + CSV | 是 | API 字段 vid |

### log_fusion.nsfocus_ip_blacklist_log

来源列 62 个，另加前述 8 个本地管理列。

| 字段（上游同名） | SQL 类型 | 来源证据 | 可空 | 说明 |
|---|---|---|---|---|
| `acted` | BIGINT | API | 是 | 实际处理动作原值，不能与 action 合并 |
| `action` | BIGINT | API | 是 | 动作原值，不猜测 1/3/5 的中文含义 |
| `ar` | BIGINT | API | 是 | API 字段 ar |
| `asset_name` | TEXT | API | 是 | API 字段 asset_name |
| `card` | TEXT | API | 是 | API 字段 card |
| `cascade_node_id` | VARCHAR(128) | API | 是 | API 字段 cascade_node_id |
| `cdnip` | VARCHAR(64) | API | 是 | API 字段 cdnip |
| `collector_id` | VARCHAR(128) | API | 是 | API 字段 collector_id |
| `count` | BIGINT UNSIGNED | API | 是 | 次数；API 样例为数字字符串，非空时转非负整数 |
| `dcity` | TEXT | API | 是 | API 字段 dcity |
| `dcountry` | TEXT | API | 是 | API 字段 dcountry |
| `detail` | LONGTEXT | API | 是 | 详细内容，与 raw_data 分别保留 |
| `dev_ip` | VARCHAR(64) | API | 是 | API 字段 dev_ip |
| `device_type` | VARCHAR(64) | API | 是 | API 字段 device_type |
| `dip` | VARCHAR(64) | API | 是 | API 字段 dip |
| `direction` | TEXT | API | 是 | API 字段 direction |
| `dmac` | TEXT | API | 是 | API 字段 dmac |
| `domain` | VARCHAR(1024) | API | 是 | 检测域名；黑名单日志不只包含 IP |
| `dport` | SMALLINT UNSIGNED | API | 是 | 目的端口；非空时转整数并校验 0..65535 |
| `dst_asset` | BIGINT | API | 是 | API 字段 dst_asset |
| `dzonename` | TEXT | API | 是 | API 字段 dzonename |
| `engine` | TEXT | API | 是 | 检测引擎名称 |
| `event` | TEXT | API | 是 | 上游日志名称/检测描述，并非事件接口 eventNsuid |
| `facility` | BIGINT | API | 是 | API 字段 facility |
| `ip` | VARCHAR(64) | API | 是 | 上游 ip 原值，允许保留 0.0.0.0，不用 sip/dip 替代 |
| `key1` | TEXT | API | 是 | API 字段 key1 |
| `log_id` | VARCHAR(128) | API | 否 | 上游日志 ID；必填，按原值保存 |
| `log_source_id` | VARCHAR(128) | API | 是 | 日志来源 ID，不等同于产品实例编号 |
| `log_src_id` | VARCHAR(128) | API | 是 | 上游 log_src_id，按原值保留 |
| `log_time` | BIGINT | API | 是 | API log_time 秒值 |
| `manufacturers` | TEXT | API | 是 | API 字段 manufacturers |
| `module` | BIGINT | API | 是 | API 字段 module |
| `ns_date` | TEXT | API | 是 | API 字段 ns_date |
| `offset` | TEXT | API | 是 | API 字段 offset |
| `partition` | TEXT | API | 是 | API 字段 partition |
| `policy_id` | BIGINT | API | 是 | API 字段 policy_id |
| `priority` | BIGINT | API | 是 | API 字段 priority |
| `product` | TEXT | API | 是 | API 字段 product |
| `protocol` | VARCHAR(32) | API | 是 | API 字段 protocol |
| `pversion` | TEXT | API | 是 | API 字段 pversion |
| `raw_data` | LONGTEXT | API | 是 | API 原始日志文本；CSV 导出样例该列为空 |
| `recv_time` | BIGINT | API | 是 | API Unix 毫秒接收时间戳 |
| `ruleid` | BIGINT | API | 是 | 上游 ruleid；不要未经核对合并 CSV 的 rule_id |
| `scity` | TEXT | API | 是 | API 字段 scity |
| `scountry` | TEXT | API | 是 | API 字段 scountry |
| `security_name` | VARCHAR(128) | API | 是 | API 字段 security_name |
| `severity` | BIGINT | API | 是 | 上游 severity 原值，不套用事件接口 risk 映射 |
| `sip` | VARCHAR(64) | API | 是 | API 字段 sip |
| `smac` | TEXT | API | 是 | API 字段 smac |
| `smt_user` | TEXT | API | 是 | API 字段 smt_user |
| `sport` | SMALLINT UNSIGNED | API | 是 | 源端口；非空时转整数并校验 0..65535 |
| `src_asset` | BIGINT | API | 是 | API 字段 src_asset |
| `szonename` | TEXT | API | 是 | API 字段 szonename |
| `table` | VARCHAR(128) | API | 是 | source.table，与 API 外层 index 分别保留 |
| `threat_level` | BIGINT | API | 是 | 上游威胁等级原值，未确认统一中文等级 |
| `threat_type` | VARCHAR(255) | API | 是 | API 字段 threat_type |
| `time` | BIGINT | API | 是 | 设备上报 time；API 样例为秒值字符串，与 timestamp 独立保留 |
| `timestamp` | BIGINT | API | 是 | API Unix 秒时间戳，不是 MySQL TIMESTAMP 类型 |
| `topic` | TEXT | API | 是 | API 字段 topic |
| `user_name` | TEXT | API | 是 | API 字段 user_name |
| `vender` | TEXT | API | 是 | 上游厂商字段拼写原样保留 |
| `vid` | BIGINT | API | 是 | API 字段 vid |

### log_fusion.nsfocus_web_security_log

来源列 84 个，另加前述 8 个本地管理列。

| 字段（上游同名） | SQL 类型 | 来源证据 | 可空 | 说明 |
|---|---|---|---|---|
| `acted` | BIGINT | API + CSV | 是 | 实际处理动作原值，不能与 action 合并 |
| `action` | BIGINT | API + CSV | 是 | 动作原值，不猜测 1/3/5 的中文含义 |
| `action_mapping` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `affect_os` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `ar` | BIGINT | API + CSV | 是 | API 字段 ar |
| `asset_name` | TEXT | API + CSV | 是 | API 字段 asset_name |
| `attack_type` | TEXT | 仅 CSV 模板 | 是 | 上游字符串分类，不强制假定为整数枚举 |
| `card` | TEXT | API + CSV | 是 | API 字段 card |
| `cascade_node_id` | VARCHAR(128) | API + CSV | 是 | API 字段 cascade_node_id |
| `cdnip` | VARCHAR(64) | API + CSV | 是 | API 字段 cdnip |
| `cnnvd_id` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `collector_id` | VARCHAR(128) | API + CSV | 是 | API 字段 collector_id |
| `count` | BIGINT UNSIGNED | API + CSV | 是 | 次数；API 样例为数字字符串，非空时转非负整数 |
| `cve_id` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `cwe_id` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `dcity` | TEXT | API + CSV | 是 | API 字段 dcity |
| `dcountry` | TEXT | API + CSV | 是 | API 字段 dcountry |
| `detail` | LONGTEXT | API + CSV | 是 | 详细内容，与 raw_data 分别保留 |
| `dev_id` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `dev_ip` | VARCHAR(64) | API + CSV | 是 | API 字段 dev_ip |
| `device_type` | VARCHAR(64) | API + CSV | 是 | API 字段 device_type |
| `digest` | LONGTEXT | API + CSV | 是 | 上游 digest，可能是较长内容编码，不当作固定长度哈希 |
| `dip` | VARCHAR(64) | API + CSV | 是 | API 字段 dip |
| `direction` | TEXT | API + CSV | 是 | API 字段 direction |
| `dmac` | TEXT | API + CSV | 是 | API 字段 dmac |
| `dport` | SMALLINT UNSIGNED | API + CSV | 是 | 目的端口；非空时转整数并校验 0..65535 |
| `dst_asset` | BIGINT | API + CSV | 是 | API 字段 dst_asset |
| `dzonename` | TEXT | API + CSV | 是 | API 字段 dzonename |
| `event` | TEXT | API + CSV | 是 | 上游日志名称/检测描述，并非事件接口 eventNsuid |
| `extension` | LONGTEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `facility` | BIGINT | API | 是 | API 字段 facility |
| `gr_type_mapping` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `hash` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `key1` | TEXT | API + CSV | 是 | API 字段 key1 |
| `log_id` | VARCHAR(128) | API + CSV | 否 | 上游日志 ID；必填，按原值保存 |
| `log_source_id` | VARCHAR(128) | API + CSV | 是 | 日志来源 ID，不等同于产品实例编号 |
| `log_src_id` | VARCHAR(128) | API | 是 | 上游 log_src_id，按原值保留 |
| `log_time` | BIGINT | API | 是 | API log_time 秒值 |
| `log_type` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `loginip` | VARCHAR(64) | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `manufacturers` | TEXT | API + CSV | 是 | API 字段 manufacturers |
| `module` | BIGINT | API + CSV | 是 | API 字段 module |
| `msg` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `ns_date` | TEXT | API | 是 | API 字段 ns_date |
| `offset` | TEXT | API | 是 | API 字段 offset |
| `oper_result` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `partition` | TEXT | API | 是 | API 字段 partition |
| `policy_id` | BIGINT | API + CSV | 是 | API 字段 policy_id |
| `popular` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `priority` | BIGINT | API | 是 | API 字段 priority |
| `product` | TEXT | API + CSV | 是 | API 字段 product |
| `protocol` | VARCHAR(32) | API + CSV | 是 | API 字段 protocol |
| `pversion` | TEXT | API + CSV | 是 | API 字段 pversion |
| `raw_data` | LONGTEXT | API + CSV | 是 | API 原始日志文本；CSV 导出样例该列为空 |
| `rawinfo` | LONGTEXT | API + CSV | 是 | 原始报文/内容字符串，不解码或截短 |
| `rawlen` | BIGINT | API + CSV | 是 | API 字段 rawlen |
| `recv_time` | BIGINT | API + CSV | 是 | API Unix 毫秒接收时间戳 |
| `rule_id` | TEXT | 仅 CSV 模板 | 是 | CSV 导出模板列，与 ruleid 的别名关系未验证 |
| `ruleid` | BIGINT | API + CSV | 是 | 上游 ruleid；不要未经核对合并 CSV 的 rule_id |
| `scity` | TEXT | API + CSV | 是 | API 字段 scity |
| `scountry` | TEXT | API + CSV | 是 | API 字段 scountry |
| `security_name` | VARCHAR(128) | API + CSV | 是 | API 字段 security_name |
| `service` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `severity` | BIGINT | API | 是 | 上游 severity 原值，不套用事件接口 risk 映射 |
| `sip` | VARCHAR(64) | API + CSV | 是 | API 字段 sip |
| `smac` | TEXT | API + CSV | 是 | API 字段 smac |
| `smt_user` | TEXT | API + CSV | 是 | API 字段 smt_user |
| `sport` | SMALLINT UNSIGNED | API + CSV | 是 | 源端口；非空时转整数并校验 0..65535 |
| `src_asset` | BIGINT | API + CSV | 是 | API 字段 src_asset |
| `szonename` | TEXT | API + CSV | 是 | API 字段 szonename |
| `table` | VARCHAR(128) | API | 是 | source.table，与 API 外层 index 分别保留 |
| `threat_level` | BIGINT | API + CSV | 是 | 上游威胁等级原值，未确认统一中文等级 |
| `threat_type` | VARCHAR(255) | API + CSV | 是 | API 字段 threat_type |
| `time` | BIGINT | API + CSV | 是 | 设备上报 time；API 样例为秒值字符串，与 timestamp 独立保留 |
| `timestamp` | BIGINT | API + CSV | 是 | API Unix 秒时间戳，不是 MySQL TIMESTAMP 类型 |
| `topic` | TEXT | API | 是 | API 字段 topic |
| `uri` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `url` | LONGTEXT | API + CSV | 是 | 上游完整 URL，不截断 |
| `user` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `user_name` | TEXT | API + CSV | 是 | API 字段 user_name |
| `vender` | TEXT | API + CSV | 是 | 上游厂商字段拼写原样保留 |
| `version` | TEXT | 仅 CSV 模板 | 是 | CSV 模板扩展字段；实际类型未确认，暂保留文本 |
| `vid` | BIGINT | API + CSV | 是 | API 字段 vid |
| `websecure_type` | TEXT | API | 是 | WEB 安全分类原值 |
