# Elysia 灵魂/身体进程控制脚本 —— PID 级精确控制（评估报告 §8-3）
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start        # 启动灵魂
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body # 启动灵魂+身体
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop         # 停止两者
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status       # 查看状态
param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",
    [switch]$Body
)

$ProjectRoot = "A:\WorkPlace\Elysia\elysia"
$RunDir   = Join-Path $ProjectRoot "data\run"
$LogDir   = Join-Path $ProjectRoot "data\logs"
$PidFile  = Join-Path $RunDir "soul.pid"
$BodyPid  = Join-Path $RunDir "body.pid"
$Stdout   = Join-Path $LogDir "soul.out.log"
$Stderr   = Join-Path $LogDir "soul.err.log"
$BodyOut  = Join-Path $LogDir "body.out.log"
$BodyErr  = Join-Path $LogDir "body.err.log"
$ErrorActionPreference = "Stop"

function Get-SoulPid {
    if (-not (Test-Path $PidFile)) { return $null }
    $content = Get-Content $PidFile -Raw
    if ($content -match "^\d+$") { return [int]$content }
    return $null
}

function Get-BodyPid {
    if (-not (Test-Path $BodyPid)) { return $null }
    $content = Get-Content $BodyPid -Raw
    if ($content -match "^\d+$") { return [int]$content }
    return $null
}

function Stop-OneProcess($pidFile) {
    $pid2 = $null
    if (-not (Test-Path $pidFile)) { return }
    $content = Get-Content $pidFile -Raw
    if ($content -match "^\d+$") { $pid2 = [int]$content }
    if ($null -eq $pid2) { return }
    $proc = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
    if ($null -ne $proc) { Stop-Process -Id $pid2 -Force }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

switch ($Action) {
    "start" {
        $old = Get-SoulPid
        if ($null -ne $old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
            Write-Error "灵魂已在运行 (PID $old)。先 stop 再 start。"; exit 1
        }
        New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
        # 启动独立进程（uv run python -m elysia.soul.main），PID 落盘
        $proc = Start-Process -FilePath "uv" `
            -ArgumentList @("run", "--no-sync", "python", "-m", "elysia.soul.main") `
            -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
            -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
        $proc.Id | Set-Content $PidFile
        Write-Host "[ok] 灵魂已启动 PID=$($proc.Id)（日志: data\logs\soul.*.log）"

        if ($Body) {
            $oldBody = Get-BodyPid
            if ($null -ne $oldBody -and (Get-Process -Id $oldBody -ErrorAction SilentlyContinue)) {
                Write-Warning "身体已在运行 (PID $oldBody)，跳过身体启动"
            } else {
                $bodyProc = Start-Process -FilePath "uv" `
                    -ArgumentList @("run", "--no-sync", "python", "-m", "elysia.body.main") `
                    -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
                    -RedirectStandardOutput $BodyOut -RedirectStandardError $BodyErr -PassThru
                $bodyProc.Id | Set-Content $BodyPid
                Write-Host "[ok] 身体已启动 PID=$($bodyProc.Id)（日志: data\logs\body.*.log）"
            }
        }
    }
    "stop" {
        # 始终停 body（如果存在）
        Stop-OneProcess $BodyPid
        Write-Host "[ok] 身体已停止（如运行中）"
        # 停灵魂
        $pid2 = Get-SoulPid
        if ($null -eq $pid2) { Write-Host "[info] 无 PID 文件，灵魂未在运行"; exit 0 }
        $proc = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
        if ($null -eq $proc) {
            Write-Host "[info] PID $pid2 已不存在（进程早已退出），清理 PID 文件"
        } else {
            Stop-Process -Id $pid2 -Force
            Write-Host "[ok] 已停止灵魂 PID=$pid2"
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    "status" {
        $pid2 = Get-SoulPid
        $bp = Get-BodyPid
        $soulOk = $null -ne $pid2 -and (Get-Process -Id $pid2 -ErrorAction SilentlyContinue)
        $bodyOk = $null -ne $bp -and (Get-Process -Id $bp -ErrorAction SilentlyContinue)
        if ($soulOk) { Write-Host "[ok] 灵魂运行中 PID=$pid2" }
        else { Write-Host "[info] 灵魂未运行" }
        if ($bodyOk) { Write-Host "[ok] 身体运行中 PID=$bp" }
        else { Write-Host "[info] 身体未运行" }
        if (-not $soulOk -and -not $bodyOk) { exit 0 }
    }
}