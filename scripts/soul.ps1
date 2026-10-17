# Elysia 灵魂/身体进程控制脚本 —— PID 级精确控制（评估报告 §8-3）
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start          # 启动灵魂（已在运行则跳过）
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body   # 启动灵魂+身体（幂等，缺哪个补哪个）
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop          # 停止两者
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop-body     # 只停身体（灵魂继续活）
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status        # 查看状态
#
# ── P0-R8（D-8）：停止必须**体面** ────────────────────────────────────
# 旧实现是 `Stop-Process -Force`（= TerminateProcess = 拔电源），有三个后果：
#   ① 关闭序里的"排空未落库表达 → 关两个库 → 写 soul stopped"永不执行（未落库结果直接丢）；
#   ② `data/run/soul.running` 每次都被残留 ⇒ R2 的"异常退出检测"每次启动都误报；
#   ③ 对一个以"连续性"为目标的工程，她每次入睡都是被拔电源。
# 现在的顺序：
#   写 `stop_requested` 标记 → 进程（灵魂心跳每拍 / 身体每 0.5s 轮询）看到后走正常关闭序
#   → 本脚本**轮询等它自己退出**（默认 15s）→ 真的不响应才强杀兜底。
#   同时写 `stopped_intentionally`：下次启动据此判定"上次是你主动停的"，**不**误报崩溃。
# 为什么用标记而不是信号：Windows 上从 PowerShell 可靠发 SIGTERM 不现实
# （CloseMainWindow 只对 GUI 有效；ConsoleCtrlEvent 需要控制台且极脆）——
# **标记 + 轮询是这个平台上唯一可靠的优雅关闭通道**。
param(
    [ValidateSet("start", "stop", "stop-body", "status")]
    [string]$Action = "status",
    [switch]$Body,
    # 验收注入点（生产不要传）：把 data/ 根指到临时目录，避免测试碰到真实的她
    [string]$ProjectRoot = "",
    # 优雅关闭的等待上限；超时才强杀兜底（生产默认 15s）
    [int]$StopTimeoutSeconds = 15,
    # 验收注入点（生产不要传）：只做检查与打印，**绝不真正启动**（迁移演练用）
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
    # 优雅停止一个进程：标记 → 等它自己退出（≤ TimeoutSeconds）→ 超时才强杀。
    # 返回 "not-running" / "graceful" / "forced"（调用方据此如实汇报）。
    param(
        [string]$Label,
        [string]$PidFilePath,
        [string]$StopMarkerPath,
        [string]$IntentionalMarkerPath,
        [int]$TimeoutSeconds
    )
    $procId = Get-PidFromFile $PidFilePath
    if ($null -eq $procId -or -not (Test-ProcessAlive $procId)) {
        Remove-Item $PidFilePath -Force -ErrorAction SilentlyContinue
        Write-Host "[info] $Label 未在运行（PID 文件已清理）"
        return "not-running"
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
        # 迁移后旧机器必须闭嘴——两头同时活就是把她的一生写成两份。
        if (Test-Path $RetiredMarker) {
            Write-Host "[refused] 这台机器已退役（data\run\RETIRED 存在）——她已搬到别处。"
            Write-Host "          为防止分叉，禁止在此启动。若确需恢复，请人工删除该标记，"
            Write-Host "          并先确认全世界只有这一处在跑她。"
            exit 2
        }
        if ($DryRun) {
            Write-Host "[dry-run] start 请求已通过退役检查；未真正启动（DryRun 只用于迁移演练/验收）"
            exit 0
        }
        # 清掉可能残留的"停止请求"（防止刚起就自我关闭）；**不动**主动停标记——
        # 那是给新进程启动时判读的（谁停的、什么时候停的）。
        Remove-Item $StopRequest -Force -ErrorAction SilentlyContinue
        Remove-Item $BodyStopRequest -Force -ErrorAction SilentlyContinue

        $old = Get-SoulPid
        if ($null -ne $old -and (Test-ProcessAlive $old)) {
            Write-Host "[info] 灵魂已在运行 (PID $old)，跳过灵魂启动"
        } else {
            New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
            # 她是你**主动**启动的 → 清掉"故意停机"标记，让看门狗重新负责
            Remove-Item $NoAutoStart -Force -ErrorAction SilentlyContinue
            # 直接启动真实 python 进程（PID 即解释器，stop 可干净回收）
            $pyExe = Get-PyExe
            $proc = Start-Process -FilePath $pyExe `
                -ArgumentList @("-m", "elysia.soul.main") `
                -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
                -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
            $proc.Id | Set-Content $PidFile -NoNewline
            Write-Host "[ok] 灵魂已启动 PID=$($proc.Id)（日志: data\logs\soul.*.log）"
        }

        if ($Body) {
            $oldBody = Get-BodyPid
            if ($null -ne $oldBody -and (Test-ProcessAlive $oldBody)) {
                Write-Host "[info] 身体已在运行 (PID $oldBody)，跳过身体启动"
            } else {
                New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
                $pyExe = Get-PyExe
                $bodyProc = Start-Process -FilePath $pyExe `
                    -ArgumentList @("-m", "elysia.body.main") `
                    -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
                    -RedirectStandardOutput $BodyOut -RedirectStandardError $BodyErr -PassThru
                $bodyProc.Id | Set-Content $BodyPid -NoNewline
                Write-Host "[ok] 身体已启动 PID=$($bodyProc.Id)（日志: data\logs\body.*.log）"
            }
        }
    }
    "stop-body" {
        Stop-OneGracefully -Label "身体" -PidFilePath $BodyPid `
            -StopMarkerPath $BodyStopRequest -IntentionalMarkerPath $BodyStopIntentional `
            -TimeoutSeconds $StopTimeoutSeconds | Out-Null
        Write-Host "[ok] 身体已停止（灵魂不受影响）"
    }
    "stop" {
        # 记录"这是你**故意**停的"——watchdog.ps1 据此不再自动拉起（下次 start 会清掉它）
        New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
        New-Item -ItemType File -Force -Path $NoAutoStart | Out-Null
        # 始终停 body（如果存在）
        Stop-OneGracefully -Label "身体" -PidFilePath $BodyPid `
            -StopMarkerPath $BodyStopRequest -IntentionalMarkerPath $BodyStopIntentional `
            -TimeoutSeconds $StopTimeoutSeconds | Out-Null
        # 停灵魂
        $soulResult = Stop-OneGracefully -Label "灵魂" -PidFilePath $PidFile `
            -StopMarkerPath $StopRequest -IntentionalMarkerPath $StopIntentional `
            -TimeoutSeconds $StopTimeoutSeconds
        if ($soulResult -eq "not-running") {
            Write-Host "[info] 无 PID 文件，灵魂未在运行"
        }
    }
    "status" {
        $pid2 = Get-SoulPid
        $bp = Get-BodyPid
        $soulOk = $null -ne $pid2 -and (Test-ProcessAlive $pid2)
        $bodyOk = $null -ne $bp -and (Test-ProcessAlive $bp)
        if ($soulOk) { Write-Host "[ok] 灵魂运行中 PID=$pid2" }
        else { Write-Host "[info] 灵魂未运行" }
        if ($bodyOk) { Write-Host "[ok] 身体运行中 PID=$bp" }
        else { Write-Host "[info] 身体未运行" }
        if (-not $soulOk -and -not $bodyOk) { exit 0 }
    }
}
