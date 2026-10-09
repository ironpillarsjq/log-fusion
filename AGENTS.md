# AGENTS.md

## 项目结构（哪些是活代码）

- `python/` + `frontend/`：**当前实现**。FastAPI（`python/app/main.py`）+ Jinja2 服务端渲染（`python/app/templates/dashboard.html`）+ 静态资源挂载在 `/static`（指向根目录 `frontend/`，含 `app.js`/`styles.css`）。无前端构建步骤，HTMX / Alpine.js / ECharts 全部走 CDN。
- `旧前端/`（Vue SPA）、`旧后端/`（Spring Boot + Maven）：**遗留代码，不要修改**。`旧前端/docs/` 下的接口契约文档是 API 形状的有用参考。
- 根目录的 `启动服务.ps1` / `启动模拟请求.ps1`：标准启动方式。

## 运行命令

```powershell
# 服务（默认 0.0.0.0:8080）
.\启动服务.ps1            # 可选 -BindHost -Port -Reload

# 模拟 Fluent Bit 客户端（另开窗口）
.\启动模拟请求.ps1 -Count 3   # 0 = 持续发送
```

两个脚本都硬编码了 conda 环境解释器 `D:\Data\Miniconda3\conda_envs\log-fusion\python.exe`，并设置 `PYTHONPATH=python/`（`from .config import ...` 依赖它）。直接手跑等价于：`conda activate log-fusion; cd python; $env:PYTHONPATH=(Get-Location).Path; python run.py`（`run.py` 端口 8080）。`python/README.md` 里写的 8000 是过期的，以脚本/`run.py` 为准。

无 lint / typecheck / 测试套件。`requirements.txt` 里有 `pytest`、`httpx`，但仓库里没有任何测试文件——新增代码时不要假设存在可跑的测试命令。

## PowerShell 脚本必须是 UTF-8 带 BOM

Windows PowerShell 5.1 对无 BOM 的 `.ps1` 按系统 GBK 解码，脚本里的中文（`；`、`：` 等全角字符）会导致**解析错误**（如 "字符串缺少终止符"）。编辑任何 `.ps1` 后确认文件头是 `EF BB BF`；本仓库所有 `.ps1` 都是 UTF-8 with BOM。

其他 PowerShell 陷阱：
- 文件/目录名含中文，`Test-Path` / `Get-Content` / `Remove-Item` 等一律用 `-LiteralPath`（`-Path` 会做通配符展开）。
- 终端输出中文乱码是控制台编码问题，不是代码问题；需要时先 `[Console]::OutputEncoding=[Text.Encoding]::UTF8`。

## 服务端状态与数据库

- MySQL 在 `192.168.5.6:3306`，配置在 `python/app/config.py`（默认 `root`/`mysql_YbJWfE`，两个库 `linux_logs`、`windows_logs`）。可用环境变量或 `python/.env` 覆盖（`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`LINUX_DB`、`WINDOWS_DB`）。
- **心跳与 SSE 全是进程内存**：`heartbeats` dict 和 `event_queue` 在 `main.py` 模块级定义。重启即丢失；不要用多 worker/多进程 uvicorn，否则心跳监控和 `/api/v1/logs/stream` 会失效。
- 表不存在或写入失败不会报错给客户端：`POST /api/v1/logs/{platform}` 会把 `persisted` 置为 `false` 但仍然推入实时流；evidence 相关接口表缺失时返回空集合。排查"没数据"时先看 `/health` 的 `databases` 状态和接口返回里的 `persisted`。
- Linux 日志表名 = 分类名（7 个，见 `app/catalog.py`），Windows 表名 = `{channel}_logs`。`services.query_category` 对分类名做了 `isalnum` 校验。

## 模拟客户端的数据依赖

`启动模拟请求.ps1` / `python/simulator/send_simulated_clients.py` 读取仓库**之外**的样例文件：`C:\Users\ironp\Desktop\data\linux\raw-logs.log` 和 `windows\raw-logs.log`。文件不存在或为空时脚本直接退出。模拟器默认 `--url http://127.0.0.1:8000`，通过 `启动模拟请求.ps1` 调用时会被改成 8080。

## 局域网访问

`启动服务.ps1` 默认绑定 `0.0.0.0` 并尝试自动添加防火墙入站规则 `log-fusion <port>`（无管理员权限时会打印补救命令并继续）。若其他主机仍连不上：本机开了代理 TUN 网卡 `ziyoumao (198.18.0.1)`，先关掉代理的 TUN/系统模式再排查。

## Git 注意事项

无 `.gitignore`，`__pycache__/`、`*.pyc`、`.vscode/` 目前被跟踪。不要提交新生成的 `.pyc`。
