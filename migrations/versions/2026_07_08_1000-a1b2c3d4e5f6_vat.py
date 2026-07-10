"""VAT: item tax_type + movement tax_amount

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-07-08 10:00:00.000000

부가세 지원: 품목에 과세구분(taxable/exempt), 재고이동에 부가세액 스냅샷을 추가한다.
기존 IN/OUT 이동은 (품목 기본값이 과세이므로) 공급가액의 10% 로 세액을 백필한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('items', sa.Column('tax_type', sa.String(length=10),
                                     server_default='taxable', nullable=False))
    op.add_column('stock_movements', sa.Column('tax_amount', sa.Numeric(18, 4),
                                               server_default='0', nullable=False))
    # 백필: 매입/매출 이동의 부가세액 = ROUND(공급가액 x 10%). (기존 품목은 모두 과세 기본값)
    op.execute(
        "UPDATE stock_movements SET tax_amount = ROUND(quantity * unit_price * 0.1) "
        "WHERE movement_type IN ('IN', 'OUT')"
    )


def downgrade() -> None:
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.drop_column('tax_amount')
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_column('tax_type')
