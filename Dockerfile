FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 비루트 사용자로 실행(최소 권한). 앱 코드 소유권을 넘겨 런타임 쓰기 경로 확보.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# 기동 시 스키마 마이그레이션 → (멱등)시드 → 서버 실행.
# DATABASE_URL / JWT_SECRET 는 compose(또는 런타임 환경)에서 주입한다.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
