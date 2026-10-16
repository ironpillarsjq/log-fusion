# 绿盟 API 调试与分页使用说明

本项目与 `backend/` 同级，以下命令在本项目根目录执行。本工具独立请求绿盟设备，不接入 Log Fusion 的数据库或 HTTP 服务。依据用户提供的《绿盟日志审计系统 API 联动第三方指导手册》§3.1、§3.4.1、§3.5.1–3.5.6；文件名版本为 V2.0R02F01，内页页眉为 V2.0R01F00。以下实机结果记录于 2026-10-15，仅代表本次账号和查询范围。

## 本地连接文件

经用户明确要求，连接地址、用户名、**明文密码及 Token** 保存在 `config/connection.json`。该连接文件及 `data/` 均由项目自己的 `.gitignore` 忽略。文档、脚本和调试报告不包含真实密码及 Token；不要将连接文件分享或提交。

连接文件的结构如下（仅为占位示例）：

```json
{
  "base_url": "https://192.0.2.10",
  "username": "YOUR_USERNAME",
  "password": "YOUR_PASSWORD",
  "token": "TOKEN_FROM_AUTH",
  "insecure": true
}
```

本次设备使用自签名证书，因此连接文件配置 `insecure=true`。使用可信证书时删除该字段或设为 `false`，私有 CA 可配置 `ca_cert` 的 PEM 文件路径。设备连接直连内网，不使用系统 HTTP 代理，不跟随重定向。

`nsfocus_api.py` 仅依赖 Python 3 标准库。默认读取上述文件；要使用其他连接文件，将 `--config <路径>` 放在子命令之前。Token 缺失时自动登录并保存；HTTP 401 时重新登录并重试一次。其他业务错误不猜测其含义，可用 `auth` 显式刷新 Token；设备的 Token 有效期未在该手册中确认。

```powershell
# 重新登录，保存最新 Token，不在控制台打印 Token
python nsfocus_api.py auth
```

## 事件及辅助接口实测

| 手册章节 | 方法及路径 | 本次结果 |
|---|---|---|
| 3.1 | `POST /api/v2/auth` | HTTP 200，`errCode=0`，已保存 Token |
| 3.5.1 | `GET /api/v2/events/data/combinationQuery` | HTTP 200；最近 24 小时、最近 7 天、未传时间范围的测试均返回 0 条 |
| 3.5.2 | `GET /api/v1/events/typeTree` | HTTP 200，15 个顶层分类 |
| 3.5.3 | `GET /api/v1/events/typesAll` | HTTP 200，170 个分类项 |
| 3.5.4 | `GET /api/v2/events/types` | HTTP 200，13 个顶层类型 |
| 3.5.5 | `GET /api/v2/events/data/logList` | 缺少 `nsuids` 时 HTTP 400、`errCode=28520`；因没有真实事件 ID，成功返回关联日志的分支待验证 |
| 3.5.6 | `GET /api/v1/events/logs` | HTTP 200，112 个日志类型 |

**3.5.1 必须带 `conditionStr`，无筛选条件时也要发送字符串 `[]`。** 本机省略该参数会返回 HTTP 400、`errCode=10000`。手册将筛选字段列为可选，但仅靠 `page`、`size` 调用在本设备上不成功。

时间戳按 Unix **秒**发送。秒时间戳配合 `conditionStr=[]` 返回 HTTP 200；毫秒时间戳测试返回 HTTP 400。0 条表示本次账号及范围下的查询结果，不能据此断定整个设备没有事件，也不等同于原始日志为空。

```powershell
# 查询最近 24 小时的事件，默认一页，每页 100 条
python nsfocus_api.py events --last-hours 24 --size 10

# 不指定时间范围，范围由设备决定
python nsfocus_api.py events --no-time --size 10

# 查询目录
python nsfocus_api.py catalog type-tree
python nsfocus_api.py catalog all-types
python nsfocus_api.py catalog types
python nsfocus_api.py catalog log-types

# 有真实事件 eventNsuid 后，才能验证关联日志
python nsfocus_api.py related --nsuids REAL_EVENT_NSUID --size 10
```

带筛选条件时，把 JSON 数组存为单独文件，再通过 `events --condition-file <路径>` 传入。脚本将其编码为 URL 查询参数 `conditionStr`，不把 `columnName` 等字段直接平铺到 URL。下列结构取自手册样例，具体字段和运算符支持范围仍以设备为准：

```json
[{"columnName":"sip","operateType":"contain","columnValue":["192.0.2.1"]}]
```

## 用户日志批量拉取

3.4.1 支持通过请求体的 `page` 和 `size` 分页。已实际验证：

- 每页 100 条、连续 2 页，各返回 100 条。
- 每页 1,000 条、连续 2 页，共返回 2,000 条。两页分别约 5.9 秒、7.5 秒；按 `(index, source.log_id)` 检查，单页内和两页间均无重复。
- 1,000 条测试的固定 24 小时时间范围匹配 `totalSize=5,416,859`；这与此前 5,600,986 条是不同时间点的滚动窗口，不能直接作为数据增减结论。

```powershell
# 已验证的批量请求方式：两页，每页 1,000 条
python nsfocus_api.py logs --type windows_log --last-hours 24 --size 1000 --pages 2

# 固定时间范围，从指定页开始，适合分段获取或失败后重跑
python nsfocus_api.py logs --type windows_log --start-time 1644543000 --end-time 1644546600 --page 1 --size 1000 --pages 2
```

