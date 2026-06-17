<#
.SYNOPSIS
    Reads Maple's per-session Claude Code activity logs for a given day and
    writes a plain-English Markdown "Maple Digest" into this (Rocky) project.

.DESCRIPTION
    Maple writes one JSONL log file per Claude Code session (one event per line)
    into a shared OneDrive folder. This script gathers all of a single day's
    files, groups events by session, and produces a digest that answers:
    who touched Maple, what they changed, what they ran, and whether anything broke.

    The script does NOT call any AI and has no external dependencies, so it runs
    unattended on a schedule and produces identical output every time.

.PARAMETER Date
    The day to summarize, as YYYY-MM-DD. Defaults to today.

.PARAMETER Yesterday
    Convenience switch: summarize yesterday instead of today. Ignored if -Date
    is also supplied.

.EXAMPLE
    .\summarize-maple-activity.ps1
    Summarizes today's Maple activity.

.EXAMPLE
    .\summarize-maple-activity.ps1 -Date 2026-06-17

.EXAMPLE
    .\summarize-maple-activity.ps1 -Yesterday

.NOTES
    If Rocky ever moves to a different PC, only $MapleActivityFolder (below)
    needs changing -- the OneDrive path prefix differs per machine.
#>

[CmdletBinding()]
param(
    [string]$Date,
    [switch]$Yesterday
)

# ---------------------------------------------------------------------------
# CONFIG -- change this one line if the logs live somewhere else on this PC.
# ---------------------------------------------------------------------------
$MapleActivityFolder = "C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files\Maple\logs\activity"

# Digests are written into this Rocky project, next to this script.
$DigestFolder = Join-Path $PSScriptRoot "digests"

# ---------------------------------------------------------------------------
# Resolve the target date.
# ---------------------------------------------------------------------------
if ($Date) {
    # Validate the supplied date string.
    $parsed = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($Date, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
        Write-Error "Invalid -Date '$Date'. Use the format YYYY-MM-DD (e.g. 2026-06-17)."
        exit 1
    }
    $TargetDate = $parsed.ToString('yyyy-MM-dd')
}
elseif ($Yesterday) {
    $TargetDate = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')
}
else {
    $TargetDate = (Get-Date).ToString('yyyy-MM-dd')
}

# Ensure the digests folder exists.
if (-not (Test-Path -LiteralPath $DigestFolder)) {
    New-Item -ItemType Directory -Path $DigestFolder -Force | Out-Null
}
$DigestPath = Join-Path $DigestFolder "maple-activity-$TargetDate.md"

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

# Pull just the file name out of a full path for compact display.
function Get-Leaf([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return $path }
    return Split-Path -Path $path -Leaf
}

# Format an ISO-8601-with-offset timestamp as a short local "HH:mm" for display.
# Falls back to the raw string if it can't be parsed.
function Format-Time([string]$ts) {
    if ([string]::IsNullOrWhiteSpace($ts)) { return "??:??" }
    $dto = [datetimeoffset]::MinValue
    if ([datetimeoffset]::TryParse($ts, [ref]$dto)) {
        return $dto.ToString('HH:mm')
    }
    return $ts
}

# Escape characters that would break Markdown table / inline rendering,
# and collapse runs of whitespace so multi-line commands display on one line.
function Format-Inline([string]$text) {
    if ($null -eq $text) { return "" }
    $t = $text -replace "`r`n", " " -replace "`n", " " -replace "`r", " "
    $t = $t -replace "\s+", " "
    $t = $t -replace "\|", "\|"
    return $t.Trim()
}

# Keep the digest skimmable: trim long single-line strings (e.g. giant pasted
# shell blocks) to a readable length with an ellipsis. Operates on already-
# inlined text.
function Limit-Length([string]$text, [int]$max) {
    if ($null -eq $text) { return "" }
    if ($text.Length -le $max) { return $text }
    return $text.Substring(0, $max).TrimEnd() + " ..."
}

# ---------------------------------------------------------------------------
# Find this day's log files.
# ---------------------------------------------------------------------------
$files = @()
if (Test-Path -LiteralPath $MapleActivityFolder) {
    $files = @(Get-ChildItem -LiteralPath $MapleActivityFolder -Filter "$TargetDate`__*.jsonl" -File -ErrorAction SilentlyContinue)
}

