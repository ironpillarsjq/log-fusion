# 供「启动模拟请求.cmd」调用：批处理里不写中文路径，避免 cmd.exe 代码页乱码。
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $root '启动模拟请求.ps1') @args