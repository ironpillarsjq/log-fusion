# WAF 与 DAS Syslog 接入

当前实现（2026-10-28）：FastAPI 生命周期内的接收器支持 TCP/UDP，将消息先提交到本地 SQLite 缓冲，再解析并写入现有 `LOG_FUSION_DB`（默认 `log_fusion`）中的六张表。没有修改 Linux/Windows HTTP 采集协议、API 轮询安全日志采集、现有存证算法或前端。执行 run.py（或现有启动服务脚本）会自动启动 WAF/DAS 接收器，退出/重载时一并释放；导入模块不会监听端口。MySQL 六表仍须显式建表。

## 1. 建表与兼容性

先审查并在目标环境显式执行 [六表 SQL](sql/device_syslog.sql)。脚本默认 `USE log_fusion`，如果环境变量配置了其他库名，执行前须同步调整。脚本不创建数据库，不操作现有业务表；`IF NOT EXISTS` 不负责校验/升级已有同名表，执行后应核验 `SHOW CREATE TABLE` 与脚本一致。

| 表 | 接收类型 |
|---|---|
| `waf_audit_log` | `waf_log_login`、`waf_log_op`、`waf_log_system_run`、`waf_log_wafstat` |
| `waf_security_log` | `waf_log_l4acl`、`waf_log_ddos`、`waf_log_deface`、`waf_log_webaccess`、`waf_log_arp`、`waf_log_websec`、`waf_log_ipblock`、`waf_log_traffic_event`、`waf_log_session` |
| `waf_traffic_log` | `waf_log_site_traffic`、`waf_log_bps_pps` |
| `das_statement_log` | DAS JSON `audit`；旧版中文 `[CEF]:` 业务告警 |
| `das_session_log` | DAS JSON `session` |
| `das_sensitive_log` | DAS JSON `warn` |

字段事实源为 `backend/app/device_log_catalog.py` 的 `COMMON` / `BUSINESS` / `TABLES`；SQL 与该定义的一致性由 `tests/test_syslog.py` 检查。沿用厂商字段名，如 WAF `user/desc/type/source/cpu/mem`，DAS `ss_action/ss_result/swarn_id`；不增加截图中的 `acted` 等报文未提供字段。保留拼写 `conflit_mac`；兼容 `characters/charaters`、`api_event_type/api_event_typ`、敏感日志 `table_name/table_names`。每条记录均保存 `log_type`。

六张表都有 `raw_log LONGTEXT NOT NULL`（完整 UTF-8 消息文本）、`raw_bytes LONGBLOB NOT NULL`（完整消息字节）、`raw_sha256 BINARY(32)`、`payload JSON` 和处理元数据。这里的“消息”是一次 UDP 载荷，或完成 TCP 分帧后的消息；TCP 长度前缀、LF/CRLF 分帧结束符不属于消息正文。UDP 的末尾空白/控制字符保留；TCP octet-counting 消息内的空白、换行和控制字符同样保留。文本、原始字节和解析 JSON 同时保存，会增加存储量，应按真实流量规划容量。

`raw_sha256` 是原文字节摘要，不是现有 `log_hash` 存证算法，不提供来源认证，也没有唯一约束；重复内容可能是不同的合法事件。`ingest_id` 是首次落盘生成的 UUID，重放保持不变且在每表唯一。没有把 `ssid`、`correlation_id` 或设备 ID 当成日志唯一 ID，也没有在缺少实测依据时按 DAS 原生 ID 去重。

迁移顺序：停止主服务或明确关闭对应 Syslog 接收器 → 在现有库执行新增六表 SQL → 以应用写入账号启动接收器 → 本地/设备联调。已有 Linux/Windows、心跳、API 安全日志不依赖这六表。回滚先停接收器并保留其 SQLite 文件及六表；回退代码不会影响原有应用。若需删除六表，先备份数据，再单独审查显式 DROP 操作；本仓库不提供自动破坏性回滚。