# No logs -> write a "nothing happened" digest and stop (do not error).
if ($files.Count -eq 0) {
    $empty = @()
    $empty += "# Maple Digest -- $TargetDate"
    $empty += ""
    $empty += "No Maple activity recorded for $TargetDate."
    $empty += ""
    $empty += "_Generated $((Get-Date).ToString('yyyy-MM-dd HH:mm')) from ``$MapleActivityFolder``._"
    Set-Content -LiteralPath $DigestPath -Value ($empty -join "`r`n") -Encoding UTF8
    Write-Host "No Maple activity for $TargetDate."
    Write-Host "Digest written to: $DigestPath"
    return
}

# ---------------------------------------------------------------------------
# Read + parse every line; skip any that won't parse (half-written last line).
# ---------------------------------------------------------------------------
$events = New-Object System.Collections.Generic.List[object]
$skippedLines = 0

foreach ($f in $files) {
    $lines = Get-Content -LiteralPath $f.FullName -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $obj = $line | ConvertFrom-Json -ErrorAction Stop
            $events.Add($obj)
        }
        catch {
            $skippedLines++
        }
    }
}

# ---------------------------------------------------------------------------
# Group by session.
# ---------------------------------------------------------------------------
$sessions = $events | Group-Object -Property session

# Day-level rollups.
$allUsers = @($events | Where-Object { $_.user } | Select-Object -ExpandProperty user -Unique | Sort-Object)
$allFiles = @($events | Where-Object { $_.event -eq 'PostToolUse' -and $_.file } | Select-Object -ExpandProperty file -Unique)
$allCommands = @($events | Where-Object { $_.event -eq 'PostToolUse' -and $_.command })
$allFailures = @($events | Where-Object { $_.event -eq 'PostToolUse' -and $_.success -eq $false })

# ---------------------------------------------------------------------------
# Build the digest.
# ---------------------------------------------------------------------------
$md = New-Object System.Collections.Generic.List[string]

$md.Add("# Maple Digest -- $TargetDate")
$md.Add("")
$md.Add("## Day summary")
$md.Add("")
$sessionCount = @($sessions).Count
$whoList = if ($allUsers.Count -gt 0) { $allUsers -join ", " } else { "(unknown)" }
$md.Add("- **Sessions:** $sessionCount across $($allUsers.Count) person(s): $whoList")
$md.Add("- **Distinct files changed:** $($allFiles.Count)")
$md.Add("- **Commands run:** $($allCommands.Count)")
if ($allFailures.Count -gt 0) {
    $md.Add("- **:warning: Failures (success=false):** $($allFailures.Count) -- see flagged commands below")
}
else {
    $md.Add("- **Failures (success=false):** 0")
}
if ($skippedLines -gt 0) {
    $md.Add("- _Note: skipped $skippedLines unparseable log line(s)._")
}
$md.Add("")

# Order sessions by their earliest timestamp.
$orderedSessions = $sessions | Sort-Object -Property @{ Expression = {
    ($_.Group | Where-Object { $_.ts } | Sort-Object ts | Select-Object -First 1).ts
} }

$md.Add("## Sessions")
$md.Add("")

