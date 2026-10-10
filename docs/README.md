# Log Fusion 项目总览

> 文档基线日期：2026-10-10（同日按当前工作区代码复核）。本文描述当前 FastAPI 实现；遗留 Spring Boot/Vue 工程和规划文档不代表现行行为。

## 1. 项目目标与状态

Log Fusion 是面向运维和审计场景的日志融合平台，接收 Linux 审计日志与 Windows 事件日志，提供分类入库、统计查询、CSV 导出、SSE 实时通知、客户端心跳监控和存证记录兼容查询。

当前项目处于开发中期：核心查询、采集写入、仪表盘、持久化心跳和前端心跳实时通道（WebSocket + 断线重连 + 假死检测）已经具备；认证授权、正式数据库迁移、完整存证写入/校验、可靠的多订阅者实时分发和自动化测试尚未完成。

## 2. 当前核心能力

- FastAPI 提供页面、HTTP API、SSE 与 WebSocket。
- Jinja2 在 `/` 服务端渲染仪表盘，Alpine.js 管理页面状态，HTMX 刷新数据库状态，ECharts 展示分类统计。
- 查询 7 类 Linux 日志表和 5 类 Windows 日志表。
- 接收 Linux/Windows 日志：`parsers.py` 把原始记录解析成数据库行（含时间归一化与按表裁剪），再批量入库；响应用 `inserted`/`skipped`/`failed`/`errors[].stage` 区分“解析失败”和“入库失败”，并始终尝试发送实时事件。
- 在 `log_fusion.client_status` 中持久化客户端心跳，按 10 秒/30 秒阈值计算在线、延迟、离线状态。
- 通过 `/ws/client-monitor` 广播心跳快照（每次心跳成功时，以及后台每 5 秒的巡检轮次；页面已接入该通道并实现断线重连与假死检测），通过 `/api/v1/logs/stream` 推送日志事件。
- 查询并导出已有 `audit_log_evidence` 表；当前代码不创建也不写入该表。

## 3. 代码范围

```text
log-fusion/
├── backend/                    # 当前 FastAPI 后端
│   ├── app/
│   │   ├── main.py             # 应用、路由、采集写入、实时通道
│   │   ├── config.py           # 环境配置
│   │   ├── db.py               # 三个 MySQL Engine 与会话
│   │   ├── models.py           # client_status ORM 模型
│   │   ├── services.py         # 日志查询与统计
│   │   ├── parsers.py          # 采集记录解析（auditd/syslog/winlog → 数据库行）
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
├── tests/                      # pytest 单元/契约测试（不连接数据库）
├── tools/                      # .cmd 启动器的 ASCII 转发脚本；chart-layout-check.cjs（概览环形图布局自检）
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
| 数据库 | MySQL：`linux_logs`、`windows_logs`、`log_fusion` |

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
| `LOG_FUSION_DB` | `log_fusion` | 平台状态库 |
| `RAW_STORAGE_DIR` | `data/raw` | 原始存储目录配置；当前代码未使用 |

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
- 存证接口返回空：已核验本环境 `linux_logs` 与 `log_fusion` 中都不存在 `audit_log_evidence`，该接口必然返回空集合；需先确认该表由谁写入。
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
