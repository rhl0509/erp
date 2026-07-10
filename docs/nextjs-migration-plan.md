# Next.js 전환 아키텍처 & 점진 마이그레이션 계획

- 대상: `app/static/index.html`(~2,500줄 단일 HTML SPA) → Next.js 프론트엔드
- 전제: FastAPI 백엔드(`app/`)는 그대로 유지. 재작성 대상은 프론트만.
- 원칙: 빅뱅 금지 — 스트랭글러(strangler) 패턴으로 페이지 단위 수직 슬라이스 이전.
- 작성일: 2026-07-10 (버전 표기는 스캐폴딩 시점에 최신 안정판 재확인 필요)

---

## 0. 현황 요약 (설계 근거)

| 항목 | 현재 상태 | 근거 |
|---|---|---|
| 프론트 서빙 | FastAPI가 `/`에서 index.html, `/static` 정적 서빙 | `app/main.py:148-157` |
| API 표면 | `/api/*` 14개 라우터 (auth, users, partners, items, stock, warehouses, purchase, sales, costing, payments, invoices, stats, reports, audit) | `app/main.py:118-131` |
| 인증 | form-encoded `POST /api/auth/login` → `{access_token}`, 이후 `Authorization: Bearer` | `app/api/auth.py:83`, `app/deps.py:10` |
| 토큰 보관 | `localStorage`(`erp_token`) | `app/static/index.html:874-880` |
| 권한 게이팅 | `GET /api/auth/me` → `ME.permissions` 배열(`*` 와일드카드), `data-perm` 속성으로 nav/버튼 숨김 | `app/static/index.html:883,991`, `app/deps.py:43-52` |
| 오류 계약 | 모든 오류 `{detail, code, request_id, fields?}` 통일 (422는 `fields`에 한글 필드별 메시지) | `app/main.py:59-98` |
| 목록 계약 | `Page[T] = {items, total, page, page_size, pages}` | `app/schemas.py:10-16` |
| 페이지(13+2) | 대시보드/거래처/품목/재고/창고/발주/수주/매입매출/재고평가/결제/채권채무/회원/감사로그 + 내정보 + 로그인·회원가입 | `app/static/index.html:276-288` |
| 공통 헬퍼 | `api()` fetch 래퍼(401→강제 로그아웃), `downloadFile()`(엑셀 blob), `toast`, `won`, `esc`, 페이지네이션 | `app/static/index.html:894-930` |
| nav 배지 | 재고 알림 수(`/api/stock/alerts/count`), 승인 대기 회원 수(`/api/users/pending/count`) 폴링 | `app/static/index.html:279,287,993-994` |
| CORS | `http://localhost:3000` 기본 허용 — Next dev 기본 포트와 이미 정합 | `app/config.py:28` |
| 라우팅 | hash 기반(`#partners`), 새로고침 딥링크는 일부 페이지만 복원됨(purchase/sales/valuation/payments/aging 누락) | `app/static/index.html:995-996` |

특기: 백엔드가 이미 잘 정리된 API 계약(오류/페이지네이션 통일, OpenAPI 자동 생성)을 갖고 있어 프론트 전환 난이도는 "화면 이식"에 수렴한다. 백엔드 변경은 원칙적으로 0건.

---

## 1. 목표 레포 레이아웃

### 권장: 모노레포 — `frontend/` 서브디렉터리

