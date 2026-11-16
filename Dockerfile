FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Tashkent \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# libarchive-tools → bsdtar (RAR/ZIP tender attachments); tzdata for Asia/Tashkent
RUN apt-get update \
 && apt-get install -y --no-install-recommends libarchive-tools libatomic1 tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install -e . \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY alembic.ini ./
COPY alembic ./alembic
COPY config.yaml profile.yaml company.yaml ./

RUN useradd --create-home --uid 1000 bot \
 && mkdir -p /app/data /app/workspaces \
 && chown -R bot:bot /app /ms-playwright
USER bot

VOLUME ["/app/data"]
CMD ["python", "-m", "app.main"]
