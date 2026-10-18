# Elysia 灵魂/身体进程控制脚本 —— PID 级精确控制（评估报告 §8-3）
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start          # 启动灵魂（已在运行则跳过）
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body   # 启动灵魂+身体（幂等，缺哪个补哪个）
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop          # 停止两者
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop-body     # 只停身体（灵魂继续活）
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status        # 查看状态
#
# ── P0-R14（2026-10-18 事故 21:02:23）：她被杀、面板点「开机」却"什么都没做" ──
#  ① **判活必须校验"这个 PID 是不是她"**（旧代码只看 PID 存在）：
#     PID 被系统复用给任何无关进程时，"灵魂已在运行，跳过启动" ⇒ 面板显示成功、她其实还是死的。
#     现在用 `Get-ProcessIdentity` 读命令行（含 `elysia.soul.main` / `elysia.body.main`）：
#       elysia  = 她本人 → 跳过
#       other   = PID 被别的进程占用 → **删除 PID 文件并继续启动**（并明确说清）
#       gone    = 进程已不在 → 清理 PID 文件并启动
#       unknown = 进程在但读不到命令行 → **拒绝重复启动**（宁可报错，也不制造双实例/分叉）
#  ② **stderr/stdout 不再被覆盖**：`Start-Process -RedirectStandardError` 每次启动会清空目标文件
#     ⇒ 上一次死亡的 stderr 证据被下一次启动抹掉。现在启动前把旧日志归档为
#     `<名>.<旧启动时间>.log`，再开新文件。
#  ③ **退场哨兵**：启动成功后拉起 `scripts/soul_sentinel.py`（兄弟进程，不是她的父进程），
#     等她退出时把**退出码/存活时长**写进 `data/logs/exit-<ts>.json`——
#     这是区分"自己崩了"与"被别人 TerminateProcess 杀了"的唯一低成本证据。
#
# ── P0-R8（D-8）：停止必须**体面** ────────────────────────────────────
#   写 `stop_requested` 标记 → 进程看到后走正常关闭序 → 本脚本轮询等它退出（默认 15s）
#   → 真的不响应才强杀兜底；同时写 `stopped_intentionally` 供下次启动区分"主动停"与"崩溃"。
#   为什么用标记而不是信号：Windows 上从 PowerShell 可靠发 SIGTERM 不现实。
#
# ── P0-R13：`RETIRED` 标记（旧机器退役，防分叉）─────────────────────────
param(
    [ValidateSet("start", "stop", "stop-body", "status")]
    [string]$Action = "status",
    [switch]$Body,
    # 验收注入点（生产不要传）：把 data/ 根指到临时目录，避免测试碰到真实的她
    [string]$ProjectRoot = "",
    # 优雅关闭的等待上限；超时才强杀兜底（生产默认 15s）
    [int]$StopTimeoutSeconds = 15,
    # 验收注入点（生产不要传）：只做检查与打印，**绝不真正启动**（迁移演练/验收用）
    [switch]$DryRun
)

$ExplicitRoot = -not [string]::IsNullOrWhiteSpace($ProjectRoot)
if (-not $ExplicitRoot) { $ProjectRoot = Split-Path $PSScriptRoot -Parent }

$RunDir   = Join-Path $ProjectRoot "data\run"
$LogDir   = Join-Path $ProjectRoot "data\logs"
$PidFile  = Join-Path $RunDir "soul.pid"
$BodyPid  = Join-Path $RunDir "body.pid"
$Stdout   = Join-Path $LogDir "soul.out.log"
$Stderr   = Join-Path $LogDir "soul.err.log"
$BodyOut  = Join-Path $LogDir "body.out.log"
$BodyErr  = Join-Path $LogDir "body.err.log"
# P0-R8：优雅关闭的两个标记（语义见文件头；body 用 body_ 前缀区分）
$StopRequest         = Join-Path $RunDir "stop_requested"
$StopIntentional     = Join-Path $RunDir "stopped_intentionally"
$BodyStopRequest     = Join-Path $RunDir "body_stop_requested"
$BodyStopIntentional = Join-Path $RunDir "body_stopped_intentionally"
# "故意停机"标记：watchdog.ps1 见到它就**不自动拉起**（尊重你的 stop）。
# start 会清掉它，stop 会创建它 —— 于是看门狗不会跟你抢方向盘。
$NoAutoStart = Join-Path $RunDir "no_autostart"
# P0-R13（迁移防分叉，红线 #2）：旧机器**退役**标记。存在即拒绝启动——
# 她已搬到别处，两头同时活 = 分叉 = 把同一段人生写成两份。
$RetiredMarker = Join-Path $RunDir "RETIRED"
# 她"本次会话"的标记（R2 写入；含 started_at，用于给旧 stderr 归档起好名字）
$SoulRunningMarker = Join-Path $RunDir "soul.running"
$BodyRunningMarker = Join-Path $RunDir "body.running"
$ErrorActionPreference = "Stop"

