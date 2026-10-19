# no_autostart.ps1 —— "用户主动停机"标记的单一事实源（soul.ps1 / watchdog.ps1 共用）
#
# 语义（2026-10-19 用户批准补漏）：
#   用户 `soul.ps1 stop` 之后，看门狗不应立刻把她拉回来 —— **但"故意停机"只属于当次开机会话**。
#   机器重启代表一次新的会话：跨过一次重启，标记自动失效（否则看门狗会永远安静地不干活）。
#
# 实现：
#   · 标记写成 JSON：{"stopped_at": "...", "boot_id": "<当次开机时间>", "reason": "..."}
#   · boot_id = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime 的秒级本地时间
#   · 读取时 boot_id 不同 ⇒ 过期（stale），忽略并让看门狗正常自动拉起
#   · 向后兼容旧格式（0 字节空文件）：用文件 mtime 与当次开机时间比较
#       mtime 早于开机时间 ⇒ 过期；晚于 ⇒ 当次会话内有效
#   · 无法读取开机时间时**保守处理**：标记仍视为有效（宁可尊重用户停机，也不擅自拉起）
#
# 自测（"取消测试"等价物；还原判断逻辑后必须 FAIL）：
#   powershell -ExecutionPolicy Bypass -File scripts\no_autostart.ps1 -SelfTest
#   覆盖：同会话 JSON 抑制 / 跨重启 JSON 放行 / 旧空文件且旧 放行 / 旧空文件且新 抑制
#
# 本文件必须 UTF-8 **带 BOM**（PowerShell 5.1 按 ANSI 读中文会撑破语法，本工程已踩 3 次）。

param(
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Get-BootTime {
    <#
      当次开机的本地时间。取不到返回 $null（调用方据此保守处理，不猜）。
    #>
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        if ($null -eq $os -or $null -eq $os.LastBootUpTime) { return $null }
        return [datetime]$os.LastBootUpTime
    } catch {
        return $null
    }
}

function Get-BootId {
    param($BootTime = $null)
    if ($null -eq $BootTime) { $BootTime = Get-BootTime }
    if ($null -eq $BootTime) { return "unknown" }
    return ([datetime]$BootTime).ToString("yyyy-MM-ddTHH:mm:ss")
}

function Set-NoAutostartMarker {
    <#
      写"故意停机"标记（UTF-8 无 BOM 的 JSON；解析侧两种都容忍）。
      -BootTime 是验收注入点（模拟"当次开机时间"），生产不要传。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Reason = "user stop",
        $BootTime = $null
    )
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir) -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $payload = [ordered]@{
        stopped_at = [datetime]::Now.ToString("o")
        boot_id    = (Get-BootId -BootTime $BootTime)
        reason     = $Reason
    }
    $json = ($payload | ConvertTo-Json -Compress)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
}

