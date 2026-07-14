"""
초기 데이터 시드 스크립트.

실행:  python -m app.seed
생성:  권한(16종), 역할(admin + manager/staff/warehouse/viewer), admin 계정, 샘플 거래처/품목
여러 번 실행해도 안전하도록 멱등(idempotent)하게 작성됨.

admin 초기 비밀번호:
  - SEED_ADMIN_PASSWORD 환경변수가 있으면 그 값을 사용한다.
  - 없으면: SQLite(로컬 개발) 또는 SEED_DEV_DEFAULTS=1 일 때만 기본값
    admin1234 를 사용하고, 운영 DB(MySQL 등)에서는 무작위 비밀번호를
    생성해 1회 출력한다(고정 기본 비밀번호로 운영 배포 방지).
"""
import os
import secrets

from sqlalchemy import select

from .database import SessionLocal, Base, engine
from . import models  # noqa: F401
from .models import User, Role, Permission, Partner, Item, StockMovement, Warehouse
from .security import hash_password, validate_password
from .services import generate_code, post_movement
from .gl import ensure_accounts

PERMISSIONS = [
    ("*", "전체 권한 (슈퍼관리자)"),
    ("user:read", "사용자 조회"),
    ("user:write", "사용자 등록/수정"),
    ("audit:read", "감사 로그 조회"),
    ("partner:read", "거래처 조회"),
    ("partner:write", "거래처 등록/수정/삭제"),
    ("item:read", "품목 조회"),
    ("item:write", "품목 등록/수정/삭제"),
    ("stock:read", "재고 조회"),
    ("stock:write", "입출고 등록/삭제"),
    ("purchase:read", "발주 조회"),
    ("purchase:write", "발주 등록/확정/입고/취소"),
    ("sales:read", "수주 조회"),
    ("sales:write", "수주 등록/확정/출고/취소"),
    ("payment:read", "결제/수금·잔액 조회"),
    ("payment:write", "결제/수금 등록·삭제"),
    ("invoice:read", "세금계산서 조회"),
    ("invoice:write", "세금계산서 발행·취소"),
]

_BUSINESS_PERMS = ["partner:read", "partner:write", "item:read", "item:write",
                   "stock:read", "stock:write",
                   "purchase:read", "purchase:write", "sales:read", "sales:write",
                   "payment:read", "payment:write",
                   "invoice:read", "invoice:write"]

# 회사에서 쓰는 역할 세트. admin(*)은 아래와 별도로 생성한다.
# 각 역할은 (이름, 설명, 권한코드 목록). 멱등 시드로 반복 실행해도 안전.
COMPANY_ROLES = [
    ("manager", "매니저(업무 전체 + 회원·감사 조회)", _BUSINESS_PERMS + ["user:read", "audit:read"]),
    ("staff", "일반 직원(업무 전체)", _BUSINESS_PERMS),
    ("warehouse", "창고 담당(재고 중심)",
     ["item:read", "stock:read", "stock:write", "purchase:read", "sales:read"]),
    ("viewer", "조회 전용",
     ["partner:read", "item:read", "stock:read", "purchase:read", "sales:read",
      "payment:read", "invoice:read"]),
]


def _get_perm(db, code: str) -> Permission:
    return db.execute(select(Permission).where(Permission.code == code)).scalar_one()


def _admin_password() -> tuple[str, bool]:
    """admin 초기 비밀번호와 '무작위 생성 여부'를 돌려준다.

    우선순위: SEED_ADMIN_PASSWORD > 로컬 개발 기본값(admin1234, SQLite 또는
    SEED_DEV_DEFAULTS=1 일 때만) > 무작위 생성(운영 안전 기본).
    """
    env_pw = os.environ.get("SEED_ADMIN_PASSWORD", "")
    if env_pw:
        return env_pw, False
    if engine.dialect.name == "sqlite" or os.environ.get("SEED_DEV_DEFAULTS") == "1":
        return "admin1234", False
    return secrets.token_urlsafe(12), True