function Get-PidFromFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $content = (Get-Content $Path -Raw -ErrorAction SilentlyContinue)
    if ($null -eq $content) { return $null }
    $content = $content.Trim()
    if ($content -match "^\d+$") { return [int]$content }
    return $null
}

function Get-SoulPid {
    return Get-PidFromFile $PidFile
}

function Get-BodyPid {
    return Get-PidFromFile $BodyPid
}

function Test-ProcessAlive([int]$ProcessId) {
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-ProcessCommandLine([int]$ProcessId) {
    # Win32_Process.CommandLine：能读到才能确认"这个 PID 是不是她"。
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
        if ($null -ne $proc -and $null -ne $proc.CommandLine) {
            return [string]$proc.CommandLine
        }
    } catch {
        # 进程恰好退出 / 权限不足：交给调用方按 'unknown' 处理，不猜
    }
    return $null
}

function Get-ProcessIdentity([int]$ProcessId, [string]$Kind) {
    # 返回 'elysia' / 'other' / 'gone' / 'unknown'。
    # **这是 P0-R14 的核心**：PID 存在 ≠ 她活着。Windows 会把 PID 复用给任何进程。
    if (-not (Test-ProcessAlive $ProcessId)) { return "gone" }
    $cmd = Get-ProcessCommandLine $ProcessId
    if ([string]::IsNullOrWhiteSpace($cmd)) { return "unknown" }
    $marker = "elysia.$Kind.main"
    if ($cmd.ToLowerInvariant().Contains($marker)) { return "elysia" }
    return "other"
}

function Test-IsElysiaProcess([int]$ProcessId, [string]$Kind) {
    return (Get-ProcessIdentity $ProcessId $Kind) -eq "elysia"
}

function Describe-Identity([string]$State, [int]$ProcessId) {
    switch ($State) {
        "elysia" { return "运行中（命令行确认是她，PID $ProcessId）" }
        "other"  { return "PID $ProcessId 已被**其它进程**占用（陈旧 PID 文件，按未运行处理）" }
        "unknown" { return "PID $ProcessId 存活但读不到命令行（无法确认是不是她）" }
        default  { return "未运行" }
    }
}

function Get-OldStartStamp([string]$MarkerPath) {
    # 旧 stderr 的归档名用「上一次会话的启动时间」，比"现在"更能说明它属于哪一条命。
    try {
        if (Test-Path $MarkerPath) {
            $match = Select-String -Path $MarkerPath -Pattern "started_at=([\d.]+)" | Select-Object -First 1
            if ($null -ne $match) {
                $epoch = [double]$match.Matches[0].Groups[1].Value
                return [DateTimeOffset]::FromUnixTimeSeconds([long]$epoch).LocalDateTime.ToString("yyyyMMdd-HHmmss")
            }
        }
    } catch {
        # 解析失败就用"现在"，绝不因此阻断启动
    }
    return (Get-Date -Format "yyyyMMdd-HHmmss")
}

