# 安全产品日志客户端

这是与 `backend/`、`frontend/` 同级的独立项目，目前支持绿盟日志审计系统。使用 Python 3.10+ 标准库，无需安装第三方包，也不依赖 FastAPI、MySQL 或主项目的 `PYTHONPATH`。

## 目录

```text
security-log-client/
├── nsfocus_api.py              # 登录、日志/事件分页、辅助接口
├── nsfocus_log_search.py       # 单页请求、环境变量认证和 dry-run
├── analyze_samples.py          # 离线分析与 JSONL 样例导出
├── probe_log_types.py          # 逐类探测截图可见24类日志并保存真实样例
├── pyproject.toml
├── config/
│   ├── connection.example.json # 可复制的占位配置
│   └── connection.json         # 已保存的真实密码/Token（Git 忽略）
├── docs/                       # API.md 接口实测；DATABASE.md 字段清单及入库设计
├── sql/                       # log_fusion_nsfocus_tables.sql 为当前三类专用表；另有字段映射JSON
│                              # schema_draft.sql 为历史通用四表草案，不与当前方案同时执行
├── data/                       # Git 忽略
│   ├── debug/                  # 从旧位置迁入的完整调试记录
│   ├── samples/                # 本次从设备重新获取的样例
│   └── captures/               # 后续请求的默认输出目录
└── reports/                    # 分析报告、统计 JSON 与样例 JSONL（Git 忽略）
```

## 使用

先进入本项目目录：

```powershell
Set-Location -LiteralPath .\security-log-client

# 默认读取 config/connection.json；重新登录并更新 Token
python nsfocus_api.py auth

# 查询事件；脚本始终发送 conditionStr，无筛选条件时为 []
python nsfocus_api.py events --last-hours 24 --size 10

# 顺序拉取 3 页，每页 1,000 条；启动时固定时间范围
python nsfocus_api.py logs --type windows_log --last-hours 24 --size 1000 --pages 3

# 事件对应的日志类型目录
python nsfocus_api.py catalog log-types

# 检查截图中完整可见24类日志；先查24小时，必要时查7天
python probe_log_types.py

# 分别查询最近1个月和3个月，每个类型的两个窗口都会检查
python probe_log_types.py --months 1 3 --timeout 30

# 只预览单页请求，不联网
python nsfocus_log_search.py --base-url https://192.0.2.10 --type windows_log --dry-run
```

也可在其他工作目录使用脚本的绝对路径运行；默认配置与默认输出目录按脚本所在位置解析。显式 `--config`、`--output-dir` 和分析输入路径中的相对路径仍相对当前工作目录。

连接文件按用户要求保存真实地址、账号、明文密码和 Token，已从旧位置移动到 `config/connection.json`。本次设备使用自签名证书，连接文件保留 `insecure=true`。新设备可从示例配置复制并填写；Token 可以为空，脚本会登录并保存。

## 本次本地样例与分析

2026-10-15 已从设备重新获取：

- `data/samples/windows_20261015/`：最近 24 小时请求范围下的前 3 页，共 3,000 条日志；每页原始响应和 manifest 均已保存。
- `data/samples/events_20261015/`：事件查询成功响应，本次结果 0 条。
- `reports/windows_20261015/analysis.md`：中文分析报告。
- `reports/windows_20261015/summary.json`：完整统计及数据质量检查。
- `reports/windows_20261015/samples.jsonl`：按 `(index, source.log_id)` 去重的完整样例，每行一条，保留嵌套 source 和原始日志。

重新分析本次样例：

```powershell
python analyze_samples.py data/samples/windows_20261015
```

分析程序只读取 manifest 指定的分页，避免将历史调试目录中的重复请求混入同一批。分析包括事件码/操作/主机/IP 分布、字段缺失、原文解析字段一致性和接收时间差。`oevent_id` 用作 Windows 事件码；`eventidentifier` 在本批样例中均为 0，不能代替事件码。日志的 risk/severity 映射未确认，不套用事件接口的风险等级。

本次仅是 API 前几页的便利样本，实际日志时间跨度和请求窗口见报告；不代表全天或全网分布，也不直接证明是否存在攻击。最大分页深度与全量快照一致性仍待确认。完整参数和实机记录见 [接口说明](docs/API.md)。

## 日志类型真实数据探测

`probe_log_types.py` 先重新登录并读取设备目录，再逐一请求截图中完整可见的 24 类日志。每类先查询固定结束时间下的最近 24 小时；未取得确认样例时扩大到最近 7 天。每次仅请求 1 条，默认请求结束后间隔 0.2 秒。

结果默认位于 `data/type-probes/<UTC时间>/report.md`、`report.json`，各次完整响应位于同目录 `responses/`。必须有真实记录且 index/table 与请求类别对应才标记有数据；网络错误最多重试一次，失败历史保留。查询失败、只有总数没有样例、返回其他类型等均标记待确认，不当作空结果。0 条也只代表当前账号和窗口，不代表全部历史没有数据。样例字段数只表示这一条记录，不是类别字段全集。

指定 `--months 1 3` 时按北京时间回溯日历月，固定一个结束时间，每类两个窗口都查，不因较短窗口有数据而跳过较长窗口。遇到目标月份没有对应日期时取月末，例如 3 月 31 日回溯一个月为 2 月 28/29 日。报告记录准确起止时间；不把三个月固定当作 90 天。

## 入库设计（未实施）

按用户最新要求，当前方案在 **log_fusion** 库设计三张专用表：`nsfocus_ips_log`（入侵防护）、`nsfocus_ip_blacklist_log`（恶意 IP 全局黑名单）、`nsfocus_web_security_log`（WEB 安全）。每类实际 API 字段独立建列；IPS/WEB 也保留已核验 CSV 模板扩展字段，完整 JSON 用于快照和新增字段兜底。

当前 [建表 SQL](sql/log_fusion_nsfocus_tables.sql)、[完整字段映射](sql/log_fusion_nsfocus_tables.fields.json) 和 [三类专用表设计说明](docs/LOG_FUSION_TABLES.md) 已提供，含 UTC 时间、去重、索引、入库契约及上线/回滚步骤。旧 [Windows 字段核验及通用方案](docs/DATABASE.md) 仅作历史参考，不与新方案同时执行。

草案未执行；没有数据库导入程序、建库或数据写入。三张表均保存 §3.4.1 日志，不是 §3.5.1 的安全事件表；安全事件仍无真实样例。

## 迁移说明

两个请求脚本已从主仓库 `tools/` 移到本目录；接口专题文档已从主仓库 `docs/` 移到本项目 `docs/API.md`，历史调试目录已迁入 `data/debug/`。原路径不再作为运行入口。目录及真实配置都在本项目内，复制整个目录即可携带代码、配置和本地数据。

无实际数据库结构变化，无实际接口行为变化。本项目只登录和读取外部安全产品接口、保存文件及离线分析，不导入 Log Fusion。
