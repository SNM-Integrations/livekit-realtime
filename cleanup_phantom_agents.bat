@echo off
REM Kill phantom agent processes to prevent session minute waste
REM Run this regularly or before any work session

echo =========================================
echo Cleaning Up Phantom Agent Processes
echo =========================================
echo.

echo Checking for running agent.py processes...
wmic process where "commandline like '%%agent.py%%'" get processid,commandline,creationdate 2>nul | findstr /i "agent.py"

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo WARNING: Found phantom processes! Killing them now...
    wmic process where "commandline like '%%agent.py%%'" delete
    echo.
    echo Killed phantom processes!
) ELSE (
    echo No phantom processes found - you're good!
)

echo.
echo =========================================
echo Cleanup Complete
echo =========================================
pause
