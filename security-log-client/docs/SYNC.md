# 历史补录与后台增量采集

2026-10-16：三张日志表和 `log_fusion.nsfocus_sync_state` 已实际创建，字段、类型及身份唯一索引由迁移命令核验。按用户最新要求，**历史导入量已足够，启动仅实时追加最新日志**。本机 `backend/.env` 为 `SECURITY_SYNC_ENABLED=true`。保留已有数据和历史断点，不再自动续跑剩余历史。

## 可选的显式历史补录（当前不需要再执行）

在 PowerShell 执行（使用本机已有 Python 环境）：

```powershell
Set-Location -LiteralPath 'C:\Users\ironp\Desktop\log-fusion\backend'
& 'D:\Data\Miniconda3\conda_envs\log-fusion\python.exe' -B ..\security-log-client\import_history.py run --months 3 --history-only --until-complete
```

该命令会读取 `backend/.env` 的数据库设置和 `security-log-client/config/connection.json` 的产品凭据；即使后台开关关闭，用户显式运行的 CLI 仍会执行。它将查询开始时的当前时间固定为本次历史截止时间，按北京时间回退三个日历月。已有断点会保留，只扩展缺少的首尾时间段，不缩短已经选定的历史范围。再次执行相同命令会续跑，并把截止时间延长到再次执行时。三类历史队列全部完成后退出；历史限定模式不执行实时巡检。

可按 Ctrl+C 停止。已经提交的记录和进度保留，当前未提交事务回滚。不要删除同步表或反复新建实例 ID；实例 ID 是去重身份的一部分。

另开 PowerShell 查看进度（同样先进入 `backend/`）：

```powershell
& 'D:\Data\Miniconda3\conda_envs\log-fusion\python.exe' -B ..\security-log-client\import_history.py status
```

`rows` 是此实例已实际入库的唯一记录数；`history.pending` 是未完成时间窗口；`history.checked_until` 是历史连续核对边界；`status=complete` 才代表该类历史队列完成。`expected` 仅在整段首次查询时记录，扩展/恢复已有时间段时可为 null，不能用它减去 `rows` 判断漏数。实时记录可与历史重叠，重复计数也不能直接作为上游唯一条数。

## 首次部署与迁移

新环境必须先装根目录 `requirements.txt` 依赖，配置数据库和产品凭据，再执行：

```powershell
& 'D:\Data\Miniconda3\conda_envs\log-fusion\python.exe' -B ..\security-log-client\import_history.py migrate
```

该命令只创建缺失的新表；同名日志表必须通过列名、类型、NULL 约束和身份唯一索引校验，否则失败。MySQL DDL 隐式提交，不能当作一个可整体回滚的事务。迁移中断时重跑会先验证已建表。不修改原有业务表。不自动 DROP 表；回退应用时先关闭采集，保留新表和数据，删除表须另行明确授权。

## 后台与前端

后台默认开启，启动服务后仅采集最新数据。首次只从 now-lag-overlap 开始，不读取历史断点，也不恢复旧实时队列。关闭时将 `SECURITY_SYNC_ENABLED=false` 并重启；仅编辑配置不会改变已运行进程的缓存配置。

配置项：`SECURITY_SOURCE_INSTANCE=nsfocus-site-a`、`SECURITY_CONFIG_FILE=security-log-client/config/connection.json`（相对仓库根）、`SECURITY_HISTORY_MONTHS=3`、`SECURITY_POLL_SECONDS=30`、`SECURITY_PAGE_SIZE=10000`、`SECURITY_OVERLAP_SECONDS=120`、`SECURITY_LIVE_WINDOW_SECONDS=600`、`SECURITY_LAG_SECONDS=30`、`SECURITY_TIMEOUT_SECONDS=90`。

后台只做增量巡检，单次请求不并发；默认 30 秒安排下一轮、回看重叠 120 秒、避开最新 30 秒。设备查询较慢时实际间隔会延长；同一进程长中断后的待处理范围最多回看 600 秒，更早间隙不追补。重启重新从近期起点开始。保持单 worker，CLI 与 backend 共用 MySQL 会话锁。CLI run 默认同样仅实时；只有显式 --history-only 才可能处理旧数据，--until-complete 只能与它组合。

