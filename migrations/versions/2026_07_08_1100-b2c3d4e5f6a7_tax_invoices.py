"""tax invoices (세금계산서)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-08 11:00:00.000000

세금계산서 헤더/명세 테이블을 추가한다(발행 시점 스냅샷). 원천 문서(발주/수주)를
ref_type/ref_id 로 연결한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tax_invoices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('invoice_no', sa.String(length=30), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('ref_type', sa.String(length=2), nullable=False),
        sa.Column('ref_id', sa.Integer(), nullable=True),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('partner_name', sa.String(length=100), nullable=False),
        sa.Column('partner_business_no', sa.String(length=20), nullable=False),
        sa.Column('issue_date', sa.String(length=10), nullable=False),
        sa.Column('supply_amount', sa.Integer(), nullable=False),
        sa.Column('tax_amount', sa.Integer(), nullable=False),
        sa.Column('total_amount', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), server_default='issued', nullable=False),
        sa.Column('note', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partners.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tax_invoices_invoice_no', 'tax_invoices', ['invoice_no'], unique=True)
    op.create_index('ix_tax_invoices_partner_id', 'tax_invoices', ['partner_id'], unique=False)

    op.create_table(
        'tax_invoice_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('item_name', sa.String(length=100), nullable=False),
        sa.Column('spec', sa.String(length=100), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('unit_price', sa.Integer(), nullable=False),
        sa.Column('supply_amount', sa.Integer(), nullable=False),
        sa.Column('tax_amount', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['tax_invoices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tax_invoice_lines_invoice_id', 'tax_invoice_lines', ['invoice_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tax_invoice_lines_invoice_id', table_name='tax_invoice_lines')
    op.drop_table('tax_invoice_lines')
    op.drop_index('ix_tax_invoices_partner_id', table_name='tax_invoices')
    op.drop_index('ix_tax_invoices_invoice_no', table_name='tax_invoices')
    op.drop_table('tax_invoices')