脚本在启动时固定起止秒时间戳，随后顺序翻页，每个请求结束后默认间隔 0.2 秒；`--interval` 最小 0.1 秒。手册默认调用上限为 10 次/秒，设备可以调整，脚本的默认节奏低于该限制。`--pages` 是本轮最多请求页数，默认 1，不会自动下载数百万条。

每次运行默认创建 `data/captures/<子命令>_<UTC时间>/`：

- `page_000001.json` 等：各页完整响应，逐页落盘。
- `manifest.json`：固定请求条件、起始页、请求页数、每页耗时、计数及本轮状态，不包含密码或 Token。
- `status=page_limit_reached`：本轮达到指定页数；**不代表全部查询结果已下载**。
- `status=exhausted`：设备返回空页或最后不足一页，且没有与 `totalSize` 矛盾。
- `status=failed`：请求失败、部分结果或分页异常；已完成的页文件保留，脚本以非零退出码结束。

可用 `--output-dir <新目录>` 指定输出位置。若已有 `manifest.json` 则拒绝复用，避免混入不同时间窗口的分页结果。中断后参考 manifest 的固定时间范围与最后成功页，用新目录和 `--page` 手动继续；没有自动断点续传或自动合并。

设备返回 `partOfData=true`（手册称查询超过 1,000 分片时可能出现）、相邻页面完全重复、响应页码/页大小与请求不一致，或短页与总量矛盾时，脚本保存已收到的响应并停止。大量数据建议缩小时间窗口分段拉取，再按 `(index, source.log_id)` 去重和核对。手册没有给出最大 `size`、最深可翻页范围或快照/游标协议；本次仅验证到 1,000 条/页、前两页，不保证数百万条全量导出的完整性或耗时。

## 本次调试产物

本地结果位于已被 Git 忽略的 `data/debug/`：

- `api_debug_report.json`、`events_debug_variants.json`：早期逐项调试与参数差异报告，含失败记录。正式脚本的 Token 保存及刷新已通过独立检查。
- `event_type_tree.json`、`event_types_all.json`、`event_types.json`、`event_log_types.json`：四个辅助目录响应。
- `events_last7days.json`、`related_validation.json`：七天事件查询及关联日志参数校验结果。
- `batch1000_verified/`：实机批量测试的两页原始响应和 manifest。

无实际数据库结构变化，无实际接口行为变化；仅登录外部设备、读取接口和保存本地文件。Log Fusion 的采集、数据库和前端均未接入这些外部数据。

## 独立项目内的重新取样

脚本和文档迁入本项目后，2026-10-15 使用保存的连接文件重新请求最近 24 小时范围的前 3 页，各 1,000 条，总计 3,000 条。原始响应和固定查询条件位于 `data/samples/windows_20261015/`；事件空响应位于 `data/samples/events_20261015/`。因此当前分页验证已覆盖前 3 页，深度上限仍未确认。

离线分析入口为 `analyze_samples.py`，输出位于 `reports/windows_20261015/`。其中 `analysis.md` 是中文报告，`summary.json` 为统计结果，`samples.jsonl` 为去重后的完整日志样例。主机名、时间差和风险字段均保留口径限制，不将样例统计等同于全量安全结论。

## 截图可见日志类型的数据探测

2026-10-15 通过 `probe_log_types.py` 核查截图中完整可见的 24 种安全设备日志，逐类调用 §3.4.1，每次第 1 页、1 条。固定结束时间下先查最近 24 小时，未取得样例的再查最近 7 天，并使用设备日志目录核对名称。

本次有真实记录且返回类型与请求一致的三类：入侵防护 `ipslog`（24 小时 totalSize=66,486）、恶意 IP 全局黑名单 `ipblacklistlog`（64）、WEB 安全 `ips_web_secure_event_log`（440）。分别保存一条样例，单条 source 字段数为 70、62、64；这不是各类型的完整字段全集。其他 21 类在本账号最近 24 小时及最近 7 天查询中均返回 0；不代表全部历史没有数据。

完整范围、结果和各次原始响应位于本地 `data/type-probes/20261015T085710_068890Z/`。防火墙的 24 小时请求曾连接重置，相同窗口重试成功返回 0，历史错误保留在 report.json。网络错误最多重试一次，失败或只返回总数没有真实样例时不标记为“有数据”。该探测针对日志接口，不是 3.5.1 事件接口。

### 一个月及三个月复核

使用 `python probe_log_types.py --months 1 3 --timeout 30` 对全部 24 类各查两个窗口，共 48 个查询窗口；包含一次网络重试，实际发起 49 次日志请求。固定截止时间为北京时间 **2026-10-15 19:52:18**：一个月从 **2026-09-15 19:52:18** 开始，三个月从 **2026-07-15 19:52:18** 开始。本次三个月为 92 天，按日历月计算；三个月包含一个月，不应将两个计数相加。

| 日志类型 | 最近一个月 | 最近三个月 |
|---|---:|---:|
| 入侵防护 ipslog | 1,870,861 | 6,010,498 |
| 恶意 IP 全局黑名单 ipblacklistlog | 903 | 6,718 |
| WEB 安全 ips_web_secure_event_log | 6,222 | 18,826 |

其余 21 类在两个窗口内均成功返回 0；没有未确认或 partOfData 部分结果。内容过滤日志三个月查询曾遇到连接重置，自动重试后成功返回 0，失败历史保留。每个非空窗口保存一条真实样例，计数来自 totalSize，并非下载了全部匹配记录。完整报告及 48 个窗口的成功响应位于本地 `data/type-probes/20261015T115217_913168Z/`。
