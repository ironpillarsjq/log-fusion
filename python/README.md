# Log Fusion FastAPI

## 启动

```powershell
conda activate log-fusion
cd python
$env:PYTHONPATH = (Get-Location).Path
python run.py
```

访问 `http://127.0.0.1:8000/` 查看 Jinja2 仪表盘，访问 `/docs` 查看接口文档。

数据库配置从环境变量读取：`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`LINUX_DB`、`WINDOWS_DB`。默认连接到项目检查过的 `linux_logs` 和 `windows_logs`。

当前实现已包含：日志类别和统计、Linux/Windows 分类查询、CSV 导出、SSE 实时流、旧版日志接收接口、心跳监控接口、Jinja2 首页，以及存证接口的读取/导出兼容层。存证表尚不存在时接口返回空集合，后续创建表后会自动读取。
