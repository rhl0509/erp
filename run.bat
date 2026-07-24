@echo off
setlocal
cd /d "%~dp0"

REM Run the ERP app on SQLite (no MySQL needed).
REM Usage: double-click this file, or type  run.bat  in CMD.
REM Then open http://localhost:8040/  (login: admin / admin1234)

set "DATABASE_URL=sqlite:///./erp_dev.db"
set "JWT_SECRET=local-dev-only-secret-0123456789abcdef"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] venv not found: %PY%
    echo Create it first:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [1/2] Seeding initial data...
"%PY%" -m app.seed
if errorlevel 1 (
    echo [ERROR] Seed failed.
    pause
    exit /b 1
)

echo [2/2] Starting server at http://localhost:8040/
echo Login: admin / admin1234   ^(press Ctrl+C to stop^)
"%PY%" -m uvicorn app.main:app --reload --port 8040

pause
