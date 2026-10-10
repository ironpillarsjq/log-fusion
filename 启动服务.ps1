param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8080,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonRoot = Join-Path $ProjectRoot "backend"
$EnvPython = "D:\Data\Miniconda3\conda_envs\log-fusion\python.exe"

if (-not (Test-Path -LiteralPath $EnvPython)) {
    throw "找不到 log-fusion 环境 Python：$EnvPython"
}

if (-not (Test-Path -LiteralPath (Join-Path $PythonRoot "app\main.py"))) {
    throw "找不到 FastAPI 项目目录：$PythonRoot"
}

$env:PYTHONPATH = $PythonRoot
Set-Location -LiteralPath $PythonRoot

if (-not (Get-NetFirewallRule -DisplayName "log-fusion $Port" -ErrorAction SilentlyContinue)) {
    try {
        New-NetFirewallRule -DisplayName "log-fusion $Port" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -ErrorAction Stop | Out-Null
        Write-Host "已添加 Windows 防火墙入站规则：log-fusion $Port" -ForegroundColor Green
    } catch {
        Write-Host "未添加防火墙规则（需要管理员权限）。若局域网仍无法访问，请用管理员 PowerShell 执行：" -ForegroundColor Yellow
        Write-Host "  New-NetFirewallRule -DisplayName 'log-fusion $Port' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow" -ForegroundColor Yellow
    }
}

$Arguments = @(
    "-m", "uvicorn", "app.main:app",
    "--host", $BindHost,
    "--port", $Port
)
if ($Reload) {
    $Arguments += "--reload"
}

$LocalIps = if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "*") {
    @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' } |
        ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
        Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' } |
        ForEach-Object { $_.Address.IPAddressToString })
} else {
    @($BindHost)
}
$AccessUrls = ($LocalIps | ForEach-Object { "http://$_`:$Port/" }) -join "  "

Write-Host "正在启动日志融合服务..." -ForegroundColor Cyan
Write-Host "监听端口：$Port（所有网卡）" -ForegroundColor Green
Write-Host "访问地址：$AccessUrls" -ForegroundColor Green
Write-Host "接口文档：$($LocalIps | ForEach-Object { "http://$_`:$Port/docs" } | Select-Object -First 1)" -ForegroundColor DarkGreen
Write-Host "按 Ctrl+C 停止服务。" -ForegroundColor Yellow

& $EnvPython @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "FastAPI 服务已退出，退出码：$LASTEXITCODE"
}
