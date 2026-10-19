# gate.ps1 -- the ONLY gate entry for the Elysia repository.
#
# Why this file exists (T-013):
#   * `uv run pytest` / `uv run mypy` go through a broken trampoline on this box.
#   * adding `-q` on the command line stacks with pyproject addopts (-q) and hides
#     the final summary line.
#   * people cannot remember four exact commands; a typo wastes a full gate run.
#   So: one fixed entry point. Wrong usage is REJECTED IN CODE, not in docs.
#
# Fixed order (do not reorder):
#   1. uv run --no-sync ruff check
#   2. uv run --no-sync ruff format --check
#   3. .venv\Scripts\python.exe -m mypy src
#   4. .venv\Scripts\python.exe -m pytest
#
# All four always run (no early return). Each step prints: command, exit code,
# last 3 output lines. Summary: "GATE PASSED" / "GATE FAILED (n)".
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\gate.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\gate.ps1 -Skip pytest
#   powershell -ExecutionPolicy Bypass -File scripts\gate.ps1 -SelfTest
#
# -Skip accepts: ruff-check, ruff-format, ruff (both), mypy, pytest.
# A skipped gate is explicitly marked "NOT FULL GREEN" in the summary.
#
# This file must stay UTF-8 **with BOM** (PowerShell 5.1 ANSI reads break Chinese syntax).
# Comments are ASCII on purpose (same reason).

