# Elysia 数据备份脚本
# 用法: powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
# 行为: 备份 elysia/ 到 D:\ElysiaBackup\elysia\<时间戳>，仅保留最近 30 份
param(
    [string]$Source = "A:\WorkPlace\Elysia\elysia",
    [string]$BackupRoot = "D:\ElysiaBackup\elysia",
    [int]$Keep = 30
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Source)) { Write-Error "源目录不存在: $Source"; exit 1 }

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$dest = Join-Path $BackupRoot $ts
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# robocopy: /E 含子目录 /XD 排除 .venv/.git/缓存/日志（不进备份）
robocopy $Source $dest /E `
    /XD ".venv" ".git" "__pycache__" "data\logs" "data\cache" "data\tmp" `
    /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy 失败 (exit=$LASTEXITCODE)"; exit 1 }

# 清理旧备份：仅保留最近 $Keep 份
$all = Get-ChildItem $BackupRoot -Directory | Sort-Object Name -Descending
$stale = $all | Select-Object -Skip $Keep
foreach ($item in $stale) { Remove-Item $item.FullName -Recurse -Force }

Write-Host "[ok] 备份完成: $dest（现有 $($all.Count) 份，保留 $Keep 份）"