function Archive-Log([string]$LogPath, [string]$Stamp) {
    # P0-R14：`RedirectStandardError` 会清空目标文件 ⇒ 启动前先把旧内容搬走。
    # 空文件不必留（没有信息），非空一律归档并如实打印路径。
    if (-not (Test-Path $LogPath)) { return }
    $item = Get-Item $LogPath -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.Length -le 0) { return }
    $dir = Split-Path -Parent $LogPath
    $base = [System.IO.Path]::GetFileNameWithoutExtension($LogPath)
    $ext = [System.IO.Path]::GetExtension($LogPath)
    $target = Join-Path $dir ("{0}.{1}{2}" -f $base, $Stamp, $ext)
    Move-Item -Path $LogPath -Destination $target -Force
    Write-Host "[info] 旧日志已归档 -> $target（本次启动不会抹掉上一次死亡的证据）"
}

function Start-ExitSentinel([int]$ProcessId, [string]$Kind, [string]$Python) {
    # P0-R14：兄弟进程哨兵（**不是**她的父进程）——只等退出码，不影响她的进程模型。
    $sentinel = Join-Path $ProjectRoot "scripts\soul_sentinel.py"
    if (-not (Test-Path $sentinel)) {
        Write-Host "[warn] 找不到退场哨兵 $sentinel（不影响启动，但本次没有退出码证据）"
        return
    }
    $startedEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    try {
        Start-Process -FilePath $Python `
            -ArgumentList @("`"$sentinel`"", "--pid", "$ProcessId", "--out", "`"$LogDir`"", "--kind", $Kind, "--started-ts", "$startedEpoch") `
            -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    } catch {
        Write-Host "[warn] 退场哨兵未启动：$($_.Exception.Message)"
    }
}

function Get-PyExe {
    # 优先直接使用项目 .venv 解释器（确定性，避免 uv 解析回退到系统 Python 造成多环境冲突）
    $venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    # 兜底：uv 解析（venv 缺失时）
    $line = & uv run --no-sync python -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1
    $py = ($line -as [string]).Trim()
    if (-not $py -or -not (Test-Path $py)) { Write-Error "无法解析 python 解释器路径: $py"; exit 1 }
    return $py
}

function Stop-OneGracefully {
    # 优雅停止一个进程：**先校验身份** → 标记 → 等它自己退出（≤ TimeoutSeconds）→ 超时才强杀。
    # 返回 "not-running" / "graceful" / "forced" / "refused"（调用方据此如实汇报）。
    param(
        [string]$Label,
        [string]$Kind,
        [string]$PidFilePath,
        [string]$StopMarkerPath,
        [string]$IntentionalMarkerPath,
        [int]$TimeoutSeconds
    )
    $procId = Get-PidFromFile $PidFilePath
    if ($null -eq $procId) {
        Write-Host "[info] $Label 未在运行（无 PID 文件）"
        return "not-running"
    }
    $state = Get-ProcessIdentity $procId $Kind
    if ($state -eq "gone") {
        Remove-Item $PidFilePath -Force -ErrorAction SilentlyContinue
        Write-Host "[info] $Label 未在运行（PID $procId 已不存在，PID 文件已清理）"
        return "not-running"
    }
    if ($state -eq "other") {
        # 绝不杀陌生进程：PID 被复用是 Windows 的正常行为
        Remove-Item $PidFilePath -Force -ErrorAction SilentlyContinue
        Write-Host "[warn] $Label PID $procId 已被其它进程占用（不是她），按陈旧 PID 处理；**不会杀它**"
        return "not-running"
    }
    if ($state -eq "unknown") {
        Write-Host "[refused] $Label PID $procId 存活但读不到命令行，无法确认是不是她 ⇒ 拒绝停止（防误杀）"
        return "refused"
    }

    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    # 1) 先写"主动停"标记：即使随后超时强杀，"这次是人为停的"也必须留下证据
    Set-Content -Path $IntentionalMarkerPath -Encoding ASCII `
        -Value ("requested_at=" + $stamp + "`nby=soul.ps1 " + $Action + "`npid=" + $procId)
    # 2) 停止请求：进程里轮询这个标记（**不立即强杀**）
    Set-Content -Path $StopMarkerPath -Encoding ASCII -Value ("requested_at=" + $stamp)
    Write-Host "[...] $Label：已请求优雅关闭（等它自己退出，最多 ${TimeoutSeconds}s）"

    # 3) 等进程自己退出
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        Start-Sleep -Milliseconds 200
        if (-not (Test-ProcessAlive $procId)) {
            Remove-Item $StopMarkerPath -Force -ErrorAction SilentlyContinue
            Remove-Item $PidFilePath -Force -ErrorAction SilentlyContinue
            $elapsed = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            Write-Host "[ok] $Label 已优雅关闭（${elapsed}s；落库与档案封存已执行）"
            return "graceful"
        }
    }

    # 4) 超时兜底：强杀（主动停标记已写 ⇒ 下次启动不会误报"非正常退出"）
    Write-Host "[warn] $Label 在 ${TimeoutSeconds}s 内没有响应停止请求 ⇒ 强杀兜底（这是最后手段）"
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
    Remove-Item $StopMarkerPath -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFilePath -Force -ErrorAction SilentlyContinue
    return "forced"
}