```
D:\erp\
├─ app/                  # FastAPI (변경 없음)
│  └─ static/index.html  # 레거시 SPA — 이전 기간 공존, 완료 후 삭제
├─ frontend/             # Next.js (신규)
│  ├─ src/
│  │  ├─ app/                     # App Router
│  │  │  ├─ layout.tsx            # 루트: 폰트·globals.css·Providers
│  │  │  ├─ globals.css           # 디자인 토큰(CSS 변수) 이식
│  │  │  ├─ login/page.tsx        # 로그인·회원가입 (비보호)
│  │  │  └─ (app)/                # 인증 필요 구간 (route group)
│  │  │     ├─ layout.tsx         # 앱 셸: topbar+nav+권한 게이팅+인증 가드
│  │  │     ├─ dashboard/page.tsx
│  │  │     ├─ partners/page.tsx  … (도메인별 1 라우트)
│  │  ├─ components/ui/           # 디자인 시스템 컴포넌트(§5)
│  │  ├─ features/<domain>/       # 도메인별 훅·테이블·모달 폼
│  │  └─ lib/
│  │     ├─ api/                  # 생성된 타입 + 클라이언트 + 오류 처리
│  │     └─ auth/                 # AuthProvider, usePermission, 가드
│  ├─ next.config.ts              # rewrites(§1.3)
│  └─ package.json
├─ docs/
└─ alembic/ …
```

| | 모노레포(`frontend/`) — 권장 | 별도 레포 |
|---|---|---|
| API 계약 동기화 | OpenAPI 스키마 생성이 같은 트리에서 원자적(백엔드 수정+타입 재생성+프론트 수정이 한 커밋) | 스키마 버전 조율·배포 순서 문제 발생 |
| 운영 공수 | CI/이슈/배포 하나 | 레포 2개 관리, 내부 ERP 규모에 과함 |
| 단점 | Python/Node 툴체인 혼재(무시 가능 — `frontend/`로 격리) | 팀 분리·독립 배포가 필요할 때만 이점 |

단일 팀 내부 ERP + 프론트-백 강결합(타입 생성 파이프라인)이므로 모노레포가 명백히 유리. 별도 레포는 프론트 전담 조직이 생길 때 재검토.

### 1.2 dev 구성

- FastAPI: `uvicorn app.main:app --port 8000` (기존 그대로)
- Next dev: `:3000`, `next.config.ts`의 `rewrites`로 프록시:
  - `/api/:path*` → `http://127.0.0.1:8000/api/:path*`
  - `/legacy` → `http://127.0.0.1:8000/` (공존 기간, §6.2)
  - `/static/:path*` → `http://127.0.0.1:8000/static/:path*` (레거시 자산)
- rewrites는 same-origin이므로 **CORS 무관** — `cors_origins` 설정 변경 불필요. 프론트 코드도 상대경로 `/api/...` 그대로 사용(현 `api()` 헬퍼와 동일 관행).

### 1.3 prod 구성 — 권장: Next 서버(standalone) + 리버스 프록시

```
브라우저 ── nginx/Caddy(:80/:443)
              ├─ /api/*, /docs, /metrics, /health → uvicorn(:8000)
              └─ 그 외 전부                      → next start(:3000)
```

- `output: "standalone"` 빌드 → `node server.js`로 경량 실행. 리버스 프록시가 없으면 Next rewrites가 프록시 역할을 대신해도 됨(단일 진입점 = Next).
- 대안: `output: "export"` 정적 export를 FastAPI `StaticFiles`로 서빙 — Node 런타임이 없어지는 장점이 있으나, 동적 라우트 제약·향후 httpOnly 쿠키 전환(§4) 봉쇄·이미지 최적화 불가. **토큰 저장을 localStorage로 확정할 때만** 고려할 차선(오픈 결정 §7-2, §7-4).

---

## 2. 기술 선택 (2026 기준, 이 규모에 맞게)

