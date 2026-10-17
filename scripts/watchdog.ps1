# Elysia 进程级看门狗 —— 补上"崩了不会自愈"的**剩下那一半**
#
# 背景（19.4 缺点 1）：协程级自愈已就位（core/supervisor.py：心跳协程异常退出会被拉起），
# 但**整个进程**被杀（OOM / 手动 taskkill / 蓝屏重启后）仍需要有人拉。本脚本负责那一半。
#
# P0-R3（D-3）修复的三件事（审计实测）：
#  ① 判活不再只看 PID：加**心跳新鲜度**。旧判据 `Test-SoulAlive` 只看 PID 是否在，
#     于是"进程在、心跳停"（僵死/死锁/库卡死）她会被永远卡在那里无人知晓。
#     现在：心跳停超过 -StaleSeconds（默认 60s）⇒ 判定僵死 ⇒ kill 后重启。
#  ② **每次运行都写一行**（是否在跑 / 心跳年龄 / 判定 / 动作）。旧版只在"有动作"时写，
#     结果 2026-10-17 00:52 → 20:41 她停机 16.82 小时期间 watchdog.log **一个字都没有**，
#     没人能知道"看门狗自己也没跑"。
#  ③ 自身失败也可见：异常写 `run-error` 行（带类型/消息/行号）。
#     "最后一次成功运行时间" = watchdog.log 里最后一条 `run-ok` 行的时间戳（面板读尾行即可）。
#
# 保守原则（宁可少杀，不可错杀）：
#  - 心跳**读不到**时**绝不**判僵死（进程在就当她在），但在日志里明确写"判不了 + 原因"；
#  - 进程刚起来（uptime < -StartupGraceSeconds，默认 120s）不判僵死（给启动留时间）；
#  - 启动命令发出后 2 秒复核是否真有活进程，没起来就如实记 `started-but-not-alive`，不假装成功。
#
# 用法（在项目根目录执行）:
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1              # 只检查一次
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Register    # 每 5 分钟自动检查
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Unregister  # 取消
#
# 尊重"故意的停机"：`data\run\no_autostart` 存在时**不拉起**。
#   soul.ps1 stop  → 创建该标记（你不想让她跑）
#   soul.ps1 start → 清除该标记（你又想让她跑了）
#
# ⚠️ 验收注入点（生产不要传；pytest 用它把根目录指到临时目录，避免碰真实的她）：
#   -ProjectRoot / -PythonExe / -SoulScript / -StaleSeconds / -StartupGraceSeconds
param(
    [int]$IntervalMinutes = 5,
    [switch]$Register,
    [switch]$Unregister,
    [string]$ProjectRoot = "",
    [string]$PythonExe = "",
    [string]$SoulScript = "",
    [double]$StaleSeconds = 60,
    [double]$StartupGraceSeconds = 120
)

$ErrorActionPreference = "Stop"

# 项目根 = 本脚本所在目录的上一级（生产）；验收时可注入到临时目录
$ExplicitRoot = -not [string]::IsNullOrWhiteSpace($ProjectRoot)
if (-not $ExplicitRoot) { $ProjectRoot = Split-Path $PSScriptRoot -Parent }

$RunDir      = Join-Path $ProjectRoot "data\run"
$LogDir      = Join-Path $ProjectRoot "data\logs"
$PidFile     = Join-Path $RunDir "soul.pid"
$NoAutoStart = Join-Path $RunDir "no_autostart"
$HeartbeatDb = Join-Path $ProjectRoot "data\heartbeat.db"
$WatchLog    = Join-Path $LogDir "watchdog.log"
$TaskName    = "Elysia-Watchdog"
if ([string]::IsNullOrWhiteSpace($SoulScript)) { $SoulScript = Join-Path $ProjectRoot "scripts\soul.ps1" }
if ([string]::IsNullOrWhiteSpace($PythonExe))  { $PythonExe  = Join-Path $ProjectRoot ".venv\Scripts\python.exe" }

