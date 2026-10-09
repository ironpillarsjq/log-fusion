param(
    [string]$Url = "http://127.0.0.1:8080",
    [string]$DataDir = "C:\Users\ironp\Desktop\data",
    [int]$Interval = 3,
    [int]$Count = 0,
    [string[]]$Clients = @("sim-linux-001", "sim-windows-001")
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonRoot = Join-Path $ProjectRoot "python"
$Simulator = Join-Path $PythonRoot "simulator\send_simulated_clients.py"
$EnvPython = "D:\Data\Miniconda3\conda_envs\log-fusion\python.exe"

if (-not (Test-Path -LiteralPath $EnvPython)) {
    throw "找不到 log-fusion 环境 Python：$EnvPython"
}
if (-not (Test-Path -LiteralPath $Simulator)) {
    throw "找不到模拟脚本：$Simulator"
}
if (-not (Test-Path -LiteralPath $DataDir)) {
    throw "找不到 Fluent Bit 数据目录：$DataDir"
}
if ($Interval -lt 1) {
    throw "Interval 必须大于等于 1 秒"
}
if ($Count -lt 0) {
    throw "Count 不能小于 0；0 表示持续发送"
}

$env:PYTHONPATH = $PythonRoot
Set-Location -LiteralPath $PythonRoot

$Arguments = @(
    $Simulator,
    "--url", $Url,
    "--data-dir", $DataDir,
    "--interval", $Interval,
    "--count", $Count,
    "--clients"
)
$Arguments += $Clients

Write-Host "正在启动模拟请求客户端..." -ForegroundColor Cyan
Write-Host "目标服务：$Url" -ForegroundColor Green
Write-Host "数据目录：$DataDir" -ForegroundColor Green
Write-Host "发送间隔：$Interval 秒；发送轮数：$(if ($Count -eq 0) { '持续运行' } else { $Count })" -ForegroundColor Green
Write-Host "按 Ctrl+C 停止模拟请求。" -ForegroundColor Yellow

& $EnvPython @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "模拟请求已退出，退出码：$LASTEXITCODE"
}