| 영역 | 권장 | 근거 / 기각한 대안 |
|---|---|---|
| 프레임워크 | **Next.js App Router(최신 안정, 16.x 예상 — 스캐폴딩 시 확인) + TypeScript + React 19** | 사용자 확정. Pages Router는 신규 채택 이유 없음 |
| 스타일 | **globals.css에 현 CSS 변수 토큰 이식 + CSS Modules** | 현 디자인 시스템이 이미 완성된 ~220줄 토큰 기반 CSS(`index.html:17-219`) — 1:1 이식이 최저 비용·최고 충실도. Tailwind v4는 전 클래스 재작성 churn 대비 이득 없음(단일 디자인, 다크모드 없음). 오픈 결정 §7-3 |
| 데이터 페칭 | **클라이언트 컴포넌트 + TanStack Query v5** | 근거: (1) 모든 데이터가 인증 뒤 사용자별 — SEO/공개 페이지 없음 (2) 공존 기간 토큰이 localStorage라 서버 컴포넌트에서 읽을 수 없음 (3) 검색·정렬·페이지네이션·모달 CRUD 등 상호작용 중심 테이블 — 클라이언트 캐시+무효화(mutation 후 invalidate)가 현재 손수 만든 reload 패턴을 그대로 대체. Server Components는 레이아웃·정적 셸에만 사용 |
| Route Handlers(BFF) | **초기 미사용** | 토큰을 httpOnly 쿠키로 옮기는 결정(§7-2)이 나면 로그인/프록시 핸들러 2개만 추가 |
| 서버상태 캐시 | TanStack Query (Next 내장 `use cache`/fetch 캐시는 사용 안 함) | Next 캐시는 서버 페칭 전제 — 본 구조와 불일치. 이중 캐시 계층은 과설계 |
| 폼/검증 | **react-hook-form**, 서버 422 `fields`를 필드 에러로 매핑 | 서버가 이미 한글 필드별 메시지를 내려줌(`app/main.py:71-83`) — zod 클라이언트 스키마 중복은 초기엔 생략, 필요해지면 추가 |
| 전역 상태 | 없음 (ME는 Query + Context) | Redux/Zustand 불필요 — 서버 상태 외 전역 상태가 사실상 없음 |
| 테이블 | 자체 `<DataTable>` (정렬 헤더·페이저 포함) | TanStack Table은 현 테이블 복잡도(정렬+페이지네이션이 전부, 서버사이드) 대비 과함 |
| 토스트/모달 | 자체 소형 구현(현 CSS 이식) | 라이브러리(sonner/radix) 도입보다 기존 스타일 충실 이식이 저렴. 접근성 보강 필요 시 Radix Dialog만 선별 도입 |

**과설계 경계(하지 않을 것):** i18n 프레임워크(한국어 단일), 다크모드, Storybook, GraphQL/tRPC, 마이크로프론트엔드, 상태관리 라이브러리, E2E 자동화 전면 도입(핵심 흐름 소수만 선택적).

---

## 3. API 타입 안전 파이프라인

수기 fetch 래퍼 + 손 타이핑 대신 **OpenAPI 스키마에서 타입 자동 생성**:

```
FastAPI(/openapi.json) ──(openapi-typescript)──> frontend/src/lib/api/schema.d.ts
                                                      │
                                          openapi-fetch 클라이언트가 소비
```

- **openapi-typescript**(devDep): `npm run gen:api` = `openapi-typescript http://127.0.0.1:8000/openapi.json -o src/lib/api/schema.d.ts`. 서버 기동 없이도 생성 가능: `python -c "import json; from app.main import app; print(json.dumps(app.openapi()))" > openapi.json` 스크립트 병용 권장(CI 친화).
- **openapi-fetch**(runtime, ~2KB): 경로·메서드·요청/응답이 전부 타입 체크되는 `client.GET("/api/partners", {params:{query:{...}}})`. 코드젠 산출물이 타입 파일 1개뿐이라 유지비 최소.
- 대안 비교: hey-api/orval(TanStack Query 훅까지 생성 — 편하지만 생성 코드량·설정 증가, 이 규모엔 openapi-fetch + 얇은 수제 훅이 단순), 수기 래퍼(타입 드리프트 방지 불가 — 기각).
- 드리프트 방지: CI에서 `gen:api` 재실행 후 `git diff --exit-code`로 스키마-타입 불일치 검출.
- 공통 오류 계약은 생성 타입과 별도로 1곳에 고정:

