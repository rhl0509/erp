"""general ledger (accounts / journal_entries / journal_lines + 계정과목 12개 시드)

Revision ID: b3f2a1c0d9e8
Revises: f7a8b9c0d1e2
Create Date: 2026-07-11 09:00:00.000000

총계정원장(GL) — 설계: docs/gl-design.md (전기형 B, §9 결정 반영).
실 타깃은 MySQL(운영). SQLite 로컬 개발·테스트는 create_all + app.gl.ensure_accounts
로 스키마·계정을 만들므로 이 파일을 타지 않는다.

1) accounts: 계정과목 마스터 + 12계정 시드(app/gl.py ACCOUNTS_SEED 와 동일 내용).
2) journal_entries: 전표 헤더. UNIQUE(source_type, source_id) 로 원천 1행=전표 1건
   멱등성 보장(MySQL 은 NULL source_id 중복을 허용하므로 MANUAL 전표 다건 가능).
3) journal_lines: 분개 라인. 금액 DECIMAL(18,4) — stock_movements.cost 정밀도 정책과
   동일. (account_id, entry_id) 복합 인덱스가 계정별원장·시산표 집계 경로.

업그레이드 후 기존 데이터 소급 전기(§9-6): python -m scripts.rebuild_gl
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f2a1c0d9e8'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, name, account_type, normal_side) — app/gl.py ACCOUNTS_SEED 와 동일해야 한다.
ACCOUNTS_SEED = [
    ("1110", "현금및예금",     "asset",     "debit"),
    ("1120", "외상매출금",     "asset",     "debit"),
    ("1130", "상품",           "asset",     "debit"),
    ("1140", "부가세대급금",   "asset",     "debit"),
    ("2110", "외상매입금",     "liability", "credit"),
    ("2120", "부가세예수금",   "liability", "credit"),
    ("3110", "이월이익잉여금", "equity",    "credit"),
    ("4110", "상품매출",       "revenue",   "credit"),
    ("4910", "재고조정이익",   "revenue",   "credit"),
    ("5110", "매출원가",       "expense",   "debit"),
    ("5120", "재고감모손실",   "expense",   "debit"),
    ("5130", "재고원가차이",   "expense",   "debit"),
]


def upgrade() -> None:
    # 1) 계정과목 마스터
    accounts = op.create_table(
        'accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=10), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('account_type', sa.String(length=10), nullable=False),
        sa.Column('normal_side', sa.String(length=6), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_accounts_code'), 'accounts', ['code'], unique=True)
    op.bulk_insert(accounts, [
        {"code": c, "name": n, "account_type": t, "normal_side": s, "is_active": True}
        for c, n, t, s in ACCOUNTS_SEED
    ])

    # 2) 전표 헤더
    op.create_table(
        'journal_entries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entry_no', sa.String(length=30), nullable=False),
        sa.Column('entry_date', sa.String(length=10), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=False),
        sa.Column('source_type', sa.String(length=10), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=10), server_default='posted', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_type', 'source_id', name='uq_journal_entries_source'),
    )
    op.create_index(op.f('ix_journal_entries_entry_no'), 'journal_entries', ['entry_no'], unique=True)
    op.create_index('ix_journal_entries_entry_date', 'journal_entries', ['entry_date'], unique=False)

    # 3) 분개 라인
    op.create_table(
        'journal_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('line_no', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('debit', sa.Numeric(precision=18, scale=4), server_default='0', nullable=False),
        sa.Column('credit', sa.Numeric(precision=18, scale=4), server_default='0', nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=True),
        sa.Column('memo', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['entry_id'], ['journal_entries.id'],
                                name='fk_journal_lines_entry_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'],
                                name='fk_journal_lines_account_id'),
        sa.ForeignKeyConstraint(['partner_id'], ['partners.id'],
                                name='fk_journal_lines_partner_id'),
    )
    op.create_index(op.f('ix_journal_lines_entry_id'), 'journal_lines', ['entry_id'], unique=False)
    op.create_index('ix_journal_lines_account_entry', 'journal_lines',
                    ['account_id', 'entry_id'], unique=False)


def downgrade() -> None:
    # 역순 제거. 전표는 원천에서 언제든 재생성 가능(rebuild)하므로 데이터 손실 아님.
    op.drop_index('ix_journal_lines_account_entry', table_name='journal_lines')
    op.drop_index(op.f('ix_journal_lines_entry_id'), table_name='journal_lines')
    op.drop_table('journal_lines')
    op.drop_index('ix_journal_entries_entry_date', table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_entry_no'), table_name='journal_entries')
    op.drop_table('journal_entries')
    op.drop_index(op.f('ix_accounts_code'), table_name='accounts')
    op.drop_table('accounts')
