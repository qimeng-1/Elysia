# Elysia 数据备份脚本
#
# 用法（在项目根目录执行）:
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1                 # 立即备份一次
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Register       # 注册"每日备份"计划任务
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Unregister     # 取消计划任务
#
# 行为（三步，顺序有讲究）:
#   1) robocopy 复制整个项目（排除 .venv/.git/缓存/日志）
#   2) 再用 SQLite 的 .backup 把 data 下两个**运行中的库**覆盖成**事务一致快照**
#      —— 这一步是必须的：她跑着时 db + -wal + -shm 的字节拷贝**不是一致快照**
#      （旧版只做 robocopy，等于把可能写了一半的页备进去；见 ADR-002 更正节）
#   3) 只保留最近 $Keep 份
#
# 源目录默认 = 本脚本所在目录的上一级（可移植，不硬编码盘符）
param(
    [string]$Source = "",
    [string]$BackupRoot = "D:\ElysiaBackup\elysia",
    [int]$Keep = 30,
    [string]$Time = "03:00",
    [switch]$Register,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Source)) { $Source = Split-Path $PSScriptRoot -Parent }

$TaskName = "Elysia-DailyBackup"
$ScriptPath = Join-Path $PSScriptRoot "backup.ps1"

function Register-DailyTask {
    $action = "powershell -ExecutionPolicy Bypass -File `"$ScriptPath`""
    & schtasks.exe /Create /TN $TaskName /TR $action /SC DAILY /ST $Time /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "注册计划任务失败（exit=$LASTEXITCODE）——可能需要在**管理员** PowerShell 中执行"
        exit 1
    }
    Write-Host "[ok] 已注册每日备份任务 '$TaskName'（每天 $Time）。取消：-Unregister"
}

function Unregister-DailyTask {
    & schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "[ok] 已取消计划任务 '$TaskName'（本来不存在也算成功）"
}

if ($Register) { Register-DailyTask; exit 0 }
if ($Unregister) { Unregister-DailyTask; exit 0 }

if (-not (Test-Path $Source)) { Write-Error "源目录不存在: $Source"; exit 1 }

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$dest = Join-Path $BackupRoot $ts
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# ── 1) 文件级复制 ─────────────────────────────────────────────
robocopy $Source $dest /E `
    /XD ".venv" ".git" "__pycache__" ".mypy_cache" ".pytest_cache" ".ruff_cache" "logs" "cache" "tmp" `
    /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy 失败 (exit=$LASTEXITCODE)"; exit 1 }

# ── 2) 运行中的库 → 事务一致快照 ──────────────────────────────
# 先删掉 robocopy 带过来的 WAL 伴生文件：否则将来打开这份备份时，
# 会把**陈旧的 WAL** 重放到刚做好的快照上，反而搞坏它。
Get-ChildItem (Join-Path $dest "data") -Filter "*.db-wal" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $dest "data") -Filter "*.db-shm" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

$venvPy = Join-Path $Source ".venv\Scripts\python.exe"
$snapshot = Join-Path $PSScriptRoot "snapshot_dbs.py"
if (Test-Path $venvPy) {
    & $venvPy $snapshot `
        (Join-Path $Source "data\state.db")     (Join-Path $dest "data\state.db") `
        (Join-Path $Source "data\heartbeat.db") (Join-Path $dest "data\heartbeat.db")
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "库快照有失败（见上方 [fail]）——本次备份里的库可能不一致，请检查后再依赖它"
    }
} else {
    Write-Warning "未找到 $venvPy，跳过库一致性快照（备份里的库只是运行中的字节拷贝，不保证一致）"
}

# ── 3) 清理旧备份：仅保留最近 $Keep 份 ────────────────────────
$all = Get-ChildItem $BackupRoot -Directory | Sort-Object Name -Descending
$stale = $all | Select-Object -Skip $Keep
foreach ($item in $stale) { Remove-Item $item.FullName -Recurse -Force }

Write-Host "[ok] 备份完成: $dest（现有 $($all.Count) 份，保留 $Keep 份）"
