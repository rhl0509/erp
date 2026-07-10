# 총계정원장(General Ledger) 설계 문서

작성일: 2026-07-11 · 대상: D:\erp (FastAPI + SQLAlchemy 2.0 + MySQL / Next.js App Router)
상태: **설계안 — 오픈 결정(§9) 확정 전 구현 착수 금지**

---

## 1. 요구사항 정리 / 가정

### 만드는 것
기존 ERP(매입·매출·재고·결제·세금계산서)에 복식부기 총계정원장을 추가한다.
산출물: 분개장(journal), 계정별원장(ledger), 시산표(trial balance), (선택) 간이 재무제표.

### 대전제 — GL은 기존 숫자와 일치해야 한다
GL은 새 진실을 만들지 않는다. 기존 시스템이 이미 확정한 값을 그대로 복식부기 형식으로 옮긴다.

| 기존 값 | 원천 | GL에서의 위치 |
|---|---|---|
| AP/AR 청구액 = 납품 기준(입고−반품 / 출고−반품), VAT 포함 | `app/api/partners.py:191-233`, `app/api/reports.py:23-98`(aging) | 외상매입금/외상매출금 잔액 |
| 공급가액 = 수량×단가(세별도), 세액 = `compute_tax` 스냅샷 | `app/services.py:17-24`, `app/services.py:253-257` | 매출/매입·부가세 계정 |
| 재고평가 = Σ on_hand×avg_cost(창고별 이동평균), VAT 제외 | `app/services.py:183-215`, `app/api/costing.py:14-24` | 재고자산 잔액 |
| COGS = 출고 시점 이동평균(movement.cost), 0원 출고는 손익 제외 | `app/api/costing.py:93-122` | 매출원가 (0원 출고는 별도 계정 §4-D) |
| 반품 = 음수 수량 역이동 | `app/services.py:281-331` | 역방향 분개 |
| 창고간 이전 = 회사 전체 평가액 불변, AP/AR·매출 무영향 | `app/services.py:334-392` | **전기하지 않음** |
| 세금계산서 = 발행 시점 스냅샷 문서 | `app/models.py:286-312` | **전기하지 않음**(§4-H) |

### 가정 / 제약
- 내부·단일 사용자군, 단일 법인, 원화 단일 통화.
- 인식 시점은 현행 그대로 **발생주의·납품 기준**(입고/출고 시 AP/AR·수익 인식). 현금주의 전환은 범위 밖 — 기존 aging/여신(`app/api/sales.py:239-260`)이 전부 납품 기준이라 GL만 다른 기준을 쓰면 숫자가 갈라진다.
- **범위 밖**: 다통화, 부문/프로젝트 회계, 기말 결산 마감분개·이월, 고정자산/감가상각, 급여, 세무조정, 계정과목 편집 UI(초기엔 시드 고정).
- 마이그레이션은 기존 Alembic 체계(`migrations/versions/`)를 따른다.
- 날짜 컬럼은 기존 관행(String(10) `YYYY-MM-DD`, 예: `Payment.pay_date` `app/models.py:342`)을 따른다.

### 경제적 사건 목록 (원천 데이터에서 도출)
원천 행의 필드만으로 사건이 판별된다 — 이것이 뒤의 "재생성 가능" 설계의 근거다.

| 사건 | 판별 규칙 (StockMovement / Payment) |
|---|---|
| 매입입고 | `movement_type=IN`, `quantity>0` |
| 매입반품 | `movement_type=IN`, `quantity<0` (`ref_type=PRET`) |
| 매출출고 | `movement_type=OUT`, `quantity>0`, `unit_price>0` |
| 비매출 출고(증정·폐기·감모) | `movement_type=OUT`, `quantity>0`, `unit_price=0` |
| 매출반품 | `movement_type=OUT`, `quantity<0` (`ref_type=SRET`) |
| 재고조정 | `movement_type=ADJUST` (부호 있는 quantity) |
| 창고간 이전 | `movement_type=TRANSFER` — **회계 무영향, 전기 제외** |
| 지급 | `Payment.kind=AP` |
| 수금 | `Payment.kind=AR` |
| 세금계산서 발행/취소 | TaxInvoice — **전기 제외**(신고 보조 문서, §4-H) |