页面 `http://127.0.0.1:8080/#/security` 每 10 秒刷新，并接收既有 WebSocket 的 `type=security_logs` 通知。可查看三类入库数、历史范围、进度、错误、每页 30 条最近入库记录和原始 JSON 详情。按本地 ID 游标翻页，显示的是入库顺序，补录期间不代表事件时间顺序。数据库不可用返回 503，不伪装成 0 条。CLI 独立运行时没有 WS 推送，页面的定时刷新仍可看到新数据。

## 完整性边界

查询只请求第一页。先用 size=1 查询匹配数，超过 10,000 时二分时间范围；可一次取全的窗口才批量入库。窗口分界重叠一秒，适配设备包含端点的行为，依靠唯一身份去重。短页或计数变化会继续细分，单秒数据超过限制时明确报错并保留窗口，不声称补录完成。设备不提供固定快照，因此保留期变化、上游删除和新到达数据会影响数值。

`(source_instance, upstream_index, log_id)` 相同且规范化 JSON 摘要相同则跳过；摘要不同则保留首条并让窗口进入重试，不能静默覆盖。已实测黑名单一批 6,725 条中包含 1 条完全重复身份，因此入库数量可以小于上游匹配数。原始 JSON 保留未知字段、空字符串和原值类型；独立列中的空数值写 NULL，0 保留。字段越界、内容冲突、网络错误、部分响应均不前移断点。每个时间窗口的日志和断点同事务提交。

## 本次只读数量核验

查询时间：2026-10-16 10:12:03 北京时间；范围 2026-07-16 10:12:03 至 2026-10-16 10:12:03。

| 类型 | 上游匹配数 |
|---|---:|
| 入侵防护日志 | 6,008,700 |
| 恶意 IP 全局黑名单日志 | 6,725 |
| WEB 安全日志 | 18,946 |
| 合计 | 6,034,371 |

报告：本地忽略目录 `reports/three_month_counts_20261016.json`。用户要求停止时，已提交 IPS 733 条、WEB 9,284 条、黑名单 0 条；剩余历史队列保留。后续核验不能把这份历史快照作为新的实时总数。

## 验证记录

- 已核验三张真实表的列名、SQL 类型、NULL 约束与身份唯一索引。原有表保留。
- 完整 pytest 278 项通过（测试始终禁止数据库连接）；JavaScript 语法检查、两项图表服务端渲染自检、WS 类型隔离检查通过。首次受限沙箱运行新增 TestClient 用例时挂起，已中止；完全权限下新增用例和完整套件通过，未放宽数据库隔离。
- 真实只读 API：摘要、三类列表、IPS/WEB 原始详情、页面均 HTTP 200；采集开关 false，入库数量保持 733 / 0 / 9,284。原始详情来源字段实测 IPS 70、WEB 64。黑名单完整历史响应的本地归一化验证单独执行，不写数据库。
- 未验证：六百万条全量采集完成及长期增量运行；用户已明确要求由自己执行。

## 用户确认后的历史状态

本次切换前只读核验：IPS 730,914 条、黑名单 6,724 条、WEB 18,952 条；用户确认足够，不再要求全量历史。后台启动将 IPS 未完成历史标记 stopped，已完成两类仍为 complete，保留已有日志和 pending。新实例不安排历史，状态 disabled。前文 733/0/9,284 等验证记录是较早快照，不是当前数量。实时初始化只更新同步 JSON，不修改表结构或删除日志。

## 仅实时时段验证（2026-10-16 后续）

- 全套 287 项隔离测试通过；Python/JavaScript 语法、图表两项自检通过。新增用例覆盖启动不创建历史、保留旧数据/窗口、历史不回退执行、长中断仅查询近期、旧实时分片裁剪和 backend 生命周期接线。
- 本机服务已按仅实时配置重启。只读摘要确认 enabled=true、collectionMode=live_only、overlapSeconds=120、liveWindowSeconds=600。三类均获得新一轮 watching / error 为空；IPS 历史为 stopped、8 个旧窗口仍保留但没有执行。
- 首轮 IPS 从 730,914 增至 730,972（追加 58 条）；黑名单与 WEB 当轮没有新增。实时起点约为北京时间 14:44:11，已核对至约 14:46:16，没有从上午或三个月前的游标继续。该记录是首轮快照，后续会继续增长。
- 本次无实际数据库结构变化，仅更新同步 JSON 并追加近期日志；未删除已有业务记录。摘要接口扩展字段，无新增路由。
