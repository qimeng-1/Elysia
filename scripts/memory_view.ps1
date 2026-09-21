# Elysia 记忆浏览器启动脚本（只读观测，可与运行中的灵魂并存）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\memory_view.ps1
$ProjectRoot = "A:\WorkPlace\Elysia\elysia"
$venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Error "未找到 .venv 解释器: $venvPy"; exit 1 }
Start-Process -FilePath $venvPy `
    -ArgumentList @("-m", "elysia.tools.memory_view") `
    -WorkingDirectory $ProjectRoot
Write-Host "[ok] 记忆浏览器已启动（只读 data\heartbeat.db）"