```ts
// lib/api/errors.ts (개념)
type ApiError = { detail: string; code: string; request_id: string; fields?: Record<string,string> };
```

`api()` 헬퍼의 책임(401→로그아웃, detail→토스트, fields→폼 에러, 204→null, blob 다운로드)은 `lib/api/client.ts`의 미들웨어/헬퍼로 이식(`index.html:894-930` 패리티).

---

## 4. 인증/인가 이식

### 4.1 토큰 보관 — 권장: **공존 기간 localStorage 유지 → 전환 완료 시 httpOnly 쿠키로 격상(오픈 결정)**

| | localStorage (현행 유지) | httpOnly 쿠키 + BFF |
|---|---|---|
| 보안 | XSS 시 토큰 탈취 가능(내부망·8h 만료로 완화되나 ERP 재무 데이터임) | XSS 토큰 탈취 차단(최선) |
| 공존 | **레거시 index.html과 같은 origin에서 토큰 자연 공유** — 이전 기간 이중 로그인 없음 | 레거시는 localStorage, Next는 쿠키 → 세션 이원화·동기화 코드 필요 |
| 구현 | 0 (현행 이식) | 로그인 Route Handler + 쿠키→Authorization 변환 프록시(catch-all 1개) + Node 런타임 필수 |
| 백엔드 변경 | 없음 | 없음(BFF가 헤더 변환) |

권장 시나리오: **이전 기간은 localStorage**(스트랭글러 공존의 핵심 이점), 레거시 삭제 시점에 httpOnly 쿠키 + 로그인/프록시 Route Handler로 전환(그때 Next middleware 인증 가드도 활성화 가능). 처음부터 쿠키로 가면 공존 기간 내내 두 세션을 관리해야 함 — 비추천.

### 4.2 로그인 연동

- `POST /api/auth/login`은 OAuth2 form-encoded(`app/api/auth.py:83`) — openapi-fetch의 `bodySerializer`로 `URLSearchParams` 직렬화(현 `index.html:936-938`과 동일). JSON으로 보내면 422 나는 함정을 클라이언트 1곳에 캡슐화.
- 회원가입은 JSON `POST /api/auth/register` — 승인 대기 안내 문구 패리티 유지.

### 4.3 게이팅 구조 (data-perm → 선언적 가드)

```
로그인 성공 → useMe(): GET /api/auth/me (TanStack Query, staleTime 길게)
  ├─ (app)/layout.tsx: me 없음(401) → /login redirect (클라이언트 가드)
  ├─ <Nav>: 메뉴 정의 배열 [{href, label, perm?, badge?}] → can(perm) 필터
  │    · perm 매핑은 현 data-perm 그대로: stock:read, purchase:read, sales:read,
  │      payment:read, user:read, audit:read (index.html:279-288)
  ├─ usePermission(code) / <RequirePerm code> → 버튼·액션 게이팅 (data-perm 버튼들 대체)
  └─ 401 인터셉터(클라이언트 미들웨어): 토큰 제거 + /login 이동 (doLogout 패리티)
```

- `can(code)` 로직 패리티: `permissions.includes("*") || permissions.includes(code)` (`index.html:883`).
- 클라이언트 가드의 한계(비인증 시 보호 페이지 셸이 잠깐 렌더될 수 있음)는 내부 도구에서 수용 가능. 서버는 어차피 모든 요청을 `require_permission`으로 재검증(`app/deps.py:43`) — 프론트 게이팅은 UX용, 보안 경계는 백엔드라는 현 모델 유지. 쿠키 전환 시 middleware로 격상.
- 라우팅 개선: hash(`#partners`) → 실제 경로(`/partners`). 부수 효과로 현재 깨져 있는 일부 페이지 딥링크/새로고침 복원(`index.html:996`에 purchase/sales 등 누락)이 자연 해결됨.

