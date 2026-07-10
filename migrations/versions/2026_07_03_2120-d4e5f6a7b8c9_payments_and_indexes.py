"""payments (AR/AP) + reporting indexes

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-03 21:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('payment_no', sa.String(length=30), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=2), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('pay_date', sa.String(length=10), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payments_payment_no', 'payments', ['payment_no'], unique=True)
    op.create_index('ix_payments_partner_id', 'payments', ['partner_id'], unique=False)

    # 통계/리포트/감사 조회 가속용 인덱스
    op.create_index('ix_stock_movements_created_at', 'stock_movements', ['created_at'], unique=False)
    op.create_index('ix_stock_movements_partner_id', 'stock_movements', ['partner_id'], unique=False)
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs')
    op.drop_index('ix_stock_movements_partner_id', table_name='stock_movements')
    op.drop_index('ix_stock_movements_created_at', table_name='stock_movements')
    op.drop_index('ix_payments_partner_id', table_name='payments')
    op.drop_index('ix_payments_payment_no', table_name='payments')
    op.drop_table('payments')
