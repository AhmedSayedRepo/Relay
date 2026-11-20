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
rem "gh auth status" asks api.github.com, which times out on this network and
rem looked like being signed out. "gh auth token" only reads the saved sign-in.
gh auth token >nul 2>&1 || (echo Run "gh auth login" first. & exit /b 1)

set VER=%~1
if "%VER%"=="" (
  for /f "delims=" %%t in ('gh release view --repo %RELEASES% --json tagName -q .tagName 2^>nul') do set LAST=%%t
  if "!LAST!"=="" (echo Could not find the last release. Run: release.bat 0.6.5 & exit /b 1)
  set LAST=!LAST:agent-v=!
  for /f "tokens=1-3 delims=." %%a in ("!LAST!") do set /a PATCH=%%c+1 & set VER=%%a.%%b.!PATCH!
  echo Last release: !LAST!
)

rem The "What's new" window shows only for versions that have notes.
rem Read from this folder (after git pull), not from the GitHub API.
findstr /c:"\"%VER%\" to" "%~dp0%NOTES:/=\%" >nul 2>&1
if errorlevel 1 (
  echo WARNING: no release notes for %VER% in WhatsNew.kt - the app will not show "What's new".
)

set BANK=false
if /i "%~2"=="bank" set BANK=true
if "%BANK%"=="true" (echo Relay Bank SMS is included.) else (echo Relay Bank SMS is skipped - add "bank" after the version to include it.)
set /p OK=Release version %VER% now? (Y/N) 
if /i not "%OK%"=="Y" (echo Cancelled. & exit /b 0)

rem Start the build. api.github.com sometimes times out on this network, so:
rem try the API three times, then start it by pushing a tag with git, which
rem goes through github.com (the same way "git pull" works).
set STARTED=
for /l %%n in (1,1,3) do if not defined STARTED (
  gh api -X POST repos/%REPO%/actions/workflows/android-release.yml/dispatches -f ref=main -f "inputs[version]=%VER%" -f "inputs[bank_sms]=%BANK%" >nul 2>&1 && set STARTED=api
  if not defined STARTED (echo GitHub API did not answer, try %%n of 3... & timeout /t 5 /nobreak >nul)
)
if not defined STARTED (
  rem A tag ending in -bank also builds Relay Bank SMS.
  set TAG=agent-v%VER%
  if "%BANK%"=="true" set TAG=agent-v%VER%-bank
  echo Starting the release with a git tag instead: !TAG!
  git fetch -q origin main || (echo Could not reach GitHub at all. Check the internet. & exit /b 1)
  git tag !TAG! origin/main || exit /b 1
  git push -q origin !TAG! || (git tag -d !TAG! >nul & echo Could not push the tag. & exit /b 1)
  set STARTED=tag
)
echo Started. Waiting for GitHub to pick it up...
timeout /t 8 /nobreak >nul
set RUN=
for /f "delims=" %%i in ('gh run list --repo %REPO% --workflow android-release.yml -L 1 --json databaseId -q ".[0].databaseId" 2^>nul') do set RUN=%%i
if "!RUN!"=="" (
  echo The build is running on GitHub; this PC cannot follow it right now.
  echo Watch it here: https://github.com/%REPO%/actions/workflows/android-release.yml
  echo When it is green, phones will offer the update inside the apps.
  exit /b 0
)
gh run watch !RUN! --repo %REPO% --exit-status
if errorlevel 1 (
  echo Release failed. Details: https://github.com/%REPO%/actions/runs/!RUN!
  exit /b 1
)
echo.
echo Released %VER%. Phones will offer the update inside the apps.
echo https://github.com/%RELEASES%/releases/tag/agent-v%VER%
endlocal
