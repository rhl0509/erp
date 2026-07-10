# ERP Foundation API (Windows)

ERP 공통 기반 + 거래처/품목 마스터 백엔드 (FastAPI + SQLAlchemy + MySQL).
이 문서는 **Windows OS 개발 환경** 기준입니다.

포함된 것:
- **웹 관리 콘솔(UI)** — 로그인/대시보드/거래처·품목 CRUD를 브라우저에서 바로 사용 (별도 빌드 불필요)
- 인증(JWT) + RBAC(역할/권한)
- 채번 서비스 (행 잠금으로 동시성 안전)
- 감사 로그 (생성/수정/삭제 이력 + 변경 전후 값)
- 거래처(Partner) / 품목(Item) 마스터 CRUD
- 재고: 입출고 이력 + 품목별 현재고(잔고 캐시) + 안전재고 미달 알림
- 거래 문서: 발주(구매)·수주(판매) — 명세 + 상태전이(확정/부분입출고/완료/취소) + 재고 자동 연동
- 원가·손익: 이동평균 원가 + 재고평가액 + 매출총이익(COGS)
- 회계: 결제/수금(AP·AR) 기록 + 거래처 미지급·미수 잔액
- 목록 API 페이지네이션(`items/total/page/page_size/pages`) + 검색·유형 필터
- 일관된 오류 응답(`{detail, code, fields}`) — 입력값 오류 시 필드별 한글 메시지

## 화면 미리보기

모노크롬 디자인 시스템을 적용한 웹 관리 콘솔입니다. (`admin` / `admin1234`)

| 로그인 | 대시보드 |
|--------|----------|
| ![로그인](docs/screenshots/01-login.png) | ![대시보드](docs/screenshots/02-dashboard.png) |

| 거래처 관리 | 품목 등록 모달 |
|-------------|----------------|
| ![거래처 관리](docs/screenshots/03-partners.png) | ![품목 등록 모달](docs/screenshots/04-item-modal.png) |

## 사전 준비

- Python 3.10 이상 설치 (설치 시 **"Add python.exe to PATH"** 체크)
- MySQL 설치 후 DB 생성 (utf8mb4 권장):

```sql
CREATE DATABASE erp_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> 아래 명령은 기본적으로 **PowerShell** 기준입니다. CMD를 쓰면 별도 표기한 곳을 참고하세요.

## 1. 가상환경 생성 및 활성화

```powershell
cd erp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

- CMD를 쓰는 경우: `.\.venv\Scripts\activate.bat`
- PowerShell에서 `이 시스템에서 스크립트를 실행할 수 없으므로...` 오류가 나면, 현재 창에서만 정책을 풀어줍니다:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

활성화되면 프롬프트 앞에 `(.venv)` 가 붙습니다.

## 2. 패키지 설치

```powershell
pip install -r requirements.txt
```

## 3. 환경 변수 설정

`.env.example` 을 복사해 `.env` 로 만들고 DB 접속 정보와 JWT_SECRET 을 채웁니다.

```powershell
copy .env.example .env
```

- CMD도 동일하게 `copy .env.example .env`
- 메모장으로 열어 편집: `notepad .env`

`.env` 예시:

```
DATABASE_URL=mysql+pymysql://root:내비밀번호@localhost:3306/erp_db?charset=utf8mb4
JWT_SECRET=길고-무작위한-문자열로-반드시-변경
ACCESS_TOKEN_EXPIRE_MINUTES=480
CORS_ORIGINS=http://localhost:3000
```

## 4. 초기 데이터 시드

```powershell
python -m app.seed
```

→ 권한(16종), 역할(`admin` + `manager`/`staff`/`warehouse`/`viewer`), 관리자 계정(`admin` / `admin1234`), 샘플 거래처·품목이 생성됩니다.

## 5. 서버 실행

**가장 간단한 방법 (MySQL 없이 SQLite로 바로 실행):**

```powershell
.\run_sqlite.ps1   # PowerShell
```
```cmd
run.bat            :: CMD 또는 탐색기에서 더블클릭
```

→ 시드 + 서버 기동을 한 번에. 둘 다 SQLite(`erp_dev.db`)를 사용하며 환경변수를 자동 설정합니다.

**직접 실행 (MySQL 등 `.env` 설정을 쓸 때):**

먼저 스키마 마이그레이션을 적용합니다 (최초 1회 및 모델 변경 시). MySQL 등 운영 DB는
기동 시 테이블을 자동 생성하지 않으므로, 시드/실행 전에 반드시 먼저 실행해야 합니다:

```powershell
alembic upgrade head
```

그 다음 서버를 실행합니다:

```powershell
uvicorn app.main:app --reload --port 8000
```

- **웹 관리 콘솔: http://localhost:8000/** ← 가장 추천
- API 문서(Swagger): http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health

> **가장 쉬운 사용법은 웹 콘솔입니다.** http://localhost:8000/ 에 접속해
> `admin` / `admin1234` 로 로그인하면, 거래처·품목을 표·검색·페이지네이션이 있는
> 화면에서 등록/수정/삭제할 수 있습니다. (개발자용으로는 `/docs` Swagger도 그대로 제공)

## 6. 명령줄에서 테스트 (curl)

Windows 10/11에는 `curl.exe` 가 기본 포함되어 있습니다.
단, **PowerShell에서 `curl` 은 다른 명령(Invoke-WebRequest)의 별칭**이므로,
반드시 `curl.exe` 라고 정확히 입력해야 합니다.

### PowerShell

로그인 (토큰 발급):

```powershell
curl.exe -X POST http://localhost:8000/api/auth/login -d "username=admin&password=admin1234"
```

응답의 `access_token` 값을 변수에 넣고 거래처 등록:

```powershell
$TOKEN = "여기에_받은_access_token_붙여넣기"

curl.exe -X POST http://localhost:8000/api/partners -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{\"name\":\"가나다물산\",\"partner_type\":\"customer\",\"ceo_name\":\"김철수\"}'

curl.exe http://localhost:8000/api/partners -H "Authorization: Bearer $TOKEN"
```

### CMD

```cmd
curl -X POST http://localhost:8000/api/auth/login -d "username=admin&password=admin1234"

set TOKEN=여기에_받은_access_token_붙여넣기

curl -X POST http://localhost:8000/api/partners -H "Authorization: Bearer %TOKEN%" -H "Content-Type: application/json" -d "{\"name\":\"가나다물산\",\"partner_type\":\"customer\",\"ceo_name\":\"김철수\"}"

curl http://localhost:8000/api/partners -H "Authorization: Bearer %TOKEN%"
```

> JSON 안의 따옴표 이스케이프(`\"`)가 번거로우면 Swagger UI 사용을 권장합니다.

## 디렉터리 구조

```
erp/
├─ app/
│  ├─ config.py        설정 (.env)
│  ├─ database.py      엔진/세션/Base/get_db
│  ├─ security.py      비밀번호 해시 + JWT
│  ├─ models.py        ORM 모델 (RBAC, 감사, 채번, 마스터)
│  ├─ schemas.py       Pydantic 스키마 (+ Page 페이지네이션, ErrorOut)
│  ├─ services.py      채번 + 감사 로그 + 페이지네이션 헬퍼
│  ├─ deps.py          현재 사용자 / 권한 검사 의존성
│  ├─ seed.py          초기 데이터 시드
│  ├─ main.py          앱 팩토리 + 라우터 + 오류 핸들러 + UI 서빙
│  ├─ static\
│  │  └─ index.html    웹 관리 콘솔(단일 파일, 빌드 불필요)
│  └─ api\
│     ├─ auth.py       로그인 / 내 정보(권한 포함)
│     ├─ users.py      사용자·역할 관리
│     ├─ partners.py   거래처 CRUD (+ 매입/매출 내역)
│     ├─ items.py      품목 CRUD
│     ├─ stock.py      입출고 + 현재고/안전재고 알림
│     ├─ purchase.py   발주(구매) 문서 + 입고
│     ├─ sales.py      수주(판매) 문서 + 출고
│     ├─ costing.py    재고평가·매출총이익(이동평균 원가)
│     ├─ payments.py   결제/수금(AR/AP) + 거래처 잔액
│     └─ stats.py / reports.py / audit.py   통계·엑셀 리포트·감사 로그
├─ migrations/         Alembic 마이그레이션 (env.py + versions/)
├─ alembic.ini
├─ requirements.txt
├─ .env.example
└─ README.md
```

## 권한 코드

| 코드 | 설명 |
|------|------|
| `*` | 전체 권한 (admin) |
| `user:read` / `user:write` | 사용자 조회 / 등록·수정 |
| `audit:read` | 감사 로그 조회 |
| `partner:read` / `partner:write` | 거래처 조회 / 등록·수정·삭제 |
| `item:read` / `item:write` | 품목 조회 / 등록·수정·삭제 |
| `stock:read` / `stock:write` | 재고 조회 / 입출고 등록·삭제 |
| `purchase:read` / `purchase:write` | 발주 조회 / 등록·확정·입고·취소 |
| `sales:read` / `sales:write` | 수주 조회 / 등록·확정·출고·취소 |
| `payment:read` / `payment:write` | 결제/수금·잔액 조회 / 등록·삭제 |

