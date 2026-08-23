# Build the frontend (if needed) and run the TravelIP backend, which serves
# the built frontend as static files. Binds to 127.0.0.1 only -- this app
# runs 100% locally, never expose it beyond loopback.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $root "frontend/dist/index.html"))) {
    Write-Host "Building frontend..."
    Push-Location (Join-Path $root "frontend")
    npm install
    npm run build
    Pop-Location
}

Push-Location $root
& "$root/.venv/Scripts/python.exe" -m backend.main
Pop-Location