---

## 5. 디자인 시스템 이식 매핑표 (모노크롬 유지, 다크모드 없음)

`:root` CSS 변수(`index.html:17-32`: color/spacing/type scale/radius/ring)는 `globals.css`로 **그대로 복사**해 토큰으로 사용. 컴포넌트는 CSS Modules로 스타일을 옮기고 마크업을 React화.

| 현재 클래스/패턴 | React 컴포넌트 | 비고 |
|---|---|---|
| `.btn` `.ghost` `.danger` `.sm` `.block` `[disabled]` | `<Button variant size block>` | |
| `.stat`(.k/.v/.foot) | `<StatCard label value foot>` | 대시보드·리포트 카드 |
| `.panel` + `.panel-head` + `.toolbar` | `<Panel>` `<PanelHead>` `<Toolbar>` | 목록 화면 골격 |
| `table` + `th.sortable` + `.pager` + `.empty` + `.loading` | `<DataTable columns sort onSort page onPage>` | 서버사이드 정렬·페이지네이션(`Page[T]`), 빈/로딩 상태 내장 |
| `.tag`(.cust/.supp/.both/.gray) | `<Tag variant>` | 거래처 유형·상태 뱃지 |
| `.overlay`+`.modal`(h3/.body/.foot) | `<Modal title footer>` | ESC/포커스 트랩 보강 |
| `.form-grid`+`.field`+`.fld-err` | `<FormGrid>` `<Field label error>` | RHF 연결, 서버 `fields` 에러 표시 |
| `.toasts`/`.toast` | `<ToastProvider>` + `useToast()` | `toast(msg,isErr)` 패리티 |
| `.nav`(+`.cnt` 배지) | `<Nav items>` + `<NavBadge>` | 배지 = 폴링 쿼리(alerts/count, pending/count) |
| `.topbar`/`.who-btn`/`.brand` | `(app)/layout.tsx` 앱 셸 | |
| `.chips`/`.chip` | `<Chips>` | 권한 목록(내 정보) |
| `pre.json` | `<JsonView>` | 감사로그 상세 |
| `dl.kv` | `<KeyValueList>` | 상세 보기 |
| 헬퍼 `won()` `esc()` | `lib/format.ts`의 `won()` / esc는 **삭제** | React가 이스케이프 담당 — XSS 표면 축소 부수효과 |
| 폰트(Inter/Pretendard CDN) | `next/font` + Pretendard 셀프호스팅 권장 | 외부 CDN 의존 제거(내부망 안정성) |

---

## 6. 점진 이전 전략 (핵심)

### 6.1 슬라이스 순서 (수직 슬라이스 = 라우트+훅+컴포넌트+게이팅 완결)

| # | 슬라이스 | 내용 | 비고 |
|---|---|---|---|
| 0 | 스캐폴딩 | create-next-app, 토큰 CSS, rewrites, 타입 생성 파이프라인 | §8 |
| 1 | **인증** | /login(로그인·회원가입), api 클라이언트, AuthProvider, 401 처리 | 이후 모든 슬라이스의 토대 |
| 2 | **레이아웃/nav** | 앱 셸, 권한 기반 nav, 배지 2종, 내 정보(me), 로그아웃 | `<RequirePerm>` 확립 |
| 3 | **대시보드** | 통계 카드(stats/overview 등)·최근 목록 패널 | 읽기 전용 — 위험 최소로 패턴 검증 |
| 4 | **마스터**: 거래처 → 품목 | CRUD 테이블+모달, 검색/페이지네이션, 거래처 원장(transactions/summary/balance) | `<DataTable>`·`<Modal>`·RHF 패턴 확립 |
| 5 | **트랜잭션**: 발주 → 수주 → 재고 → 창고 | 상태 전이 액션(confirm/receive/return/cancel/ship), 라인아이템 폼, 창고간 이전 | 가장 복잡 — 패턴 성숙 후 착수 |
| 6 | **리포트**: 매입/매출 → 재고평가 → 채권/채무(aging) | 기간·그룹별 집계 뷰, **엑셀 export(blob+auth 헤더)** | `downloadFile` 패리티 |
| 7 | **관리**: 결제 → 회원 → 감사로그 | 결제/수금, 승인 워크플로, JSON 상세 뷰 | |
| 8 | **레거시 제거** | index.html 삭제, `/legacy` rewrite 제거, (선택) 쿠키 전환·middleware 가드 | FastAPI `/` 는 Next로 위임 or 리다이렉트 |

