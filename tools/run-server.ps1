# 供「启动服务.cmd」调用：批处理里不写中文路径，避免 cmd.exe 代码页乱码。
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $root '启动服务.ps1') @args