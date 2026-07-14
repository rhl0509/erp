"""period close (accounting_periods — 월별 회계기간 마감)

Revision ID: d6e7f8a9b0c1
Revises: c4d5e6f7a8b9
Create Date: 2026-07-14 14:00:00.000000

월 단위 회계기간 마감. 마감하면 그 달에 속하는 기표가 전면 차단되고(수기 전표·과거 날짜
결제·마감월 재고이동·원천 삭제), 손익계정을 3110 이월이익잉여금으로 대체하는 마감 전표
(journal_entries.source_type='CLOSING', source_id=accounting_periods.id)가 생긴다.

기존 전표에는 손대지 않는다 — 모든 기간이 'open' 으로 시작하므로 업그레이드 직후의 동작은
지금과 동일하고, 사용자가 마감을 눌러야 차단이 시작된다(기간 행은 조회 시 지연 생성).

SQLite 로컬 개발·테스트는 create_all 로 스키마를 만들므로 이 파일을 타지 않는다(운영=MySQL).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'accounting_periods',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),          # YYYY-MM
        sa.Column('status', sa.String(length=10), server_default='open', nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('closed_by_id', sa.Integer(), nullable=True),
        sa.Column('closed_by_name', sa.String(length=50), server_default='', nullable=False),
        sa.Column('net_income', sa.Numeric(precision=18, scale=4),
                  server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['closed_by_id'], ['users.id'],
                                name='fk_accounting_periods_closed_by', ondelete='SET NULL'),
    )
    op.create_index(op.f('ix_accounting_periods_period'), 'accounting_periods',
                    ['period'], unique=True)


def downgrade() -> None:
    # 마감 전표는 기간 행을 참조(source_id)하므로 함께 제거한다 — 남겨 두면 고아가 된다.
    op.execute("DELETE FROM journal_lines WHERE entry_id IN "
               "(SELECT id FROM journal_entries WHERE source_type = 'CLOSING')")
    op.execute("DELETE FROM journal_entries WHERE source_type = 'CLOSING'")
    op.drop_index(op.f('ix_accounting_periods_period'), table_name='accounting_periods')
    op.drop_table('accounting_periods')