순서 근거: 의존 토대(인증→셸) 먼저, 그다음 위험 낮은 읽기 화면(대시보드)으로 패턴 검증, 단순 CRUD(마스터)로 패턴 확립, 복잡한 상태 전이(트랜잭션)는 성숙 후.

### 6.2 공존 방법 (스트랭글러 껍데기)

- **Next가 front door**: 사용자는 항상 Next origin 접속. 미이전 페이지는 `/legacy#<page>`로 rewrites 프록시(레거시는 hash 라우팅이라 경로 1개로 전 페이지 커버).
- **같은 origin ⇒ localStorage 토큰 공유** — 두 UI 간 SSO가 공짜(§4.1 권장의 근거).
- 슬라이스 완료 시마다 레거시 nav의 해당 버튼을 `location.href="/<page>"`(Next 경로) 링크로 치환하고 레거시 쪽 해당 페이지 코드는 방치(삭제는 8단계에 일괄) — index.html 수정은 버튼 핸들러 몇 줄 수준. Next nav에는 반대로 미이전 페이지를 `/legacy#<page>` 링크로 노출.
- 이 방식이면 어느 시점에도 "모든 페이지 접근 가능 + 단일 로그인" 유지, 롤백은 링크 원복으로 즉시 가능.

### 6.3 슬라이스 완료 정의(DoD) — 공통 체크리스트

1. **스크린 패리티**: 레거시 화면과 나란히 비교 — 컬럼·필터·정렬·페이지네이션·빈 상태·토스트 문구 동일(개선은 별도 이슈로, 이전 중 스코프 확대 금지)
2. **권한 패리티**: 해당 페이지 `data-perm` 매핑 전수 확인 — 권한 없는 계정으로 nav 미노출·버튼 미노출·직접 URL 진입 시 안내 확인(서버 403 처리 포함)
3. **API 연동**: 생성 타입 사용(수기 타입 0), 422 `fields`→폼 에러, 401→로그아웃, 오류 토스트에 `detail` 표시
4. **레거시 nav 치환** + Next nav 등록, 딥링크(새로고침) 동작
5. 배지·카운트 등 부속 동작 패리티 (해당 시)

---

## 7. 오픈 결정 (사용자 확인 필요)

| # | 결정 | 권장 | 근거 | 트레이드오프 |
|---|---|---|---|---|
| 1 | 레포 구성 | **모노레포 `frontend/`** | 타입 생성 파이프라인의 원자적 커밋, 운영 단순 | 별도 레포는 팀 분리 시에만 이점 |
| 2 | 토큰 저장 | **공존 기간 localStorage → 완료 시 httpOnly 쿠키+BFF 격상** | 공존 기간 단일 세션 유지가 최우선, 최종 보안은 쿠키가 우위 | 처음부터 쿠키 시 이중 세션 관리 비용; 끝까지 localStorage 시 XSS 리스크 잔존 |
| 3 | 스타일 | **CSS 변수 토큰 + CSS Modules (Tailwind 미채택)** | 완성된 디자인 시스템의 최저비용·최고충실도 이식 | Tailwind 선호 팀원이 있거나 신규 화면 다작 예정이면 v4 채택 재검토 |
| 4 | 배포 형태 | **Next standalone 서버 + 리버스 프록시** | 향후 쿠키/BFF·middleware 여지 확보 | 정적 export는 Node 불필요하나 §7-2 격상 경로 봉쇄 |
| 5 | 이전 중 신규 기능 정책 | **신규/변경 기능은 Next에만 구현(레거시 동결)** | 이중 구현 방지, 이전 완주 동기 유지 | 미이전 페이지에 긴급 수정 필요 시 예외 허용 기준 필요 |

