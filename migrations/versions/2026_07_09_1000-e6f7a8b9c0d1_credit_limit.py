"""partner credit_limit (여신한도)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-09 10:00:00.000000

거래처 여신한도(0=무제한)를 추가한다. 매출 출고 시 (미수 + 이번 출고)가 한도를
넘으면 출고를 막는다. Aging(연령분석) 리포트는 별도 스키마 변경 없이 이동/결제에서 파생.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('partners',
                  sa.Column('credit_limit', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('partners', schema=None) as batch_op:
        batch_op.drop_column('credit_limit')