---

## 2. 접근 방식 결정: 파생형(A) vs 전기형(B)

| 기준 | (A) 파생형 — 쿼리로 원장/시산표 계산, 저장 없음 | (B) 전기형 — JournalEntry/Line에 복식부기 전기·저장 |
|---|---|---|
| 정합성 | 원천이 단일 진실이라 **구조적으로 항상 일치** | 이중 기록 — 훅 누락/버그 시 어긋날 수 있음(재대사·재전기로 방어) |
| 감사추적 | 전표번호·전표 개념 없음. 원천 삭제 시 과거 원장도 소급 변동 | 전표번호·일자·출처 링크가 남음. AuditLog와 결합해 추적 가능 |
| 성능 | 시산표=전 이동·결제 풀스캔+사건 판별 CASE 분기를 매 조회마다 수행 | 전기 시 1회 계산, 조회는 인덱스된 lines 집계 — 기간 필터·러닝밸런스 저렴 |
| 구현비용 | 낮음(테이블 없음). 단, 사건→분개 매핑을 SQL/파이썬 조회 로직에 중복 구현 | 중간(테이블 3개+전기 서비스+훅+백필) |
| 소급(기존 데이터) | 자동(쿼리니까) | 백필 스크립트 필요 — 단 매핑이 원천 필드의 순수 함수라 결정적으로 재생성 가능 |
| 확장(기초잔액·수기전표·마감) | 불가능(저장소가 없음) | 자연스럽게 수용(MANUAL 전표) |
| 치명적 제약 | **매입반품의 이동평균원가가 원천에 저장돼 있지 않아**(§4-C) 쿼리만으론 재고자산 잔액을 valuation과 일치시킬 수 없음 | 전기 시점에 `apply_stock_delta`가 돌려주는 avg_cost를 그대로 사용 가능 |

### 권장: (B) 전기형 + "언제든 원천에서 재생성 가능" 설계
이유:
1. 모든 분개가 원천 행(+ 전기 시점 avg_cost)의 **결정적 함수**다. 따라서 전기형의 최대 약점(이중 기록 드리프트)을 "전체 재전기(rebuild) 명령 + 재대사 리포트"로 상쇄할 수 있다 — 파생형의 정합성 보장과 전기형의 감사추적·성능을 동시에 얻는다.
2. 전표번호·감사추적은 요구사항이다. 파생형은 이를 제공할 수 없다.
3. 파생형은 매입반품 재고 대변 금액(반품 시점 이동평균)을 계산할 수 없어 재고자산 잔액이 valuation과 어긋난다(§4-C). 이 시점에서 파생형은 사실상 탈락이다.
4. 이 ERP 규모(내부·단일사용자군)에서 전기형 추가 비용은 테이블 3개+서비스 1개 수준으로 과하지 않다.

### 권장 구조 (흐름)

```
[거래 API]                [서비스 계층]                        [GL]
purchase/receive ─┐
sales/ship        ├→ post_movement ──┐ (같은 DB 트랜잭션)
stock/movements   ┘                  ├→ gl.post_for_movement ─→ JournalEntry
po/return, so/return → post_return ──┘        (사건 판별→분개 생성,   + JournalLines
stock/transfer     → post_transfer → (전기 없음)  차대 일치 검증)
payments POST      → gl.post_for_payment ──────────────────────→ ˝
stock DELETE / payments DELETE → gl.delete_for_source ─────────→ 전표 삭제(동반)

[조회] /api/gl/journal(분개장) · /ledger(계정별원장) · /trial-balance(시산표)
[정합] /api/gl/reconcile: GL잔액 vs aging·valuation·margin 대조
[복구] scripts/rebuild_gl.py: 전 전표 삭제 후 원천 replay 재전기(백필과 동일 코드)
```

