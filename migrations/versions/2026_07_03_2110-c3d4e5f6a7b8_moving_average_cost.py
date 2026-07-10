"""moving average cost + movement cost

Revision ID: c3d4e5f6a7b8
Revises: 2b1bc2d1c4c0
Create Date: 2026-07-03 21:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = '2b1bc2d1c4c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock_balances', sa.Column('avg_cost', sa.Float(), server_default='0', nullable=False))
    op.add_column('stock_movements', sa.Column('cost', sa.Float(), server_default='0', nullable=False))

    # 백필(기존 데이터 근사): 이동원가 = 입고 매입단가, 잔고 평균원가 = 입고 가중평균 매입단가.
    # (신규 이동부터는 post_movement 가 정확한 이동평균을 유지한다.)
    op.execute("UPDATE stock_movements SET cost = unit_price WHERE movement_type = 'IN'")
    op.execute(
        "UPDATE stock_balances SET avg_cost = COALESCE(("
        "  SELECT SUM(m.quantity * m.unit_price) * 1.0 / NULLIF(SUM(m.quantity), 0) "
        "  FROM stock_movements m "
        "  WHERE m.item_id = stock_balances.item_id AND m.movement_type = 'IN'"
        "), 0)"
    )


def downgrade() -> None:
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.drop_column('cost')
    with op.batch_alter_table('stock_balances', schema=None) as batch_op:
        batch_op.drop_column('avg_cost')
