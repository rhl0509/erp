"""stock balance cache + movement source refs

Revision ID: b7e1c0d2f3a4
Revises: 643a533a1b31
Create Date: 2026-07-03 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e1c0d2f3a4'
down_revision: Union[str, None] = '643a533a1b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 품목별 현재고 캐시(잔고)
    op.create_table(
        'stock_balances',
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('on_hand', sa.Integer(), server_default='0', nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('item_id'),
    )
    # 이동 출처 추적 컬럼
    op.add_column('stock_movements', sa.Column('ref_type', sa.String(length=10), server_default='MANUAL', nullable=False))
    op.add_column('stock_movements', sa.Column('ref_line_id', sa.Integer(), nullable=True))

    # 기존 이동을 합산해 잔고를 백필한다(입고+/출고-/조정±). 데이터 무손실.
    op.execute(
        "INSERT INTO stock_balances (item_id, on_hand) "
        "SELECT item_id, SUM(CASE WHEN movement_type = 'OUT' THEN -quantity ELSE quantity END) "
        "FROM stock_movements GROUP BY item_id"
    )


def downgrade() -> None:
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.drop_column('ref_line_id')
        batch_op.drop_column('ref_type')
    op.drop_table('stock_balances')