# 读"最后一条 soul 心跳"的内联探针：只读打开（mode=ro，不改 -shm），未知一律退出码非 0。
# 为什么要 python 而不是 sqlite3.exe：sqlite3 CLI 不一定装；.venv 里的 python 一定在。
#
# ⚠️ 探针里**只用单引号、绝不写 stderr**（本工程实测两个 PowerShell 5.1 的坑）：
#   1) PS 5.1 把内嵌双引号传给原生程序时会丢掉 ⇒ `sqlite3.connect("file:...")` 变成语法错误；
#   2) `$ErrorActionPreference = "Stop"` 下原生命令写 stderr 会抛 RemoteException
#      ⇒ 探针的报错必须走 **stdout**，否则"读不到心跳"会变成"看门狗自己崩了"。
$BeatProbe = @'
import sqlite3, sys
try:
    con = sqlite3.connect('file:%s?mode=ro' % sys.argv[1], uri=True, timeout=3.0)
    row = con.execute(
        'SELECT ts FROM heartbeats WHERE beat_type=? ORDER BY id DESC LIMIT 1', ('soul',)
    ).fetchone()
    con.close()
except Exception as exc:
    print('%s: %s' % (type(exc).__name__, exc))
    sys.exit(2)
if row is None:
    sys.exit(3)
print('%.3f' % float(row[0]))
'@

function Write-WatchLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $WatchLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Write-Summary([string]$Verdict, [string]$Action, [string]$BeatText, [string]$AliveText) {
    # 每次运行**都必须**留下这一行（run-ok）——它同时是"最后一次成功运行时间"的来源
    Write-WatchLog "run-ok | alive=$AliveText | $BeatText | verdict=$Verdict | action=$Action"
}

function Get-PidFromFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $content = (Get-Content $Path -Raw -ErrorAction SilentlyContinue)
    if ($null -eq $content) { return $null }
    $content = $content.Trim()
    if ($content -match "^\d+$") { return [int]$content }
    return $null
}

function Test-SoulProcessAlive {
    param([int]$ProcessId)
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-ProcessAgeSeconds {
    param([int]$ProcessId)
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return $null }
    try {
        return ((Get-Date) - $proc.StartTime).TotalSeconds
    } catch {
        return $null   # 拿不到启动时间 ⇒ "未知"，不拿它当判据
    }
}

function Get-SoulBeat {
    # 返回 @{ Age = 秒数或 $null; Error = 原因或 $null }
    param([string]$DbPath, [string]$Python)
    if (-not (Test-Path $DbPath)) {
        return [pscustomobject]@{ Age = $null; Error = "heartbeat.db 不存在" }
    }
    if (-not (Test-Path $Python)) {
        return [pscustomobject]@{ Age = $null; Error = "python 解释器不存在：$Python" }
    }
    # 本地把 EAP 降为 Continue：原生命令的 stderr 在 Stop 下会变成终止性错误。
    # 探针的报错走 stdout（见 $BeatProbe 注释），stderr 直接丢弃、不参与判定。
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Python -c $BeatProbe $DbPath 2>$null
        $exit = $LASTEXITCODE
    } catch {
        $ErrorActionPreference = $oldEap
        return [pscustomobject]@{ Age = $null; Error = "读心跳失败（$($_.Exception.Message)）" }
    } finally {
        $ErrorActionPreference = $oldEap
    }
    $text = ([string]($out -join "")).Trim()
    if ($exit -eq 3) {
        return [pscustomobject]@{ Age = $null; Error = "库里还没有 soul 心跳" }
    }
    if ($exit -ne 0) {
        if ([string]::IsNullOrWhiteSpace($text)) { $text = "exit=$exit" }
        return [pscustomobject]@{ Age = $null; Error = "读心跳失败（$text）" }
    }
    $ts = 0.0
    if (-not [double]::TryParse($text, [ref]$ts)) {
        return [pscustomobject]@{ Age = $null; Error = "心跳 ts 解析不出：$text" }
    }
    $now = [double]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000.0
    return [pscustomobject]@{ Age = ($now - $ts); Error = $null }
}

