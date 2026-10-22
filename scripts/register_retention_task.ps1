# Elysia 遗忘维护（P0-R12）—— 每周日凌晨自动跑一次三层降采样
#
# 她**必须已停止**才会真正动手：`scripts/retention.py` 自己查 data/run 下的
# soul.pid **和 body.pid**，任一进程在跑就拒绝执行（两个进程都写 heartbeat.db，
# 一边裁一边写会撞写锁）。因此本任务只是"定时提醒 + 执行"，不会去停她——停机是人的决定。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\register_retention_task.ps1 -Register
#   powershell -ExecutionPolicy Bypass -File scripts\register_retention_task.ps1 -Unregister
#
# 口径（见 src/elysia/core/retention.py）：
#   热(0-3d) 原样 ｜ 温(3-30d) 每分钟一行 ｜ 冷(30-365d) 每小时一行 ｜ 年(>365d) 每天一行
#   她认定重要的时刻（protected / importance>=0.8）前后 ±6h 永不降采样；
#   裁剪前先归档到 ..\_archive\heartbeats\heartbeat-YYYYMM.db.gz；
#   memories / memory_index / thought_log / expression_log 一行不动。
#
# 2026-10-22（T-027）：注册后**回读计划任务**核对 Command/Arguments。
# 原因：`schtasks /TR` 的引号在 PowerShell 转发时可能被吃掉，那种任务会在凌晨静默失败；
# 只看到 schtasks 返回 0 不算数，必须读回任务本体（Execute / Arguments）确认没被改写。
param(
    [switch]$Register,
    [switch]$Unregister,
    [string]$RunAt = "03:30",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path $PSScriptRoot -Parent
}
$PythonExe  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Retention  = Join-Path $ProjectRoot "scripts\retention.py"
$TaskName   = "Elysia-Retention"

function Assert-RetentionTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Error "注册后找不到计划任务 '$TaskName'"
        exit 1
    }
    $action = @($task.Actions)[0]
    $command = [string]$action.Execute
    $arguments = [string]$action.Arguments
    if ($command -ne $PythonExe) {
        Write-Error "计划任务命令被改写：'$command'（期望 '$PythonExe'）"
        exit 1
    }
    if (($arguments -notlike "*retention.py*") -or ($arguments -notlike "*--mode tier*") -or ($arguments -notlike "*--apply*")) {
        Write-Error "计划任务参数被改写：'$arguments'（期望含 retention.py --mode tier --apply）"
        exit 1
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "[ok] 已注册 '$TaskName'（每周日 $RunAt；她必须已停止才会真正动手）"
    Write-Host "     状态 = $($task.State)；下次运行 = $($info.NextRunTime)"
    Write-Host "     动作 = $command $arguments"
    Write-Host "     取消：-Unregister；立即手动跑（先停她）：python scripts\retention.py --apply"
}

function Register-RetentionTask {
    if (-not (Test-Path $PythonExe)) {
        Write-Error "找不到 python 解释器：$PythonExe"
        exit 1
    }
    if (-not (Test-Path $Retention)) {
        Write-Error "找不到 retention.py：$Retention"
        exit 1
    }
    $action = "`"$PythonExe`" `"$Retention`" --mode tier --apply"
    & schtasks.exe /Create /TN $TaskName /TR $action /SC WEEKLY /D SUN /ST $RunAt /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "注册遗忘维护任务失败（exit=$LASTEXITCODE）——可能需要在**管理员** PowerShell 中执行"
        exit 1
    }
    Assert-RetentionTask
}

function Unregister-RetentionTask {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[ok] 已取消 '$TaskName'（本来不存在也算成功）"
}

if ($Register) { Register-RetentionTask; exit 0 }
if ($Unregister) { Unregister-RetentionTask; exit 0 }

Write-Host "用法：-Register 注册每周维护 / -Unregister 取消 / 手动跑（先停她）：python scripts\retention.py --apply"
exit 0
