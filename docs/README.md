# Log Fusion 项目总览

> 文档基线日期：2026-10-10（同日按当前工作区代码复核）。本文描述当前 FastAPI 实现；遗留 Spring Boot/Vue 工程和规划文档不代表现行行为。

## 1. 项目目标与状态

### 日志编码约定

日志链路统一使用 **UTF-8**：采集端 JSON/JSONL 文件、HTTP 请求与响应、服务端处理、导出文件和前端页面展示均遵守此约定。Windows 事件日志原生字符串由采集插件转换为 Unicode 后，以 UTF-8 输出；禁止将 GBK/ANSI 字节直接作为 JSON 请求体发送。MySQL 连接使用 `utf8mb4`，表/列也应支持 `utf8mb4`（既有表实际字符集需单独核验）。

当前页面 `backend/app/templates/dashboard.html` 声明 `<meta charset="utf-8">`；`frontend/app.js` 通过 `fetch` 的 `Response.json()` 读取 UTF-8 JSON，日志表格通过 Alpine `x-text="row[k]"` 展示解码后的 Unicode 字符串，不进行 GBK/ANSI 二次转码。JSON 中的 `\uXXXX` 是合法 Unicode 转义，JSON 解析后会还原为对应字符；嵌套 JSON 字符串中的转义显示属于内容格式问题，不是页面字符集问题。

JSON/JSONL 使用 UTF-8 无 BOM；CSV 导出允许附加 UTF-8 BOM 以方便 Excel 识别；PowerShell 5.1 的中文 `.ps1` 文件必须为 UTF-8 with BOM。已有乱码或 `�` 如果在采集/入库前已经产生，前端设置 UTF-8 无法恢复，需修正采集端后重新采集。

Log Fusion 是面向运维和审计场景的日志融合平台，接收 Linux 审计日志与 Windows 事件日志，提供分类入库、统计查询、CSV 导出、客户端心跳实时监控（WebSocket）、原始日志 SHA-256 存证与归档。

当前项目处于开发中期：核心查询、采集写入、原始日志 SHA-256 存证与归档、仪表盘、持久化心跳、实时通道（WebSocket，含断线重连与假死检测，SSE 已移除）和**实时动态图表看板**已经具备；认证授权、正式数据库迁移、存证逐条校验接口、多实例实时广播（当前必须单 worker）和集成测试/CI 尚未完成（已有 260 个不连库的契约用例，其中 21 个归档用例需要 `tmp_path`）。

## 2. 当前核心能力

- FastAPI 提供页面、HTTP API 与 WebSocket（唯一的实时通道；SSE 已于 2026-10-13 移除，见 ADR-015）。
- Jinja2 在 `/` 服务端渲染仪表盘，Alpine.js 管理页面状态，HTMX 刷新数据库状态，ECharts 展示统计图表。
- **实时图表看板**（2026-10-13 新增，方案与决策见 `PLAN-frontend-charts.md`）：运行概览有实时接入趋势 + 平台占比半环仪表，客户端心跳监控有延迟趋势 + 状态分布（**在线率仪表与客户端延迟排行已按需求移除**，在线率仍在指标卡上显示），融合日志管理下新增二级子页**日志统计图表**（接入趋势、24 小时×星期热力图、TOP 主机/类别排行）。图表数据全部来自服务端聚合接口（`metrics.py`，带 TTL 缓存），前端不自己累积历史。
  - 心跳页的延迟图**只展示未掉线的客户端**：离线时「延迟」等于心跳年龄，只会一路涨到无意义的大值，因此延迟趋势按状态过滤并在图下注明排除了几个离线客户端。
  - 延迟趋势与概览「接入趋势」同一套视觉：主序列（平均延迟）是**渐变面积折线**，客户端细线细而半透明（1px、opacity 0.35）、退到背景，平均延迟线宽 2px（只比背景线粗一档）。
  - 曲线**按原始采样值直接画折线**（`smooth:false`，不做 EWMA、不做滑动平均）：平滑会掩盖单点尖峰，而尖峰正是这张图要看的东西，2026-10-13 按需求取消平滑（改回之前的中间版本见 `CHANGELOG.md`）。
  - 唯一保留的「稳定观感」措施是**纵轴锚定**（`niceDelayMax()`，`DELAY_AXIS_FLOOR` 下限 5 秒）：自动缩放的纵轴会把 3~4 秒之间的小波动撑满整个高度，看起来像剧烈震荡。延迟逼近 10 秒（在线/延迟分界阈值）时，图上会出现一条 `延迟阈值 10s` 参考线。
  - 所有图表统一走 `app.js` 的 `paint()`：数据没变就不重画、只有数据变就合并更新、只有序列结构变才重建——这样 WebSocket 每 5 秒推送时不会把整张图重新播一遍动画（否则页面会一直闪），见 `ARCHITECTURE.md` §4.2。