핵심 원칙:
- **원천과 같은 트랜잭션에서 전기**한다(post_movement/post_return의 flush 이후, commit 전). 이동은 성공했는데 전표가 없는 상태가 구조적으로 불가능.
- 전표는 `(source_type, source_id)` UNIQUE로 원천 1행=전표 1건 멱등성 보장.
- 원천이 삭제되면(수기 이동 `app/api/stock.py:177-225`, 결제 `app/api/payments.py:146-158`) 전표도 같은 트랜잭션에서 삭제 — 삭제 이력은 기존 AuditLog가 담당(현행 삭제 정책과 동일 철학).

---

## 3. 계정과목 (Chart of Accounts) 초안

한국 중소 도소매 관행 수준의 최소 실용 체계. 4자리 코드, 첫 자리=대분류(계층은 코드 프리픽스로 표현, parent 컬럼 없이 시작).

| 코드 | 계정명 | 유형 | 용도 |
|---|---|---|---|
| 1110 | 현금및예금 | 자산 | 지급/수금 상대 계정(method 구분 없이 단일 — §9-6) |
| 1120 | 외상매출금 (AR) | 자산 | 매출출고·매출반품·수금. 라인에 partner_id 보조 차원 |
| 1130 | 상품 (재고자산) | 자산 | 입고·출고·반품·조정. 잔액 == Σ on_hand×avg_cost |
| 1140 | 부가세대급금 (매입세액) | 자산 | 매입 tax_amount. 재고원가에 불포함(현행 규칙 그대로) |
| 2110 | 외상매입금 (AP) | 부채 | 매입입고·매입반품·지급. 라인에 partner_id |
| 2120 | 부가세예수금 (매출세액) | 부채 | 매출 tax_amount |
| 3110 | 이월이익잉여금/기초자본 | 자본 | 기초잔액 수기 전표 전용(백필 전 개시 잔액) |
| 4110 | 상품매출 | 수익 | 유상 출고 공급가액. 반품은 차변 기입(순액 표시) |
| 4910 | 재고조정이익 | 수익 | ADJUST 증가분 |
| 5110 | 매출원가 (COGS) | 비용 | 유상 출고의 qty×cost. margin 리포트와 정의 일치 |
| 5120 | 재고감모손실 | 비용 | 0원 출고(증정·폐기), ADJUST 감소분 |
| 5130 | 재고원가차이 | 비용(±) | 매입반품의 (매입단가−이동평균) 차이(§4-C). 잡이익 겸용 |

- 매출환입(반품) 전용 차감계정은 두지 않는다 — 기존 리포트가 전부 순액 집계(`app/api/reports.py:47-55`)라 4110 차변 기입이 정의상 일치한다. 필요 시 후속 확장.
- 계정 마스터는 시드로 고정 배포(사용자 편집 UI는 범위 밖, §9-3).

---

## 4. 분개 규칙 매핑표 (핵심)

표기: `qty` = 저장 수량(반품은 음수 저장이므로 아래는 절대값 기준으로 서술), `supply` = |qty|×unit_price, `tax` = |tax_amount|, `total` = supply+tax, `avg` = 그 시점 창고별 이동평균원가(`apply_stock_delta` 반환값), `cost` = movement.cost. 면세 품목은 세액 라인 자체를 생략(0원 라인 금지).

### A. 매입입고 — IN, qty>0 (`app/services.py:224-278`)
| 차변 | 대변 |
|---|---|
| 1130 상품 supply | 2110 외상매입금 total (partner_id) |
| 1140 부가세대급금 tax | |

이동평균 수식상 재고평가액 증가분이 정확히 supply(=qty×매입단가)이므로 1130 잔액이 valuation과 일치한다(`app/services.py:199-215`).

### B. 매출출고 — OUT, qty>0, unit_price>0 (전표 1건에 4~5라인)
| 차변 | 대변 |
|---|---|
| 1120 외상매출금 total (partner_id) | 4110 상품매출 supply |
| | 2120 부가세예수금 tax |
| 5110 매출원가 qty×cost | 1130 상품 qty×cost |

cost = 출고 시점 이동평균(`app/services.py:251`) — margin 리포트의 COGS(`app/api/costing.py:105`)와 동일 값.

