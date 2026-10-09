# Windows EVTX 导入 MySQL

## 1. 安装依赖

在项目根目录执行：

```powershell
python -m pip install -r scripts/requirements.txt
```

## 2. 配置连接信息

推荐用环境变量（也可以直接修改 `config.py` 中的默认值）：

```powershell
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="3306"
$env:DB_USER="你的账号"
$env:DB_PASSWORD="你的密码"
$env:DB_NAME="windows_logs"
```

## 3. 创建数据库和表

```powershell
python -m scripts.create_tables
```

会创建 `application_logs`、`security_logs`、`setup_logs`、`system_logs`、`forwardedevents_logs` 五张表。每张表包含 Windows `System` 节点的公共字段，以及一个 JSON 类型的 `eventdata` 字段；`system_extra` 用于保留未来遇到的未知 System 子节点。

## 4. 导入日志

```powershell
python -m scripts.import_logs
```

默认扫描项目下的 `2008` 和 `2016` 目录。也可以指定目录：

```powershell
python -m scripts.import_logs --log-dir .\2008 --log-dir .\2016 --batch-size 500
```

脚本按文件批量提交事务，源文件和年份也会写入表中。重复执行会再次插入记录；如需幂等导入，可在业务上增加哈希列/唯一索引。
