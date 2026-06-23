from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # mysql+pymysql://<user>:<password>@<host>:<port>/<db>?charset=utf8mb4
    database_url: str = "mysql+pymysql://root:password@localhost:3306/erp_db?charset=utf8mb4"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8시간

    # 콤마로 여러 origin 지정 가능
    cors_origins: str = "http://localhost:3000"


settings = Settings()
