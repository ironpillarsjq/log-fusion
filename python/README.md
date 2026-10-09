# Log Fusion FastAPI

## 启动

```powershell
conda activate log-fusion
cd python
$env:PYTHONPATH = (Get-Location).Path
python run.py
```

访问 `http://127.0.0.1:8000/` 查看 Jinja2 仪表盘，访问 `/docs` 查看接口文档。

前端采用 FastAPI + Jinja2 服务端渲染，使用 HTMX 做局部刷新、Alpine.js 管理页面状态，Bootstrap 5 提供响应式组件，ECharts 提供图表，SSE 提供日志实时事件；不需要独立的 Vue/React 构建工程。

数据库配置从环境变量读取：`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`LINUX_DB`、`WINDOWS_DB`。默认连接到项目检查过的 `linux_logs` 和 `windows_logs`。

当前实现已包含：日志类别和统计、Linux/Windows 分类查询、CSV 导出、SSE 实时流、旧版日志接收接口、心跳监控接口、Jinja2 首页，以及存证接口的读取/导出兼容层。存证表尚不存在时接口返回空集合，后续创建表后会自动读取。

## 模拟 Fluent Bit 客户端

模拟脚本会读取 `C:\Users\ironp\Desktop\data\linux\raw-logs.log` 和 `windows\raw-logs.log` 的真实样例，按原字段格式发送日志，同时发送 `client_id` 心跳：

```powershell
conda activate log-fusion
cd C:\Users\ironp\Desktop\log-fusion\python
$env:PYTHONPATH = (Get-Location).Path
python simulator\send_simulated_clients.py --interval 3
```

联调时可以用 `--count 3` 只发送三轮；默认 `--count 0` 持续运行，按 `Ctrl+C` 停止。
