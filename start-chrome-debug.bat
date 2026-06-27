@echo off
REM ============================================================================
REM  Fanar Agent - launch a debuggable Chrome for the agent to drive (CDP).
REM
REM  WHY: Some portals (e.g. Tawtheeq / MOI E-Services) use reCAPTCHA Enterprise
REM  "score-based" - an INVISIBLE bot check with no puzzle to solve. It rejects
REM  freshly-automated browsers no matter how much we disguise them. The reliable
REM  fix is to let the agent drive YOUR OWN real Chrome instead.
REM
REM  HOW TO USE:
REM    1) Run this file (double-click), OR just run START.bat which launches it
REM       for you. A real Chrome window opens.
REM    2) ONE TIME: sign into a Google account in that window (this is what makes
REM       reCAPTCHA trust the browser). Keep the window open.
REM    3) Start the backend as usual. The agent AUTO-DETECTS this Chrome on
REM       port 9222 and drives it - Tawtheeq sign-in then passes reCAPTCHA. You
REM       can also watch it in the app's in-app Live Browser view.
REM
REM  NOTE: reCAPTCHA Enterprise (Tawtheeq) rejects headless / freshly-automated
REM  browsers, so driving a REAL Chrome like this is the only reliable way to
REM  pass it - there is no software "bypass".
REM
REM  Uses a SEPARATE profile (%LOCALAPPDATA%\fanar-chrome) so it never touches
REM  your everyday Chrome data.
REM ============================================================================
setlocal
set "PROFILE=%LOCALAPPDATA%\fanar-chrome"
set "PORT=9222"

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if "%CHROME%"=="" (
  echo.
  echo  ERROR: Could not find Chrome. Install Google Chrome, or edit this file
  echo  to point CHROME at your chrome.exe.
  echo.
  pause
  exit /b 1
)

REM  If a debuggable Chrome is already listening on %PORT%, don't open a second one.
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo  A debuggable Chrome is already running on port %PORT% - reusing it.
  endlocal
  exit /b 0
)

echo.
echo  Launching Chrome with remote debugging on port %PORT%
echo  Profile: %PROFILE%
echo.
echo   ^>^> SIGN INTO GOOGLE in the window that opens (one time), then leave it open.
echo   ^>^> The agent drives THIS Chrome so the Tawtheeq sign-in passes reCAPTCHA.
echo.
REM  Occlusion / backgrounding flags: keep the page RENDERING even when this window is minimized
REM  or not focused. Without them Windows occlusion detection suspends painting, so the agent's
REM  screenshots freeze/blank and the window visibly repaints ("refreshes") when focus changes.
REM  With them you can MINIMIZE this Chrome and keep using the app while the agent works.
start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check --disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling https://www.google.com
endlocal