### C. 매입반품 — IN, qty<0, ref_type=PRET  ⚠ 유일하게 까다로운 매핑
AP 차감액은 반품단가 기준(total), 재고평가 감소액은 **반품 시점 이동평균 기준**(|qty|×avg — `post_return`은 `unit_cost=None`으로 평균 불변·수량만 차감, `app/services.py:309-311`)이다. 둘의 차이를 5130에 흘린다:

| 차변 | 대변 |
|---|---|
| 2110 외상매입금 total (partner_id) | 1130 상품 |qty|×avg |
| | 1140 부가세대급금 tax |
| (unit_price<avg 이면) 5130 재고원가차이 차액 | (unit_price>avg 이면) 5130 재고원가차이 차액 |

이렇게 해야 1130 잔액 == Σ on_hand×avg_cost 불변식이 유지된다. **주의: movement.cost에는 매입단가가 저장되므로**(`app/services.py:318-319`) 이 avg는 전기 시점에 `apply_stock_delta` 반환값에서 받아야 하고, 백필 시엔 이동 이력 replay로 복원한다(§6).

### D. 매출반품 — OUT, qty<0, ref_type=SRET (B의 역방향)
| 차변 | 대변 |
|---|---|
| 4110 상품매출 supply | 1120 외상매출금 total (partner_id) |
| 2120 부가세예수금 tax | |
| 1130 상품 |qty|×cost | 5110 매출원가 |qty|×cost |

cost = 반품 시점 이동평균(원 COGS 근사 — 기존 정책 그대로, `app/services.py:318`). 재고 증가가 평균 불변·수량 증가이므로 |qty|×cost가 정확히 valuation 증가분과 일치한다.

### E. 비매출 출고(0원 출고) — OUT, qty>0, unit_price=0
| 차변 | 대변 |
|---|---|
| 5120 재고감모손실 qty×cost | 1130 상품 qty×cost |

**매출원가(5110)가 아니다** — 기존 margin 리포트가 0원 출고를 revenue/COGS 양쪽에서 제외하므로(`app/api/costing.py:106-109`), 5110에 넣으면 GL COGS ≠ margin COGS가 된다. AR·매출·세액 라인 없음(supply=0, tax=0).

### F. 재고조정 — ADJUST (부호 있는 qty, cost=조정 시점 avg)
| qty>0 (증가) | 차) 1130 상품 qty×cost / 대) 4910 재고조정이익 qty×cost |
|---|---|
| **qty<0 (감소)** | 차) 5120 재고감모손실 |qty|×cost / 대) 1130 상품 |qty|×cost |

### G. 결제 — Payment (`app/api/payments.py:56-131`)
| AP 지급 | 차) 2110 외상매입금 amount (partner_id) / 대) 1110 현금및예금 amount |
|---|---|
| **AR 수금** | 차) 1110 현금및예금 amount / 대) 1120 외상매출금 amount (partner_id) |

### H. 전기하지 않는 것 (명시적 제외)
- **TRANSFER**: 회사 전체 재고평가액 불변(출고창고 평균으로 입고창고 평균 갱신, `app/services.py:334-392`). 창고별 재고 계정을 분리하지 않으므로 GL 무영향. 이 불변식(partner=None·unit_price=0·tax=0)을 전기 서비스도 존중한다.
- **세금계산서 발행/취소**: 부가세는 이동 시점에 이미 인식했다. 발행 시 또 전기하면 이중계상. 주의: 계산서 스냅샷은 **주문 전량(l.qty) 기준**이라(`app/api/invoices.py:89-97`) 부분출고 시 GL 부가세 계정(납품 기준)과 금액이 다를 수 있다 — §9-5.
- **PO/SO 확정·취소, draft 삭제**: 재고·채권채무 변동이 없으므로 무전기(현행도 회계 영향 없음).

### 엣지 케이스
- **거래처 없는 유상 수기 이동**(`StockMovement.partner_id` nullable, `app/models.py:175`): 기존 aging/거래처잔액은 partner 있는 행만 AP/AR로 집계한다. GL도 동일하게 — partner 없으면 2110/1120 대신 **1110 현금및예금**을 상대 계정으로(즉시 현금거래 간주). 이렇게 해야 GL AP/AR 잔액 == aging 합계. *운영상 이런 데이터가 실재하는지 확인 필요 — 없다면 입력 시 partner 필수화가 더 깔끔하다.*
- **원천 삭제**: 수기 이동 삭제(최근 IN만 허용, `app/api/stock.py:190-204`)·결제 삭제 시 연결 전표를 같은 트랜잭션에서 삭제. 역분개 방식 대안은 §9-4.