- 左侧导航是二级菜单（仅「融合日志管理」有子菜单），五个页面都有 URL hash 深链（`#/overview`、`#/heartbeat`、`#/logs`、`#/log-stats`、`#/evidence`），刷新后停在原页面。导航下方是「服务连接」状态卡：四个库各一行（状态点 + 中文名 + 真实库名 + 已连接/不可用，HTMX 每 30 秒刷新片段）+ 浏览器自己的 WebSocket 实时通道行。
- **运行概览**含「哈希存证」面板：存证总数、一致/哈希异常/原始缺失的占比条与计数（`/api/v1/evidence/summary`，30 秒一轮）。存证库查询失败时面板显示「存证库不可用」而不是 0——原先概览里那三行写死的「系统状态」徽标已移除，连接状态统一看侧栏卡片。
- 查询 7 类 Linux 日志表和 5 类 Windows 日志表；融合日志列表按 **30 条/页**分页（可翻页，支持平台/类别/关键词筛选）。
- 接收 Linux/Windows 日志：`parsers.py` 把原始记录解析成数据库行（含时间归一化与按表裁剪），再批量入库；响应用 `inserted`/`skipped`/`failed`/`hashed`/`errors[].stage` 区分“解析失败”“入库失败”和“存证跳过”。
- 每条日志由 `hashing.py` 按 `sha256(时间戳 + 0x1F + 存证原文)` 计算摘要，把存证原文与摘要写入 `log_hash.log_hash`（唯一索引去重；原文为空则跳过存证且不影响入库）。**存证原文按平台不同**：Linux 用 `log` 字段原文，Windows 载荷没有 `log`，改用整条记录的规范化 JSON。
- **完整性存证页**是分四个功能区的「存证中心」：存证总览 / 存证台账 / 归档管理 / 算法与策略。
  台账按 **30 条/页**分页，可按校验状态、平台、入库时间范围与关键词筛选，并可导出**当前筛选条件**下的 CSV；
  归档（会删除数据）独立成区，避免与日常查询混在一起。
- **存证归档**：把历史存证导出为 CSV 再从数据库删除，分两种方式——
  **自动归档**（后台每天一次，导出 `created_at` 早于 3 个日历月的记录）与
  **手动归档**（页面上选时间区间导出）。导出文件永久保留在 `data/archive/`，
  且**只有确认文件落盘并校验一致后才执行删除**，失败时一条都不删。
- 在 `log_fusion.client_status` 中持久化客户端心跳，按 10 秒/30 秒阈值计算在线、延迟、离线状态。
- 通过 `/ws/client-monitor` 广播心跳快照（每次心跳成功时，以及后台每 5 秒的巡检轮次；页面已接入该通道并实现断线重连与假死检测）——这是**唯一**的实时通道；采集侧不再推送逐条日志事件（原 `/api/v1/logs/stream` 已于 2026-10-13 移除，ADR-015）。

## 3. 代码范围

