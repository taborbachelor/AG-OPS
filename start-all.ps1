# One-command launcher: SITL + backend + GCS + customer site, each in its own window.
# Usage: right-click > Run with PowerShell, or from a terminal: .\start-all.ps1
# Add -NoBrowser to skip auto-opening the GCS in your default browser.

param(
    [switch]$NoBrowser
)

$root = $PSScriptRoot

Write-Host "Starting SITL simulator..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\sitl'; .\run_sitl.bat"
Start-Sleep -Seconds 3

Write-Host "Starting backend (FastAPI :8000)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\venv\Scripts\Activate.ps1; uvicorn app.main:app --host 0.0.0.0 --port 8000"

Write-Host "Starting GCS frontend (:3000)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm start"

Write-Host "Starting customer site (:3001)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\web'; npm run dev"

Write-Host ""
Write-Host "All four processes launching in separate windows." -ForegroundColor Green
Write-Host "Give SITL ~30s to get a GPS fix before connecting from the GCS."
Write-Host ""
Write-Host "  GCS (operator):    http://localhost:3000"
Write-Host "  Customer site:     http://localhost:3001"
Write-Host "  Backend API docs:  http://localhost:8000/docs"
Write-Host ""
Write-Host "In the GCS: link chip (top-left) -> Quick Connect -> Simulator."
Write-Host "Flagship demo: SPRAY -> Area -> box farmland near Sabetha, KS -> Detect fields ->"
Write-Host "Generate Spray Plan -> Upload -> FLY -> ARM & TAKEOFF."
Write-Host ""
Write-Host "Close the four spawned windows to stop everything (SITL exits on backend disconnect)."

if (-not $NoBrowser) {
    Start-Sleep -Seconds 5
    Start-Process "http://localhost:3000"
}
