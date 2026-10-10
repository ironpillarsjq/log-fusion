# Log Fusion FastAPI 后端

> 当前实现的完整说明以 [`docs/`](../docs/README.md) 为准，本文件只保留最短启动指引。

## 启动

```powershell
conda activate log-fusion
Set-Location -LiteralPath .\backend
$env:PYTHONPATH = (Get-Location).Path
python run.py        # 固定 0.0.0.0:8080，开启 reload
```

或在仓库根目录执行 `.\启动服务.ps1`。访问：

- 仪表盘 `http://127.0.0.1:8080/`
- 接口文档 `http://127.0.0.1:8080/docs`
- 健康检查 `http://127.0.0.1:8080/health`

页面为 FastAPI + Jinja2 服务端渲染，HTMX 局部刷新、Alpine.js 管状态、Bootstrap 5 组件、ECharts 图表、SSE 日志事件、WebSocket 心跳快照，全部走 CDN，无前端构建步骤。

## 数据库

三个逻辑库：`linux_logs`、`windows_logs`、`log_fusion`（心跳状态 `client_status`）。连接参数从环境变量或 `backend/.env` 读取：`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`LINUX_DB`、`WINDOWS_DB`、`LOG_FUSION_DB`。默认 `.env` 相对启动时的 CWD（即 `backend/.env`）。

## 主要能力

日志类别与统计、Linux/Windows 分类查询、关键词检索、CSV 导出、SSE 实时日志流、日志采集接口（`app/parsers.py` 解析 auditd/syslog/winlog 后入库，失败按 `parse`/`insert` 阶段上报）、`log_fusion.client_status` 心跳持久化与 WebSocket 广播、存证读取兼容层（表缺失时返回空集合）。

## 模拟 Fluent Bit 客户端

在仓库根目录运行 `.\启动模拟请求.ps1 -Count 3`（`-Count 0` 持续发送）。脚本读取仓库外的样例文件 `C:\Users\ironp\Desktop\data\linux\raw-logs.log` 与 `windows\raw-logs.log`（可用 `-DataDir` 覆盖目录），按原字段格式发送日志和心跳。直接运行：

```powershell
conda activate log-fusion
Set-Location -LiteralPath .\backend
$env:PYTHONPATH = (Get-Location).Path
python .\simulator\send_simulated_clients.py --url http://127.0.0.1:8080 --count 3
```

模拟器自身默认 URL 是 `http://127.0.0.1:8000`，直接运行时需显式传 `--url`。