```text
log-fusion/
├── backend/                    # 当前 FastAPI 后端
│   ├── app/
│   │   ├── main.py             # 应用、路由、采集写入、实时通道、自动归档任务
│   │   ├── config.py           # 环境配置与归档策略
│   │   ├── db.py               # 四个 MySQL Engine 与会话
│   │   ├── models.py           # client_status、log_hash、archive_log ORM 模型
│   │   ├── services.py         # 日志查询与统计
│   │   ├── metrics.py          # 图表聚合：趋势/排行 TTL 缓存 + 心跳历史环形缓冲
│   │   ├── parsers.py          # 采集记录解析（auditd/syslog/winlog → 数据库行）
│   │   ├── hashing.py          # 原始日志 SHA-256 存证行构造
│   │   ├── archive.py          # 存证归档：导出 CSV + 校验落盘 + 按 id 删除
│   │   ├── catalog.py          # 类别、查询列与各表真实列定义
│   │   ├── ws.py               # WebSocket 连接管理
│   │   └── templates/dashboard.html
│   ├── simulator/send_simulated_clients.py
│   └── run.py
├── frontend/                   # /static 静态目录，无构建步骤
│   ├── app.js                  # 当前 Jinja2 页面使用
│   ├── vendor/                 # 自托管前端库（Bootstrap/HTMX/Alpine/ECharts，带版本号）
│   ├── styles.css              # 旧静态页面资源，当前模板未引用
│   └── index.html              # 未配置为当前入口
├── docs/                       # 当前实现的维护文档
│   ├── sql/log_hash.sql        # 原始日志存证库/表的可复现 DDL
│   ├── sql/archive_log.sql     # 归档台账表的可复现 DDL（含核验 SQL）
│   └── fluent-bit-client.conf  # 真实客户端接入样例（Linux auditd / Windows winlog）
├── data/archive/               # 归档 CSV 落盘目录（运行期产物，已在 .gitignore 中）
├── tests/                      # pytest 单元/契约测试（不连接数据库）
├── tools/                      # .cmd 启动器的 ASCII 转发脚本；chart-layout-check.cjs（概览环形图布局自检）；
│                               # chart-render-check.cjs（全部图表的服务端渲染/空态/越界自检，可接 --live=URL 跑真实数据）；
│                               # restore_archive.py（把归档 CSV 导回 log_hash，默认只预览）；
│                               # nsfocus_log_search.py（绿盟设备认证及用户日志查询，独立标准库脚本）
├── deploy/                     # 服务器部署：log-fusion.service（systemd 单元）；
│                               # install-service.sh（一次性且幂等地注册服务：校验→enable→restart→/health 自检）；
│                               # update-log-fusion.sh（一键更新：停服→git reset --hard 原地更新→重启）
├── 旧前端/                     # 遗留 Vue SPA，只读参考
├── 旧后端/                     # 遗留 Spring Boot，只读参考
├── 启动服务.cmd / 启动服务.ps1
├── 启动模拟请求.cmd / 启动模拟请求.ps1
├── pytest.ini
├── requirements.txt
└── environment.yml
```

活代码、规则文件、`docs/` 和启动脚本中的 `python/` 过期路径引用已全部清理，当前仓库实际目录是 `backend/`；只有 `旧后端/`、`旧前端/docs/` 的历史材料保留该路径。

## 4. 技术栈

| 层次 | 当前实现 |
|---|---|
| 运行时 | Python 3.12 |
| Web | FastAPI 0.143.0、Uvicorn 0.54.0 |
| 数据访问 | SQLAlchemy 2.1.4、PyMySQL 1.2.3 |
| 配置 | pydantic-settings 2.15.0 |
| 模板 | Jinja2 3.1.6 |
| 实时 | FastAPI WebSocket（唯一实时通道；原 sse-starlette 已于 2026-10-13 移除，ADR-015） |
| 浏览器 UI | Bootstrap 5.3.3、HTMX 2.0.4、Alpine.js 3.14.8、ECharts 5.5.1（自托管于 `frontend/vendor/`） |
| 数据库 | MySQL：`linux_logs`、`windows_logs`、`log_fusion`、`log_hash` |
| 迁移 | `alembic==1.20.0` 已在 `requirements.txt` 中，但仓库内没有 `alembic.ini` 或迁移脚本；建表仍靠 `create_all()` 与 `docs/sql/*.sql`（见 `docs/DATABASE.md` §9） |

仓库没有前端构建步骤；四个前端库已自托管在 `frontend/vendor/`（见 ADR-011），页面不依赖公网 CDN，断网也能完整加载。

## 5. 环境与配置

推荐使用 `environment.yml` 创建 Conda 环境，或在 Python 3.12 环境中安装 `requirements.txt`。

`backend/app/config.py` 读取当前工作目录下的 `.env` 和系统环境变量。支持的变量如下：

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `APP_NAME` | `Log Fusion` | FastAPI 应用名 |
| `DB_HOST` | `192.168.5.6` | MySQL 主机 |
| `DB_PORT` | `3306` | MySQL 端口 |
| `DB_USER` | `root` | MySQL 用户 |
| `DB_PASSWORD` | 代码内存在默认值 | MySQL 密码；应由环境覆盖 |
| `LINUX_DB` | `linux_logs` | Linux 日志库 |
| `WINDOWS_DB` | `windows_logs` | Windows 日志库 |
| `LOG_FUSION_DB` | `log_fusion` | 平台状态库（心跳 `client_status` + 归档台账 `archive_log`） |
| `LOG_HASH_DB` | `log_hash` | 原始日志存证库 |
| `LOG_HASH_TABLE` | `log_hash` | 存证表名 |
| `RAW_STORAGE_DIR` | `data/raw` | 原始存储目录配置；当前代码未使用 |
| `ARCHIVE_DIR` | `data/archive` | 归档 CSV 目录（相对路径按仓库根解析；文件永久保留） |
| `ARCHIVE_RETENTION_MONTHS` | `3` | 自动归档保留期（日历月）；`<=0` 表示不启用 |
| `ARCHIVE_BATCH_LIMIT` | `200000` | 单次归档处理的最大行数 |

