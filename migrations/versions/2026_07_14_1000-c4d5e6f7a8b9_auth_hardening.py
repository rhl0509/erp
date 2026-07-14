"""auth hardening (비밀번호 정책·강제변경·토큰 무효화·2FA·가입 승인/거절·비번 재설정)

Revision ID: c4d5e6f7a8b9
Revises: b3f2a1c0d9e8
Create Date: 2026-07-14 10:00:00.000000

users 확장:
  - department / signup_reason : 가입 신청 정보(승인 판단용)
  - rejected_at / reject_reason: 가입 거절(계정 삭제 대신 표시 — 이력 보존·재가입 방지)
  - token_version              : 발급 JWT 에 심는 값. 올리면 그 이전 토큰이 전부 무효
                                (비번 변경·재설정·임시비번·비활성화 시 강제 로그아웃)
  - must_change_password       : 임시비번·정책 미달 계정은 비번을 바꿔야 업무 API 사용 가능
  - password_changed_at / last_login_at
  - totp_secret / totp_enabled : 2단계 인증(TOTP)
  - email                      : UNIQUE 로 승격(재설정 대상 식별). 기존 빈 문자열은 NULL 로
                                 바꾼다 — UNIQUE 는 NULL 중복만 허용하기 때문.

신규 테이블:
  - password_reset_tokens : 재설정 토큰(원문은 메일로만, DB엔 sha256 해시), 1회용·시한부

백필 정책(사용자 결정 2026-07-14): **기존 계정은 전부 비밀번호 강제 변경 대상**으로 표시한다.
새 정책(10자 이상·영문+숫자·아이디 포함 금지)을 통과하는지 해시만으로는 알 수 없고,
정책 미달 비밀번호가 그대로 남아 있는 편이 더 위험하기 때문이다.

SQLite 로컬 개발·테스트는 create_all 로 스키마를 만들므로 이 파일을 타지 않는다(운영=MySQL).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3f2a1c0d9e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) users 컬럼 추가
    op.add_column('users', sa.Column('department', sa.String(length=100),
                                     nullable=False, server_default=''))
    op.add_column('users', sa.Column('signup_reason', sa.String(length=255),
                                     nullable=False, server_default=''))
    op.add_column('users', sa.Column('rejected_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('reject_reason', sa.String(length=255),
                                     nullable=False, server_default=''))
    op.add_column('users', sa.Column('token_version', sa.Integer(),
                                     nullable=False, server_default='1'))
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(),
                                     nullable=False, server_default='0'))
    op.add_column('users', sa.Column('password_changed_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('totp_secret', sa.String(length=64),
                                     nullable=False, server_default=''))
    op.add_column('users', sa.Column('totp_enabled', sa.Boolean(),
                                     nullable=False, server_default='0'))

    # 2) email: 빈 문자열 → NULL 로 정규화한 뒤 UNIQUE 부여.
    #    (빈 문자열이 여러 행에 있으면 UNIQUE 를 걸 수 없다. NULL 중복은 허용된다.)
    op.execute("UPDATE users SET email = NULL WHERE email = '' OR email IS NULL")
    op.create_unique_constraint('uq_users_email', 'users', ['email'])

    # 3) 기존 계정 전부 비밀번호 강제 변경 대상으로 표시(새 정책 소급 적용)
    op.execute("UPDATE users SET must_change_password = 1")

    # 4) 비밀번호 재설정 토큰
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name='fk_password_reset_tokens_user_id', ondelete='CASCADE'),
    )
    op.create_index(op.f('ix_password_reset_tokens_user_id'),
                    'password_reset_tokens', ['user_id'], unique=False)
    op.create_index(op.f('ix_password_reset_tokens_token_hash'),
                    'password_reset_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_password_reset_tokens_token_hash'), table_name='password_reset_tokens')
    op.drop_index(op.f('ix_password_reset_tokens_user_id'), table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')

    op.drop_constraint('uq_users_email', 'users', type_='unique')
    op.execute("UPDATE users SET email = '' WHERE email IS NULL")

    for col in ('totp_enabled', 'totp_secret', 'last_login_at', 'password_changed_at',
                'must_change_password', 'token_version', 'reject_reason', 'rejected_at',
                'signup_reason', 'department'):
        op.drop_column('users', col)
