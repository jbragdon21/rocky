@echo off
REM ===================================================================
REM  restart_dashboard.cmd - stop dashboard.exe and start it again
REM ===================================================================
REM  The dashboard is the only long-lived Rocky process, so it is the
REM  only one that needs this. rocky.exe runs as one-shot scheduled
REM  tasks and picks up a new build on its next run automatically.
REM
REM  Two things a restart accomplishes:
REM    1. Loads the build that OneDrive has synced into this folder.
REM    2. Releases the running image, which lets OneDrive land an
REM       update it has been retrying.
REM
REM  Paths come from %~dp0 (this script's own folder), so the same file
REM  works on the dev laptop and the Rocky laptop, whose OneDrive paths
REM  differ. Keep it next to dashboard.exe.
REM
REM  Also safe to use as a "make sure the dashboard is up" task: if
REM  nothing is running, taskkill no-ops and it just starts.
REM
REM  Usage:  restart_dashboard.cmd [extra dashboard.exe args]
REM ===================================================================

setlocal
set "DIR=%~dp0"
set "DATADIR=C:\Rocky"
set "LOG=%DATADIR%\dashboard_restart.log"

if not exist "%DATADIR%" mkdir "%DATADIR%"

if not exist "%DIR%dashboard.exe" (
    echo ERROR: dashboard.exe not found in %DIR%
    echo [%DATE% %TIME%] ERROR: dashboard.exe not found in %DIR% >>"%LOG%"
    exit /b 1
)

REM Note the space before every >> below. Without it, a log line ending
REM in a digit (say "--port 8080") parses as a handle redirect rather
REM than text, silently truncating the line and redirecting stdin.
echo [%DATE% %TIME%] Stopping dashboard.exe >>"%LOG%"
taskkill /IM dashboard.exe /F >nul 2>&1

REM Give Windows a moment to release the image lock. OneDrive usually
REM lands an update even while the exe runs (Windows permits renaming a
REM running image), so this wait is short on purpose - the dashboard is
REM only down a few seconds. If a pending sync does not make it in time,
REM the header's "New build - restart" badge lights up and the next run
REM collects it.
REM
REM timeout needs a real console; under some schedulers stdin is
REM redirected and it errors out, so fall back to ping as the sleep.
timeout /t 15 /nobreak >nul 2>&1 || ping -n 16 127.0.0.1 >nul 2>&1

echo [%DATE% %TIME%] Starting %DIR%dashboard.exe %* >>"%LOG%"
start "Rocky Dashboard" /min /d "%DIR%" "%DIR%dashboard.exe" %*

endlocal
