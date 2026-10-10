param(
    [string]$Url = "http://127.0.0.1:8080",
    [string]$DataDir = "C:\Users\ironp\Desktop\data",
    [int]$Interval = 3,
    [int]$Count = 0,
    [string[]]$Clients = @("sim-linux-001", "sim-windows-001"),
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonRoot = Join-Path $ProjectRoot "backend"
$Simulator = Join-Path $PythonRoot "simulator\send_simulated_clients.py"
$EnvPython = "D:\Data\Miniconda3\conda_envs\log-fusion\python.exe"
$LogFile = Join-Path $env:TEMP "log-fusion-simulator.log"

# 全程记录到日志文件：双击运行时窗口会在退出瞬间关闭，只有日志能还原现场。
$transcript = $false
try {
    Start-Transcript -Path $LogFile -Force | Out-Null
    $transcript = $true
} catch {
    Write-Host "无法写入日志文件 $LogFile ：$($_.Exception.Message)" -ForegroundColor DarkYellow
}

function Wait-BeforeExit {
    # 双击运行时窗口会在脚本结束时立刻关闭（表现为“闪退”），这里留一次按键，
    # 保证报错信息能被看到。持续运行模式（-Count 0）不会走到这里。
    if (-not $NoPause) {
        Write-Host ""
        Write-Host "按回车键关闭窗口…" -ForegroundColor DarkGray
        try { [void](Read-Host) } catch { }
    }
}

function Write-LaunchContext {
    Write-Host "=== 启动上下文 ===" -ForegroundColor DarkGray
    Write-Host "时间        ：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "PowerShell  ：$($PSVersionTable.PSVersion) / $($PSVersionTable.PSEdition)"
    Write-Host "宿主        ：$($Host.Name)"
    Write-Host "当前目录    ：$(Get-Location)"
    Write-Host "脚本路径    ：$PSCommandPath"
    Write-Host "日志文件    ：$LogFile"
    Write-Host "参数        ：Url=$Url DataDir=$DataDir Interval=$Interval Count=$(if ($Count -eq 0) { '持续运行' } else { $Count }) Clients=$($Clients -join ',')"
    Write-Host ""
}

try {
    Write-LaunchContext

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
    Write-Host ""

    & $EnvPython @Arguments
    $childExit = $LASTEXITCODE
    Write-Host ""
    Write-Host "python 进程退出码：$childExit"

    # 0xC000013A：进程被 Ctrl+C 终止，属于正常停止
    if ($childExit -eq -1073741510) {
        Write-Host "已手动停止（Ctrl+C）。" -ForegroundColor Yellow
    }
    elseif ($childExit -ne 0) {
        throw "模拟请求已退出，退出码：$childExit"
    }
}
catch {
    Write-Host ""
    Write-Host "模拟请求失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "出错位置：$($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    Write-Host "排查建议：" -ForegroundColor Yellow
    Write-Host "  1) 服务是否在运行：$Url/health" -ForegroundColor Yellow
    Write-Host "  2) 样例文件是否存在且非空：$DataDir\linux\raw-logs.log、$DataDir\windows\raw-logs.log" -ForegroundColor Yellow
    Write-Host "  3) 若响应为 502 或被代理拦截，请把 127.0.0.1、localhost 加入系统代理绕过列表" -ForegroundColor Yellow
    Write-Host "完整记录见日志文件：$LogFile" -ForegroundColor DarkGray
    Wait-BeforeExit
    if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
    exit 1
}

Wait-BeforeExit
if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
