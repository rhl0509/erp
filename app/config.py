from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 절대 운영에 나가면 안 되는 자리표시자 시크릿. 이 값들로는 기동을 거부한다.
# (과거 docker-compose 기본값이었던 약한 문자열도 방어적으로 포함)
_INSECURE_SECRETS = {
    "", "change-me", "change-me-in-production",
    "change-this-to-a-long-random-secret-please", "erp_root_pw",
    "please-change-this-to-a-long-random-string",  # .env.example 의 예전 예시값
}
# 정확 일치 블록리스트만으로는 .env.example 을 그대로 복사한 값을 놓친다.
# "바꿔야 하는 값"임을 드러내는 표식이 들어 있으면 길이와 무관하게 거부한다.
_PLACEHOLDER_MARKERS = ("change", "please", "example", "replace", "your-", "xxxx", "placeholder", "todo")
_MIN_SECRET_LEN = 16


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # mysql+pymysql://<user>:<password>@<host>:<port>/<db>?charset=utf8mb4
    database_url: str = "mysql+pymysql://root:password@localhost:3306/erp_db?charset=utf8mb4"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8시간

    # 콤마로 여러 origin 지정 가능
    cors_origins: str = "http://localhost:3000"

    # 인증 세션 쿠키(httpOnly) — Next 앱은 이 쿠키로 인증한다(토큰을 JS/localStorage에
    # 두지 않아 XSS 탈취면을 제거). same-origin 프록시(next rewrites)라 samesite=lax 로
    # 충분하다. HTTPS 배포 시 COOKIE_SECURE=true 로 Secure 플래그를 켠다.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"   # lax / strict / none

    # ---------- 시간대 ----------
    # 회계 날짜(전표일자·발행일·채번 기간·이번 달)의 기준이 되는 사업장 시간대.
    business_tz: str = "Asia/Seoul"
    # DB 의 naive 타임스탬프(created_at 등)가 어느 시간대로 기록되는가.
    # MySQL 은 세션 time_zone(기본 system), SQLite 의 CURRENT_TIMESTAMP 는 UTC 다.
    # DB 를 KST 로 운영한다면 Asia/Seoul 로 두면 변환이 항등이 된다.
    db_tz: str = "UTC"

    # ---------- 비밀번호 정책 ----------
    # 길이 하한만 설정으로 뺀다(복잡도 규칙은 security.validate_password 고정).
    password_min_length: int = 10

    # ---------- 비밀번호 재설정 ----------
    # 재설정 링크(메일) 수명. 짧을수록 안전하지만 메일 지연을 감안해 기본 30분.
    reset_token_ttl_minutes: int = 30
    # 재설정 링크가 가리킬 프론트 주소(Next 앱).
    app_base_url: str = "http://localhost:3000"

    # ---------- SMTP (선택) ----------
    # smtp_host 가 비어 있으면 메일 발송 기능은 꺼진 것으로 간주하고,
    # 비밀번호 재설정은 '관리자 임시비밀번호 발급' 경로로 안내한다(auth.forgot_password).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "erp@localhost"
    smtp_starttls: bool = True

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host.strip())

    @model_validator(mode="after")
    def _guard_jwt_secret(self):
        # 기본/약한/자리표시자 시크릿으로는 조용히 기동하지 않는다(토큰 위조 위험).
        low = self.jwt_secret.lower()
        is_placeholder = any(m in low for m in _PLACEHOLDER_MARKERS)
        if self.jwt_secret in _INSECURE_SECRETS or len(self.jwt_secret) < _MIN_SECRET_LEN or is_placeholder:
            raise ValueError(
                "JWT_SECRET가 설정되지 않았거나 안전하지 않습니다. "
                f".env 에 최소 {_MIN_SECRET_LEN}자 이상의 무작위 값을 지정하세요. "
                '예: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self


settings = Settings()
