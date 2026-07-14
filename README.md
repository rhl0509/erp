# ERP Foundation API (Windows)

ERP 공통 기반 + 거래처/품목 마스터 백엔드 (FastAPI + SQLAlchemy + MySQL).
이 문서는 **Windows OS 개발 환경** 기준입니다.

포함된 것:
- **웹 관리 콘솔(UI)** — Next.js 앱(`frontend/`). 로그인/대시보드/거래처·품목·재고·발주·수주·회계·총계정원장을 브라우저에서 사용 (실행법은 아래 참고)
- 인증·계정: 세션 쿠키(httpOnly JWT) + RBAC(역할/권한), 회원가입→관리자 승인/거절,
  비밀번호 정책·강제 변경, 2단계 인증(TOTP), 비밀번호 재설정(메일 또는 관리자 임시비번) — [로그인·계정 관리](#로그인계정-관리) 참고
- 채번 서비스 (행 잠금으로 동시성 안전)
- 감사 로그 (생성/수정/삭제 이력 + 변경 전후 값)
- 거래처(Partner) / 품목(Item) 마스터 CRUD
- 재고: 입출고 이력 + **창고별** 현재고(잔고 캐시) + 안전재고 미달 알림 + 창고간 이전
- 거래 문서: 발주(구매)·수주(판매) — 명세 + 상태전이(확정/부분입출고/완료/취소) + 재고 자동 연동 + 반품
- 원가·손익: 이동평균 원가(품목+창고) + 재고평가액 + 매출총이익(COGS)
- 부가세: 공급가/세액 분리 + 세금계산서 발행·취소
- 회계: 결제/수금(AP·AR) 기록 + 거래처 미지급·미수 잔액 + 여신한도
- 총계정원장(GL): 복식부기 자동 전기 — 분개장·계정별원장·시산표·간이 손익/재무상태표 + 재대사·재전기
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

→ 권한(18종), 역할(`admin` + `manager`/`staff`/`warehouse`/`viewer`), 관리자 계정(`admin` / `admin1234`), 샘플 거래처·품목, 계정과목(12종)이 생성됩니다.

> ⚠️ 기본 비밀번호 `admin1234` 는 **비밀번호 정책에 미달**하므로(10자 미만·아이디 포함),
> 첫 로그인 직후 변경 화면으로 이동하며 변경 전에는 업무 화면·API 가 막힙니다.
> 처음부터 다른 값을 쓰려면 시드 전에 `SEED_ADMIN_PASSWORD` 를 지정하세요.

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

이 서버는 **API 전용**입니다(레거시 단일 HTML 콘솔은 2026-07 제거, 웹 UI 는 Next.js 앱으로 이관).

- API 문서(Swagger): http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health

### 웹 관리 콘솔(Next.js 앱) 실행 ← 실제 화면

별도 터미널에서 `frontend/` 를 실행합니다(같은 origin 으로 `/api` → :8000 프록시).

```powershell
cd frontend
npm install    # 최초 1회
npm run dev     # http://localhost:3000
```

> 브라우저에서 **http://localhost:3000** 접속 후 `admin` / `admin1234` 로 로그인하면
> 대시보드·거래처·품목·재고·발주/수주·회계·총계정원장을 화면에서 사용할 수 있습니다.
> (백엔드 :8000 이 함께 떠 있어야 하며, 개발자용 API 는 :8000/docs Swagger 제공)

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
├─ app/                 백엔드 (API 전용)
│  ├─ config.py        설정 (.env)
│  ├─ database.py      엔진/세션/Base/get_db
│  ├─ security.py      비밀번호 해시·정책 + JWT(token_version) + 재설정 토큰
│  ├─ mailer.py        SMTP 메일 발송(선택 — 비밀번호 재설정 링크)
│  ├─ errors.py        필드 단위 오류(FieldError → {fields} 응답)
│  ├─ models.py        ORM 모델 (RBAC, 감사, 채번, 마스터, 문서, 재고, 결제, 세금계산서, GL)
│  ├─ schemas.py       Pydantic 스키마 (+ Page 페이지네이션, ErrorOut)
│  ├─ services.py      채번 + 감사 로그 + 재고/원가(post_movement·apply_stock_delta) + 부가세
│  ├─ gl.py            총계정원장 전기 서비스 (분개 생성·차대 검증·재전기)
│  ├─ deps.py          현재 사용자 / 권한 검사 의존성
│  ├─ seed.py          초기 데이터 시드 (권한·역할·계정과목)
│  ├─ main.py          앱 팩토리 + 라우터 + 오류 핸들러
│  └─ api\
│     ├─ auth.py       로그인(2FA)·회원가입·내 정보·비밀번호 변경/재설정·2FA 설정
│     ├─ users.py      사용자·역할 관리 + 승인/거절·임시비번 발급·2FA 해제
│     ├─ partners.py   거래처 CRUD (+ 매입/매출 내역, 미수·미지급 잔액)
│     ├─ items.py      품목 CRUD
│     ├─ stock.py      입출고 + 현재고/안전재고 알림
│     ├─ warehouses.py 창고 CRUD + 창고간 이전
│     ├─ purchase.py   발주(구매) 문서 + 입고 + 반품
│     ├─ sales.py      수주(판매) 문서 + 출고 + 반품 (여신한도 체크)
│     ├─ costing.py    재고평가·매출총이익(이동평균 원가)
│     ├─ payments.py   결제/수금(AR/AP)
│     ├─ invoices.py   세금계산서 발행·취소
│     ├─ gl.py         분개장·계정별원장·시산표·손익·재무상태·재대사·수기전표·재전기
│     └─ stats.py / reports.py / audit.py   통계·엑셀 리포트·감사 로그
├─ frontend/           웹 관리 콘솔 (Next.js App Router) ← 실제 화면
├─ migrations/         Alembic 마이그레이션 (env.py + versions/ 13개)
├─ scripts/            rebuild_gl.py (GL 전량 재전기)
├─ tests/              pytest (test_api / test_gl / test_warehouse / test_concurrency)
├─ docs/               gl-design.md (GL 설계·구현 결과) · nextjs-migration-plan.md
├─ alembic.ini
├─ requirements.txt
├─ .env.example
└─ README.md
```

## 로그인·계정 관리

### 가입 → 승인
공개 회원가입(`POST /api/auth/register`)은 **비활성·무권한**으로 계정을 만든다. 관리자가 회원 화면에서
역할을 부여하며 **승인**(`is_active=true`)해야 로그인할 수 있고, **거절**하면(`POST /api/users/{id}/reject`)
사유가 기록되어 로그인 시 본인에게 안내된다(계정을 삭제하지 않아 이력이 남고 같은 아이디 재가입도 막힌다).

### 비밀번호 정책
`PASSWORD_MIN_LENGTH`(기본 10) 이상 + 영문·숫자 포함, 공백·아이디 포함·흔한 비밀번호 금지.
규칙의 단일 소스는 서버(`app/security.py`)이며 화면은 `GET /api/auth/password-policy` 로 문구를 받아 쓴다.
정책에 미달하는 비밀번호를 가진 계정(임시비번 포함)은 `must_change_password` 가 켜져
**비밀번호를 바꿔야 업무 API 를 쓸 수 있다**(그 전에는 403 + `/change-password` 화면으로 유도).

### 세션 무효화
발급 JWT 에는 `token_version` 이 들어간다. 비밀번호 변경·재설정·임시비번 발급·계정 비활성화 시
서버가 이 값을 올려 **그 이전에 발급된 토큰(다른 기기 세션)을 즉시 무효화**한다.

### 비밀번호 재설정 (두 경로)
| 상황 | 흐름 |
|------|------|
| SMTP 설정됨(`SMTP_HOST`) | 비밀번호 찾기 → 등록 이메일로 1회용 재설정 링크(기본 30분) → `/reset-password` |
| SMTP 미설정(기본) | 비밀번호 찾기 → "관리자에게 요청" 안내 → 관리자가 회원 화면에서 **임시 비밀번호 발급**(1회 표시) → 사용자가 로그인 후 강제 변경 |

계정 존재 여부는 응답으로 알려주지 않는다(계정 열거 방지). 설정은 `.env.example` 의 SMTP 항목 참고.

### 2단계 인증(TOTP)
내 정보 화면에서 QR 을 스캔해 인증 앱(Google Authenticator 등)에 등록하고 6자리 코드로 확정한다.
켜지면 로그인 시 코드가 필요하다. 인증 앱을 분실하면 관리자가 `POST /api/users/{id}/2fa/disable` 로 풀어 준다.

### 무차별 대입 방어
로그인 실패 5회/5분(IP+아이디), 가입 10회/시간(IP), 비밀번호 찾기 5회/시간(IP) 초과 시 429.
인메모리 카운터라 다중 워커에서는 워커별로 계산된다(강한 보장이 필요하면 Redis 등으로 교체).

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
| `payment:read` / `payment:write` | 결제/수금·잔액 조회 / 등록·삭제 (**총계정원장 조회/기표도 이 권한**) |
| `invoice:read` / `invoice:write` | 세금계산서 조회 / 발행·취소 |

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

임시 SQLite로 매 테스트마다 스키마를 초기화·시드하며, 인증/권한(RBAC)·비밀번호 정책·세션 무효화·
2단계 인증·가입 승인/거절·비밀번호 재설정·부분수정·재고 잔고·발주/수주 문서 흐름·이동평균 원가·
부가세/세금계산서·반품·다중창고·총계정원장 전기/재대사·동시성을 검증합니다(총 107개).
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