本次开发没有连接真实 MySQL、没有执行以上 DDL。表尚未创建时接收仍可落盘，写库失败保留为 pending，待手动建表后重试。

## 2. 随主服务启动与配置

使用安装了项目依赖的 Python，在 `backend/` 执行 `python -B run.py`，或按原流程运行根目录启动脚本即可；不需要额外开启两个接收进程。`run.py` 的 Uvicorn worker 启动 FastAPI lifespan 时，先绑定 WAF/DAS TCP/UDP 端口，再完成应用启动。reload 监控父进程不监听 Syslog；只允许单 worker。应用退出/重载时关闭监听、等待已接收原文落盘、结束当前在途写库（不继续消费整批积压），随后释放缓冲和连接池；下一次启动自动继续 pending。

默认 WAF 为 `0.0.0.0:5514`，DAS 为 `0.0.0.0:5515`，都监听 TCP 和 UDP。设备目的地址填接收机器实际 IP，不填 `0.0.0.0`，也不填 HTTP 8080 端口。设备可用协议和需要发送的日志类型还须在设备管理界面开启。接收器不自动修改防火墙，应允许选用的 5514/5515 TCP/UDP 入站。

通过环境变量或 `backend/.env` 修改； `.env` 按工作目录读取，标准脚本已切换到 backend。基础示例：

```dotenv
SYSLOG_ENABLED=true
SYSLOG_HOST=0.0.0.0
WAF_SYSLOG_ENABLED=true
WAF_SYSLOG_PORT=5514
WAF_SYSLOG_SOURCE_INSTANCE=waf-site-a
WAF_SYSLOG_PROTOCOLS=both
WAF_SYSLOG_TIMEZONE=+08:00
DAS_SYSLOG_ENABLED=true
DAS_SYSLOG_PORT=5515
DAS_SYSLOG_SOURCE_INSTANCE=das-site-a
DAS_SYSLOG_PROTOCOLS=both
DAS_SYSLOG_TIMEZONE=+08:00
```

来源过滤列表使用 JSON 数组，例如 `WAF_SYSLOG_ALLOW_SOURCES=["192.0.2.10/32"]`，DAS 对应 `DAS_SYSLOG_ALLOW_SOURCES`；示例地址须替换为实际发送端 IP。默认空数组表示不限制；过滤地址不是密码认证。可设 `WAF_SYSLOG_ENABLED=false` 或 `DAS_SYSLOG_ENABLED=false` 单独关闭；`SYSLOG_ENABLED=false` 则全部关闭，不创建监听、缓冲或接收器数据库连接池。

各产品前缀支持 `ENABLED`、`PORT`、`SOURCE_INSTANCE`、`PROTOCOLS`、`TIMEZONE`、`FRAMING`（默认 auto）、`BODY_BASE64`（默认 false）、`ALLOW_SOURCES`。全局还支持 `SYSLOG_SPOOL_DIR`（仓库根 data/syslog）、`SYSLOG_MAX_BYTES`（1048576）、`SYSLOG_MAX_SPOOL_BYTES`（每实例 1073741824）、`SYSLOG_IDLE_TIMEOUT`（60 秒）、`SYSLOG_MAX_CONNECTIONS`（每实例 64）。端口要求 1～65535，布尔/列表/协议参数由配置模型校验。

启用的接收器遇端口占用、目录不可写或非法配置时会使应用启动失败，并清理已绑定端口；不能只启动 HTTP 却假装接收器正常。若之前启动过独立 CLI，应先停止它。MySQL 暂不可用/六表未建不阻止接收启动，原文仍落缓冲，写库错误按 pending 重试。

UDP 接收兼容标准 asyncio 与 uvloop：优先使用 `loop.sock_recvfrom()`，遇 `NotImplementedError` 时改用 `add_reader()` 注册可读事件、通过非阻塞 socket `recvfrom(65535)` 读取整条报文；每次等待结束/取消都移除 reader。启动时先运行一次接收任务再报告就绪；运行中任务异常会立即在 `uvicorn.error` 记录产品、端口、异常类型及堆栈，并关闭失效 UDP socket。退出不再因已失败的 UDP 任务而跳过后续清理。不需要更改 run.py 或禁用 uvloop。