---

## 5. 데이터 모델 (전기형)

```
accounts
  id PK · code VARCHAR(10) UNIQUE · name VARCHAR(100)
  account_type VARCHAR(10)  -- asset/liability/equity/revenue/expense
  is_active BOOL · created_at/updated_at

journal_entries (전표)
  id PK · entry_no VARCHAR(30) UNIQUE     -- JV-YYYYMM-#### (락분리 채번, 결번 허용)
  entry_date VARCHAR(10)                  -- YYYY-MM-DD (이동=created_at 날짜, 결제=pay_date)
  description VARCHAR(255)                -- 예: "수주 SO-202607-0001 출고"
  source_type VARCHAR(10)                 -- MOVEMENT / PAYMENT / MANUAL
  source_id INT NULL                      -- 원천 PK (MANUAL이면 NULL)
  created_at/updated_at
  UNIQUE (source_type, source_id)         -- 원천당 전표 1건(멱등 전기·중복 방지)
  INDEX (entry_date)

journal_lines (분개 라인)
  id PK · entry_id FK(journal_entries, ON DELETE CASCADE)
  line_no SMALLINT · account_id FK(accounts)
  debit NUMERIC(18,4) DEFAULT 0 · credit NUMERIC(18,4) DEFAULT 0
  partner_id FK(partners) NULL            -- AR/AP 라인의 보조원장 차원(aging 대사용)
  memo VARCHAR(255)
  INDEX (account_id, entry_id)            -- 계정별원장·시산표 집계 경로
```

설계 노트:
- **금액은 NUMERIC(18,4)** — COGS가 Decimal(avg_cost×qty)이라 기존 `StockMovement.cost` 정밀도 정책(`app/models.py:177-179`)과 맞춘다. 공급가·세액은 정수지만 같은 타입으로 통일.
- **차/대 분리 컬럼**(부호 컬럼 대신) — 시산표·원장 표시가 표준 관행과 일치하고 CHECK(debit>=0 AND credit>=0, 둘 중 하나만 >0)로 방어 가능.
- **불변식(서비스 계층에서 강제)**: ① 전표별 Σdebit == Σcredit(전기 함수가 flush 전에 assert — 위반 시 예외로 전체 롤백) ② 0원 라인 금지 ③ posted 전표 수정 금지(수정은 원천을 통해서만: 원천 삭제→전표 삭제). DB CHECK로는 행 간 합계를 못 잡으므로 재대사 쿼리(§7)로 이중 방어.
- **전표번호 채번**: 이동과 같은 고빈도 경로이므로 `generate_movement_no`의 락분리 패턴(`app/services.py:101-138`)을 seq_key="JE"로 재사용. 결번 허용(내부 번호).
- status 컬럼은 두지 않는다(자동 전기는 항상 posted, 취소=삭제) — MANUAL 전표를 도입해도 posted 단일 상태로 시작. draft 워크플로는 범위 밖.

---

## 6. 백필(소급 전기) 전략

원칙: **백필 코드 == 재전기(rebuild) 코드 == 최초 전기 코드의 동일 매핑 함수.** 세 경로가 같은 함수를 쓰면 드리프트가 원천 차단된다.

