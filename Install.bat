@echo off
setlocal enabledelayedexpansion
title AI Studio Audio Splitter Pro Max - Setup
color 0B

echo ==============================================================================
echo                AI Studio Audio Splitter Pro Max - Setup
echo ==============================================================================
echo.
echo  Is package me Python, PyTorch, Demucs AI, yt-dlp, Gradio aur FFmpeg
echo  sab kuch pre-bundled hai.
echo.
echo  Choose Installation Option:
echo  [1] Install to Local AppData (Standard Install - Recommended)
echo  [2] Run Portable in current folder (Only create Desktop Shortcut)
echo  [3] Exit
echo.
set /p choice="Enter option (1/2/3) [Default 1]: "
if "%choice%"=="" set choice=1

if "%choice%"=="3" goto :EOF

set "SOURCE_DIR=%~dp0"
set "SOURCE_DIR=%SOURCE_DIR:~0,-1%"

if "%choice%"=="2" (
    set "TARGET_DIR=%SOURCE_DIR%"
    goto :CREATE_SHORTCUTS
)

:: Option 1: Install to %LOCALAPPDATA%\AI-Audio-Splitter-Pro
set "TARGET_DIR=%LOCALAPPDATA%\AI-Audio-Splitter-Pro"
echo.
echo [1/3] Copying files to %TARGET_DIR% ...
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

robocopy "%SOURCE_DIR%" "%TARGET_DIR%" /E /XD "%SOURCE_DIR%\.git" "%SOURCE_DIR%\runtime" /XF "*.log" /R:1 /W:1 /NJH /NJS /NDL /NC /NS

:CREATE_SHORTCUTS
echo.
echo [2/3] Creating Desktop and Start Menu Shortcuts...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop); " ^
  "$startMenu = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs); " ^
  "$targetExe = Join-Path '%TARGET_DIR%' 'AudioSplitterPro.exe'; " ^
  "$iconPath = Join-Path '%TARGET_DIR%' 'app_icon.ico'; " ^
  "$wsh = New-Object -ComObject WScript.Shell; " ^
  "$scDesk = $wsh.CreateShortcut((Join-Path $desktop 'AI Audio Splitter Pro Max.lnk')); " ^
  "$scDesk.TargetPath = $targetExe; " ^
  "$scDesk.WorkingDirectory = '%TARGET_DIR%'; " ^
  "$scDesk.IconLocation = $iconPath + ',0'; " ^
  "$scDesk.Description = 'AI Studio Audio Splitter Pro Max'; " ^
  "$scDesk.Save(); " ^
  "$smFolder = Join-Path $startMenu 'AI Audio Splitter Pro'; " ^
  "if (-not (Test-Path $smFolder)) { New-Item -ItemType Directory -Path $smFolder -Force | Out-Null }; " ^
  "$scSm = $wsh.CreateShortcut((Join-Path $smFolder 'AI Audio Splitter Pro Max.lnk')); " ^
  "$scSm.TargetPath = $targetExe; " ^
  "$scSm.WorkingDirectory = '%TARGET_DIR%'; " ^
  "$scSm.IconLocation = $iconPath + ',0'; " ^
  "$scSm.Description = 'AI Studio Audio Splitter Pro Max'; " ^
  "$scSm.Save(); " ^
  "Write-Host 'Shortcuts created successfully!'"

echo.
echo [3/3] Installation Complete!
echo.
echo ==============================================================================
echo  Desktop par 'AI Audio Splitter Pro Max' shortcut ban gaya hai!
echo  Aap use double click karke kabhi bhi chala sakte hain.
echo ==============================================================================
echo.
set /p runNow="Kya aap abhi app chalana chahte hain? (Y/N) [Default Y]: "
if "%runNow%"=="" set runNow=Y
if /i "%runNow%"=="Y" (
    start "" "%TARGET_DIR%\AudioSplitterPro.exe"
)
exit /b 0
