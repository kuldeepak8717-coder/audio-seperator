@echo off
title AI Studio Audio Splitter Pro Max - Uninstall
color 0C

echo ==============================================================================
echo                AI Studio Audio Splitter Pro Max - Uninstall
echo ==============================================================================
echo.
set /p confirm="Kya aap sure hain ki app ko uninstall karna chahte hain? (Y/N): "
if /i not "%confirm%"=="Y" exit /b 0

echo.
echo Removing shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop); " ^
  "$deskLink = Join-Path $desktop 'AI Audio Splitter Pro Max.lnk'; " ^
  "if (Test-Path $deskLink) { Remove-Item $deskLink -Force }; " ^
  "$startMenu = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs); " ^
  "$smFolder = Join-Path $startMenu 'AI Audio Splitter Pro'; " ^
  "if (Test-Path $smFolder) { Remove-Item $smFolder -Recurse -Force }; " ^
  "Write-Host 'Shortcuts removed.'"

set "TARGET_DIR=%LOCALAPPDATA%\AI-Audio-Splitter-Pro"
if exist "%TARGET_DIR%" (
    echo Removing installed files from %TARGET_DIR% ...
    timeout /t 1 >nul
    rmdir /s /q "%TARGET_DIR%"
)

echo.
echo Uninstall completed successfully!
pause
exit /b 0