절차 (`scripts/rebuild_gl.py`, 트랜잭션 1개 또는 청크 커밋):
1. 기존 GL 전표 전체 삭제(초기 백필이면 없음).
2. StockMovement를 `id` 오름차순으로 replay. **(item_id, warehouse_id)별 이동평균 시뮬레이터**를 메모리에 유지 — `apply_stock_delta`와 동일 수식으로 IN은 가중평균 갱신, PRET/OUT/ADJUST는 평균 불변. 매입반품(§4-C)의 "반품 시점 avg"는 이 시뮬레이터에서 얻는다(원천에 저장돼 있지 않은 유일한 값).
3. Payment를 pay_date·id 순으로 전기.
4. 검증(§7 재대사)을 실행해 통과해야 커밋.
- entry_date는 이동=created_at의 날짜, 결제=pay_date. TRANSFER·취소문서는 건너뜀.
- 데이터 규모가 내부 ERP 수준이므로 전량 replay 비용은 무시 가능. 실패 시 전체 롤백(부분 백필 금지).
- 검증: replay 종료 시점의 시뮬레이터 avg_cost가 현재 `stock_balances.avg_cost`와 일치해야 한다 — 불일치면 백필 중단하고 원인 조사(과거 IN 삭제로 인한 이력 단절 가능성, 확인 필요).

## 7. 산출물(리포트)과 기존 리포트의 관계

| API | 내용 | 기존과의 대사(불변식) |
|---|---|---|
| `GET /api/gl/journal` | 분개장 — 전표 목록+라인, 기간·출처·계정 필터, 페이지네이션(`paginate` 재사용) | 원천 문서로 딥링크(source_type/id) |
| `GET /api/gl/ledger?account_code&date_from&date_to` | 계정별원장 — 기간 라인 + 러닝밸런스, 기초/기말 잔액 | 1120 잔액 == aging(AR) 총액, 2110 == aging(AP) 총액 |
| `GET /api/gl/trial-balance?date_to` | 시산표 — 계정별 차/대 합계·잔액, Σ차 == Σ대 | 1130 == valuation summary, 5110 == margin COGS, 4110 == margin revenue(순액) |
| `GET /api/gl/reconcile` | 재대사 — 위 불변식들을 실제로 대조해 diff 반환 | CI/운영 헬스체크로 사용. diff≠0이면 rebuild 권고 |
| (선택) `GET /api/gl/income-statement` | 간이 손익 = 수익 − 비용 | margin 리포트의 상위 호환(감모손실 포함) |

기존 aging(거래처별 FIFO 상계)·valuation(품목별)·margin은 **보조원장**으로 그대로 유지 — GL은 총계 관점, 기존 리포트는 차원별 상세라는 역할 분담. 중복 아님.

## 8. 구현 계획 (슬라이스)

기존 트랜잭션에의 침습은 **함수 호출 4곳 추가**가 전부다(서비스 내부 2곳 + 라우터 2곳). 원천 로직·값 계산은 일절 손대지 않는다.

| # | 슬라이스 | 내용 | 완료 기준 |
|---|---|---|---|
| S1 | GL 코어 | `accounts/journal_entries/journal_lines` 모델 + Alembic 마이그레이션 + 계정 시드(§3). `app/gl.py` 전기 서비스: `post_for_movement(db, movement, avg_cost)`, `post_for_payment(db, payment)`, `delete_for_source(db, type, id)` — 사건 판별·분개 생성·차대 검증 | 매핑표 §4의 사건별 단위 테스트(면세·0원·반품·partner 없는 이동 포함) 통과 |
| S2 | 훅 연결 | `post_movement`·`post_return` 말미(avg_cost 확보 지점)에서 전기 호출, TRANSFER 제외. `payments.py` 생성/삭제, `stock.py` 이동 삭제에 전기/삭제 호출 | 입고→출고→반품→결제→삭제 시나리오 통합 테스트에서 시산표 균형 + 재대사 diff=0 |
| S3 | 백필/재전기 | `scripts/rebuild_gl.py`(§6) + `/api/gl/reconcile` | 기존 운영 데이터 소급 전기 후 재대사 전 항목 diff=0, 시뮬레이터 avg == stock_balances.avg_cost |
| S4 | 조회 API | journal / ledger / trial-balance (+ 권한 `gl:read` 시드) | 스키마 확정, 페이지네이션·기간 필터, N+1 없음(lines selectin) |
| S5 | 프론트 | Next.js `(app)/gl/journal`·`(app)/gl/ledger`·`(app)/gl/trial-balance` 페이지 — 기존 `(app)/aging`·`(app)/transactions` 페이지 패턴(page.tsx + module.css) 답습 | 분개장→원천 문서 링크, 시산표 합계 균형 표시 |
| S6 | 선택 | MANUAL 전표 API(기초잔액용, 3110 상대) · 간이 손익/재무상태 | 오픈 결정 후 |

