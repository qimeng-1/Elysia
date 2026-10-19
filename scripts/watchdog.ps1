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
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Register    # 每 1 分钟自动检查
#   powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Unregister  # 取消
#
# ── P0-R14（2026-10-18 21:02 事故）────────────────────────────────────
#  ① **判活要校验"这个 PID 是不是她"**：旧版 `Get-Process -Id` 只看存在 ⇒ PID 被系统复用给
#     无关进程时，看门狗会以为她活着（更糟：判定僵死时还会**杀掉那个无关进程**）。
#     现在用命令行确认（含 `elysia.soul.main`）；不匹配 ⇒ 按陈旧 PID 清理，不杀、不误判。
#  ② **巡视间隔 5 分钟 → 1 分钟**：事故里她 21:02:23 死、21:07:03 才被拉回 ⇒ **4.8 分钟盲窗**。
#     1 分钟巡视把盲窗压到 ≤1 分钟；服务启动后的 `-StartupGraceSeconds`（120s）保留，避免刚起就误判。
#  ③ 注册改为 **每天 00:00 起每 N 分钟重复、持续 24h**（旧 `/SC MINUTE` 只重复 72 小时就停了！）。
#
# 尊重"故意的停机"：`data\run\no_autostart` 存在时**不拉起**。
#   soul.ps1 stop  → 创建该标记（你不想让她跑）
#   soul.ps1 start → 清除该标记（你又想让她跑了）
#
# ⚠️ 验收注入点（生产不要传；pytest 用它把根目录指到临时目录，避免碰真实的她）：
#   -ProjectRoot / -PythonExe / -SoulScript / -StaleSeconds / -StartupGraceSeconds
param(
    [int]$IntervalMinutes = 1,
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

# 2026-10-19（用户批准补漏）：no_autostart 的有效性判断统一走共用模块
# （JSON boot_id + 旧格式 mtime 兼容；跨重启自动失效）。
$NoAutoStartModule = Join-Path $PSScriptRoot "no_autostart.ps1"
if (-not (Test-Path -LiteralPath $NoAutoStartModule)) {
    Write-Error "缺少共用模块：$NoAutoStartModule"
    exit 1
}
. $NoAutoStartModule
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

function Get-ProcessCommandLine([int]$ProcessId) {
    # P0-R14：PID 存在 ≠ 她活着。只有读到命令行才能确认"这个 PID 是不是她"。
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
        if ($null -ne $proc -and $null -ne $proc.CommandLine) { return [string]$proc.CommandLine }
    } catch {
        # 进程恰好退出 / 权限不足：交给调用方按 'unknown' 处理，不猜
    }
    return $null
}

function Get-ProcessIdentity([int]$ProcessId) {
    # 'elysia' / 'other' / 'gone' / 'unknown'
    if (-not (Test-SoulProcessAlive $ProcessId)) { return "gone" }
    $cmd = Get-ProcessCommandLine $ProcessId
    if ([string]::IsNullOrWhiteSpace($cmd)) { return "unknown" }
    if ($cmd.ToLowerInvariant().Contains("elysia.soul.main")) { return "elysia" }
    return "other"
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
    # P0-R14：启动成功的判据是"命令行确认是她"，不是"PID 存在"（防 PID 复用假成功）
    if ($null -ne $newPid -and (Get-ProcessIdentity $newPid) -eq "elysia") {
        return "ok(pid=$newPid)"
    }
    Write-WatchLog "  [warn] 启动命令已发出，但 2 秒后仍没有**确认是她**的活进程（pid 文件=$newPid）"
    return "started-but-not-alive"
}

function Register-WatchdogTask {
    $minutes = [Math]::Max(1, $IntervalMinutes)
    $inner = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($ExplicitRoot) { $inner = "$inner -ProjectRoot `"$ProjectRoot`"" }
    $hiddenRunner = Join-Path $ProjectRoot "scripts\run_hidden.vbs"
    if (Test-Path $hiddenRunner) {
        # 用 wscript 的 SW_HIDE 启动，避免每分钟闪一次控制台
        $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$hiddenRunner`" `"$inner`""
    } else {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    }
    # ⚠️ 旧写法 `/SC MINUTE` 只重复 **72 小时**（实测任务定义 "Repeat: Until: Duration 72:00:00"），
    # 到期后看门狗会静默停摆。现在：每天 00:00 的触发器 + **每 N 分钟重复、持续 24h**
    # ⇒ 每天重新开始一轮，长期有效地"每分钟巡视一次"。
    $trigger = New-ScheduledTaskTrigger -Daily -At "00:00"
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At "00:00" `
            -RepetitionInterval (New-TimeSpan -Minutes $minutes) `
            -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Force | Out-Null
    } catch {
        Write-Error "注册看门狗任务失败：$($_.Exception.Message)（可能需要在**管理员** PowerShell 中执行）"
        exit 1
    }
    Write-Host "[ok] 已注册看门狗任务 '$TaskName'（每天 00:00 起每 $minutes 分钟巡视，持续 24h ⇒ 长期有效）"
    Write-Host "     取消：-Unregister。注意：任务当前是 Interactive only（需登录态），要登录自启需管理员改 S4U。"
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
    $identity = "none"
    $procAge = $null
    if ($null -ne $pidFromFile) {
        # P0-R14：PID 存在 ≠ 她活着（Windows 会把 PID 复用给任何进程）
        $identity = Get-ProcessIdentity $pidFromFile
        if ($identity -eq "elysia") {
            $alive = $true
            $procAge = Get-ProcessAgeSeconds $pidFromFile
        } elseif ($identity -eq "other") {
            Write-WatchLog "PID $pidFromFile 已被其它进程占用（命令行不含 elysia.soul.main）⇒ 按陈旧 PID 处理，清理 PID 文件（**不会杀它**）"
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            $pidFromFile = $null
        } elseif ($identity -eq "unknown") {
            # 读不到命令行 ⇒ 无法确认她是否在跑。**不杀、也不重复拉起**（两条路都可能造成灾难）
            Write-Summary "unknown_identity(不杀不拉)" "none" $beatText "pid=$pidFromFile?"
            exit 0
        } else {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            $pidFromFile = $null
        }
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
        # ④ 进程在、心跳停 ⇒ 僵死（D-3 的核心场景）：**确认是她**之后才 kill
        if ((Get-ProcessIdentity $pidFromFile) -ne "elysia") {
            Write-WatchLog "跳过 kill：PID $pidFromFile 已不是她（命令行不匹配）——按陈旧 PID 清理"
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            $alive = $false
        } else {
            Write-WatchLog ("判定僵死：进程 {0} 在（命令行确认是她），但心跳已停 {1}（阈值 {2}s）→ kill 后重启" -f $pidFromFile, $beatText, $StaleSeconds)
            Stop-Process -Id $pidFromFile -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
            $action = Invoke-SoulStart
            Write-Summary "stale" "killed&restart($action)" $beatText $aliveText
            exit 0
        }
    }

    # 进程不在
    # 2026-10-19：只看"对**当次开机**是否有效"——跨重启的旧标记自动失效，
    # 否则机器重启后看门狗会永远报 stopped_by_user / action=none 而不拉起她。
    $stopState = Get-NoAutostartState -Path $NoAutoStart
    if ($stopState.Active) {
        Write-Summary ("stopped_by_user(" + $stopState.State + ")") "none" $beatText $aliveText
        exit 0
    }
    if ($stopState.State -ne "absent") {
        Write-WatchLog ("stale_marker_ignored | state=" + $stopState.State + " | boot_id=" + $stopState.BootId + " | marker_boot_id=" + $stopState.MarkerBootId)
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