可选 CLI 继续用于状态和重放，也可作为高级场景的独立启动方式；独立监听之前应关闭应用内对应接收器，不能两个进程共用同一实例缓冲。现有 `deploy/log-fusion.service` 会随 run.py 一起管理接收器，无须额外注册两个服务；网页一键更新会一起重启监听，重启间隙尤其 UDP 仍可能丢数据。

以下参数表仅适用于可选 CLI `python -B -m app.syslog_server`，应用内接收使用上面的环境变量，不读取 CLI 参数：

| 参数 | 默认与行为 |
|---|---|
| `--product` | 必填 `waf` / `das`；不凭正文中的 IP 猜产品 |
| `--source-instance` | 必填，稳定实例名，1～64 个字母/数字/下划线/点/冒号/连字符 |
| `--host` / `--port` | `0.0.0.0` / `5514`；host 使用数字 IPv4/IPv6，DAS 建议显式 5515 |
| `--protocols` | `both`，也可 `udp` / `tcp` |
| `--timezone` | `+08:00`；支持 UTC、数字偏移、可用的 IANA 时区（后者依赖系统 tzdata） |
| `--framing` | `auto`：数字开头按长度前缀，其余按 LF/CRLF；可强制 `octet` / `line` |
| `--body-base64` | 默认关闭；显式开启后解码剥离消息头后的整体正文，不修改 raw_log/raw_bytes |
| `--max-bytes` | 1048576，单条 TCP/UDP 消息处理上限；UDP 仍受实际数据报长度/网络分片限制 |
| `--max-spool-bytes` | 1073741824，本地所有 pending+quarantine 原文的总字节预算，不是 SQLite 文件磁盘大小上限 |
| `--spool` | 默认仓库根 `data/syslog/<product>-<source-instance>.sqlite3`，冒号文件名编码为 `%3A`；相对路径固定相对仓库根 |
| `--idle-timeout` | 60 秒，每次 TCP 读的等待上限，超时关闭连接 |
| `--max-connections` | 64，超过上限的新 TCP 连接拒绝 |
| `--allow-source` | 不传表示不限来源，支持重复指定 IP/CIDR |
| `--status` | 只打印本地缓冲状态，不监听端口、不连接 MySQL |
| `--replay-only` | 只处理 pending，不监听端口；写库失败退出码 1 |
| `--retry-quarantine` | 显式把 quarantine 改为 pending；应修复规则后与 status/replay-only 使用 |

## 3. 产品解析约定

