"""
초기 데이터 시드 스크립트.

실행:  python -m app.seed
생성:  권한, admin/staff 역할, admin 계정(admin/admin1234), 샘플 거래처/품목
여러 번 실행해도 안전하도록 멱등(idempotent)하게 작성됨.
"""
from sqlalchemy import select

from .database import SessionLocal, Base, engine
from . import models  # noqa: F401
from .models import User, Role, Permission, Partner, Item
from .security import hash_password
from .services import generate_code

PERMISSIONS = [
    ("*", "전체 권한 (슈퍼관리자)"),
    ("user:read", "사용자 조회"),
    ("user:write", "사용자 등록/수정"),
    ("partner:read", "거래처 조회"),
    ("partner:write", "거래처 등록/수정/삭제"),
    ("item:read", "품목 조회"),
    ("item:write", "품목 등록/수정/삭제"),
]

STAFF_PERMS = ["partner:read", "partner:write", "item:read", "item:write"]


def _get_perm(db, code: str) -> Permission:
    return db.execute(select(Permission).where(Permission.code == code)).scalar_one()


def run():
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

        # 3) staff 역할 (마스터 관리 권한)
        staff_role = db.execute(select(Role).where(Role.name == "staff")).scalar_one_or_none()
        if not staff_role:
            staff_role = Role(name="staff", description="일반 직원")
            db.add(staff_role)
            db.flush()
        for code in STAFF_PERMS:
            perm = _get_perm(db, code)
            if perm not in staff_role.permissions:
                staff_role.permissions.append(perm)

        # 4) admin 계정
        admin = db.execute(select(User).where(User.username == "admin")).scalar_one_or_none()
        if not admin:
            admin = User(
                username="admin",
                full_name="관리자",
                hashed_password=hash_password("admin1234"),
            )
            admin.roles = [admin_role]
            db.add(admin)

        # 5) 샘플 마스터 데이터
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
                purchase_price=7000, sales_price=10000,
            ))

        db.commit()
        print("시드 완료. 로그인 계정: admin / admin1234")
    finally:
        db.close()


if __name__ == "__main__":
    run()
