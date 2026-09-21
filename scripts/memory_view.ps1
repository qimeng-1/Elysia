# Elysia memory browser launcher (read-only; safe to run while soul is alive)
# Usage (any working directory; paths are absolute):
#   powershell -ExecutionPolicy Bypass -File "A:\WorkPlace\Elysia\elysia\scripts\memory_view.ps1"
# NOTE: kept ASCII-only on purpose -- Windows PowerShell 5.1 decodes BOM-less
# .ps1 as ANSI, which corrupts non-ASCII characters and breaks parsing.
$ProjectRoot = "A:\WorkPlace\Elysia\elysia"
$venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "data\logs"
$Out = Join-Path $LogDir "memory_view.out.log"
$Err = Join-Path $LogDir "memory_view.err.log"

if (-not (Test-Path $venvPy)) { Write-Error "venv python not found: $venvPy"; exit 1 }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$proc = Start-Process -FilePath $venvPy `
    -ArgumentList @("-m", "elysia.tools.memory_view") `
    -WorkingDirectory $ProjectRoot -PassThru `
    -RedirectStandardOutput $Out -RedirectStandardError $Err

# Confirm it did not crash: if it exited, print the log tail (otherwise the
# failure is swallowed and you only see "no window appeared").
Start-Sleep -Seconds 2
if ($proc.HasExited) {
    Write-Host "[error] memory viewer failed to start (exit code $($proc.ExitCode)); log tail:"
    if (Test-Path $Err) { Get-Content $Err -Tail 30 }
    if (Test-Path $Out) { Get-Content $Out -Tail 30 }
    exit 1
}
Write-Host "[ok] memory viewer started (PID $($proc.Id)); reading data\heartbeat.db"