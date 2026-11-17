@echo off
setlocal EnableDelayedExpansion
rem Releases both Android apps (Relay Receipts + Relay Bank SMS) from this PC.
rem Needs the GitHub CLI signed in once:  gh auth login
rem Usage:  release.bat            next patch version (0.6.4 -> 0.6.5)
rem         release.bat 0.7.0      a version you choose
rem         release.bat 0.7.0 bank also release Relay Bank SMS (rarely needed)

set REPO=AhmedSayedRepo/Relay
set RELEASES=AhmedSayedRepo/relay-releases
set NOTES=android/app/src/main/java/com/relay/agent/presentation/WhatsNew.kt

where gh >nul 2>&1 || (echo GitHub CLI is not installed: https://cli.github.com & exit /b 1)
gh auth status >nul 2>&1 || (echo Run "gh auth login" first. & exit /b 1)

set VER=%~1
if "%VER%"=="" (
  for /f "delims=" %%t in ('gh release view --repo %RELEASES% --json tagName -q .tagName 2^>nul') do set LAST=%%t
  if "!LAST!"=="" (echo Could not find the last release. Run: release.bat 0.6.5 & exit /b 1)
  set LAST=!LAST:agent-v=!
  for /f "tokens=1-3 delims=." %%a in ("!LAST!") do set /a PATCH=%%c+1 & set VER=%%a.%%b.!PATCH!
  echo Last release: !LAST!
)

rem The "What's new" window shows only for versions that have notes.
gh api -H "Accept: application/vnd.github.raw" repos/%REPO%/contents/%NOTES% 2>nul | findstr /c:"\"%VER%\" to" >nul
if errorlevel 1 (
  echo WARNING: no release notes for %VER% in WhatsNew.kt - the app will not show "What's new".
)

set BANK=false
if /i "%~2"=="bank" set BANK=true
if "%BANK%"=="true" (echo Relay Bank SMS is included.) else (echo Relay Bank SMS is skipped - add "bank" after the version to include it.)
set /p OK=Release version %VER% now? (Y/N) 
if /i not "%OK%"=="Y" (echo Cancelled. & exit /b 0)

gh workflow run android-release.yml --repo %REPO% -f version=%VER% -f bank_sms=%BANK% || exit /b 1
echo Started. Waiting for GitHub to pick it up...
timeout /t 8 /nobreak >nul
for /f "delims=" %%i in ('gh run list --repo %REPO% --workflow android-release.yml -L 1 --json databaseId -q ".[0].databaseId"') do set RUN=%%i
gh run watch !RUN! --repo %REPO% --exit-status
if errorlevel 1 (
  echo Release failed. Details: https://github.com/%REPO%/actions/runs/!RUN!
  exit /b 1
)
echo.
echo Released %VER%. Phones will offer the update inside the apps.
echo https://github.com/%RELEASES%/releases/tag/agent-v%VER%
endlocal