(i18n은 한국어 단일 확정으로 판단해 결정 목록에서 제외 — 필요해지면 그때 논의)

---

## 8. 스캐폴딩 다음 단계 (승인 후 첫 작업)

Windows/PowerShell, `D:\erp`에서:

```powershell
# 1) 스캐폴딩 (플래그는 실행 시점 최신 CLI 기준 재확인)
npx create-next-app@latest frontend --typescript --app --src-dir --eslint --no-tailwind --import-alias "@/*"

# 2) 의존성
cd frontend
npm i @tanstack/react-query openapi-fetch react-hook-form
npm i -D openapi-typescript

# 3) 타입 생성 (FastAPI 기동 상태에서)
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/lib/api/schema.d.ts
```

첫 커밋(슬라이스 0~1) 파일 목록:

| 파일 | 내용 |
|---|---|
| `frontend/next.config.ts` | rewrites: `/api/*`·`/legacy`·`/static/*` → `127.0.0.1:8000` |
| `frontend/src/app/globals.css` | `index.html:17-49`의 토큰·베이스 스타일 이식 |
| `frontend/src/app/layout.tsx` | 폰트(next/font), `<Providers>`(QueryClient) |
| `frontend/src/lib/api/schema.d.ts` | 생성 산출물 (+ `package.json`에 `gen:api` 스크립트) |
| `frontend/src/lib/api/client.ts` | openapi-fetch 인스턴스 + Bearer 주입 + 401/오류 미들웨어 + form 로그인 헬퍼 + blob 다운로드 |
| `frontend/src/lib/auth/AuthProvider.tsx` | `useMe`/`can`/`usePermission`/`logout` |
| `frontend/src/app/login/page.tsx` | 로그인·회원가입(패리티: admin 힌트, 승인 안내) |
| `frontend/src/components/ui/{Button,Field,Toast}.tsx` | 로그인에 필요한 최소 셋 |
| `frontend/src/app/(app)/layout.tsx` | 인증 가드 + 앱 셸 골격(이후 슬라이스 2에서 nav 완성) |

검증: 로그인 → `/dashboard`(빈 페이지) 진입, 잘못된 비밀번호 시 오류 문구, `/legacy#partners`로 레거시 정상 접근 + 재로그인 불필요 확인.

---

## 9. 위험 요소

| 위험 | 완화 |
|---|---|
| form-encoded 로그인을 JSON으로 보내는 실수(422) | 클라이언트 1곳에 캡슐화, 슬라이스 1 DoD에 포함 |
| 엑셀 export가 `<a href>`로는 인증 헤더 못 실음 | fetch→blob→objectURL 패턴 이식(`index.html:913-930`), 슬라이스 6 DoD |
| OpenAPI 스키마-타입 드리프트 | CI에서 `gen:api` + `git diff --exit-code` |
| 공존 기간 레거시/Next 이중 수정 유혹 | §7-5 레거시 동결 정책 |
| Next 버전별 캐싱/라우팅 기본값 차이 | 스캐폴딩 시점에 채택 버전 공식 문서로 rewrites·캐시 기본값 확인(본 문서의 버전 언급은 그 시점에 재검증) |
| 클라이언트 가드 한계(보호 페이지 셸 노출) | 내부 도구로 수용, 데이터는 서버 권한 검증이 방어(`app/deps.py:43`) — 쿠키 전환 시 middleware 격상 |
