# Start the full Fanar Agent desktop stack: backend + frontend + Electron shell.
# Usage:  ./scripts/run_desktop.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

Write-Host "→ Starting backend (FastAPI, port 8008)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-File", "$PSScriptRoot/run_backend.ps1"

Write-Host "→ Starting frontend (Next.js, port 3000)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-File", "$PSScriptRoot/run_frontend.ps1"

Write-Host "→ Waiting for the UI on http://localhost:3000 ..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        Invoke-WebRequest -Uri "http://localhost:3000" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ready = $true; break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { Write-Warning "UI did not come up in time; launching Electron anyway." }

Set-Location "$root/desktop"
# VS Code's integrated terminal sets ELECTRON_RUN_AS_NODE=1, which makes electron.exe
# run as plain Node and crash ("app is undefined"). Clear it before launching.
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
if (-not (Test-Path "node_modules")) {
    Write-Host "→ Installing Electron..." -ForegroundColor Cyan
    npm install
}
if (-not (Test-Path "node_modules/electron/dist/electron.exe")) {
    Write-Host "→ Repairing Electron binary..." -ForegroundColor Yellow
    node node_modules/electron/install.js
}
Write-Host "→ Launching Fanar Agent desktop app..." -ForegroundColor Green
npm start
