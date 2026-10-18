# Elysia Telegram 桥的计划任务 —— **本脚本默认只打印状态，不注册**。
# 注册动作由总领在真机验收后显式执行（-Register）。
#
# 为什么用 pythonw.exe：
#   面板那次踩过"Windows 防火墙只放行 python.exe ⇒ pythonw 的入站被拦"的坑，但那是**入站服务**；
#   本桥只做**出站** HTTPS（api.telegram.org）与 long polling，防火墙不拦出站 ⇒
#   用 pythonw.exe 既不会被拦，也没有黑窗口。**不要再加 --host 之类监听参数。**
#
# 为什么"登录触发 + 每 2 分钟重试"：
#   与面板同一个教训（2026-10-17）：AtLogOn 任务失败后 State 回 Ready 且没有下次运行时间，
#   孤儿一死就再也不回来。桥自身有 **PID 单实例护栏**（PID 存在但命令行不是 telegram_bridge
#   会被当陈旧 PID 覆盖），所以"每 2 分钟无条件尝试启动"是幂等的：活着它自己退出，死了被拉起。
#
# 用法（PowerShell 需 UTF-8 BOM，本文件已带）：
#   powershell -ExecutionPolicy Bypass -File scripts\register_telegram_task.ps1            # 只看状态
#   powershell -ExecutionPolicy Bypass -File scripts\register_telegram_task.ps1 -Register  # 真机验收后由总领执行
#   powershell -ExecutionPolicy Bypass -File scripts\register_telegram_task.ps1 -Unregister
#
# 前置：把 @BotFather 给的 token 放进 data\run\telegram_token.txt（UTF-8；带不带 BOM 都能读）。
param(
    [switch]$Register,
    [switch]$Unregister,
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path $PSScriptRoot -Parent
}
$Py     = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Script = Join-Path $ProjectRoot "scripts\telegram_bridge.py"
$Token  = Join-Path $ProjectRoot "data\run\telegram_token.txt"
$Chat   = Join-Path $ProjectRoot "data\run\telegram_chat.txt"
$Task   = "Elysia-Telegram"

function Show-TelegramStatus {
    $t = Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
    if ($null -eq $t) {
        Write-Host "[info] 计划任务 '$Task' 尚未注册（本脚本默认不注册）"
    } else {
        Write-Host "[ok] '$Task' 已注册，State=$($t.State)"
        $info = Get-ScheduledTaskInfo -TaskName $Task
        Write-Host "     LastRunTime=$($info.LastRunTime) LastTaskResult=$($info.LastTaskResult) NextRunTime=$($info.NextRunTime)"
    }
    if (Test-Path $Token) {
        Write-Host "[ok] token 文件存在：$Token（内容绝不打印）"
    } else {
        Write-Host "[warn] 还没有 token：$Token（先把 @BotFather 的 token 放进去）"
    }
    if (Test-Path $Chat) {
        $bound = (Get-Content -LiteralPath $Chat -TotalCount 1).Trim()
        Write-Host "[ok] 已绑定 chat_id=$bound"
    } else {
        Write-Host "[info] 尚未绑定 chat：用你的 Telegram 给机器人发第一条消息即自动绑定"
    }
    Write-Host "     前台调试：.venv\Scripts\python.exe scripts\telegram_bridge.py --once"
}

function Register-TelegramTask {
    if (-not (Test-Path $Py))     { Write-Error "找不到 pythonw：$Py"; exit 1 }
    if (-not (Test-Path $Script)) { Write-Error "找不到 telegram_bridge.py：$Script"; exit 1 }
    if (-not (Test-Path $Token))  { Write-Error "还没有 token 文件：$Token（先放 token 再注册）"; exit 1 }

    $act = New-ScheduledTaskAction -Execute $Py `
        -Argument ('"' + $Script + '"') -WorkingDirectory $ProjectRoot

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
        -Description "Elysia Telegram bridge: bidirectional chat via long polling (no inbound port)" | Out-Null

    Write-Host "[ok] 已注册 '$Task'（登录启动 + 每 2 分钟幂等重试；PID 护栏防双发）"
    Show-TelegramStatus
}

function Unregister-TelegramTask {
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[ok] 已取消 '$Task'（本来不存在也算成功）"
}

if ($Register)   { Register-TelegramTask; exit 0 }
if ($Unregister) { Unregister-TelegramTask; exit 0 }

Show-TelegramStatus
exit 0