switch ($Action) {
    "start" {
        # P0-R13（防分叉，红线 #2）：**退役机器拒绝启动**。
        if (Test-Path $RetiredMarker) {
            Write-Host "[refused] 这台机器已退役（data\run\RETIRED 存在）——她已搬到别处。"
            Write-Host "          为防止分叉，禁止在此启动。若确需恢复，请人工删除该标记，"
            Write-Host "          并先确认全世界只有这一处在跑她。"
            exit 2
        }

        # P0-R14：判活 = PID 存在 **且** 命令行确认是她
        $old = Get-SoulPid
        $soulState = "none"
        if ($null -ne $old) { $soulState = Get-ProcessIdentity $old "soul" }
        $oldBody = Get-BodyPid
        $bodyState = "none"
        if ($null -ne $oldBody) { $bodyState = Get-ProcessIdentity $oldBody "body" }

        if ($DryRun) {
            Write-Host ("[dry-run] 灵魂：" + (Describe-Identity $soulState $old))
            if ($Body) { Write-Host ("[dry-run] 身体：" + (Describe-Identity $bodyState $oldBody)) }
            Write-Host "[dry-run] 未真正启动（DryRun 只用于迁移演练/验收）"
            exit 0
        }

        if ($soulState -eq "other") {
            Write-Host "[warn] 灵魂 PID $old 已被其它进程占用，按陈旧 PID 处理（不会杀它），继续启动"
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            $soulState = "none"
        }
        if ($soulState -eq "gone") {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            $soulState = "none"
        }
        if ($soulState -eq "unknown") {
            Write-Host "[refused] 灵魂 PID $old 存活但读不到命令行，无法确认是不是她 ⇒ 拒绝重复启动（防分叉）。"
            Write-Host "          请人工确认后删除 data\run\soul.pid 再试。"
            exit 3
        }

        # 清掉可能残留的"停止请求"（防止刚起就自我关闭）；**不动**主动停标记——
        # 那是给新进程启动时判读的（谁停的、什么时候停的）。
        Remove-Item $StopRequest -Force -ErrorAction SilentlyContinue
        Remove-Item $BodyStopRequest -Force -ErrorAction SilentlyContinue

        if ($soulState -eq "elysia") {
            Write-Host "[info] 灵魂已在运行 (PID $old，命令行确认是她)，跳过灵魂启动"
        } else {
            New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
            # 她是你**主动**启动的 → 清掉"故意停机"标记，让看门狗重新负责
            Remove-Item $NoAutoStart -Force -ErrorAction SilentlyContinue
            # P0-R14：先把上一次的 stdout/stderr 归档（否则本次启动会抹掉死亡证据）
            $stamp = Get-OldStartStamp $SoulRunningMarker
            Archive-Log $Stdout $stamp
            Archive-Log $Stderr $stamp
            # 直接启动真实 python 进程（PID 即解释器，stop 可干净回收）
            $pyExe = Get-PyExe
            $proc = Start-Process -FilePath $pyExe `
                -ArgumentList @("-m", "elysia.soul.main") `
                -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
                -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
            $proc.Id | Set-Content $PidFile -NoNewline
            Start-ExitSentinel -ProcessId $proc.Id -Kind "soul" -Python $pyExe
            Write-Host "[ok] 灵魂已启动 PID=$($proc.Id)（日志: data\logs\soul.*.log）"
        }

        if ($Body) {
            if ($bodyState -eq "other") {
                Write-Host "[warn] 身体 PID $oldBody 已被其它进程占用，按陈旧 PID 处理（不会杀它），继续启动"
                Remove-Item $BodyPid -Force -ErrorAction SilentlyContinue
                $bodyState = "none"
            }
            if ($bodyState -eq "gone") {
                Remove-Item $BodyPid -Force -ErrorAction SilentlyContinue
                $bodyState = "none"
            }
            if ($bodyState -eq "unknown") {
                Write-Host "[refused] 身体 PID $oldBody 存活但读不到命令行 ⇒ 拒绝重复启动（防分叉）"
                exit 3
            }
            if ($bodyState -eq "elysia") {
                Write-Host "[info] 身体已在运行 (PID $oldBody，命令行确认是她)，跳过身体启动"
            } else {
                New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
                $pyExe = Get-PyExe
                $stampBody = Get-OldStartStamp $BodyRunningMarker
                Archive-Log $BodyOut $stampBody
                Archive-Log $BodyErr $stampBody
                $bodyProc = Start-Process -FilePath $pyExe `
                    -ArgumentList @("-m", "elysia.body.main") `
                    -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
                    -RedirectStandardOutput $BodyOut -RedirectStandardError $BodyErr -PassThru
                $bodyProc.Id | Set-Content $BodyPid -NoNewline
                Start-ExitSentinel -ProcessId $bodyProc.Id -Kind "body" -Python $pyExe
                Write-Host "[ok] 身体已启动 PID=$($bodyProc.Id)（日志: data\logs\body.*.log）"
            }
        }
    }
    "stop-body" {
        $bodyResult = Stop-OneGracefully -Label "身体" -Kind "body" -PidFilePath $BodyPid `
            -StopMarkerPath $BodyStopRequest -IntentionalMarkerPath $BodyStopIntentional `
            -TimeoutSeconds $StopTimeoutSeconds
        if ($bodyResult -eq "refused") { exit 3 }
        Write-Host "[ok] 身体已停止（灵魂不受影响）"
    }
    "stop" {
        # 记录"这是你**故意**停的"——watchdog.ps1 据此不再自动拉起（下次 start 会清掉它）
        New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
        New-Item -ItemType File -Force -Path $NoAutoStart | Out-Null
        # 始终停 body（如果存在）
        $bodyResult = Stop-OneGracefully -Label "身体" -Kind "body" -PidFilePath $BodyPid `
            -StopMarkerPath $BodyStopRequest -IntentionalMarkerPath $BodyStopIntentional `
            -TimeoutSeconds $StopTimeoutSeconds
        # 停灵魂
        $soulResult = Stop-OneGracefully -Label "灵魂" -Kind "soul" -PidFilePath $PidFile `
            -StopMarkerPath $StopRequest -IntentionalMarkerPath $StopIntentional `
            -TimeoutSeconds $StopTimeoutSeconds
        if ($soulResult -eq "not-running" -and $bodyResult -eq "not-running") {
            Write-Host "[info] 灵魂与身体都未在运行"
        }
        if ($soulResult -eq "refused" -or $bodyResult -eq "refused") { exit 3 }
    }
    "status" {
        $pid2 = Get-SoulPid
        $bp = Get-BodyPid
        $soulState = "none"
        if ($null -ne $pid2) { $soulState = Get-ProcessIdentity $pid2 "soul" }
        $bodyState = "none"
        if ($null -ne $bp) { $bodyState = Get-ProcessIdentity $bp "body" }

        if ($soulState -eq "elysia") { Write-Host "[ok] 灵魂运行中 PID=$pid2（命令行确认是她）" }
        else { Write-Host "[info] 灵魂未运行（$(Describe-Identity $soulState $pid2)）" }
        if ($bodyState -eq "elysia") { Write-Host "[ok] 身体运行中 PID=$bp（命令行确认是她）" }
        else { Write-Host "[info] 身体未运行（$(Describe-Identity $bodyState $bp)）" }
        if ($soulState -ne "elysia" -and $bodyState -ne "elysia") { exit 0 }
    }
}