위험 요소:
- **S2가 유일한 위험 지점** — 훅 누락 경로(예: 새 이동 생성 경로 추가 시). 완화: 전기 진입점을 `post_movement`/`post_return` 내부에 두어 라우터가 어떤 경로로 이동을 만들든 자동 전기. 수기 이동 삭제·결제만 라우터 훅.
- 매입반품 avg 복원(§6) 불일치 가능성 — 과거에 IN 삭제가 있었다면 replay 결과가 현재 잔고와 어긋날 수 있음(확인 필요). S3 검증 단계에서 드러난다.
- Decimal/int 혼합 반올림 — 전 라인 NUMERIC(18,4) 통일 + 시산표는 Decimal로 집계 후 표시만 반올림.

## 9. 오픈 결정 (사용자 확인 필요)

| # | 결정 | 권장 | 근거 / 트레이드오프 |
|---|---|---|---|
| 1 | 파생형 vs 전기형 | **전기형(B) + 재생성 가능 설계** | §2. 전표번호·감사추적 요구 + 매입반품 avg 문제로 파생형은 정합 요건을 못 채운다. 비용: 테이블 3개·백필 1회 |
| 2 | 매입반품 재고 대변 금액 | **반품 시점 이동평균 + 5130 차이계정** | GL 재고자산 == valuation 불변식 유지(§4-C). 대안(반품단가 그대로)은 구현이 반나절 줄지만 재고 계정이 valuation과 서서히 어긋난다 — 비권장 |
| 3 | 0원 출고·재고조정의 계정 | **5120 재고감모손실(매출원가와 분리)** | margin 리포트가 0원 출고를 COGS에서 제외하므로(costing.py:106-109) 분리해야 GL COGS == margin COGS. 대안(전부 5110)은 계정은 줄지만 기존 리포트와 숫자가 갈라진다 |
| 4 | 원천 삭제 시 전표 처리 | **전표 동반 삭제(AuditLog가 이력 담당)** | 현행이 원천 자체를 물리 삭제하는 시스템이라(stock.py:177, payments.py:146) 전표만 역분개로 남기면 원천 없는 전표가 생겨 대사가 깨진다. 대안(역분개)은 원천도 삭제 대신 취소 플래그로 바꾸는 큰 리팩터링과 함께라야 의미 있음 — 후속 과제 |
| 5 | 부가세 신고 단위·기준 | **GL은 납품(이동) 기준 유지, 세금계산서는 신고 보조자료로 병행** | 계산서 스냅샷이 주문 전량 기준이라(invoices.py:89-97) 부분출고 시 GL 세액과 불일치 가능. 신고를 계산서 기준으로 맞추려면 계산서 발행 로직을 납품 실적 기준으로 바꾸는 별도 결정 필요 — 이 갭을 어느 쪽으로 닫을지 확인 필요 |
| 6 | 기존 데이터 소급 전기 | **전체 소급(백필)** | 데이터 규모가 작고 시산표·원장이 개통 첫날부터 완전해진다. 대안(개통일 이후만+기초잔액 수기 전표)은 백필 검증(§6)이 실패할 때의 fallback |
| 7 | 현금 계정 분리 | **1110 단일로 시작** | Payment.method(계좌이체/현금/카드)별 계정 분리는 언제든 후속 가능(전표 재생성으로 소급도 가능). 지금 나누면 계정만 늘고 활용처가 없다 |

---
근거 파일: `app/services.py`(compute_tax:17-24, apply_stock_delta:183-215, post_movement:224-278, post_return:281-331, post_transfer:334-392, 채번:101-138) · `app/models.py`(StockMovement:160-190, StockBalance:194-213, TaxInvoice:286-312, Payment:331-349) · `app/api/partners.py:191-233` · `app/api/reports.py:23-98` · `app/api/costing.py:14-24, 93-122` · `app/api/invoices.py:89-97` · `app/api/stock.py:177-225` · `app/api/payments.py:56-158` · `app/api/sales.py:239-260` · `migrations/versions/`
