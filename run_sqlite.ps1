# MySQL 설치 없이 SQLite로 바로 확인용 실행 스크립트.
# 사용:  .\run_sqlite.ps1
# 그 다음 브라우저에서 http://localhost:8040/ 접속 -> admin / admin1234
#
# (이 파일은 한글 주석이 깨지지 않도록 반드시 'UTF-8 with BOM'으로 저장하세요.)

$ErrorActionPreference = "Stop"

# 스크립트 위치를 기준으로 동작 (어느 폴더에서 실행해도 안전)
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

$env:DATABASE_URL = "sqlite:///./erp_dev.db"
$env:JWT_SECRET   = "local-dev-only-secret-0123456789abcdef"

# 가상환경 파이썬
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[오류] 가상환경을 찾을 수 없습니다: $py" -ForegroundColor Red
    Write-Host "먼저 아래를 실행하세요:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

Write-Host "[1/2] 초기 데이터 시드..." -ForegroundColor Cyan
& $py -m app.seed
if ($LASTEXITCODE -ne 0) { Write-Host "[오류] 시드 실패 (종료코드 $LASTEXITCODE)" -ForegroundColor Red; exit 1 }

Write-Host "[2/2] 서버 실행 (http://localhost:8040/)" -ForegroundColor Cyan
& $py -m uvicorn app.main:app --reload --port 8040