def run():
    # SQLite(로컬)에서는 테이블을 즉석 생성한다. 운영 DB는 시드 전에
    # 반드시 `alembic upgrade head` 로 스키마를 만들어 둔다.
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # 1) 권한
        existing = {p.code for p in db.execute(select(Permission)).scalars().all()}
        for code, desc in PERMISSIONS:
            if code not in existing:
                db.add(Permission(code=code, description=desc))
        db.flush()

        # 2) admin 역할 (* 권한)
        admin_role = db.execute(select(Role).where(Role.name == "admin")).scalar_one_or_none()
        if not admin_role:
            admin_role = Role(name="admin", description="시스템 관리자")
            db.add(admin_role)
            db.flush()
        star = _get_perm(db, "*")
        if star not in admin_role.permissions:
            admin_role.permissions.append(star)

        # 3) 회사용 역할 세트 (manager/staff/warehouse/viewer)
        for name, desc, perm_codes in COMPANY_ROLES:
            role = db.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
            if not role:
                role = Role(name=name, description=desc)
                db.add(role)
                db.flush()
            for code in perm_codes:
                perm = _get_perm(db, code)
                if perm not in role.permissions:
                    role.permissions.append(perm)

        # 4) admin 계정
        admin_pw_msg = "admin 계정이 이미 있어 비밀번호를 변경하지 않았습니다."
        admin = db.execute(select(User).where(User.username == "admin")).scalar_one_or_none()
        if not admin:
            password, generated = _admin_password()
            # 비밀번호 정책(app/security.validate_password)에 못 미치는 초기 비밀번호는
            # 첫 로그인에서 변경을 강제한다(로컬 기본값 admin1234 가 여기 해당).
            try:
                validate_password(password, "admin")
                must_change = False
            except ValueError:
                must_change = True

            admin = User(
                username="admin",
                full_name="관리자",
                hashed_password=hash_password(password),
                must_change_password=must_change,
            )
            admin.roles = [admin_role]
            db.add(admin)
            if generated:
                admin_pw_msg = (
                    f"admin 초기 비밀번호(무작위 생성, 지금만 표시): {password}\n"
                    "로그인 후 반드시 비밀번호를 변경하세요."
                )
            elif password == "admin1234":
                admin_pw_msg = (
                    "로그인 계정: admin / admin1234 (로컬 개발 기본값)\n"
                    "이 비밀번호는 정책 미달이라 첫 로그인 후 변경해야 업무 화면을 쓸 수 있습니다."
                )
            else:
                admin_pw_msg = "로그인 계정: admin / (SEED_ADMIN_PASSWORD 로 지정한 값)"
            if must_change and not generated:
                admin_pw_msg += "" if password == "admin1234" else "\n(정책 미달 비밀번호 — 첫 로그인 시 변경 필요)"

        # 5) 기본창고 (멱등) — 재고 이동은 창고 차원이 필수라 마스터보다 먼저 보장.
        #    창고 미지정 API 호출(하위호환)은 전부 이 창고(is_default=True)로 해석된다.
        main_wh = db.execute(select(Warehouse).where(Warehouse.code == "MAIN")).scalar_one_or_none()
        if not main_wh:
            main_wh = Warehouse(code="MAIN", name="기본창고", is_default=True)
            db.add(main_wh)
            db.flush()
        # 기본창고가 하나도 없으면(수동 조작 등) MAIN 을 기본으로 복구한다.
        if not db.execute(select(Warehouse).where(Warehouse.is_default.is_(True))).scalars().first():
            main_wh.is_default = True
            db.flush()

        # 5.5) GL 계정과목 12개 시드(멱등, 마이그레이션 시드와 동일 내용).
        #      아래 샘플 입고가 GL 전기를 유발하므로 계정이 먼저 있어야 한다.
        ensure_accounts(db)

        # 6) 샘플 마스터 데이터
        if not db.execute(select(Partner)).scalars().first():
            db.add(Partner(
                code=generate_code(db, "PARTNER", "CUST"),
                name="샘플상사", partner_type="customer", ceo_name="홍길동",
                phone="02-1234-5678", business_no="123-45-67890",
            ))
        if not db.execute(select(Item)).scalars().first():
            db.add(Item(
                code=generate_code(db, "ITEM", "ITEM"),
                name="샘플상품", spec="100x100", unit="EA",
                purchase_price=7000, sales_price=10000, safety_stock=15,
            ))
        db.flush()

        # 7) 샘플 입고 이력 (첫 품목 대상, 1회만) — 잔고도 함께 갱신(기본창고)
        if not db.execute(select(StockMovement)).scalars().first():
            item = db.execute(select(Item)).scalars().first()
            if item:
                post_movement(
                    db, item_id=item.id, movement_type="IN", quantity=10,
                    unit_price=item.purchase_price, note="초기 입고(샘플)",
                )

        db.commit()
        print(f"시드 완료. {admin_pw_msg}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
