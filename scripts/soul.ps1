# Elysia 灵魂进程控制脚本 —— PID 级精确控制（评估报告 §8-3）
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start   # 启动灵魂
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop    # 停止灵魂（仅杀 PID 文件记录进程）
#   powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status  # 查看状态
param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status"
)

$ProjectRoot = "A:\WorkPlace\Elysia\elysia"
$RunDir   = Join-Path $ProjectRoot "data\run"
$PidFile  = Join-Path $RunDir "soul.pid"
$LogDir   = Join-Path $ProjectRoot "data\logs"
$Stdout   = Join-Path $LogDir "soul.out.log"
$Stderr   = Join-Path $LogDir "soul.err.log"
$ErrorActionPreference = "Stop"

function Get-SoulPid {
    if (-not (Test-Path $PidFile)) { return $null }
    $content = Get-Content $PidFile -Raw
    if ($content -match "^\d+$") { return [int]$content }
    return $null
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
            -ArgumentList @("run", "python", "-m", "elysia.soul.main") `
            -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
            -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
        $proc.Id | Set-Content $PidFile
        Write-Host "[ok] 灵魂已启动 PID=$($proc.Id)（日志: data\logs\soul.*.log）"
    }
    "stop" {
        $pid2 = Get-SoulPid
        if ($null -eq $pid2) { Write-Host "[info] 无 PID 文件，灵魂未在运行"; exit 0 }
        $proc = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
        if ($null -eq $proc) {
            Write-Host "[info] PID $pid2 已不存在（进程早已退出），清理 PID 文件"
        }
        else {
            # 只杀 PID 文件记录的这一个进程，绝不用通配匹配（避免误杀其他 python）
            Stop-Process -Id $pid2 -Force
            Write-Host "[ok] 已停止灵魂 PID=$pid2"
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    "status" {
        $pid2 = Get-SoulPid
        if ($null -eq $pid2) { Write-Host "[info] 灵魂未运行"; exit 0 }
        $proc = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
        if ($null -eq $proc) { Write-Host "[warn] PID 文件存在但进程已退出 (PID $pid2)"; exit 1 }
        Write-Host "[ok] 灵魂运行中 PID=$pid2"
    }
}
