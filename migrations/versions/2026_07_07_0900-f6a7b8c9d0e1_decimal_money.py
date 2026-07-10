"""money columns Float -> Numeric(18,4)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-07 09:00:00.000000

이동평균 원가(stock_balances.avg_cost)와 이동원가(stock_movements.cost)를
단정도 FLOAT 에서 DECIMAL(18,4) 로 바꾼다. 큰 금액에서 원 단위 오차가
누적되던 문제를 없애고 회계 수치를 정밀하게 저장한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch 모드로 SQLite/MySQL 모두에서 타입 변경을 지원한다.
    with op.batch_alter_table('stock_balances', schema=None) as batch_op:
        batch_op.alter_column(
            'avg_cost',
            existing_type=sa.Float(),
            type_=sa.Numeric(18, 4),
            existing_nullable=False,
            existing_server_default='0',
        )
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.alter_column(
            'cost',
            existing_type=sa.Float(),
            type_=sa.Numeric(18, 4),
            existing_nullable=False,
            existing_server_default='0',
        )


def downgrade() -> None:
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.alter_column(
            'cost',
            existing_type=sa.Numeric(18, 4),
            type_=sa.Float(),
            existing_nullable=False,
            existing_server_default='0',
        )
    with op.batch_alter_table('stock_balances', schema=None) as batch_op:
        batch_op.alter_column(
            'avg_cost',
            existing_type=sa.Numeric(18, 4),
            type_=sa.Float(),
            existing_nullable=False,
            existing_server_default='0',
        )