function Invoke-SoulStart {
    # 拉起她（走既有原语 scripts\soul.ps1）；返回如实的结果字符串，不假装成功。
    if (-not (Test-Path $SoulScript)) {
        Write-WatchLog "  [fail] 找不到 soul.ps1：$SoulScript —— 没法拉，且不会假装拉起"
        return "failed(no-script)"
    }
    try {
        & $SoulScript start -Body *>&1 | ForEach-Object { Write-WatchLog "  $_" }
    } catch {
        Write-WatchLog "  [fail] soul.ps1 调用抛错：$($_.Exception.Message)"
        return "failed(exception)"
    }
    Start-Sleep -Seconds 2
    $newPid = Get-PidFromFile $PidFile
    if ($null -ne $newPid -and (Test-SoulProcessAlive $newPid)) {
        return "ok(pid=$newPid)"
    }
    Write-WatchLog "  [warn] 启动命令已发出，但 2 秒后仍没有活进程（pid 文件=$newPid）"
    return "started-but-not-alive"
}

function Register-WatchdogTask {
    $action = "powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($ExplicitRoot) { $action = "$action -ProjectRoot `"$ProjectRoot`"" }
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

# ── 单次检查（每次运行都必须以 run-ok 或 run-error 收尾，绝不静默退出）──────
try {
    $beat = Get-SoulBeat -DbPath $HeartbeatDb -Python $PythonExe
    if ($null -ne $beat.Age) {
        $beatText = ("beat_age={0:0.0}s" -f [double]$beat.Age)
    } else {
        $beatText = "beat_age=unknown($($beat.Error))"
    }

    $pidFromFile = Get-PidFromFile $PidFile
    $alive = $false
    $procAge = $null
    if ($null -ne $pidFromFile) {
        $alive = Test-SoulProcessAlive $pidFromFile
        if ($alive) { $procAge = Get-ProcessAgeSeconds $pidFromFile }
    }
    if ($alive) { $aliveText = "True(pid=$pidFromFile)" } else { $aliveText = "False" }

    if ($alive) {
        # ① 启动宽限：她刚起来、还没来得及写心跳时**不许**判僵死（错杀比不杀更伤）
        if ($null -ne $procAge -and $procAge -lt $StartupGraceSeconds) {
            Write-Summary "starting(grace)" "none" $beatText $aliveText
            exit 0
        }
        # ② 心跳新鲜 ⇒ 健康
        if ($null -ne $beat.Age -and [double]$beat.Age -le $StaleSeconds) {
            Write-Summary "healthy" "none" $beatText $aliveText
            exit 0
        }
        # ③ 心跳读不到 ⇒ 判不了。**不许猜成"她死了"**（进程在就当她在），但要写清原因
        if ($null -eq $beat.Age) {
            Write-Summary "unknown_beat(不杀)" "none" $beatText $aliveText
            exit 0
        }
        # ④ 进程在、心跳停 ⇒ 僵死（D-3 的核心场景）：kill 后重启
        Write-WatchLog ("判定僵死：进程 {0} 在，但心跳已停 {1}（阈值 {2}s）→ kill 后重启" -f $pidFromFile, $beatText, $StaleSeconds)
        Stop-Process -Id $pidFromFile -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        $action = Invoke-SoulStart
        Write-Summary "stale" "killed&restart($action)" $beatText $aliveText
        exit 0
    }

    # 进程不在
    if (Test-Path $NoAutoStart) {
        Write-Summary "stopped_by_user" "none" $beatText $aliveText
        exit 0
    }
    if ($null -ne $pidFromFile) {
        Write-WatchLog "清理陈旧 PID 文件：$pidFromFile 已不存在"
    }
    $action = Invoke-SoulStart
    Write-Summary "not_running" "restart($action)" $beatText $aliveText
    exit 0
} catch {
    $kind = "unknown"
    try { $kind = $_.Exception.GetType().Name } catch { }
    $line = ""
    try { $line = $_.InvocationInfo.ScriptLineNumber } catch { }
    Write-WatchLog "run-error | ${kind}: $($_.Exception.Message) | 行号=$line"
    exit 1
}