- 消息头支持 RFC3164/RFC5424（包括多段 structured-data 和 MSG BOM），也兼容厂商样例中的无头正文、主机/程序前缀。旧 DAS 文档的非标准 `<-1>` 保留为异常元数据，不臆造 facility/severity。TCP 同时支持 RFC6587 字节长度分帧及 LF/CRLF 分帧；一条消息可能跨多次读，一次读也可能包含多条消息。带真实换行的正文应采用长度分帧；不猜测无结束符的 EOF 残帧。
- WAF 根据 15 个 tag 路由，按已知字段名边界提取，允许字段值内普通空格、IPv6、URL、中文；Web 访问支持默认与 ECLF。未知新增字段始终在 raw_log/raw_bytes，未必能在非结构化文本中可靠识别为独立 payload 字段。重复已知字段边界可能有歧义，保守隔离，避免悄悄覆盖。
- WAF DDoS 的多源 IP 转为 `src_ips` JSON 数组，不伪造单一 src_ip；`count_num` 是匹配次数，不是日志条数。`req_bps` 的厂商单位是 bytes/second，不直接标为 bit/s。流量控制 rx_rate 等未在文档确认的单位不自行映射。
- `http/alertinfo/characters/wci/wsi` 按来源格式原值保存。`decoded_waf_fields()` 是可选详情辅助函数，按类型解码，异常 Base64 返回该字段 None，不修改存储原文，也不执行 HTTP/SQL 内容。
- DAS 新版 JSON 只接受 `audit/session/warn`；未知数值型 log_type、顶层数组、重复 JSON 键、NaN/Infinity、孤立 Unicode 代理项、超过 64 层的嵌套均隔离，不猜测类别。语句、会话、敏感日志按厂商原生字段分表；大 ID 保存字符串。会话字段与模板冲突时按说明字段保存，缺 ssid/ss_action/ss_result 时标 partial，保留完整 payload。
- 旧 DAS `[CEF]:` 是中文标签格式，映射为语句告警；`命中规则` 存 rule_name，不伪造 rule_id；`数据库名称` 与服务实例 db_name 分别保存。按已知中文标签切分，普通 SQL `|` 不分割；若正文出现重复标签导致无法消歧则隔离。这条链路只代表旧版已通知的告警，不能据此声称采到了全部 SQL。
- 事件时间优先取产品 stat_time/capture_time；没有产品时间时才回退到消息头，并标 partial。无时区时间按 `--timezone` 转 UTC；RFC3164 缺年份时选接收时刻附近最近的一年。产品时间有值但非法时保留 NULL 和错误，不用接收时间冒充事件时间。received_at 单独保存真实接收时间。
- 字段超宽、端口超界、无效数值等：对应结构列置 NULL、状态 partial，完整值留在 payload/raw_log；不裁剪原文。0 是有效值，空值不写空字符串到整数列。DAS duration 原值仍保存，另生成 duration_us。

## 4. 缓冲、重试与运行边界

接收成功计数表示 SQLite 已提交（WAL + synchronous FULL），不表示已写 MySQL。接收回路不做 MySQL 查询，原文写缓冲通过后台线程完成；数据库写入使用独立有连接/读写超时的连接池。写库按单条事务顺序处理，参数化 SQL，表名/字段来自固定 catalog。数据库提交成功后才删本地 pending；远端提交后本地未确认而重启时使用同一 ingest_id 重放。连续收到内容相同的两条报文会生成两个 ingest_id，不被摘要吞掉。重复 ingest_id 若内容摘要不同则拒绝，不覆盖旧原文。

解析失败/未知类型/非法 UTF-8 留在 SQLite quarantine，不写错表；通过 SQLite 只读查询可查看 inbox 中的 raw BLOB、error、received_at。写 MySQL 失败保留 pending 并按轮次重试，错误日志不输出 SQL 绑定参数。`--status` 给出 pending/quarantine/payloadBytes/maxPayloadBytes/lastError；接收计数、拒绝/丢弃/分帧错误计数由运行日志输出，当前没有新增 HTTP 状态接口。

TCP/UDP 是传输协议，不承诺设备到数据库端到端零丢失：TCP 本身没有业务入库确认；UDP 可能丢包/乱序/分片失败。格式非法、超长、缺少 TCP 完整帧的内容不会冒充完整日志；原文只有完整帧进入缓冲后才受持久化保护。磁盘写失败/缓冲预算耗尽时记录 dropped：UDP 无法通知发送方，TCP 关闭连接，实际是否重试取决于设备。SQLite 文件有索引/WAL/已释放空间，实际磁盘占用可能大于有效原文字节预算；本次未做自动清理 quarantine、跨机备份或磁盘告警。

```powershell
# 查状态：不访问 MySQL
python -B -m app.syslog_server --product waf --source-instance waf-site-a --status
# 停止主服务或关闭对应产品接收后，重放待写库数据
python -B -m app.syslog_server --product waf --source-instance waf-site-a --replay-only
# 修复解析规则后，重新尝试隔离记录
python -B -m app.syslog_server --product waf --source-instance waf-site-a --retry-quarantine --replay-only
```

## 5. 验证与待办

