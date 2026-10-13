# Elysia 进程级看门狗 —— 补上"崩了不会自愈"的**剩下那一半**
#
# 背景（19.4 缺点 1）：协程级自愈已就位（core/supervisor.py：心跳协程异常退出会被拉起），
# 但**整个进程**被杀（OOM / 手动 taskkill / 蓝屏重启后）仍需要有人拉。本脚本负责那一半。
#
# 用法（在项目根目录执行）:
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1              # 只检查一次
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Register    # 每 5 分钟自动检查
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Unregister  # 取消
#
# 尊重"故意的停机"：`data\run\no_autostart` 存在时**不拉起**。
#   soul.ps1 stop  → 创建该标记（你不想让她跑）
#   soul.ps1 start → 清除该标记（你又想让她跑了）
param(
    [int]$IntervalMinutes = 5,
    [switch]$Register,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$RunDir      = Join-Path $ProjectRoot "data\run"
$LogDir      = Join-Path $ProjectRoot "data\logs"
$PidFile     = Join-Path $RunDir "soul.pid"
$NoAutoStart = Join-Path $RunDir "no_autostart"
$WatchLog    = Join-Path $LogDir "watchdog.log"
$SoulScript  = Join-Path $PSScriptRoot "soul.ps1"
$TaskName    = "Elysia-Watchdog"

function Write-WatchLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $WatchLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Test-SoulAlive {
    if (-not (Test-Path $PidFile)) { return $false }
    $content = (Get-Content $PidFile -Raw).Trim()
    if ($content -notmatch "^\d+$") { return $false }
    return $null -ne (Get-Process -Id ([int]$content) -ErrorAction SilentlyContinue)
}

function Register-WatchdogTask {
    $action = "powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $minutes = [Math]::Max(1, $IntervalMinutes)
    & schtasks.exe /Create /TN $TaskName /TR $action /SC MINUTE /MO $minutes /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "注册看门狗任务失败（exit=$LASTEXITCODE）——可能需要在**管理员** PowerShell 中执行"
        exit 1
    }
    Write-Host "[ok] 已注册看门狗任务 '$TaskName'（每 $minutes 分钟检查一次）。取消：-Unregister"
}

function Unregister-WatchdogTask {
    & schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "[ok] 已取消看门狗任务 '$TaskName'（本来不存在也算成功）"
}

if ($Register) { Register-WatchdogTask; exit 0 }
if ($Unregister) { Unregister-WatchdogTask; exit 0 }

if (Test-SoulAlive) { exit 0 }   # 她活着，什么都不做（不写日志，避免刷屏）

if (Test-Path $NoAutoStart) {
    Write-WatchLog "灵魂未在运行，但存在 no_autostart 标记（你故意停的）→ 不拉起"
    exit 0
}

Write-WatchLog "灵魂未在运行且无 no_autostart 标记 → 拉起（soul.ps1 start -Body）"
& $SoulScript start -Body *>&1 | ForEach-Object { Write-WatchLog "  $_" }
