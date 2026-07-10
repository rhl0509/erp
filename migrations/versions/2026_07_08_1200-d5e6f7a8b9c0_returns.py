"""returns: purchase/sales order line returned_qty

Revision ID: d5e6f7a8b9c0
Revises: b2c3d4e5f6a7
Create Date: 2026-07-08 12:00:00.000000

매입/매출 반품 누적 수량(returned_qty)을 발주/수주 명세에 추가한다. 반품 자체는
음수 수량의 역이동(stock_movements)으로 기록되므로 별도 테이블은 없다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('purchase_order_lines',
                  sa.Column('returned_qty', sa.Integer(), server_default='0', nullable=False))
    op.add_column('sales_order_lines',
                  sa.Column('returned_qty', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('sales_order_lines', schema=None) as batch_op:
        batch_op.drop_column('returned_qty')
    with op.batch_alter_table('purchase_order_lines', schema=None) as batch_op:
        batch_op.drop_column('returned_qty')