安全提示：仓库当前含数据库默认凭据，只适合受控开发环境。部署前应轮换密码、使用最小权限账号并通过环境注入。

## 6. 启动服务

在仓库根目录运行：

```powershell
.\启动服务.ps1        # 也可直接双击「启动服务.cmd」
```

默认监听 `0.0.0.0:8080`。脚本支持 `-BindHost`、`-Port`、`-Reload`，并尝试创建名为 `log-fusion <port>` 的 Windows 防火墙入站规则。脚本固定使用：

```text
D:\Data\Miniconda3\conda_envs\log-fusion\python.exe
```

不使用脚本时，可执行：

```powershell
conda activate log-fusion
Set-Location -LiteralPath .\backend
$env:PYTHONPATH = (Get-Location).Path
python run.py
```

`backend/run.py` 固定使用 `0.0.0.0:8080` 且开启 reload。正常访问地址：

- 仪表盘：`http://127.0.0.1:8080/`
- OpenAPI：`http://127.0.0.1:8080/docs`
- 健康检查：`http://127.0.0.1:8080/health`

### 6.1 运行测试

```powershell
python -m pytest        # 在仓库根目录执行
```

测试位于 `tests/`（当前 **260 个用例**，其中 21 个归档用例需要 `tmp_path`，在受限沙箱外实测全部通过）：通过类级 patch 禁用 `Engine.connect/begin`，并把连接参数指向不可用占位值，因此**不连接任何数据库**，可在无 MySQL 环境重复运行。覆盖类别名校验、列表/导出取数上限、关键词与最近日志的列适配、采集解析（时间/auditd 映射/Windows 字段/按表裁剪）与入库失败分阶段上报、心跳状态阈值、WebSocket 建连快照与连接计数、图表聚合接口（趋势/排行的参数校验与 TTL 缓存、心跳历史环形缓冲）、静态资源与模板契约、**前端图表接线契约**（模板里的每个图表容器都必须有对应的 `getChart()`、页面卸载要释放图表实例、时间桶必须按本地时区渲染）、路由与 `docs/API.md` §2 的一致性（含 SSE 已移除的守卫）、存证哈希口径（`hashing.py`）、存证接口 503 语义与 errno 1062 判定、服务端更新接口（`/api/v1/admin/update` 的 confirm 校验、systemd-run 触发与并发拒绝）、**更新脚本的原地更新不变量**（只用 `reset --hard`，不含 `git clean`/`rm -rf`；`.gitignore` 必须忽略 `.venv/`、`.env`、`data/`、`backend/data/`；单元文件、安装脚本与更新脚本三处的服务名必须一致）、归档「落盘校验通过才删除」等不变量，以及 `docs/` 与代码的文档一致性（`test_doc_sync.py`）。仍没有 lint / typecheck 配置。

图表本身另有两个不依赖浏览器的自检（改动图表后手工执行，见 §8）：

```powershell
node tools\chart-layout-check.cjs                     # 概览环形图的几何/图例布局
node tools\chart-render-check.cjs                     # 全部图表的渲染、空态、文字越界、防闪屏
node tools\chart-render-check.cjs --live=http://127.0.0.1:8080   # 追加真实接口数据
```

## 7. 发送模拟数据

模拟器依赖仓库外的两个非空文件：

```text
C:\Users\ironp\Desktop\data\linux\raw-logs.log
C:\Users\ironp\Desktop\data\windows\raw-logs.log
```

`启动模拟请求.ps1` 已指向 `backend/`，不再有路径失效问题；样例目录默认 `C:\Users\ironp\Desktop\data`，可用 `-DataDir` 覆盖。**双击请用 `启动模拟请求.cmd`**（本机 `.ps1` 默认关联是记事本，双击只会打开文件而不执行；详见 `AGENTS.md`）。等价的直接命令：

