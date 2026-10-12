# Log Fusion 项目总览

> 文档基线日期：2026-10-10（同日按当前工作区代码复核）。本文描述当前 FastAPI 实现；遗留 Spring Boot/Vue 工程和规划文档不代表现行行为。

## 1. 项目目标与状态

Log Fusion 是面向运维和审计场景的日志融合平台，接收 Linux 审计日志与 Windows 事件日志，提供分类入库、统计查询、CSV 导出、SSE 实时通知、客户端心跳监控和原始日志存证。

当前项目处于开发中期：核心查询、采集写入、原始日志 SHA-256 存证与归档、仪表盘、持久化心跳和前端心跳实时通道（WebSocket + 断线重连 + 假死检测）已经具备；认证授权、正式数据库迁移、存证逐条校验接口、可靠的多订阅者实时分发和自动化测试尚未完成。

## 2. 当前核心能力

- FastAPI 提供页面、HTTP API、SSE 与 WebSocket。
- Jinja2 在 `/` 服务端渲染仪表盘，Alpine.js 管理页面状态，HTMX 刷新数据库状态，ECharts 展示分类统计。
- 查询 7 类 Linux 日志表和 5 类 Windows 日志表；融合日志列表按 **30 条/页**分页（可翻页，支持平台/类别/关键词筛选）。
- 接收 Linux/Windows 日志：`parsers.py` 把原始记录解析成数据库行（含时间归一化与按表裁剪），再批量入库；响应用 `inserted`/`skipped`/`failed`/`hashed`/`errors[].stage` 区分“解析失败”“入库失败”和“存证跳过”，并始终尝试发送实时事件。
- 每条日志入库前由 `hashing.py` 按 `sha256(timestamp + 0x1F + log)` 计算摘要，把原始 `log` 原文与摘要写入 `log_hash.log_hash`（唯一索引去重；缺 `log` 则跳过存证且不影响入库）。
- **完整性存证页**展示 `log_hash` 真实存证记录，按 **30 条/页**分页，可按平台与关键词筛选。
- **存证归档**：把历史存证导出为 CSV 再从数据库删除，分两种方式——
  **自动归档**（后台每天一次，导出 `created_at` 早于 3 个日历月的记录）与
  **手动归档**（页面上选时间区间导出）。导出文件永久保留在 `data/archive/`，
  且**只有确认文件落盘并校验一致后才执行删除**，失败时一条都不删。
- 在 `log_fusion.client_status` 中持久化客户端心跳，按 10 秒/30 秒阈值计算在线、延迟、离线状态。
- 通过 `/ws/client-monitor` 广播心跳快照（每次心跳成功时，以及后台每 5 秒的巡检轮次；页面已接入该通道并实现断线重连与假死检测），通过 `/api/v1/logs/stream` 推送日志事件。

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
│                               # restore_archive.py（把归档 CSV 导回 log_hash，默认只预览）
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
| 实时 | sse-starlette 3.5.0、FastAPI WebSocket |
| 浏览器 UI | Bootstrap 5.3.3、HTMX 2.0.4、Alpine.js 3.14.8、ECharts 5.5.1（自托管于 `frontend/vendor/`） |
| 数据库 | MySQL：`linux_logs`、`windows_logs`、`log_fusion`、`log_hash` |

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

测试位于 `tests/`：通过类级 patch 禁用 `Engine.connect/begin`，并把连接参数指向不可用占位值，因此**不连接任何数据库**，可在无 MySQL 环境重复运行。当前覆盖类别名校验、列表/导出取数上限、关键词与最近日志的列适配、采集解析（时间/auditd 映射/Windows 字段/按表裁剪）与入库失败分阶段上报、心跳状态阈值、静态资源与模板契约、以及路由与 `docs/API.md` §2 的一致性。仍没有 lint / typecheck 配置。

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

`--count 0` 表示持续发送；默认间隔 3 秒；`--clients` 可覆盖客户端标识列表。模拟器自身的 URL 默认值仍是 `http://127.0.0.1:8000`，因此直接调用时应显式传 `--url`。

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
    "log_fusion": true
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
- 页面元素错位或图表空白：确认 `/static/vendor/` 下文件返回 200（依赖已自托管，不再需要公网）。改动概览环形图布局后，用 `node tools/chart-layout-check.cjs` 自检（真实 `app.js` + 真实 ECharts 服务端渲染，断言图例与图形不重叠、内容不被裁掉）。
- 局域网无法访问：检查防火墙规则和代理/TUN 网卡；服务脚本无管理员权限时只打印补救命令。
- 模拟脚本启动失败：确认样例文件 `C:\Users\ironp\Desktop\data\linux\raw-logs.log` 与 `windows\raw-logs.log` 存在且非空，或用 `-DataDir` 指向正确目录。
- 心跳跨重启保留但实时连接丢失：数据在 MySQL，WebSocket 连接和日志 SSE 队列仅存在于进程内。

## 9. 文档导航

- [系统架构](./ARCHITECTURE.md)
- [数据库设计](./DATABASE.md)
- [API 文档](./API.md)
- [开发日志](./CHANGELOG.md)
- [技术决策](./DECISIONS.md)
- [待开发任务](./TODO.md)

## 10. 文档事实边界

- 当前行为以 `backend/`、`frontend/` 和实际启动脚本为准。
- `旧前端/docs/` 是遗留接口契约，只用于理解历史目标。
- 根目录功能文档和开发清单包含规划与历史快照，不能直接当作当前验收结果。
- 本基线没有连接真实 MySQL 核验线上 DDL、索引和数据量；相关未知项见 `DATABASE.md`。
