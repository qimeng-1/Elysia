# 注册 Elysia-Panel 计划任务：登录即启动，并**每 2 分钟**重试一次（幂等自愈）。
#
# 为什么需要"每 2 分钟重试"（2026-10-17 实测缺陷）：
#   原触发器只有 AtLogOn。任务在 21:08:30 失败退出（LastTaskResult=4294967295=-1）后
#   State 回到 Ready 且**没有下次运行时间**（AtLogOn 只在登录时触发）。
#   面板进程当时还活着，但那是手工起的孤儿 —— 任务不再监督它。
#   ⇒ 孤儿一死，面板就再也不会自己回来，用户第二天又打不开。
#
# 修法：给触发器加每 2 分钟重复。面板自身已有**端口级单例护栏**
# （端口被占就干净退出），所以"无条件每 2 分钟尝试启动"是安全的：
# 活着时它自己退出，死了时它被拉起。**用幂等启动代替复杂的存活检测。**
#
# 用法：powershell -ExecutionPolicy Bypass -File scripts\register_panel_task.ps1
#       powershell -ExecutionPolicy Bypass -File scripts\register_panel_task.ps1 -Unregister
# ASCII-only on purpose (cmd/PowerShell ANSI 读取的坑)。

param([switch]$Unregister)

$ErrorActionPreference = "Stop"
$Root   = Split-Path $PSScriptRoot -Parent
$Py     = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Script = Join-Path $Root "scripts\elysia_panel.py"
$Task   = "Elysia-Panel"

if (-not (Test-Path $Py)) { Write-Error "missing pythonw: $Py"; exit 1 }
if (-not (Test-Path $Script)) { Write-Error "missing panel: $Script"; exit 1 }

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[ok] unregistered $Task"
    exit 0
}

$act = New-ScheduledTaskAction -Execute $Py `
    -Argument ('"' + $Script + '" --no-open') -WorkingDirectory $Root

# 登录触发 + 每 2 分钟重复（无限期）
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trg.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 2)).Repetition

$set = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

$prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $Task -Action $act -Trigger $trg -Settings $set `
    -Principal $prn -Force `
    -Description "Elysia control panel: start at logon, retry every 2 min (idempotent via port guard)" | Out-Null

$t = Get-ScheduledTask -TaskName $Task
Write-Host "[ok] registered $Task"
Write-Host "     Execute : $($t.Actions | ForEach-Object { $_.Execute })"
Write-Host "     Repeat  : $($t.Triggers | ForEach-Object { $_.Repetition.Interval })"
Write-Host "     Setting : MultipleInstances=$($t.Settings.MultipleInstances) StartWhenAvailable=$($t.Settings.StartWhenAvailable)"
