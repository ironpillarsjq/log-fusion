# AGENTS.md

> 事实源：`docs/`（[README](./docs/README.md) / [ARCHITECTURE](./docs/ARCHITECTURE.md) / [DATABASE](./docs/DATABASE.md) / [API](./docs/API.md) / [DECISIONS](./docs/DECISIONS.md) / [CHANGELOG](./docs/CHANGELOG.md) / [TODO](./docs/TODO.md)）。本文件只沉淀易踩坑的操作性提示，细节以 `docs/` 和代码为准；开发流程见 `AGENT_RULES.md`。

## 活代码边界

- `backend/` + `frontend/`：**当前实现**。FastAPI 入口 `backend/app/main.py`；Jinja2 模板 `backend/app/templates/dashboard.html`；`frontend/` 经 `/static` 挂载，页面实际只用 `app.js`（`styles.css`/`index.html` 未被模板引用）。前端第三方库自托管在 `frontend/vendor/`（文件名带版本号，`/static/vendor/*` 允许长缓存、其他 `/static/*` 禁缓存），不要再改回 CDN 引用。
- `旧前端/`（Vue）、`旧后端/`（Spring Boot）：**遗留只读，不要修改**；`旧前端/docs/` 是历史接口契约参考。
- 根目录 `项目功能文档.md`、`前端功能开发清单.md`、`后端功能开发清单.md` 含规划/历史快照，**不是**当前验收结论。

## 运行命令

```powershell
# 服务（默认 0.0.0.0:8080）
.\启动服务.ps1                 # 可选 -BindHost -Port -Reload

# 模拟 Fluent Bit 客户端（另开窗口）
.\启动模拟请求.ps1 -Count 3    # 0 = 持续发送
```

两个脚本都硬编码解释器 `D:\Data\Miniconda3\conda_envs\log-fusion\python.exe`，并设 `PYTHONPATH=backend/`（`from .config import ...` 依赖它）。手跑等价于：`conda activate log-fusion; Set-Location .\backend; $env:PYTHONPATH=(Get-Location).Path; python run.py`（`run.py` 固定 0.0.0.0:8080 且 reload）。

**双击请用 `启动服务.cmd` / `启动模拟请求.cmd`，不要双击 `.ps1`**：本机 `.ps1` 的默认关联是 Windows 记事本（`AppX…Microsoft.WindowsNotepad`），双击只会用记事本打开、不执行脚本（表现为窗口一闪即退）。`.cmd` 内容全 ASCII，经 `tools\run-server.ps1`、`tools\run-simulator.ps1` 转发，避免 cmd.exe 按控制台代码页误读中文路径。模拟脚本的完整运行记录在 `%TEMP%\log-fusion-simulator.log`。

测试：仓库根目录 `python -m pytest`（用例在 `tests/`，通过类级 patch 禁用 `Engine.connect/begin` 并把连接参数指向不可用占位值，**不连接任何数据库**）。无 lint / typecheck 配置。

## 必须遵守

- 编辑任何 `.ps1` 后确认文件头为 `EF BB BF`（UTF-8 with BOM）。PowerShell 5.1 会把无 BOM 的中文脚本按 GBK 解析，导致「字符串缺少终止符」之类**解析错误**。
- 中文/特殊字符路径一律用 `-LiteralPath`（`-Path` 会做通配符展开）。
- 终端中文乱码是控制台编码问题：需要时先 `[Console]::OutputEncoding=[Text.Encoding]::UTF8`。

## 服务端状态与数据库

- MySQL 在 `192.168.5.6:3306`，三个逻辑库：`linux_logs`、`windows_logs`、`log_fusion`。配置在 `backend/app/config.py`（默认 `root`/`mysql_YbJWfE`），可用环境变量或 `backend/.env` 覆盖（`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`LINUX_DB`、`WINDOWS_DB`、`LOG_FUSION_DB`）。默认 `.env` 相对 CWD，启动脚本切到 `backend/`。
- **心跳已持久化**到 `log_fusion.client_status`，启动时 `create_all` 自动建表；`log_fusion` 库不存在则建表失败只记日志、应用照常启动，但 `POST /api/v1/heartbeat` 返回 503。状态阈值 10s 在线 / 30s 延迟 / 其余离线，后台 watchdog 每 5s 重算并广播。
- **实时通道仍是进程内存**：WebSocket 连接集合（`ws.py`）和 SSE `event_queue`（`maxsize=1000`）不跨进程。**必须单 worker**，多 worker 会导致 SSE 分流、WS 广播不完整、watchdog 重复。
- 写入/表缺失不报错给客户端：`POST /api/v1/logs/{platform}` 无论解析失败还是入库失败都返回 200，用 `persisted` 和 `errors[].stage`（`parse`/`insert`）区分；evidence 接口表缺失时返回空集合。排查「没数据」先看 `/health` 的 `databases` 与响应里的这些字段。
- **采集解析层是 `backend/app/parsers.py`**（参考只读的 `旧后端/log-backend/src/main/python/`）。最容易踩的坑都在这四个数字上：时间必须先转 UTC `datetime`（MySQL 不接受 `2026-10-02T09:31:59Z`、`2026-10-02 20:03:18 +0800`，否则 **1292**）；整型列空值写 `NULL` 而不是 `""`（否则 **1366**）；字符串按列宽截断（否则 **1406**）；行字段必须按 `catalog.LINUX_TABLE_FIELDS` / `WINDOWS_TABLE_FIELDS` 裁剪（否则 **1054**）。
- **浏览器同源连接上限是 6 条（HTTP/1.1）**：前端每个页面只保留 1 条 WebSocket（不占 HTTP 连接额度），REST 请求因此不会被长连接挤掉。历史教训：早期版本每页还额外开 1 条日志 SSE，刷新与多标签会让 SSE 累积到 6 条（`/health` 的 `realtime.sse_clients`），所有 REST 请求（含自动刷新）随之永久排队——现象是页面刷新一直转圈、心跳页无数据，而服务端毫秒级返回。`app.js` 现在不再使用 EventSource（ADR-012），并在 `pagehide` 断开 WebSocket、给 fetch 加 8 秒超时；SSE 接口仅保留给外部消费方。
- Linux 表名 = 分类名（7 个，`app/catalog.py`），Windows 表名 = `{channel}_logs`；`services.query_category` 只校验分类名是否为 ASCII 字母/数字/下划线，**不**校验是否属于 catalog。Linux 表**列不齐**：`hostname`/`source_address` 部分表没有，关键词查询与 `/api/v1/logs/recent` 必须按表适配（见 `docs/DATABASE.md` §4.3）。

## 模拟客户端的数据依赖

`启动模拟请求.ps1` / `backend/simulator/send_simulated_clients.py` 读取仓库**之外**的 `C:\Users\ironp\Desktop\data\linux\raw-logs.log` 与 `windows\raw-logs.log`；文件缺失或为空时直接退出。模拟器默认 `--url http://127.0.0.1:8000`，脚本会覆盖为 8080。

## 局域网访问

`启动服务.ps1` 默认绑定 `0.0.0.0`，并尝试添加防火墙入站规则 `log-fusion <port>`（无管理员权限时打印补救命令并继续）。其他主机仍连不上时：本机有代理 TUN 网卡 `ziyoumao (198.18.0.1)`，先关掉代理的 TUN/系统模式再排查。

## Git

已添加 `.gitignore`（忽略 `__pycache__/`、`*.pyc`、`.vscode/`）。历史上被跟踪的 `.pyc` 已 `git rm --cached`；不要重新提交生成的 `.pyc`。
