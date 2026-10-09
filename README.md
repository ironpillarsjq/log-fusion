# log-fusion

## 一键启动脚本

在项目根目录打开 PowerShell：

```powershell
.'启动服务.ps1'
```

另开一个 PowerShell 窗口启动模拟请求：

```powershell
.'启动模拟请求.ps1' -Interval 3 -Count 0
```

`-Count 0` 表示持续发送；联调时可以使用 `-Count 3` 只发送三轮。服务脚本可以传入 `-BindHost`、`-Port` 和 `-Reload`，模拟脚本可以传入 `-Url`、`-DataDir` 和 `-Clients` 参数。
