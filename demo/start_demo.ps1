# Starts the separate AttendAI DEMO instance (fictional, labelled data) on this computer only:
#   backend  http://127.0.0.1:8001   (database: demo\attendai_demo.db, never backend\data\attendai.db)
#   frontend http://127.0.0.1:3001   (shows a DEMO banner on every page)
# The real AttendAI (127.0.0.1:3000 / 127.0.0.1:8000) is not touched.
#
# First time (or to rebuild the fictional data):
#   cd backend
#   .\.venv\Scripts\python.exe scripts\seed_demo.py --db ..\demo\attendai_demo.db --credentials ..\private\demo_credentials.txt --replace
# Then run this script from the attendai folder:  powershell -ExecutionPolicy Bypass -File demo\start_demo.ps1
# Demo sign-in names and passwords are in private\demo_credentials.txt.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$demoDb = Join-Path $PSScriptRoot 'attendai_demo.db'
if (-not (Test-Path $demoDb)) { throw "Demo database missing: run scripts\seed_demo.py first (see the top of this file)" }
foreach ($port in 8001, 3001) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port is already in use; the demo may already be running" }
}

# Environment variables override backend\.env for these processes only
$env:ENVIRONMENT = 'production'
$env:DATABASE_URL = 'sqlite:///' + ($demoDb -replace '\\', '/')
$env:SECRET_KEY = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 62 | ForEach-Object { [char]$_ })  # new each run
$env:DEBUG = 'False'
$env:ALLOW_SELF_REGISTRATION = 'False'
$env:ENABLE_SYNTHETIC_CAMERA = 'False'
$env:CORS_ORIGINS = 'http://127.0.0.1:3001,http://localhost:3001'
$api = Start-Process -FilePath (Join-Path $backend '.venv\Scripts\python.exe') -WorkingDirectory $backend -WindowStyle Hidden -PassThru `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8001'
foreach ($name in 'ENVIRONMENT', 'DATABASE_URL', 'SECRET_KEY', 'DEBUG', 'ALLOW_SELF_REGISTRATION', 'ENABLE_SYNTHETIC_CAMERA', 'CORS_ORIGINS') {
    Remove-Item "Env:$name"
}

$env:PORT = '3001'; $env:HOST = '127.0.0.1'; $env:BROWSER = 'none'
$env:REACT_APP_API_URL = 'http://127.0.0.1:8001'; $env:REACT_APP_DEMO_MODE = 'true'
$web = Start-Process -FilePath 'npm.cmd' -ArgumentList 'start' -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -PassThru
foreach ($name in 'PORT', 'HOST', 'BROWSER', 'REACT_APP_API_URL', 'REACT_APP_DEMO_MODE') { Remove-Item "Env:$name" }

"Demo backend PID $($api.Id), demo frontend PID $($web.Id)" | Set-Content (Join-Path $PSScriptRoot 'demo_pids.txt')
"DEMO starting: open http://127.0.0.1:3001 in about a minute. Stop it with:  taskkill /T /F /PID $($api.Id); taskkill /T /F /PID $($web.Id)"
