"""multi warehouse (창고 마스터 + 잔고/이동 창고 차원)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-10 10:00:00.000000

Phase 3 다중창고. 실 타깃은 MySQL(운영)이며 PK 교체는 MySQL DDL 로 수행한다.
(SQLite 로컬 개발·테스트는 create_all 로 스키마를 만들므로 이 파일을 타지 않는다.)

1) warehouses 테이블 생성 + 기본창고(code=MAIN, is_default=1) INSERT.
2) stock_balances: warehouse_id 추가(nullable) → 기본창고 id 로 백필 → NOT NULL
   → PK 를 item_id 단일에서 복합 (item_id, warehouse_id) 로 교체 → FK.
   (복합 PK 의 leftmost prefix 가 item_id 인덱스를 제공하므로 기존 items FK 도 유지된다)
3) stock_movements: warehouse_id 추가(nullable) → 기본창고로 백필 → NOT NULL + FK + index.

백필 값(기본창고 id)은 하드코딩하지 않고 방금 INSERT 한 행을 SELECT 해 사용한다
(멱등·AUTO_INCREMENT 시작값과 무관하게 이식 가능).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _default_warehouse_id(bind) -> int:
    """기본창고(MAIN) id 조회. INSERT 직후 호출되므로 반드시 존재한다."""
    return bind.execute(
        sa.text("SELECT id FROM warehouses WHERE code = 'MAIN'")
    ).scalar_one()


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 창고 마스터 + 기본창고 시드
    op.create_table(
        'warehouses',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_warehouses_code'), 'warehouses', ['code'], unique=True)
    op.execute(
        "INSERT INTO warehouses (code, name, is_default, is_active, created_at, updated_at) "
        "VALUES ('MAIN', '기본창고', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    default_id = _default_warehouse_id(bind)

    # 2) stock_balances: 창고 차원 추가 + PK 교체 (item_id → (item_id, warehouse_id))
    op.add_column('stock_balances', sa.Column('warehouse_id', sa.Integer(), nullable=True))
    bind.execute(
        sa.text("UPDATE stock_balances SET warehouse_id = :wid"),
        {"wid": default_id},
    )
    op.alter_column('stock_balances', 'warehouse_id',
                    existing_type=sa.Integer(), nullable=False)
    # MySQL: PK 드롭+추가를 한 문장으로 원자 수행. 새 복합 PK 의 leftmost prefix 가
    # item_id 인덱스를 제공해 기존 items FK(ondelete CASCADE)가 계속 유효하다.
    op.execute(
        "ALTER TABLE stock_balances DROP PRIMARY KEY, "
        "ADD PRIMARY KEY (item_id, warehouse_id)"
    )
    op.create_foreign_key(
        'fk_stock_balances_warehouse_id', 'stock_balances',
        'warehouses', ['warehouse_id'], ['id'],
    )

    # 3) stock_movements: 창고 차원 추가(NOT NULL + FK + index)
    op.add_column('stock_movements', sa.Column('warehouse_id', sa.Integer(), nullable=True))
    bind.execute(
        sa.text("UPDATE stock_movements SET warehouse_id = :wid"),
        {"wid": default_id},
    )
    op.alter_column('stock_movements', 'warehouse_id',
                    existing_type=sa.Integer(), nullable=False)
    op.create_index(op.f('ix_stock_movements_warehouse_id'),
                    'stock_movements', ['warehouse_id'], unique=False)
    op.create_foreign_key(
        'fk_stock_movements_warehouse_id', 'stock_movements',
        'warehouses', ['warehouse_id'], ['id'],
    )


def downgrade() -> None:
    # 역순 복구. 다중창고 데이터는 품목 단위로 접는다(수량 합·가중평균 원가) —
    # 창고 구분 정보는 손실되지만 총수량·총평가액은 보존된다.
    # TRANSFER 이동은 창고 없이는 의미가 없으므로 삭제한다(잔고 합계에 영향 없음:
    # 페어의 부호합이 0이라 품목 총량 불변).

    # 3) stock_movements 역순
    op.execute("DELETE FROM stock_movements WHERE movement_type = 'TRANSFER'")
    op.drop_constraint('fk_stock_movements_warehouse_id', 'stock_movements', type_='foreignkey')
    op.drop_index(op.f('ix_stock_movements_warehouse_id'), table_name='stock_movements')
    op.drop_column('stock_movements', 'warehouse_id')

    # 2) stock_balances 역순: 품목 단위 집계를 스크래치 테이블로 만들고, 비운 뒤
    #    PK 를 단일 item_id 로 되돌리고 나서 다시 채운다(PK 충돌 방지 순서).
    #    (TEMPORARY/CTAS 는 GTID 복제 환경 제약이 있어 명시적 CREATE + INSERT 를 쓴다)
    op.execute("DROP TABLE IF EXISTS _sb_agg")
    op.execute(
        "CREATE TABLE _sb_agg ("
        "  item_id INT NOT NULL PRIMARY KEY, "
        "  on_hand INT NOT NULL, "
        "  avg_cost DECIMAL(18,4) NOT NULL"
        ")"
    )
    op.execute(
        "INSERT INTO _sb_agg (item_id, on_hand, avg_cost) "
        "SELECT item_id, "
        "       SUM(on_hand), "
        "       CASE WHEN SUM(on_hand) > 0 "
        "            THEN SUM(on_hand * avg_cost) / SUM(on_hand) "
        "            ELSE 0 END "
        "FROM stock_balances GROUP BY item_id"
    )
    op.execute("DELETE FROM stock_balances")
    op.drop_constraint('fk_stock_balances_warehouse_id', 'stock_balances', type_='foreignkey')
    op.execute(
        "ALTER TABLE stock_balances DROP PRIMARY KEY, ADD PRIMARY KEY (item_id)"
    )
    op.drop_column('stock_balances', 'warehouse_id')
    op.execute(
        "INSERT INTO stock_balances (item_id, on_hand, avg_cost, updated_at) "
        "SELECT item_id, on_hand, avg_cost, CURRENT_TIMESTAMP FROM _sb_agg"
    )
    op.execute("DROP TABLE _sb_agg")

    # 1) 창고 마스터 제거
    op.drop_index(op.f('ix_warehouses_code'), table_name='warehouses')
    op.drop_table('warehouses')