`python -B -m pytest tests/test_syslog.py` 覆盖 15 种 WAF tag、三类 DAS、旧版告警、保留原文、类型转换、时区、真实 loopback TCP/UDP（含长报文）、应用生命周期启停/端口冲突回滚/重载端口重用、分帧、磁盘重启恢复、写库失败保留、幂等重放、隔离和 SQL/catalog 一致性；MySQL 仍由 tests/conftest.py 硬禁用。

2026-10-28 UDP 兼容性回归模拟 uvloop 的 `sock_recvfrom()` 未实现行为：WAF/DAS 各发送两条约 40 KB UDP 报文，核验原文字节完整、解析后写入测试 sink、重复关闭和同端口重用；另覆盖启动接收 API 失败的回滚、运行中任务失败及时报警并关闭 socket。Windows 本地测试使用 SelectorEventLoop 模拟此能力缺口，不等同于在 Linux 真正运行 uvloop 或真实 MySQL 联调。

待真实环境核验：设备实际传输协议与分帧、整体 Base64 覆盖范围、DAS 会话字段/数值 log_type 变体、六表 DDL/MySQL 方言与吞吐。前端列表/详情与聚合 API、存证库联动、队列备份与容量告警尚未实现，不能把本接收器已完成等同于这些能力已完成。

协议依据：[RFC5424](https://www.rfc-editor.org/rfc/rfc5424.html)、[RFC6587](https://datatracker.ietf.org/doc/html/rfc6587)、[RFC5426](https://www.rfc-editor.org/rfc/rfc5426.html)。产品字段根据本次用户提供的 WAF V6.0.8.1 接口说明、DAS 新旧 Syslog 说明实现，实际报文优先核验。

## 6. 服务器排查：端口已监听但没有数据

`ss` 出现 5514/5515 仅证明 socket 已绑定；CLI `--status` 的 pending/quarantine 都为 0，可能是尚未接收，也可能是已全部写库，不能单独作为采集成功证据。应同时检查六表最新 `received_at`、对应实例/路径的缓冲，以及服务日志。过滤日志时先筛 Syslog 再取末尾，避免高频心跳淹没错误：

```bash
journalctl -u log-fusion --since today --no-pager | grep -Ei 'syslog|traceback|exception' | tail -n 80
```

已发现旧实现存在事件循环兼容性缺陷：[uvloop 0.22.1 源码](https://github.com/MagicStack/uvloop/blob/v0.22.1/uvloop/loop.pyx) 的 `sock_recvfrom()` 直接抛出 `NotImplementedError`；`uvicorn[standard]` 在适用平台可能自动使用 uvloop。旧版 UDP task 因此可能退出，socket 却仍显示监听，关闭时才出现 `Syslog listener cleanup failed error_type=NotImplementedError`。修复如 §2；用户服务器日志与此现象吻合，但完整异常堆栈、实际事件循环及修复后的收包仍须服务器复测。历史心跳写入死锁不能据此当作 Syslog 故障原因。

更新修复并重启现有服务后，可在服务器发送一条带明确标记的本机测试日志（会新增一条业务测试记录），把接收/解析/写库与设备网络路径分别核验：

```bash
/opt/log-fusion/.venv/bin/python -B - <<'PY'
import socket
from datetime import datetime, timezone

message = f"tag:waf_log_login stat_time:{datetime.now(timezone.utc).isoformat()} user:SYSLOG_PROBE"
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.sendto(message.encode("utf-8"), ("127.0.0.1", 5514))
PY
```

```sql
SELECT id, log_type, peer_ip, received_at, raw_log
FROM log_fusion.waf_audit_log
WHERE `user` = 'SYSLOG_PROBE'
ORDER BY id DESC LIMIT 5;
```

有新记录才说明本机链路成功；未出现则复查缓冲及服务错误。若配置了来源过滤，需确认测试源 127.0.0.1 允许接收。本机成功后，再用 `tcpdump -nn -i any 'udp dst port 5514' -c 10` 查看 WAF 报文是否到达；WAF 端未到达的报文不会因本接收器修复而自动恢复。