```powershell
conda activate log-fusion
Set-Location -LiteralPath .\backend
$env:PYTHONPATH = (Get-Location).Path
python .\simulator\send_simulated_clients.py --url http://127.0.0.1:8080 --count 3
```

`--count 0` 表示持续发送；默认间隔 3 秒；`--clients` 可覆盖客户端标识列表。模拟器自身的 URL 默认值已与启动脚本一致（`http://127.0.0.1:8080`，2026-10-13 由 8000 改齐），直接调用无需再显式传 `--url`。

### 7.1 绿盟设备用户日志查询（独立工具）

`tools/nsfocus_log_search.py` 根据用户提供的绿盟 API 手册 §3.1 / §3.4.1 编写，使用 Python 3 标准库，无需额外安装依赖。查询外部绿盟设备，不向 Log Fusion 导入日志。

```powershell
# 把示例地址换成设备实际地址；运行后交互输入密码
python tools/nsfocus_log_search.py --base-url https://192.0.2.10 --username admin --type windows_log

# 时间戳按手册 10 位示例推定为 Unix 秒；查询指定页并保存完整响应
python tools/nsfocus_log_search.py --base-url https://192.0.2.10 --username admin --type windows_log --query-string "*" --start-time 1644543000 --end-time 1644546600 --page 1 --size 10 --output nsfocus_logs.json

# 只预览查询请求，不登录、不联网
python tools/nsfocus_log_search.py --base-url https://192.0.2.10 --type windows_log --dry-run

# 推荐限定查询范围，例如最近 24 小时，避免无时间限制查询超时
$queryEnd = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$queryStart = $queryEnd - 86400
python tools/nsfocus_log_search.py --base-url https://192.0.2.10 --username admin --type windows_log --start-time $queryStart --end-time $queryEnd --size 10
```

日志类型必须填写设备实际支持的标识；手册示例为 `windows_log`，多个类型用逗号分隔。未指定的时间字段不发送；默认查询一页（第 1 页、10 条），翻页使用 `--page`，不自动拉取全部日志。完整参数见 `--help`。

认证使用 `POST /api/v2/auth`（JSON 字段 `name`、`password`），随后携带 `Auth-Token` 调用 `POST /api/audit/log/search`。也可通过 `NSFOCUS_USERNAME` / `NSFOCUS_PASSWORD` 环境变量登录，或提供 `NSFOCUS_TOKEN` 跳过登录；不打印或保存认证响应。查询固定 `query_type="es"`，默认 `query_string="*"`。HTTP 错误、非 JSON、业务错误码会返回非零退出码；`partOfData=true` 时提醒缩小时间范围。

默认验证 HTTPS 证书，私有 CA 使用 `--ca-cert <PEM文件>`，受控测试自签名设备可显式加 `--insecure`。脚本忽略系统代理以直连内网设备，并拒绝 HTTP 重定向。手册文件名标注 V2.0R02F01，内页页眉为 V2.0R01F00；其他设备版本的兼容性仍需分别核验。

2026-10-15 经用户授权实机验证：登录返回 `errCode=0`，使用 Unix 秒时间戳查询最近 24 小时的 `windows_log` 成功返回 10 条日志，查询耗时约 10.8 秒。首次未限制时间范围的请求读取超时；最近一小时查询返回 0 条，因此空结果应结合查询时间与类型判断。调试响应保存在 `data/nsfocus_debug/logs_windows_last24h.json`（本地运行产物，`data/` 已被 Git 忽略）；账号密码及 Token 未写入仓库文件。