foreach ($s in $orderedSessions) {
    $evs = @($s.Group | Sort-Object -Property ts)
    if ($evs.Count -eq 0) { continue }

    $user = ($evs | Where-Object { $_.user } | Select-Object -First 1).user
    $machine = ($evs | Where-Object { $_.machine } | Select-Object -First 1).machine
    if (-not $user) { $user = "(unknown user)" }
    if (-not $machine) { $machine = "(unknown machine)" }

    $sessionId = "$($s.Name)"
    $shortId = if ($sessionId.Length -ge 8) { $sessionId.Substring(0, 8) } else { $sessionId }

    $firstTs = ($evs | Where-Object { $_.ts } | Select-Object -First 1).ts
    $lastTs = ($evs | Where-Object { $_.ts } | Select-Object -Last 1).ts
    $span = "$(Format-Time $firstTs) -> $(Format-Time $lastTs)"

    $md.Add("### $user on $machine  ($span)")
    $md.Add("")
    $md.Add("_Session ``$shortId``_")
    $md.Add("")

    # Intent: UserPromptSubmit prompts in order.
    $prompts = @($evs | Where-Object { $_.event -eq 'UserPromptSubmit' -and $_.prompt })
    $md.Add("**Intent:**")
    if ($prompts.Count -gt 0) {
        foreach ($p in $prompts) {
            $md.Add("- `"$(Limit-Length (Format-Inline $p.prompt) 300)`"")
        }
    }
    else {
        $md.Add("- _(no prompts recorded)_")
    }
    $md.Add("")

    # Files changed: distinct file values.
    $sFiles = @($evs | Where-Object { $_.event -eq 'PostToolUse' -and $_.file } | Select-Object -ExpandProperty file -Unique)
    $md.Add("**Files changed:** $($sFiles.Count)")
    if ($sFiles.Count -gt 0) {
        foreach ($file in $sFiles) {
            $md.Add("- ``$(Get-Leaf $file)``")
        }
    }
    else {
        $md.Add("- _(none)_")
    }
    $md.Add("")

    # Commands run, with description; flag failures.
    $sCmds = @($evs | Where-Object { $_.event -eq 'PostToolUse' -and $_.command })
    $sFails = @($sCmds | Where-Object { $_.success -eq $false })
    $cmdHeader = "**Commands run:** $($sCmds.Count)"
    if ($sFails.Count -gt 0) { $cmdHeader += " ($($sFails.Count) failed)" }
    $md.Add($cmdHeader)
    if ($sCmds.Count -gt 0) {
        foreach ($c in $sCmds) {
            $flag = if ($c.success -eq $false) { ":x: " } else { "" }
            $desc = if ($c.description) { " -- $(Format-Inline $c.description)" } else { "" }
            $md.Add("- $flag``$(Limit-Length (Format-Inline $c.command) 160)``$desc")
        }
    }
    else {
        $md.Add("- _(none)_")
    }
    $md.Add("")

    # Git movement: head at first SessionStart vs. last Stop.
    $startEv = $evs | Where-Object { $_.event -eq 'SessionStart' } | Select-Object -First 1
    $stopEv = $evs | Where-Object { $_.event -eq 'Stop' } | Select-Object -Last 1
    $startHead = if ($startEv -and $startEv.git_head) { $startEv.git_head } else { $null }
    $stopHead = if ($stopEv -and $stopEv.git_head) { $stopEv.git_head } else { $null }
    $branch = if ($startEv -and $startEv.git_branch) { $startEv.git_branch } elseif ($stopEv -and $stopEv.git_branch) { $stopEv.git_branch } else { "?" }

    if ($startHead -and $stopHead) {
        if ($startHead -eq $stopHead) {
            $md.Add("**Git:** ``$branch`` at ``$startHead`` -- no new commit during session")
        }
        else {
            $md.Add("**Git:** ``$branch`` ``$startHead`` -> ``$stopHead`` -- committed")
        }
    }
    elseif ($startHead -and -not $stopHead) {
        $md.Add("**Git:** ``$branch`` started at ``$startHead`` -- no Stop event recorded (session may still be open)")
    }
    elseif (-not $startHead -and $stopHead) {
        $md.Add("**Git:** ``$branch`` ended at ``$stopHead`` -- no SessionStart recorded")
    }
    else {
        $md.Add("**Git:** _(no git info recorded)_")
    }
    $md.Add("")
    $md.Add("---")
    $md.Add("")
}

$md.Add("_Generated $((Get-Date).ToString('yyyy-MM-dd HH:mm')) from ``$MapleActivityFolder``._")

Set-Content -LiteralPath $DigestPath -Value ($md -join "`r`n") -Encoding UTF8

Write-Host "Maple digest for $TargetDate written ($sessionCount session(s), $($allFiles.Count) file(s) changed, $($allCommands.Count) command(s), $($allFailures.Count) failure(s))."
Write-Host "Digest written to: $DigestPath"