## 응답 형식 (클라이언트 연동용)

목록 조회는 페이지네이션 형식으로 반환됩니다:

```json
{ "items": [ ... ], "total": 120, "page": 1, "page_size": 20, "pages": 6 }
```

- 쿼리 파라미터: `page`(1부터), `page_size`(최대 100), `q`(이름/코드 검색),
  거래처는 `partner_type`, 품목은 `item_type` 필터 지원.

오류는 항상 동일한 형식입니다:

```json
{ "detail": "입력값을 다시 확인해 주세요.", "code": "validation_error",
  "fields": { "name": "최소 1자 이상 입력해 주세요." } }
```

- `code`: `unauthorized` / `forbidden` / `not_found` / `conflict` / `validation_error` 등
- `fields`: 입력값 오류일 때만 포함(필드명 → 한글 메시지). 화면에서 입력칸 옆에 바로 표시 가능.

`/api/auth/me` 는 역할과 함께 평탄한 `permissions` 배열을 돌려주므로, 화면에서 버튼 노출을 권한에 따라 제어할 수 있습니다.

## Docker 로 실행

MySQL + API 를 한 번에 띄웁니다 (로컬에 Docker 필요):

```powershell
$env:JWT_SECRET = "길고-무작위한-값"   # 필수: 미지정이면 compose 가 기동을 거부함
docker compose up --build
```

→ DB 헬스체크 통과 후 API 컨테이너가 `alembic upgrade head` → 시드 → uvicorn 순으로 실행합니다.
접속: http://localhost:8000/

## 테스트

```powershell
pip install -r requirements-dev.txt
pytest
```

임시 SQLite로 매 테스트마다 스키마를 초기화·시드하며, 인증/권한(RBAC)·부분수정·재고 잔고·발주/수주 문서 흐름·이동평균 원가를 검증합니다.
`.github/workflows/ci.yml` 로 push/PR 마다 GitHub Actions에서 자동 실행됩니다.

## 자주 막히는 부분 (Windows)

- **`python` 입력 시 Microsoft Store가 열림**: 설치 시 PATH 등록이 안 된 경우입니다.
  Python을 다시 설치하며 "Add to PATH"를 체크하거나, `py -m venv .venv` / `py -m app.seed` 처럼 `py` 런처를 사용하세요.
- **`Activate.ps1` 실행 거부**: 위 1번의 `Set-ExecutionPolicy -Scope Process` 명령으로 해결.
- **`curl` 결과가 이상함**: PowerShell의 `curl` 별칭 때문입니다. `curl.exe` 로 입력하세요.
- **MySQL 연결 오류(2059 등)**: 비밀번호 플러그인 문제일 수 있습니다. 의존성에 포함된 `cryptography` 가 처리하지만,
  계속 실패하면 MySQL 사용자를 `mysql_native_password` 방식으로 재설정해 보세요.

## 스키마 마이그레이션 (Alembic)

스키마는 **Alembic** 으로 관리합니다. SQLite 로컬 개발에서는 편의상 기동 시 자동 생성되지만,
MySQL 등 운영 DB는 자동 생성하지 않으므로 마이그레이션을 사용합니다.

```powershell
alembic upgrade head                      # 최신 스키마 적용
alembic revision --autogenerate -m "설명"  # 모델 변경 후 새 마이그레이션 생성
alembic downgrade -1                       # 한 단계 되돌리기
alembic current / history                  # 현재 리비전 / 이력
```

접속 URL·메타데이터는 `migrations/env.py` 가 앱 설정(`.env`)에서 직접 읽으므로 `alembic.ini` 에 중복 기입하지 않습니다.

## 운영 전 체크리스트

- 운영 DB는 실행/시드 전에 반드시 `alembic upgrade head` 로 스키마를 먼저 만들 것 (자동 생성 안 함)
- `JWT_SECRET` 는 반드시 16자 이상 무작위 값으로 설정 — 기본/약한 값이면 **기동이 거부**됩니다
- 채번·재고 출고는 행 잠금(`SELECT ... FOR UPDATE`)으로 동시성을 보장하지만, MySQL 격리수준/인덱스 점검 권장