param(
    # Rest FIRST: stray positional args (e.g. "uv run pytest") must land here so the
    # guard can reject them; otherwise PowerShell binds them to -Skip (array).
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Rest = @(),
    [string[]]$Skip = @(),
    [switch]$SelfTest,
    # Decoys: we accept these names only so we can REJECT them with a clear message
    # instead of a PowerShell "parameter cannot be found" error.
    [switch]$q,
    [switch]$qq,
    [switch]$quiet
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$AllowedSkip = @("ruff-check", "ruff-format", "ruff", "mypy", "pytest")

function Get-DangerousArgError {
    param([string[]]$InvocationArgs = @())
    $joined = (($InvocationArgs | ForEach-Object { [string]$_ }) -join " ").Trim()
    if ([string]::IsNullOrWhiteSpace($joined)) {
        if ($q -or $qq -or $quiet) {
            return "Refusing: do not add -q/-qq/--quiet. pyproject addopts already has -q; stacking hides the summary."
        }
        if ($env:PYTEST_ADDOPTS -and $env:PYTEST_ADDOPTS -match '(^|\s)-q{1,2}(\s|$)') {
            return "Refusing: PYTEST_ADDOPTS contains -q; stacking hides the pytest summary. Clear it before running the gate."
        }
        return $null
    }
    if ($joined -match '(^|\s)-q{1,2}(\s|$)' -or $joined -match '(^|\s)--quiet(\s|$)') {
        return "Refusing dangerous argument '$joined': do not add -q. Use scripts\gate.ps1 (fixed commands) instead."
    }
    if ($joined -match '(^|\s)uv\s+run(\s|$)') {
        return "Refusing dangerous argument '$joined': never use 'uv run' for pytest/mypy here (broken trampoline). The gate already picks the correct commands."
    }
    return $null
}

function Get-StepTailLines {
    param([string[]]$Lines, [int]$Count = 3)
    if ($null -eq $Lines -or $Lines.Count -eq 0) { return @("(no output)") }
    if ($Lines.Count -le $Count) { return $Lines }
    return $Lines[($Lines.Count - $Count)..($Lines.Count - 1)]
}

function Invoke-Gate {
    param(
        [string[]]$Skip = @(),
        [hashtable]$Runners = $null
    )
    $steps = @(
        [pscustomobject]@{ Key = "ruff-check";  Cmd = "uv run --no-sync ruff check";        Exe = "uv"; Args = @("run", "--no-sync", "ruff", "check") },
        [pscustomobject]@{ Key = "ruff-format"; Cmd = "uv run --no-sync ruff format --check"; Exe = "uv"; Args = @("run", "--no-sync", "ruff", "format", "--check") },
        [pscustomobject]@{ Key = "mypy";        Cmd = ".\.venv\Scripts\python.exe -m mypy src"; Exe = ".\.venv\Scripts\python.exe"; Args = @("-m", "mypy", "src") },
        [pscustomobject]@{ Key = "pytest";      Cmd = ".\.venv\Scripts\python.exe -m pytest";   Exe = ".\.venv\Scripts\python.exe"; Args = @("-m", "pytest") }
    )

    $skipKeys = @()
    foreach ($token in $Skip) {
        $t = ([string]$token).Trim().ToLower()
        if ($t -eq "ruff") { $skipKeys += @("ruff-check", "ruff-format"); continue }
        if ($t -eq "format") { $t = "ruff-format" }
        if ($AllowedSkip -contains $t) { $skipKeys += $t; continue }
        throw "Invalid -Skip value '$token'. Allowed: $($AllowedSkip -join ', ')."
    }
    $skipKeys = @($skipKeys | Select-Object -Unique)

    $failures = @()
    $ran = 0
    $skipped = @()

    Write-Host ""
    Write-Host "=== Elysia gate ==="
    Write-Host "root: $Root"
    foreach ($step in $steps) {
        if ($skipKeys -contains $step.Key) {
            $skipped += $step.Key
            Write-Host ""
            Write-Host "[SKIP] $($step.Cmd)"
            continue
        }
        $ran++
        Write-Host ""
        Write-Host "[RUN ] $($step.Cmd)"
        $exitCode = 0
        $lines = @()
        if ($null -ne $Runners -and $Runners.ContainsKey($step.Key)) {
            $result = & $Runners[$step.Key]
            $exitCode = [int]$result.ExitCode
            $lines = @($result.Output | ForEach-Object { [string]$_ })
        } else {
            Push-Location $Root
            try {
                $lines = @(& $step.Exe @($step.Args) 2>&1 | ForEach-Object { [string]$_ })
                $exitCode = [int]$LASTEXITCODE
            } finally {
                Pop-Location
            }
        }
        Write-Host "[EXIT] $exitCode"
        foreach ($line in Get-StepTailLines -Lines $lines -Count 3) {
            Write-Host "    $line"
        }
        if ($exitCode -ne 0) { $failures += $step.Key }
    }

    $skippedText = ""
    if ($skipped.Count -gt 0) { $skippedText = " SKIPPED: " + ($skipped -join ", ") }
    if ($failures.Count -gt 0) {
        $summary = "GATE FAILED (" + $failures.Count + ")" + $skippedText
        $exit = 1
    } else {
        $summary = "GATE PASSED"
        if ($skipped.Count -gt 0) { $summary = "GATE PASSED" + $skippedText + " -- NOT FULL GREEN" }
        $exit = 0
    }
    Write-Host ""
    Write-Host "=== SUMMARY ==="
    Write-Host ("ran=" + $ran + "/" + $steps.Count + " failed=" + $failures.Count + " skipped=" + $skipped.Count)
    Write-Host $summary
    return [pscustomobject]@{
        ExitCode     = $exit
        Failures     = $failures.Count
        FailureNames = $failures
        Ran          = $ran
        Skipped      = $skipped
        Summary      = $summary
    }
}

function Invoke-SelfTest {
    $script:pass = 0
    $script:fail = 0
    function Check([string]$Name, [bool]$Condition) {
        if ($Condition) {
            Write-Host ("PASS  " + $Name)
            $script:pass++
        } else {
            Write-Host ("FAIL  " + $Name)
            $script:fail++
        }
    }

    $okOut = @("ok-1", "ok-2", "ok-3", "ok-4")
    $badOut = @("bad-1", "bad-2", "bad-3", "bad-4", "bad-5")
    $fakeOk = { [pscustomobject]@{ ExitCode = 0; Output = $okOut } }
    $fakeBad = { [pscustomobject]@{ ExitCode = 2; Output = $badOut } }
    $allOk = @{
        "ruff-check"  = $fakeOk
        "ruff-format" = $fakeOk
        "mypy"        = $fakeOk
        "pytest"      = $fakeOk
    }
    $oneBad = @{
        "ruff-check"  = $fakeOk
        "ruff-format" = $fakeOk
        "mypy"        = $fakeBad
        "pytest"      = $fakeOk
    }

    # 1) failure => exit=1, summary has failure count
    $r1 = Invoke-Gate -Skip @() -Runners $oneBad
    Check "failure => exit=1" ($r1.ExitCode -eq 1)
    Check "failure summary has count" ($r1.Summary -match "GATE FAILED \(1\)")

    # 2) all four always ran (no early return)
    Check "all four steps ran" ($r1.Ran -eq 4)
    Check "only one failure recorded" ($r1.Failures -eq 1)

    # 3) skip is explicit and cannot look like full green
    $r2 = Invoke-Gate -Skip @("pytest") -Runners $allOk
    Check "skip => exit=0" ($r2.ExitCode -eq 0)
    Check "skip reduces ran to 3" ($r2.Ran -eq 3)
    Check "skip is listed in summary" ($r2.Summary -match "SKIPPED: pytest")
    Check "skip marks NOT FULL GREEN" ($r2.Summary -match "NOT FULL GREEN")

    # 4) dangerous usage guard (removing the guard makes these FAIL)
    Check "reject -q" ($null -ne (Get-DangerousArgError -InvocationArgs @("-q")))
    Check "reject -qq" ($null -ne (Get-DangerousArgError -InvocationArgs @("-qq")))
    Check "reject --quiet" ($null -ne (Get-DangerousArgError -InvocationArgs @("--quiet")))
    Check "reject uv run pytest" ($null -ne (Get-DangerousArgError -InvocationArgs @("uv", "run", "pytest")))
    Check "reject uv run mypy" ($null -ne (Get-DangerousArgError -InvocationArgs @("uv", "run", "--no-sync", "mypy", "src")))
    Check "allow clean invocation" ($null -eq (Get-DangerousArgError -InvocationArgs @()))

    Write-Host ""
    Write-Host ("SELFTEST RESULT: " + $script:pass + " passed, " + $script:fail + " failed")
    if ($script:fail -gt 0) { return 1 }
    return 0
}

if ($SelfTest) {
    exit (Invoke-SelfTest)
}

$danger = Get-DangerousArgError -InvocationArgs $Rest
if ($null -ne $danger) {
    Write-Host "[ERROR] $danger"
    exit 2
}

try {
    $result = Invoke-Gate -Skip $Skip
} catch {
    Write-Host ("[ERROR] " + $_.Exception.Message)
    exit 2
}
exit $result.ExitCode