## 8. 健康检查与排障

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8080/health
```

典型响应：

```json
{
  "status": "ok",
  "databases": {
    "linux_logs": true,
    "windows_logs": true,
    "log_fusion": true,
    "log_hash": true
  }
}
```

注意：顶层 `status` 固定为 `ok`，数据库是否可用应以 `databases` 中各布尔值为准。

常见问题：

- 页面能打开但统计为 0：检查 `/health`；首页会吞掉汇总异常并显示 0。
- 日志接收返回 200 但没入库：看响应的 `persisted`、`inserted`、`skipped`、`failed` 与 `errors[].stage`——`parse` 表示解析失败（记录里有 `reason`），`insert` 表示数据库写入失败（`reason` 是 MySQL 错误摘要）。
- 存证页没有数据：**先分辨是「读不到」还是「真的没有」**——查询失败时接口返回 **503** 并在页面弹出错误提示，只有请求成功且 `total` 为 0 才代表确实没有存证记录。再确认 `/health` 里 `log_hash` 为 `true`。存证写入要求采集载荷带 `log` 字段；
  缺 `log` 或内容为空时只入库业务表、跳过存证，并在采集响应里给出 `errors[].stage="hash"`。
- 归档后数据不见了：归档是**导出后删除**。先看 `data/archive/` 下对应 CSV（文件名含时间戳），
  再用 `GET /api/v1/archive/log` 台账核对 `row_count`/`deleted_count`/`file_sha256`。
  需要把归档数据放回库里（恢复误删或继续测试）时用 `python tools/restore_archive.py data/archive/<文件>.csv`——默认只预览，确认后加 `--apply` 才写库；靠唯一索引幂等跳过已有记录。
- 归档文件找不到：`archive_dir` 的相对路径按**仓库根**解析（`<repo>/data/archive`），
  不是进程工作目录；接口 `/api/v1/archive/preview` 会返回实际使用的绝对路径。
- 页面元素错位或图表空白：确认 `/static/vendor/` 下文件返回 200（依赖已自托管，不再需要公网）。图表改动用两个自检工具定位（真实 `app.js` + 真实 ECharts 服务端渲染）：
  `node tools\chart-layout-check.cjs` 断言概览环形图的图例与图形不重叠、内容不被裁掉；
  `node tools\chart-render-check.cjs`（加 `--live=http://127.0.0.1:8080` 会用真实接口数据）断言所有图表都能渲染、空数据要画出「暂无」空态、文字不被顶出画布、热力图恰好 7×24 格，以及「重复推送同一份数据不得重画」（防闪屏）。
- 图表有图但全是 0：先分辨「窗口内确实没有日志」和「接口查询失败」——趋势/排行接口查询失败返回 **500**（页面弹出错误提示条），不会降级成全 0；心跳历史接口返回空序列则说明**进程刚重启**（历史缓冲在内存里），页面会写明这一原因。
- 图表一直在闪／整张图反复重播动画：说明有代码绕过了 `app.js` 的 `paint()` 直接 `setOption`，或者每次都带 `replaceMerge`。实时推送很密（WS 5 秒、概览汇总 2 秒），必须让「数据没变就不重画、只有数据变才合并更新」；`tests/test_frontend_contracts.py` 会断言 `setOption` 只在 `paint` 内出现一次，`node tools\chart-render-check.cjs` 里也有一节专门验证「重复推送不得重画」。
- 心跳页看不到某台机器的延迟曲线：先确认它是不是**离线**——离线客户端按设计不展示心跳延迟（延迟会随掉线时间无限增长），状态分布甜甜圈里仍能看到它的离线状态。
- 图表趋势整体偏 8 小时：后端时间桶一律 UTC（`...Z`），本地化只在前端做；擅自改成直接截取 ISO 字符串当横轴就会出现这个现象（`tests/test_frontend_contracts.py` 有守卫）。
- 图表数据不刷新：新聚合接口都带服务端 TTL 缓存（趋势 `1h` 5 秒、`24h` 15 秒、`7d`/`30d` 60 秒、排行 60 秒），响应里的 `cached`/`ageMs`/`generatedAt` 就是用来判断「是缓存还是没刷新」的；页面图表下方也直接显示这行新鲜度。
- 局域网无法访问：检查防火墙规则和代理/TUN 网卡；服务脚本无管理员权限时只打印补救命令。
- 模拟脚本启动失败：确认样例文件 `C:\Users\ironp\Desktop\data\linux\raw-logs.log` 与 `windows\raw-logs.log` 存在且非空，或用 `-DataDir` 指向正确目录。
- 心跳跨重启保留但实时连接丢失：数据在 MySQL，WebSocket 连接只存在于进程内。

## 9. 文档导航

- [系统架构](./ARCHITECTURE.md)
- [数据库设计](./DATABASE.md)
- [API 文档](./API.md)
- [开发日志](./CHANGELOG.md)
- [技术决策](./DECISIONS.md)
- [待开发任务](./TODO.md)
- [图表增强与二级导航实施方案](./PLAN-frontend-charts.md)（2026-10-13 已实施，含实施记录与偏差）

## 10. 文档事实边界

- 当前行为以 `backend/`、`frontend/` 和实际启动脚本为准。
- `旧前端/docs/` 是遗留接口契约，只用于理解历史目标。
- 根目录功能文档和开发清单包含规划与历史快照，不能直接当作当前验收结果。
- 本基线没有连接真实 MySQL 核验线上 DDL、索引和数据量；相关未知项见 `DATABASE.md`。