function Get-NoAutostartState {
    <#
      读取标记并判断**对当次开机会话是否有效**。
      返回对象：Active(bool) / State(absent|active|stale|legacy_active|legacy_stale|unknown_boot)
                Reason(string) / BootId / MarkerBootId
      -BootTime 是验收注入点（模拟"当次开机时间"），生产不要传。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        $BootTime = $null
    )
    $result = [pscustomobject]@{
        Active       = $false
        State        = "absent"
        Reason       = $null
        BootId       = (Get-BootId -BootTime $BootTime)
        MarkerBootId = $null
    }
    if (-not (Test-Path -LiteralPath $Path)) { return $result }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $result }

    $raw = ""
    try {
        $raw = [System.IO.File]::ReadAllText($item.FullName)
    } catch {
        $raw = ""
    }
    $raw = ([string]$raw).Trim()
    $raw = $raw.TrimStart([char]0xFEFF).Trim()

    # ── 新格式：JSON（有 boot_id）──────────────────────────────
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        $parsed = $null
        try {
            $parsed = $raw | ConvertFrom-Json
        } catch {
            $parsed = $null   # 不是 JSON ⇒ 按旧格式（mtime 规则）处理
        }
        if ($null -ne $parsed) {
            $markerBoot = [string]$parsed.boot_id
            if (-not [string]::IsNullOrWhiteSpace($markerBoot)) {
                $result.MarkerBootId = $markerBoot
                $result.Reason = [string]$parsed.reason
                $currentBoot = $result.BootId
                if ($currentBoot -eq "unknown" -or $markerBoot -eq "unknown") {
                    # 读不到开机时间：保守 —— 尊重用户停机
                    $result.Active = $true
                    $result.State = "unknown_boot"
                } elseif ($markerBoot -eq $currentBoot) {
                    $result.Active = $true
                    $result.State = "active"
                } else {
                    $result.State = "stale"
                }
                return $result
            }
        }
    }

    # ── 旧格式（0 字节空文件 / 非 JSON）：mtime 与开机时间比较 ─────
    $boot = $BootTime
    if ($null -eq $boot) { $boot = Get-BootTime }
    $result.MarkerBootId = (Get-BootId -BootTime $item.LastWriteTime)
    if ($null -eq $boot) {
        $result.Active = $true
        $result.State = "unknown_boot"
        return $result
    }
    if ($item.LastWriteTime -lt [datetime]$boot) {
        $result.State = "legacy_stale"
    } else {
        $result.Active = $true
        $result.State = "legacy_active"
    }
    return $result
}

# ── 自测（-SelfTest）──────────────────────────────────────────────
function Invoke-SelfTest {
    $script:passed = 0
    $script:failed = 0
    function Check([string]$Name, [bool]$Condition) {
        if ($Condition) {
            Write-Host ("PASS  " + $Name)
            $script:passed++
        } else {
            Write-Host ("FAIL  " + $Name)
            $script:failed++
        }
    }

    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("elysia_noauto_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $boot = [datetime]"2026-10-19 18:00:00"
    try {
        # 1) 同会话 JSON 标记 ⇒ 抑制
        $p1 = Join-Path $root "same.json"
        Set-NoAutostartMarker -Path $p1 -Reason "selftest" -BootTime $boot
        $s1 = Get-NoAutostartState -Path $p1 -BootTime $boot
        Check "same-boot JSON: active" ($s1.Active -eq $true -and $s1.State -eq "active")

        # 2) 跨重启 JSON 标记 ⇒ 放行（stale）
        $s2 = Get-NoAutostartState -Path $p1 -BootTime $boot.AddHours(3)
        Check "cross-boot JSON: stale => not active" ($s2.Active -eq $false -and $s2.State -eq "stale")

        # 3) 旧空文件 + mtime 早于开机 ⇒ 放行
        $p3 = Join-Path $root "legacy_old.marker"
        New-Item -ItemType File -Force -Path $p3 | Out-Null
        (Get-Item -LiteralPath $p3).LastWriteTime = $boot.AddHours(-2)
        $s3 = Get-NoAutostartState -Path $p3 -BootTime $boot
        Check "legacy empty + old mtime: stale" ($s3.Active -eq $false -and $s3.State -eq "legacy_stale")

        # 4) 旧空文件 + mtime 晚于开机 ⇒ 当次会话内仍抑制
        $p4 = Join-Path $root "legacy_new.marker"
        New-Item -ItemType File -Force -Path $p4 | Out-Null
        (Get-Item -LiteralPath $p4).LastWriteTime = $boot.AddMinutes(5)
        $s4 = Get-NoAutostartState -Path $p4 -BootTime $boot
        Check "legacy empty + new mtime: active" ($s4.Active -eq $true -and $s4.State -eq "legacy_active")

        # 5) 不存在 ⇒ absent（不是"故意停机"）
        $s5 = Get-NoAutostartState -Path (Join-Path $root "absent.marker") -BootTime $boot
        Check "missing marker: absent" ($s5.Active -eq $false -and $s5.State -eq "absent")

        Write-Host ""
        Write-Host ("SELFTEST RESULT: " + $script:passed + " passed, " + $script:failed + " failed")
        if ($script:failed -gt 0) { return 1 }
        return 0
    } finally {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    exit (Invoke-SelfTest)
}
