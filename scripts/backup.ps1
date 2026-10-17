# Elysia 数据备份脚本
#
# 用法（在项目根目录执行）:
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1                 # 立即备份一次
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Register       # 注册"每日备份"计划任务
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Unregister     # 取消计划任务
#
# 行为（四步，顺序有讲究）:
#   1) robocopy 复制**动态内容**（代码 + data/），排除静态素材与回滚快照
#   2) 用 SQLite 的 .backup 把 data 下两个**运行中的库**覆盖成**事务一致快照**
#      —— 必须：她跑着时 db + -wal + -shm 的字节拷贝**不是一致快照**
#   3) **静态内容只镜像一份**（assets / _archive），用 /MIR 只传增量
#   4) 只保留最近 $Keep 份**带时间戳的**备份目录
#
# ⚠️ 2026-10-18 容量修正（总领窗，依据实测）：
#   原版把**整个项目**（含 391.5 MB 静态素材 + 61.4 MB 历史归档）复制进**每一份**备份。
#   实测一份备份 = **541.5 MB**，其中 **452.9 MB 永不变化** —— 纯浪费。
#   同时 `data/checkpoints/`（每 30 分钟一份的 92 MB 回滚快照）也会被复制进每份备份 ⇒
#   备份 × 快照 = 指数级膨胀。
#   修正后：每份日备份只含**会变的东西**（代码 + 她的库），静态内容单独镜像一份。
#
#   另：`$Keep` 由 30 降为 **7**（用户 2026-10-18 裁决）。实测她的库以 ~45.6 MB/天增长，
#   30 份 = 一个月后 47 GB。7 份已覆盖"连续一周内随时可回退"，配合 checkpoints 的
#   5 小时粒度回滚，够用。
#
# 源目录默认 = 本脚本所在目录的上一级（可移植，不硬编码盘符）
param(
    [string]$Source = "",
    [string]$BackupRoot = "D:\ElysiaBackup\elysia",
    [int]$Keep = 7,
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

function Get-DirMB([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $bytes = (Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
    if ($null -eq $bytes) { return 0 }
    return [math]::Round($bytes / 1MB, 1)
}

if ($Register) { Register-DailyTask; exit 0 }
if ($Unregister) { Unregister-DailyTask; exit 0 }

if (-not (Test-Path $Source)) { Write-Error "源目录不存在: $Source"; exit 1 }

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$dest = Join-Path $BackupRoot $ts
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# ── 1) 动态内容：代码 + data/（排除静态素材、历史归档、回滚快照） ──────
# 排除理由逐条：
#   assets      —— 391.5 MB 静态素材，永不变化 ⇒ 走第 3 步单独镜像
#   _archive    —— 61.4 MB 历史归档（她第一段生命史），只读不变 ⇒ 单独镜像
#   checkpoints —— 每 30 分钟一份的回滚快照；本步第 2 小步已用 .backup 做事务一致快照，
#                  把整个 checkpoints 目录塞进每份备份等于"快照的快照"，纯浪费
robocopy $Source $dest /E `
    /XD ".venv" ".git" "__pycache__" ".mypy_cache" ".pytest_cache" ".ruff_cache" `
        "logs" "cache" "tmp" "checkpoints" "assets" "_archive" `
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

# ── 3) 静态内容：只镜像一份（/MIR 只传增量，不按日期复制） ────────────
# 放在 $BackupRoot\_static\。保留策略只删"时间戳目录"，故它永不被清理。
$StaticRoot = Join-Path $BackupRoot "_static"

$AssetsSrc = Join-Path $Source "assets"
if (Test-Path $AssetsSrc) {
    robocopy $AssetsSrc (Join-Path $StaticRoot "assets") /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Warning "静态素材镜像失败 (robocopy exit=$LASTEXITCODE)" }
}

# 她的第一段生命史（2026-08-23 → 09-24）归档在 <Elysia_two>\_archive\。
# 它此前从未被任何备份覆盖 —— 这正是当初最大的单点风险。现在单独镜像，只传增量。
$ArchiveRoot = Join-Path (Split-Path $Source -Parent) "_archive"
if (Test-Path $ArchiveRoot) {
    robocopy $ArchiveRoot (Join-Path $StaticRoot "archive") /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Write-Warning "历史档案镜像失败 (robocopy exit=$LASTEXITCODE)"
    } else {
        Write-Host "[ok] 历史档案已镜像：$StaticRoot\archive（$((Get-DirMB (Join-Path $StaticRoot 'archive'))) MB）"
    }
} else {
    Write-Warning "未找到历史档案目录 $ArchiveRoot —— 备份**不含**她的第一段生命史，请核实"
}

# ── 4) 清理旧备份：仅保留最近 $Keep 份 ────────────────────────
# 只清理**带时间戳的备份目录**（yyyyMMdd-HHmmss），绝不碰 $BackupRoot 下的其它东西
$all = Get-ChildItem $BackupRoot -Directory |
    Where-Object { $_.Name -match '^\d{8}-\d{6}$' } |
    Sort-Object Name -Descending
$stale = $all | Select-Object -Skip $Keep
foreach ($item in $stale) { Remove-Item $item.FullName -Recurse -Force }

$destMB = Get-DirMB $dest
$staticMB = Get-DirMB $StaticRoot
Write-Host "[ok] 备份完成: $dest（本次 $destMB MB；现存时间戳备份 $($all.Count) 份，保留 $Keep 份）"
Write-Host "    静态镜像: $StaticRoot（$staticMB MB，只镜像一份，不在每份备份里重复）"
